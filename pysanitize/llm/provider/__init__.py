"""LLM providers.

- ``OpenAICompatProvider`` — external / internet access via any OpenAI-compatible endpoint.
- ``PingAnLLMProvider`` — intranet (PingAn gateway) access. Imported lazily
  because it pulls in pycryptodome / requests / json_repair, which are only
  needed for the intranet path.
- ``Pingan_LangchainLLMServing`` — legacy langchain-based PingAn backup (kept, not exported).
"""

from .base import LLMProvider, LLMResponse, ToolCallRequest
from .openai import OpenAICompatProvider

__all__ = [
    "LLMProvider",
    "LLMResponse",
    "ToolCallRequest",
    "OpenAICompatProvider",
]


def __getattr__(name: str):
    if name == "PingAnLLMProvider":
        from .pingan import PingAnLLMProvider
        return PingAnLLMProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
