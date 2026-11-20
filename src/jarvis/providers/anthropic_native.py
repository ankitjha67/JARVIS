"""Native Anthropic Messages API adapter (official SDK, streaming, client tools)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import anthropic

from ..config import ProviderSpec
from .base import Completion, Message, ProviderError, StreamEvent, TextDelta, ToolCall, ToolSchema

MAX_TOKENS = 16000


def _to_wire(messages: list[Message]) -> list[dict[str, Any]]:
    """Canonical history → Messages API. Tool results are grouped into one user turn."""
    wire: list[dict[str, Any]] = []
    for m in messages:
        role = m["role"]
        if role == "user":
            wire.append({"role": "user", "content": m["content"]})
        elif role == "assistant":
            raw = (m.get("raw") or {}).get("anthropic")
            if raw:
                # Replay this provider's own blocks unchanged (thinking blocks must not be edited).
                wire.append({"role": "assistant", "content": raw})
                continue
            blocks: list[dict[str, Any]] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for c in m.get("tool_calls") or []:
                blocks.append({"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]})
            if blocks:
                wire.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            block = {
                "type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"],
                "is_error": bool(m.get("is_error")),
            }
            if wire and wire[-1]["role"] == "user" and isinstance(wire[-1]["content"], list) and all(
                b.get("type") == "tool_result" for b in wire[-1]["content"]
            ):
                wire[-1]["content"].append(block)
            else:
                wire.append({"role": "user", "content": [block]})
    return wire


def _translate(exc: Exception) -> ProviderError:
    if isinstance(exc, anthropic.APITimeoutError):
        return ProviderError("Anthropic timed out.", retryable=True, kind="timeout")
    if isinstance(exc, anthropic.APIConnectionError):
        return ProviderError("Anthropic is not reachable.", retryable=True, kind="unreachable")
    if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        return ProviderError("Anthropic rejected the API key.", retryable=True, kind="auth")
    if isinstance(exc, anthropic.NotFoundError):
        return ProviderError("Anthropic: model not found.", retryable=True, kind="bad_request")
    if isinstance(exc, anthropic.RateLimitError):
        return ProviderError("Anthropic rate limit reached.", retryable=True, kind="rate_limit")
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code >= 500:
            return ProviderError(f"Anthropic server error ({exc.status_code}).", retryable=True, kind="server")
        return ProviderError(f"Anthropic rejected the request: {str(exc.message)[:300]}", retryable=False,
                             kind="bad_request")
    return ProviderError(f"Anthropic failed: {type(exc).__name__}", retryable=True, kind="error")


class AnthropicProvider:
    def __init__(self, spec: ProviderSpec, api_key: str | None) -> None:
        self.spec = spec
        # base_url is taken from ANTHROPIC_BASE_URL by the SDK when that variable is set.
        self._client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=0)

    async def aclose(self) -> None:
        await self._client.close()

    async def list_models(self) -> list[str]:
        try:
            ids: list[str] = []
            async for model in self._client.with_options(timeout=10.0).models.list():
                ids.append(model.id)
            return ids
        except anthropic.AnthropicError as exc:
            raise _translate(exc) from exc

    async def stream(
        self, model: str, system: str, messages: list[Message], tools: list[ToolSchema]
    ) -> AsyncIterator[StreamEvent]:
        kwargs: dict[str, Any] = {
            "model": model, "max_tokens": MAX_TOKENS, "system": system, "messages": _to_wire(messages),
        }
        if tools:
            kwargs["tools"] = [
                {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools
            ]
        try:
            async with self._client.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    yield TextDelta(text)
                final = await stream.get_final_message()
        except anthropic.AnthropicError as exc:
            raise _translate(exc) from exc

        text = "".join(b.text for b in final.content if b.type == "text")
        calls = [
            ToolCall(b.id, b.name, b.input if isinstance(b.input, dict) else {},
                     None if isinstance(b.input, dict) else "arguments must be a JSON object")
            for b in final.content if b.type == "tool_use"
        ]
        stop = {"tool_use": "tool_use", "max_tokens": "max_tokens", "refusal": "refusal"}.get(
            final.stop_reason or "", "end")
        if stop in ("max_tokens", "refusal"):
            calls = []  # never run a tool call that may have been cut off mid-input
        raw = [b.model_dump(mode="json", exclude_none=True) for b in final.content]
        yield Completion(text, calls, stop, raw, final.usage.input_tokens, final.usage.output_tokens)
