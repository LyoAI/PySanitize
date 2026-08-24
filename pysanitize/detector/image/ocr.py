"""Optional OCR text-region detection via PaddleOCR (the ``[image-ocr]`` extra).

Targets the ``text`` class: mask every printed text region in an image (a
company name on a logo, a seal, a caption, a screenshot of a table). For a
document sanitizer this is the safe semantics — any printed text in an image
could be sensitive — so every OCR line becomes a ``label="text"`` box.

PaddleOCR is heavy (ships ``paddlepaddle``) and is *not* a dependency of MinerU
3.x, so it stays behind a lazy import. ``build_detectors`` skips ``text`` with a
warning when the extra is missing, rather than failing the run.
"""

from __future__ import annotations

from pathlib import Path

from pysanitize.utils import get_logger

from .base import DetectedObject, ImageDetector

logger = get_logger()


class OCRTextDetector(ImageDetector):
    """Detects text regions in an image with PaddleOCR.

    Args:
        lang: OCR language (``"ch"`` handles Chinese + Latin).
        confidence: drop lines whose per-line score is below this.
    """

    def __init__(self, lang: str = "ch", confidence: float = 0.5):
        try:
            from paddleocr import PaddleOCR
        except ImportError:
            raise RuntimeError(
                "paddleocr not installed; run `uv sync --extra image-ocr`"
            ) from None
        # 3.x constructor args; older versions ignore unknown ones only if not
        # passed — keep the common subset.
        self._ocr = PaddleOCR(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            lang=lang,
        )
        self.confidence = confidence

    def detect(self, image_path: Path) -> list[DetectedObject]:
        result = self._ocr.ocr(str(image_path))
        boxes: list[DetectedObject] = []
        # 2.x: [page_result] with page_result = [[box, (text, score)], ...]
        # 3.x: same shape per page; a page with no text may be [] or [None].
        for page in result or []:
            if not page:
                continue
            for item in page:
                if not item or len(item) < 2:
                    continue
                poly, text_info = item[0], item[1]
                if isinstance(text_info, (tuple, list)) and len(text_info) >= 2:
                    score = float(text_info[1])
                else:
                    score = 1.0
                if score < self.confidence:
                    continue
                xs = [p[0] for p in poly]
                ys = [p[1] for p in poly]
                boxes.append(
                    DetectedObject(
                        int(min(xs)),
                        int(min(ys)),
                        int(max(xs)),
                        int(max(ys)),
                        label="text",
                        confidence=score,
                    )
                )
        return boxes
