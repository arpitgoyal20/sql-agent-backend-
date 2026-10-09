"""execute_sql (code): run the validated query on the read-only DB with cap and timeout.
Errors are fed back like validation errors so the writer can retry."""

import sqlite3

from app import db
from app.dialects import unsupported_message
from app.state import AgentState

PAGE_SIZE = 100  # first page sent with the chat result; the UI pages through /api/query/run


def execute_sql(state: AgentState) -> dict:
    if not state.get("execute", True):
        return {"executed": False}
    dialect = state.get("dialect", "sqlite")
    sql = state.get("exec_sql")
    if not sql:  # could not be translated to SQLite
        return {"executed": False, "execution_notice": unsupported_message(dialect)}
    try:
        result = db.execute_page(sql, limit=PAGE_SIZE, offset=0)
    except sqlite3.Error as e:
        if dialect != "sqlite":
            # Valid in its own dialect but not runnable on SQLite: report, don't rewrite.
            return {"executed": False, "execution_notice": unsupported_message(dialect, str(e))}
        error = f"EXECUTION: {e}"
        return {"executed": False, "execution_error": error, "validation_errors": [error]}
    except db.QueryTimeout as e:
        error = f"EXECUTION: {e}"
        return {"executed": False, "execution_error": error, "validation_errors": [error]}
    return {
        "executed": True,
        "execution_error": None,
        "result_columns": result.columns,
        "result_rows": result.rows,
        "row_count": result.row_count,
        "truncated": result.total > result.row_count,
        "result_total": result.total,
        "result_limit": result.limit,
    }
