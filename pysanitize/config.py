"""Global configuration: loads ``.env`` at the repo root and exposes settings.

Mirrors the pattern in ``finsearch/config.py``: environment-driven knobs with
sane defaults, loaded once at import time. Only the knobs the desensitizer
needs are wired up so far.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from dotenv import load_dotenv

# Repo root: ``pysanitize/config.py`` → one level up.
REPO_ROOT = Path(__file__).resolve().parent.parent

# Load KEY=VALUE pairs from ``.env`` (never overwrites existing env vars).
load_dotenv(REPO_ROOT / ".env")

# ---- LLM settings --------------------------------------------------------
# LLM model configs live in ``config/llm/<model>.yaml`` — one ``openai:``
# section per model; ``api_key`` values are ``${ENV_VAR}`` placeholders
# expanded at load time (see ``llm/llm_registry._load_model_config``).
LLM_CONFIG_DIR = REPO_ROOT / "config" / "llm"
# Per-call LLM request timeout (seconds). Without it a stalled provider call
# rides the SDK's 600s x 2 retries and a long document looks hung.
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "180"))

# ---- Document parsing (MinerU) -------------------------------------------
# MinerU local backend; the CLI picks the device. "pipeline" runs on CPU,
# "vlm-engine"/"hybrid-engine" need a GPU.
MINERU_BACKEND = os.getenv("MINERU_BACKEND", "pipeline")
# Directory under the output root that holds MinerU's parse artifacts.
MINERU_OUT_DIRNAME = "md"

# ---- Desensitizer config ---------------------------------------------------
FIELDS_CONFIG = REPO_ROOT / "config" / "fields.yaml"
PIPELINE_CONFIG = REPO_ROOT / "config" / "pipeline.yaml"


def load_yaml(path: Path | str) -> dict[str, Any]:
    """Load a YAML config file (returns {} when absent or empty)."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_pipeline_config(path: Path | str = PIPELINE_CONFIG) -> dict[str, Any]:
    """Load config/pipeline.yaml (stage switches/params for the sanitize run)."""
    return load_yaml(path)

# ---- Local models & outputs ------------------------------------------------
# Root for downloaded local models (YuNet face-detection ONNX, etc.).
MODELS_DIR = Path(os.getenv("MODELS_DIR", str(Path.home() / "Models")))
# Derived artifacts (MinerU parse cache) and default desensitization outputs.
CACHE_DIR = REPO_ROOT / ".cache"
OUT_DIR = Path(os.getenv("OUT_DIR", str(REPO_ROOT / "output")))
