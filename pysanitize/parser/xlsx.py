"""Native xlsx producer: openpyxl worksheets → table ``Block``s with cell provenance.

MinerU's office path adds nothing for spreadsheets — no geometry, tables
flattened anyway — so ``.xlsx`` bypasses it entirely. Each sheet becomes one
``table`` block whose text is a GitHub-flavored markdown table; every
non-empty cell records a :class:`~pysanitize.parser.blocks.CellBox`
(``sheet``/``row``/``col`` + the exact char range its text occupies in the
block), the spreadsheet analog of ``LineBox`` geometry. Detection therefore
runs on the *same rendered strings* the xlsx redactor writes back — the
offset→cell mapping is exact by construction, never matched after the fact.

Rendered surface (what detectors see, and what a masked cell becomes):

- str: cell text on one line; newlines flatten to spaces for detection (1:1
  in length — the redactor splices the same offsets into the raw cell, so a
  rewritten cell keeps its newlines)
- int / integral float: ``13800138000`` (never ``13800138000.0``)
- other float: shortest python repr
- datetime/date/time: ISO-ish (``2024-03-01``, ``2024-03-01 09:30:00``);
  a datetime in a time-less number format renders date-only — no phantom
  midnight — and recovers as a date
- bool: Excel-style ``TRUE`` / ``FALSE``
- formula without a cached value: the formula string itself, so literals
  embedded in formulas (``="13800138000"``) stay detectable

Known limits (P2): embedded images and cell comments are not part of the
detection surface — the redactor's byte-level verify still reports any
detection value surviving in them. Pipes (``|``) inside cell text are not
escaped; such rows render imperfectly in sanitized.md, but offsets, masking
and redaction are unaffected.
"""

from __future__ import annotations

import datetime as _dt
import re as _re
from pathlib import Path

from pysanitize.utils import get_logger

from .blocks import Block, CellBox

logger = get_logger()

# Stop scanning a sheet after this many consecutive empty rows — styled-but-
# blank tails can inflate max_row enormously.
_MAX_EMPTY_RUN = 200

# A number format carrying any of these markers renders a time part, so the
# cell renders (and recovers) as a datetime; without them a date-formatted
# cell drops openpyxl's phantom midnight.
_HAS_TIME = _re.compile(r"[hHsS]")


def parse_xlsx_blocks(path: Path) -> list[Block]:
    """Every worksheet (hidden ones included) → one markdown-table ``Block``.

    All sheets participate: hidden / veryHidden sheets are a classic place to
    park data, and a redaction tool that skips them would silently leak. Each
    block's ``page`` is the sheet's 1-based position, so audit "pages" map to
    sheets one-to-one.
    """
    from openpyxl import load_workbook

    wb_values = load_workbook(path, data_only=True, read_only=False)
    wb_formulas = load_workbook(path, data_only=False, read_only=False)
    blocks: list[Block] = []
    for sheet_idx, ws in enumerate(wb_values.worksheets):
        rows: list[list[tuple[str, CellBox | None]]] = []
        fws = wb_formulas[ws.title]
        empty_run = 0
        for r, row in enumerate(ws.iter_rows(values_only=True), start=1):
            rendered = [
                _render(v, fws.cell(row=r, column=c).value, ws.cell(row=r, column=c).number_format)
                for c, v in enumerate(row, start=1)
            ]
            if not any(text for text, _ in rendered):
                empty_run += 1
                if empty_run >= _MAX_EMPTY_RUN and r >= _MAX_EMPTY_RUN:
                    break
                continue
            empty_run = 0
            row_cells: list[tuple[str, CellBox | None]] = []
            for c, (text, kind) in enumerate(rendered, start=1):
                if text is None:
                    row_cells.append(("", None))
                    continue
                row_cells.append(
                    (text, CellBox(text, ws.title, r, c, 0, 0, kind, sheet_idx))
                )
            rows.append(row_cells)
        if ws._images:
            logger.warning(
                "%s: sheet %r embeds %d image(s); image masking inside xlsx "
                "is not implemented yet — they pass through unmasked",
                path.name,
                ws.title,
                len(ws._images),
            )
        if not rows:
            continue
        blocks.append(_sheet_block(sheet_idx + 1, rows))
    return blocks


def _row_line(row: list[tuple[str, CellBox | None]]) -> tuple[str, list[tuple[CellBox, int]]]:
    """One markdown row ``| a | b |``, plus each cell's char position in it.

    Positions are captured *while building* — never searched afterwards — so
    duplicate cell texts can never be mistaken for one another.
    """
    parts: list[str] = []
    placed: list[tuple[CellBox, int]] = []
    at = 0
    for i, (text, cell) in enumerate(row):
        prefix = "| " if i == 0 else " | "
        parts.append(prefix)
        at += len(prefix)
        if cell is not None:
            placed.append((cell, at))
        parts.append(text)
        at += len(text)
    parts.append(" |")
    return "".join(parts), placed


def _sheet_block(page: int, rows: list[list[tuple[str, CellBox | None]]]) -> Block:
    """Assemble one sheet's markdown table, recording each CellBox's char range.

    ``block.text[cell.start:cell.end] == cell.text`` is the invariant the
    redactor's offset math rests on. Empty cells render as ``""`` between
    pipes (no CellBox); the markdown separator line sits between the first row
    (header) and the rest.
    """
    width = max(len(r) for r in rows)
    padded = [r + [("", None)] * (width - len(r)) for r in rows]
    header, header_placed = _row_line(padded[0])
    sep = "| " + " | ".join("---" for _ in range(width)) + " |"
    data = [_row_line(r) for r in padded[1:]]
    lines = [header, sep] + [line for line, _ in data]
    cells: list[CellBox] = []
    offset = 0  # where the current line starts in "\n".join(lines)
    for line, placed in [(header, header_placed), (sep, [])] + data:
        for cell, at in placed:
            cells.append(cell._replace(start=offset + at, end=offset + at + len(cell.text)))
        offset += len(line) + 1
    return Block(
        block_id="",
        type="table",
        text="\n".join(lines),
        page=page,
        order=0,
        cells=cells,
    )


def _render(value, formula, fmt: str = "") -> tuple[str | None, str]:
    """One cell → ``(rendered_text_or_None, kind)`` for the detection surface.

    ``fmt`` (the cell's number format) decides date vs datetime rendering:
    openpyxl hands back ``datetime`` for date-formatted cells either way, and
    the detection surface should match what the user sees — a ``yyyy-mm-dd``
    cell renders (and recovers) as a date, not with a phantom midnight.
    """
    if isinstance(value, str):
        return value.replace("\n", " ").replace("\r", " "), "str"
    if value is None:
        # No cached value: a formula string may still carry literals worth
        # detecting; a truly empty cell renders nothing.
        if isinstance(formula, str) and formula.startswith("="):
            return formula.replace("\n", " "), "formula"
        return None, "str"
    if isinstance(value, bool):
        return ("TRUE" if value else "FALSE"), "bool"
    if isinstance(value, int):
        return str(value), "int"
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value)), "int"
        return str(value), "float"
    if isinstance(value, _dt.datetime):
        if not _HAS_TIME.search(fmt or ""):
            return value.strftime("%Y-%m-%d"), "date"
        return value.strftime("%Y-%m-%d %H:%M:%S"), "datetime"
    if isinstance(value, _dt.date):
        return value.strftime("%Y-%m-%d"), "date"
    if isinstance(value, _dt.time):
        return value.strftime("%H:%M:%S"), "time"
    return str(value).replace("\n", " "), "str"
