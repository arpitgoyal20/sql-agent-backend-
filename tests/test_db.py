import sqlite3
import time

import pytest

from app import db


def test_connection_is_read_only():
    conn = db.connect()
    try:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM Orders")
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE x (a INT)")
    finally:
        conn.close()


@pytest.mark.parametrize(
    "sql, expected",
    [
        ("SELECT 1", "SELECT 1 LIMIT 200"),
        ("SELECT 1 LIMIT 10", "SELECT 1 LIMIT 10"),
        ("SELECT 1 LIMIT 5000", "SELECT 1 LIMIT 200"),
        ("SELECT 1 LIMIT 5000 OFFSET 3", "SELECT 1 LIMIT 200 OFFSET 3"),
        ("SELECT 1 UNION SELECT 2", "SELECT 1 UNION SELECT 2 LIMIT 200"),
        (
            "WITH t AS (SELECT 1 AS a) SELECT a FROM t",
            "WITH t AS (SELECT 1 AS a) SELECT a FROM t LIMIT 200",
        ),
    ],
)
def test_apply_row_cap(sql, expected):
    assert db.apply_row_cap(sql, 200) == expected


def test_execute_truncates_at_cap():
    res = db.execute("SELECT OrderID FROM Orders ORDER BY OrderID")
    assert res.row_count == 200
    assert res.truncated is True
    assert res.columns == ["OrderID"]
    assert res.rows[0] == [1] and res.rows[-1] == [200]


def test_execute_exactly_cap_rows_is_not_truncated():
    res = db.execute("SELECT EmployeeID FROM Employees")  # exactly 200 employees
    assert res.row_count == 200
    assert res.truncated is False


def test_execute_respects_smaller_user_limit():
    res = db.execute("SELECT Name FROM Departments LIMIT 3")
    assert res.row_count == 3
    assert res.truncated is False


def test_execute_times_out():
    slow = (
        "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) "
        "SELECT count(*) FROM n"
    )
    start = time.monotonic()
    with pytest.raises(db.QueryTimeout):
        db.execute(slow, timeout_s=0.3)
    assert time.monotonic() - start < 2


def test_explain_query_plan_rejects_bad_sql():
    db.explain_query_plan("SELECT FirstName FROM Employees")
    with pytest.raises(sqlite3.Error):
        db.explain_query_plan("SELECT nope FROM Employees")


def test_cte_column_list_survives_sqlite_round_trip():
    sql = "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 5) SELECT i FROM n"
    assert "n(i)" in db.apply_row_cap(sql, 200)
    assert db.execute(sql).rows == [[1], [2], [3], [4], [5]]
