"""Run one turn of the graph and translate it into the SSE events of §12.

Shared by the API (`main.py`) and the CLI so both see exactly the same event sequence:
step / intent / sql / result / token / explanation / clarify / refusal / error / done.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessageChunk

from app.graph import STEP_LABELS, route_after_execute, route_after_validate
from app.nodes.common import message_text
from app.nodes.explain_sql import ASSUMPTIONS_MARKER
from app.dialects import executed_sql
from app.inspector import checklist, inspect
from app.schema_loader import get_schema
from app.validator import transpile

log = logging.getLogger(__name__)

Event = tuple[str, dict[str, Any]]

ERROR_TEXT = "Something went wrong while answering. Please try again."
BUSY_TEXT = "The AI service is busy or rate-limited right now. Please try again in a minute."


def error_text(exc: BaseException) -> str:
    """User-safe message for an exception; provider details stay in the server log."""
    detail = f"{type(exc).__name__} {exc}"
    if any(s in detail for s in ("429", "RESOURCE_EXHAUSTED", "RateLimit", "503", "UNAVAILABLE")):
        return BUSY_TEXT
    return ERROR_TEXT


class TokenFilter:
    """Pass explainer tokens through until the ASSUMPTIONS: block starts.

    Holds back a tail that could be the start of the marker, so a marker split across
    chunks ("ASSUMP" + "TIONS:") never leaks into the stream.
    """

    def __init__(self, marker: str = ASSUMPTIONS_MARKER) -> None:
        self.marker = marker.upper()
        self.buffer = ""
        self.stopped = False

    def feed(self, text: str) -> str:
        if self.stopped:
            return ""
        self.buffer += text
        upper = self.buffer.upper()
        idx = upper.find(self.marker)
        if idx >= 0:
            out, self.buffer, self.stopped = self.buffer[:idx], "", True
            return out
        keep = 0
        for n in range(min(len(self.marker) - 1, len(upper)), 0, -1):
            if self.marker.startswith(upper[-n:]):
                keep = n
                break
        out = self.buffer[: len(self.buffer) - keep]
        self.buffer = self.buffer[len(self.buffer) - keep :]
        return out

    def flush(self) -> str:
        out, self.buffer = ("" if self.stopped else self.buffer), ""
        return out


def pretty_sql(sql: str, dialect: str) -> str:
    try:
        return transpile(sql, read=dialect, write=dialect, pretty=True)
    except Exception:  # formatting is cosmetic; never fail a turn over it
        return sql


def _step(node: str, status: str, errors: list[str] | None = None) -> Event:
    data = {"node": node, "status": status, "label": STEP_LABELS.get(node, node)}
    if errors:
        data["errors"] = errors
    return "step", data


def _finish_status(node: str, state: dict) -> str:
    if node == "validate_sql":
        return "retry" if route_after_validate(state) in ("generate_sql", "rewrite_user_sql") else "ok"
    if node == "execute_sql":
        if state.get("execution_error"):
            return "retry" if route_after_execute(state) != "explain_sql" else "ok"
        return "ok" if state.get("executed") else "skip"  # notice: shown in the result event
    return "ok"


def _events_for_update(node: str, state: dict, update: dict, tokens: TokenFilter) -> list[Event]:
    events: list[Event] = []
    if node in ("classify_intent", "rewrite_user_sql") and update.get("intent"):
        events.append(("intent", {"intent": update["intent"]}))
    if node == "validate_sql" and update.get("final_sql"):
        dialect = state.get("dialect", "sqlite")
        events.append(
            (
                "sql",
                {
                    "sql": pretty_sql(state["final_sql"], dialect),
                    "dialect": dialect,
                    "warnings": state.get("validation_warnings") or [],
                    "optimization_notes": state.get("optimization_notes") or [],
                    "index_suggestions": state.get("index_suggestions") or [],
                    "issues": state.get("issues_found") or [],
                    "removed_joins": state.get("removed_joins") or [],
                    "validation": checklist(state.get("validation_warnings") or [], dialect),
                    "inspection": inspect(state["final_sql"], get_schema(), dialect),
                    "modified_previous": state.get("intent") == "modify",
                    "original_sql": (
                        state.get("user_sql") if state.get("intent") in ("optimize", "debug") else None
                    ),
                    "executed_sql": executed_sql(state.get("exec_sql"), dialect),
                },
            )
        )
    if node == "execute_sql" and update.get("execution_notice"):
        events.append(
            (
                "result",
                {
                    "columns": [], "rows": [], "row_count": 0, "truncated": False,
                    "total": 0, "limit": 0, "offset": 0,
                    "error": update["execution_notice"],
                },
            )
        )
    if node == "execute_sql" and update.get("executed"):
        events.append(
            (
                "result",
                {
                    "columns": state.get("result_columns") or [],
                    "rows": state.get("result_rows") or [],
                    "row_count": state.get("row_count", 0),
                    "truncated": bool(state.get("truncated")),
                    "total": state.get("result_total", state.get("row_count", 0)),
                    "limit": state.get("result_limit") or state.get("row_count", 0),
                    "offset": 0,
                },
            )
        )
    if node == "explain_sql":
        rest = tokens.flush()
        if rest.strip():
            events.append(("token", {"text": rest}))
        events.append(
            (
                "explanation",
                {"text": state.get("explanation", ""), "assumptions": state.get("assumptions") or []},
            )
        )
    if node == "clarify":
        events.append(("clarify", {"text": state.get("clarification") or ""}))
    if node == "refuse":
        events.append(
            (
                "refusal",
                {"text": state.get("refusal") or "", "reason": state.get("refusal_reason") or "out_of_scope"},
            )
        )
    return events


async def run_turn(
    graph,
    *,
    thread_id: str,
    message: str,
    dialect: str = "sqlite",
    execute: bool = True,
    current_sql: str | None = None,
) -> AsyncIterator[Event]:
    """Yield (event, data) pairs for one turn. Always ends with `done`; never raises."""
    config = {"configurable": {"thread_id": thread_id}}
    inputs = {"user_input": message, "dialect": dialect, "execute": execute, "current_sql": current_sql}
    state: dict[str, Any] = {"dialect": dialect, "execute": execute}
    tokens = TokenFilter()

    try:
        async for mode, chunk in graph.astream(
            inputs, config, stream_mode=["updates", "messages", "tasks"]
        ):
            if mode == "tasks":
                # Task-start payloads carry "input"; finishes are reported via "updates".
                if "input" in chunk and chunk.get("name") in STEP_LABELS:
                    yield _step(chunk["name"], "start")
            elif mode == "messages":
                msg, meta = chunk
                if meta.get("langgraph_node") == "explain_sql" and isinstance(msg, AIMessageChunk):
                    text = tokens.feed(message_text(msg))
                    if text:
                        yield "token", {"text": text}
            elif mode == "updates":
                for node, update in chunk.items():
                    if node not in STEP_LABELS:
                        continue
                    update = update or {}
                    state.update({k: v for k, v in update.items() if k != "messages"})
                    status = _finish_status(node, state)
                    errors = state.get("validation_errors") if status == "retry" else None
                    yield _step(node, status, errors)
                    for event in _events_for_update(node, state, update, tokens):
                        yield event
    except Exception as exc:
        log.exception("turn failed (thread %s)", thread_id)
        yield "error", {"text": error_text(exc)}

    yield "done", {"thread_id": thread_id, "intent": state.get("intent")}
