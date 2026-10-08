"""Validator tests (§14). Pure code: no LLM, no API key."""

import pytest

from app.validator import validate


def errors_of(sql, schema, dialect="sqlite"):
    return validate(sql, schema, dialect).errors


def assert_ok(sql, schema, dialect="sqlite"):
    res = validate(sql, schema, dialect)
    assert res.errors == [], res.errors
    return res


def assert_category(sql, schema, category, dialect="sqlite"):
    errs = errors_of(sql, schema, dialect)
    assert errs, f"expected {category} for: {sql}"
    assert errs[0].startswith(f"{category}:"), errs
    return errs


# ---- 1. parse ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELEC FirstName FROM Employees",
        "SELECT FROM",
        "SELECT FirstName FROM Employees WHERE (Salary > 1",
        "",
        "   ;  ",
    ],
)
def test_syntax_errors(schema, sql):
    assert_category(sql, schema, "SYNTAX")


def test_trailing_semicolon_is_fine(schema):
    assert_ok("SELECT FirstName FROM Employees;", schema)


# ---- 2. single statement ---------------------------------------------------------------


def test_two_selects_rejected(schema):
    assert errors_of("SELECT 1; SELECT 2", schema) == ["MULTI: exactly one statement allowed"]


def test_drop_after_semicolon_is_multi_and_destructive(schema):
    errs = errors_of("SELECT * FROM Orders; DROP TABLE Orders", schema)
    assert errs[0] == "MULTI: exactly one statement allowed"
    assert any(e.startswith("DESTRUCTIVE") for e in errs)
    assert validate("SELECT 1; DROP TABLE Orders", schema).destructive


# ---- 3. read-only ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO Departments (Name) VALUES ('x')",
        "INSERT INTO Departments SELECT * FROM Departments",
        "REPLACE INTO Departments (DepartmentID, Name) VALUES (1, 'x')",
        "UPDATE Employees SET Salary = 0",
        "DELETE FROM Orders WHERE Status = 'cancelled'",
        "DROP TABLE Orders",
        "DROP INDEX idx_orders_customer",
        "ALTER TABLE Orders ADD COLUMN x TEXT",
        "TRUNCATE TABLE Orders",
        "CREATE TABLE x (a INT)",
        "CREATE TABLE x AS SELECT * FROM Orders",
        "CREATE INDEX idx_x ON Orders(OrderDate)",
        "CREATE VIEW v AS SELECT * FROM Orders",
        "MERGE INTO Orders USING Customers ON Orders.CustomerID = Customers.CustomerID "
        "WHEN MATCHED THEN DELETE",
        "PRAGMA table_info(Orders)",
        "PRAGMA writable_schema = 1",
        "ATTACH DATABASE '/tmp/x.db' AS x",
        "DETACH DATABASE x",
        "VACUUM",
        "SELECT * INTO OrdersCopy FROM Orders",
        "WITH d AS (DELETE FROM Orders RETURNING *) SELECT * FROM d",
        "SELECT load_extension('/tmp/evil')",
    ],
)
def test_destructive_rejected(schema, sql):
    res = validate(sql, schema)
    assert res.destructive, res.errors
    assert res.errors[0].startswith("DESTRUCTIVE: only SELECT queries are allowed")
    assert res.sqlite_sql is None


def test_destructive_postgres_grant(schema):
    assert validate("GRANT SELECT ON Orders TO bob", schema, "postgres").destructive


@pytest.mark.parametrize("sql", ["VALUES (1, 2)", "REINDEX"])
def test_not_select(schema, sql):
    assert_category(sql, schema, "NOT_SELECT")


def test_parenthesised_select_allowed(schema):
    assert_ok("(SELECT FirstName FROM Employees)", schema)


def test_words_in_strings_are_not_destructive(schema):
    assert_ok("SELECT Name FROM Products WHERE Name = 'DROP TABLE Orders; DELETE'", schema)
    assert_ok("SELECT OrderID FROM Orders WHERE Status = 'cancelled' -- delete these", schema)


# ---- 4. tables ---------------------------------------------------------------------------


def test_unknown_table_lists_available_and_suggests(schema):
    errs = assert_category("SELECT name FROM Employee", schema, "UNKNOWN_TABLE")
    assert "Employee." in errs[0]
    assert "Did you mean Employees?" in errs[0]
    assert "Available: Departments, Employees, Customers" in errs[0]


def test_unknown_table_in_join_and_subquery(schema):
    assert_category(
        "SELECT o.OrderID FROM Orders o JOIN Shipments s ON s.OrderID = o.OrderID",
        schema,
        "UNKNOWN_TABLE",
    )
    assert_category(
        "SELECT FirstName FROM Employees WHERE DepartmentID IN (SELECT id FROM Teams)",
        schema,
        "UNKNOWN_TABLE",
    )


def test_system_and_attached_tables_rejected(schema):
    assert_category("SELECT * FROM sqlite_master", schema, "UNKNOWN_TABLE")
    assert_category("SELECT * FROM other.Orders", schema, "UNKNOWN_TABLE")
    assert_ok("SELECT OrderID FROM main.Orders", schema)


def test_table_names_case_insensitive(schema):
    assert_ok("SELECT firstname FROM EMPLOYEES", schema)


def test_cte_name_is_not_an_unknown_table(schema):
    assert_ok(
        "WITH recent AS (SELECT OrderID, CustomerID FROM Orders WHERE OrderDate >= '2025-01-01') "
        "SELECT CustomerID, COUNT(*) AS n FROM recent GROUP BY CustomerID",
        schema,
    )


# ---- 5. columns --------------------------------------------------------------------------


def test_unknown_column(schema):
    errs = assert_category("SELECT name FROM Employees", schema, "UNKNOWN_COLUMN")
    assert "'name' does not exist" in errs[0]
    assert "Employees(EmployeeID, FirstName, LastName" in errs[0]


def test_unknown_qualified_column(schema):
    assert_category("SELECT e.Nickname FROM Employees e", schema, "UNKNOWN_COLUMN")


def test_unknown_column_in_where_and_order_by(schema):
    assert_category("SELECT FirstName FROM Employees WHERE Age > 30", schema, "UNKNOWN_COLUMN")
    assert_category("SELECT FirstName FROM Employees ORDER BY Age", schema, "UNKNOWN_COLUMN")


def test_column_from_wrong_table(schema):
    assert_category(
        "SELECT c.OrderDate FROM Customers c JOIN Orders o ON o.CustomerID = c.CustomerID",
        schema,
        "UNKNOWN_COLUMN",
    )


def test_ambiguous_column_across_join(schema):
    errs = assert_category(
        "SELECT CustomerID FROM Customers c JOIN Orders o ON o.CustomerID = c.CustomerID",
        schema,
        "UNKNOWN_COLUMN",
    )
    assert "ambiguous" in errs[0]
    assert "Customers and Orders" in errs[0]


def test_column_not_exposed_by_subquery(schema):
    assert_category(
        "SELECT x.Salary FROM (SELECT FirstName FROM Employees) AS x", schema, "UNKNOWN_COLUMN"
    )


# ---- valid queries: no false positives ----------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        # simple filter and aliases
        "SELECT EmployeeID, FirstName, LastName, HireDate FROM Employees "
        "WHERE HireDate >= '2024-01-01'",
        "SELECT e.FirstName AS first_name, e.Salary * 1.1 AS raised FROM Employees AS e",
        # SELECT *, qualified star
        "SELECT * FROM Employees",
        "SELECT o.* FROM Orders o JOIN Customers c ON c.CustomerID = o.CustomerID",
        # ORDER BY / GROUP BY / HAVING on select aliases
        "SELECT FirstName AS fn FROM Employees ORDER BY fn",
        "SELECT strftime('%Y', OrderDate) AS yr, COUNT(*) AS n FROM Orders "
        "GROUP BY yr HAVING COUNT(*) > 10 ORDER BY yr",
        "SELECT DepartmentID, AVG(Salary) AS avg_salary FROM Employees GROUP BY DepartmentID "
        "HAVING avg_salary > 80000 ORDER BY avg_salary DESC",
        # CTEs, including chained and recursive
        "WITH t AS (SELECT CustomerID, COUNT(*) AS n FROM Orders GROUP BY CustomerID) "
        "SELECT c.Name, t.n FROM t JOIN Customers c ON c.CustomerID = t.CustomerID",
        "WITH a AS (SELECT OrderID, CustomerID FROM Orders), b AS (SELECT CustomerID FROM a) "
        "SELECT COUNT(*) FROM b",
        "WITH RECURSIVE chain(id, depth) AS (SELECT EmployeeID, 0 FROM Employees "
        "WHERE ManagerID IS NULL UNION ALL SELECT e.EmployeeID, chain.depth + 1 "
        "FROM Employees e JOIN chain ON e.ManagerID = chain.id) SELECT id, depth FROM chain",
        # subqueries: derived table, scalar, IN, correlated EXISTS
        "SELECT x.a FROM (SELECT FirstName AS a FROM Employees) AS x",
        "SELECT FirstName, Salary FROM Employees "
        "WHERE Salary > (SELECT AVG(Salary) FROM Employees)",
        "SELECT Name FROM Customers WHERE CustomerID IN (SELECT CustomerID FROM Orders)",
        "SELECT e.FirstName FROM Employees e WHERE EXISTS "
        "(SELECT 1 FROM Orders o WHERE o.EmployeeID = e.EmployeeID)",
        # set operations
        "SELECT Name FROM Customers UNION SELECT Name FROM Products",
        "SELECT CustomerID FROM Customers EXCEPT SELECT CustomerID FROM Orders",
        "SELECT CustomerID FROM Customers INTERSECT SELECT CustomerID FROM Orders",
        # self-join on Employees
        "SELECT e.FirstName, m.FirstName AS manager FROM Employees e "
        "LEFT JOIN Employees m ON e.ManagerID = m.EmployeeID",
        # three-table join with aggregation
        "SELECT c.Name, SUM(oi.Quantity * oi.UnitPrice) AS revenue FROM Customers c "
        "JOIN Orders o ON o.CustomerID = c.CustomerID "
        "JOIN OrderItems oi ON oi.OrderID = o.OrderID "
        "GROUP BY c.CustomerID, c.Name ORDER BY revenue DESC LIMIT 5",
        # window function, CASE, quoted identifiers
        "SELECT FirstName, RANK() OVER (PARTITION BY DepartmentID ORDER BY Salary DESC) AS r "
        "FROM Employees",
        "SELECT CASE WHEN Salary > 100000 THEN 'high' ELSE 'normal' END AS band FROM Employees",
        'SELECT "FirstName" FROM "Employees"',
    ],
)
def test_valid_queries(schema, sql):
    assert_ok(sql, schema)


def test_valid_query_returns_sqlite_sql(schema):
    res = assert_ok("SELECT FirstName FROM Employees;", schema)
    assert res.sqlite_sql == "SELECT FirstName FROM Employees"


# ---- 6. joins ------------------------------------------------------------------------------


def test_fk_joins_have_no_warnings(schema):
    res = assert_ok(
        "SELECT c.Name, p.Amount FROM Customers c JOIN Orders o ON c.CustomerID = o.CustomerID "
        "JOIN Payments p ON p.OrderID = o.OrderID",
        schema,
    )
    assert res.warnings == []


def test_non_fk_join_is_a_warning_not_an_error(schema):
    res = assert_ok(
        "SELECT o.OrderID FROM Orders o JOIN Payments p ON p.PaidAt = o.OrderDate", schema
    )
    assert len(res.warnings) == 1
    assert res.warnings[0].startswith("NON_FK_JOIN: Payments.PaidAt = Orders.OrderDate")


def test_join_on_cte_is_not_checked(schema):
    res = assert_ok(
        "WITH t AS (SELECT CustomerID AS cid FROM Orders) "
        "SELECT c.Name FROM Customers c JOIN t ON t.cid = c.CustomerID",
        schema,
    )
    assert res.warnings == []


def test_self_join_on_manager_fk_has_no_warning(schema):
    res = assert_ok(
        "SELECT e.FirstName FROM Employees e JOIN Employees m ON m.EmployeeID = e.ManagerID",
        schema,
    )
    assert res.warnings == []


# ---- 7. database check ---------------------------------------------------------------------


def test_db_check_catches_what_sqlglot_allows(schema):
    # Parses and qualifies fine, but SQLite has no such function.
    assert_category("SELECT no_such_fn(FirstName) FROM Employees", schema, "DB")


# ---- dialects --------------------------------------------------------------------------------


def test_postgres_validates_and_transpiles(schema):
    sql = (
        "SELECT FirstName, HireDate FROM Employees "
        "WHERE HireDate >= '2024-01-01' AND FirstName ILIKE 'a%' LIMIT 10"
    )
    res = assert_ok(sql, schema, "postgres")
    assert res.sqlite_sql is not None
    assert "ILIKE" not in res.sqlite_sql.upper()
    # The transpiled query really runs on SQLite.
    from app import db

    db.explain_query_plan(res.sqlite_sql)


def test_postgres_quoted_mixed_case(schema):
    assert_ok('SELECT "FirstName" FROM "Employees" e', schema, "postgres")


def test_mysql_validates_and_transpiles(schema):
    res = assert_ok(
        "SELECT `FirstName`, `Salary` FROM `Employees` ORDER BY `Salary` DESC LIMIT 5",
        schema,
        "mysql",
    )
    assert "`" not in res.sqlite_sql
    from app import db

    assert db.execute(res.sqlite_sql).row_count == 5


def test_dialect_errors_still_caught(schema):
    assert_category("SELECT nope FROM Employees", schema, "UNKNOWN_COLUMN", "postgres")
    assert_category("SELECT name FROM employee", schema, "UNKNOWN_TABLE", "mysql")
    assert validate("DELETE FROM Orders", schema, "mysql").destructive
