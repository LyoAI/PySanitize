"""JSON redaction: detections → leaf edits → value replacement + data-level verify.

The structured-data analog of :mod:`pysanitize.redact.xlsx`. Where the xlsx
redactor maps char offsets onto ``(sheet, row, col)`` cells, this maps them
onto leaf paths via the parser's :class:`JsonBox` provenance — exact by
construction, since detection ran on the very strings these leaves render.

"Redaction" means value replacement at the source: a string leaf has its
sensitive spans spliced out in place (keys, nesting, order and sibling
values untouched — the output is a re-serialization, so formatting
normalizes to the source's indent); a number or bool whose rendered form
matched is replaced by the masked *string* (JSON has no masked number —
``--recover`` restores the type from the audit's ``kind`` tag). There is no
off-surface metadata to scrub (no docProps, no comments): every byte of a
JSON document is data, and every leaf sits on the detection surface, so
whatever detection misses, :func:`verify_redaction_json` reports.

Verification is data-level, not byte-level: the output is re-loaded and every
leaf — strings, rendered numbers/bools, and keys (keys are schema and are
never rewritten, so a key echoing a detected value is *reported*, not
silently kept) — is checked for the detection values.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from bisect import bisect_right

from pysanitize.detector.base import Detection
from pysanitize.detector.specs import MaskSpec
from pysanitize.parser.json import format_path, render_scalar
from pysanitize.redact._grams import ValueMatcher
from pysanitize.utils import apply_repls, get_logger

logger = get_logger()


@dataclass
class JsonEdit:
    """One leaf to rewrite: the original rendered text + masked segments.

    Same ``repls`` shape as :class:`pysanitize.redact.xlsx.CellEdit` —
    ``(rel_start, rel_end, placeholder, d_start, d_end, masked_start,
    masked_end)`` within ``text``; ``masked_*`` are the placeholder's exact
    position in the final masked value, so recovery splices by recorded
    position, never by string matching.
    """

    path: tuple  # traversal route from the root (str keys / int indices)
    text: str  # the leaf's rendered original (what detectors saw)
    kind: str = "str"
    repls: list[tuple[int, int, str, int, int, int, int]] = field(default_factory=list)
    start: int = -1
    end: int = -1


def resolve_json_values(
    doc, detections: list[Detection], mask_map: dict[str, MaskSpec]
) -> list[JsonEdit]:
    """Map text detections onto leaf edits via the blocks' JsonBox provenance.

    Per-piece semantics identical to :func:`pysanitize.redact.xlsx.resolve_cells`:
    a detection crossing leaves contributes each leaf its intersected piece.

    Boxes within a block are ascending and non-overlapping, so candidates are
    found by bisecting on box ends — a top-level-object JSON parses to ONE
    block holding every leaf, and scanning it linearly per detection made
    large documents quadratic (30k detections × 100k boxes).
    """
    edits: dict[tuple, JsonEdit] = {}
    box_bounds: dict[int, tuple[list[int], list[int]]] = {}  # id(block) -> (starts, ends)
    for d in detections:
        for block in doc.span(d.start, d.end):
            if not block.json_boxes:
                continue
            lo = max(d.start, block.char_start) - block.char_start
            hi = min(d.end, block.char_end) - block.char_start
            bounds = box_bounds.get(id(block))
            if bounds is None:
                boxes = block.json_boxes
                bounds = ([box.start for box in boxes], [box.end for box in boxes])
                box_bounds[id(block)] = bounds
            starts, ends = bounds
            # candidates sit between "first box ending past lo" and "first box
            # starting past hi" — a range, never a slice copy of a giant block
            for j in range(bisect_right(ends, lo), bisect_right(starts, hi)):
                box = block.json_boxes[j]
                clo, chi = max(lo, box.start), min(hi, box.end)
                if chi <= clo:
                    continue
                edit = edits.get(box.path)
                if edit is None:
                    edit = JsonEdit(path=box.path, text=box.text, kind=box.kind)
                    edits[box.path] = edit
                piece = box.text[clo - box.start : chi - box.start]
                edit.repls.append(
                    (clo - box.start, chi - box.start, mask_map[d.field_type].mask(piece), d.start, d.end, -1, -1)
                )
    out = list(edits.values())
    for edit in out:
        _fill_masked_offsets(edit.repls)
        edit.start, edit.end = edit.repls[0][3], edit.repls[-1][4]
    return out


def _fill_masked_offsets(repls: list[tuple]) -> None:
    """Sort ascending and fill each repl's ``masked_start``/``masked_end``.

    Pure arithmetic (see the xlsx twin) — redact and recover agree by
    construction, and a string holding several look-alike placeholders
    restores exactly.
    """
    repls.sort(key=lambda r: r[0])
    shift = 0
    for i, (rs, re_, masked, ds, de, _, _) in enumerate(repls):
        ms = rs + shift
        repls[i] = (rs, re_, masked, ds, de, ms, ms + len(masked))
        shift += len(masked) - (re_ - rs)


def redact_json(
    doc_path: str | Path,
    edits: list[JsonEdit],
    out_path: str | Path,
) -> Path:
    """Write a redacted copy of ``doc_path`` to ``out_path``.

    Structure and key order are preserved (``json.loads`` keeps insertion
    order; splicing never adds or removes elements). Each rewritten string
    leaf is the source value with its masked spans spliced in — spliced over
    the *original* leaf, not over the detection surface: the parser flattens
    ``\\r``/``\\n`` to spaces for detection (one leaf per line), but that
    substitution is length-preserving, so the same offsets apply to the raw
    value and its newlines survive redaction (and therefore recovery).
    Indent and trailing-newline follow the source (2 spaces when the source
    is single-line/minified → compact output).
    """
    src = Path(doc_path)
    # Stream both ways: a 99 MB export would otherwise be alive three times
    # over at once (source text + parsed tree + serialized copy + its utf-8
    # bytes on write). ``json.load``/``json.dump`` read and write in chunks,
    # and the indent sniff needs only the file's head and last byte.
    indent, newline = _source_style(src)
    with src.open(encoding="utf-8-sig") as fh:
        data = json.load(fh)
    for edit in edits:
        # Splice over the source leaf, not ``edit.text``: the latter is the
        # detection surface, where the parser replaced newlines with spaces.
        # The rewrite is 1:1 in length, so ``repls`` offsets hold for both —
        # using the raw value keeps untouched newlines intact. Non-string
        # leaves (numbers/bools) are wholly replaced by the masked string.
        raw = _get_leaf(data, edit.path)
        on_surface = isinstance(raw, str) and raw.replace("\r", " ").replace("\n", " ") == edit.text
        text = apply_repls(raw if on_surface else edit.text, edit.repls)
        _set_leaf(data, edit.path, text)
    with Path(out_path).open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=indent)
        fh.write(newline)
    logger.info(
        "redacted %d value(s) -> %s", len(edits), out_path
    )
    return Path(out_path)


def verify_redaction_json(out_path: str | Path, values: list[str]) -> list[tuple[str, str]]:
    """Which ``values`` still appear in the redacted JSON — data-level, with paths.

    Re-loads the output and walks everything: string leaves (substring check,
    like the xlsx byte grep), rendered numbers/bools, and *keys* — keys are
    schema and are never rewritten, so a key echoing a detected value is
    reported for the operator to judge, never silently kept. Returns
    ``(value, json-path of first occurrence)`` pairs deduped by value; the
    caller logs them loudly but never fails the run on them.

    Matching uses the shared positional prefilter
    (:class:`pysanitize.redact._grams.ValueMatcher`) — candidate buckets on
    shared prefix grams (ID region codes) held thousands of values and made
    per-leaf matching quadratic on text-heavy documents.
    """
    wanted = {v for v in values if v}
    if not wanted:
        return []
    matcher = ValueMatcher(wanted)
    data = json.loads(Path(out_path).read_text(encoding="utf-8-sig"))
    found: dict[str, str] = {}

    def check(text: str, where: str) -> None:
        matcher.scan(text, where, found)

    def walk(node, path: tuple) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                check(k, format_path(path + (k,)) + " (key)")
                walk(v, path + (k,))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, path + (i,))
        else:
            rendered = render_scalar(node)
            if rendered is not None:
                check(rendered[0], format_path(path))

    walk(data, ())
    return sorted(found.items())


def _get_leaf(data, path: tuple):
    """Read the value at ``path`` — the exact inverse of :func:`_set_leaf`."""
    node = data
    for k in path:
        node = node[k]
    return node


def _set_leaf(data, path: tuple, value) -> None:
    """Assign ``value`` at ``path`` — the exact inverse of the parser's walk."""
    node = data
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = value


def _source_style(src: Path) -> tuple[str | None, str]:
    """``(indent, trailing_newline)`` sniffed from the source *file*.

    The output should look like the input: a two-space-indented file stays
    two-space-indented, a minified one stays minified (``indent=None``). Only
    the head (the first two lines' worth) and the last byte are read — the
    style of a 99 MB export must not cost a 99 MB string.
    """
    with src.open("rb") as fh:
        head = fh.read(4096)
        fh.seek(max(0, src.stat().st_size - 1))
        newline = "\n" if fh.read(1) == b"\n" else ""
    text = head.decode("utf-8-sig", errors="replace")  # head may cut a glyph; only line 2 matters
    second = text.split("\n", 1)[1] if "\n" in text else ""
    indent = None
    if second[:1] in (" ", "\t"):
        indent = second[: len(second) - len(second.lstrip(" \t"))]
    return indent, newline
