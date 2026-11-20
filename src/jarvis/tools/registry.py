"""Tool registry: every tool has a pydantic args model, a base tier and an async handler."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..policy import Tier


class ToolError(Exception):
    """A tool failed or refused. The message is safe to show the user and the model."""


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


Classifier = Callable[[Any], tuple[Tier, str]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[Args]
    tier: Tier
    handler: Callable[[Any], Awaitable[Any]]
    summary: Callable[[Any], str]
    taints: bool = False  # result is untrusted content (SEC-1)
    classify: Classifier | None = None  # argument-dependent tier/refusal
    sensitive: bool = False  # acts outside the machine or cannot be undone: always needs approval
    raw_schema: dict[str, Any] | None = None  # JSON Schema for tools defined elsewhere (MCP servers)

    def validate(self, raw: dict[str, Any]) -> Any:
        """Parse model-supplied arguments. Raises pydantic.ValidationError for native tools."""
        if self.raw_schema is not None:
            if not isinstance(raw, dict):
                raise ToolError("arguments must be a JSON object")
            return raw  # the owning server validates against its own schema
        return self.args_model.model_validate(raw)

    @staticmethod
    def dump(args: Any) -> dict[str, Any]:
        return args if isinstance(args, dict) else args.model_dump()

    def schema(self) -> dict[str, Any]:
        if self.raw_schema is not None:
            return {"name": self.name, "description": self.description, "parameters": self.raw_schema}
        params = self.args_model.model_json_schema()
        params.pop("title", None)
        for prop in params.get("properties", {}).values():
            prop.pop("title", None)
        return {"name": self.name, "description": self.description, "parameters": params}

    def classify_call(self, args: Any) -> tuple[Tier, str]:
        """May raise ToolError to refuse outright."""
        if self.classify:
            return self.classify(args)
        return self.tier, ""


REGISTRY: dict[str, Tool] = {}


def tool(
    name: str, description: str, args: type[Args], tier: Tier, *, summary: Callable[[Any], str],
    taints: bool = False, classify: Classifier | None = None, sensitive: bool = False,
) -> Callable[[Callable[[Any], Awaitable[Any]]], Callable[[Any], Awaitable[Any]]]:
    def register(fn: Callable[[Any], Awaitable[Any]]) -> Callable[[Any], Awaitable[Any]]:
        if name in REGISTRY:
            raise RuntimeError(f"duplicate tool: {name}")
        REGISTRY[name] = Tool(name, description, args, tier, fn, summary, taints, classify, sensitive)
        return fn
    return register


def schemas() -> list[dict[str, Any]]:
    return [t.schema() for t in sorted(REGISTRY.values(), key=lambda t: t.name)]
