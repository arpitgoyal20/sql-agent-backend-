"""Input guard (§10): length limit, control-character stripping, injection patterns, and
safe wrapping of user text for prompts. Pure code, no LLM."""

from __future__ import annotations

import re

MAX_INPUT_CHARS = 4000

INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (name, re.compile(rx, re.IGNORECASE))
    for name, rx in [
        ("ignore_instructions", r"\bignore\s+(?:all\s+|the\s+|any\s+)?(?:all|previous|above|prior)\s+(?:instructions|rules|prompts?)\b"),
        ("you_are_now", r"\byou\s+are\s+now\b"),
        ("system_prompt", r"\bsystem\s+prompt\b"),
        ("developer_mode", r"\bdeveloper\s+mode\b"),
        ("act_as", r"\bact\s+as\b"),
        ("system_tag", r"<\s*/?\s*system\s*>"),
        ("user_message_tag", r"<\s*/\s*user_message\s*>"),
        ("forget_rules", r"\bforget\s+(?:all\s+)?(?:your|the|previous)\s+(?:rules|instructions)\b"),
        ("jailbreak", r"\bjailbreak"),
    ]
]

# Control characters except tab, newline and carriage return.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Pasted SQL: SELECT ... FROM, a CTE header (`WITH name AS (`), or a code fence.
_SQL_HINT = re.compile(
    r"```|\bselect\b[\s\S]+?\bfrom\b|\bwith\s+(?:recursive\s+)?\w+(?:\s*\([^)]*\))?\s+as\s*\(",
    re.IGNORECASE,
)


def sanitize(text: str) -> str:
    return _CONTROL_CHARS.sub("", text or "").strip()


def is_too_long(text: str) -> bool:
    return len(text) > MAX_INPUT_CHARS


def detect_injection(text: str) -> list[str]:
    """Names of the injection patterns found in `text` (empty list if none)."""
    return [name for name, rx in INJECTION_PATTERNS if rx.search(text or "")]


def looks_like_sql(text: str) -> bool:
    return bool(_SQL_HINT.search(text or ""))


def wrap_user(text: str, tag: str = "user_message") -> str:
    """Wrap user text in <tag>...</tag>, neutralising any tag-like text inside it."""
    escaped = re.sub(
        rf"<\s*(/?)\s*{tag}\s*>", lambda m: f"&lt;{m.group(1)}{tag}&gt;", text, flags=re.I
    )
    return f"<{tag}>{escaped}</{tag}>"


_FENCE = re.compile(r"```(?:sql)?\s*([\s\S]*?)```", re.IGNORECASE)
_RAW_SQL = re.compile(r"\b(?:select\b|with\s+(?:recursive\s+)?\w+(?:\s*\([^)]*\))?\s+as\s*\()[\s\S]*", re.IGNORECASE)


def extract_sql(text: str) -> str | None:
    """Pull pasted SQL out of a message: a fenced block first, else from SELECT/WITH on."""
    fence = _FENCE.search(text or "")
    if fence and fence.group(1).strip():
        return fence.group(1).strip()
    if looks_like_sql(text):
        raw = _RAW_SQL.search(text)
        if raw:
            return raw.group(0).strip()
    return None


def strip_code_fence(sql: str) -> str:
    """LLMs sometimes wrap SQL in ``` fences even in structured output."""
    fence = _FENCE.search(sql or "")
    return (fence.group(1) if fence else sql or "").strip()
