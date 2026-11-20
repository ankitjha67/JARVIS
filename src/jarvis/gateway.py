"""FastAPI gateway: loopback WebSocket with token + Origin checks, static HUD, telemetry push (SEC-2)."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
import tempfile
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import psutil
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from . import __version__, ack, mcp_oauth, protocol, secrets, selfheal, speech, telemetry, voice_training
from . import wake as wake_mod
from .agents import AgentManager, PlanManager
from .background_tasks import BackgroundTaskEngine, TaskRunner
from .clipboard import ClipboardHistory
from .confirmation import ConfirmationGate, UndoStack
from .config import (
    DATA_DIR,
    DB_PATH,
    DEV,
    DEV_ORIGINS,
    HOST,
    NAME_RE,
    PORT,
    PROTOCOL_VERSION,
    PROVIDER_BY_ID,
    RESERVED_SECRETS,
    UI_DIST,
    ensure_dirs,
)
from .file_index import FileIndex
from .memory import MemoryStore
from .model_ladder import ModelLadder
from .outcome_verify import OutcomeVerifier
from .scheduler import Scheduler
from .skills import SkillRegistry
from .mcp_client import McpManager
from .orchestrator import Orchestrator
from .policy import normalise_web_url
from .protocol import msg
from .providers import Router
from .providers.base import Completion
from .store import Store, now_iso
from .tools import browser as browser_tools
from .tools import context as tool_context
from .tools.registry import REGISTRY
from .wake import WakeListener

log = logging.getLogger(__name__)

HOTKEYS = {"summon": "Ctrl+Alt+J", "kill": "Ctrl+Alt+Backspace"}
_SETTINGS_KEYS = {"privacy_mode", "routing", "models", "voice", "max_steps", "autonomy", "email", "mcp_servers",
                  "custom_providers", "integrations", "theme"}


def allowed_origins(port: int = PORT) -> set[str]:
    origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    if DEV:
        origins.update(DEV_ORIGINS)
    return origins


def allowed_hosts(port: int = PORT) -> set[str]:
    return {f"127.0.0.1:{port}", f"localhost:{port}"}


def validate_settings_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Accept only known keys with sane shapes; the HUD is not trusted to write arbitrary settings."""
    clean: dict[str, Any] = {}
    for key, value in patch.items():
        if key not in _SETTINGS_KEYS:
            raise ValueError(f"unknown setting: {key}")
        if key == "privacy_mode":
            if value not in ("local", "hybrid", "cloud"):
                raise ValueError("privacy_mode must be local, hybrid or cloud")
        elif key == "routing":
            if not isinstance(value, dict) or not set(value) <= {"mode", "order", "manual_provider"}:
                raise ValueError("invalid routing settings")
            if "mode" in value and value["mode"] not in ("auto", "manual"):
                raise ValueError("routing.mode must be auto or manual")
            if "order" in value and not (isinstance(value["order"], list)
                                         and all(isinstance(p, str) and NAME_RE.match(p) for p in value["order"])):
                raise ValueError("routing.order contains an unknown provider")
            manual = value.get("manual_provider")
            if manual is not None and not (isinstance(manual, str) and NAME_RE.match(manual)):
                raise ValueError("unknown provider")
        elif key == "models":
            if not isinstance(value, dict) or not all(
                isinstance(k, str) and NAME_RE.match(k) and (v is None or (isinstance(v, str) and 0 < len(v) <= 200))
                for k, v in value.items()
            ):
                raise ValueError("invalid model selection")
        elif key == "voice":
            if not isinstance(value, dict) or not set(value) <= {"speak", "auto_send", "quiet_ms", "wake_word",
                                                                  "wake_sensitivity", "wake_keys", "wake_ack", "wake_reply",
                                                                  "wake_aliases", "stt_model", "corrections",
                                                                  "trained_at", "tts_voice", "wake_listen", "stt_engine",
                                                                  "stt_cloud_model", "tts_engine", "tts_cloud_model",
                                                                  "tts_cloud_voice",
                                                                  "listen_window_s", "listen_pause_s"}:
                raise ValueError("invalid voice settings")
            if "wake_sensitivity" in value and not (isinstance(value["wake_sensitivity"], int | float)
                                                    and 0.1 <= value["wake_sensitivity"] <= 0.85):
                raise ValueError("wake_sensitivity must be 0.1–0.85")
            if "wake_keys" in value and not (isinstance(value["wake_keys"], str)
                                             and re.fullmatch(r"[A-Za-z0-9+ ]{0,40}", value["wake_keys"])):
                raise ValueError("wake_keys must be a key combination such as ctrl+win+space")
            for name in ("stt_engine", "tts_engine"):
                if name in value and not (isinstance(value[name], str) and (value[name] in ("local", "system")
                                                                              or NAME_RE.match(value[name]))):
                    raise ValueError(f"voice.{name} must be an engine id")
            for name in ("stt_cloud_model", "tts_cloud_model", "tts_cloud_voice"):
                if name in value and not (isinstance(value[name], str) and re.fullmatch(r"[\w./:\-]{0,80}", value[name])):
                    raise ValueError(f"voice.{name} must be a short model or voice id")
            for flag in ("wake_word", "wake_ack", "wake_listen"):
                if flag in value and not isinstance(value[flag], bool):
                    raise ValueError(f"voice.{flag} must be true or false")
            if "listen_window_s" in value and not (isinstance(value["listen_window_s"], int | float)
                                                   and 3 <= value["listen_window_s"] <= 30):
                raise ValueError("listen_window_s must be 3–30 seconds")
            if "listen_pause_s" in value and not (isinstance(value["listen_pause_s"], int | float)
                                                  and (value["listen_pause_s"] == 0 or 1 <= value["listen_pause_s"] <= 10)):
                raise ValueError("listen_pause_s must be 0 (use the full time) or 1–10 seconds")
            if "tts_voice" in value and not (isinstance(value["tts_voice"], str)
                                             and re.fullmatch(r"[\w ()\-.,]{0,80}", value["tts_voice"])):
                raise ValueError("invalid voice name")
            if "stt_model" in value and value["stt_model"] not in ("tiny.en", "base.en", "small.en"):
                raise ValueError("unknown speech model")
            if "corrections" in value and not (isinstance(value["corrections"], dict) and len(value["corrections"]) <= 150
                                               and all(isinstance(k, str) and isinstance(v, str)
                                                       and re.fullmatch(r"[a-z']{2,30}", k) and re.fullmatch(r"[a-z']{2,30}", v)
                                                       for k, v in value["corrections"].items())):
                raise ValueError("corrections must map lowercase words to lowercase words")
            if "trained_at" in value and not (value["trained_at"] is None or isinstance(value["trained_at"], str)):
                raise ValueError("invalid trained_at")
            if "wake_reply" in value and not (isinstance(value["wake_reply"], str)
                                              and re.fullmatch(r"[\w ,.!?'-]{0,60}", value["wake_reply"])):
                raise ValueError("wake_reply must be a short phrase")
            if "wake_aliases" in value and not (isinstance(value["wake_aliases"], list) and len(value["wake_aliases"]) <= 12
                                                and all(isinstance(a, str) and re.fullmatch(r"[a-z']+( [a-z']+){0,2}", a)
                                                        and 3 <= len(a) <= 40 for a in value["wake_aliases"])):
                raise ValueError("wake_aliases must be up to 12 short lowercase phrases")
            if "quiet_ms" in value and not (isinstance(value["quiet_ms"], int) and 400 <= value["quiet_ms"] <= 5000):
                raise ValueError("quiet_ms must be 400–5000")
            for flag in ("speak", "auto_send"):
                if flag in value and not isinstance(value[flag], bool):
                    raise ValueError(f"voice.{flag} must be true or false")
        elif key == "autonomy":
            if value not in ("guarded", "trusted"):
                raise ValueError("autonomy must be guarded or trusted")
        elif key == "email":
            if not isinstance(value, dict) or not set(value) <= {"address", "imap_host", "imap_port", "smtp_host", "smtp_port"}:
                raise ValueError("invalid email settings")
            for field in ("address", "imap_host", "smtp_host"):
                if field in value and not (isinstance(value[field], str) and len(value[field]) <= 200
                                           and not any(c.isspace() for c in value[field])):
                    raise ValueError(f"email.{field} is not valid")
            for field in ("imap_port", "smtp_port"):
                if field in value and not (isinstance(value[field], int) and 1 <= value[field] <= 65535):
                    raise ValueError(f"email.{field} must be a port number")
        elif key == "mcp_servers":
            if not isinstance(value, dict) or len(value) > 20:
                raise ValueError("invalid MCP server list")
            for name, cfg in value.items():
                if cfg is None:
                    continue  # null removes a server
                if not (isinstance(name, str) and name.isidentifier() and len(name) <= 32):
                    raise ValueError("MCP server names must be short identifiers (letters, digits, underscore)")
                if not isinstance(cfg, dict) or not (("command" in cfg) ^ ("url" in cfg)):
                    raise ValueError(f"MCP server {name} needs either a command or a url")
                if "url" in cfg and not (isinstance(cfg["url"], str) and cfg["url"].startswith(("http://", "https://"))):
                    raise ValueError(f"MCP server {name}: url must be http(s)")
                for bag in ("headers", "env"):
                    extra = cfg.get(bag, {})
                    if not (isinstance(extra, dict) and len(extra) <= 20 and all(
                            isinstance(k, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", k)
                            and isinstance(v, str) and len(v) <= 2000 and "\n" not in v and "\r" not in v
                            for k, v in extra.items())):
                        raise ValueError(f"MCP server {name}: invalid {bag}")
                if "command" in cfg and not (isinstance(cfg["command"], str) and cfg["command"].strip()
                                             and isinstance(cfg.get("args", []), list)
                                             and all(isinstance(a, str) for a in cfg.get("args", []))
                                             and isinstance(cfg.get("env", {}), dict)):
                    raise ValueError(f"MCP server {name}: invalid command definition")
        elif key == "custom_providers":
            if not isinstance(value, dict) or len(value) > 20:
                raise ValueError("invalid custom provider list")
            for pid, cfg in value.items():
                if cfg is None:
                    continue
                if not (isinstance(pid, str) and NAME_RE.match(pid)) or pid in PROVIDER_BY_ID or pid in RESERVED_SECRETS:
                    raise ValueError("provider ids use lowercase letters, digits and underscore, and must not reuse a built-in name")
                if not isinstance(cfg, dict) or not set(cfg) <= {"label", "base_url", "needs_key"}:
                    raise ValueError(f"custom provider {pid}: invalid definition")
                base = cfg.get("base_url")
                if not (isinstance(base, str) and normalise_web_url(base) and len(base) <= 300):
                    raise ValueError(f"custom provider {pid}: base URL must be a plain http(s) address")
        elif key == "integrations":
            if not isinstance(value, dict) or not set(value) <= {"homeassistant", "telegram", "file_index", "clipboard",
                                                                   "screen_history"}:
                raise ValueError("invalid integrations settings")
        elif key == "max_steps":
            if not (isinstance(value, int) and 1 <= value <= 40):
                raise ValueError("max_steps must be 1–40")
        elif key == "theme":
            if value not in ("ion", "holographic", "sovereign"):
                raise ValueError("theme must be ion, holographic or sovereign")
        clean[key] = value
    return clean


class _HudFiles(StaticFiles):
    """index.html must never be cached, or a rebuilt HUD keeps loading the previous hashed assets."""

    async def get_response(self, path: str, scope: Any) -> Any:
        response = await super().get_response(path, scope)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-store"
        return response


class Hub:
    """Owns the long-lived services and the set of authenticated HUD connections."""

    def __init__(self, token: str, store: Store | None = None, router: Router | None = None,
                 window_control: Callable[[str], None] | None = None,
                 faults: selfheal.FaultLog | None = None) -> None:
        ensure_dirs()
        self.token = token
        self.store = store or Store(DB_PATH)
        self.router = router or Router(
            self.store.get_settings, bench_store=lambda patch: self.store.kv("model_bench", patch))
        self.router.on_change = self.refresh_providers
        self.orchestrator = Orchestrator(self.store, self.router, self.broadcast)
        tool_context.bind_settings(self.store.get_settings)
        tool_context._hub = self  # type: ignore[attr-defined]

        self.memory = MemoryStore(DATA_DIR / "memory.db")
        self.scheduler = Scheduler(DATA_DIR / "scheduler.db")
        self.file_index = FileIndex(DATA_DIR / "file_index.db")
        self.clipboard = ClipboardHistory(DATA_DIR / "clipboard.db")
        self.skills = SkillRegistry(DATA_DIR / "skills")
        self.agents = AgentManager()
        self.plans = PlanManager()

        # Mark-LVII systems
        self.bg_engine = BackgroundTaskEngine(DATA_DIR / "bg_tasks.db")
        self.bg_runner = TaskRunner(self.bg_engine, on_update=self._on_bg_update)
        self.undo_stack = UndoStack()
        self.confirm_gate = ConfirmationGate()
        self.ladder = ModelLadder()
        self.verifier = OutcomeVerifier()

        self.ha = None
        self.telegram = None

        self.mcp = McpManager()
        self.mcp.on_change = self.broadcast_integrations
        self._trainer: voice_training.Trainer | None = None
        self._speak_reply = False  # the request in flight was spoken, so its answer should be spoken too
        self._reply_text = ""
        self._ui_speaks = False
        self._wake_ack: tuple[bool, str, str] = (True, "Yes?", "")
        self._dictation_keys = ""
        self._voice_settings: dict[str, Any] = {}
        self._user_messages = 0  # how many requests have arrived (to tell whether the dictation app delivered one)
        self.summon: Callable[[], None] | None = None  # set by the entry point: brings the window forward
        self.wake = WakeListener(self._on_wake, lambda: self.call_soon(self.broadcast_integrations))
        self.wake.on_level = self._on_level
        self.window_control = window_control
        # A hub given its own store (tests) must not write faults into the real data folder.
        self.faults = faults or selfheal.FaultLog(None if store is None else Path(tempfile.mkdtemp()) / "faults.json")
        self.faults.on_change = lambda: self.call_soon(self.broadcast_selfheal)
        self.healer = selfheal.Healer(self.faults, self._ask_model)
        self._fault_handler = selfheal.FaultHandler(self.faults)
        self.clients: set[WebSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._tasks = [asyncio.create_task(self._telemetry_loop()), asyncio.create_task(self._probe_loop()),
                       asyncio.create_task(self._heal_loop())]
        logging.getLogger().addHandler(self._fault_handler)
        settings = await asyncio.to_thread(self.store.get_settings)
        await self.mcp.sync(settings.get("mcp_servers") or {})
        self._apply_wake(settings)

        self.clipboard.start()
        self._tasks.append(asyncio.create_task(self.scheduler.run_loop()))
        self._tasks.append(asyncio.create_task(self.bg_runner.run_loop()))
        self._init_routines()
        self._register_agent_runner()
        self._init_integrations(settings)

    def _register_agent_runner(self) -> None:
        from .agents import AgentTask
        from collections.abc import Awaitable

        async def _default_runner(task: AgentTask, report: Callable[[str, float], Awaitable[None]]) -> Any:
            await report("Starting sub-agent...", 0.1)
            try:
                result = await self.orchestrator.run_headless(task.goal)
                await report("Completed", 1.0)
                await self.broadcast(msg("agent.update", task_id=task.id, status="done",
                                         result=str(result)[:500]))
                return result
            except Exception as exc:
                await self.broadcast(msg("agent.update", task_id=task.id, status="failed",
                                         error=str(exc)[:300]))
                raise

        self.agents.register_runner("default", _default_runner)

    def _init_routines(self) -> None:
        from .routines import RoutineEngine
        self.routines = RoutineEngine(
            self.scheduler,
            run_model_fn=self.orchestrator.run_headless,
            broadcast_fn=self.broadcast,
        )
        self.routines.register_handlers()

    async def _on_bg_update(self, task_id: str, task: dict[str, Any]) -> None:
        await self.broadcast(msg("bg_task.update", task_id=task_id, status=task.get("status"),
                                  progress=task.get("progress", 0), name=task.get("name", "")))

    def _init_integrations(self, settings: dict[str, Any]) -> None:
        integrations = settings.get("integrations") or {}
        ha_cfg = integrations.get("homeassistant") or {}
        if ha_cfg.get("url") and ha_cfg.get("token"):
            try:
                from .homeassistant import HomeAssistant
                self.ha = HomeAssistant(ha_cfg["url"], ha_cfg["token"])
            except Exception:
                log.warning("Home Assistant init failed", exc_info=True)
        tg_cfg = integrations.get("telegram") or {}
        if tg_cfg.get("token"):
            try:
                from .telegram_bot import TelegramBot
                allowed = tg_cfg.get("allowed_users", [])
                self.telegram = TelegramBot(tg_cfg["token"], allowed, self._on_telegram)
                asyncio.create_task(self.telegram.start())
            except Exception:
                log.warning("Telegram bot init failed", exc_info=True)

    async def _on_telegram(self, text: str, chat_id: str) -> str:
        reply_parts: list[str] = []
        done_event = asyncio.Event()
        capture_id = id(done_event)

        self._telegram_captures = getattr(self, "_telegram_captures", {})
        self._telegram_captures[capture_id] = (reply_parts, done_event)

        _original_broadcast = Hub.broadcast

        async def _intercept_broadcast(hub_self: Any, message: dict[str, Any]) -> None:
            for cap_id, (parts, evt) in list(getattr(hub_self, "_telegram_captures", {}).items()):
                kind = message.get("type")
                if kind == "assistant.delta":
                    parts.append(str(message.get("text", "")))
                elif kind in ("assistant.done", "error"):
                    if kind == "error":
                        parts.append(f"Error: {message.get('message', 'unknown')}")
                    evt.set()
            await _original_broadcast(hub_self, message)

        self.broadcast = lambda message: _intercept_broadcast(self, message)  # type: ignore[assignment]
        try:
            await self.orchestrator.submit(text)
            await asyncio.wait_for(done_event.wait(), timeout=120)
        except asyncio.TimeoutError:
            reply_parts.append("(timed out)")
        finally:
            self._telegram_captures.pop(capture_id, None)
            if not self._telegram_captures:
                self.broadcast = _original_broadcast.__get__(self, Hub)  # type: ignore[assignment]
        return "".join(reply_parts) or "Done."

    async def stop(self) -> None:
        await self.orchestrator.cancel("Jarvis is shutting down.")
        logging.getLogger().removeHandler(self._fault_handler)
        self.bg_runner.stop()
        self.bg_engine.close()
        self.clipboard.stop()
        if self.telegram:
            await self.telegram.stop()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with suppress(asyncio.CancelledError):
                await t
        await asyncio.to_thread(self.wake.stop)
        await self.mcp.aclose()
        await browser_tools.shutdown()
        await self.router.aclose()
        self.store.close()

    def _track_reply(self, message: dict[str, Any]) -> None:
        """Speak the answer to a request that was made by voice (once, when the final turn completes)."""
        kind = message.get("type")
        if kind == "assistant.start":
            self._reply_text = ""
        elif kind == "assistant.delta":
            self._reply_text += str(message.get("text", ""))
        elif kind == "assistant.done":
            if self._speak_reply and not message.get("cancelled") and not message.get("has_tools"):
                self._speak_reply = False
                plain = re.sub(r"[*_`#>]+", "", self._reply_text).strip()
                if plain and not self._ui_speaks:  # the HUD speaks every reply itself when that setting is on
                    self._say(plain[:700])
        elif kind == "error" or (kind == "approval.request" and self._speak_reply):
            if kind == "error":
                self._speak_reply = False
            else:
                self._say("I need your approval to continue.")

    async def broadcast(self, message: dict[str, Any]) -> None:
        self._track_reply(message)
        if not self.clients:
            return
        data = json.dumps(message, default=str, ensure_ascii=False)
        for ws in list(self.clients):
            try:
                await ws.send_text(data)
            except (WebSocketDisconnect, RuntimeError, OSError):
                self.clients.discard(ws)

    def call_soon(self, coro_fn: Callable[[], Any]) -> None:
        """Schedule a coroutine from another thread (hotkeys)."""
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(coro_fn()))

    async def _telemetry_loop(self) -> None:
        await asyncio.to_thread(telemetry.sample)  # prime CPU and network deltas
        while True:
            await asyncio.sleep(1.0)
            if self.clients:
                try:
                    sample = await asyncio.to_thread(telemetry.sample)
                    sample["wake"] = self.wake.live()
                    await self.broadcast(msg("telemetry", **sample))
                except Exception:
                    log.exception("telemetry sample failed")

    async def _probe_loop(self) -> None:
        while True:
            await self.refresh_providers()
            await asyncio.sleep(30.0)

    async def refresh_providers(self, retest: bool = False) -> None:
        statuses = await self.router.probe(retest=retest)
        await self.broadcast(msg("providers", providers=[s.to_json() for s in statuses]))

    async def broadcast_selfheal(self) -> None:
        await self.broadcast(msg("selfheal", **self.faults.snapshot()))

    async def _ask_model(self, system: str, user: str) -> str:
        """One plain question to the best available model (used by self-repair). No tools are offered."""
        text = ""
        async for event in self.router.stream(system, [{"role": "user", "content": user}], [], "smart"):
            if isinstance(event, Completion):
                text = event.text or text
        return text

    async def _heal_loop(self) -> None:
        """Recovery: restart services that have stopped. Code is never changed from here."""
        while True:
            await asyncio.sleep(5.0)
            try:
                settings = await asyncio.to_thread(self.store.get_settings)
                wanted = bool((settings.get("voice") or {}).get("wake_word"))
                failed = wanted and self.wake.state == "error" and "microphone" not in self.wake.error.lower()
                await asyncio.to_thread(self.healer.recover, "Wake word listener", failed,
                                        lambda s=settings: self._apply_wake(s), self.wake.error)
            except Exception:
                log.debug("heal loop tick failed", exc_info=True)

    def _apply_wake(self, settings: dict[str, Any]) -> None:
        voice = settings.get("voice") or {}
        self.wake.apply(bool(voice.get("wake_word")), float(voice.get("wake_sensitivity", 0.5)),
                        voice.get("wake_aliases") or [], str(voice.get("stt_model") or "base.en"),
                        voice.get("corrections") or {})
        self._wake_ack = (bool(voice.get("wake_ack", True)), str(voice.get("wake_reply", "Yes?")),
                          str(voice.get("tts_voice") or ""))
        self._ui_speaks = bool(voice.get("speak"))
        # With a dictation shortcut (Wispr Flow) the window still runs: it times when to stop dictation, and its
        # own transcript is the fallback if the dictation app delivers nothing.
        self._dictation_keys = str(voice.get("wake_keys") or "").strip()
        self.wake.capture_enabled = bool(voice.get("wake_listen", True)) or bool(self._dictation_keys)
        self._voice_settings = settings
        engine = str(voice.get("stt_engine") or "local")
        if engine != "local" and settings.get("privacy_mode") != "local":
            model = str(voice.get("stt_cloud_model") or "")
            self.wake.request_stt = lambda pcm: speech.transcribe(pcm, engine, settings, model)
        else:
            self.wake.request_stt = None
        self.wake.listen_window = float(voice.get("listen_window_s", 10))
        self.wake.listen_pause = float(voice.get("listen_pause_s", 2.5))

    def _say(self, text: str) -> None:
        """Speak with the chosen voice engine; the Windows voice is the fallback."""
        speech.say(text, getattr(self, "_voice_settings", {}), lambda: ack.speak(text, self._wake_ack[2]))

    async def _finish_dictation(self, heard: str) -> None:
        """Stop the dictation app, then make sure the request arrives even if that app delivered nothing."""
        before = self._user_messages
        await self._after_wake(delay=0.0)  # the same shortcut ends hands-free dictation; the app then types the text
        await self.broadcast(msg("voice.session", state="ended", text=""))
        await asyncio.sleep(5.0)
        if heard and self._user_messages == before and not self.orchestrator.busy:
            log.info("the dictation app delivered nothing; using Jarvis's own transcript")
            await self.broadcast(msg("voice.session", state="final", text=heard[:2000]))

    def _on_wake(self, score: float, text: str, stage: str) -> None:
        """Runs on a listener thread. Stages are documented on wake.WakeHandler."""
        ack_on, reply, voice = getattr(self, "_wake_ack", (True, "Yes?", ""))
        listening_mode = self.wake.capture_enabled
        if stage == "early":
            if ack_on:
                ack.chime()  # immediate: the user knows they were heard before they finish speaking
            if self.summon:
                self.summon()
            self.call_soon(lambda: self.broadcast(msg("ui.focus", reason="wake", text="")))
            if listening_mode:
                window = self.wake.listen_window
                self.call_soon(lambda: self.broadcast(msg("voice.session", state="listening", text="", window_s=window)))
            if self._dictation_keys:
                self.call_soon(self._after_wake)  # start the dictation app (Wispr Flow hands-free)
            return
        if stage == "partial":
            self.call_soon(lambda: self.broadcast(msg("voice.session", state="listening", text=text[:400])))
            return
        if self._dictation_keys and stage in ("ended", "final"):
            self._speak_reply = bool(text)
            self.call_soon(lambda: self._finish_dictation(text))
            return
        if stage == "ended":
            self.call_soon(lambda: self.broadcast(msg("voice.session", state="ended", text="")))
            return
        if text:
            # The whole request has been heard: hand it to the agent, and answer aloud when it is done.
            self._speak_reply = True
            self.call_soon(lambda: self.broadcast(msg("voice.session", state="final", text=text[:2000])))
            return
        if ack_on:
            ack.speak(reply, voice)  # listening mode is off: acknowledge the name by voice

    def _on_level(self, level: float) -> None:
        self.call_soon(lambda: self.broadcast(msg("voice.level", level=round(level, 3))))

    async def _after_wake(self, delay: float = 0.45) -> None:
        """Press the user's dictation shortcut (Wispr Flow hands-free): once to start, once more to finish."""
        keys = self._dictation_keys
        if not keys:
            return
        from .tools import computer

        await asyncio.sleep(delay)  # let the window come forward and the command bar take focus
        try:
            await computer.ui_hotkey(computer.HotkeyArgs(keys=keys))
        except Exception as exc:  # noqa: BLE001 - a failed key press must not break waking
            log.warning("could not press the dictation shortcut: %s", exc)

    # -- voice training ---------------------------------------------------------------------------------

    def _training_state(self, state: str, **extra: Any) -> dict[str, Any]:
        t = self._trainer
        return msg("voice.training", state=state, index=t.index if t else 0, total=len(voice_training.PASSAGE),
                   sentence=t.sentence() if t else "", **extra)

    async def _voice_train(self, ws: WebSocket, action: str) -> None:
        if action == "start":
            if self._trainer is not None:
                return
            self._trainer = voice_training.Trainer()
            if not self.wake.set_training(self._training_utterance):
                self._trainer = None
                await ws.send_text(json.dumps(msg("voice.training", state="unavailable", index=0, total=0, sentence="")))
                return
            await self.broadcast(self._training_state("reading"))
        elif action == "skip" and self._trainer is not None:
            self._trainer.skip()
            await self._training_advance({})
        elif action == "cancel" and self._trainer is not None:
            self.wake.set_training(None)
            self._trainer = None
            await self.broadcast(msg("voice.training", state="cancelled", index=0, total=0, sentence=""))

    def _training_utterance(self, pcm: Any, text: str) -> None:
        """Runs on the transcriber thread for each utterance while a training run is reading."""
        trainer = self._trainer
        if trainer is None or trainer.finished:
            return
        verdict = trainer.offer(pcm, text)
        self.call_soon(lambda: self._training_advance(verdict))

    async def _training_advance(self, verdict: dict[str, Any]) -> None:
        trainer = self._trainer
        if trainer is None:
            return
        if not trainer.finished:
            await self.broadcast(self._training_state("reading", heard=verdict.get("heard", ""),
                                                      note=verdict.get("note", ""), accepted=verdict.get("accepted", True)))
            return
        self.wake.set_training(None)
        await self.broadcast(self._training_state("evaluating", heard=verdict.get("heard", "")))
        try:
            result = await asyncio.to_thread(self._training_finish, trainer)
        except Exception:
            log.exception("voice training evaluation failed")
            self._trainer = None
            await self.broadcast(msg("voice.training", state="failed", index=0, total=0, sentence=""))
            return
        self._trainer = None
        settings = await asyncio.to_thread(self.store.get_settings)
        voice = settings["voice"]
        aliases = list(dict.fromkeys([*voice.get("wake_aliases", []), *result["aliases"]]))[:12]
        merged = {**voice.get("corrections", {}), **result["corrections"]}
        patch = {"voice": {"wake_aliases": aliases, "corrections": dict(list(merged.items())[:150]),
                           "stt_model": result["model"], "trained_at": now_iso()}}
        settings = await asyncio.to_thread(self.store.update_settings, validate_settings_patch(patch))
        self._apply_wake(settings)
        await self.broadcast(msg("settings", settings=settings))
        await self.broadcast(msg("voice.training", state="done", index=len(voice_training.PASSAGE),
                                 total=len(voice_training.PASSAGE), sentence="", result=result))

    def _training_finish(self, trainer: voice_training.Trainer) -> dict[str, Any]:
        """Score the run, learn from it, and try the larger model on the same audio. Blocking."""
        if not trainer.heard:
            return {"sentences": 0, "skipped": len(trainer.skipped), "model": self.wake.model_name, "models": [],
                    "aliases": [], "corrections": {}, "before": 0.0, "after": 0.0}
        current = self.wake.model_name
        scores: dict[str, float] = {}
        transcripts: dict[str, list[str]] = {current: trainer.heard}
        scores[current], _ = trainer.score(trainer.heard)
        other = next((m for m in voice_training.MODELS if m != current), None)
        free_gb = psutil.virtual_memory().available / 1073741824
        if other and free_gb >= 3.0 and (scores[current] < 0.97 or current != "base.en"):
            try:
                model = wake_mod.load_stt(other)  # downloads once (small.en is about 480 MB)
                transcripts[other] = [wake_mod.transcribe(model, pcm) for pcm in trainer.audio]
                scores[other], _ = trainer.score(transcripts[other])
                del model
            except Exception as exc:  # noqa: BLE001 - the comparison is optional; keep the current model
                log.warning("could not evaluate %s: %s", other, exc)
        # Prefer the smaller, faster model unless the larger one is clearly better for this voice.
        best = current
        for name, value in scores.items():
            margin = 0.03 if voice_training.MODELS.index(name) > voice_training.MODELS.index(best) else -0.03
            if value > scores[best] + margin:
                best = name
        before, alignments = trainer.score(transcripts[best])
        aliases, corrections = voice_training.learn(alignments)
        after, _ = trainer.score(transcripts[best], corrections)
        names = sum(1 for s in trainer.kept_sentences() for w in voice_training.words(s) if w == "jarvis")
        heard_names = sum(1 for t in transcripts[best] if wake_mod.find_wake(t, tuple(aliases))[0])
        return {"sentences": len(trainer.heard), "skipped": len(trainer.skipped), "model": best,
                "models": [{"name": n, "accuracy": round(v, 3)} for n, v in scores.items()],
                "aliases": aliases, "corrections": corrections, "before": round(before, 3), "after": round(after, 3),
                "name_sentences": sum(1 for s in trainer.kept_sentences() if "jarvis" in s.lower()),
                "name_recognised": heard_names, "name_total": names}

    async def integrations(self) -> dict[str, Any]:
        settings = await asyncio.to_thread(self.store.get_settings)
        mail = settings.get("email") or {}
        return {
            "email": {"configured": bool(mail.get("address") and mail.get("imap_host") and mail.get("smtp_host")),
                      "has_password": await asyncio.to_thread(secrets.has_key, "email")},
            "mcp": self.mcp.status(),
            "tool_count": len(REGISTRY),
            "tools": [
                {"name": t.name, "description": t.description, "tier": int(t.tier),
                 "module": (t.handler.__module__ or "").rsplit(".", 1)[-1].replace("_tools", "").replace("_", " ")}
                for t in sorted(REGISTRY.values(), key=lambda t: t.name)
            ],
            "wake": self.wake.status(),
            "voices": await asyncio.to_thread(ack.list_voices),
            "default_voice": await asyncio.to_thread(ack.default_voice),
            "vault": sorted((await asyncio.to_thread(self.store.kv, "vault")).keys()),
            "speech": await asyncio.to_thread(speech.catalogue, settings),
        }

    async def broadcast_integrations(self) -> None:
        await self.broadcast(msg("integrations", **await self.integrations()))

    async def ready_payload(self) -> dict[str, Any]:
        return msg(
            "ready", v=PROTOCOL_VERSION, version=__version__, state=self.orchestrator.state,
            settings=await asyncio.to_thread(self.store.get_settings),
            providers=[s.to_json() for s in self.router.statuses()],
            history=await self.orchestrator.history_payload(),
            approvals=self.orchestrator.pending_approvals(),
            hotkeys=HOTKEYS, busy=self.orchestrator.busy,
            has_window=self.window_control is not None,
            integrations=await self.integrations(),
        )

    # -- message dispatch -----------------------------------------------------------------------

    async def handle(self, ws: WebSocket, m: Any) -> None:
        orch = self.orchestrator
        match m:
            case protocol.UserMessage():
                self._user_messages += 1
                await orch.submit(m.text.strip())
            case protocol.RunCancel():
                await orch.cancel()
            case protocol.ApprovalRespond():
                if not orch.resolve_approval(m.approval_id, m.decision, m.remember):
                    await ws.send_text(json.dumps(msg("error", message="That approval has already been resolved.",
                                                      code="stale_approval")))
            case protocol.ConversationNew():
                await orch.new_conversation()
            case protocol.SettingsSet():
                try:
                    patch = validate_settings_patch(m.patch)
                except ValueError as exc:
                    await ws.send_text(json.dumps(msg("error", message=f"Setting rejected: {exc}", code="settings")))
                    return
                settings = await asyncio.to_thread(self.store.update_settings, patch)
                await self.broadcast(msg("settings", settings=settings))
                if {"privacy_mode", "routing", "models"} & patch.keys():
                    await self.refresh_providers()
                if "mcp_servers" in patch:
                    # A null value removes a server; drop those keys so they do not linger in storage.
                    servers = {k: v for k, v in (settings.get("mcp_servers") or {}).items() if v}
                    if servers != settings.get("mcp_servers"):
                        await asyncio.to_thread(self.store.replace_setting, "mcp_servers", servers)
                        await self.broadcast(msg("settings", settings=await asyncio.to_thread(self.store.get_settings)))
                    await self.mcp.sync(servers)
                if "custom_providers" in patch:
                    kept = {k: v for k, v in (settings.get("custom_providers") or {}).items() if v}
                    if kept != settings.get("custom_providers"):
                        await asyncio.to_thread(self.store.replace_setting, "custom_providers", kept)
                        await self.broadcast(msg("settings", settings=await asyncio.to_thread(self.store.get_settings)))
                    await self.refresh_providers()
                if "voice" in patch:
                    self._apply_wake(settings)
                if {"mcp_servers", "email", "voice"} & patch.keys():
                    await self.broadcast_integrations()
            case protocol.SecretSet():
                try:
                    await asyncio.to_thread(secrets.set_key, m.provider, m.value)
                except ValueError as exc:
                    await ws.send_text(json.dumps(msg("error", message=f"Key not saved: {exc}", code="secret")))
                    return
                except Exception as exc:  # noqa: BLE001 - keyring backends raise assorted errors
                    log.warning("keyring write failed: %s", type(exc).__name__)
                    await ws.send_text(json.dumps(msg("error", message="Windows Credential Manager refused the key.",
                                                      code="secret")))
                    return
                await self.broadcast(msg("notice", level="info",
                                         message=("Key saved." if m.value.strip() else "Key removed.")))
                settings = await asyncio.to_thread(self.store.get_settings)
                is_provider = m.provider in PROVIDER_BY_ID or m.provider in (settings.get("custom_providers") or {})
                if m.provider != "email" and not is_provider:
                    # A free-form key for MCP servers and other integrations: track its name (never its value).
                    await asyncio.to_thread(self.store.kv_set_item, "vault", m.provider, bool(m.value.strip()))
                if is_provider:
                    await self.refresh_providers()
                else:
                    await self.broadcast_integrations()
            case protocol.ProvidersRefresh():
                await self.refresh_providers(retest=m.retest)
            case protocol.AuditQuery():
                items, more = await asyncio.to_thread(
                    self.store.query_audit, limit=m.limit, before_id=m.before_id, tool=m.tool, min_tier=m.min_tier)
                await ws.send_text(json.dumps(msg("audit.page", items=items, has_more=more,
                                                  append=m.before_id is not None), default=str))
            case protocol.VoicePreview():
                settings = await asyncio.to_thread(self.store.get_settings)
                chosen = m.voice or str(settings["voice"].get("tts_voice") or "")
                speech.say("Good day. I am Jarvis, at your service.", settings if not m.voice else {},
                           lambda: ack.speak("Good day. I am Jarvis, at your service.", chosen))
            case protocol.VoiceTrain():
                await self._voice_train(ws, m.action)
            case protocol.WakeCalibrate():
                def done(heard: list[str]) -> None:
                    aliases = tuple(self.wake.aliases or ())
                    items = [{"text": t[:120], "recognised": wake_mod.find_wake(t, aliases)[0],
                              "alias": " ".join(wake_mod.normalise_phrase(t).split()[:3])} for t in heard]
                    self.call_soon(lambda: self.broadcast(msg("wake.calibration", state="done", items=items)))

                if self.wake.calibrate(done):
                    await self.broadcast(msg("wake.calibration", state="listening", items=[]))
                else:
                    await ws.send_text(json.dumps(msg("wake.calibration", state="unavailable", items=[])))
            case protocol.ClientError():
                self.faults.record("DisplayError", m.message, m.stack, source="display")
            case protocol.SelfHeal():
                if m.action == "propose":
                    # Diagnosis takes a while (a model call, then the test-suite on a copy): run it in the background.
                    task = asyncio.create_task(self.healer.propose(m.fault_id))
                    self._tasks.append(task)
                elif m.action == "apply":
                    await asyncio.to_thread(self.healer.apply, m.fault_id)  # reached only from the user's button
                elif m.action == "rollback":
                    await asyncio.to_thread(self.healer.rollback, m.fault_id)
                elif m.action == "dismiss":
                    self.healer.dismiss(m.fault_id)
                await ws.send_text(json.dumps(msg("selfheal", **self.faults.snapshot())))
            case protocol.WindowControl():
                if self.window_control:
                    await asyncio.to_thread(self.window_control, m.action)


def create_app(hub: Hub, port: int = PORT) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await hub.start()
        yield
        await hub.stop()

    app = FastAPI(title="Jarvis core", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.get("/api/health")
    async def health() -> JSONResponse:
        return JSONResponse({"ok": True, "version": __version__})

    @app.get("/api/pair")
    async def pair(request: Request) -> JSONResponse:
        """Return the WebSocket token for mobile pairing. Loopback only."""
        host = request.headers.get("host", "")
        if host not in allowed_hosts(port):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        return JSONResponse({"token": hub.token, "version": __version__, "ws": f"ws://127.0.0.1:{port}/ws"})

    @app.get(mcp_oauth.CALLBACK_PATH)
    async def mcp_sign_in_done(request: Request) -> HTMLResponse:
        """Where the browser lands after the user approves a connector. Loopback only; one-time state required."""
        if request.headers.get("host", "") not in allowed_hosts(port):
            return HTMLResponse("Not allowed.", status_code=403)
        q = request.query_params
        state, code, error = q.get("state", "")[:512], q.get("code", "")[:4096], q.get("error", "")[:120]
        if error:
            ok = False
            hub.mcp.sign_ins.fail(state, error)
        else:
            ok = hub.mcp.sign_ins.complete(state, code, q.get("iss"))
        title = "Signed in" if ok else "Sign-in did not complete"
        body = ("You can close this tab and return to Jarvis." if ok
                else "Jarvis was not waiting for this sign-in, or it was refused. Remove the connector in Jarvis and add it again.")
        page = (f"<!doctype html><meta charset=utf-8><title>Jarvis</title><body style='font:16px system-ui;background:#06101d;"
                f"color:#d6ecff;display:grid;place-items:center;height:100vh;margin:0'><div><h1>{title}</h1><p>{body}</p></div>")
        return HTMLResponse(page, status_code=200 if ok else 400,
                            headers={"Cache-Control": "no-store", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        # Browsers always send Origin on WebSocket handshakes, so a hostile page is rejected here even
        # before the token check; the Host check defeats DNS rebinding.
        origin = ws.headers.get("origin")
        host = ws.headers.get("host", "")
        if host not in allowed_hosts(port) or (origin is not None and origin not in allowed_origins(port)):
            await ws.close(code=4403)
            return
        await ws.accept()
        try:
            first = protocol.client_adapter.validate_json(await asyncio.wait_for(ws.receive_text(), timeout=5))
            if not isinstance(first, protocol.Hello) or not hmac.compare_digest(first.token, hub.token):
                raise ValueError("bad hello")
        except (TimeoutError, ValidationError, ValueError, WebSocketDisconnect):
            with suppress(RuntimeError):
                await ws.close(code=4401)
            return

        hub.clients.add(ws)
        try:
            await ws.send_text(json.dumps(await hub.ready_payload(), default=str))
            _msg_times: list[float] = []
            _MAX_MSG_PER_MIN = 60
            import time as _time
            while True:
                raw = await ws.receive_text()
                now_t = _time.monotonic()
                _msg_times = [t for t in _msg_times if now_t - t < 60]
                if len(_msg_times) >= _MAX_MSG_PER_MIN:
                    await ws.send_text(json.dumps(msg("error", message="Rate limit exceeded.", code="rate_limit")))
                    continue
                _msg_times.append(now_t)
                if len(raw) > 100_000:
                    await ws.send_text(json.dumps(msg("error", message="Message too large.", code="protocol")))
                    continue
                try:
                    parsed = protocol.client_adapter.validate_json(raw)
                except ValidationError:
                    await ws.send_text(json.dumps(msg("error", message="Malformed message.", code="protocol")))
                    continue
                try:
                    await hub.handle(ws, parsed)
                except Exception:
                    log.exception("handler failed for %s", getattr(parsed, "type", "?"))
                    await ws.send_text(json.dumps(msg("error", message="Jarvis could not process that.",
                                                      code="internal")))
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(ws)

    if UI_DIST.is_dir():
        app.mount("/", _HudFiles(directory=UI_DIST, html=True), name="ui")
    else:
        @app.get("/")
        async def no_ui() -> PlainTextResponse:
            return PlainTextResponse("HUD not built. Run: npm --prefix ui install && npm --prefix ui run build",
                                     status_code=503)

    return app


__all__ = ["HOST", "Hub", "create_app"]
