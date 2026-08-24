"""Class-driven image detection: routing, YOLO class filtering, OCR parsing."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np

from pysanitize.detector.image import build_detectors
from pysanitize.detector.image.base import DetectedObject
from pysanitize.detector.image.opencv import HaarFaceDetector
from pysanitize.detector.image.yolo import YOLODetector


# ---- routing ----------------------------------------------------------------


def test_build_detectors_empty_classes_is_noop():
    assert build_detectors([]) == []
    assert build_detectors(None) == []
    assert build_detectors(["", "  "]) == []


def test_build_detectors_face_routes_to_haar():
    dets = build_detectors(["face"], backend="haar")
    assert len(dets) == 1 and isinstance(dets[0], HaarFaceDetector)


def test_build_detectors_face_with_missing_yolo_degrades():
    # backend=yolo needs ultralytics → warning, no detectors, no crash
    assert build_detectors(["face"], backend="yolo") == []


def test_build_detectors_text_without_paddleocr_degrades():
    # paddleocr is an optional extra; missing → skipped with a warning
    assert build_detectors(["text"]) == []


def test_build_detectors_face_and_text_keeps_face_when_ocr_missing():
    dets = build_detectors(["face", "text"], backend="haar")
    assert len(dets) == 1 and isinstance(dets[0], HaarFaceDetector)


# ---- YOLO class filter ------------------------------------------------------

class _Row:
    def __init__(self, xyxy, conf, cls):
        self.xyxy = [np.array(xyxy, dtype=float)]
        self.conf = conf
        self.cls = np.array(cls)


class _Result:
    def __init__(self, rows):
        self.boxes = rows


class _FakeModel:
    names = {0: "person", 2: "car"}

    def __init__(self, rows):
        self._rows = rows

    def predict(self, *a, **k):
        return [_Result(self._rows)]


def _install_fake_yolo(monkeypatch, rows):
    mod = types.ModuleType("ultralytics")
    mod.YOLO = lambda *a, **k: _FakeModel(rows)
    monkeypatch.setitem(sys.modules, "ultralytics", mod)


def test_yolo_class_filter_keeps_only_requested_classes(monkeypatch):
    rows = [
        _Row([10, 20, 30, 40], 0.9, 0),   # person
        _Row([50, 60, 70, 80], 0.8, 2),   # car
    ]
    _install_fake_yolo(monkeypatch, rows)
    det = YOLODetector(weights="w.pt", classes=["person"], confidence=0.1)
    boxes = det.detect(Path("x.png"))
    assert len(boxes) == 1
    assert boxes[0].label == "person"
    assert boxes[0].confidence == 0.9
    assert (boxes[0].x0, boxes[0].y0, boxes[0].x1, boxes[0].y1) == (10, 20, 30, 40)


def test_yolo_no_classes_keeps_everything(monkeypatch):
    rows = [
        _Row([10, 20, 30, 40], 0.9, 0),
        _Row([50, 60, 70, 80], 0.8, 2),
    ]
    _install_fake_yolo(monkeypatch, rows)
    det = YOLODetector(weights="w.pt", classes=None, confidence=0.1)
    boxes = det.detect(Path("x.png"))
    assert {b.label for b in boxes} == {"person", "car"}


# ---- OCR parsing ------------------------------------------------------------

def _install_fake_paddleocr(monkeypatch, pages):
    mod = types.ModuleType("paddleocr")

    class _PaddleOCR:
        def __init__(self, *a, **k):
            pass

        def ocr(self, path):
            return pages

    mod.PaddleOCR = _PaddleOCR
    monkeypatch.setitem(sys.modules, "paddleocr", mod)


def test_ocr_text_detector_builds_bounding_boxes(monkeypatch):
    from pysanitize.detector.image.ocr import OCRTextDetector

    pages = [[
        [[[10, 10], [50, 10], [50, 30], [10, 30]], ("北京某某科技有限公司", 0.95)],
        [[[100, 100], [160, 100], [160, 120], [100, 120]], ("机密", 0.88)],
    ]]
    _install_fake_paddleocr(monkeypatch, pages)
    det = OCRTextDetector(confidence=0.5)
    boxes = det.detect(Path("x.png"))
    assert len(boxes) == 2
    first = boxes[0]
    assert (first.x0, first.y0, first.x1, first.y1) == (10, 10, 50, 30)
    assert first.label == "text"
    assert first.confidence == 0.95


def test_ocr_text_detector_filters_low_confidence(monkeypatch):
    from pysanitize.detector.image.ocr import OCRTextDetector

    pages = [[
        [[[0, 0], [10, 0], [10, 10], [0, 10]], ("模糊", 0.2)],
        [[[20, 0], [40, 0], [40, 10], [20, 10]], ("清晰", 0.9)],
    ]]
    _install_fake_paddleocr(monkeypatch, pages)
    det = OCRTextDetector(confidence=0.5)
    boxes = det.detect(Path("x.png"))
    assert len(boxes) == 1
    assert boxes[0].confidence == 0.9


def test_ocr_text_detector_handles_empty_pages(monkeypatch):
    from pysanitize.detector.image.ocr import OCRTextDetector

    _install_fake_paddleocr(monkeypatch, [[None], []])
    assert OCRTextDetector(confidence=0.5).detect(Path("x.png")) == []


def test_ocr_text_detector_missing_dependency_raises_clear_error():
    # paddleocr is not installed in the test env → clear RuntimeError message
    import pytest

    with pytest.raises(RuntimeError, match="image-ocr"):
        from pysanitize.detector.image.ocr import OCRTextDetector

        OCRTextDetector()
