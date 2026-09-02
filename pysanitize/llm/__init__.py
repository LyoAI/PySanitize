"""LLM layer — independent module for calling language models.

The agent (``agent/``) depends on this module; this module does not depend on
the agent. ``OpenAICompatProvider`` talks to any OpenAI-compatible endpoint and
downstream code is agnostic to the transport behind the shared ``LLMResponse``
interface.
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
