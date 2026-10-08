"""validate_sql (code): the only place `final_sql` and `last_sql` are set."""

import re

from app.nodes.common import schema
from app.state import AgentState
from app.validator import check_index_suggestion, index_candidates, tables_in, validate


_INDEX_KEY = re.compile(r"\w+\(\w+")


def validate_sql(state: AgentState) -> dict:
    candidate = (state.get("candidate_sql") or "").strip().rstrip(";").strip()
    dialect = state.get("dialect", "sqlite")
    result = validate(candidate, schema(), dialect)
    if not result.ok:
        return {
            "validation_errors": result.errors,
            "validation_warnings": result.warnings,
            "execution_error": None,
        }

    update = {
        "final_sql": candidate,
        "last_sql": candidate,
        "validation_errors": [],
        "validation_warnings": result.warnings,
        "exec_sql": result.sqlite_sql,
        "execution_error": None,
    }
    if state.get("intent") == "optimize":
        update.update(_optimize_extras(state, candidate, dialect))
    elif state.get("index_suggestions"):
        s = schema()
        checked = (check_index_suggestion(t, s) for t in state["index_suggestions"])
        update["index_suggestions"] = [t for t in checked if t]
    return update


def _optimize_extras(state: AgentState, final_sql: str, dialect: str) -> dict:
    """Keep only index advice that is real, and report removed joins from the SQL itself."""
    s = schema()
    advice: list[str] = []
    seen: set[str] = set()
    llm_advice = [check_index_suggestion(t, s) for t in state.get("index_suggestions") or []]
    for text in [a for a in llm_advice if a] + index_candidates(final_sql, s, dialect):
        key = _INDEX_KEY.search(text).group(0).lower()  # "Table(FirstColumn"
        if key not in seen:
            seen.add(key)
            advice.append(text)

    before = tables_in(state.get("user_sql") or "", s, dialect)
    removed = sorted(before - tables_in(final_sql, s, dialect))
    llm_removed = state.get("removed_joins") or []
    removed_joins = [
        next((r for r in llm_removed if t.lower() in r.lower()), t) for t in removed
    ]
    return {"index_suggestions": advice, "removed_joins": removed_joins}
