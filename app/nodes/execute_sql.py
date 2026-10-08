"""execute_sql (code): run the validated query on the read-only DB with cap and timeout.
Errors are fed back like validation errors so the writer can retry."""

import sqlite3

from app import db
from app.state import AgentState


def execute_sql(state: AgentState) -> dict:
    if not state.get("execute", True):
        return {"executed": False}
    sql = state.get("exec_sql")
    if not sql:  # could not be translated to SQLite; validate_sql already warned
        return {"executed": False}
    try:
        result = db.execute(sql)
    except (sqlite3.Error, db.QueryTimeout) as e:
        error = f"EXECUTION: {e}"
        return {"executed": False, "execution_error": error, "validation_errors": [error]}
    return {
        "executed": True,
        "execution_error": None,
        "result_columns": result.columns,
        "result_rows": result.rows,
        "row_count": result.row_count,
        "truncated": result.truncated,
    }
