"""clarify (LLM, text): one short clarifying question naming real tables/columns.
Also reached after MAX_ATTEMPTS failed validations."""

from app import guard
from app.llm import get_llm, render
from app.nodes.common import MAX_ATTEMPTS, assistant_message, message_text, schema
from app.state import AgentState

FALLBACK = (
    "Could you be more specific? For example, which table (such as Customers, Orders or "
    "Employees) and which columns or filters do you have in mind?"
)


async def clarify(state: AgentState) -> dict:
    failed = state.get("attempts", 0) >= MAX_ATTEMPTS and state.get("validation_errors")
    after = (
        f" (after {MAX_ATTEMPTS} attempts the SQL still failed validation: "
        f"{'; '.join(state['validation_errors'])})"
        if failed
        else ""
    )
    prompt = render(
        "clarifier",
        after_failures=after,
        schema_context=state.get("schema_context") or schema().schema_text(),
        user_message=guard.wrap_user(state["user_input"]),
    )
    reply = await get_llm().ainvoke(prompt)
    text = message_text(reply).strip() or FALLBACK
    return {
        "clarification": text,
        "final_sql": None,
        "messages": [assistant_message(text, kind="clarify", intent="clarify")],
    }
