"""Centralized config: defaults exist, YAML deep-merge works, getters resolve."""

from __future__ import annotations

from pathlib import Path

import yaml

from pysanitize import config as cfg


def test_defaults_cover_all_sections():
    for section in ("text", "image", "output"):
        assert section in cfg._DEFAULTS
        assert isinstance(cfg._DEFAULTS[section], dict)


def test_get_text_config_has_all_keys():
    text = cfg.get_text_config()
    for key in (
        "detector", "model", "provider", "verify_checksums",
        "chunking", "min_value_len", "max_value_len",
        "max_completion_tokens", "min_title_sections",
    ):
        assert key in text, f"missing text config key: {key}"
    assert text["detector"] in ("rules", "llm", "hybrid")
    assert int(text["chunking"]["chunk_size"]) > 0


def test_get_image_config_has_all_keys():
    image = cfg.get_image_config()
    for key in (
        "enabled", "classes", "detector", "score_threshold",
        "mosaic_factor", "model_path", "haar", "ocr", "yolo",
    ):
        assert key in image, f"missing image config key: {key}"
    assert image["detector"] in ("auto", "yunet", "haar", "yolo")
    assert set(image["haar"]) == {"scale_factor", "min_neighbors"}
    assert set(image["ocr"]) == {"lang", "confidence"}
    assert "confidence" in image["yolo"]


def test_yaml_overrides_defaults(tmp_path: Path, monkeypatch):
    """User YAML values win; untouched keys keep their defaults."""
    yaml_file = tmp_path / "pipeline.yaml"
    yaml_file.write_text(
        yaml.safe_dump({
            "text": {"detector": "hybrid"},
            "image": {"mosaic_factor": 8},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "PIPELINE_CONFIG", yaml_file)
    monkeypatch.setattr(cfg, "load_pipeline_config", lambda path=None: cfg.load_yaml(yaml_file))

    text = cfg.get_text_config()
    image = cfg.get_image_config()
    assert text["detector"] == "hybrid"                    # overridden
    assert text["model"] == cfg._DEFAULTS["text"]["model"]  # default kept
    assert text["chunking"]["chunk_size"] == 6000           # nested default kept
    assert image["mosaic_factor"] == 8                      # overridden
    assert image["score_threshold"] == 0.5                  # default kept


def test_missing_yaml_file_falls_back_to_defaults(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        cfg, "load_pipeline_config", lambda path=None: cfg.load_yaml(tmp_path / "nope.yaml")
    )
    text = cfg.get_text_config()
    image = cfg.get_image_config()
    assert text == cfg._DEFAULTS["text"]
    assert image == cfg._DEFAULTS["image"]


def test_llm_config_dir_and_paths_exist():
    assert cfg.CONFIG_DIR.name == "config"
    assert cfg.LLM_CONFIG_DIR == cfg.CONFIG_DIR / "llm"
    assert cfg.FIELDS_CONFIG == cfg.CONFIG_DIR / "fields.yaml"
