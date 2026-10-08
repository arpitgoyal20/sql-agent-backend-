"""LangGraph state (§6). Only `messages` and `last_sql` carry over between turns;
`guard_input` resets everything else at the start of each turn."""

from typing import Annotated, Literal, TypedDict

from langgraph.graph.message import add_messages

Intent = Literal[
    "generate", "modify", "optimize", "debug", "explain", "destructive", "out_of_scope", "clarify"
]
DialectName = Literal["sqlite", "postgres", "mysql"]
WriterNode = Literal["generate_sql", "rewrite_user_sql"]


class AgentState(TypedDict, total=False):
    # --- from §6 ---
    messages: Annotated[list, add_messages]
    user_input: str
    intent: Intent
    user_sql: str | None
    refers_to_previous: bool
    schema_context: str
    candidate_sql: str | None
    validation_errors: list[str]
    validation_warnings: list[str]
    attempts: int
    final_sql: str | None
    last_sql: str | None  # persists across turns
    optimization_notes: list[str]
    index_suggestions: list[str]
    issues_found: list[str]  # debug mode
    result_columns: list[str]
    result_rows: list[list]
    row_count: int
    truncated: bool
    explanation: str
    assumptions: list[str]
    clarification: str | None
    refusal: str | None
    dialect: DialectName
    execute: bool

    # --- additions (see README → Assumptions) ---
    refusal_reason: Literal["out_of_scope", "destructive"] | None
    injection_patterns: list[str]  # guard matches; forces out_of_scope unless clearly SQL
    writer: WriterNode | None  # which generator branch to retry
    user_sql_findings: list[str]  # validator output on the user's own SQL (rewrite modes)
    fixes: list[str]
    removed_joins: list[str]
    exec_sql: str | None  # SQLite text that execute_sql runs (set by validate_sql)
    executed: bool
    execution_error: str | None
    thread_title: str | None  # generated on the first turn, for the sidebar


# Every per-turn field and its reset value. `messages`, `last_sql`, `dialect` and `execute`
# are not here: the first two carry over, the last two arrive with each request.
PER_TURN_DEFAULTS: dict = {
    "intent": None,
    "user_sql": None,
    "refers_to_previous": False,
    "schema_context": "",
    "candidate_sql": None,
    "validation_errors": [],
    "validation_warnings": [],
    "attempts": 0,
    "final_sql": None,
    "optimization_notes": [],
    "index_suggestions": [],
    "issues_found": [],
    "result_columns": [],
    "result_rows": [],
    "row_count": 0,
    "truncated": False,
    "explanation": "",
    "assumptions": [],
    "clarification": None,
    "refusal": None,
    "refusal_reason": None,
    "injection_patterns": [],
    "writer": None,
    "user_sql_findings": [],
    "fixes": [],
    "removed_joins": [],
    "exec_sql": None,
    "executed": False,
    "execution_error": None,
    "thread_title": None,
}
