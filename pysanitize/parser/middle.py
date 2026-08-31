"""middle.json projection: MinerU's structured intermediate parse → ``Block``s.

``content_list_v2`` is a lossy projection of ``<stem>_middle.json`` — MinerU's
structured intermediate result: it flattens paragraphs, drops the TOC, and
carries block bboxes in a different coordinate space. ``middle.json`` keeps
every span with page coordinates, which is exactly what the M2 redactor needs
for precise masking.

Primary source: ``pdf_info[].para_blocks``. Per-line geometry is projected into
``Block.line_boxes`` so both the PDF redactor and the image OCR field detector
can map a char range onto page/image coordinates. Tables carry no cell text in
middle (3.x: a placeholder span only), so their markdown is recovered by
aligning back onto the v2 ``html`` (same per-page reading order).
"""

from __future__ import annotations

import glob
import json
from pathlib import Path
from typing import Any

from pysanitize.utils import get_logger

from .blocks import BBox, Block, LineBox, html_table_to_markdown

logger = get_logger()

# middle uses ``text`` where content_list_v2 uses ``paragraph``; keep the v2
# name so downstream logic (META_TYPES / IMAGE_TYPES / md rendering) is
# unchanged.
_TYPE_MAP = {"text": "paragraph"}

# Sub-block kinds inside a middle ``table`` block that carry real text spans
# (the cell text itself is not in middle 3.x).
_TABLE_CAPTION_TYPES = ("table_caption", "table_footnote")


def locate_middle_json(doc: Path, out_dir: Path) -> Path | None:
    """Find ``<stem>_middle.json`` for one document under ``out_dir``.

    Same layout rules as ``mineru._locate_content_list`` (rglob across the
    per-method subdirectory; newest wins when re-runs picked a different
    backend).
    """
    base = out_dir / doc.stem
    pattern = glob.escape(f"{doc.stem}_middle.json")
    cands = sorted(base.rglob(pattern)) if base.exists() else []
    if not cands:
        cands = sorted(out_dir.rglob(pattern))
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def load_middle(doc: Path, out_dir: Path) -> dict | None:
    """Load ``<stem>_middle.json`` (``None`` when absent)."""
    path = locate_middle_json(doc, out_dir)
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def project_middle(
    middle: dict, v2_pages: list | None = None
) -> tuple[list[Block], list[tuple[float, float]] | None]:
    """Project a parsed ``middle.json`` onto reading-ordered ``Block``s.

    Args:
        middle: the loaded ``<stem>_middle.json`` dict.
        v2_pages: paginated content_list_v2 records, used only to recover table
            markdown (middle 3.x tables carry no cell text).

    Returns:
        ``(blocks, page_dimensions)`` — page_dimensions is ``[(w, h), ...]``
        per page (or ``None`` when the file carries no geometry, e.g. the
        office backend with ``include_bbox=False``).
    """
    pages = middle.get("pdf_info")
    if not isinstance(pages, list):
        return [], None
    blocks: list[Block] = []
    page_dims: list[tuple[float, float]] = []
    order = 0
    for page_info in pages:
        if not isinstance(page_info, dict):
            continue
        page = int(page_info.get("page_idx", 0)) + 1
        size = page_info.get("page_size") or []
        page_dims.append((float(size[0]), float(size[1])) if len(size) >= 2 else (0.0, 0.0))
        v2_page = v2_pages[page - 1] if v2_pages and page - 1 < len(v2_pages) else None
        raw = [
            _project_para(pb, page)
            for pb in page_info.get("para_blocks", [])
            if isinstance(pb, dict)
        ]
        if v2_page:
            _align_tables(raw, v2_page)
        for b in raw:
            if b is None:
                continue
            b.order = order
            b.block_id = f"b{order}"
            blocks.append(b)
            order += 1
    return blocks, page_dims


def _project_para(pb: dict, page: int) -> Block | None:
    """One ``para_blocks`` entry → ``Block`` (``None`` when unprojectable)."""
    btype = pb.get("type")
    if btype == "index":
        # TOC: the v2 era contributed no TOC text to the markdown. Keep an
        # empty placeholder so per-page order still aligns with the v2 records
        # used for table recovery.
        return Block(block_id="", type="index", text="", page=page, order=0)
    bbox = _bbox(pb.get("bbox"))
    if btype == "table":
        return Block(
            block_id="",
            type="table",
            text=_table_caption(pb),
            page=page,
            order=0,
            bbox=bbox,
        )
    if btype in ("image", "chart"):
        return _project_media(pb, btype, page)
    lines = pb.get("lines")
    if isinstance(lines, list):
        return _project_text(pb, btype, lines, page, bbox)
    return None


def _project_text(
    pb: dict, btype: str, lines: list, page: int, bbox: BBox | None
) -> Block:
    """Text/title/list/... block → Block with per-line ``LineBox`` geometry.

    ``block.text`` is the physical lines joined with ``"\n"`` (matching the v2
    era); each ``LineBox`` records its flat char range so a detection offset can
    be mapped onto the line's box.
    """
    line_texts = [_line_text(line.get("spans")) for line in lines if isinstance(line, dict)]
    text = "\n".join(line_texts)
    line_boxes: list[LineBox] = []
    offset = 0
    for line, lt in zip(lines, line_texts):
        if lt:
            lbx = _bbox(line.get("bbox")) if isinstance(line, dict) else None
            if lbx is None:
                lbx = bbox
            if lbx is not None:
                line_boxes.append(LineBox(lt, lbx, offset, offset + len(lt)))
        offset += len(lt) + 1  # account for the "\n" separator in ``text``
    level = None
    if btype == "title" and pb.get("level") is not None:
        level = int(pb["level"])
    return Block(
        block_id="",
        type=_TYPE_MAP.get(btype, btype),
        text=text,
        page=page,
        order=0,
        level=level,
        bbox=bbox,
        line_boxes=line_boxes or None,
    )


def _project_media(pb: dict, btype: str, page: int) -> Block:
    """image/chart block → Block carrying the extracted image path + page box."""
    image_path: Path | None = None
    image_bbox: BBox | None = None
    parts: list[str] = []

    def walk(node: Any) -> None:
        nonlocal image_path, image_bbox
        if isinstance(node, dict):
            if node.get("image_path"):
                image_path = Path(node["image_path"])
                image_bbox = _bbox(node.get("bbox")) or image_bbox
            if (
                isinstance(node.get("type"), str)
                and node["type"].startswith("span_")
                and node.get("content")
            ):
                parts.append(str(node["content"]))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(pb)
    return Block(
        block_id="",
        type=btype,
        text="\n".join(p for p in parts if p),
        page=page,
        order=0,
        bbox=image_bbox or _bbox(pb.get("bbox")),
        image_path=image_path,
        image_bbox=image_bbox,
    )


def _line_text(spans: list | None) -> str:
    """Concatenate a line's span texts (inline equations get breathing space)."""
    parts: list[str] = []
    for span in spans or []:
        if not isinstance(span, dict):
            continue
        content = span.get("content") or ""
        if span.get("type") == "span_equation_inline":
            parts.append(f" {content} ")
        else:
            parts.append(str(content))
    return "".join(parts)


def _table_caption(pb: dict) -> str:
    """Real text spans under a middle table block (caption/footnote)."""
    parts: list[str] = []
    for sub in pb.get("blocks") or []:
        if not isinstance(sub, dict) or sub.get("type") not in _TABLE_CAPTION_TYPES:
            continue
        for line in sub.get("lines") or []:
            if isinstance(line, dict):
                parts.append(_line_text(line.get("spans")))
    return "\n".join(parts)


def _align_tables(raw_blocks: list[Block | None], v2_page: list) -> None:
    """Recover table markdown from v2 records, aligned by per-page order.

    middle 3.x tables carry only a placeholder span (no cell text); the v2
    ``html`` holds the cells. v2 and middle list blocks in the same page order,
    so table #k lines up with v2 record #k — with a ±1 retry for the occasional
    discarded/missing block. No match → caption-only fallback.
    """
    records = [r for r in v2_page if isinstance(r, dict)]
    for i, block in enumerate(raw_blocks):
        if block is None or block.type != "table":
            continue
        html = _v2_table_html_at(records, i)
        if html:
            block.text = html_table_to_markdown(html)
        else:
            logger.warning(
                "table on page %d not aligned with content_list_v2; "
                "keeping caption-only markdown",
                block.page,
            )


def _v2_table_html_at(records: list, i: int) -> str:
    """The ``html`` of a v2 ``table`` record near position ``i`` (±1 retry)."""
    for j in (i, i - 1, i + 1):
        if not 0 <= j < len(records):
            continue
        rec = records[j]
        if not isinstance(rec, dict) or rec.get("type") != "table":
            continue
        content = rec.get("content")
        html = content.get("html") if isinstance(content, dict) else None
        if isinstance(html, str) and html:
            return html
    return ""


def _bbox(value) -> BBox | None:
    """MinerU bbox ``[x0, y0, x1, y1]`` → ``BBox`` (None when absent/bad)."""
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return None
    try:
        return BBox(float(value[0]), float(value[1]), float(value[2]), float(value[3]))
    except (TypeError, ValueError):
        return None
