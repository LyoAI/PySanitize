"""Image masking: NEAREST mosaic blocks, clipping, file round-trip."""

from __future__ import annotations

import numpy as np
from PIL import Image

from pysanitize.detector.image.base import DetectedObject
from pysanitize.masker.image import ImageMasker, mosaic


def _halves():
    img = Image.new("RGB", (200, 150))
    px = img.load()
    for y in range(150):
        for x in range(200):
            px[x, y] = (255, 0, 0) if x < 100 else (0, 0, 255)
    return img


def test_mosaic_is_solid_nearest_blocks():
    img = _halves()
    box = DetectedObject(40, 30, 90, 80)  # 50x50 → 3x3 blocks at factor 16
    out = mosaic(img, [box], factor=16)
    arr = np.asarray(out)
    # every block of the downscaled-up image is uniform: round-trip reproduces it
    w, h = 50, 50
    rt = Image.fromarray(arr[box.y0:box.y1, box.x0:box.x1]).resize(
        (w // 16, h // 16), Image.NEAREST
    ).resize((w, h), Image.NEAREST)
    assert (arr[box.y0:box.y1, box.x0:box.x1] == np.asarray(rt)).all()


def test_mosaic_region_changed_and_surround_untouched():
    img = _halves()
    # the box spans the red/blue boundary (x=100), so the mosaic has detail to flatten
    box = DetectedObject(40, 30, 140, 80)
    out = mosaic(img, [box], factor=16)
    arr = np.asarray(out)
    before = np.asarray(img)
    # outside the box is byte-identical
    mask = np.ones_like(before, dtype=bool)
    mask[box.y0:box.y1, box.x0:box.x1] = False
    assert (arr[mask] == before[mask]).all()
    # inside the box at least something changed
    assert (arr[box.y0:box.y1, box.x0:box.x1] != before[box.y0:box.y1, box.x0:box.x1]).any()


def test_mosaic_clips_out_of_bounds():
    img = _halves()
    box = DetectedObject(180, 130, 250, 200)
    out = mosaic(img, [box], factor=16)
    assert out.size == img.size


def test_facebox_clipped():
    fb = DetectedObject(-10, -10, 20, 20)
    c = fb.clipped(100, 100)
    assert (c.x0, c.y0, c.x1, c.y1) == (0, 0, 20, 20)


def test_mask_file_round_trip(tmp_path):
    img = _halves()
    src = tmp_path / "src.png"
    dst = tmp_path / "dst.png"
    img.save(src)
    ImageMasker(factor=16).mask_file(src, dst, [DetectedObject(40, 30, 90, 80)])
    assert dst.exists()
    assert Image.open(dst).size == img.size
