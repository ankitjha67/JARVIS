"""Adapter for every OpenAI-compatible endpoint: LM Studio, Ollama, NVIDIA NIM, OpenAI, Gemini, OpenRouter, Groq."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx2 as httpx  # the openai and anthropic SDKs are built on httpx2
import openai

from ..config import ProviderSpec
from .base import Completion, Message, ProviderError, StreamEvent, TextDelta, ToolCall, ToolSchema

_NON_CHAT_HINTS = ("embed", "rerank", "whisper", "tts", "dall-e", "image", "moderation", "guard", "safety",
                   "parse", "translate", "audio", "realtime")


# Endpoints known to accept stream_options; others are left alone rather than risk a rejected request.
_USAGE_IN_STREAM = frozenset({"openai", "nvidia", "openrouter", "groq", "lmstudio"})


def _to_wire(system: str, messages: list[Message]) -> list[dict[str, Any]]:
    wire: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        role = m["role"]
        if role == "user":
            wire.append({"role": "user", "content": m["content"]})
        elif role == "assistant":
            item: dict[str, Any] = {"role": "assistant", "content": m.get("content") or None}
            if m.get("tool_calls"):
                item["tool_calls"] = [
                    {"id": c["id"], "type": "function",
                     "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
                    for c in m["tool_calls"]
                ]
            if item["content"] is None and "tool_calls" not in item:
                item["content"] = ""
            wire.append(item)
        elif role == "tool":
            wire.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
    return wire


def _translate(exc: Exception, label: str) -> ProviderError:
    if isinstance(exc, openai.APITimeoutError):
        return ProviderError(f"{label} timed out.", retryable=True, kind="timeout")
    if isinstance(exc, openai.APIConnectionError):
        return ProviderError(f"{label} is not reachable.", retryable=True, kind="unreachable")
    if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError):
        return ProviderError(f"{label} rejected the API key.", retryable=True, kind="auth")
    if isinstance(exc, openai.RateLimitError):
        return ProviderError(f"{label} rate limit reached.", retryable=True, kind="rate_limit")
    if isinstance(exc, openai.NotFoundError):
        return ProviderError(f"{label}: model or endpoint not found.", retryable=True, kind="bad_request")
    if isinstance(exc, openai.APIStatusError):
        if exc.status_code >= 500:
            return ProviderError(f"{label} server error ({exc.status_code}).", retryable=True, kind="server")
        detail = str(getattr(exc, "message", "") or exc)[:300]
        return ProviderError(f"{label} rejected the request: {detail}", retryable=False, kind="bad_request")
    return ProviderError(f"{label} failed: {type(exc).__name__}", retryable=True, kind="error")


class OpenAICompatProvider:
    def __init__(self, spec: ProviderSpec, api_key: str | None) -> None:
        self.spec = spec
        self._client = openai.AsyncOpenAI(
            base_url=spec.base_url,
            api_key=api_key or "not-needed",
            timeout=httpx.Timeout(120.0, connect=3.0 if spec.local else 8.0),
            max_retries=0,  # the router owns fallback; do not stack retries underneath it
        )

    async def aclose(self) -> None:
        await self._client.close()

    async def list_models(self) -> list[str]:
        try:
            page = await self._client.with_options(timeout=4.0 if self.spec.local else 10.0).models.list()
        except openai.OpenAIError as exc:
            raise _translate(exc, self.spec.label) from exc
        ids = sorted({m.id for m in page.data})
        return [i for i in ids if not any(h in i.lower() for h in _NON_CHAT_HINTS)]

    async def loaded_models(self) -> list[str]:
        """LM Studio only: models currently in memory (avoids auto-loading a 17 GB model)."""
        if self.spec.id != "lmstudio" or not self.spec.base_url:
            return []
        url = self.spec.base_url.replace("/v1", "/api/v0/models")
        try:
            async with httpx.AsyncClient(timeout=3.0) as http:
                data = (await http.get(url)).json().get("data", [])
        except (httpx.HTTPError, ValueError):
            return []
        return [m["id"] for m in data if m.get("state") == "loaded" and m.get("type") in ("llm", "vlm")]

    async def stream(
        self, model: str, system: str, messages: list[Message], tools: list[ToolSchema]
    ) -> AsyncIterator[StreamEvent]:
        kwargs: dict[str, Any] = {"model": model, "messages": _to_wire(system, messages), "stream": True}
        if self.spec.id in _USAGE_IN_STREAM:
            kwargs["stream_options"] = {"include_usage": True}  # token counts for the session readout
        if tools:
            kwargs["tools"] = [
                {"type": "function",
                 "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}}
                for t in tools
            ]
        text_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        finish = None
        usage_in = usage_out = 0
        try:
            stream = await self._client.chat.completions.create(**kwargs)
            async for chunk in stream:
                if chunk.usage:
                    usage_in, usage_out = chunk.usage.prompt_tokens or 0, chunk.usage.completion_tokens or 0
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                finish = choice.finish_reason or finish
                delta = choice.delta
                if delta is None:
                    continue
                if delta.content:
                    text_parts.append(delta.content)
                    yield TextDelta(delta.content)
                for tc in delta.tool_calls or []:
                    slot = calls.setdefault(tc.index or 0, {"id": "", "name": "", "args": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function and tc.function.name:
                        slot["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        slot["args"] += tc.function.arguments
        except openai.OpenAIError as exc:
            raise _translate(exc, self.spec.label) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.spec.label} connection dropped.", retryable=True, kind="unreachable") from exc

        tool_calls: list[ToolCall] = []
        for idx in sorted(calls):
            slot = calls[idx]
            if not slot["name"]:
                continue
            args: dict[str, Any] = {}
            err = None
            try:
                parsed = json.loads(slot["args"] or "{}")
                if isinstance(parsed, dict):
                    args = parsed
                else:
                    err = "arguments must be a JSON object"
            except json.JSONDecodeError as exc:
                err = f"arguments were not valid JSON ({exc.msg})"
            tool_calls.append(ToolCall(slot["id"] or f"call_{idx}", slot["name"], args, err))

        stop = "tool_use" if tool_calls else ("max_tokens" if finish == "length" else "end")
        if finish == "content_filter":
            stop = "refusal"
        yield Completion("".join(text_parts), tool_calls, stop, None, usage_in, usage_out)
