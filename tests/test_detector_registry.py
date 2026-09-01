"""Detection aggregation: exact dedup, containment, ordering, scoring."""

from __future__ import annotations

from pysanitize.detector.base import Detection
from pysanitize.detector.registry import DetectionRegistry, resolve


def D(field, value, start, end, source="rules", confidence=1.0):
    return Detection(field_type=field, value=value, start=start, end=end,
                     page=1, source=source, confidence=confidence)


def test_exact_duplicate_rules_wins_over_llm():
    out = resolve([
        D("person_name", "张三", 4, 6, source="llm", confidence=0.95),
        D("person_name", "张三", 4, 6, source="rules", confidence=0.9),
    ])
    assert len(out) == 1
    assert out[0].source == "rules"


def test_contained_span_dropped():
    # company contains the person name → company mask covers it
    out = resolve([
        D("company_name", "北京某某科技有限公司", 0, 10),
        D("person_name", "某某", 2, 4),
    ])
    assert [d.field_type for d in out] == ["company_name"]


def test_position_sorted_output():
    out = resolve([
        D("person_name", "丙", 8, 9),
        D("phone", "13800000000", 0, 11),
        D("email", "a@b.com", 5, 12),
    ])
    starts = [d.start for d in out]
    assert starts == sorted(starts)


def test_partial_overlap_merges_into_union():
    # phone [0,11) and stock [6,12) overlap on [6,11): the union [0,12) keeps
    # the verbatim union text and an identity whose mask reveals nothing —
    # phone's keep_head/tail would publish the other field's digits, stock's
    # fixed "******" template would not.
    out = resolve([
        D("phone", "13812360051", 0, 11),
        D("stock_code", "600519", 6, 12),
    ])
    assert len(out) == 1
    assert (out[0].start, out[0].end) == (0, 12)
    assert out[0].field_type == "stock_code"
    assert out[0].value == "138123600519"


def test_partial_overlap_prefers_reveal_nothing_mask():
    # Regression: a union that kept the phone identity masked "张三1…" with
    # phone's keep_head=3 — the person name leaked into the sanitized output.
    # The union must take the template-masked field's identity instead.
    out = resolve([
        D("person_name", "张三的账户13800", 0, 10),
        D("phone", "13800138000", 5, 16),
    ])
    assert len(out) == 1
    assert (out[0].start, out[0].end) == (0, 16)
    assert out[0].field_type == "person_name"  # template "***" hides the union
    assert out[0].value == "张三的账户13800138000"  # verbatim union text


def test_contained_span_type_survives_merge_priority():
    # Containment is not a union: the outer field detected exactly this span,
    # so its identity (and mask) stays even when an inner field outranks it.
    out = resolve([
        D("company_name", "13812345600公司", 0, 13),
        D("phone", "13812345600", 0, 11),
    ])
    assert len(out) == 1
    assert out[0].field_type == "company_name"  # inner phone did not grow it
    assert (out[0].start, out[0].end) == (0, 13)


def test_chained_overlaps_merge_into_one_span():
    out = resolve([
        D("person_name", "a" * 10, 0, 10),
        D("company_name", "a" * 10, 5, 15),
        D("person_name", "a" * 8, 12, 20),
    ])
    assert len(out) == 1
    assert (out[0].start, out[0].end) == (0, 20)
    assert out[0].field_type == "company_name"  # outranks person_name
    assert out[0].value == "a" * 20


def test_registry_runs_all_detectors(make_doc, monkeypatch):
    doc = make_doc([("paragraph", "电话 13812345678", 1)])

    class Fake:
        def __init__(self, dets):
            self.dets = dets

        def detect(self, doc):
            return self.dets

    reg = DetectionRegistry([Fake([D("phone", "13812345678", 3, 14)])])
    reg.add(Fake([D("phone", "13812345678", 3, 14, source="llm")]))
    out = reg.detect(doc)
    assert len(out) == 1
    assert out[0].source == "rules"
