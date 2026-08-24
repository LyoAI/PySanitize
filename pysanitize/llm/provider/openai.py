"""OpenAI-compatible LLM provider for external (internet) access.

Uses the OpenAI SDK directly (no langchain). Works with any OpenAI-compatible
chat-completions endpoint: OpenAI, DeepSeek, Qwen via DashScope compatible-mode,
local vLLM, etc. The PingAn intranet gateway is the same protocol and is served
by ``PingAnLLMProvider`` (``pingan.py``) with extra auth headers.
"""

from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI, OpenAI

from pysanitize.config import LLM_TIMEOUT_S
from pysanitize.utils import get_logger
from .base import LLMProvider, LLMResponse, ToolCallRequest

logger = get_logger()


class OpenAICompatProvider(LLMProvider):
    """Provider for any OpenAI-compatible endpoint."""

    def __init__(
        self,
        api_key: str,
        api_base: str,
        default_model: str,
        extra_headers: dict[str, Any] | None = None,
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.extra_headers = extra_headers or {}
        # A bounded request timeout (SDK default is 600s x 2 retries — one
        # stalled call then blocks an episode for ~30min) and a single retry.
        self.client = OpenAI(api_key=api_key, base_url=api_base,
                             timeout=LLM_TIMEOUT_S, max_retries=1)
        self.async_client = AsyncOpenAI(api_key=api_key, base_url=api_base,
                                        timeout=LLM_TIMEOUT_S, max_retries=1)

    def _parse(self, response: Any) -> LLMResponse:
        choice = response.choices[0] if response.choices else None
        message = choice.message if choice else None
        tool_calls = [
            ToolCallRequest(
                id=tc.id,
                name=tc.function.name,
                arguments=self._parse_arguments(tc.function.arguments),
            )
            for tc in (message.tool_calls or [])
        ]
        usage = response.usage
        return LLMResponse(
            content=message.content if message else None,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage={
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            }
            if usage
            else {},
            reasoning_content=getattr(message, "reasoning_content", None) or None,
        )

    @staticmethod
    def _parse_arguments(raw: str | dict[str, Any]) -> dict[str, Any]:
        """Parse tool-call arguments; fall back to a raw dict on malformed JSON."""
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Failed to parse tool arguments; returning raw string.")
            return {"_raw": raw}

    def _build_kwargs(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_completion_tokens: int,
        temperature: float,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": self._sanitize_empty_content(messages),
            "max_completion_tokens": max(1, max_completion_tokens),
            "temperature": temperature,
        }
        if self.extra_headers:
            kwargs["extra_headers"] = self.extra_headers
        if tools:
            kwargs.update(tools=tools, tool_choice="auto")
        if response_format:
            kwargs["response_format"] = response_format
        return kwargs

    def invoke(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_completion_tokens: int = 4096,
        temperature: float = 0.7,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        try:
            response = self.client.chat.completions.create(
                **self._build_kwargs(messages, tools, model, max_completion_tokens, temperature, response_format)
            )
            return self._parse(response)
        except Exception as e:
            logger.error("Error invoking LLM: %s", e)
            return LLMResponse(content=None, finish_reason="error", error=str(e))

    async def ainvoke(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_completion_tokens: int = 4096,
        temperature: float = 0.7,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        try:
            response = await self.async_client.chat.completions.create(
                **self._build_kwargs(messages, tools, model, max_completion_tokens, temperature, response_format)
            )
            return self._parse(response)
        except Exception as e:
            logger.error("Error invoking LLM: %s", e)
            return LLMResponse(content=None, finish_reason="error", error=str(e))
