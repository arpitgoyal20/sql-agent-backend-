"""Run-from-editor endpoint (CHANGES-v2 §2.3): validated, read-only, paginated, no LLM."""

import pytest

from app.nodes.refuse import DESTRUCTIVE_TEXT
from app.workbench import TIMEOUT_TEXT
from tests.test_api import client  # noqa: F401  (fixture reuse)


def run(client, sql, **kw):
    r = client.post("/api/query/run", json={"sql": sql, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_valid_select_paginates(client):
    sql = (
        "SELECT o.OrderID, c.Name FROM Orders o JOIN Customers c ON c.CustomerID = o.CustomerID "
        "ORDER BY o.OrderID"
    )
    page1 = run(client, sql)
    assert page1["status"] == "ok"
    assert page1["columns"] == ["OrderID", "Name"]
    assert page1["row_count"] == 100 and page1["total"] == 2000
    assert page1["limit"] == 100 and page1["offset"] == 0
    assert isinstance(page1["elapsed_ms"], int) and page1["warnings"] == []
    assert page1["sql"].startswith("SELECT")
    page2 = run(client, sql, limit=50, offset=100)
    assert [r[0] for r in page2["rows"]] == list(range(101, 151))
    beyond = run(client, sql, offset=5000)
    assert beyond["status"] == "ok" and beyond["rows"] == [] and beyond["total"] == 2000
    assert client.fake.calls == []  # no LLM involved


def test_users_own_limit_is_respected(client):
    body = run(client, "SELECT Name FROM Products ORDER BY Price DESC LIMIT 5;")
    assert body["row_count"] == 5 and body["total"] == 5


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM Orders",
        "UPDATE Employees SET Salary = 0",
        "DROP TABLE Orders",
        "SELECT 1; DROP TABLE Orders",
        "PRAGMA table_info(Orders)",
        "INSERT INTO Departments (Name) VALUES ('x')",
    ],
)
def test_writes_are_refused(client, sql):
    assert run(client, sql) == {"status": "refused", "text": DESTRUCTIVE_TEXT}


def test_unknown_column_is_invalid(client):
    body = run(client, "SELECT nope FROM Employees")
    assert body["status"] == "invalid"
    assert body["errors"][0].startswith("UNKNOWN_COLUMN")


def test_too_long_is_invalid(client):
    body = run(client, "SELECT 1 " + " " * 10_001)
    assert body["status"] == "invalid" and body["errors"][0].startswith("TOO_LONG")


def test_non_fk_join_is_a_warning(client):
    body = run(client, "SELECT o.OrderID FROM Orders o JOIN Payments p ON p.PaidAt = o.OrderDate")
    assert body["status"] == "ok" and body["warnings"][0].startswith("NON_FK_JOIN")


def test_postgres_runs_after_transpile(client):
    body = run(
        client,
        'SELECT "Name" FROM "Products" WHERE "Name" ILIKE \'%mouse%\'',
        dialect="postgres",
    )
    assert body["status"] == "ok" and body["rows"] == [["Wireless Mouse"]]
    # What actually ran on SQLite is reported, and has no ILIKE.
    assert body["executed_sql"] and "ILIKE" not in body["executed_sql"].upper()


def test_sqlite_runs_have_no_executed_sql(client):
    assert run(client, "SELECT Name FROM Products LIMIT 1")["executed_sql"] is None


def test_postgres_feature_sqlite_lacks_is_a_readable_error(client):
    body = run(client, "SELECT FirstName FROM Employees WHERE FirstName ~ '^A'", dialect="postgres")
    assert body["status"] == "error"
    assert body["text"].startswith(
        "This PostgreSQL feature isn't supported on the SQLite demo database."
    )
    assert body["executed_sql"]  # the translated SQL is still shown


def test_slow_query_times_out(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "query_timeout_s", 0.3)
    body = run(
        client,
        "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT i FROM n",
    )
    assert body["status"] == "error" and body["text"] == TIMEOUT_TEXT


def test_paging_bounds_validated(client):
    assert client.post("/api/query/run", json={"sql": "SELECT 1", "limit": 0}).status_code == 422
    assert client.post("/api/query/run", json={"sql": "SELECT 1", "offset": -1}).status_code == 422
