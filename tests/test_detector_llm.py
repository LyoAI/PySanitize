"""LLM detector: chunking, verbatim re-match, hallucination gate, offsets."""

from __future__ import annotations

from pysanitize.detector.llm import (
    LLMDetector,
    _locate_value,
    _parse_findings,
    auto_title_level,
    chunk_blocks,
)
from pysanitize.parser.blocks import Block


# ---- chunking ---------------------------------------------------------------


def test_chunk_blocks_single_short(make_doc):
    doc = make_doc([("paragraph", "短文本", 1)])
    assert chunk_blocks(doc.blocks, doc.text) == [(0, "短文本")]


def test_chunk_blocks_title_opens_new_section(make_doc):
    doc = make_doc([
        ("title", "第一章", 1, 1),  # major title → opens a new chunk
        ("paragraph", "甲" * 100, 1),
        ("title", "第二章", 2, 1),
        ("paragraph", "乙" * 100, 2),
    ])
    chunks = chunk_blocks(doc.blocks, doc.text)
    assert len(chunks) == 2
    assert chunks[0][1].startswith("第一章") and chunks[0][1].endswith("甲" * 100)
    assert chunks[1][1].startswith("第二章")
    # re-joining with the dropped inter-chunk separators reconstructs doc.text
    assert "\n\n".join(c for _, c in chunks) == doc.text


def test_chunk_blocks_level2_title_accumulates(make_doc):
    # a level-2 title (below TITLE_LEVEL_LIMIT) does NOT force a boundary —
    # the whole chapter accumulates into one chunk.
    doc = make_doc([
        ("title", "第一章", 1, 1),
        ("title", "1.1", 1, 2),
        ("paragraph", "甲" * 100, 1),
        ("title", "1.2", 1, 2),
        ("paragraph", "乙" * 100, 1),
    ])
    chunks = chunk_blocks(doc.blocks, doc.text)
    assert len(chunks) == 1
    joined = chunks[0][1]
    assert "第一章" in joined and "1.1" in joined and "1.2" in joined
    assert "\n\n".join(c for _, c in chunks) == doc.text


def test_chunk_blocks_title_unknown_level_forces(make_doc):
    # level=None (no heading info) counts as major — old behavior preserved.
    doc = make_doc([
        ("title", "第一节", 1),
        ("paragraph", "甲" * 100, 1),
        ("title", "第二节", 2),
        ("paragraph", "乙" * 100, 2),
    ])
    assert len(chunk_blocks(doc.blocks, doc.text)) == 2


def test_chunk_blocks_table_is_own_chunk(make_doc):
    table = "| a | b |\n|---|---|"
    doc = make_doc([
        ("paragraph", "正文", 1),
        ("table", table, 1),
        ("paragraph", "尾部", 1),
    ])
    chunks = chunk_blocks(doc.blocks, doc.text)
    assert [c for _, c in chunks] == ["正文", table, "尾部"]


def test_chunk_blocks_skips_meta(make_doc):
    doc = make_doc([
        ("page_header", "内部资料", 1),
        ("paragraph", "正文", 1),
        ("page_footer", "第 1 页", 1),
    ])
    assert "内部资料" not in doc.text
    assert chunk_blocks(doc.blocks, doc.text) == [(0, "正文")]


def test_chunk_blocks_budget_split(make_doc):
    doc = make_doc([("paragraph", f"p{i}" + "甲" * 800, 1) for i in range(10)])
    chunks = chunk_blocks(doc.blocks, doc.text, chunk_size=2000)
    assert len(chunks) > 1
    for base, c in chunks:  # exact-slice contract
        assert doc.text[base : base + len(c)] == c
    assert "\n\n".join(c for _, c in chunks) == doc.text


def test_chunk_blocks_oversized_single_block(make_doc):
    doc = make_doc([("paragraph", "甲" * 5000 + "。乙" * 5000, 1)])
    chunks = chunk_blocks(doc.blocks, doc.text, chunk_size=4000)
    assert len(chunks) > 1
    for base, c in chunks:
        assert doc.text[base : base + len(c)] == c  # exact slices
        assert len(c) <= 4000 + 200  # split lands near the budget
    assert "".join(c for _, c in chunks) == doc.text  # contiguous coverage


def test_chunk_blocks_title_level_limit_none_disables_boundaries(make_doc):
    doc = make_doc([
        ("title", "第一章", 1, 1),
        ("paragraph", "甲" * 100, 1),
        ("title", "第二章", 2, 1),
        ("paragraph", "乙" * 100, 2),
    ])
    chunks = chunk_blocks(doc.blocks, doc.text, title_level_limit=None)
    assert len(chunks) == 1  # titles no longer force boundaries
    assert "\n\n".join(c for _, c in chunks) == doc.text


# ---- auto title level -------------------------------------------------------


def _titles(level_counts: dict[int, int]) -> list[Block]:
    blocks = []
    i = 0
    for level, n in level_counts.items():
        for _ in range(n):
            blocks.append(
                Block(block_id=f"t{i}", type="title", text="x", page=1, order=i, level=level)
            )
            i += 1
    return blocks


def test_auto_title_level_picks_coarse_level():
    # coarse chapters (15) + many subsections (807) → force at level 1
    blocks = _titles({1: 15, 2: 807})
    assert auto_title_level(blocks, text_len=300_000, chunk_size=6000) == 1


def test_auto_title_level_none_when_sections_tiny():
    # all one shallow level, 800 of them → sections too small → no forcing
    blocks = _titles({1: 800})
    assert auto_title_level(blocks, text_len=300_000, chunk_size=6000) is None


def test_auto_title_level_skips_lonely_top_title():
    # a single level-0 doc title is not a chapter boundary → use level 1
    blocks = _titles({0: 1, 1: 10, 2: 200})
    assert auto_title_level(blocks, text_len=300_000, chunk_size=6000) == 1


def test_auto_title_level_uses_deep_level_when_only_that_exists():
    # a doc whose only headings are level 2 → still derives a chapter level
    blocks = _titles({2: 40})
    assert auto_title_level(blocks, text_len=100_000, chunk_size=6000) == 2


def test_auto_title_level_no_titles():
    assert auto_title_level([], text_len=1000, chunk_size=6000) is None


# ---- JSON parsing -----------------------------------------------------------


def test_parse_findings_shapes():
    assert _parse_findings('{"findings": [{"field_type": "phone", "value": "138"}]}') == [
        {"field_type": "phone", "value": "138"}
    ]
    assert _parse_findings('[{"field_type": "phone", "value": "138"}]') == [
        {"field_type": "phone", "value": "138"}
    ]
    # json_repair fallback for trailing junk
    assert _parse_findings('{"findings": [{"field_type": "a", "value": "b"}]} }') == [
        {"field_type": "a", "value": "b"}
    ]
    assert _parse_findings("not json at all") == []
    assert _parse_findings("") == []


# ---- verbatim re-match ------------------------------------------------------


def test_locate_value_verbatim():
    chunk = "联系人：张三，电话 13812345678。"
    ms = _locate_value(chunk, "张三")
    assert len(ms) == 1 and ms[0].span() == (4, 6)
    # whitespace-tolerant fallback: LLM inserted a space where the text has a newline
    ms2 = _locate_value("甲\n乙", "甲 乙")
    assert ms2 and ms2[0].span() == (0, 3)


# ---- detector end-to-end (fake LLM) -----------------------------------------


class FakeLLM:
    def __init__(self, content: str):
        self.content = content
        self.error = None
        self.calls: list[dict] = []

    def invoke(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return self


def _detector_with(doc, findings_json, monkeypatch, model="m"):
    fake = FakeLLM(findings_json)
    monkeypatch.setattr("pysanitize.detector.llm.get_llm", lambda *a, **k: fake)
    det = LLMDetector(model=model, provider="openai")
    detections = det.detect(doc)
    return detections, fake


def test_llm_detector_offsets_and_temp_zero(make_doc, monkeypatch):
    doc = make_doc([
        ("paragraph", "甲方：张三。联系电话 13812345678。", 1),
    ])
    payload = (
        '{"findings": ['
        '{"field_type": "person_name", "value": "张三"},'
        '{"field_type": "phone", "value": "13812345678"}'
        "]}"
    )
    detections, fake = _detector_with(doc, payload, monkeypatch)
    assert len(detections) == 2
    by_type = {d.field_type: d for d in detections}
    assert doc.text[by_type["person_name"].start : by_type["person_name"].end] == "张三"
    assert doc.text[by_type["phone"].start : by_type["phone"].end] == "13812345678"
    assert all(d.source == "llm" for d in detections)
    # temperature pinned at 0, JSON mode requested
    call = fake.calls[0]
    assert call["temperature"] == 0.0
    assert call["response_format"] == {"type": "json_object"}


def test_llm_detector_hallucination_gate(make_doc, monkeypatch):
    doc = make_doc([("paragraph", "本文没有敏感信息。", 1)])
    payload = '{"findings": [{"field_type": "person_name", "value": "不存在的人"}]}'
    detections, _ = _detector_with(doc, payload, monkeypatch)
    assert detections == []  # value never re-matches → dropped


def test_llm_detector_ignores_unknown_field_type(make_doc, monkeypatch):
    doc = make_doc([("paragraph", "张三出现了", 1)])
    payload = '{"findings": [{"field_type": "spaceship", "value": "张三"}]}'
    detections, _ = _detector_with(doc, payload, monkeypatch)
    assert detections == []


def test_llm_detector_value_length_bounds(make_doc, monkeypatch):
    doc = make_doc([("paragraph", "甲 张三 乙", 1)])
    # value too short (1 char) → dropped
    payload = '{"findings": [{"field_type": "person_name", "value": "甲"}]}'
    detections, _ = _detector_with(doc, payload, monkeypatch)
    assert detections == []


def test_llm_detector_degrades_when_client_unavailable(make_doc, monkeypatch):
    doc = make_doc([("paragraph", "电话 13812345678", 1)])

    def boom(*a, **k):
        raise RuntimeError("Missing credentials")

    monkeypatch.setattr("pysanitize.detector.llm.get_llm", boom)
    detections = LLMDetector(model="m", provider="openai").detect(doc)
    assert detections == []  # no hard crash; caller falls back to rules


def test_llm_detector_overlap_dedup(make_doc, monkeypatch):
    # same span reported from two chunks must collapse downstream — here we just
    # assert the LLM detector emits each occurrence with correct page.
    doc = make_doc([("paragraph", "电话 13812345678", 1)])
    payload = '{"findings": [{"field_type": "phone", "value": "13812345678"}]}'
    detections, _ = _detector_with(doc, payload, monkeypatch)
    assert len(detections) == 1
    assert detections[0].page == 1
