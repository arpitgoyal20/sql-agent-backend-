"""rewrite_user_sql (LLM, structured): optimize, debug or explain the user's own SQL.

The user's query is validated first and the findings go into the prompt; only the rewritten
query goes through validate_sql. `explain` of a valid query needs no LLM: the query is passed
through unchanged. `explain` of a broken query is handled as `debug`.
"""

from pydantic import BaseModel, Field

from app import guard
from app.llm import get_llm, render
from app.nodes.common import bullet, schema
from app.state import AgentState
from app.validator import validate


class RewriteDraft(BaseModel):
    sql: str = Field(description="The corrected/optimized SELECT, or the unchanged query")
    issues: list[str] = Field(default_factory=list, description="debug: what is wrong and why")
    fixes: list[str] = Field(default_factory=list, description="Each change made")
    optimization_notes: list[str] = Field(default_factory=list)
    index_suggestions: list[str] = Field(
        default_factory=list, description="CREATE INDEX statements, advice only"
    )
    removed_joins: list[str] = Field(default_factory=list)


async def rewrite_user_sql(state: AgentState) -> dict:
    user_sql = state.get("user_sql") or ""
    dialect = state.get("dialect", "sqlite")
    intent = state["intent"]
    update: dict = {"attempts": state.get("attempts", 0) + 1, "writer": "rewrite_user_sql"}

    findings = state.get("user_sql_findings")
    if not state.get("attempts"):
        checked = validate(user_sql, schema(), dialect)
        findings = checked.errors + checked.warnings
        update["user_sql_findings"] = findings
        if intent == "explain" and checked.errors:
            intent = update["intent"] = "debug"

    if intent == "explain":
        update["candidate_sql"] = user_sql
        return update

    prompt = render(
        "rewriter",
        dialect=dialect,
        user_sql=guard.wrap_user(user_sql, tag="user_sql"),
        intent=intent,
        user_sql_findings=bullet(findings or []),
        validation_errors=bullet(state.get("validation_errors") or []),
        user_message=guard.wrap_user(state["user_input"]),
        existing_indexes=schema().describe_indexes(),
        schema_context=state["schema_context"],
    )
    draft: RewriteDraft = await get_llm().with_structured_output(RewriteDraft).ainvoke(prompt)
    update.update(
        candidate_sql=guard.strip_code_fence(draft.sql),
        issues_found=draft.issues,
        fixes=draft.fixes,
        optimization_notes=draft.optimization_notes,
        # Checked and merged with code-derived advice in validate_sql.
        index_suggestions=draft.index_suggestions,
        removed_joins=draft.removed_joins,
    )
    return update
