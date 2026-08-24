"""Geometry loader for MinerU's ``_middle.json`` (M2 renderer support).

``content_list_v2.json`` carries no coordinates; MinerU's ``middle.json`` does —
per-page ``pdf_info`` with page size and paragraph boxes in pixel coordinates.
This module reads it defensively (the schema varies across MinerU versions) and
attaches geometry to ``Block`` objects. M1 does not need it; M2 PDF redaction
does, because scanned PDFs have no text layer to ``search_for``.
"""

from __future__ import annotations

import glob
import json
from pathlib import Path

from pysanitize.utils import get_logger

from .blocks import BBox, Block

logger = get_logger()


def locate_middle(doc: Path, out_dir: Path) -> Path | None:
    """Find ``<stem>_middle.json`` for one document under ``out_dir``."""
    base = out_dir / doc.stem
    pattern = glob.escape(f"{doc.stem}_middle.json")
    cands = sorted(base.rglob(pattern)) if base.exists() else []
    if not cands:
        cands = sorted(out_dir.rglob(pattern))
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def load_page_dimensions(middle_path: Path) -> list[tuple[float, float]] | None:
    """Page ``(width, height)`` per page from ``middle.json``, or None."""
    data = _load(middle_path)
    if not data:
        return None
    pages = data.get("pdf_info")
    if not isinstance(pages, list) or not pages:
        return None
    dims: list[tuple[float, float]] = []
    for page in pages:
        dims.append(_page_size(page))
    return dims if any(d for d in dims) else None


def attach_block_boxes(blocks: list[Block], middle_path: Path) -> None:
    """Best-effort attach ``bbox`` to each block from ``middle.json`` in place.

    Tolerates missing/incompatible schema: blocks keep ``bbox=None`` when the
    middle JSON has no usable geometry for them. The ``para_blocks`` list is
    assumed to be in the same reading order as the projected blocks.
    """
    data = _load(middle_path)
    if not data:
        return
    pages = data.get("pdf_info")
    if not isinstance(pages, list):
        return
    for block in blocks:
        if not 1 <= block.page <= len(pages):
            continue
        para_blocks = pages[block.page - 1].get("para_blocks")
        if not isinstance(para_blocks, list) or block.order >= len(para_blocks):
            continue
        box = _extract_box(para_blocks[block.order])
        if box is not None:
            block.bbox = box


def _page_size(page: dict) -> tuple[float, float]:
    size = page.get("page_size")
    if isinstance(size, dict) and size.get("w") and size.get("h"):
        return float(size["w"]), float(size["h"])
    info = page.get("page_info")
    if isinstance(info, dict) and info.get("width") and info.get("height"):
        return float(info["width"]), float(info["height"])
    return 0.0, 0.0


def _extract_box(item) -> BBox | None:
    box = item.get("bbox")
    if isinstance(box, dict):
        if all(k in box for k in ("x0", "y0", "x1", "y1")):
            return BBox(box["x0"], box["y0"], box["x1"], box["y1"])
        if all(k in box for k in ("x", "y", "w", "h")):
            return BBox(box["x"], box["y"], box["x"] + box["w"], box["y"] + box["h"])
    if isinstance(box, list) and len(box) == 4:
        return BBox(*box)
    return None


def _load(middle_path: Path) -> dict | None:
    try:
        with middle_path.open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("cannot read middle.json %s: %s", middle_path, e)
        return None
