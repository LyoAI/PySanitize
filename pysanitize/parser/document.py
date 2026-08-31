"""ParsedDocument: MinerU parse output shaped for detection & masking.

The parser layer's public entry point is ``parse_document`` — it runs MinerU
(via ``mineru.py``), pairs extracted images, and assembles the document text
with per-block char offsets that detectors consume.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path

from pysanitize.config import CACHE_DIR, MINERU_BACKEND, MINERU_OUT_DIRNAME

from .blocks import Block, ExtractedImage, META_TYPES
from .mineru import parse_blocks, pair_images


@dataclass
class ParsedDocument:
    """The unified view of a parsed document.

    ``text`` is the full document text: the markdown of every non-meta block
    joined by ``"\n\n"``. Each block's ``char_start/char_end`` slices into it,
    so offsets produced by detectors map straight back to blocks (and, via
    ``page``, to pages for the M2 renderer).
    """

    doc_id: str
    source_path: Path
    source_suffix: str  # ".pdf" / ".docx" / ".xlsx" / ...
    text: str
    blocks: list[Block]
    pages: int
    out_dir: Path  # MinerU output root
    page_dimensions: list[tuple[float, float]] | None = None  # (w, h) per page
    images: list[ExtractedImage] = field(default_factory=list)

    _by_start: list[Block] = field(init=False, repr=False, default_factory=list)

    def __post_init__(self) -> None:
        self._by_start = sorted(
            (b for b in self.blocks if 0 <= b.char_start < b.char_end),
            key=lambda b: b.char_start,
        )

    def block_at(self, offset: int) -> Block | None:
        """The block containing ``offset`` (or None if in a gap / meta block)."""
        starts = [b.char_start for b in self._by_start]
        i = bisect_right(starts, offset) - 1
        if i < 0:
            return None
        block = self._by_start[i]
        return block if block.char_start <= offset < block.char_end else None

    def span(self, start: int, end: int) -> list[Block]:
        """All blocks overlapping ``[start, end)``, in reading order."""
        starts = [b.char_start for b in self._by_start]
        i = bisect_right(starts, start) - 1
        out: list[Block] = []
        for b in self._by_start[max(i, 0):]:
            if b.char_start >= end:
                break
            if b.char_start <= start < b.char_end or start <= b.char_start < end:
                out.append(b)
        return out


def build_document(
    doc_id: str,
    source_path: Path,
    blocks: list[Block],
    out_dir: Path,
    images: list[ExtractedImage] | None = None,
    page_dimensions: list[tuple[float, float]] | None = None,
) -> ParsedDocument:
    """Assemble a ``ParsedDocument`` from projected blocks.

    Non-meta blocks are joined into ``text`` with ``"\n\n"`` separators and
    each block's char range recorded. Page furniture is skipped from the text
    (to keep it out of the desensitized output) but retained in blocks for the
    M2 renderer.
    """
    parts: list[str] = []
    offset = 0
    for block in blocks:
        if block.type in META_TYPES:
            block.char_start = block.char_end = -1
            continue
        block.char_start = offset
        offset += len(block.text)
        block.char_end = offset
        offset += 2  # the "\n\n" separator that follows in the join
        parts.append(block.text)
    text = "\n\n".join(parts)
    pages = max((b.page for b in blocks), default=1)
    return ParsedDocument(
        doc_id=doc_id,
        source_path=Path(source_path),
        source_suffix=Path(source_path).suffix.lower(),
        text=text,
        blocks=blocks,
        pages=pages,
        out_dir=out_dir,
        page_dimensions=page_dimensions,
        images=images or [],
    )


def parse_document(
    doc_path: str | Path,
    out_dir: Path | None = None,
    *,
    backend: str = MINERU_BACKEND,
    lang: str = "ch",
    skip_existing: bool = True,
) -> ParsedDocument:
    """Parse a document with MinerU into a ``ParsedDocument`` (public entry).

    Args:
        doc_path: PDF / image / docx / pptx / xlsx file.
        out_dir: MinerU output root. Defaults to ``.cache/md``.
        backend: MinerU backend ("pipeline" | "vlm-engine" | ...).
        lang: OCR language hint passed to ``mineru -l``.
        skip_existing: reuse on-disk parse output instead of re-running.

    Raises:
        ValueError: unsupported document type.
        RuntimeError: MinerU failed or produced no parse output.
    """
    path = Path(doc_path)
    out_dir = Path(out_dir) if out_dir is not None else (CACHE_DIR / MINERU_OUT_DIRNAME)
    blocks, page_dimensions = parse_blocks(
        path, out_dir, backend=backend, lang=lang, skip_existing=skip_existing
    )
    images = pair_images(path, out_dir, blocks)
    return build_document(
        doc_id=path.stem,
        source_path=path,
        blocks=blocks,
        out_dir=out_dir,
        images=images,
        page_dimensions=page_dimensions,
    )
