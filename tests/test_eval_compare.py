import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "run_eval", Path(__file__).resolve().parent.parent / "scripts" / "run_eval.py"
)
run_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_eval)


def test_identical_unordered():
    assert run_eval.same_results([(1, "a"), (2, "b")], [(2, "b"), (1, "a")], ordered=False)


def test_order_matters_when_expected_is_ordered():
    assert not run_eval.same_results([(1,), (2,)], [(2,), (1,)], ordered=True)


def test_extra_and_reordered_columns_tolerated():
    expected = [(1, "a"), (2, "b")]
    got = [("a", "x@e", 1), ("b", "y@e", 2)]
    assert run_eval.same_results(expected, got, ordered=False)


def test_different_rows_fail():
    assert not run_eval.same_results([(1,)], [(1,), (2,)], ordered=False)
    assert not run_eval.same_results([(1,), (2,)], [(1,), (3,)], ordered=False)


def test_top_level_order_detection():
    assert run_eval.has_top_level_order("SELECT a FROM t ORDER BY a")
    assert not run_eval.has_top_level_order("SELECT a FROM (SELECT a FROM t ORDER BY a)")
