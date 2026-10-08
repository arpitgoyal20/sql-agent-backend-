"""guard_input (code): reset per-turn state, sanitise, length check, injection scan."""

from langchain_core.messages import HumanMessage

from app import guard
from app.nodes.common import now_iso
from app.state import PER_TURN_DEFAULTS, AgentState

TOO_LONG_TEXT = (
    f"Your message is longer than {guard.MAX_INPUT_CHARS:,} characters. "
    "Please shorten it and ask again."
)
EMPTY_TEXT = "Please type a question about the database."


def guard_input(state: AgentState) -> dict:
    text = guard.sanitize(state.get("user_input", ""))
    update = {
        **PER_TURN_DEFAULTS,
        "user_input": text,
        "dialect": state.get("dialect") or "sqlite",
        "execute": state.get("execute", True),
        "messages": [
            HumanMessage(
                content=text[: guard.MAX_INPUT_CHARS], additional_kwargs={"created_at": now_iso()}
            )
        ],
    }
    if not text:
        update.update(refusal=EMPTY_TEXT, refusal_reason="out_of_scope", intent="out_of_scope")
    elif guard.is_too_long(text):
        update.update(refusal=TOO_LONG_TEXT, refusal_reason="out_of_scope", intent="out_of_scope")
    else:
        update["injection_patterns"] = guard.detect_injection(text)
    return update
