"""Image masking: pixelate (mosaic) the detected regions of an image.

The mosaic is a NEAREST-neighbour downscale + upscale over each region —
effectively a low-res block overlay that hides identity while keeping the
surrounding image intact. ``factor`` controls block size (16 = chunky, ~8 =
softer blur).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from pysanitize.detector.image.base import DetectedObject
from pysanitize.utils import get_logger

from .base import Masker

logger = get_logger()


def mosaic(img: Image.Image, boxes: list[DetectedObject], factor: int = 16) -> Image.Image:
    """Return a copy of ``img`` with each box's region pixelated."""
    out = img.copy()
    width, height = out.size
    for box in boxes:
        b = box.clipped(width, height)
        if b.width <= 0 or b.height <= 0:
            continue
        region = out.crop((b.x0, b.y0, b.x1, b.y1))
        sw = max(1, b.width // factor)
        sh = max(1, b.height // factor)
        small = region.resize((sw, sh), Image.NEAREST)
        out.paste(small.resize((b.width, b.height), Image.NEAREST), (b.x0, b.y0))
    return out


class ImageMasker(Masker):
    """Mosaics detected regions onto an image and saves the masked copy."""

    def __init__(self, factor: int = 16):
        self.factor = factor

    def mask_file(
        self,
        src: str | Path,
        dst: str | Path,
        boxes: list[DetectedObject],
    ) -> Path:
        src = Path(src)
        dst = Path(dst)
        with Image.open(src) as im:
            out = mosaic(im.convert("RGB"), boxes, self.factor)
        dst.parent.mkdir(parents=True, exist_ok=True)
        out.save(dst)
        logger.debug(
            "masked %s -> %s (%d regions)", src.name, dst.name, len(boxes)
        )
        return dst
