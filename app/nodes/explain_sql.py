"""explain_sql (LLM, streamed text): plain-English explanation plus an ASSUMPTIONS block."""

from __future__ import annotations

import re

from app.llm import get_llm, render
from app.nodes.common import assistant_message, bullet, message_text
from app.state import AgentState

ASSUMPTIONS_MARKER = "ASSUMPTIONS:"
_NONE = re.compile(r"^\s*(none|n/?a|no assumptions)\.?\s*$", re.IGNORECASE)


def split_explanation(text: str) -> tuple[str, list[str]]:
    """Split LLM output into (explanation, assumptions) at the ASSUMPTIONS: marker."""
    idx = text.upper().find(ASSUMPTIONS_MARKER)
    if idx < 0:
        return text.strip(), []
    body, tail = text[:idx].strip(), text[idx + len(ASSUMPTIONS_MARKER):]
    items = []
    for line in tail.splitlines():
        item = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip()
        if item and not _NONE.match(item):
            items.append(item)
    return body, items


async def explain_sql(state: AgentState) -> dict:
    intent = state.get("intent")
    if state.get("execute", True) and state.get("executed"):
        rows = str(state.get("result_total") or state.get("row_count", 0))
    elif state.get("execution_notice"):
        rows = "not run: the SQLite demo database does not support a feature this query uses"
    else:
        rows = "not run"
    changes = (state.get("issues_found") or []) + (state.get("fixes") or [])
    changes += state.get("optimization_notes") or []
    prompt = render(
        "explainer",
        intent=intent,
        final_sql=state.get("final_sql") or "",
        row_count=rows,
        assumptions=bullet(state.get("assumptions") or []),
        changes=bullet(changes),
    )
    chunks = []
    async for chunk in get_llm().astream(prompt):
        chunks.append(message_text(chunk))
    explanation, parsed = split_explanation("".join(chunks))
    assumptions = parsed or state.get("assumptions") or []
    return {
        "explanation": explanation,
        "assumptions": assumptions,
        "messages": [
            assistant_message(
                explanation, kind="answer", intent=intent, sql=state.get("final_sql")
            )
        ],
    }
