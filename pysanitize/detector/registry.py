"""Detection aggregation & overlap resolution.

Combines output from several ``TextDetector``\\ s (rules, LLM) into one clean
list: exact duplicates collapse, a span inside an already-kept span is dropped
because the container's mask already covers it, and partially overlapping
spans merge into their union — identified by the highest-priority field whose
mask reveals nothing, so the union never publishes characters from the other
field's content. The result is ordered by document position, mutually
non-overlapping, and safe to feed to the masker.
"""

from __future__ import annotations

from .base import Detection, TextDetector
from .specs import load_field_specs

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
# Inverted so a higher score means a higher-priority (more exact) field —
# _score must agree with the documented order above.
_TYPE_RANK = {t: len(_TYPE_ORDER) - i for i, t in enumerate(_TYPE_ORDER)}

# Within an identical span+type, the deterministic rules hit beats the LLM's.
_SOURCE_RANK = {"rules": 1, "llm": 0}


def _score(d: Detection) -> float:
    """Higher is better: type rank dominates, then source, then confidence."""
    return (
        _TYPE_RANK.get(d.field_type, 99) * 1000
        + _SOURCE_RANK.get(d.source, 0) * 100
        + d.confidence
    )


def _hides_all(field_type: str) -> bool:
    """Whether the field's mask reveals nothing (a fixed template)."""
    spec = load_field_specs().get(field_type)
    return bool(spec and spec.mask.template)


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

    # 2. Merge overlaps — a span inside an already-kept one is redundant (the
    #    outer mask covers it: "北京某某科技" inside "北京某某科技有限公司"),
    #    and two *partially* overlapping spans become their union: every shared
    #    character is masked exactly once. Leaving partial overlaps to the
    #    masker would corrupt text — it can only skip an absorbed span.
    items = sorted(best.values(), key=lambda d: (d.start, -d.end, -_score(d)))
    kept: list[Detection] = []
    for d in items:
        prev = kept[-1] if kept else None
        if prev is None or d.start >= prev.end:
            kept.append(d)
            continue
        if d.end > prev.end:  # a true union — the span grows past prev
            stronger, weaker = (d, prev) if _score(d) > _score(prev) else (prev, d)
            # A keep_head/keep_tail mask presumes the value is exactly the
            # span its own detector vetted; on a union it would publish
            # characters from the *other* field's content ("张三" kept by a
            # phone mask). A grown union may only take an identity whose mask
            # reveals nothing (a fixed template).
            if not _hides_all(stronger.field_type) and _hides_all(weaker.field_type):
                stronger, weaker = weaker, stronger
            prev.field_type = stronger.field_type
            prev.source = stronger.source
            prev.confidence = stronger.confidence
            prev.value += d.value[prev.end - d.start:]  # verbatim uncovered tail
            prev.end = d.end
        # else: contained — prev's mask already covers it, keep prev as-is.
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
