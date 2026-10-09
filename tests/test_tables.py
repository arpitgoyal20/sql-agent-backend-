"""Table browser and preview endpoints (CHANGES-v2 §2.1, §2.2)."""

import pytest

from tests.test_api import client  # noqa: F401  (fixture reuse)

ROW_COUNTS = {
    "Departments": 8, "Employees": 200, "Customers": 500, "Products": 50,
    "Orders": 2000, "OrderItems": 6035, "Payments": 1791,
}


def test_tables_lists_all_with_row_counts_and_keys(client):
    tables = {t["name"]: t for t in client.get("/api/tables").json()["tables"]}
    assert {n: t["row_count"] for n, t in tables.items()} == ROW_COUNTS
    orders = {c["name"]: c for c in tables["Orders"]["columns"]}
    assert orders["OrderID"]["pk"] is True and orders["OrderID"]["fk"] is None
    assert orders["CustomerID"]["fk"] == {"table": "Customers", "column": "CustomerID"}
    assert orders["Status"]["doc"]
    manager = next(c for c in tables["Employees"]["columns"] if c["name"] == "ManagerID")
    assert manager["fk"] == {"table": "Employees", "column": "EmployeeID"}


def test_schema_is_an_alias_of_tables(client):
    assert client.get("/api/schema").json() == client.get("/api/tables").json()


@pytest.mark.parametrize("table, total", list(ROW_COUNTS.items()))
def test_preview_total_matches_row_count(client, table, total):
    body = client.get(f"/api/tables/{table}/preview").json()
    assert body["total"] == total
    assert body["row_count"] == min(100, total)
    assert body["limit"] == 100 and body["offset"] == 0
    assert body["sql"] == f"SELECT *\nFROM {table}\nLIMIT 100;"


def test_preview_orders_pages_to_the_end(client):
    first = client.get("/api/tables/orders/preview?limit=100&offset=0").json()
    assert first["row_count"] == 100 and first["total"] == 2000
    assert first["columns"][0] == "OrderID" and first["rows"][0][0] == 1
    last = client.get("/api/tables/Orders/preview?limit=100&offset=1900").json()
    assert last["row_count"] == 100 and last["rows"][-1][0] == 2000
    assert last["sql"] == "SELECT *\nFROM Orders\nLIMIT 100 OFFSET 1900;"
    beyond = client.get("/api/tables/Orders/preview?offset=5000").json()
    assert beyond["rows"] == [] and beyond["total"] == 2000


@pytest.mark.parametrize(
    "name",
    ["Shipments", "Orders;DROP TABLE Orders", 'Orders"--', "sqlite_master", "Orders%20WHERE%201=1"],
)
def test_unknown_or_injected_table_names_are_404(client, name):
    r = client.get(f"/api/tables/{name}/preview")
    assert r.status_code == 404
    assert r.json() == {"error": "UNKNOWN_TABLE", "available": list(ROW_COUNTS)}


@pytest.mark.parametrize("query", ["limit=0", "limit=501", "offset=-1"])
def test_preview_rejects_bad_paging(client, query):
    assert client.get(f"/api/tables/Orders/preview?{query}").status_code == 422
