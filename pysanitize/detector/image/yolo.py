"""Optional YOLO detection via ``ultralytics`` (the ``[image-yolo]`` extra).

Kept behind a lazy import so the default install (no ultralytics) still works;
the routing in ``build_detectors`` only instantiates this when the user asked
for ``face`` (with ``--image-backend yolo``) or for arbitrary object classes.

``classes`` filters detections to the requested class *names*; a face-only model
like ``yolo8n-face`` reports its single class as ``"face"``, while a general
model such as ``yolov8n.pt`` reports COCO names (``person``, ``car``, …).
"""

from __future__ import annotations

from pathlib import Path

from pysanitize.utils import get_logger

from .base import DetectedObject, ImageDetector

logger = get_logger()


class YOLODetector(ImageDetector):
    """YOLO object detection with an ultralytics model and class filtering.

    Args:
        weights: model name or path (``yolo8n-face``, ``yolov8n.pt``, …).
        classes: class *names* to keep (e.g. ``["person", "car"]``). ``None``
            keeps everything the model detects.
        confidence: per-box confidence threshold passed to ``model.predict``.
    """

    def __init__(
        self,
        weights: str | Path = "yolo8n-face",
        classes: list[str] | None = None,
        confidence: float = 0.25,
    ):
        try:
            from ultralytics import YOLO
        except ImportError:
            raise RuntimeError(
                "ultralytics not installed; run `uv sync --extra image-yolo`"
            ) from None
        self.model = YOLO(str(weights))
        self.names = self.model.names  # {cls_idx: name} (dict or list)
        self.classes = set(classes) if classes else None
        self.confidence = confidence

    def _label_for(self, cls_idx: int) -> str:
        try:
            name = self.names[cls_idx]
            # face models report their only class as "face"; map it explicitly
            return "face" if name == "face" else str(name)
        except (KeyError, IndexError, TypeError):
            return "object"

    def detect(self, image_path: Path) -> list[DetectedObject]:
        results = self.model.predict(
            str(image_path), conf=self.confidence, verbose=False
        )
        boxes: list[DetectedObject] = []
        for r in results:
            for row in r.boxes:
                x0, y0, x1, y1 = (int(v) for v in row.xyxy[0].tolist())
                cls_idx = int(row.cls)
                label = self._label_for(cls_idx)
                if self.classes is not None and label not in self.classes:
                    continue
                boxes.append(
                    DetectedObject(x0, y0, x1, y1, label=label, confidence=float(row.conf))
                )
        return boxes
