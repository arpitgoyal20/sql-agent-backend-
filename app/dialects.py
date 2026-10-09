"""Dialect labels and the message shown when a non-SQLite query cannot run on the demo database.

PostgreSQL and MySQL queries are validated in their own dialect, then translated to SQLite to
run on the bundled sample data. A valid query can still use a feature SQLite lacks (regex `~`,
ARRAY_AGG, EXTRACT ...); that is reported, not "fixed", because the query itself may be right.
"""

from __future__ import annotations

from app.validator import transpile

LABELS = {"sqlite": "SQLite", "postgres": "PostgreSQL", "mysql": "MySQL"}


def unsupported_message(dialect: str, detail: str | None = None) -> str:
    text = f"This {LABELS.get(dialect, dialect)} feature isn't supported on the SQLite demo database."
    return f"{text} SQLite reported: {detail}" if detail else text


def executed_sql(sqlite_sql: str | None, dialect: str) -> str | None:
    """Pretty SQLite text that actually ran, for non-SQLite dialects (None for SQLite)."""
    if dialect == "sqlite" or not sqlite_sql:
        return None
    try:
        return transpile(sqlite_sql, read="sqlite", write="sqlite", pretty=True)
    except Exception:
        return sqlite_sql
