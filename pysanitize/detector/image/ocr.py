"""Optional OCR detection via PaddleOCR (the ``[image-ocr]`` extra).

Two complementary detectors share the OCR pipeline:

- :class:`OCRTextDetector` — the ``text`` class: mask **every** printed-text
  region (a company name on a logo, a seal, a caption, a screenshot of a
  table). Safe semantics for a sanitizer.
- :class:`OCRFieldDetector` — the ``image.fields`` path: OCR the image, run the
  same *text* rule detectors over the recognized text, and mosaic only the
  spans matching the configured fields (company names, registered addresses,
  …). Complement to the class-driven detectors; the LLM is not used per image
  (too expensive), the rules engine is shared instead.

PaddleOCR is heavy (ships ``paddlepaddle``) and is *not* a dependency of MinerU
3.x, so it stays behind a lazy import. Missing it degrades to a warning, never
a hard failure.
"""

from __future__ import annotations

from pathlib import Path

from pysanitize.config import get_image_config
from pysanitize.parser.blocks import BBox, Block, LineBox, line_sub_boxes
from pysanitize.parser.document import build_document
from pysanitize.detector.rules import RuleDetector
from pysanitize.utils import get_logger

from .base import DetectedObject, ImageDetector

logger = get_logger()


def _build_paddle(lang: str | None = None):
    """Lazy PaddleOCR init; raises RuntimeError with an install hint when absent."""
    try:
        from paddleocr import PaddleOCR
    except ImportError:
        raise RuntimeError(
            "paddleocr not installed; run `uv sync --extra image-ocr`"
        ) from None
    ocr_cfg = get_image_config().get("ocr", {})
    lang = lang or str(ocr_cfg.get("lang", "ch"))
    # 3.x constructor args; older versions ignore unknown ones only if not
    # passed — keep the common subset.
    return PaddleOCR(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        lang=lang,
    )


def _ocr_lines(ocr, image_path: Path, confidence: float) -> list[tuple[str, BBox, float]]:
    """OCR an image into ``(text, box, score)`` per line, filtered by score.

    Handles the 2.x/3.x result shapes: ``[page_result]`` with
    ``page_result = [[box, (text, score)], ...]``; an empty page may be ``[]``
    or ``[None]``.
    """
    result = ocr.ocr(str(image_path))
    out: list[tuple[str, BBox, float]] = []
    for page in result or []:
        if not page:
            continue
        for item in page:
            if not item or len(item) < 2:
                continue
            poly, text_info = item[0], item[1]
            if isinstance(text_info, (tuple, list)) and len(text_info) >= 2:
                text, score = str(text_info[0]), float(text_info[1])
            elif isinstance(text_info, dict):
                text, score = str(text_info.get("content", "")), float(text_info.get("confidence", 1.0))
            else:
                text, score = str(text_info), 1.0
            if not text or score < confidence:
                continue
            xs = [p[0] for p in poly]
            ys = [p[1] for p in poly]
            out.append((text, BBox(min(xs), min(ys), max(xs), max(ys)), score))
    return out


class OCRTextDetector(ImageDetector):
    """Detects text regions in an image with PaddleOCR.

    Args:
        lang: OCR language (``"ch"`` handles Chinese + Latin; default:
            ``image.ocr.lang`` from the pipeline config).
        confidence: drop lines whose per-line score is below this (default:
            ``image.ocr.confidence``).
    """

    def __init__(self, lang: str | None = None, confidence: float | None = None):
        ocr_cfg = get_image_config().get("ocr", {})
        self.confidence = (
            float(ocr_cfg.get("confidence", 0.5))
            if confidence is None
            else float(confidence)
        )
        self._ocr = _build_paddle(lang)

    def detect(self, image_path: Path) -> list[DetectedObject]:
        return [
            DetectedObject(int(b.x0), int(b.y0), int(b.x1), int(b.y1), label="text", confidence=s)
            for _, b, s in _ocr_lines(self._ocr, image_path, self.confidence)
        ]


class OCRFieldDetector(ImageDetector):
    """Field-driven image detection: OCR → text detectors → matching regions.

    For every extracted image the OCR text is reassembled into a throwaway
    :class:`ParsedDocument` (with per-line boxes) and the configured *text*
    field detectors run over it; each hit is mapped back onto its OCR line
    boxes and returned as a mosaicing region. Only spans matching the selected
    fields are masked — unlike ``text``, which masks every printed region.
    """

    def __init__(
        self,
        specs=None,
        fields: list[str] | None = None,
        *,
        verify_checksums: bool = True,
        lang: str | None = None,
        confidence: float | None = None,
    ):
        ocr_cfg = get_image_config().get("ocr", {})
        self.confidence = (
            float(ocr_cfg.get("confidence", 0.5))
            if confidence is None
            else float(confidence)
        )
        self._rule = RuleDetector(specs=specs, fields=fields, verify_checksums=verify_checksums)
        self._ocr = _build_paddle(lang)

    def detect(self, image_path: Path) -> list[DetectedObject]:
        lines = _ocr_lines(self._ocr, image_path, self.confidence)
        if not lines:
            return []
        # Joined WITH the "\n" separator the offsets below account for — a
        # separator-less join would drift every line after the first by one
        # char per preceding line, shifting (or dropping) the mosaic boxes.
        text = "\n".join(t for t, _, _ in lines)
        line_boxes: list[LineBox] = []
        offset = 0
        for t, box, _ in lines:
            if t:
                line_boxes.append(LineBox(t, box, offset, offset + len(t)))
            offset += len(t) + 1  # the "\n" separator included above
        block = Block(
            block_id="img",
            type="paragraph",
            text=text,
            page=1,
            order=0,
            line_boxes=line_boxes,
        )
        # A single block starting at char 0, so detection offsets are already
        # block-relative — the shared sub-box mapper does the rest.
        doc = build_document("img", image_path, [block], image_path.parent)
        out: list[DetectedObject] = []
        for d in self._rule.detect(doc):
            for b in line_sub_boxes(line_boxes, d.start, d.end):
                out.append(
                    DetectedObject(
                        int(b.x0), int(b.y0), int(b.x1), int(b.y1),
                        label=d.field_type, confidence=d.confidence,
                    )
                )
        return out
