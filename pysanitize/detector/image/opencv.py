"""OpenCV face detection: YuNet (accurate, needs a small ONNX model) with a
Haar-cascade fallback (ships with opencv, zero download, no confidence).

YuNet model: ``face_detection_yunet_2023mar.onnx`` from the OpenCV model zoo
(~340 KB). It is downloaded on first use into ``MODELS_DIR`` when online;
offline installs fall back to Haar automatically.
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2

from pysanitize.config import MODELS_DIR
from pysanitize.utils import get_logger

from .base import DetectedObject, ImageDetector

logger = get_logger()

YUET_MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
    "face_detection_yunet_2023mar.onnx"
)


def _download(url: str, dest: Path, timeout: int = 30) -> bool:
    """Best-effort download; returns False when offline or the request fails."""
    import urllib.request

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=timeout) as resp, open(dest, "wb") as f:
            f.write(resp.read())
        return dest.stat().st_size > 0
    except Exception as e:
        logger.warning("model download failed (%s); will fall back", e)
        return False


class YuNetFaceDetector(ImageDetector):
    """YuNet via ``cv2.FaceDetectorYN`` (ONNX, CPU, accurate)."""

    def __init__(
        self,
        model_path: str | Path | None = None,
        score_threshold: float = 0.5,
    ) -> None:
        self.model_path = Path(model_path) if model_path else (MODELS_DIR / "face_detection_yunet_2023mar.onnx")
        self.score_threshold = score_threshold
        if not self.model_path.exists() and not _download(YUET_MODEL_URL, self.model_path):
            raise FileNotFoundError(f"YuNet model unavailable: {self.model_path}")
        self._detector = cv2.FaceDetectorYN_create(
            str(self.model_path), "", (0, 0), score_threshold=score_threshold
        )

    def detect(self, image_path: Path) -> list[DetectedObject]:
        img = cv2.imread(str(image_path))
        if img is None:
            logger.warning("cannot read image %s", image_path)
            return []
        h, w = img.shape[:2]
        self._detector.setInputSize((w, h))
        ok, faces = self._detector.detect(img)
        if not ok or faces is None:
            return []
        boxes: list[DetectedObject] = []
        for row in faces:
            x, y, fw, fh, *_, score = row
            boxes.append(
                DetectedObject(
                    int(x), int(y), int(x + fw), int(y + fh),
                    label="face", confidence=float(score),
                )
            )
        return boxes


class HaarFaceDetector(ImageDetector):
    """OpenCV's bundled Haar cascade (offline-safe, no confidence)."""

    def __init__(self, scale_factor: float = 1.1, min_neighbors: int = 5) -> None:
        cascade = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        self._cascade = cv2.CascadeClassifier(cascade)
        self.scale_factor = scale_factor
        self.min_neighbors = min_neighbors

    def detect(self, image_path: Path) -> list[DetectedObject]:
        img = cv2.imread(str(image_path))
        if img is None:
            logger.warning("cannot read image %s", image_path)
            return []
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        rects = self._cascade.detectMultiScale(
            gray, scaleFactor=self.scale_factor, minNeighbors=self.min_neighbors
        )
        return [
            DetectedObject(int(x), int(y), int(x + w), int(y + h), label="face", confidence=0.5)
            for x, y, w, h in rects
        ]


def build_face_detector(
    backend: str = "auto", model_path: str | Path | None = None, score_threshold: float = 0.5
) -> ImageDetector:
    """Pick a face detector: ``auto`` tries YuNet then Haar; ``yunet`` / ``haar``
    force one. YOLO (``backend="yolo"``) lives in ``yolo.py``."""
    if backend in ("auto", "yunet"):
        try:
            det = YuNetFaceDetector(model_path, score_threshold)
            if backend == "yunet":
                return det
            logger.info("face detection backend: YuNet")
            return det
        except Exception as e:
            if backend == "yunet":
                raise RuntimeError(f"YuNet unavailable: {e}") from e
            logger.warning("YuNet unavailable (%s); falling back to Haar", e)
    if backend == "haar":
        return HaarFaceDetector()
    if backend == "auto":
        logger.info("face detection backend: Haar")
        return HaarFaceDetector()
    if backend == "yolo":
        from .yolo import YOLODetector  # lazy: only if ultralytics is installed

        return YOLODetector(
            model_path or "yolo8n-face",
            classes=["face"],
            confidence=score_threshold,
        )
    raise ValueError(f"unknown face-detection backend: {backend!r}")
