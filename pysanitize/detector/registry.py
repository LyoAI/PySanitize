"""Detection aggregation & overlap resolution.

Combines output from several ``TextDetector``\\ s (rules, LLM) into one clean
list: exact duplicates collapse, and a span fully contained inside an
already-kept span is dropped because the container's mask already covers it.
The result is ordered by document position and is safe to feed to the masker.
"""

from __future__ import annotations

from .base import Detection, TextDetector

# Priority when two different-type detections overlap: exact (regex) fields
# win over fuzzy person/company, and company over person (a person name inside
# a company name — "张三科技有限公司" — is masked by the company anyway).
_TYPE_ORDER = (
    "bank_account",
    "id_card",
    "credit_code",
    "phone",
    "email",
    "stock_code",
    "company_name",
    "person_name",
)
_TYPE_RANK = {t: i for i, t in enumerate(_TYPE_ORDER)}

# Within an identical span+type, the deterministic rules hit beats the LLM's.
_SOURCE_RANK = {"rules": 1, "llm": 0}


def _score(d: Detection) -> float:
    """Higher is better: type rank dominates, then source, then confidence."""
    return (
        _TYPE_RANK.get(d.field_type, 99) * 1000
        + _SOURCE_RANK.get(d.source, 0) * 100
        + d.confidence
    )


def resolve(detections: list[Detection]) -> list[Detection]:
    """Dedup and de-overlap ``detections``; returns position-sorted spans."""
    # 1. Exact dedup by (field_type, start, end) — rules beats LLM, else
    #    higher confidence ("联系人：王小明" hits from both rounds collapse).
    best: dict[tuple[str, int, int], Detection] = {}
    for d in detections:
        key = (d.field_type, d.start, d.end)
        prev = best.get(key)
        if prev is None or _score(d) > _score(prev):
            best[key] = d

    # 2. Containment — an outer span masks everything inside it, so the inner
    #    detection is redundant ("北京某某科技" inside "北京某某科技有限公司").
    items = sorted(best.values(), key=lambda d: (d.start, -d.end, -_score(d)))
    kept: list[Detection] = []
    spans: list[tuple[int, int]] = []
    for d in items:
        if any(s <= d.start and d.end <= e for s, e in spans):
            continue
        spans.append((d.start, d.end))
        kept.append(d)
    return sorted(kept, key=lambda d: (d.start, d.end))


class DetectionRegistry:
    """Runs one or more ``TextDetector``\\ s and resolves their combined output."""

    def __init__(self, detectors: list[TextDetector] | None = None):
        self.detectors: list[TextDetector] = detectors or []

    def add(self, detector: TextDetector) -> None:
        self.detectors.append(detector)

    def detect(self, doc) -> list[Detection]:
        """Run every registered detector and resolve overlaps."""
        found: list[Detection] = []
        for detector in self.detectors:
            found.extend(detector.detect(doc))
        return resolve(found)
