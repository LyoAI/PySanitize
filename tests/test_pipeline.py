"""sanitize_document orchestration (MinerU parse monkeypatched away)."""

from __future__ import annotations

import json

import numpy as np
from PIL import Image

import pysanitize.pipeline as pl
from pysanitize.detector.image.base import DetectedObject


def _patch_parse(monkeypatch, doc):
    monkeypatch.setattr(pl, "parse_document", lambda *a, **k: doc)


def test_text_masking_and_public_audit(make_doc, monkeypatch, tmp_path):
    doc = make_doc([
        ("title", "借款协议", 1),
        ("page_header", "内部资料", 1),
        ("paragraph", "甲方：北京某某科技有限公司 电话 13812345678", 1),
    ])
    _patch_parse(monkeypatch, doc)
    r = pl.sanitize_document("doc.pdf", detector="rules", out_dir=tmp_path / "out")

    md = r.sanitized_md.read_text(encoding="utf-8")
    assert "138****5678" in md and "13812345678" not in md
    assert "北京某某****" in md or "****" in md
    assert "内部资料" not in md  # meta header never leaks

    audit = json.loads(r.audit_path.read_text(encoding="utf-8"))
    raw = json.dumps(audit, ensure_ascii=False)
    assert "13812345678" not in raw  # audit.json carries no raw values
    assert audit["findings"]["by_field"]["phone"] == 1
    assert r.sensitive_report_path is None


def test_audit_flag_writes_raw_report(make_doc, monkeypatch, tmp_path):
    doc = make_doc([("paragraph", "电话 13812345678", 1)])
    _patch_parse(monkeypatch, doc)
    r = pl.sanitize_document("doc.pdf", detector="rules", audit=True, out_dir=tmp_path / "out")
    assert r.sensitive_report_path is not None
    payload = json.loads(r.sensitive_report_path.read_text(encoding="utf-8"))
    assert any(d["value"] == "13812345678" for d in payload["detections"])
    assert all(d["masked_value"] for d in payload["detections"])


def test_fields_subset(make_doc, monkeypatch, tmp_path):
    doc = make_doc([("paragraph", "电话 13812345678 姓名 张三", 1)])
    _patch_parse(monkeypatch, doc)
    r = pl.sanitize_document("doc.pdf", detector="rules", fields=["phone"], out_dir=tmp_path / "out")
    md = r.sanitized_md.read_text(encoding="utf-8")
    assert "138****5678" in md
    assert "张三" in md  # person_name not requested → untouched


def test_invalid_detector_mode(make_doc, monkeypatch, tmp_path):
    doc = make_doc([("paragraph", "x", 1)])
    _patch_parse(monkeypatch, doc)
    import pytest

    with pytest.raises(ValueError, match="detector"):
        pl.sanitize_document("doc.pdf", detector="magic", out_dir=tmp_path / "out")


def test_image_copy_and_link_rewrite(make_doc, monkeypatch, tmp_path):
    img_dir = tmp_path / "md" / "doc" / "images"
    img_dir.mkdir(parents=True)
    img = img_dir / "aabb.jpg"
    Image.fromarray(np.full((80, 60, 3), 200, dtype=np.uint8)).save(img)

    doc = make_doc([("paragraph", "正文", 1), ("image", "", 1)], out_dir=tmp_path / "md")
    doc.blocks[1].image_path = img
    doc.images = [type("EI", (), {"path": img, "page": 1})()]

    _patch_parse(monkeypatch, doc)
    r = pl.sanitize_document("doc.pdf", detector="rules", mask_images=True,
                             image_classes=["face"], image_backend="haar",
                             out_dir=tmp_path / "out")
    md = r.sanitized_md.read_text(encoding="utf-8")
    assert "images_masked/aabb.jpg" in md
    assert (r.out_dir / "images_masked" / "aabb.jpg").exists()
    assert r.images_total == 1 and r.images_masked == 0  # blank → no face


def test_image_masking_skipped_without_classes(make_doc, monkeypatch, tmp_path):
    # "no image detection by default": --mask-images without explicit targets does nothing.
    img_dir = tmp_path / "md" / "doc" / "images"
    img_dir.mkdir(parents=True)
    img = img_dir / "aabb.jpg"
    Image.fromarray(np.full((80, 60, 3), 200, dtype=np.uint8)).save(img)

    doc = make_doc([("paragraph", "正文", 1), ("image", "", 1)], out_dir=tmp_path / "md")
    doc.blocks[1].image_path = img
    doc.images = [type("EI", (), {"path": img, "page": 1})()]

    _patch_parse(monkeypatch, doc)
    r = pl.sanitize_document("doc.pdf", detector="rules", mask_images=True,
                             out_dir=tmp_path / "out")
    # no targets → nothing masked, but originals are still copied under images_masked/
    assert (r.out_dir / "images_masked" / "aabb.jpg").exists()
    assert "images_masked/aabb.jpg" in r.sanitized_md.read_text(encoding="utf-8")
    assert r.images_masked == 0


def test_mask_images_mosaics_detected_regions(monkeypatch, tmp_path):
    # _prepare_images end-to-end with a fake detector returning labeled boxes:
    # real mosaic applied, name_map points into images/.
    img_dir = tmp_path / "md" / "doc" / "images"
    img_dir.mkdir(parents=True)
    img = img_dir / "logo.png"
    # gradient inside the box region → the mosaic has real detail to flatten
    arr = np.zeros((60, 80, 3), dtype=np.uint8)
    arr[:, :] = (10, 60, 120)
    arr[10:50, 10:50, 0] = np.tile(np.arange(40, dtype=np.uint8), (40, 1)) * 4
    Image.fromarray(arr).save(img)
    doc = type("Doc", (), {"images": [type("EI", (), {"path": img, "page": 1})()]})()

    class FakeDetector:
        def detect(self, src):
            return [DetectedObject(10, 10, 50, 50, label="text", confidence=0.9)]

    monkeypatch.setattr(pl, "build_detectors", lambda *a, **k: [FakeDetector()])
    masked, name_map = pl._prepare_images(
        doc, tmp_path / "out", mask=True, classes=["text"], backend="auto",
        model_path=None, score_threshold=0.5, factor=16,
    )
    assert len(masked) == 1
    dst = masked[0]
    assert dst.exists()
    assert name_map["logo.png"] == dst
    assert dst.parent.name == "images_masked"
    # the region changed (mosaicked) while the corner stayed identical
    out = np.asarray(Image.open(dst))
    assert not (out[10:50, 10:50] == arr[10:50, 10:50]).all()
    assert (out[0:4, 0:4] == arr[0:4, 0:4]).all()


def test_llm_provider_and_model_flow_to_detector(make_doc, monkeypatch, tmp_path):
    doc = make_doc([("paragraph", "电话 13812345678", 1)])
    _patch_parse(monkeypatch, doc)
    seen = {}

    class FakeLLMDetector:
        def __init__(
            self, model=None, provider=None, fields=None,
            chunk_size=None, title_level_limit=None,
        ):
            seen["model"] = model
            seen["provider"] = provider
            seen["fields"] = fields
            seen["chunk_size"] = chunk_size
            seen["title_level_limit"] = title_level_limit

        def detect(self, doc):
            return []

    monkeypatch.setattr(pl, "LLMDetector", FakeLLMDetector)
    pl.sanitize_document("doc.pdf", detector="hybrid",
                         llm_model="qwen3.6-27b", llm_provider="pingan",
                         out_dir=tmp_path / "out")
    assert seen["model"] == "qwen3.6-27b"
    assert seen["provider"] == "pingan"
    assert seen["fields"] is None
    # chunking comes from config/pipeline.yaml, not the CLI
    assert seen["chunk_size"] == 6000
    assert seen["title_level_limit"] == "auto"


def test_llm_provider_defaults_from_config(make_doc, monkeypatch, tmp_path):
    doc = make_doc([("paragraph", "电话 13812345678", 1)])
    _patch_parse(monkeypatch, doc)
    seen = {}

    class FakeLLMDetector:
        def __init__(
            self, model=None, provider=None, fields=None,
            chunk_size=None, title_level_limit=None,
        ):
            seen["model"] = model
            seen["provider"] = provider
            seen["chunk_size"] = chunk_size
            seen["title_level_limit"] = title_level_limit

        def detect(self, doc):
            return []

    monkeypatch.setattr(pl, "LLMDetector", FakeLLMDetector)
    pl.sanitize_document("doc.pdf", detector="hybrid", out_dir=tmp_path / "out")
    assert seen["provider"] == "openai"  # config/pipeline.yaml text.provider
    assert seen["model"] == "deepseek-v4-flash"  # config/pipeline.yaml text.model
    assert seen["chunk_size"] == 6000  # config/pipeline.yaml text.chunking.chunk_size
    assert seen["title_level_limit"] == "auto"  # text.chunking.title_level_limit


def test_hybrid_uses_both_detectors(make_doc, monkeypatch, tmp_path):
    doc = make_doc([("paragraph", "电话 13812345678 以及 张三", 1)])
    _patch_parse(monkeypatch, doc)

    class FakeLLM:
        error = None
        content = '{"findings": [{"field_type": "person_name", "value": "张三"}]}'

        def invoke(self, messages, **kwargs):
            return self

    monkeypatch.setattr("pysanitize.detector.llm.get_llm", lambda *a, **k: FakeLLM())
    r = pl.sanitize_document("doc.pdf", detector="hybrid", out_dir=tmp_path / "out")
    md = r.sanitized_md.read_text(encoding="utf-8")
    assert "138****5678" in md and "***" in md
