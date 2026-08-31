"""Image detection: class-driven sensitive-region detection.

The public entry is :func:`build_detectors`, which turns a list of *classes*
(the targets the user wants masked) into the detectors that find them:

- ``face``   → YuNet / Haar (or ``yolo`` face model when ``backend="yolo"``)
- ``text``   → OCR text-region detection (PaddleOCR, ``[image-ocr]`` extra)
- anything else → YOLO object detection filtered to those class names

An empty class list means *no image masking at all* — the user must explicitly
name their targets (the "no image detection by default" decision), so a bare
``--mask-images`` has no effect on images.
"""

from __future__ import annotations

from pathlib import Path

from pysanitize.config import get_image_config
from pysanitize.utils import get_logger

from .base import DetectedObject, ImageDetector
from .opencv import build_face_detector

logger = get_logger()

# Class keywords that route to the built-in backends; anything else is a YOLO
# class name (or numeric class id) passed through to the model's filter.
_FACE_CLASSES = {"face", "人脸"}
_TEXT_CLASSES = {"text", "text_region", "文字", "文本"}


def build_detectors(
    classes: list[str] | tuple[str, ...] | None,
    *,
    backend: str = "auto",
    model_path: str | Path | None = None,
    score_threshold: float | None = None,
) -> list[ImageDetector]:
    """Build the detectors for the requested classes (empty → []).

    Args:
        classes: what to look for — ``face``, ``text``, and/or YOLO class names
            (``person``, ``car``, …). ``None`` / empty means no image masking.
        backend: face backend when ``face`` is requested (``auto``/``yunet``/
            ``haar``/``yolo``).
        model_path: detection weights — YuNet ONNX for ``face`` (non-yolo), a
            YOLO ``.pt`` for object classes / the yolo face model.
        score_threshold: confidence cutoff passed to each detector (default:
            ``image.score_threshold`` from the pipeline config).

    Returns:
        A list of detectors to run per image. Detectors whose backend is not
        installed are skipped with a warning instead of failing the run.
    """
    if score_threshold is None:
        score_threshold = float(get_image_config().get("score_threshold", 0.5))
    classes = [c.strip() for c in (classes or []) if c and c.strip()]
    if not classes:
        return []
    face = [c for c in classes if c in _FACE_CLASSES]
    text = [c for c in classes if c in _TEXT_CLASSES]
    obj = [c for c in classes if c not in face and c not in text]

    detectors: list[ImageDetector] = []
    try:
        if backend == "yolo" and (face or obj):
            # One YOLO model covers both the face class and object classes.
            detectors.append(_build_yolo(model_path, face + obj, score_threshold))
        else:
            if face:
                detectors.append(build_face_detector(backend, model_path, score_threshold))
            if obj:
                detectors.append(_build_yolo(model_path, obj, score_threshold))
    except Exception as e:
        logger.warning("YOLO detection unavailable (%s), skipping %s targets", e, ",".join(face + obj))
    if text:
        try:
            from .ocr import OCRTextDetector

            detectors.append(OCRTextDetector(confidence=score_threshold))
        except Exception as e:
            logger.warning("OCR text detection unavailable (%s), skipping text target", e)
    return detectors


def _build_yolo(model_path, classes: list[str], confidence: float) -> ImageDetector:
    from .yolo import YOLODetector  # lazy: only if ultralytics is installed

    weights = model_path or (
        "yolo8n-face" if classes == ["face"] else "yolov8n.pt"
    )
    logger.info("object detection backend: YOLO %s (%s)", weights, ",".join(classes))
    return YOLODetector(weights, classes=classes, confidence=confidence)


__all__ = [
    "DetectedObject",
    "ImageDetector",
    "build_detectors",
    "build_face_detector",
]
