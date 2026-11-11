"""XLSX redaction: detections → cell edits → value replacement + scrubbing.

The spreadsheet analog of :mod:`pysanitize.redact.pdf`. Where the PDF redactor
maps char offsets onto page rectangles (geometry), this maps them onto
``(sheet, row, col)`` via the parser's :class:`CellBox` provenance — exact by
construction, since detection ran on the very strings these cells render.

"Redaction" here means *value replacement at the source*: an xlsx has no
overlay to draw, the sensitive bytes live in the sheet XML / shared strings,
so a masked cell has its value rewritten (a formula in that cell dies with it
— the displayed value is the sensitive thing). Beyond edited cells:

- docProps (creator / lastModifiedBy / title / …) — always scrubbed; author
  metadata is the classic silent xlsx leak.
- cached formula results of *other* cells never survive: any cell whose
  rendered value contains a detection was detected itself and edited.
- cell comments, sheet names and formula *literals* echoing a detection
  value are scrubbed — value-based, since the value is proven sensitive by a
  detection elsewhere; over-redaction of the copy, never a leak.
- chart series caches (``numCache`` / ``strCache`` — copies of the source
  labels/values) are dropped; Excel rebuilds them from the references on open.
- ``fullCalcOnLoad`` forces recalculation so formulas fed by masked cells
  never show stale derived values.

:func:`verify_redaction_xlsx` is byte-level: it greps every text member of
the output zip (sheets, shared strings, charts, comments, docProps, …) for
the detection values, raw and XML-escaped — the net that reports anything the
scrubbing above missed (e.g. pivot caches, P2).
"""

from __future__ import annotations

import re
import zipfile
from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path

from pysanitize.detector.base import Detection
from pysanitize.detector.specs import MaskSpec
from pysanitize.redact._grams import ValueMatcher
from pysanitize.utils import apply_repls, get_logger

logger = get_logger()

# Zip members scanned by verify (everything textual in an xlsx is xml-ish).
_TEXT_SUFFIXES = frozenset({".xml", ".rels", ".vml"})

# Values made purely of word characters (letters, digits, _, CJK) can only
# occur inside one maximal \w+ run of a blob — the token-level fast path in
# verify is exact for them. Anything else may straddle token boundaries.
_WORDY_RE = re.compile(r"\w+")


@dataclass
class CellEdit:
    """One cell to rewrite: the original rendered text + masked segments.

    ``repls`` are ``(rel_start, rel_end, placeholder, d_start, d_end,
    masked_start, masked_end)`` spans within ``text`` (block-relative to the
    cell, sorted ascending); splicing them right to left yields the masked
    cell value. ``d_start``/``d_end`` keep the detection's global span so the
    pipeline can record cells per span for ``--recover``;
    ``masked_start``/``masked_end`` are where the placeholder lands in the
    *final* masked value — recovery splices by that recorded position, never
    by string matching, so a cell holding several look-alike placeholders
    (two masked names both ``***``) restores exactly.
    """

    sheet: str
    sheet_idx: int  # 0-based position (stable across sheet renames)
    row: int  # 1-based
    col: int  # 1-based
    text: str  # the cell's rendered original (what detectors saw)
    kind: str = "str"
    repls: list[tuple[int, int, str, int, int, int, int]] = field(default_factory=list)
    start: int = -1
    end: int = -1


def resolve_cells(
    doc, detections: list[Detection], mask_map: dict[str, MaskSpec]
) -> list[CellEdit]:
    """Map text detections onto cell edits via the blocks' CellBox provenance.

    Mirrors :func:`pysanitize.redact.pdf.resolve_rects`: for each detection,
    every block it touches contributes the CellBoxes its range intersects;
    the intersected *piece* of the cell text is masked with the field's mask
    (the same per-piece semantics ``_split_straddling`` gives the md path).
    Detections that map onto no cell (sheet furniture — there is none today)
    simply produce no edit.
    """
    edits: dict[tuple[str, int, int], CellEdit] = {}
    cell_bounds: dict[int, tuple[list[int], list[int]]] = {}  # id(block) -> (starts, ends)
    for d in detections:
        for block in doc.span(d.start, d.end):
            if not block.cells:
                continue
            lo = max(d.start, block.char_start) - block.char_start
            hi = min(d.end, block.char_end) - block.char_start
            bounds = cell_bounds.get(id(block))
            if bounds is None:
                cells = block.cells
                bounds = ([cell.start for cell in cells], [cell.end for cell in cells])
                cell_bounds[id(block)] = bounds
            starts, ends = bounds
            # candidate range between "first cell ending past lo" and "first
            # cell starting past hi" — a sheet parses to ONE giant block, so
            # this must stay O(log n + hits): a slice here copied tens of
            # thousands of cells per detection and made large sheets quadratic
            for j in range(bisect_right(ends, lo), bisect_right(starts, hi)):
                cell = block.cells[j]
                clo, chi = max(lo, cell.start), min(hi, cell.end)
                if chi <= clo:
                    continue
                key = (cell.sheet, cell.row, cell.col)
                edit = edits.get(key)
                if edit is None:
                    edit = CellEdit(
                        sheet=cell.sheet,
                        sheet_idx=cell.idx,
                        row=cell.row,
                        col=cell.col,
                        text=cell.text,
                        kind=cell.kind,
                    )
                    edits[key] = edit
                piece = cell.text[clo - cell.start : chi - cell.start]
                edit.repls.append(
                    (clo - cell.start, chi - cell.start, mask_map[d.field_type].mask(piece), d.start, d.end, -1, -1)
                )
    out = list(edits.values())
    for edit in out:
        _fill_masked_offsets(edit.repls)
        edit.start, edit.end = edit.repls[0][3], edit.repls[-1][4]
    return out


def _fill_masked_offsets(repls: list[tuple]) -> None:
    """Sort ``repls`` ascending and fill each one's ``masked_start``/``masked_end``.

    Positions are computed, never searched: applying the replacements left to
    right, a placeholder lands at ``rel_start`` plus the accumulated length
    delta of everything before it. Pure arithmetic on the same data the
    redactor splices, so redact and recover agree by construction.
    """
    repls.sort(key=lambda r: r[0])
    shift = 0
    for i, (rs, re_, masked, ds, de, _, _) in enumerate(repls):
        ms = rs + shift
        repls[i] = (rs, re_, masked, ds, de, ms, ms + len(masked))
        shift += len(masked) - (re_ - rs)


def redact_xlsx(
    doc_path: str | Path,
    edits: list[CellEdit],
    out_path: str | Path,
    *,
    values: list[str] | None = None,
    placeholder: str = "***",
) -> Path:
    """Write a redacted copy of ``doc_path`` to ``out_path``.

    Args:
        doc_path: source workbook. Opened with formulas (``data_only=False``):
            untouched cells keep their formulas; an edited cell's formula is
            replaced by its masked value.
        edits: cells to rewrite (from :func:`resolve_cells`).
        out_path: destination — the pipeline passes ``out_dir/redacted.xlsx``.
        values: detection values, used to scrub cell comments and sheet names
            that contain them (substring → ``placeholder``).
        placeholder: the stand-in used by comment / sheet-name scrubbing.
    """
    from openpyxl import load_workbook

    wb = load_workbook(Path(doc_path), data_only=False)
    for edit in edits:
        ws = wb.worksheets[edit.sheet_idx] if 0 <= edit.sheet_idx < len(wb.worksheets) else wb[edit.sheet]
        cell = ws.cell(row=edit.row, column=edit.col)
        # Splice over the cell's own string when it has one: ``edit.text`` is
        # the detection surface, where newlines were flattened to spaces (1:1
        # in length, so ``repls`` offsets hold for both). Writing the surface
        # back destroyed embedded newlines in multi-line cells. Numbers,
        # formulas and rich text keep the rendered string (wholly masked).
        raw = cell.value
        on_surface = isinstance(raw, str) and raw.replace("\n", " ").replace("\r", " ") == edit.text
        text = apply_repls(raw if on_surface else edit.text, edit.repls)
        cell.value = text

    values = [v for v in (values or []) if v]
    n_comments, n_formulas = _scrub_comments_and_formulas(wb, values, placeholder)
    n_sheets = _scrub_sheet_names(wb, values)
    _drop_chart_caches(wb)
    _scrub_properties(wb)
    wb.calculation.fullCalcOnLoad = True
    wb.save(out_path)
    if n_comments or n_formulas or n_sheets:
        logger.info(
            "scrubbed %d comment(s), %d formula literal(s), renamed %d "
            "sheet(s) containing sensitive values",
            n_comments,
            n_formulas,
            n_sheets,
        )
    logger.info("redacted %d cells -> %s", len(edits), out_path)
    return Path(out_path)


def verify_redaction_xlsx(out_path: str | Path, values: list[str]) -> list[tuple[str, str]]:
    """Which whitespace-free ``values`` still appear in the redacted workbook.

    Byte-level: every text member of the zip (sheets, shared strings, charts,
    comments, docProps, …) is searched — unlike the PDF text layer this
    catches caches and metadata openpyxl does not model. Members are
    ``html.unescape``d first: openpyxl writes non-ASCII strings as numeric
    character references (``&#24352;``), which a raw grep would silently miss.
    Returns ``(value, zip member)`` pairs deduped by value; the caller logs
    them loudly but never fails the run on them.

    Word-only values are matched per ``\\w+`` token with the shared positional
    prefilter (:class:`pysanitize.redact._grams.ValueMatcher`): such a value
    can only ever sit inside one maximal word run, so token-level checks are
    exact — and scanning every value against every member blob is quadratic
    (63k values × megabyte blobs ≈ minutes). Values containing non-word
    characters fall back to the direct blob grep (they could straddle token
    boundaries).
    """
    import html
    import re

    wanted = {v for v in values if v and not any(c.isspace() for c in v)}
    if not wanted:
        return []
    wordy = {v for v in wanted if _WORDY_RE.fullmatch(v)}
    direct = wanted - wordy
    matcher = ValueMatcher(wordy)
    found: dict[str, str] = {}
    with zipfile.ZipFile(out_path) as zf:
        for name in zf.namelist():
            if Path(name).suffix.lower() not in _TEXT_SUFFIXES:
                continue
            blob = html.unescape(zf.read(name).decode("utf-8", errors="ignore"))
            for v in direct:
                if v not in found and v in blob:
                    found[v] = name
            if not wordy:
                continue
            for tok in re.findall(r"\w+", blob):
                matcher.scan(tok, name, found)
            if len(found) == len(wanted):
                return sorted(found.items())
    return sorted(found.items())


def _scrub_comments_and_formulas(wb, values: list[str], placeholder: str) -> tuple[int, int]:
    """Value-echo scrubbing of the surfaces detection cannot reach per span.

    Cell comments and sheet names never enter the detection surface, and a
    formula's *literal* may echo a value whose displayed cache was missed by
    the name heuristics (``=CONCATENATE(B2," 发给张三")`` — 张三 flanked by a
    CJK char is not a rule hit, but 张三 detected elsewhere proves it
    sensitive). Any occurrence of a detected value in these surfaces goes to
    ``placeholder`` — over-redaction of a redacted copy, never a leak. Plain
    string cells are NOT scrubbed this way: they sit on the detection surface,
    so misses there are the detector's (and verify's) to report.

    Candidate values per text come from the shared positional prefilter
    (:class:`pysanitize.redact._grams.ValueMatcher`) — a formula-heavy
    workbook would otherwise multiply every formula by every value.
    Candidates are replaced longest-first: disjoint values make the order
    irrelevant, and nesting (张三 inside 张三丰, both detected) masks the
    longer one — deterministic, unlike iterating detection order.
    """
    comments = formulas = 0
    if not values:
        return 0, 0
    matcher = ValueMatcher(set(values))

    def _clean(text: str) -> str:
        clean = text
        for v in sorted(matcher.matches(text), key=lambda v: (-len(v), v)):
            clean = clean.replace(v, placeholder)
        return clean

    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.comment is not None:
                    text = cell.comment.text
                    clean = _clean(text)
                    if clean != text:
                        cell.comment.text = clean
                        comments += 1
                value = cell.value
                if isinstance(value, str) and value.startswith("="):
                    clean = _clean(value)
                    if clean != value:
                        cell.value = clean
                        formulas += 1
    return comments, formulas


# Excel forbids : \ / ? * [ ] in sheet (tab) names and caps them at 31 chars —
# the generic "***" placeholder is itself illegal there.
_SHEET_PLACEHOLDER = "□□□"


def _scrub_sheet_names(wb, values: list[str]) -> int:
    """Mask detection values inside sheet (tab) names, keeping them legal.

    Uses its own placeholder because Excel rejects ``*`` in titles; results
    are deduplicated (Excel rejects duplicates) and truncated to 31 chars.
    """
    renamed = 0
    taken = {ws.title for ws in wb.worksheets}
    for ws in wb.worksheets:
        if not any(v in ws.title for v in values):
            continue
        new = ws.title
        for v in values:
            new = new.replace(v, _SHEET_PLACEHOLDER)
        new = new[:31] or _SHEET_PLACEHOLDER
        while new in taken:  # keep tabs unique — Excel rejects duplicates
            new = (new[:30] + "~") if len(new) >= 31 else new + "_"
        taken.discard(ws.title)
        taken.add(new)
        ws.title = new
        renamed += 1
    return renamed


def _drop_chart_caches(wb) -> None:
    """Null chart series caches — copies of source labels/values (PII echoes).

    References stay intact; Excel rebuilds caches on open. Best-effort: an
    exotic chart shape openpyxl fails to model is left for verify to report.
    """
    for ws in wb.worksheets:
        for chart in getattr(ws, "_charts", []) or []:
            for ser in getattr(chart, "series", []) or []:
                for ref in (
                    getattr(ser, "val", None),
                    getattr(ser, "cat", None),
                ):
                    for attr in ("numRef", "strRef"):
                        node = getattr(ref, attr, None)
                        if node is not None and getattr(node, "numCache", None) is not None:
                            node.numCache = None
                        if node is not None and getattr(node, "strCache", None) is not None:
                            node.strCache = None


def _scrub_properties(wb) -> None:
    """Blank core document properties (creator, title, …) — classic metadata PII."""
    props = wb.properties
    props.creator = "PySanitize"
    props.lastModifiedBy = "PySanitize"
    for attr in ("title", "subject", "description", "keywords", "category"):
        setattr(props, attr, "")
