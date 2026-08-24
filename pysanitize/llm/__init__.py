"""LLM layer — independent module for calling language models.

The agent (``agent/``) depends on this module; this module does not depend on
the agent. Both intranet (``PingAnLLMProvider``) and external
(``OpenAICompatProvider``) access share the same ``LLMResponse`` interface, so
downstream code is agnostic to the transport.
"""

from .llm_registry import LLM, get_llm
from .provider import LLMProvider, LLMResponse, ToolCallRequest, OpenAICompatProvider

__all__ = [
    "LLM",
    "get_llm",
    "LLMProvider",
    "LLMResponse",
    "ToolCallRequest",
    "OpenAICompatProvider",
]


def __getattr__(name: str):
    """Lazy ``PingAnLLMProvider`` — only needed on the intranet path."""
    if name == "PingAnLLMProvider":
        from .provider import PingAnLLMProvider
        return PingAnLLMProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
