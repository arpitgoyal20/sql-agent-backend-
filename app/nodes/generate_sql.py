"""generate_sql (LLM, structured): write SQL for `generate` and `modify` intents."""

from pydantic import BaseModel, Field

from app import guard
from app.llm import get_llm, render
from app.nodes.common import bullet, few_shots_text
from app.state import AgentState


class SqlDraft(BaseModel):
    sql: str = Field(description="One SELECT query, plain SQL text without markdown")
    assumptions: list[str] = Field(
        default_factory=list, description="Interpretations made to answer the request"
    )


async def generate_sql(state: AgentState) -> dict:
    use_previous = state.get("intent") == "modify" or state.get("refers_to_previous")
    prompt = render(
        "generator",
        dialect=state.get("dialect", "sqlite"),
        schema_context=state["schema_context"],
        few_shots=few_shots_text(),
        last_sql=(state.get("last_sql") if use_previous else None) or "none",
        validation_errors=bullet(state.get("validation_errors") or []),
        user_message=guard.wrap_user(state["user_input"]),
    )
    draft: SqlDraft = await get_llm().with_structured_output(SqlDraft).ainvoke(prompt)
    return {
        "candidate_sql": guard.strip_code_fence(draft.sql),
        "assumptions": draft.assumptions,
        "attempts": state.get("attempts", 0) + 1,
        "writer": "generate_sql",
    }
