"""Provider-neutral types.

Canonical message shapes (what the orchestrator and the store use):
  {"role": "user", "content": str}
  {"role": "assistant", "content": str, "tool_calls": [{"id", "name", "args"}], "raw": {provider_id: ...}}
  {"role": "tool", "tool_call_id": str, "name": str, "content": str, "is_error": bool}

`raw` carries a provider's own representation of an assistant turn so it can be replayed
unchanged to the same provider (required for Anthropic thinking blocks).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..config import ProviderSpec

Message = dict[str, Any]
ToolSchema = dict[str, Any]  # {"name", "description", "parameters": JSON Schema}


@dataclass
class TextDelta:
    text: str


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]
    parse_error: str | None = None


@dataclass
class Completion:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop: str = "end"  # end | tool_use | max_tokens | refusal
    raw: Any = None
    input_tokens: int = 0
    output_tokens: int = 0


StreamEvent = TextDelta | Completion


class ProviderError(Exception):
    """A provider call failed. `retryable` means another provider may be tried."""

    def __init__(self, message: str, *, retryable: bool, kind: str = "error") -> None:
        super().__init__(message)
        self.retryable = retryable
        self.kind = kind  # auth | rate_limit | unreachable | timeout | bad_request | server | error


class Provider(Protocol):
    spec: ProviderSpec

    async def list_models(self) -> list[str]: ...

    def stream(
        self, model: str, system: str, messages: list[Message], tools: list[ToolSchema]
    ) -> AsyncIterator[StreamEvent]: ...

    async def aclose(self) -> None: ...
