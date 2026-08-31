"""Core parser data structures shared by M1 (markdown output) and M2 (layout redaction)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from lxml import html as lxml_html


class LineBox(NamedTuple):
    """One physical line inside a block.

    ``start``/``end`` are char offsets of ``text`` within the block's flattened
    text, so a detection range can be mapped onto the line's geometry.
    """

    text: str
    bbox: "BBox"
    start: int  # char offset of ``text`` in the flattened block text
    end: int  # exclusive


def line_sub_boxes(
    line_boxes: list[LineBox],
    rel_start: int,
    rel_end: int,
    pad: float = 1.0,
) -> list[BBox]:
    """Estimate the sub-rect of ``[rel_start, rel_end)`` across line boxes.

    CJK text is effectively monospaced, so within each overlapped line the hit
    is placed by proportional character width. Returns one box per line, in the
    same coordinate space as the line boxes (PDF points or image pixels). Used
    by both the PDF redactor and the OCR field detector.
    """
    out: list[BBox] = []
    for line in line_boxes:
        lo = max(rel_start, line.start)
        hi = min(rel_end, line.end)
        if hi <= lo or not line.text:
            continue
        n = len(line.text)
        x0 = line.bbox.x0 + (lo - line.start) / n * line.bbox.width
        x1 = line.bbox.x0 + (hi - line.start) / n * line.bbox.width
        out.append(BBox(x0 - pad, line.bbox.y0 - pad, x1 + pad, line.bbox.y1 + pad))
    return out


@dataclass
class BBox:
    """Axis-aligned bounding box in page-pixel coordinates.

    Office (docx/xlsx/pptx) parses carry no geometry (``None``). Coordinates
    are normalized on construction (x0<=x1, y0<=y1).
    """

    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        self.x0, self.x1 = sorted((self.x0, self.x1))
        self.y0, self.y1 = sorted((self.y0, self.y1))

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def normalized(self, pw: float, ph: float) -> "BBox":
        """Scale to 0..1 relative to a page of size ``(pw, ph)``."""
        return BBox(self.x0 / pw, self.y0 / ph, self.x1 / pw, self.y1 / ph)

    def pad(self, factor: float = 1.2) -> "BBox":
        """Grow around the center by ``factor`` (caller clamps to the page)."""
        cx, cy = (self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2
        hw, hh = self.width * factor / 2, self.height * factor / 2
        return BBox(cx - hw, cy - hh, cx + hw, cy + hh)


@dataclass
class Block:
    """One MinerU content block in reading order.

    ``char_start/char_end`` slice into ``ParsedDocument.text`` and are the
    currency of M1 masking. Geometry (``bbox`` / ``line_boxes`` / ``image_bbox``)
    is carried for the M2 renderer.
    """

    block_id: str
    type: str  # MinerU native: title / paragraph / table / image / ...
    text: str  # markdown content of this block (table → markdown table)
    page: int  # 1-based
    order: int  # 0-based reading order
    level: int | None = None  # heading level for title blocks
    char_start: int = 0
    char_end: int = 0
    bbox: BBox | None = None  # whole-block geometry (PDF/images only)
    line_boxes: list[LineBox] | None = None  # per-line text + box + char range (M2)
    image_path: Path | None = None  # extracted image file for image blocks
    image_bbox: BBox | None = None  # where the image sits on the page


@dataclass
class ExtractedImage:
    """An image MinerU extracted into ``images/`` (hash-named files)."""

    path: Path
    page: int  # page it belongs to (PDF) or 1 for a bare image
    bbox: BBox | None = None  # position on the page, when known
    caption: str = ""


def html_table_to_markdown(html: str) -> str:
    """Minimal HTML ``<table>`` → GitHub-flavored markdown, via lxml.

    Cell text is flattened (no colspan/rowspan merging), pipes escaped, and
    line breaks collapsed so each cell stays one markdown line. A wrong or
    non-table input is passed through unchanged.
    """
    if not html or "<table" not in html.lower():
        return html
    try:
        root = lxml_html.fromstring(html)
    except Exception:
        return html
    rows: list[list[str]] = []
    for tr in root.xpath(".//tr"):
        cells = []
        for cell in tr:
            if cell.tag not in ("td", "th"):
                continue
            text = " ".join(cell.itertext()).strip().replace("\n", " ").replace("|", "\\|")
            cells.append(text)
        if cells:
            rows.append(cells)
    if not rows:
        return html
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = [
        "| " + " | ".join(rows[0]) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
    ]
    for r in rows[1:]:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


# Page furniture (headers/footers/page numbers) excluded from the desensitized
# text — kept in blocks only so M2 can still redact them if configured.
META_TYPES = frozenset(
    {
        "header",
        "footer",
        "page_number",
        "page_header",
        "page_footer",
        "page_aside_text",
        "page_footnote",
    }
)
