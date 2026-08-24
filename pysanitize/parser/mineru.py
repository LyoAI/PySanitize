"""MinerU CLI wrapper: documents → reading-ordered ``Block`` lists (+ extracted images).

Ported from ``finsearch/tools/parser/mineru_parse.py`` (FinSearch-Bench). The
CLI is invoked via ``subprocess`` — the CLI starts a throwaway local server, so
parsing is fully local, no external API. Only the files MinerU leaves on disk
are consumed (``<stem>_content_list_v2.json``), so the same output is reusable
across machines regardless of how MinerU itself was run.

The projection here is pure text; geometry (bbox) lives in ``middle.json`` and
is loaded separately by ``middle.py`` for the M2 renderer.
"""

from __future__ import annotations

import glob
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

from lxml import html as lxml_html

from pysanitize.config import MINERU_BACKEND
from pysanitize.utils import get_logger

from .blocks import Block, ExtractedImage

logger = get_logger()

# content_list_v2 record types whose ``content`` holds text spans. Anything not
# in this table (``page_header``, ``page_footer``, ...) falls back to
# ``content["<kind>_content"]``.
_TEXT_FIELD = {
    "title": "title_content",
    "paragraph": "paragraph_content",
}

# Suffixes the MinerU CLI parses directly (mirrors cli/common.py). Legacy
# Office formats (.doc / .xls / .ppt) are deliberately absent: they fail inside
# MinerU, so we reject them up front with a conversion hint.
SUPPORTED_SUFFIXES = frozenset(
    {
        ".pdf",
        ".png", ".jpeg", ".jp2", ".webp", ".gif", ".bmp", ".jpg", ".tiff",
        ".docx", ".pptx", ".xlsx",
    }
)

# Extracted-image extensions under MinerU's ``images/`` directory.
_IMAGE_SUFFIXES = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff"}
)


def parse_blocks(
    doc: Path,
    out_dir: Path,
    *,
    backend: str = MINERU_BACKEND,
    lang: str = "ch",
    skip_existing: bool = True,
) -> list[Block]:
    """Parse one document into a reading-ordered ``Block`` list.

    Already-parsed files are reused (``skip_existing``), so re-runs are cheap.

    Raises:
        ValueError: unsupported document type (e.g. legacy ``.doc``).
        RuntimeError: MinerU failed or produced no content_list JSON.
    """
    if not doc.is_file():
        raise FileNotFoundError(f"no such document: {doc}")
    if doc.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(
            f"unsupported document type: {doc.suffix}; mineru parses "
            "pdf / images / docx / pptx / xlsx "
            "(convert legacy .doc/.xls/.ppt to .docx/.xlsx/.pptx first)"
        )
    out_dir.mkdir(parents=True, exist_ok=True)

    v2 = _locate_content_list(doc, out_dir)
    if v2 is None or not skip_existing:
        _run_mineru(doc, out_dir, backend=backend, lang=lang)
        v2 = _locate_content_list(doc, out_dir)
        if v2 is None:
            raise RuntimeError(
                f"mineru produced no content_list_v2 for {doc.name} under {out_dir}"
            )
    records = json.loads(v2.read_text(encoding="utf-8"))
    blocks = _project(records)
    logger.info("%s: %d blocks", doc.name, len(blocks))
    return blocks


def pair_images(doc: Path, out_dir: Path, blocks: list[Block]) -> list[ExtractedImage]:
    """Pair hash-named files under ``images/`` with image-type blocks.

    MinerU names extracted images by content hash (``sha256.jpg``), so the only
    stable link is reading order: the ``i``-th image file pairs with the
    ``i``-th ``type=image`` block. Extra files beyond the block count are still
    surfaced so the face detector scans them.
    """
    images_dir = _locate_images_dir(doc, out_dir)
    if images_dir is None:
        return []
    files = sorted(
        (p for p in images_dir.iterdir() if p.suffix.lower() in _IMAGE_SUFFIXES),
        key=lambda p: p.name,
    )
    image_blocks = [b for b in blocks if b.type == "image"]
    out: list[ExtractedImage] = []
    for block, path in zip(image_blocks, files):
        block.image_path = path
        out.append(ExtractedImage(path=path, page=block.page, caption=block.text))
    for path in files[len(image_blocks):]:
        out.append(ExtractedImage(path=path, page=1))
    return out


def _locate_images_dir(doc: Path, out_dir: Path) -> Path | None:
    """Find the ``images/`` dir MinerU wrote for one document."""
    base = out_dir / doc.stem
    cands = sorted(base.rglob("images")) if base.exists() else []
    if not cands:
        pattern = f"{glob.escape(doc.stem)}/images"
        cands = sorted(out_dir.rglob(pattern))
    return cands[0] if cands else None


def _locate_content_list(doc: Path, out_dir: Path) -> Path | None:
    """Find ``<stem>_content_list_v2.json`` for one document under ``out_dir``.

    rglob so it works across the subdirectory MinerU picks per type
    (``<method>/`` for pdf/images, ``office/`` for docx/pptx/xlsx). When the
    same stem exists under several methods (a re-run with a different ``-m``),
    the newest is returned.
    """
    base = out_dir / doc.stem
    # The stem is a literal filename, but real documents contain glob
    # metacharacters (e.g. "[1578.HK]"); escape so the pattern matches literally.
    pattern = glob.escape(f"{doc.stem}_content_list_v2.json")
    cands = sorted(base.rglob(pattern)) if base.exists() else []
    if not cands:
        cands = sorted(out_dir.rglob(pattern))
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def _run_mineru(doc: Path, out_dir: Path, *, backend: str, lang: str) -> None:
    """Invoke the local MinerU CLI (thin wrapper; no cloud calls)."""
    cmd = [
        _mineru_executable(),
        "-p",
        str(doc),
        "-o",
        str(out_dir),
        "-b",
        backend,
        "-l",
        lang,
    ]
    logger.info("mineru: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"mineru failed for {doc.name} (rc={proc.returncode}); stderr tail:\n"
            f"{(proc.stderr or '')[-3000:]}"
        )


def _mineru_executable() -> str:
    """Resolve the ``mineru`` console script even when the venv isn't activated.

    Prefers ``sys.prefix`` (the active venv); falls back to walking up from the
    ``mineru`` package location for other layouts.
    """
    exe = shutil.which("mineru")
    if exe:
        return exe
    candidates = [
        Path(sys.prefix) / "bin" / "mineru",
        Path(sys.prefix) / "Scripts" / "mineru.exe",
    ]
    spec = importlib.util.find_spec("mineru")
    if spec and spec.origin:
        site_packages = Path(spec.origin).resolve().parent.parent
        candidates += [
            site_packages.parent / "bin" / "mineru",
            site_packages.parent / "Scripts" / "mineru.exe",
        ]
    for cand in candidates:
        if cand.exists():
            return str(cand)
    raise RuntimeError(
        "mineru CLI not found on PATH (activate the venv or install mineru)"
    )


def _project(records: list) -> list[Block]:
    """Pure projection of a content_list JSON onto ``Block`` lists.

    Accepts both shapes MinerU emits: the v2 paginated list
    (``[[record, ...], ...]``, page = outer index) and the v1 flat list
    (``[record, ...]`` with a per-record ``page_idx``).
    """
    if not records:
        return []
    blocks: list[Block] = []
    order = 0
    if isinstance(records[0], list):
        for page_idx, page_records in enumerate(records):
            for rec in page_records:
                block = _project_record(rec, page_idx + 1, order)
                if block is not None:
                    blocks.append(block)
                    order += 1
    else:
        for rec in records:
            page = int(rec.get("page_idx", 0)) + 1
            block = _project_record(rec, page, order)
            if block is not None:
                blocks.append(block)
                order += 1
    return blocks


def _project_record(rec: dict, page: int, order: int) -> Block | None:
    """One content_list record → ``Block`` (v1 flat or v2 nested shapes)."""
    if not isinstance(rec, dict):
        return None
    kind = rec.get("type")
    if not isinstance(kind, str):
        return None
    content = rec.get("content")
    if isinstance(content, dict):
        # v2: text nested under content.<field>, heading level carried there.
        text = _v2_text(kind, content)
        level = content.get("level") if kind == "title" else None
    else:
        # v1: text/table_body/text_level at record top level.
        text = _v1_text(rec)
        level = rec.get("text_level")
    return Block(
        block_id=f"b{order}",
        type=kind,
        text=text,
        page=page,
        order=order,
        level=level,
    )


def _v2_text(kind: str, content: dict) -> str:
    if kind == "table":
        return _html_table_to_markdown(content.get("html") or "")
    if kind == "list" or kind == "index":
        return _join_list_items(content.get("list_items"))
    if kind == "image":
        return _join_lines(content.get("image_caption"))
    if kind == "chart":
        return _join_lines(content.get("chart_caption"))
    if kind == "code":
        return _normalize_text(content.get("code_content"))
    if kind == "algorithm":
        return _normalize_text(content.get("algorithm_content"))
    if kind == "equation_interline":
        return _normalize_text(content.get("math_content"))
    field = _TEXT_FIELD.get(kind, f"{kind}_content")
    return _normalize_text(content.get(field))


def _v1_text(rec: dict) -> str:
    kind = rec.get("type")
    if kind == "table":
        caps = rec.get("table_caption") or []
        body = _html_table_to_markdown(rec.get("table_body") or "")
        return ("\n".join(caps) + "\n\n" + body) if caps else body
    if kind == "list":
        return _join_list_items(rec.get("list_items"))
    if kind in ("image", "chart"):
        return _join_lines(rec.get("image_caption") or rec.get("chart_caption"))
    return _normalize_text(rec.get("text"))


def _normalize_text(value) -> str:
    """Coerce MinerU text payloads to a plain str.

    Accepts ``str``, a list of span dicts (``[{"content": ...}, ...]``), or a
    single span dict — MinerU emits both shapes across block kinds/versions.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                content = item.get("content")
                # Inline equations read better with surrounding spaces.
                if item.get("type") == "span_equation_inline":
                    parts.append(f" {content} " if content else "")
                else:
                    parts.append(str(content or ""))
            else:
                parts.append(str(item))
        return "".join(parts).strip()
    if isinstance(value, dict):
        return _normalize_text(value.get("content"))
    return str(value)


def _join_list_items(items) -> str:
    """list/index items → bulleted markdown lines."""
    out: list[str] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        text = _normalize_text(item.get("item_content"))
        if text.strip():
            out.append(f"- {text}")
    return "\n".join(out)


def _join_lines(values) -> str:
    """image/chart captions (a list of span-dict lists) → joined text."""
    parts: list[str] = []
    for v in values or []:
        text = _normalize_text(v)
        if text.strip():
            parts.append(text)
    return "\n".join(parts)


def _html_table_to_markdown(html: str) -> str:
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
