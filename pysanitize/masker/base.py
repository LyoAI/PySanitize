"""Masking interfaces: replace located sensitive spans (text, and later images)."""

from __future__ import annotations

from abc import ABC
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pysanitize.detector.base import Detection


class Masker(ABC):
    """Turns located sensitive content into masked output.

    Text maskers implement ``mask(text, detections)``; image maskers expose
    ``mask_file(src, dst, boxes)`` instead. Neither is required of every
    subclass, so ``mask`` defaults to raising.
    """

    def mask(self, source, detections) -> object:
        raise NotImplementedError(
            f"{type(self).__name__} does not implement mask()"
        )
