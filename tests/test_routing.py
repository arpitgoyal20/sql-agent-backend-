import pytest

from app.graph import (
    route_after_classify,
    route_after_execute,
    route_after_guard,
    route_after_schema,
    route_after_validate,
)


def test_guard_routes_preset_refusal():
    assert route_after_guard({"refusal": "too long"}) == "refuse"
    assert route_after_guard({"refusal": None}) == "classify_intent"


@pytest.mark.parametrize(
    "intent, node",
    [
        ("out_of_scope", "refuse"),
        ("destructive", "refuse"),
        ("clarify", "clarify"),
        ("generate", "retrieve_schema"),
        ("modify", "retrieve_schema"),
        ("optimize", "retrieve_schema"),
        ("debug", "retrieve_schema"),
        ("explain", "retrieve_schema"),
    ],
)
def test_route_after_classify(intent, node):
    assert route_after_classify({"intent": intent}) == node


@pytest.mark.parametrize(
    "intent, node",
    [
        ("generate", "generate_sql"),
        ("modify", "generate_sql"),
        ("optimize", "rewrite_user_sql"),
        ("debug", "rewrite_user_sql"),
        ("explain", "rewrite_user_sql"),
    ],
)
def test_route_after_schema(intent, node):
    assert route_after_schema({"intent": intent}) == node


def test_validate_success_goes_to_execute():
    assert route_after_validate({"validation_errors": [], "attempts": 1}) == "execute_sql"


def test_validate_destructive_short_circuits_even_with_attempts_left():
    state = {"validation_errors": ["DESTRUCTIVE: only SELECT queries are allowed"], "attempts": 1}
    assert route_after_validate(state) == "refuse"


def test_destructive_alongside_multi_still_refuses():
    state = {
        "validation_errors": ["MULTI: exactly one statement allowed", "DESTRUCTIVE: x"],
        "attempts": 1,
        "writer": "generate_sql",
    }
    assert route_after_validate(state) == "refuse"


@pytest.mark.parametrize("writer", ["generate_sql", "rewrite_user_sql"])
def test_validate_failure_retries_same_branch(writer):
    state = {"validation_errors": ["UNKNOWN_COLUMN: x"], "attempts": 2, "writer": writer}
    assert route_after_validate(state) == writer


def test_validate_retry_branch_falls_back_to_intent():
    state = {"validation_errors": ["SYNTAX: x"], "attempts": 1, "intent": "debug"}
    assert route_after_validate(state) == "rewrite_user_sql"


def test_validate_attempts_cap_goes_to_clarify():
    state = {"validation_errors": ["UNKNOWN_COLUMN: x"], "attempts": 3, "writer": "generate_sql"}
    assert route_after_validate(state) == "clarify"


def test_execute_success_goes_to_explain():
    assert route_after_execute({"execution_error": None, "attempts": 1}) == "explain_sql"


def test_execute_error_retries_until_cap():
    state = {"execution_error": "EXECUTION: boom", "attempts": 2, "writer": "generate_sql"}
    assert route_after_execute(state) == "generate_sql"
    assert route_after_execute({**state, "attempts": 3}) == "explain_sql"
