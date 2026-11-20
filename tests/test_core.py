"""Router, orchestrator and gateway behaviour against scripted fake providers. No network, no GPU."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from jarvis import protocol, tools
from jarvis.config import PORT, PROVIDER_BY_ID, ProviderSpec
from jarvis.gateway import Hub, create_app, validate_settings_patch
from jarvis.orchestrator import Orchestrator, _trim_to_valid
from jarvis.policy import Tier
from jarvis.providers import Completion, ProviderError, Router, TextDelta, ToolCall
from jarvis.store import Store
from jarvis.tools.registry import REGISTRY, Args, Tool


class FakeProvider:
    """Plays back a script: each entry is a Completion or an exception to raise."""

    def __init__(self, spec: ProviderSpec, script: list[Any], models: list[str] | None = None) -> None:
        self.spec, self.script, self.models, self.calls = spec, list(script), models or ["m1"], 0
        self.seen: list[list[dict[str, Any]]] = []

    async def list_models(self) -> list[str]:
        if self.models == ["__down__"]:
            raise ProviderError("down", retryable=True, kind="unreachable")
        return self.models

    async def stream(self, model, system, messages, tools_) -> AsyncIterator[Any]:
        self.calls += 1
        self.tools_offered = getattr(self, "tools_offered", []) + [len(tools_)]
        self.seen.append([dict(m) for m in messages])
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if item.text:
            yield TextDelta(item.text)
        yield item

    async def aclose(self) -> None:
        pass


def make_router(store: Store, fakes: dict[str, FakeProvider]) -> Router:
    def factory(spec: ProviderSpec, key: str | None):
        return fakes.get(spec.id) or FakeProvider(spec, [], ["__down__"])
    store.update_settings({"models": {pid: "m1" for pid in fakes}})  # pinned: no automatic model tests
    return Router(store.get_settings, factory=factory, key_lookup=lambda pid: "k" if pid in fakes else None)


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


def fake(pid: str, script: list[Any]) -> FakeProvider:
    return FakeProvider(PROVIDER_BY_ID[pid], script)


# -- router -------------------------------------------------------------------------------------

async def test_router_falls_back_before_output(store):
    fakes = {"anthropic": fake("anthropic", [ProviderError("boom", retryable=True, kind="server")]),
             "ollama": fake("ollama", [Completion("hi")])}
    router = make_router(store, fakes)
    await router.probe()
    events = [e async for e in router.stream("s", [{"role": "user", "content": "x"}], [])]
    assert events[0].provider == "ollama" and isinstance(events[-1], Completion)


async def test_router_local_mode_refuses_cloud(store):
    store.update_settings({"privacy_mode": "local"})
    fakes = {"anthropic": fake("anthropic", [Completion("cloud")])}
    router = make_router(store, fakes)
    statuses = {s.id: s for s in await router.probe()}
    assert statuses["anthropic"].state == "blocked"
    assert router.candidates() == []
    with pytest.raises(ProviderError):
        async for _ in router.stream("s", [], []):
            pass
    assert fakes["anthropic"].calls == 0


async def test_router_cloud_mode_skips_local(store):
    store.update_settings({"privacy_mode": "cloud"})
    router = make_router(store, {"ollama": fake("ollama", []), "groq": fake("groq", [])})
    store.update_settings({"models": {"groq": "m1"}})
    await router.probe()
    assert [r.provider for r in router.candidates()] == ["groq"]


async def test_router_non_retryable_error_propagates(store):
    fakes = {"anthropic": fake("anthropic", [ProviderError("bad", retryable=False, kind="bad_request")]),
             "ollama": fake("ollama", [Completion("hi")])}
    router = make_router(store, fakes)
    await router.probe()
    with pytest.raises(ProviderError):
        async for _ in router.stream("s", [], []):
            pass
    assert fakes["ollama"].calls == 0


# -- orchestrator -------------------------------------------------------------------------------

class EchoArgs(Args):
    value: str


@pytest.fixture
def test_tools():
    """Register throwaway tools at each tier; remove them afterwards."""
    ran: list[str] = []

    def add(name: str, tier: Tier, taints: bool = False):
        async def handler(a: EchoArgs) -> dict:
            ran.append(f"{name}:{a.value}")
            return {"echo": a.value}
        REGISTRY[name] = Tool(name, name, EchoArgs, tier, handler, lambda a: f"{name} {a.value}", taints)

    add("t_read", Tier.T0, taints=True)
    add("t_soft", Tier.T1)
    add("t_hard", Tier.T2)
    add("t_nuke", Tier.T3)
    yield ran
    for n in ("t_read", "t_soft", "t_hard", "t_nuke"):
        REGISTRY.pop(n, None)


class Harness:
    def __init__(self, store: Store, script: list[Any]) -> None:
        self.events: list[dict[str, Any]] = []
        self.provider = fake("ollama", script)
        self.router = make_router(store, {"ollama": self.provider})
        self.orch = Orchestrator(store, self.router, self._emit)
        self.auto: str | None = None  # auto-answer approvals: allow | deny | allow+remember

    async def _emit(self, m: dict[str, Any]) -> None:
        self.events.append(m)
        if m["type"] == "approval.request" and self.auto:
            self.orch.resolve_approval(m["approval_id"], self.auto.split("+")[0], "+remember" in self.auto)

    async def run(self, text: str = "go") -> None:
        await self.router.probe()
        await self.orch.submit(text)
        await self.orch._task

    def types(self) -> list[str]:
        return [e["type"] for e in self.events]


def call(name: str, value: str = "v", cid: str = "c1") -> ToolCall:
    return ToolCall(cid, name, {"value": value})


async def test_plain_answer_streams_and_persists(store, test_tools):
    h = Harness(store, [Completion("Hello.")])
    await h.run("hi")
    assert h.types()[:4] == ["user.echo", "state", "assistant.start", "assistant.delta"]
    assert h.events[-1] == {"type": "state", "state": "idle", "detail": ""}
    hist = store.get_messages(store.current_conversation(), 10)
    assert [m["role"] for m in hist] == ["user", "assistant"]


async def test_t1_runs_without_approval(store, test_tools):
    h = Harness(store, [Completion("", [call("t_soft")], "tool_use"), Completion("Done.")])
    await h.run()
    assert test_tools == ["t_soft:v"] and "approval.request" not in h.types()
    assert store.query_audit()[0][0]["decided_by"] == "policy"


async def test_t2_denied_does_not_run(store, test_tools):
    h = Harness(store, [Completion("", [call("t_hard")], "tool_use"), Completion("Okay.")])
    h.auto = "deny"
    await h.run()
    assert test_tools == []
    audit = store.query_audit()[0][0]
    assert audit["status"] == "denied" and audit["decided_by"] == "user"
    tool_msg = h.provider.seen[1][-1]
    assert tool_msg["role"] == "tool" and tool_msg["is_error"]


async def test_t2_allowed_runs_and_session_grant_skips_next_prompt(store, test_tools):
    h = Harness(store, [Completion("", [call("t_hard", "a")], "tool_use"), Completion("ok"),
                        Completion("", [call("t_hard", "b", "c2")], "tool_use"), Completion("ok")])
    h.auto = "allow+remember"
    await h.run()
    await h.run("again")
    assert test_tools == ["t_hard:a", "t_hard:b"]
    assert h.types().count("approval.request") == 1


async def test_t3_never_remembered(store, test_tools):
    h = Harness(store, [Completion("", [call("t_nuke", "a")], "tool_use"), Completion("ok"),
                        Completion("", [call("t_nuke", "b", "c2")], "tool_use"), Completion("ok")])
    h.auto = "allow+remember"
    await h.run()
    await h.run("again")
    reqs = [e for e in h.events if e["type"] == "approval.request"]
    assert len(reqs) == 2 and not reqs[0]["can_remember"]


async def test_taint_escalates_t1_to_approval(store, test_tools):
    """SEC-1: after reading untrusted content, even a reversible action needs approval."""
    h = Harness(store, [Completion("", [call("t_read")], "tool_use"),
                        Completion("", [call("t_soft", "x", "c2")], "tool_use"), Completion("ok")])
    h.auto = "deny"
    await h.run()
    assert test_tools == ["t_read:v"]
    assert h.types().count("approval.request") == 1
    wrapped = json.loads(h.provider.seen[1][-1]["content"])
    assert "untrusted_content" in wrapped


async def test_invalid_args_and_unknown_tool_are_reported_to_model(store, test_tools):
    h = Harness(store, [Completion("", [ToolCall("c1", "t_soft", {"nope": 1}), ToolCall("c2", "ghost", {})], "tool_use"),
                        Completion("sorry")])
    await h.run()
    results = [m for m in h.provider.seen[1] if m["role"] == "tool"]
    assert len(results) == 2 and all(r["is_error"] for r in results) and test_tools == []


async def test_step_limit_stops_runaway(store, test_tools):
    store.update_settings({"max_steps": 2})
    h = Harness(store, [Completion("", [call("t_soft", cid=f"c{i}")], "tool_use") for i in range(5)])
    await h.run()
    assert h.provider.calls == 2
    assert any(e["type"] == "notice" and "Stopped after 2" in e["message"] for e in h.events)


async def test_kill_switch_cancels_pending_approval(store, test_tools):
    h = Harness(store, [Completion("", [call("t_hard")], "tool_use"), Completion("never")])
    await h.router.probe()
    await h.orch.submit("go")
    for _ in range(100):
        if h.orch.pending_approvals():
            break
        await asyncio.sleep(0.01)
    assert h.orch.state == "awaiting_approval"
    assert await h.orch.cancel("Stopped by kill switch.")
    assert test_tools == [] and h.orch.state == "idle" and not h.orch.pending_approvals()


async def test_provider_failure_sets_error_state_not_crash(store, test_tools):
    h = Harness(store, [ProviderError("down", retryable=True, kind="unreachable")])
    await h.run()
    assert h.orch.state == "error" and any(e["type"] == "error" for e in h.events)
    assert not h.orch.busy


def test_trim_drops_orphaned_tool_calls():
    hist = [
        {"role": "tool", "tool_call_id": "old", "name": "x", "content": "{}"},
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "thinking", "tool_calls": [{"id": "c1", "name": "x", "args": {}}]},
        {"role": "user", "content": "b"},
    ]
    out = _trim_to_valid(hist)
    assert [m["role"] for m in out] == ["user", "assistant", "user"] and "tool_calls" not in out[1]


# -- real tool registry ---------------------------------------------------------------------------

def test_every_tool_schema_is_provider_safe():
    import re
    for schema in tools.schemas():
        assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", schema["name"])
        assert schema["description"] and schema["parameters"]["type"] == "object"


def test_real_tool_policy_classification():
    reg = tools.REGISTRY
    sh = reg["shell_run"]
    assert sh.classify_call(sh.args_model(command="Get-Date"))[0] == Tier.T2
    assert sh.classify_call(sh.args_model(command="Remove-Item x"))[0] == Tier.T3
    with pytest.raises(tools.ToolError):
        sh.classify_call(sh.args_model(command="Start-Process x -Verb RunAs"))
    with pytest.raises(tools.ToolError):
        reg["web_open"].classify_call(reg["web_open"].args_model(url="file:///c:/windows"))
    fo = reg["files_open"]
    assert fo.classify_call(fo.args_model(path=r"E:\nope\a.pdf"))[0] == Tier.T1
    with pytest.raises(tools.ToolError):
        fo.classify_call(fo.args_model(path=r"~\.ssh\id_rsa"))
    assert reg["files_read"].taints and reg["clipboard_read"].taints and sh.taints


async def test_files_roundtrip_and_overwrite_guard(tmp_path):
    reg = tools.REGISTRY
    target = tmp_path / "note.txt"
    w = reg["files_write"]
    args = w.args_model(path=str(target), content="hello")
    assert w.classify_call(args)[0] == Tier.T2
    await w.handler(args)
    assert (await reg["files_read"].handler(reg["files_read"].args_model(path=str(target))))["text"] == "hello"
    with pytest.raises(tools.ToolError):
        w.classify_call(args)
    assert w.classify_call(w.args_model(path=str(target), content="x", overwrite=True))[0] == Tier.T3
    found = await reg["files_search"].handler(reg["files_search"].args_model(query="note*", root=str(tmp_path)))
    assert found["matches"][0]["path"] == str(target)


async def test_system_status_reports_real_values():
    status = await tools.REGISTRY["system_status"].handler(tools.REGISTRY["system_status"].args_model())
    assert 0 <= status["ram_percent"] <= 100 and status["ram_total_gb"] > 0 and status["disks"]


# -- gateway --------------------------------------------------------------------------------------

@pytest.fixture
def client(store):
    hub = Hub("t" * 32, store=store, router=make_router(store, {"ollama": fake("ollama", [Completion("Hi there.")])}))
    with TestClient(create_app(hub), base_url=f"http://127.0.0.1:{PORT}") as c:
        yield c


GOOD = {"origin": f"http://127.0.0.1:{PORT}", "host": f"127.0.0.1:{PORT}"}


def test_ws_rejects_foreign_origin(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws", headers={**GOOD, "origin": "https://evil.example"}):
            pass
    assert exc.value.code == 4403


def test_ws_rejects_rebound_host(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={**GOOD, "host": "evil.example"}):
            pass


def test_ws_rejects_bad_token(client):
    with client.websocket_connect("/ws", headers=GOOD) as ws:
        ws.send_json({"type": "hello", "token": "x" * 32})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4401


def test_ws_full_conversation(client):
    with client.websocket_connect("/ws", headers=GOOD) as ws:
        ws.send_json({"type": "hello", "token": "t" * 32})
        ready = ws.receive_json()
        assert ready["type"] == "ready" and ready["settings"]["privacy_mode"] == "hybrid"
        ws.send_json({"type": "bogus"})
        assert ws.receive_json()["code"] == "protocol"
        ws.send_json({"type": "settings.set", "patch": {"evil": 1}})
        seen = []
        while True:
            m = ws.receive_json()
            seen.append(m["type"])
            if m["type"] == "error":
                assert m["code"] == "settings"
                break
        ws.send_json({"type": "user.message", "text": "hello"})
        text = ""
        while True:
            m = ws.receive_json()
            assert m["type"] in protocol.SERVER_TYPES
            if m["type"] == "assistant.delta":
                text += m["text"]
            if m["type"] == "state" and m["state"] == "idle":
                break
        assert text == "Hi there."
        ws.send_json({"type": "audit.query", "limit": 10})
        while (m := ws.receive_json())["type"] != "audit.page":
            pass
        assert m["items"] == []


def test_settings_patch_validation():
    assert validate_settings_patch({"privacy_mode": "local"}) == {"privacy_mode": "local"}
    for bad in ({"privacy_mode": "x"}, {"routing": {"order": ["Not A Name"]}}, {"max_steps": 999},
                {"voice": {"quiet_ms": 1}}, {"models": {"Bad-Id": "m"}}):
        with pytest.raises(ValueError):
            validate_settings_patch(bad)


def test_protocol_parity_with_typescript():
    """protocol.py and ui/src/protocol.ts must declare the same message types."""
    import re

    from jarvis.config import ROOT
    ts = (ROOT / "ui" / "src" / "protocol.ts").read_text(encoding="utf-8")
    declared = set(re.findall(r"type: '([a-z_.]+)'", ts))
    assert protocol.CLIENT_TYPES | protocol.SERVER_TYPES == declared


async def test_denial_stops_further_tool_attempts(store, test_tools):
    """After the user says no, the model gets a final turn with no tools instead of trying workarounds."""
    h = Harness(store, [Completion("", [call("t_hard", "a")], "tool_use"), Completion("You declined, so I stopped.")])
    h.auto = "deny"
    await h.run()
    assert test_tools == [] and h.provider.calls == 2
    assert h.types().count("approval.request") == 1
    assert h.provider.tools_offered[0] > 0 and h.provider.tools_offered[1] == 0


# ---- self-healing ---------------------------------------------------------------------------------


def test_fault_log_groups_redacts_and_reopens(tmp_path):
    import logging

    from jarvis import selfheal

    faults = selfheal.FaultLog(tmp_path / "faults.json")
    handler = selfheal.FaultHandler(faults)
    logger = logging.getLogger("jarvis.test_selfheal")
    logger.addHandler(handler)
    try:
        for _ in range(3):
            try:
                raise KeyError("api_key=sk-" + "a" * 40)
            except KeyError:
                logger.exception("lookup failed")
        logger.error("plain error without a traceback")
    finally:
        logger.removeHandler(handler)
    snap = faults.snapshot()["faults"]
    assert len(snap) == 2                                     # three identical failures are one fault
    grouped = next(f for f in snap if f["kind"] == "KeyError")
    assert grouped["count"] == 3 and "sk-" + "a" * 40 not in json.dumps(grouped)   # the secret never reaches disk
    assert "sk-" + "a" * 40 not in (tmp_path / "faults.json").read_text(encoding="utf-8")
    faults.update(grouped["id"], status="dismissed")
    again = faults.record("KeyError", "x", "")               # a different signature (no traceback)
    assert again["id"] != grouped["id"]
    reloaded = selfheal.FaultLog(tmp_path / "faults.json")
    assert {f["id"] for f in reloaded.snapshot()["faults"]} == {f["id"] for f in faults.snapshot()["faults"]}


def test_repair_rules_are_enforced_outside_the_model(tmp_path):
    from jarvis import selfheal

    pkg = tmp_path / "jarvis"
    (pkg / "tools").mkdir(parents=True)
    (pkg / "wake.py").write_text("def f():\n    return 1 / 0\n", encoding="utf-8")
    for protected in ("policy.py", "secrets.py", "selfheal.py", "config.py"):
        (pkg / protected).write_text("ALLOW = False\n", encoding="utf-8")
    good = [{"file": "wake.py", "search": "return 1 / 0", "replace": "return 0"}]
    assert selfheal.check_edits(good, pkg) == good
    assert selfheal.check_edits([{**good[0], "file": "src/jarvis/wake.py"}], pkg)[0]["file"] == "wake.py"
    bad = [
        [{"file": "policy.py", "search": "ALLOW = False", "replace": "ALLOW = True"}],      # safety code
        [{"file": "selfheal.py", "search": "ALLOW = False", "replace": "ALLOW = True"}],    # itself
        [{"file": "../outside.py", "search": "a", "replace": "b"}],                          # path escape
        [{"file": "wake.py", "search": "not there", "replace": "x"}],                        # stale text
        [{"file": "wake.py", "search": "return 1 / 0", "replace": "x\n" * 200}],             # too large
        [{"file": "notes.txt", "search": "a", "replace": "b"}],                              # not Python
        [],
        good * 5,
    ]
    for edits in bad:
        with pytest.raises(ValueError):
            selfheal.check_edits(edits, pkg)
    diff = selfheal.apply_edits(good, pkg)
    assert "-    return 1 / 0" in diff and "+    return 0" in diff
    assert selfheal.parse_reply('```json\n{"fixable": true, "edits": []}\n```')["fixable"] is True


def test_repair_is_proposed_checked_applied_only_on_request_and_rolled_back(tmp_path, monkeypatch):
    """The whole loop on a throw-away copy of a package: fault → model patch → checked on a copy → apply → roll back."""
    import asyncio

    from jarvis import selfheal

    pkg = tmp_path / "pkg" / "jarvis"
    pkg.mkdir(parents=True)
    broken = "def ratio(a, b):\n    return a / b\n"
    (pkg / "mathy.py").write_text(broken, encoding="utf-8")
    monkeypatch.setattr(selfheal, "PACKAGE_DIR", pkg)
    monkeypatch.setattr(selfheal, "HEAL_DIR", tmp_path / "heal")
    checked: list[list[dict]] = []

    def fake_verify(edits, work, run_tests=True):
        checked.append(edits)
        copy = work / "jarvis"
        copy.mkdir(parents=True)
        (copy / "mathy.py").write_text(broken, encoding="utf-8")
        diff = selfheal.apply_edits(edits, copy)
        compile((copy / "mathy.py").read_text(encoding="utf-8"), "mathy.py", "exec")   # it really must compile
        return True, "Compiles (test double).", diff

    monkeypatch.setattr(selfheal, "verify", fake_verify)
    faults = selfheal.FaultLog(tmp_path / "faults.json")
    tb = f'Traceback (most recent call last):\n  File "{pkg / "mathy.py"}", line 2, in ratio\nZeroDivisionError: division by zero'
    fault = faults.record("ZeroDivisionError", "division by zero", tb)
    assert (fault["file"], fault["line"]) == ("mathy.py", 2)
    asked: list[str] = []

    async def model(system, user):
        asked.append(user)
        return json.dumps({"diagnosis": "b can be zero.", "fixable": True, "edits": [
            {"file": "mathy.py", "search": "    return a / b", "replace": "    return a / b if b else 0.0"}]})

    healer = selfheal.Healer(faults, model)
    proposed = asyncio.run(healer.propose(fault["id"]))
    assert proposed["status"] == "proposed" and "+    return a / b if b else 0.0" in proposed["diff"]
    assert "division by zero" in asked[0] and "def ratio" in asked[0] and len(checked) == 1
    assert (pkg / "mathy.py").read_text(encoding="utf-8") == broken          # proposing changes nothing real
    assert "edits" not in faults.snapshot()["faults"][0]                     # the raw patch is not sent to the display

    applied = healer.apply(fault["id"])
    assert applied["status"] == "applied" and "if b else 0.0" in (pkg / "mathy.py").read_text(encoding="utf-8")
    assert healer.apply(fault["id"])["status"] == "applied"                  # applying twice is harmless
    rolled = healer.rollback(fault["id"])
    assert rolled["status"] == "rolled_back" and (pkg / "mathy.py").read_text(encoding="utf-8") == broken

    # A model that tries to touch safety code, or says it is not a code problem, yields no repair.
    (pkg / "policy.py").write_text("ALLOW = False\n", encoding="utf-8")

    async def hostile(system, user):
        return json.dumps({"diagnosis": "x", "fixable": True, "edits": [
            {"file": "policy.py", "search": "ALLOW = False", "replace": "ALLOW = True"}]})

    refused = asyncio.run(selfheal.Healer(faults, hostile).propose(fault["id"]))
    assert refused["status"] == "open" and "safety" in refused["note"]
    assert (pkg / "policy.py").read_text(encoding="utf-8") == "ALLOW = False\n"

    async def environment(system, user):
        return json.dumps({"diagnosis": "The network was down.", "fixable": False, "edits": []})

    left = asyncio.run(selfheal.Healer(faults, environment).propose(fault["id"]))
    assert left["status"] == "open" and left["diagnosis"] == "The network was down."


def test_failed_services_are_restarted_with_backoff(tmp_path, monkeypatch):
    from jarvis import selfheal

    faults = selfheal.FaultLog(tmp_path / "faults.json")
    healer = selfheal.Healer(faults)
    monkeypatch.setattr(selfheal, "RECOVER_BACKOFF_S", (0.0, 0.0, 0.0))
    restarts: list[int] = []
    assert not healer.recover("Wake word listener", False, lambda: restarts.append(1))   # healthy: nothing to do
    for _ in range(6):
        healer.recover("Wake word listener", True, lambda: restarts.append(1), "device lost")
    assert len(restarts) == 3                                  # gives up after three attempts rather than loop forever
    recoveries = faults.snapshot()["repairs"]
    assert len(recoveries) == 3 and all(r["ok"] for r in recoveries) and "device lost" in recoveries[0]["detail"]

    def boom():
        raise RuntimeError("still broken")

    other = selfheal.Healer(faults)
    assert other.recover("Browser", True, boom) and faults.snapshot()["repairs"][0]["ok"] is False


# ---- speech engines -------------------------------------------------------------------------------


class _FakeHttp:
    def __init__(self, status=200, payload=None, content=b""):
        self.calls: list[tuple[str, dict]] = []
        self.status, self.payload, self.body = status, payload or {}, content

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        outer = self

        class Response:
            status_code = outer.status
            text = json.dumps(outer.payload)
            content = outer.body

            def json(self):
                return outer.payload

        return Response()


def test_cloud_speech_engines_build_the_right_requests_and_respect_privacy(monkeypatch):
    import wave as wave_mod

    np = pytest.importorskip("numpy")
    from jarvis import secrets as jsecrets
    from jarvis import speech

    keys = {"groq": "gsk_" + "k" * 30, "deepgram": "dg_" + "k" * 30, "elevenlabs": "el_" + "k" * 30}
    monkeypatch.setattr(jsecrets, "get_key", lambda name: keys.get(name))
    monkeypatch.setattr(jsecrets, "has_key", lambda name: name in keys)
    settings = {"privacy_mode": "hybrid", "custom_providers": {}}
    pcm = (np.sin(np.arange(16000) / 20) * 8000).astype(np.int16)

    http = _FakeHttp(payload={"text": " open the calculator "})
    assert speech.transcribe(pcm, "groq", settings, client=http) == "open the calculator"
    url, sent = http.calls[0]
    assert url == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert sent["headers"]["Authorization"] == "Bearer " + keys["groq"] and sent["data"]["model"] == "whisper-large-v3-turbo"
    with wave_mod.open(__import__("io").BytesIO(sent["files"]["file"][1])) as w:
        assert (w.getframerate(), w.getnchannels(), w.getnframes()) == (16000, 1, 16000)   # a real WAV was sent

    dg = _FakeHttp(payload={"results": {"channels": [{"alternatives": [{"transcript": "hello"}]}]}})
    assert speech.transcribe(pcm, "deepgram", settings, client=dg) == "hello"
    assert dg.calls[0][1]["headers"]["Authorization"].startswith("Token ")

    for bad in ("local", "nvidia", "nonsense"):
        with pytest.raises(RuntimeError):
            speech.transcribe(pcm, bad, settings, client=_FakeHttp())
    with pytest.raises(RuntimeError, match="local-only"):
        speech.transcribe(pcm, "groq", {**settings, "privacy_mode": "local"}, client=_FakeHttp())   # never leaves the machine
    with pytest.raises(RuntimeError, match="no key"):
        monkeypatch.setattr(jsecrets, "get_key", lambda name: None)
        speech.transcribe(pcm, "deepgram", settings, client=_FakeHttp())
    monkeypatch.setattr(jsecrets, "get_key", lambda name: keys.get(name))
    failing = _FakeHttp(status=401, payload={"error": "bad key " + keys["groq"]})
    with pytest.raises(RuntimeError) as err:
        speech.transcribe(pcm, "groq", settings, client=failing)
    assert "401" in str(err.value)

    voice = _FakeHttp(content=b"\x01\x00" * 800)
    wav = speech.synthesize("Hello sir", "elevenlabs", settings, client=voice)
    assert wav[:4] == b"RIFF" and "text-to-speech/" in voice.calls[0][0]            # raw audio wrapped as a playable WAV

    cat = speech.catalogue(settings)
    by_id = {e["id"]: e for e in cat["stt"]}
    assert by_id["local"]["ready"] and by_id["groq"]["ready"] and not by_id["openai"]["ready"]
    assert not by_id["nvidia"]["selectable"]
    assert not {e["id"]: e for e in speech.catalogue({**settings, "privacy_mode": "local"})["stt"]}["groq"]["ready"]

    spoken: list[str] = []
    speech.say("hi", {"voice": {"tts_engine": "system"}}, lambda: spoken.append("windows"))
    assert spoken == ["windows"]                                                    # the default stays local


def test_voice_settings_accept_engines_and_reject_junk():
    assert validate_settings_patch({"voice": {"stt_engine": "groq", "tts_engine": "system", "tts_cloud_voice": "onyx"}})
    for bad in ({"voice": {"stt_engine": "Not An Id"}}, {"voice": {"tts_cloud_voice": "x" * 90}},
                {"voice": {"stt_cloud_model": "a b; rm"}}):
        with pytest.raises(ValueError):
            validate_settings_patch(bad)
