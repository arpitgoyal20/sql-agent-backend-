"""Prompt assets stay consistent with the real schema."""

import json

import pytest
import yaml

from app import db
from app.config import ROOT
from app.llm import load_prompt, render
from app.nodes.explain_sql import split_explanation
from app.validator import validate

SHOTS = yaml.safe_load((ROOT / "prompts" / "few_shots.yaml").read_text())
GOLDEN = [json.loads(l) for l in (ROOT / "eval" / "golden.jsonl").read_text().splitlines() if l.strip()]


def test_eight_few_shots():
    assert len(SHOTS) == 8


@pytest.mark.parametrize("shot", SHOTS, ids=lambda s: s["question"][:40])
def test_few_shot_sql_is_valid_and_runs(schema, shot):
    res = validate(shot["sql"], schema)
    assert res.errors == [] and res.warnings == []
    db.execute(res.sqlite_sql)


def test_golden_has_25_entries():
    assert len(GOLDEN) == 25


@pytest.mark.parametrize("entry", GOLDEN, ids=lambda e: e["question"][:40])
def test_golden_sql_is_valid_and_returns_rows(schema, entry):
    res = validate(entry["expected_sql"], schema)
    assert res.errors == [], res.errors
    assert db.execute(res.sqlite_sql).row_count > 0


@pytest.mark.parametrize("name", ["classifier", "generator", "rewriter", "explainer", "clarifier"])
def test_prompts_load(name):
    assert "{" in load_prompt(name)


def test_render_keeps_braces_in_values():
    text = render("clarifier", after_failures="", schema_context="x", user_message="{not a field}")
    assert "{not a field}" in text


def test_split_explanation():
    body, items = split_explanation("It lists rows.\nASSUMPTIONS:\n- a\n* b\n1. c")
    assert body == "It lists rows." and items == ["a", "b", "c"]
    assert split_explanation("Text.\nAssumptions: none") == ("Text.", [])
    assert split_explanation("No marker") == ("No marker", [])
