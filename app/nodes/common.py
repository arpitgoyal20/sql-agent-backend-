"""Helpers shared by graph nodes."""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache

import yaml
from langchain_core.messages import AIMessage, BaseMessage

from app.config import ROOT
from app.schema_loader import SchemaInfo, get_schema

MAX_ATTEMPTS = 3


def schema() -> SchemaInfo:
    return get_schema()


def message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    # Some providers return a list of content parts.
    return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)


def recent_messages(messages: list[BaseMessage], limit: int = 6) -> str:
    """The last few turns before the current message, one line each, for the classifier."""
    history = messages[:-1][-limit:]  # the current user message is last
    if not history:
        return "none"
    lines = []
    for m in history:
        role = "user" if m.type == "human" else "assistant"
        text = " ".join(message_text(m).split())[:300]
        sql = (m.additional_kwargs or {}).get("sql")
        lines.append(f"{role}: {text}" + (f" [SQL: {' '.join(sql.split())}]" if sql else ""))
    return "\n".join(lines)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def assistant_message(content: str, **meta) -> AIMessage:
    """Final assistant message of a turn; `meta` (intent, sql, kind) is stored for reloads."""
    kwargs = {k: v for k, v in meta.items() if v}
    return AIMessage(content=content, additional_kwargs={**kwargs, "created_at": now_iso()})


@lru_cache
def few_shots_text() -> str:
    shots = yaml.safe_load((ROOT / "prompts" / "few_shots.yaml").read_text())
    blocks = []
    for s in shots:
        block = f"Q: {s['question']}\n"
        if s.get("previous_sql"):
            block += f"PREVIOUS SQL: {s['previous_sql'].strip()}\n"
        block += f"SQL: {s['sql'].strip()}"
        blocks.append(block)
    return "\n\n".join(blocks)


def bullet(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items) if items else "none"
