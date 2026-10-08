"""Accuracy eval (§14): run each golden question through the real graph and compare result sets.

Usage: python scripts/run_eval.py [--limit N] [--delay SECONDS] [--verbose]

For each question the graph runs up to (not including) the explainer, so only the classifier
and generator are called. Generated and expected SQL are both executed on the sample DB:
- comparison is order-insensitive unless the expected SQL has a top-level ORDER BY;
- extra columns in the generated result are tolerated (each expected column must match some
  generated column), so "also showed the email" is not a failure;
- floats are compared to 2 decimal places.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
import uuid
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlglot  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from app import db  # noqa: E402
from app.graph import compile_graph  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "eval" / "golden.jsonl"


def run_sql(sql: str) -> tuple[list[str], list[tuple]]:
    conn = db.connect()
    try:
        cur = conn.execute(sql)
        rows = cur.fetchall()
        return [d[0] for d in cur.description], [tuple(_norm(v) for v in r) for r in rows]
    finally:
        conn.close()


def _norm(v):
    return round(v, 2) if isinstance(v, float) else v


def has_top_level_order(sql: str) -> bool:
    return sqlglot.parse_one(sql, read="sqlite").args.get("order") is not None


def same_results(expected: list[tuple], got: list[tuple], ordered: bool) -> bool:
    if len(expected) != len(got):
        return False
    if not expected:
        return True
    n_exp, n_got = len(expected[0]), len(got[0])
    exp_cols = [[r[i] for r in expected] for i in range(n_exp)]
    got_cols = [[r[i] for r in got] for i in range(n_got)]
    # Match each expected column to an unused generated column with the same values.
    key = (lambda c: c) if ordered else (lambda c: Counter(map(repr, c)))
    mapping, used = [], set()
    for col in exp_cols:
        match = next((j for j, g in enumerate(got_cols) if j not in used and key(g) == key(col)), None)
        if match is None:
            return False
        mapping.append(match)
        used.add(match)
    projected = [tuple(r[j] for j in mapping) for r in got]
    return projected == expected if ordered else Counter(projected) == Counter(expected)


async def generate(graph, question: str) -> tuple[str | None, str]:
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    state = await graph.ainvoke({"user_input": question, "dialect": "sqlite", "execute": True}, config)
    note = state.get("intent") or "?"
    if state.get("validation_errors"):
        note += " | " + "; ".join(state["validation_errors"])[:160]
    return state.get("final_sql"), note


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--delay", type=float, default=0.0, help="pause between questions (rate limits)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    entries = [json.loads(l) for l in GOLDEN.read_text().splitlines() if l.strip()][: args.limit]
    graph = compile_graph(InMemorySaver(), interrupt_before=["explain_sql"])
    passed, started = 0, time.time()

    for i, entry in enumerate(entries, 1):
        question, expected_sql = entry["question"], entry["expected_sql"]
        try:
            sql, note = await generate(graph, question)
        except Exception as e:  # provider errors count as failures, run continues
            sql, note = None, f"error: {type(e).__name__}: {str(e)[:120]}"
        ok = False
        if sql:
            try:
                _, expected = run_sql(expected_sql)
                _, got = run_sql(sql)
                ok = same_results(expected, got, has_top_level_order(expected_sql))
            except sqlite3.Error as e:
                note += f" | exec error: {e}"
        passed += ok
        print(f"[{'PASS' if ok else 'FAIL'}] {i:>2}. {question}")
        if args.verbose or not ok:
            print(f"        generated: {' '.join((sql or '-').split())}")
            print(f"        expected:  {expected_sql}")
            print(f"        note:      {note}")
        if args.delay and i < len(entries):
            await asyncio.sleep(args.delay)

    rate = 100 * passed / len(entries) if entries else 0
    print(f"\nPass rate: {passed}/{len(entries)} ({rate:.0f}%) in {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
