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
