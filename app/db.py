"""Read-only access to the sample database (§4.4).

Every connection is opened `mode=ro` with `PRAGMA query_only = ON` and a progress-handler
timeout. This module is never used for the LangGraph checkpointer.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

from app.config import get_settings

# How many SQLite VM instructions between progress-handler callbacks.
_PROGRESS_STEPS = 10_000


class QueryTimeout(Exception):
    """Raised when a query runs longer than the configured timeout."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a read-only connection to the sample database."""
    path = Path(db_path) if db_path else get_settings().sample_db_path
    if not path.exists():
        raise FileNotFoundError(f"Sample database not found at {path}; run scripts/seed.py")
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
    conn.execute("PRAGMA query_only = ON")
    return conn


def _install_timeout(conn: sqlite3.Connection, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    # A non-zero return value makes SQLite abort with "interrupted".
    conn.set_progress_handler(lambda: int(time.monotonic() > deadline), _PROGRESS_STEPS)


def apply_row_cap(sql: str, cap: int, dialect: str = "sqlite") -> str:
    """Return `sql` with a LIMIT of at most `cap` on the outermost query.

    Literal limits larger than `cap` are lowered; a missing limit is added. A limit that is
    not an integer literal is left alone and the whole query is wrapped instead.
    """
    tree = sqlglot.parse_one(sql, read=dialect)
    limit = tree.args.get("limit")
    if limit is None:
        tree = tree.limit(cap, copy=False)
    else:
        value = limit.expression
        if isinstance(value, exp.Literal) and value.is_int:
            if int(value.this) > cap:
                limit.set("expression", exp.Literal.number(cap))
        else:
            wrapped = exp.select("*").from_(tree.subquery("_capped")).limit(cap)
            return wrapped.sql(dialect=dialect)
    return tree.sql(dialect=dialect)


def execute(
    sql: str,
    *,
    db_path: Path | str | None = None,
    row_cap: int | None = None,
    timeout_s: float | None = None,
) -> QueryResult:
    """Run a validated SQLite SELECT with the row cap and timeout applied.

    The cap is injected as `cap + 1` so we can tell whether more rows existed;
    `truncated` is true only when the cap actually cut rows off.
    """
    settings = get_settings()
    cap = row_cap if row_cap is not None else settings.row_cap
    timeout = timeout_s if timeout_s is not None else settings.query_timeout_s

    capped_sql = apply_row_cap(sql, cap + 1)
    conn = connect(db_path)
    try:
        _install_timeout(conn, timeout)
        try:
            cur = conn.execute(capped_sql)
            rows = cur.fetchall()
        except sqlite3.OperationalError as e:
            if "interrupted" in str(e).lower():
                raise QueryTimeout(f"query exceeded the {timeout:g}s timeout") from e
            raise
        columns = [d[0] for d in cur.description or []]
    finally:
        conn.close()

    truncated = len(rows) > cap
    rows = [list(r) for r in rows[:cap]]
    return QueryResult(columns=columns, rows=rows, row_count=len(rows), truncated=truncated)


def explain_query_plan(sql: str, *, db_path: Path | str | None = None) -> None:
    """Ask SQLite to plan `sql` without running it; raises sqlite3.Error if it cannot."""
    conn = connect(db_path)
    try:
        _install_timeout(conn, get_settings().query_timeout_s)
        conn.execute(f"EXPLAIN QUERY PLAN {sql}").fetchall()
    finally:
        conn.close()
