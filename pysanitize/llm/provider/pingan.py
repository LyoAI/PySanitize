from __future__ import annotations
from typing import Any, Dict, List, Optional
from openai import OpenAI, AsyncOpenAI
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Signature import PKCS1_v1_5
from urllib.parse import urlencode
from .base import LLMProvider, LLMResponse, ToolCallRequest
from pysanitize.config import LLM_TIMEOUT_S
from pysanitize.utils import get_logger
import base64
import hmac
import binascii
import json
import json_repair
import time


# module logger
logger = get_logger()

class PingAnLLMProvider(LLMProvider):
    """PingAn LLM platform interface implementation."""

    def __init__(self, api_key: str = "token-abc", api_base: str = "http://localhost:8000/v1", default_model: str = "default",
                 extra_headers: dict[str, any] | None = None):
        super().__init__(api_key, api_base)
        self.extra_headers = extra_headers or {}
        # LLM platform application appKey
        self.app_key = self.extra_headers.pop("appKey", "")
        # LLM platform application appSecret
        self.app_secret = self.extra_headers.pop("appSecret", "")
        # credential secret obtained from the eagw gateway
        self.rsa_private_key = self.extra_headers.pop("rsaPrivateKey", "")
        self.openapi_credential = self.extra_headers.pop("openApiCredential", "")
        self.scene_id = int(self.extra_headers.pop("sceneId", ""))
        self.request_id = self.extra_headers.pop("requestId", "")
        self.model_name = self.extra_headers.pop("model_name", "")

        logger.info(f"app_key = {self.app_key}, app_secret = {self.app_secret}, scene_id = {self.scene_id}, "
                    f"request_id = {self.request_id}, model_name = {self.model_name}")
        
        self.default_model = default_model
        self.client = OpenAI(api_key=api_key, base_url=api_base,
                             timeout=LLM_TIMEOUT_S, max_retries=1)
        self.async_client = AsyncOpenAI(api_key=api_key, base_url=api_base,
                                        timeout=LLM_TIMEOUT_S, max_retries=1)
        self.requestTime = str(int(time.time() * 1000))

    def get_gpt_sign(self, app_key: str, app_secret: str, requestTime: int) -> str:
        """Generate the GPT interface call signature."""
        params = {
            "openApiRequestTime": str(requestTime),
            "appKey": app_key,
            "appSecret": app_secret
        }
        query_string = urlencode(params).lower()
        hmac_obj = hmac.new(app_secret.encode("utf-8"), query_string.encode("utf-8"), 'sha1')
        signature = base64.b64encode(hmac_obj.digest()).decode("utf-8")
        return signature

    def get_sign(self, rsaPrivateKey, requestTime):
        '''
        Generate the signature from the credential private key.
        '''
        binary_key = binascii.a2b_hex(rsaPrivateKey)
        pkcs8_private_key = RSA.import_key(binary_key)
        h = SHA256.new(requestTime.encode("utf-8"))
        signer = PKCS1_v1_5.new(pkcs8_private_key)
        signature = signer.sign(h).hex().upper()
        return signature

    def _parse(self, response: Any) -> LLMResponse:
        # The gateway may attach a top-level resultCode; the OpenAI SDK object
        # doesn't, so read it defensively rather than crashing on every response.
        result_code = getattr(response, "resultCode", "0")
        if result_code != "0":
            logger.error(f"API error: {result_code} -> {response}")

        choice = response.choices[0] if response.choices else None
        message = choice.message if choice else None
        tool_calls = [
            ToolCallRequest(id=tool_call.id, name=tool_call.function.name,
                            arguments=json_repair.loads(tool_call.function.arguments) if isinstance(tool_call.function.arguments, str) else tool_call.function.arguments)
            for tool_call in (message.tool_calls or [])
        ]
        u = response.usage
        return LLMResponse(
            content = message.content if message else "",
            tool_calls = tool_calls,
            finish_reason = choice.finish_reason or "stop",
            usage = {
                "prompt_tokens": u.prompt_tokens,
                "completion_tokens": u.completion_tokens,
                "total_tokens": u.total_tokens
            } if u else {},
            reasoning_content = getattr(message, "reasoning_content", None) or None
        )
    
    def invoke(
        self, 
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None = None,
        model: Optional[str] = None,
        max_completion_tokens: Optional[int] = 4096,
        max_context_len: int = 200000,
        temperature: Optional[float] = 0.7,
        top_p: Optional[float] = 1.0,
        enable_thinking: bool = False,
        timeout: Optional[float] = None,  # None = inherit the client timeout
        stream: bool = False,
    ) -> LLMResponse:
        """Invoke the PingAn LLM platform interface."""
        request_time = str(int(time.time() * 1000))
        get_signature = self.get_gpt_sign(self.app_key, self.app_secret, request_time)
        eagw_sign = self.get_sign(self.rsa_private_key, request_time)
        model = model or self.default_model

        extra_body = {
            "request_id": self.request_id,
            "scene_id": self.scene_id,
        }
        chat_template_kwargs = {"thinking": enable_thinking} if "deepseek" in model else {"enable_thinking": enable_thinking}
        extra_body.update({"chat_template_kwargs": chat_template_kwargs})

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": self._sanitize_empty_content(messages),
            "max_completion_tokens": max(1, max_completion_tokens),
            "extra_body": extra_body,
            "extra_headers": {
                "Content-Type": "application/json",
                "openApiCode": "API035059",
                "openApiCredential": self.openapi_credential,
                "openApiRequestTime": request_time,
                "openApiSignature": eagw_sign,
                "gpt_app_key": self.app_key,
                "gpt_signature": get_signature
            },
            "temperature": temperature,
            "top_p": top_p,
            "timeout": timeout,
            "stream": stream
        }
        if tools:
            kwargs.update(tools=tools, tool_choice="auto")
        
        # the PingAn platform caps request headers at 2MB
        current_size = len(json.dumps(kwargs, ensure_ascii=False).encode("utf-8"))
        if current_size > 2000000:
            logger.warning(f"The request size {current_size} exceeds the limit of 2M length. Consider reducing the size of messages or tools.")
            last_user_idx = -1
            for i in range(len(messages)-1, -1, -1):
                if messages[i].get("role") == "user":
                    last_user_idx = i
                    break

            if last_user_idx != -1:
                message= messages[last_user_idx]
                message["content"] = message["content"][:max_context_len//2] + " ... " + message["content"][-max_context_len//2:]
            
            kwargs.update(messages=self._sanitize_empty_content(messages))
        
        new_size = len(json.dumps(kwargs, ensure_ascii=False).encode("utf-8"))
        if new_size > 2000000:
            logger.error(f"Even after truncation, the request size {new_size} still exceeds the limit of 2M length. Please further reduce the size of messages or tools.")
            raise ValueError("Request size exceeds the limit of 2M length.")

        try:
            response = self.client.chat.completions.create(**kwargs)
            return self._parse(response)
        except Exception as e:
            logger.error(f"Error occurred while invoking the LLM: {e}")
            return LLMResponse(content=None, finish_reason="error", error=str(e))
        
    async def ainvoke(
        self, 
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None = None,
        model: Optional[str] = None,
        max_completion_tokens: Optional[int] = 4096,
        max_context_len: int = 200000,
        temperature: Optional[float] = 0.7,
        top_p: Optional[float] = 1.0,
        enable_thinking: bool = False,
        timeout: Optional[float] = None,  # None = inherit the client timeout
        stream: bool = False,
    ) -> LLMResponse:
        """Asynchronously invoke the PingAn LLM platform interface."""
        request_time = str(int(time.time() * 1000))
        get_signature = self.get_gpt_sign(self.app_key, self.app_secret, request_time)
        eagw_sign = self.get_sign(self.rsa_private_key, request_time)
        model = model or self.default_model

        extra_body = {
            "request_id": self.request_id,
            "scene_id": self.scene_id,
        }
        chat_template_kwargs = {"thinking": enable_thinking} if "deepseek" in model else {"enable_thinking": enable_thinking}
        extra_body.update({"chat_template_kwargs": chat_template_kwargs})

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": self._sanitize_empty_content(messages),
            "max_completion_tokens": max(1, max_completion_tokens),
            "extra_body": extra_body,
            "extra_headers": {
                "Content-Type": "application/json",
                "openApiCode": "API035059",
                "openApiCredential": self.openapi_credential,
                "openApiRequestTime": request_time,
                "openApiSignature": eagw_sign,
                "gpt_app_key": self.app_key,
                "gpt_signature": get_signature
            },
            "temperature": temperature,
            "top_p": top_p,
            "timeout": timeout,
            "stream": stream
        }
        if tools:
            kwargs.update(tools=tools, tool_choice="auto")
        
        # the PingAn platform caps request headers at 2MB
        current_size = len(json.dumps(kwargs, ensure_ascii=False).encode("utf-8"))
        if current_size > 2000000:
            logger.warning(f"The request size {current_size} exceeds the limit of 2M length. Consider reducing the size of messages or tools.")
            last_user_idx = -1
            for i in range(len(messages)-1, -1, -1):
                if messages[i].get("role") == "user":
                    last_user_idx = i
                    break
            if last_user_idx != -1:
                message= messages[last_user_idx]
                message["content"] = message["content"][:max_context_len//2] + " ... " + message["content"][-max_context_len//2:]
            
            kwargs.update(messages=self._sanitize_empty_content(messages))
        new_size = len(json.dumps(kwargs, ensure_ascii=False).encode("utf-8"))
        if new_size > 2000000:
            logger.error(f"Even after truncation, the request size {new_size} still exceeds the limit of 2M length. Please further reduce the size of messages or tools.")
            raise ValueError("Request size exceeds the limit of 2M length.")
        
        try:
            response = await self.async_client.chat.completions.create(**kwargs)
            return self._parse(response)
        except Exception as e:
            logger.error(f"Error occurred while invoking the LLM: {e}")
            return LLMResponse(content=None, finish_reason="error", error=str(e))
    
