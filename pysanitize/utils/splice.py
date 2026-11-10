"""Splice placeholder replacements into a text — linear, shared by redact/recover.

A masked cell or leaf is its original text with sensitive spans replaced. The
naive way to build it re-copies the whole string once per replacement
(``text = text[:s] + rep + text[e:]``), which costs *count × length* — fine for
a phone number in a short cell, ruinous for a checklist export whose few giant
text leaves hold hundreds of thousands of hits between them (a 36 MB leaf with
357k replacements copies tens of terabytes of intermediate strings: minutes to
hours inside the one stage that logs nothing).

Both directions therefore walk the spans once, in ascending order, and join
the untouched pieces with their replacements. ``repls`` items only need to
carry ``(start, end, replacement)`` in their first three slots — the redactor's
7-tuples and recovery's 5-tuples both do.
"""

from __future__ import annotations

from typing import Iterable, Sequence


def apply_repls(text: str, repls: Iterable[Sequence]) -> str:
    """``text`` with each ``(start, end, replacement)`` spliced in — O(len + n).

    Two detections can cover the exact same range (an ID card also matches the
    bank-card rule); the last one recorded wins, matching the per-replacement
    splice's descending order byte for byte.
    """
    winner: dict[tuple[int, int], str] = {}
    for r in repls:
        winner[(r[0], r[1])] = r[2]
    if not winner:
        return text
    parts: list[str] = []
    prev = 0
    for (start, end), rep in sorted(winner.items()):
        if end <= prev:
            continue  # already covered by a previous (overlapping) replacement
        parts.append(text[prev:start] if start > prev else "")
        parts.append(rep)
        prev = end
    parts.append(text[prev:])
    return "".join(parts)
