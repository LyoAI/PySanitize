"""Text masking: replace detected spans with per-field placeholders.

Replacement is offset-safe: spans are processed left to right and the output
is rebuilt from unchanged segments, so an earlier span never sees text shifted
by a later replacement. Overlapping spans (rare; the registry already removes
contained ones) are merged so every character is masked at most once.
"""

from __future__ import annotations

from pysanitize.detector.base import Detection
from pysanitize.detector.specs import MaskSpec, load_field_specs

from .base import Masker


def mask_text(
    text: str, detections: list[Detection], specs: dict[str, MaskSpec]
) -> str:
    """Return ``text`` with every detection's span replaced by its field mask.

    Fills ``Detection.masked_value`` on each detection for the audit report.
    Spans must be position-sorted (``resolve`` output is); overlapping tails
    are merged defensively.
    """
    ordered = sorted(detections, key=lambda d: (d.start, d.end))
    parts: list[str] = []
    pos = 0
    for d in ordered:
        if d.start < pos:
            # overlaps the region already masked — extend coverage, no double
            # replacement, and record the mask for audit.
            d.masked_value = specs[d.field_type].mask(d.value)
            if d.end > pos:
                pos = d.end
            continue
        parts.append(text[pos : d.start])
        masked = specs[d.field_type].mask(d.value)
        d.masked_value = masked
        parts.append(masked)
        pos = d.end
    parts.append(text[pos:])
    return "".join(parts)


class TextMasker(Masker):
    """Holds the field→mask map and masks full document text."""

    def __init__(self, specs: dict[str, MaskSpec] | None = None):
        self.specs = specs or {
            name: spec.mask for name, spec in load_field_specs().items()
        }

    def mask(self, text: str, detections: list[Detection]) -> str:
        return mask_text(text, detections, self.specs)
