"""LLM facade + factory, configured by per-model YAML.

``LLM(model_name, provider_type)`` reads ``config/llm/<model_name>.yaml`` and
builds the provider under the ``provider_type`` key (``openai:`` / ``pingan:``).
The agent talks only to ``LLM`` — never a provider directly — so the transport
is swappable behind the shared ``LLMResponse`` interface. ``get_llm()`` is the
thin factory equivalent of the constructor.

Every provider call uses keyword arguments. Provider signatures legitimately
differ — PingAn inserts ``max_context_len`` between the ABC's
``max_completion_tokens`` and ``temperature`` — so positional binding would
mis-route values; keywords make the facade immune to that.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from pysanitize.config import LLM_CONFIG_DIR
from pysanitize.utils import get_logger
from .provider import LLMProvider, LLMResponse, OpenAICompatProvider

logger = get_logger()


class LLM:
    """Provider-agnostic LLM facade the agent invokes.

    Attributes:
        provider: The wrapped concrete provider.
        model: The model id sent to the API (the provider section's
            ``model_name``, falling back to the config file's name).
    """

    def __init__(self, model_name: str, provider_type: str = "openai") -> None:
        cfg = _load_model_config(model_name)
        section = cfg.get(provider_type) or {}
        if not section:
            raise ValueError(
                f"no '{provider_type}' section in {_config_path(model_name)}; "
                f"have: {', '.join(cfg) or 'none'}"
            )
        self.model = section.get("model_name") or model_name
        self.provider = _build_provider(provider_type, section, self.model)

    def invoke(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Synchronous chat completion; ``model`` overridable via keyword."""
        return self.provider.invoke(
            messages, tools=tools, model=kwargs.pop("model", self.model), **kwargs
        )

    async def ainvoke(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Asynchronous chat completion; ``model`` overridable via keyword."""
        return await self.provider.ainvoke(
            messages, tools=tools, model=kwargs.pop("model", self.model), **kwargs
        )

    def get_default_model(self) -> str:
        return self.model


def get_llm(model_name: str, provider_type: str = "openai") -> LLM:
    """Build the LLM facade for ``model_name`` from ``config/llm/<model_name>.yaml``."""
    return LLM(model_name, provider_type)


def _config_path(model_name: str) -> Path:
    return LLM_CONFIG_DIR / f"{model_name}.yaml"


def _load_model_config(model_name: str) -> dict:
    """Load ``config/llm/<model_name>.yaml``; raises with the available models.

    ``${VAR}`` placeholders in the file are expanded from the environment, so
    API keys never live in the repo (see ``.env`` / ``.env.example``).
    """
    path = _config_path(model_name)
    if not path.is_file():
        available = ", ".join(sorted(p.stem for p in LLM_CONFIG_DIR.glob("*.yaml")))
        raise FileNotFoundError(
            f"no LLM config for {model_name!r} at {path} (have: {available or 'none'})"
        )
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(_expand_env_vars(text)) or {}


def _expand_env_vars(text: str) -> str:
    """Replace ``${VAR}`` with the env var value (empty string when unset)."""
    return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), text)


def _build_provider(
    provider_type: str, section: dict, default_model: str
) -> LLMProvider:
    if provider_type == "pingan":
        return _pingan_provider(section, default_model)
    # openai (or any other type a yaml defines): OpenAI-compatible.
    return OpenAICompatProvider(
        api_key=section.get("api_key", ""),
        api_base=section.get("api_base", ""),
        default_model=default_model,
    )


def _pingan_provider(section: dict, default_model: str) -> LLMProvider:
    #: PingAn intranet gateway creds read from the yaml's ``pingan`` section.
    _PINGAN_CRED_KEYS = (
        "appKey",
        "appSecret",
        "rsaPrivateKey",
        "openApiCredential",
        "sceneId",
        "requestId",
        "modelName",
    )
    # Guard fires before the lazy import, so a missing sceneId raises a clear
    # error instead of a cryptic ``int('')`` ValueError from the provider.
    if not section.get("sceneId"):
        raise RuntimeError(
            "pingan provider requires 'sceneId' in the config's pingan section"
        )
    from .provider import PingAnLLMProvider
    return PingAnLLMProvider(
        api_key=section.get("api_key", ""),
        api_base=section.get("api_base", "http://localhost:8000/v1"),
        default_model=default_model,
        extra_headers={key: section[key] for key in _PINGAN_CRED_KEYS if section.get(key)},
    )
