"""PDF redaction: detections → page rectangles → true glyph removal + mosaic.

The only place that touches pymupdf (AGPL, kept a runtime dependency). For each
sensitive rect we render the region, pixelate it (NEAREST, the same look as
image mosaicing), then ``add_redact_annot`` + ``apply_redactions`` to *delete*
the underlying glyphs (``text=0``) and blank overlapping image pixels
(``images=2``, so scanned backgrounds are cleared too) while preserving vector
graphics / table borders (``graphics=0``). Finally the mosaic image is stamped
back over the rect. ``style="block"`` skips the mosaic and paints a solid box
instead.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from pysanitize.parser.blocks import BBox, line_sub_boxes
from pysanitize.utils import get_logger

logger = get_logger()

# Render resolution for the mosaic preview (points → pixels via dpi/72).
_RENDER_DPI = 150
# A hit may sit a hair outside page_size (MinerU bbox slop); grow the sub-box
# by this before clamping so glyphs are fully covered.
_PAD = 1.0


@dataclass
class Redaction:
    """One sensitive region to remove on one PDF page (0-based ``page``)."""

    page: int
    rect: BBox
    image: Path | None = None  # pre-mosaiced image to stamp (None = render+mosaic)
    start: int = -1  # char offsets of the detection (audit bookkeeping)
    end: int = -1


def resolve_rects(doc, detections, page_dims=None) -> list[Redaction]:
    """Map text detections onto page rectangles via the block geometry.

    Within a line the hit is placed by proportional char width (CJK is roughly
    monospaced); a whole block is used when no per-line boxes exist — tables in
    middle 3.x have no cell coordinates, so the whole table bbox is redacted (a
    conservative over-redaction, documented in the README). Rectangles are
    clamped to the page size. Each Redaction keeps its detection's ``start``/
    ``end`` so the pipeline can record the rects per span for ``--recover``.
    """
    redactions: list[Redaction] = []
    for d in detections:
        for block in doc.span(d.start, d.end):
            lo = max(d.start, block.char_start)
            hi = min(d.end, block.char_end)
            if hi <= lo:
                continue
            rel_lo = lo - block.char_start
            rel_hi = hi - block.char_start
            if block.line_boxes:
                boxes = line_sub_boxes(block.line_boxes, rel_lo, rel_hi, pad=_PAD)
            elif block.bbox is not None:
                boxes = [block.bbox]
            else:
                continue
            pw = ph = None
            if page_dims and 0 < block.page <= len(page_dims):
                pw, ph = page_dims[block.page - 1]
            for b in boxes:
                if pw and ph:
                    b = _clamp(b, pw, ph)
                redactions.append(
                    Redaction(page=block.page - 1, rect=b, start=d.start, end=d.end)
                )
    return redactions


def redact_pdf(
    doc_path,
    redactions,
    out_path,
    *,
    style: str = "mosaic",
    factor: int = 16,
) -> Path:
    """Write a redacted copy of ``doc_path`` to ``out_path``.

    Args:
        doc_path: source PDF.
        redactions: sensitive regions to remove (from :func:`resolve_rects`).
        out_path: destination — the pipeline passes ``out_dir/redacted.pdf``.
        style: ``mosaic`` (delete glyphs + stamp a pixelated region) or
            ``block`` (delete glyphs + solid black box).
        factor: mosaic block size in points (``image.mosaic_factor``).
    """
    import pymupdf  # the one place this AGPL library is used

    src = pymupdf.open(doc_path)
    by_page: dict[int, list[Redaction]] = {}
    for r in redactions:
        by_page.setdefault(r.page, []).append(r)
    for page_idx, page in enumerate(src):
        rects = by_page.get(page_idx)
        if not rects:
            continue
        if style == "block":
            for r in rects:
                page.add_redact_annot(_rect(r.rect), fill=(0, 0, 0))
            page.apply_redactions(images=2, graphics=0, text=0)
            continue
        # Render every region *before* apply_redactions wipes the content.
        fills = [
            (r, _mosaic_region(page, r.rect, factor) if r.image is None else r.image)
            for r in rects
        ]
        for r in rects:
            page.add_redact_annot(_rect(r.rect))
        page.apply_redactions(images=2, graphics=0, text=0)
        for r, src_img in fills:
            _stamp(page, r.rect, src_img)
    src.save(out_path)
    logger.info("redacted %d pages -> %s", len(by_page), out_path)
    return Path(out_path)


def verify_redaction(out_path, values: list[str]) -> list[tuple[str, int]]:
    """Which whitespace-free ``values`` still appear in the redacted PDF, as
    ``(value, first page it appears on)`` deduped by value.

    Scanned documents have no text layer, so this is naturally a no-op there;
    the caller logs leftovers loudly but never fails the run on them.
    """
    import pymupdf

    doc = pymupdf.open(out_path)
    wanted = {v for v in values if v and not any(c.isspace() for c in v)}
    found: dict[str, int] = {}
    for page in doc:
        page_text = page.get_text()
        if not page_text:
            continue
        for v in wanted:
            if v not in found and v in page_text:
                found[v] = page.number + 1
    return sorted(found.items())


def _rect(b: BBox):
    import pymupdf

    return pymupdf.Rect(b.x0, b.y0, b.x1, b.y1)


def _mosaic_region(page, rect: BBox, factor: int) -> bytes:
    """Render ``rect`` and pixelate it into PNG bytes (for ``insert_image``)."""
    from PIL import Image

    pix = page.get_pixmap(clip=_rect(rect), dpi=_RENDER_DPI)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    block = max(2, int(factor * _RENDER_DPI / 72))  # keep blocks ~factor pt
    sw = max(1, img.width // block)
    sh = max(1, img.height // block)
    small = img.resize((sw, sh), Image.NEAREST)
    buf = io.BytesIO()
    small.resize(img.size, Image.NEAREST).save(buf, format="PNG")
    return buf.getvalue()


def _stamp(page, rect: BBox, src) -> None:
    if isinstance(src, Path):
        page.insert_image(_rect(rect), filename=str(src))
    else:
        page.insert_image(_rect(rect), stream=src)


def _clamp(b: BBox, pw: float, ph: float) -> BBox:
    return BBox(
        min(max(b.x0, 0.0), pw),
        min(max(b.y0, 0.0), ph),
        min(max(b.x1, 0.0), pw),
        min(max(b.y1, 0.0), ph),
    )
