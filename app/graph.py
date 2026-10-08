"""LangGraph wiring (§9): nodes, routers and compile()."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.nodes.clarify import clarify
from app.nodes.classify_intent import classify_intent
from app.nodes.common import MAX_ATTEMPTS
from app.nodes.execute_sql import execute_sql
from app.nodes.explain_sql import explain_sql
from app.nodes.generate_sql import generate_sql
from app.nodes.guard_input import guard_input
from app.nodes.refuse import refuse
from app.nodes.retrieve_schema import retrieve_schema
from app.nodes.rewrite_user_sql import rewrite_user_sql
from app.nodes.validate_sql import validate_sql
from app.state import AgentState

# Labels for progress events; shared with the frontend's progress indicator.
STEP_LABELS = {
    "guard_input": "Checking request",
    "classify_intent": "Understanding request",
    "retrieve_schema": "Reading schema",
    "generate_sql": "Writing SQL",
    "rewrite_user_sql": "Writing SQL",
    "validate_sql": "Validating SQL",
    "execute_sql": "Running query",
    "explain_sql": "Explaining",
    "clarify": "Asking a question",
    "refuse": "Checking scope",
}


# ---- routers (pure functions of state; unit-tested in tests/test_routing.py) ------------


def route_after_guard(state: AgentState) -> str:
    return "refuse" if state.get("refusal") else "classify_intent"


def route_after_classify(state: AgentState) -> str:
    intent = state.get("intent")
    if intent in ("out_of_scope", "destructive"):
        return "refuse"
    if intent == "clarify":
        return "clarify"
    return "retrieve_schema"


def route_after_schema(state: AgentState) -> str:
    if state.get("intent") in ("optimize", "debug", "explain"):
        return "rewrite_user_sql"
    return "generate_sql"


def _writer(state: AgentState) -> str:
    if state.get("writer"):
        return state["writer"]
    return route_after_schema(state)


def route_after_validate(state: AgentState) -> str:
    errors = state.get("validation_errors") or []
    if not errors:
        return "execute_sql"
    if any(e.startswith("DESTRUCTIVE") for e in errors):
        return "refuse"
    if state.get("attempts", 0) < MAX_ATTEMPTS:
        return _writer(state)
    return "clarify"


def route_after_execute(state: AgentState) -> str:
    if state.get("execution_error") and state.get("attempts", 0) < MAX_ATTEMPTS:
        return _writer(state)
    return "explain_sql"


def build_graph() -> StateGraph:
    g = StateGraph(AgentState)
    for name, fn in [
        ("guard_input", guard_input),
        ("classify_intent", classify_intent),
        ("refuse", refuse),
        ("clarify", clarify),
        ("retrieve_schema", retrieve_schema),
        ("generate_sql", generate_sql),
        ("rewrite_user_sql", rewrite_user_sql),
        ("validate_sql", validate_sql),
        ("execute_sql", execute_sql),
        ("explain_sql", explain_sql),
    ]:
        g.add_node(name, fn)

    g.add_edge(START, "guard_input")
    g.add_conditional_edges("guard_input", route_after_guard, ["refuse", "classify_intent"])
    g.add_conditional_edges(
        "classify_intent", route_after_classify, ["refuse", "clarify", "retrieve_schema"]
    )
    g.add_conditional_edges(
        "retrieve_schema", route_after_schema, ["rewrite_user_sql", "generate_sql"]
    )
    g.add_edge("generate_sql", "validate_sql")
    g.add_edge("rewrite_user_sql", "validate_sql")
    g.add_conditional_edges(
        "validate_sql",
        route_after_validate,
        ["execute_sql", "refuse", "generate_sql", "rewrite_user_sql", "clarify"],
    )
    g.add_conditional_edges(
        "execute_sql", route_after_execute, ["generate_sql", "rewrite_user_sql", "explain_sql"]
    )
    g.add_edge("explain_sql", END)
    g.add_edge("refuse", END)
    g.add_edge("clarify", END)
    return g


def compile_graph(checkpointer=None, **kwargs):
    """Compile with the given checkpointer (AsyncSqliteSaver in the app, in-memory in tests).
    Extra kwargs (e.g. interrupt_before) go to StateGraph.compile."""
    return build_graph().compile(checkpointer=checkpointer, **kwargs)
