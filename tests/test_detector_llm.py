"""LLM detector: chunking, verbatim re-match, hallucination gate, offsets."""

from __future__ import annotations

from pysanitize.detector.llm import (
    LLMDetector,
    _locate_value,
    _parse_findings,
    chunk_text,
)


# ---- chunking ---------------------------------------------------------------


def test_chunk_text_splits_at_block_boundary():
    # place a "\n\n" block boundary inside the last window of chunk 0
    text = "甲" * 5790 + "\n\n" + "乙" * 500
    chunks = chunk_text(text, chunk_size=6000, overlap=300)
    assert chunks[0][0] == 0
    assert chunks[0][1] == text[:5790]  # split exactly at the block boundary
    # second chunk resumes `overlap` chars before the boundary and carries the rest
    assert chunks[1][0] == 5790 - 300
    assert chunks[1][1] == text[5790 - 300:]


def test_chunk_text_single_short():
    chunks = chunk_text("短文本", chunk_size=6000)
    assert chunks == [(0, "短文本")]


def test_chunk_text_no_blank_chunks():
    text = "A" * 10000
    chunks = chunk_text(text, chunk_size=1000, overlap=100)
    assert all(c for _, c in chunks)
    assert chunks[0][0] == 0


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
