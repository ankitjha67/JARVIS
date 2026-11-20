"""Provider selection, health probing, privacy enforcement, model auto-selection and fallback (FR-P4 … FR-P8)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from .. import secrets
from ..config import ProviderSpec, all_specs
from . import selector
from .anthropic_native import AnthropicProvider
from .base import Completion, Message, Provider, ProviderError, StreamEvent, TextDelta, ToolSchema
from .openai_compat import OpenAICompatProvider

log = logging.getLogger(__name__)

Role = Literal["smart", "fast"]

FIRST_EVENT_TIMEOUT_S = 25.0
FIRST_EVENT_TIMEOUT_LOCAL_S = 180.0
MAX_ALTERNATES = 3


@dataclass
class ProviderStatus:
    id: str
    label: str
    local: bool
    needs_key: bool
    has_key: bool
    key_help: str
    state: str = "unknown"  # ok | no_key | unreachable | error | no_model | blocked | testing | unknown
    detail: str = ""
    latency_ms: int | None = None
    models: list[str] = field(default_factory=list)
    model: str | None = None  # model used for demanding requests
    fast_model: str | None = None  # model used for short, simple requests
    auto: bool = False  # True when Jarvis chose the models from live tests
    alternates: list[str] = field(default_factory=list)  # other tested models, best first (fallbacks)
    bench: list[dict[str, Any]] = field(default_factory=list)
    bench_at: float | None = None  # unix seconds of the last model test

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Route:
    provider: str
    model: str


ProviderFactory = Callable[[ProviderSpec, str | None], Provider]
BenchStore = Callable[[dict[str, Any] | None], dict[str, Any]]  # call with a patch to save, None to load


def _default_factory(spec: ProviderSpec, key: str | None) -> Provider:
    return AnthropicProvider(spec, key) if spec.kind == "anthropic" else OpenAICompatProvider(spec, key)


class Router:
    def __init__(self, get_settings: Callable[[], dict[str, Any]], factory: ProviderFactory = _default_factory,
                 key_lookup: Callable[[str], str | None] = secrets.get_key,
                 bench_store: BenchStore | None = None) -> None:
        self._get_settings = get_settings
        self._factory = factory
        self._key_lookup = key_lookup
        self._clients: dict[str, tuple[str | None, Provider]] = {}
        self._status: dict[str, ProviderStatus] = {}
        self._bench_store = bench_store
        self._bench: dict[str, dict[str, Any]] = dict(bench_store(None)) if bench_store else {}
        self._bench_tasks: dict[str, asyncio.Task[None]] = {}
        self.on_change: Callable[[], Awaitable[None]] | None = None

    # -- clients --------------------------------------------------------------------------------

    def _client(self, spec: ProviderSpec) -> Provider:
        key = self._key_lookup(spec.id) if spec.needs_key else None
        cached = self._clients.get(spec.id)
        if cached and cached[0] == key:
            return cached[1]
        client = self._factory(spec, key)
        self._clients[spec.id] = (key, client)
        return client

    async def aclose(self) -> None:
        for task in self._bench_tasks.values():
            task.cancel()
        for _, client in self._clients.values():
            try:
                await client.aclose()
            except Exception:
                log.debug("provider close failed", exc_info=True)

    # -- health ---------------------------------------------------------------------------------

    def specs(self) -> list[ProviderSpec]:
        """Built-in providers plus the user's custom endpoints."""
        return all_specs(self._get_settings())

    def spec(self, provider_id: str) -> ProviderSpec | None:
        return next((s for s in self.specs() if s.id == provider_id), None)

    def label(self, provider_id: str) -> str:
        spec = self.spec(provider_id)
        return spec.label if spec else provider_id

    def statuses(self) -> list[ProviderStatus]:
        return [self._status[p.id] for p in self.specs() if p.id in self._status]

    async def probe(self, retest: bool = False) -> list[ProviderStatus]:
        """Check every provider. `retest` discards saved model tests so they run again."""
        if retest:
            self._bench.clear()
        settings = self._get_settings()
        results = await asyncio.gather(*(self._probe_one(p, settings) for p in all_specs(settings)))
        self._status = {s.id: s for s in results}
        return results

    async def wait_for_tests(self) -> None:
        """Block until running model tests finish (used by tests and first-run flows)."""
        while self._bench_tasks:
            await asyncio.gather(*list(self._bench_tasks.values()), return_exceptions=True)

    async def _probe_one(self, spec: ProviderSpec, settings: dict[str, Any]) -> ProviderStatus:
        has_key = bool(self._key_lookup(spec.id)) if spec.needs_key else True
        st = ProviderStatus(spec.id, spec.label, spec.local, spec.needs_key, has_key, spec.key_help)
        if settings["privacy_mode"] == "local" and not spec.local:
            st.state, st.detail = "blocked", "Blocked by Local-only privacy mode."
            return st
        if spec.needs_key and not has_key:
            st.state, st.detail = "no_key", "No API key."
            return st
        client = self._client(spec)
        started = time.perf_counter()
        try:
            st.models = await client.list_models()
        except ProviderError as exc:
            st.state = "unreachable" if exc.kind in ("unreachable", "timeout") else "error"
            st.detail = "Not running." if spec.local and st.state == "unreachable" else str(exc)
            return st
        except Exception as exc:  # noqa: BLE001 - a probe must never take the core down
            log.warning("probe of %s failed: %s", spec.id, type(exc).__name__)
            st.state, st.detail = "error", f"Probe failed ({type(exc).__name__})."
            return st
        st.latency_ms = int((time.perf_counter() - started) * 1000)

        pinned = settings["models"].get(spec.id)
        if pinned:
            st.model = st.fast_model = pinned  # an explicit choice in Settings always wins
        elif spec.local:
            # Never test local models automatically: probing would load multi-GB models into memory.
            if spec.id == "lmstudio" and isinstance(client, OpenAICompatProvider):
                loaded = await client.loaded_models()
                st.model = loaded[0] if loaded else None
            elif len(st.models) == 1:
                st.model = st.models[0]
            st.fast_model = st.model
        else:
            self._auto_select(spec, client, st)

        if st.model:
            st.state = "ok"
        elif st.state != "testing":
            st.state = "no_model"
            st.detail = st.detail or ("No model loaded — load one in LM Studio or choose one in Settings."
                                      if spec.id == "lmstudio" else "Choose a model in Settings.")
        return st

    # -- automatic model selection ----------------------------------------------------------------

    def _auto_select(self, spec: ProviderSpec, client: Provider, st: ProviderStatus) -> None:
        st.auto = True
        sig = selector.signature(st.models)
        saved = self._bench.get(spec.id)
        fresh = bool(saved and saved.get("sig") == sig and time.time() - saved.get("at", 0) < selector.BENCH_TTL_S)
        if saved and saved.get("sig") == sig:
            results = [selector.Bench(**r) for r in saved.get("results", [])]
            st.bench = sorted(saved.get("results", []), key=lambda r: (not r["tool_ok"], r["latency_ms"]))
            st.bench_at = saved.get("at")
            st.model, st.fast_model = selector.choose(results)
            st.alternates = [m for m in selector.ranked(results) if m not in (st.model, st.fast_model)][:MAX_ALTERNATES]
            if not st.model and fresh:
                st.detail = "None of this provider's models handled a tool call. Choose one manually in Settings."
        if fresh:
            return
        if spec.id not in self._bench_tasks:
            self._bench_tasks[spec.id] = asyncio.create_task(self._run_bench(spec, client, st.models, sig))
        if not st.model:  # nothing usable yet: say so; otherwise keep serving with the previous choice
            st.state = "testing"
            st.detail = f"Testing {len(selector.shortlist(st.models))} models for tool use and speed…"

    async def _run_bench(self, spec: ProviderSpec, client: Provider, models: list[str], sig: str) -> None:
        try:
            results = await selector.benchmark(client, models)
            entry = {"sig": sig, "at": time.time(), "results": [r.to_json() for r in results]}
            self._bench[spec.id] = entry
            if self._bench_store:
                await asyncio.to_thread(self._bench_store, {spec.id: entry})
            log.info("model test for %s: %d of %d handle tools", spec.id, sum(r.tool_ok for r in results), len(results))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("model test for %s failed", spec.id)
        finally:
            self._bench_tasks.pop(spec.id, None)
        if self.on_change:
            await self.on_change()

    # -- routing --------------------------------------------------------------------------------

    def candidates(self, role: Role = "smart") -> list[Route]:
        """Ordered routes permitted by privacy mode and routing mode. Enforced here, not in the UI."""
        settings = self._get_settings()
        routing = settings["routing"]
        mode = settings["privacy_mode"]  # local: local runtimes only · cloud: cloud only · hybrid: both
        if routing["mode"] == "manual" and routing.get("manual_provider"):
            order = [routing["manual_provider"]]
        else:
            known = {s.id: s for s in all_specs(settings)}
            order = [p for p in routing["order"] if p in known]
            order += [pid for pid in known if pid not in order]
        by_id = {s.id: s for s in all_specs(settings)}
        routes: list[Route] = []
        for pid in order:
            spec = by_id.get(pid)
            st = self._status.get(pid)
            if not spec or not st or st.state != "ok" or not st.model:
                continue
            if (mode == "local" and not spec.local) or (mode == "cloud" and spec.local):
                continue
            first = (st.fast_model or st.model) if role == "fast" else st.model
            routes.append(Route(pid, first))
            # The provider's other tested models are the next fallbacks before moving to another provider.
            for other in (st.model, st.fast_model, *st.alternates):
                if other and all(r.model != other or r.provider != pid for r in routes):
                    routes.append(Route(pid, other))
        return routes

    async def stream(
        self, system: str, messages: list[Message], tools: list[ToolSchema], role: Role = "smart"
    ) -> AsyncIterator[Route | StreamEvent]:
        """Yield the chosen Route, then stream events. Falls back only before any output is produced."""
        routes = self.candidates(role)
        if not routes:
            await self.probe()
            routes = self.candidates(role)
        if not routes:
            if any(s.state == "testing" for s in self._status.values()):
                raise ProviderError("Still testing which models work — try again in a few seconds.",
                                    retryable=False, kind="unreachable")
            mode = self._get_settings()["privacy_mode"]
            hint = (" Local-only mode is on and no local runtime has a model ready." if mode == "local"
                    else " Add an API key or start a local runtime in Settings.")
            raise ProviderError("No model provider is available." + hint, retryable=False, kind="unreachable")

        errors: list[str] = []
        for route in routes:
            spec = self.spec(route.provider)
            if spec is None:
                continue
            client = self._client(spec)
            produced = False
            stream = client.stream(route.model, system, messages, tools)
            try:
                # A hosted model that sends nothing for this long has stalled: move on rather than hang the run.
                # Local models get much longer because they may still be loading into memory.
                wait = FIRST_EVENT_TIMEOUT_LOCAL_S if spec.local else FIRST_EVENT_TIMEOUT_S
                try:
                    first = await asyncio.wait_for(anext(stream), timeout=wait)
                except TimeoutError as exc:
                    raise ProviderError(f"{spec.label} did not respond within {wait:.0f} s.", retryable=True,
                                        kind="timeout") from exc
                except StopAsyncIteration:
                    errors.append(f"{route.provider}: empty response")
                    continue
                produced = True
                yield route
                yield first
                if isinstance(first, Completion):
                    return
                async for event in stream:
                    yield event
                    if isinstance(event, Completion):
                        return
                return
            except ProviderError as exc:
                if produced or not exc.retryable:
                    raise
                log.warning("%s/%s failed (%s); trying next", route.provider, route.model, exc.kind)
                errors.append(str(exc))
                st = self._status.get(route.provider)
                if st and exc.kind in ("unreachable", "auth"):
                    st.state, st.detail = ("error" if exc.kind == "auth" else "unreachable"), str(exc)
            finally:
                if not produced:
                    aclose = getattr(stream, "aclose", None)
                    if aclose:
                        try:
                            await aclose()
                        except Exception:
                            log.debug("closing a stalled stream failed", exc_info=True)
        raise ProviderError("All providers failed. " + " ".join(dict.fromkeys(errors)), retryable=False,
                            kind="unreachable")


__all__ = ["ProviderStatus", "Role", "Route", "Router", "TextDelta"]
