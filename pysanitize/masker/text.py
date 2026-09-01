"""Text masking: replace detected spans with per-field placeholders.

Replacement is offset-safe: spans are processed left to right and the output
is rebuilt from unchanged segments, so an earlier span never sees text shifted
by a later replacement. Overlapping spans (rare; the registry already removes
contained ones) are merged so every character is masked at most once.
"""

from __future__ import annotations

from pysanitize.detector.base import Detection
from pysanitize.detector.specs import MaskSpec, load_field_specs

# input span (start, end) → placeholder [out_start, out_end) in the masked text
Placements = dict[tuple[int, int], tuple[int, int]]


def mask_text(
    text: str, detections: list[Detection], specs: dict[str, MaskSpec]
) -> str:
    """Return ``text`` with every detection's span replaced by its field mask.

    Fills ``Detection.masked_value`` on each detection for the audit report.
    See :func:`mask_text_placed` for the placement-recording variant.
    """
    return mask_text_placed(text, detections, specs)[0]


def mask_text_placed(
    text: str, detections: list[Detection], specs: dict[str, MaskSpec]
) -> tuple[str, Placements]:
    """Mask ``text`` and record *exactly* where each placeholder lands.

    Returns ``(masked_text, placements)`` mapping each placed detection's input
    span ``(start, end)`` to its placeholder's ``[out_start, out_end)`` range in
    the returned text. Ranges are computed while building the output, so they
    are exact even when the mask changes the string length — unlike a post-hoc
    text search, they can never match a look-alike string from the document.

    Fills ``Detection.masked_value`` on each detection for the audit report.
    Spans must be position-sorted (``resolve`` output is); overlapping tails
    are merged defensively — an absorbed detection emits no placeholder of its
    own (and gets no placement entry), and any tail the earlier mask does not
    already cover is masked, never dropped.
    """
    ordered = sorted(detections, key=lambda d: (d.start, d.end))
    parts: list[str] = []
    pos = 0
    out_len = 0
    placements: Placements = {}
    for d in ordered:
        if d.start < pos:
            # overlaps the region already masked — extend coverage, no double
            # replacement, and record the mask for audit.
            d.masked_value = specs[d.field_type].mask(d.value)
            if d.end > pos:
                # partially uncovered tail: mask it too — dropping the
                # characters would silently corrupt the text. (``resolve``
                # merges overlaps, so this only guards direct callers.)
                tail = specs[d.field_type].mask(text[pos:d.end])
                parts.append(tail)
                out_len += len(tail)
                pos = d.end
            continue
        gap = text[pos : d.start]
        masked = specs[d.field_type].mask(d.value)
        d.masked_value = masked
        placements[(d.start, d.end)] = (
            out_len + len(gap),
            out_len + len(gap) + len(masked),
        )
        parts.append(gap)
        parts.append(masked)
        out_len += len(gap) + len(masked)
        pos = d.end
    parts.append(text[pos:])
    return "".join(parts), placements


class TextMasker:
    """Holds the field→mask map and masks full document text."""

    def __init__(self, specs: dict[str, MaskSpec] | None = None):
        self.specs = specs or {
            name: spec.mask for name, spec in load_field_specs().items()
        }

    def mask(self, text: str, detections: list[Detection]) -> str:
        return mask_text(text, detections, self.specs)
