import pytest

from app.events import BUSY_TEXT, ERROR_TEXT, TokenFilter, error_text


def run(chunks):
    f = TokenFilter()
    out = "".join(f.feed(c) for c in chunks)
    return out + f.flush()


def test_passes_plain_text():
    assert run(["Hello ", "world"]) == "Hello world"


def test_stops_at_marker():
    assert run(["Answer.\n", "ASSUMPTIONS:\n- a"]) == "Answer.\n"


@pytest.mark.parametrize("split", range(1, 12))
def test_marker_split_across_chunks_never_leaks(split):
    marker = "ASSUMPTIONS:"
    text = "Body text. " + marker + " - x"
    i = text.index(marker) + split
    assert run([text[:i], text[i:]]) == "Body text. "


def test_partial_marker_prefix_that_is_not_the_marker_is_released():
    assert run(["The ASSUM", "ED total"]) == "The ASSUMED total"


def test_error_text_mapping():
    assert error_text(RuntimeError("429 RESOURCE_EXHAUSTED quota")) == BUSY_TEXT
    assert error_text(RuntimeError("503 UNAVAILABLE")) == BUSY_TEXT
    assert error_text(ValueError("boom")) == ERROR_TEXT
