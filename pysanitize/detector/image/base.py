"""Image-level detection: find sensitive regions inside extracted images.

A region can be a face, a run of printed text (a company name, a seal, a
caption), or any object class a YOLO model knows. Detectors share one box type,
:class:`DetectedObject`, tagged with the ``label`` that produced it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DetectedObject:
    """A detected sensitive region in pixel coordinates (image space).

    ``label`` names what was found: ``"face"`` (YuNet/Haar/YOLO face), ``"text"``
    (OCR text region), or a YOLO class name such as ``"person"`` / ``"car"``.
    """

    x0: int
    y0: int
    x1: int
    y1: int
    label: str = "face"
    confidence: float = 0.5

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0

    def clipped(self, width: int, height: int) -> "DetectedObject":
        x0, y0 = max(0, self.x0), max(0, self.y0)
        x1, y1 = min(width, self.x1), min(height, self.y1)
        if x1 <= x0 or y1 <= y0:
            return DetectedObject(x0, y0, x0, y0, self.label, self.confidence)
        return DetectedObject(x0, y0, x1, y1, self.label, self.confidence)


# Backward-compatible alias: M1 shipped "FaceBox"; keep it importable.
FaceBox = DetectedObject


class ImageDetector(ABC):
    """Detects sensitive regions inside an image file."""

    @abstractmethod
    def detect(self, image_path: Path) -> list[DetectedObject]: ...
