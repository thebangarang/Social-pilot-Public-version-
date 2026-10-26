"""Agent parsing (pure) tests."""
import pytest

from app.agents import _parse_proposals


def test_parse_proposals_json():
    raw = '{"proposals":[{"number":1,"caption":"c","hashtags":["#a"],"hook":"h","cta":"t","media_prompt":"m"}]}'
    out = _parse_proposals(raw)
    assert len(out) == 1
    assert out[0]["caption"] == "c"
    assert out[0]["hashtags"] == ["#a"]


def test_parse_proposals_fenced():
    raw = '```json\n{"proposals":[{"number":1,"caption":"c","hashtags":[],"hook":"","cta":"","media_prompt":""}]}\n```'
    assert _parse_proposals(raw)[0]["caption"] == "c"


def test_parse_proposals_invalid():
    with pytest.raises(RuntimeError):
        _parse_proposals("pas du json du tout")


def test_parse_proposals_bare_object():
    # LLM sometimes drops the "proposals" wrapper (single rework case).
    raw = '{"number":2,"caption":"c2","hashtags":[],"hook":"","cta":"","media_prompt":"m2"}'
    out = _parse_proposals(raw)
    assert len(out) == 1
    assert out[0]["caption"] == "c2"


def test_parse_proposals_bare_list():
    raw = '[{"number":1,"caption":"a"},{"number":2,"caption":"b"}]'
    out = _parse_proposals(raw)
    assert len(out) == 2
    assert out[1]["caption"] == "b"
