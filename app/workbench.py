"""Workbench endpoints' logic (CHANGES-v2 §2): table browser, table preview, run-from-editor.

No LLM is involved, but every query still goes through `validator.validate()` and then the
read-only connection. Nothing here executes unvalidated SQL.
"""

from __future__ import annotations

import re
import sqlite3

from app import db
from app.dialects import executed_sql, unsupported_message
from app.events import pretty_sql
from app.nodes.refuse import DESTRUCTIVE_TEXT
from app.schema_loader import get_schema
from app.validator import validate

MAX_SQL_CHARS = 10_000
TIMEOUT_TEXT = "Query took longer than 5 seconds and was stopped."
_SIMPLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def tables_payload() -> dict:
    """Tables with row counts and columns (PK, FK target, doc), for the navigator.

    Each table also keeps `foreign_keys` so `GET /api/schema` can return the same data.
    """
    schema = get_schema()
    counts = db.row_counts()
    tables = []
    for name, columns in schema.tables.items():
        fks = [fk for fk in schema.foreign_keys if fk.table == name]
        fk_by_column = {fk.column.lower(): fk for fk in fks}
        tables.append(
            {
                "name": name,
                "row_count": counts.get(name, 0),
                "columns": [
                    {
                        "name": c.name,
                        "type": c.type,
                        "pk": c.pk,
                        "fk": (
                            {"table": fk.ref_table, "column": fk.ref_column}
                            if (fk := fk_by_column.get(c.name.lower()))
                            else None
                        ),
                        "nullable": not (c.not_null or c.pk),
                        "doc": c.doc,
                    }
                    for c in columns
                ],
                "foreign_keys": [
                    {"column": fk.column, "ref_table": fk.ref_table, "ref_column": fk.ref_column}
                    for fk in fks
                ],
            }
        )
    return {"tables": tables}


def preview(name: str, limit: int, offset: int) -> dict | None:
    """`SELECT *` on one table, one page at a time. None if the table does not exist.

    The SQL is built from the schema's canonical name, never from the raw request value.
    """
    table = get_schema().resolve_table(name)
    if table is None:
        return None
    ident = table if _SIMPLE_NAME.match(table) else '"' + table.replace('"', '""') + '"'
    base = f"SELECT *\nFROM {ident}"
    shown = f"{base}\nLIMIT {limit}" + (f" OFFSET {offset}" if offset else "") + ";"
    check = validate(base, get_schema(), "sqlite")
    if not check.ok:  # cannot happen for a real table; never execute unvalidated SQL
        raise RuntimeError(f"preview SQL failed validation: {check.errors}")
    page = db.execute_page(check.sqlite_sql, limit=limit, offset=offset)
    return {
        "sql": shown,
        "columns": page.columns,
        "rows": page.rows,
        "row_count": page.row_count,
        "total": page.total,
        "limit": page.limit,
        "offset": page.offset,
        "elapsed_ms": page.elapsed_ms,
    }


def run_query(sql: str, dialect: str, limit: int, offset: int) -> dict:
    """Validate and run SQL from the editor; always returns a status payload (CHANGES-v2 §2.3)."""
    if len(sql) > MAX_SQL_CHARS:
        return {
            "status": "invalid",
            "errors": [f"TOO_LONG: queries are limited to {MAX_SQL_CHARS:,} characters"],
            "warnings": [],
        }
    check = validate(sql, get_schema(), dialect)
    if check.destructive:
        return {"status": "refused", "text": DESTRUCTIVE_TEXT}
    if not check.ok:
        return {"status": "invalid", "errors": check.errors, "warnings": check.warnings}
    ran_as = executed_sql(check.sqlite_sql, dialect)
    if not check.sqlite_sql:
        return {"status": "error", "text": unsupported_message(dialect), "executed_sql": None}
    try:
        page = db.execute_page(check.sqlite_sql, limit=limit, offset=offset)
    except db.QueryTimeout:
        return {"status": "error", "text": TIMEOUT_TEXT, "executed_sql": ran_as}
    except sqlite3.Error as e:
        text = (
            unsupported_message(dialect, str(e))
            if dialect != "sqlite"
            else f"The database returned: {e}"
        )
        return {"status": "error", "text": text, "executed_sql": ran_as}
    return {
        "status": "ok",
        "sql": pretty_sql(sql.strip().rstrip(";"), dialect),
        "columns": page.columns,
        "rows": page.rows,
        "row_count": page.row_count,
        "total": page.total,
        "limit": page.limit,
        "offset": page.offset,
        "elapsed_ms": page.elapsed_ms,
        "warnings": check.warnings,
        "executed_sql": ran_as,  # the SQLite that ran, for non-SQLite dialects
    }
