"""guard_input (code): reset per-turn state, sanitise, length check, injection scan."""

import logging

import sqlglot
from langchain_core.messages import HumanMessage
from sqlglot import exp
from sqlglot.errors import SqlglotError

from app import guard
from app.nodes.common import now_iso
from app.schema_loader import get_schema
from app.state import PER_TURN_DEFAULTS, AgentState
from app.validator import validate

log = logging.getLogger(__name__)

TOO_LONG_TEXT = (
    f"Your message is longer than {guard.MAX_INPUT_CHARS:,} characters. "
    "Please shorten it and ask again."
)
EMPTY_TEXT = "Please type a question about the database."


PREVIEW_LIMIT = 100  # the LIMIT the UI's table preview adds


def strip_preview_limit(sql: str, dialect: str) -> str:
    """Drop `LIMIT 100 [OFFSET 0]` added by the table preview, so a follow-up such as
    "count them by state" does not inherit a pagination limit. Other limits are kept."""
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except SqlglotError:
        return sql
    limit, offset = tree.args.get("limit"), tree.args.get("offset")
    limit_value = limit.expression if limit is not None else None
    offset_value = offset.expression if offset is not None else None
    is_default_limit = (
        isinstance(limit_value, exp.Literal) and limit_value.is_int and int(limit_value.this) == PREVIEW_LIMIT
    )
    is_zero_offset = offset is None or (
        isinstance(offset_value, exp.Literal) and offset_value.is_int and int(offset_value.this) == 0
    )
    if not (is_default_limit and is_zero_offset):
        return sql.strip().rstrip(";").strip()
    tree.set("limit", None)
    tree.set("offset", None)
    return tree.sql(dialect=dialect)


def adopt_current_sql(state: AgentState, dialect: str) -> str | None:
    """The editor's query, if valid and different from last_sql; else None."""
    current = (state.get("current_sql") or "").strip()
    if not current:
        return None
    result = validate(current, get_schema(), dialect)
    if not result.ok:
        log.info("ignoring invalid current_sql: %s", result.errors[0])
        return None
    adopted = strip_preview_limit(current, dialect)
    return adopted if adopted != (state.get("last_sql") or "") else None


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
    adopted = adopt_current_sql(state, update["dialect"])
    if adopted:
        update["last_sql"] = adopted
    if not text:
        update.update(refusal=EMPTY_TEXT, refusal_reason="out_of_scope", intent="out_of_scope")
    elif guard.is_too_long(text):
        update.update(refusal=TOO_LONG_TEXT, refusal_reason="out_of_scope", intent="out_of_scope")
    else:
        update["injection_patterns"] = guard.detect_injection(text)
    return update
