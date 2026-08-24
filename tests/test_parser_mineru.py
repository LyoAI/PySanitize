"""MinerU projection + wrapper (subprocess mocked, no real parse)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pysanitize.parser.mineru import (
    SUPPORTED_SUFFIXES,
    _normalize_text,
    _project,
    _project_record,
    _run_mineru,
)


# ---- projection -------------------------------------------------------------


def test_project_v2_paginated():
    records = [
        [
            {"type": "title", "content": {"title_content": "标题"}},
            {"type": "paragraph", "content": {"paragraph_content": "正文"}},
        ],
        [{"type": "table", "content": {"html": "<table><tr><td>1</td><td>2</td></tr></table>"}}],
    ]
    blocks = _project(records)
    assert [b.type for b in blocks] == ["title", "paragraph", "table"]
    assert [b.page for b in blocks] == [1, 1, 2]
    assert blocks[0].text == "标题"
    assert "| 1 | 2 |" in blocks[2].text


def test_project_v1_flat():
    records = [
        {"type": "paragraph", "text": "第一段", "page_idx": 0},
        {"type": "paragraph", "text": "第二段", "page_idx": 1},
    ]
    blocks = _project(records)
    assert [b.page for b in blocks] == [1, 2]
    assert [b.text for b in blocks] == ["第一段", "第二段"]


def test_project_v2_span_dict_content():
    records = [[{"type": "paragraph", "content": {"paragraph_content": [{"content": "合"}, {"type": "span_equation_inline", "content": "e"}, {"content": "约"}]}}]]
    blocks = _project(records)
    assert blocks[0].text == "合 e 约"  # inline equations get surrounding spaces


def test_normalize_text_handles_shapes():
    assert _normalize_text(None) == ""
    assert _normalize_text("plain") == "plain"
    assert _normalize_text([{"content": "a"}, {"content": "b"}]) == "ab"
    assert _normalize_text({"content": "nested"}) == "nested"


def test_project_record_ignores_junk():
    assert _project_record({}, 1, 0) is None
    assert _project_record({"type": 5, "content": "x"}, 1, 0) is None
    assert _project_record("not-a-dict", 1, 0) is None


# ---- CLI wrapper -------------------------------------------------------------


def test_unsupported_suffix_raises(tmp_path):
    f = tmp_path / "old.doc"
    f.write_text("x")
    from pysanitize.parser.mineru import parse_blocks

    with pytest.raises(ValueError, match="unsupported"):
        parse_blocks(f, tmp_path / "out")
    assert ".pdf" in SUPPORTED_SUFFIXES
    assert ".doc" not in SUPPORTED_SUFFIXES


def test_missing_file_raises(tmp_path):
    from pysanitize.parser.mineru import parse_blocks

    with pytest.raises(FileNotFoundError):
        parse_blocks(tmp_path / "nope.pdf", tmp_path / "out")


def test_run_mineru_raises_on_failure(tmp_path, monkeypatch):
    class Proc:
        returncode = 3
        stderr = "some mineru failure"

    monkeypatch.setattr("pysanitize.parser.mineru.subprocess.run", lambda *a, **k: Proc())
    from pysanitize.parser.mineru import _mineru_executable

    monkeypatch.setattr("pysanitize.parser.mineru._mineru_executable", lambda: "mineru")
    with pytest.raises(RuntimeError, match="mineru failed"):
        _run_mineru(Path("a.pdf"), Path("out"), backend="pipeline", lang="ch")
