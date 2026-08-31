"""Configuration center: loads ``.env``, resolves ``config/`` YAML files.

The ``config/`` directory holds all user-tunable settings:

- ``pipeline.yaml``  per-stage switches and parameters for the sanitize run
- ``fields.yaml``    sensitive-field specs (see ``detector/specs.py``)
- ``llm/<model>.yaml`` model/provider endpoints (``${VAR}`` key placeholders)

Everything is optional: a fresh clone or CI has no ``config/`` dir, and the
built-in :data:`_DEFAULTS` below mirror ``pipeline.yaml`` so the tool works
out of the box. :func:`get_text_config` / :func:`get_image_config` merge the
YAML over the defaults (deep, per-section) and are the single read path for
pipeline parameters.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(REPO_ROOT / ".env")

# Local ``config/`` (untracked, may hold real keys). A fresh clone or CI has no
# config dir; loaders fall back to built-in defaults (``_DEFAULTS`` and
# ``specs.DEFAULT_FIELD_SPECS``) so the tool works out of the box.
CONFIG_DIR = REPO_ROOT / "config"

# LLM model configs: ``config/llm/<model>.yaml``, one provider section per
# model; ``api_key`` values are ``${ENV_VAR}`` placeholders expanded at load.
LLM_CONFIG_DIR = CONFIG_DIR / "llm"
# Per-call LLM request timeout (seconds). Without it a stalled provider call
# rides the SDK's 600s x 2 retries and a long document looks hung.
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "180"))

# MinerU local backend; the CLI picks the device. "pipeline" runs on CPU,
# "vlm-engine"/"hybrid-engine" need a GPU.
MINERU_BACKEND = os.getenv("MINERU_BACKEND", "pipeline")
# Directory under the output root that holds MinerU's parse artifacts.
MINERU_OUT_DIRNAME = "md"

FIELDS_CONFIG = CONFIG_DIR / "fields.yaml"
PIPELINE_CONFIG = CONFIG_DIR / "pipeline.yaml"

# Root for downloaded local models (YuNet face-detection ONNX, etc.).
MODELS_DIR = Path(os.getenv("MODELS_DIR", str(Path.home() / "Models")))
# Derived artifacts (MinerU parse cache) and default desensitization outputs.
CACHE_DIR = REPO_ROOT / ".cache"
OUT_DIR = Path(os.getenv("OUT_DIR", str(REPO_ROOT / "output")))

# ---------------------------------------------------------------------------
# Built-in pipeline defaults — mirror config/pipeline.yaml, used when it is
# absent (fresh clone / CI). YAML values are deep-merged over these.
# ---------------------------------------------------------------------------

_DEFAULTS: dict[str, dict[str, Any]] = {
    "text": {
        "detector": "rules",
        "model": "deepseek-v4-flash",
        "provider": "openai",
        "verify_checksums": True,
        "chunking": {
            "chunk_size": 6000,
            "title_level_limit": "auto",
        },
        "min_value_len": 2,
        "max_value_len": 64,
        "max_completion_tokens": 4000,
        "min_title_sections": 3,
    },
    "image": {
        "enabled": False,
        "classes": [],
        "detector": "auto",
        "score_threshold": 0.5,
        "mosaic_factor": 16,
        "model_path": "",
        "haar": {"scale_factor": 1.1, "min_neighbors": 5},
        "ocr": {"lang": "ch", "confidence": 0.5},
        "yolo": {"confidence": 0.25},
    },
    "output": {
        "audit": False,
    },
}


def load_yaml(path: Path | str) -> dict[str, Any]:
    """Load a YAML config file (returns {} when absent or empty)."""
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def load_pipeline_config(path: Path | str = PIPELINE_CONFIG) -> dict[str, Any]:
    """Load config/pipeline.yaml (stage switches/params for the sanitize run)."""
    return load_yaml(path)


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge ``override`` onto ``base`` (dicts recurse, everything else wins)."""
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _section(name: str) -> dict[str, Any]:
    """One merged pipeline-config section (defaults deep-merged with YAML)."""
    return _merge(_DEFAULTS.get(name, {}), load_pipeline_config().get(name, {}))


def get_text_config() -> dict[str, Any]:
    """Merged ``text`` section: detector/model/chunking/LLM call parameters."""
    return _section("text")


def get_image_config() -> dict[str, Any]:
    """Merged ``image`` section: masking switches and per-backend defaults."""
    return _section("image")


def get_output_config() -> dict[str, Any]:
    """Merged ``output`` section: audit switch and output-root settings."""
    return _section("output")
