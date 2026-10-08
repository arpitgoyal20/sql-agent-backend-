"""Try the agent from a terminal: python -m app.cli ["question"] [--dialect postgres] ...

With a question, runs one turn; without, starts a REPL (/new starts a fresh thread,
/quit exits). Uses the same checkpointer and event stream as the API.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.config import get_settings
from app.events import run_turn
from app.graph import compile_graph

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"


def render(event: str, data: dict) -> str | None:
    if event == "step":
        mark = {"start": "…", "ok": "✓", "retry": "↻", "skip": "–"}[data["status"]]
        return f"{DIM}  {mark} {data['label']}{RESET}" if data["status"] != "start" else None
    if event == "intent":
        return f"{BOLD}intent:{RESET} {data['intent']}"
    if event == "sql":
        out = [f"{BOLD}SQL ({data['dialect']}):{RESET}\n{data['sql']}"]
        for key in ("warnings", "issues", "optimization_notes", "index_suggestions", "removed_joins"):
            if data[key]:
                out.append(f"{BOLD}{key}:{RESET}\n" + "\n".join(f"  - {x}" for x in data[key]))
        return "\n".join(out)
    if event == "result":
        cols, rows = data["columns"], data["rows"]
        lines = [" | ".join(cols)] + [" | ".join(map(str, r)) for r in rows[:10]]
        more = f"\n  … {data['row_count']} rows" + (" (truncated at cap)" if data["truncated"] else "")
        return f"{BOLD}result:{RESET}\n  " + "\n  ".join(lines) + more
    if event == "token":
        return None  # printed inline below
    if event == "explanation":
        extra = "".join(f"\n  - {a}" for a in data["assumptions"])
        return f"\n{BOLD}assumptions:{RESET}{extra}" if extra else ""
    if event in ("clarify", "refusal", "error"):
        return f"{BOLD}{event}:{RESET} {data['text']}"
    if event == "done":
        return f"{DIM}done (thread {data['thread_id']}){RESET}"
    return None


async def ask(graph, thread_id: str, message: str, args) -> None:
    streaming = False
    async for event, data in run_turn(
        graph, thread_id=thread_id, message=message, dialect=args.dialect, execute=not args.no_execute
    ):
        if args.raw:
            print(json.dumps({"event": event, "data": data}, default=str))
            continue
        if event == "token":
            if not streaming:
                print(f"{BOLD}explanation:{RESET} ", end="")
                streaming = True
            print(data["text"], end="", flush=True)
            continue
        line = render(event, data)
        if line is not None:
            print(("\n" if streaming else "") + line if line else "", flush=True)
            streaming = False


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message", nargs="?", help="ask one question and exit")
    parser.add_argument("--thread", default=None, help="thread id (default: new uuid)")
    parser.add_argument("--dialect", choices=["sqlite", "postgres", "mysql"], default="sqlite")
    parser.add_argument("--no-execute", action="store_true", help="do not run the query")
    parser.add_argument("--raw", action="store_true", help="print raw SSE events as JSON")
    args = parser.parse_args()

    settings = get_settings()
    async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_db_path)) as saver:
        graph = compile_graph(saver)
        thread_id = args.thread or str(uuid.uuid4())
        if args.message:
            await ask(graph, thread_id, args.message, args)
            return
        print(f"SQL agent CLI. thread={thread_id}  (/new, /quit)")
        while True:
            try:
                message = (await asyncio.to_thread(input, "\n> ")).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if message in ("/quit", "/exit"):
                break
            if message == "/new":
                thread_id = str(uuid.uuid4())
                print(f"new thread {thread_id}")
                continue
            if message:
                await ask(graph, thread_id, message, args)


if __name__ == "__main__":
    asyncio.run(main())
