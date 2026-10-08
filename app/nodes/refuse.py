"""refuse (code): fixed refusal texts, no LLM (§9)."""

from app.nodes.common import assistant_message
from app.state import AgentState

OUT_OF_SCOPE_TEXT = (
    "I'm designed to assist only with SQL and database-related tasks. "
    "Please ask a question related to the provided database schema."
)
DESTRUCTIVE_TEXT = (
    "I can only generate read-only (SELECT) queries, so I can't help with inserting, "
    "updating, deleting or altering data. I can help you write a SELECT to preview the "
    "rows that would be affected."
)


def refuse(state: AgentState) -> dict:
    if state.get("refusal"):  # preset by guard_input (empty / too long)
        text, reason = state["refusal"], state.get("refusal_reason") or "out_of_scope"
    elif state.get("intent") == "destructive" or any(
        e.startswith("DESTRUCTIVE") for e in state.get("validation_errors") or []
    ):
        text, reason = DESTRUCTIVE_TEXT, "destructive"
    else:
        text, reason = OUT_OF_SCOPE_TEXT, "out_of_scope"
    return {
        "refusal": text,
        "refusal_reason": reason,
        "final_sql": None,
        "messages": [assistant_message(text, kind="refusal", intent=reason)],
    }
