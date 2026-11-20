"""Model Context Protocol client: connect to MCP servers and expose their tools to Jarvis.

Each configured server gets one long-lived task that owns its transport and session. Its tools are
registered as `mcp__<server>__<tool>`. Because Jarvis cannot know what an external tool does:
  - every MCP result is treated as untrusted content (taints the run);
  - a tool is auto-run only when the server marks it read-only; everything else is `sensitive`
    and asks for approval in every autonomy mode.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from typing import Any

from .config import DATA_DIR
from .mcp_oauth import SIGN_IN_TIMEOUT_S, SignIns, TokenStore
from .policy import Tier
from .tools.registry import REGISTRY, Args, Tool, ToolError

log = logging.getLogger(__name__)

RESULT_CAP = 20_000
CALL_TIMEOUT_S = 90.0


@dataclass
class _Server:
    name: str
    config: dict[str, Any]
    task: asyncio.Task[None] | None = None
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    session: Any = None
    state: str = "connecting"  # connecting | signin (waiting for the user in the browser) | ready | error
    error: str = ""
    tools: list[str] = field(default_factory=list)


_SECRET_REF = re.compile(r"\$\{secret:([a-z][a-z0-9_]{0,31})\}")


def _resolve(values: dict[str, Any]) -> dict[str, str]:
    """Expand ${secret:name} from the key vault so keys never sit in settings in plain text."""
    from . import secrets

    def expand(match: re.Match[str]) -> str:
        value = secrets.get_key(match.group(1))
        if value is None:
            raise RuntimeError(f"stored key “{match.group(1)}” was not found — add it in Settings → Keys")
        return value

    return {str(k): _SECRET_REF.sub(expand, str(v)) for k, v in values.items()}


def _field(obj: Any, *names: str) -> Any:
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return None


def _safe(part: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", part)


class McpManager:
    def __init__(self) -> None:
        self._servers: dict[str, _Server] = {}
        self.on_change: Any = None  # async callback when a server's state changes
        self.sign_ins = SignIns()  # browser sign-ins in progress for remote connectors

    def status(self) -> list[dict[str, Any]]:
        return [{"name": s.name, "state": s.state, "error": s.error, "tools": len(s.tools),
                 "target": s.config.get("url") or " ".join([s.config.get("command", ""), *s.config.get("args", [])])[:120]}
                for s in self._servers.values()]

    async def sync(self, config: dict[str, Any]) -> None:
        """Start, restart or stop servers so the running set matches the settings."""
        wanted = {name: cfg for name, cfg in (config or {}).items() if isinstance(cfg, dict)}
        for name in list(self._servers):
            if name not in wanted or self._servers[name].config != wanted[name]:
                gone = "url" in self._servers[name].config and name not in wanted
                await self._stop(name)
                if gone:
                    TokenStore(name).forget()  # removing a connector signs it out
        for name, cfg in wanted.items():
            if name not in self._servers:
                server = _Server(name, cfg)
                self._servers[name] = server
                server.task = asyncio.create_task(self._run(server))

    async def aclose(self) -> None:
        for name in list(self._servers):
            await self._stop(name)

    async def _stop(self, name: str) -> None:
        server = self._servers.pop(name, None)
        if not server:
            return
        server.stop.set()
        if server.task:
            try:
                await asyncio.wait_for(server.task, timeout=5)
            except (TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001 - shutting down regardless
                server.task.cancel()
        self._unregister(server)

    def _unregister(self, server: _Server) -> None:
        for tool_name in server.tools:
            REGISTRY.pop(tool_name, None)
        server.tools = []

    async def _notify(self) -> None:
        if self.on_change:
            try:
                await self.on_change()
            except Exception:
                log.exception("MCP change callback failed")

    async def _run(self, server: _Server) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.streamable_http import streamable_http_client

        errlog = None
        first_reply = 30.0
        try:
            cfg = server.config
            if "url" in cfg:
                headers = _resolve(cfg.get("headers") or {})
                if headers:
                    import httpx2

                    http = httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(60.0, connect=10.0))
                    transport = streamable_http_client(cfg["url"], http_client=http)
                else:
                    # No key given: offer browser sign-in. It is used only if the server asks for it.
                    import httpx2

                    async def on_state(state: str, server: _Server = server) -> None:
                        server.state = state
                        await self._notify()

                    auth = self.sign_ins.provider(server.name, cfg["url"], on_state)
                    http = httpx2.AsyncClient(auth=auth, follow_redirects=True,
                                              timeout=httpx2.Timeout(60.0, connect=10.0))
                    transport = streamable_http_client(cfg["url"], http_client=http)
                    first_reply = SIGN_IN_TIMEOUT_S + 30  # leave time for the user to approve in the browser
            else:
                command = shutil.which(cfg["command"]) or cfg["command"]  # resolves npx → npx.cmd on Windows
                env = {**os.environ, **_resolve(cfg.get("env") or {})}
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                errlog = open(DATA_DIR / f"mcp-{_safe(server.name)}.log", "a", encoding="utf-8")  # noqa: SIM115
                transport = stdio_client(StdioServerParameters(command=command, args=list(cfg.get("args") or []), env=env),
                                         errlog=errlog)
            async with transport as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await asyncio.wait_for(session.initialize(), timeout=first_reply)
                    listed = await asyncio.wait_for(session.list_tools(), timeout=30)
                    server.session = session
                    for spec in listed.tools:
                        self._register(server, spec)
                    server.state, server.error = "ready", ""
                    log.info("MCP server %s ready with %d tools", server.name, len(server.tools))
                    await self._notify()
                    await server.stop.wait()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 - transports raise exception groups and OS errors
            if not server.stop.is_set():
                leaf: BaseException = exc
                while getattr(leaf, "exceptions", None):  # unwrap nested task-group errors to the real cause
                    leaf = leaf.exceptions[0]  # type: ignore[attr-defined]
                detail = (str(leaf) or type(leaf).__name__)[:200]
                if "command" in server.config:
                    detail += f" (see data/mcp-{_safe(server.name)}.log)"
                server.state, server.error = "error", detail
                log.warning("MCP server %s failed: %s", server.name, detail)
        finally:
            server.session = None
            self._unregister(server)
            if errlog:
                errlog.close()
            if not server.stop.is_set():
                await self._notify()

    def _register(self, server: _Server, spec: Any) -> None:
        name = f"mcp__{_safe(server.name)}__{_safe(spec.name)}"[:64]
        if name in REGISTRY:
            return
        annotations = getattr(spec, "annotations", None)
        # mcp 2.x exposes snake_case fields, 1.x camelCase; accept both.
        read_only = bool(_field(annotations, "read_only_hint", "readOnlyHint"))
        schema = dict(_field(spec, "input_schema", "inputSchema") or {"type": "object", "properties": {}})
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        remote_name = spec.name

        async def handler(args: dict[str, Any]) -> Any:
            if server.session is None:
                raise ToolError(f"The {server.name} server is not connected.")
            try:
                result = await asyncio.wait_for(server.session.call_tool(remote_name, args), timeout=CALL_TIMEOUT_S)
            except TimeoutError as exc:
                raise ToolError(f"{server.name} did not answer in time.") from exc
            except Exception as exc:
                raise ToolError(f"{server.name} failed: {str(exc)[:200]}") from exc
            texts = [getattr(c, "text", None) or f"[{getattr(c, 'type', 'content')}]" for c in getattr(result, "content", [])]
            text = "\n".join(texts)[:RESULT_CAP]
            if _field(result, "is_error", "isError"):
                raise ToolError(text[:400] or f"{remote_name} reported an error.")
            return {"text": text}

        description = f"[{server.name}] {(spec.description or remote_name).strip()}"[:1000]
        REGISTRY[name] = Tool(
            name=name, description=description, args_model=Args, tier=Tier.T0 if read_only else Tier.T2,
            handler=handler, summary=lambda a, n=remote_name, s=server.name: f"{s}: {n}",
            taints=True, sensitive=not read_only, raw_schema=schema,
        )
        server.tools.append(name)
