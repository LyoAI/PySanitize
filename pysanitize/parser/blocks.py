"""Core parser data structures shared by M1 (markdown output) and M2 (layout redaction)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class BBox:
    """Axis-aligned bounding box in page-pixel coordinates.

    For PDF/image documents MinerU records pixel geometry in ``middle.json``;
    office (docx/xlsx/pptx) parses carry no geometry (``None``). Coordinates
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
    comes lazily from ``middle.json`` and is consumed by the M2 renderer.
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
    line_boxes: list[tuple[str, BBox]] | None = None  # per-line text + box (M2)
    image_path: Path | None = None  # extracted image file for image blocks
    image_bbox: BBox | None = None  # where the image sits on the page


@dataclass
class ExtractedImage:
    """An image MinerU extracted into ``images/`` (hash-named files)."""

    path: Path
    page: int  # page it belongs to (PDF) or 1 for a bare image
    bbox: BBox | None = None  # position on the page, when known
    caption: str = ""


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
