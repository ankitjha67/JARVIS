"""Automatic model selection (FR-P8): ranking, live tests, persistence, pinning."""

from __future__ import annotations

import pytest

from jarvis.config import PROVIDER_BY_ID
from jarvis.orchestrator import is_simple_request
from jarvis.providers import Completion, ProviderError, Router, TextDelta, ToolCall, selector
from jarvis.store import Store


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


def test_quality_orders_models_sensibly():
    q = selector.quality
    assert (q("nvidia/nemotron-3-ultra-550b-a55b") > q("nvidia/nemotron-3.5-super-vl-120b-a12b")
            > q("nvidia/nemotron-3.5-bolt-e4b"))
    assert q("mistralai/mistral-large-4-1t-a52b") > q("minimaxai/minimax-m3.1-flash")
    assert q("claude-opus-5-5") > q("claude-opus-4-8") > q("claude-sonnet-5-5") > q("claude-haiku-5-5")
    assert q("meta/llama-3.3-70b-instruct") > q("meta/llama-3.1-8b-instruct")


def test_non_chat_models_are_never_candidates():
    for bad in ("nvidia/nv-embedqa-mistral-7b-v2", "nvidia/nemotron-3.5-content-safety", "nvidia/nvclip",
                "fastino/gliner2-pii", "nvidia/riva-translate-4b-instruct-v2", "nvidia/ai-synthetic-video-detector"):
        assert not selector.is_chat_candidate(bad)
    assert selector.is_chat_candidate("nvidia/nemotron-3-ultra-550b-a55b")


def test_choose_prefers_capable_models_that_passed_and_fast_ones_within_window():
    bench = selector.Bench
    results = [
        bench("big-ultra-500b", True, True, 4000), bench("mid-super-120b", True, True, 800),
        bench("tiny-flash", True, True, 600),
        bench("huge-ultra-900b", True, False, 500),   # cannot call tools: never chosen
        bench("slow-ultra-800b", True, True, 25000),  # too slow for interactive use
        bench("dead-pro", False, False, 20000, "timed out"),
    ]
    assert selector.choose(results) == ("big-ultra-500b", "mid-super-120b")
    assert selector.choose([bench("x", True, False, 100)]) == (None, None)


def test_shortlist_caps_probe_count():
    models = [f"vendor/model-{i}-{i + 1}b" for i in range(60)] + ["vendor/tiny-flash", "vendor/embed-1b"]
    picked = selector.shortlist(models)
    assert len(picked) <= selector.MAX_CANDIDATES
    assert "vendor/embed-1b" not in picked and "vendor/tiny-flash" in picked


class ScriptedCloud:
    """Answers the probe with a tool call for models whose name contains 'tool'; plain text otherwise."""

    def __init__(self, spec, models):
        self.spec, self.models, self.used = spec, models, []

    async def list_models(self):
        return self.models

    async def stream(self, model, system, messages, tools_):
        self.used.append(model)
        if "dead" in model:
            raise ProviderError("gone", retryable=True, kind="bad_request")
        if tools_ and tools_[0]["name"] == "volume_set" and "tool" in model:
            yield Completion("", [ToolCall("c", "volume_set", {"percent": 30})], "tool_use")
        else:
            yield TextDelta("hi")
            yield Completion("hi")

    async def aclose(self):
        pass


class Down:
    def __init__(self, spec):
        self.spec = spec

    async def list_models(self):
        raise ProviderError("down", retryable=True, kind="unreachable")

    async def aclose(self):
        pass


async def test_router_auto_selects_from_live_tests_and_persists(store):
    cloud = ScriptedCloud(PROVIDER_BY_ID["nvidia"],
                          ["v/tool-ultra-500b", "v/tool-flash", "v/text-only-900b", "v/dead-pro", "v/embed-1b"])
    saved: dict = {}

    def bench_store(patch):
        if patch:
            saved.update(patch)
        return saved

    def factory(spec, key):
        return cloud if spec.id == "nvidia" else Down(spec)

    def keys(pid):
        return "k" if pid == "nvidia" else None

    router = Router(store.get_settings, factory=factory, key_lookup=keys, bench_store=bench_store)
    first = {s.id: s for s in await router.probe()}["nvidia"]
    assert first.state == "testing" and router.candidates() == []
    await router.wait_for_tests()
    st = {s.id: s for s in await router.probe()}["nvidia"]
    assert st.state == "ok" and st.auto and st.model == "v/tool-ultra-500b"
    assert "v/embed-1b" not in cloud.used and "nvidia" in saved
    assert router.candidates("smart")[0].model == "v/tool-ultra-500b"

    # A fresh router reuses the saved results instead of testing again.
    cloud.used.clear()
    again = Router(store.get_settings, factory=factory, key_lookup=keys, bench_store=bench_store)
    assert {s.id: s for s in await again.probe()}["nvidia"].state == "ok" and cloud.used == []

    # A model pinned in Settings always wins over automatic selection.
    store.update_settings({"models": {"nvidia": "v/text-only-900b"}})
    pinned = {s.id: s for s in await again.probe()}["nvidia"]
    assert pinned.model == "v/text-only-900b" and not pinned.auto


def test_simple_request_detection():
    assert is_simple_request("open notepad") and is_simple_request("set the volume to 30 percent")
    assert not is_simple_request("explain why my laptop is slow")
    assert not is_simple_request("find my latest resume and then open it")


def test_internal_kv_is_not_exposed_as_a_setting(store):
    store.kv("model_bench", {"nvidia": {"sig": "x"}})
    assert "model_bench" not in store.get_settings()
    assert store.kv("model_bench")["nvidia"]["sig"] == "x"


async def test_stalled_model_falls_back_to_next_tested_model(store, monkeypatch):
    """A hosted model that sends nothing must not hang the run: the next-best tested model takes over."""
    import asyncio

    from jarvis.providers import router as router_mod

    monkeypatch.setattr(router_mod, "FIRST_EVENT_TIMEOUT_S", 0.2)

    class Stalling(ScriptedCloud):
        async def stream(self, model, system, messages, tools_):
            if tools_ and tools_[0]["name"] == "volume_set":  # model tests behave normally
                async for ev in super().stream(model, system, messages, tools_):
                    yield ev
                return
            self.used.append(model)
            if "ultra" in model:
                await asyncio.sleep(5)  # stalls
            yield Completion(f"answer from {model}")

    cloud = Stalling(PROVIDER_BY_ID["nvidia"], ["v/tool-ultra-500b", "v/tool-super-120b", "v/tool-flash"])
    router = Router(store.get_settings, factory=lambda spec, key: cloud if spec.id == "nvidia" else Down(spec),
                    key_lookup=lambda pid: "k" if pid == "nvidia" else None)
    await router.probe()
    await router.wait_for_tests()
    st = {s.id: s for s in await router.probe()}["nvidia"]
    assert st.model == "v/tool-ultra-500b" and st.alternates
    cloud.used.clear()
    events = [e async for e in router.stream("s", [{"role": "user", "content": "hi"}], [])]
    assert events[0].model != "v/tool-ultra-500b" and events[-1].text.startswith("answer from v/tool-")
    assert cloud.used[0] == "v/tool-ultra-500b" and len(cloud.used) == 2
