"""Field-driven image detection: OCR text → text field rules → mosaicing boxes."""

from __future__ import annotations

import sys
import types
from pathlib import Path

from pysanitize.detector.image import build_ocr_field_detector
from pysanitize.detector.image.ocr import OCRFieldDetector
from pysanitize.detector.specs import load_field_specs, select_specs


def _install_fake_paddleocr(monkeypatch, pages):
    mod = types.ModuleType("paddleocr")

    class _PaddleOCR:
        def __init__(self, *a, **k):
            pass

        def ocr(self, path):
            return pages

    mod.PaddleOCR = _PaddleOCR
    monkeypatch.setitem(sys.modules, "paddleocr", mod)


def _specs(fields):
    return select_specs(load_field_specs(), fields)


def test_ocr_field_detector_masks_only_requested_field(monkeypatch, tmp_path):
    _install_fake_paddleocr(
        monkeypatch,
        [[
            [[[10, 10], [200, 10], [200, 30], [10, 30]], ("联系电话 13812345678", 0.99)],
            [[[10, 40], [180, 40], [180, 60], [10, 60]], ("公司名称：某有限公司", 0.95)],
        ]],
    )
    img = tmp_path / "scan.png"
    img.write_bytes(b"fake")
    det = OCRFieldDetector(specs=_specs(["phone"]))
    hits = det.detect(img)
    assert hits and all(h.label == "phone" for h in hits)
    assert hits[0].confidence == 1.0  # the rule spec's confidence, not the OCR score
    # every phone hit stays inside the phone line's y-band (line box ±1 pad)
    assert all(8 <= h.y0 and h.y1 <= 32 for h in hits)
    assert hits[0].x0 > 10  # sub-rect starts mid-line, not at the line origin


def test_ocr_field_detector_boxes_align_on_later_lines(monkeypatch, tmp_path):
    # A hit on the 3rd OCR line must map onto THAT line's box: the line texts
    # are joined with "\n" and the line-box offsets account for it, so the
    # mosaic box covers the digits end-to-end (a separator-less join drifted
    # every line after the first and left the value's tail uncovered).
    _install_fake_paddleocr(
        monkeypatch,
        [[
            [[[10, 10], [200, 10], [200, 30], [10, 30]], ("公司名称：北京某某科技有限公司", 0.99)],
            [[[10, 40], [200, 40], [200, 60], [10, 60]], ("注册地址：北京市海淀区某路某号", 0.99)],
            [[[10, 70], [200, 70], [200, 90], [10, 90]], ("联系电话 13812345678", 0.99)],
        ]],
    )
    img = tmp_path / "scan.png"
    img.write_bytes(b"fake")
    hits = OCRFieldDetector(specs=_specs(["phone"])).detect(img)
    assert len(hits) == 1
    h = hits[0]
    assert 69 <= h.y0 and h.y1 <= 91  # inside line 3's y-band
    assert h.x0 >= 60  # starts at the digits, not the line origin
    assert h.x1 >= 195  # the trailing digits are covered too


def test_ocr_field_detector_absent_field_no_hits(monkeypatch, tmp_path):
    _install_fake_paddleocr(
        monkeypatch,
        [[[[[10, 10], [200, 10], [200, 30], [10, 30]], ("联系电话 13812345678", 0.99)]]],
    )
    img = tmp_path / "scan.png"
    img.write_bytes(b"fake")
    assert OCRFieldDetector(specs=_specs(["email"])).detect(img) == []


def test_ocr_field_detector_empty_image_no_hits(monkeypatch, tmp_path):
    _install_fake_paddleocr(monkeypatch, [[None]])
    img = tmp_path / "blank.png"
    img.write_bytes(b"fake")
    assert OCRFieldDetector(specs=_specs(["phone"])).detect(img) == []


def test_build_ocr_field_detector_degrades_without_paddleocr():
    # paddleocr is not installed in the test env → warning + None, no crash
    assert build_ocr_field_detector(_specs(["phone"])) is None
