"""Global configuration: loads ``.env`` at the repo root and exposes settings."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(REPO_ROOT / ".env")

# Local ``config/`` (untracked, may hold real keys). A fresh clone or CI has no
# config dir; loaders fall back to built-in defaults (``specs.DEFAULT_FIELD_SPECS``,
# empty pipeline config) so the tool works out of the box.
CONFIG_DIR = REPO_ROOT / "config"

# LLM model configs: ``config/llm/<model>.yaml``, one ``openai:`` section per
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

# Root for downloaded local models (YuNet face-detection ONNX, etc.).
MODELS_DIR = Path(os.getenv("MODELS_DIR", str(Path.home() / "Models")))
# Derived artifacts (MinerU parse cache) and default desensitization outputs.
CACHE_DIR = REPO_ROOT / ".cache"
OUT_DIR = Path(os.getenv("OUT_DIR", str(REPO_ROOT / "output")))
