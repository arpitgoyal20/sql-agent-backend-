from app.schema_loader import ForeignKey

TABLES = ["Departments", "Employees", "Customers", "Products", "Orders", "OrderItems", "Payments"]


def test_all_tables_loaded_in_db_order(schema):
    assert schema.table_names == TABLES


def test_schema_dict_is_lowercase_for_sqlglot(schema):
    d = schema.schema_dict
    assert set(d) == {t.lower() for t in TABLES}
    assert d["employees"]["hiredate"] == "TEXT"
    assert d["employees"]["salary"] == "REAL"
    assert d["orderitems"]["quantity"] == "INTEGER"


def test_foreign_keys_read_from_db(schema):
    fks = set(schema.foreign_keys)
    assert ForeignKey("Employees", "ManagerID", "Employees", "EmployeeID") in fks
    assert ForeignKey("Orders", "CustomerID", "Customers", "CustomerID") in fks
    assert ForeignKey("OrderItems", "ProductID", "Products", "ProductID") in fks
    assert len(fks) == 7


def test_fk_pair_is_case_insensitive_and_bidirectional(schema):
    assert schema.is_fk_pair("orders", "customerid", "CUSTOMERS", "CustomerID")
    assert schema.is_fk_pair("Customers", "CustomerID", "Orders", "CustomerID")
    assert not schema.is_fk_pair("Orders", "OrderID", "Customers", "CustomerID")


def test_indexes_include_declared_and_unique(schema):
    assert ("Orders", ["CustomerID"]) in schema.indexes
    assert ("OrderItems", ["OrderID"]) in schema.indexes
    assert ("Employees", ["Email"]) in schema.indexes  # UNIQUE autoindex
    assert "orderdate" not in schema.indexed_columns("Orders")
    assert "customerid" in schema.indexed_columns("orders")
    assert "orderid" in schema.indexed_columns("Orders")  # primary key


def test_resolve_table_case_insensitive(schema):
    assert schema.resolve_table("orderitems") == "OrderItems"
    assert schema.resolve_table("Nope") is None


def test_neighbours(schema):
    assert schema.neighbours("Orders") == {"Customers", "Employees", "OrderItems", "Payments"}
    assert schema.neighbours("Employees") == {"Departments", "Orders"}


def test_schema_text_preserves_case_and_includes_docs_and_fks(schema):
    text = schema.schema_text(["employees"])
    assert text.startswith("CREATE TABLE Employees (")
    assert "HireDate TEXT NOT NULL,  -- date the employee joined" in text
    assert "-- FK: Employees.ManagerID -> Employees.EmployeeID" in text
    assert "CREATE TABLE Orders" not in text


def test_schema_text_all_tables(schema):
    text = schema.schema_text()
    assert all(f"CREATE TABLE {t} (" in text for t in TABLES)
    assert "-- INDEX idx_orders_customer ON Orders(CustomerID)" in text


def test_every_column_has_a_doc(schema):
    missing = [f"{t}.{c.name}" for t, cols in schema.tables.items() for c in cols if not c.doc]
    assert missing == []
