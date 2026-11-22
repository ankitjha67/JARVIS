"""MCP server catalogue: browse and enable popular MCP servers (idea 67)."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from ..policy import Tier
from .registry import Args, ToolError, tool

CATALOGUE: list[dict[str, Any]] = [
    {"id": "filesystem", "name": "Filesystem", "description": "Read, write, search files with sandboxing.",
     "config": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]}},
    {"id": "github", "name": "GitHub", "description": "Repos, issues, PRs, code search.",
     "config": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"]},
     "needs_key": "github"},
    {"id": "slack", "name": "Slack", "description": "Send messages, search channels, read threads.",
     "config": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-slack"]},
     "needs_key": "slack_bot_token"},
    {"id": "google-drive", "name": "Google Drive", "description": "Search and read Google Drive files.",
     "config": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-gdrive"]}},
    {"id": "postgres", "name": "PostgreSQL", "description": "Query PostgreSQL databases.",
     "config": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-postgres"]},
     "needs_key": "postgres_url"},
    {"id": "sqlite", "name": "SQLite", "description": "Query local SQLite databases.",
     "config": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-sqlite"]}},
    {"id": "brave-search", "name": "Brave Search", "description": "Web search via Brave Search API.",
     "config": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-brave-search"]},
     "needs_key": "brave"},
    {"id": "fetch", "name": "Web Fetch", "description": "Fetch and convert web pages to markdown.",
     "config": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-fetch"]}},
    {"id": "memory-kg", "name": "Memory (Knowledge Graph)", "description": "Persistent knowledge graph memory.",
     "config": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"]}},
    {"id": "puppeteer", "name": "Puppeteer", "description": "Browser automation with screenshots.",
     "config": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-puppeteer"]}},
    {"id": "sequential-thinking", "name": "Sequential Thinking", "description": "Step-by-step reasoning for complex problems.",
     "config": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"]}},
    {"id": "everything", "name": "Everything Search", "description": "Fast file search with Everything SDK (Windows).",
     "config": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-everything"]}},
    {"id": "time", "name": "Time", "description": "Get current time and timezone conversions.",
     "config": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-time"]}},
    {"id": "sentry", "name": "Sentry", "description": "View errors, issues, and performance data.",
     "config": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-sentry"]},
     "needs_key": "sentry"},
    {"id": "linear", "name": "Linear", "description": "Issue tracking: create, search, update Linear issues.",
     "config": {"command": "npx", "args": ["-y", "mcp-linear"]},
     "needs_key": "linear"},
    {"id": "notion", "name": "Notion", "description": "Search and read Notion pages and databases.",
     "config": {"command": "npx", "args": ["-y", "notion-mcp-server"]},
     "needs_key": "notion"},
    {"id": "spotify", "name": "Spotify", "description": "Control playback, search music, manage playlists.",
     "config": {"command": "npx", "args": ["-y", "mcp-spotify"]},
     "needs_key": "spotify"},
    {"id": "youtube", "name": "YouTube", "description": "Search and get video transcripts.",
     "config": {"command": "npx", "args": ["-y", "mcp-youtube"]}},
    {"id": "docker", "name": "Docker", "description": "Manage containers, images, and volumes.",
     "config": {"command": "npx", "args": ["-y", "mcp-docker"]}},
    {"id": "todoist", "name": "Todoist", "description": "Task management: create, complete, search tasks.",
     "config": {"command": "npx", "args": ["-y", "mcp-todoist"]},
     "needs_key": "todoist"},
    {"id": "mobile-mcp", "name": "Mobile MCP",
     "description": "Drive iOS/Android devices: screenshot, tap, swipe, type, launch/install apps, read logs, record screen. Works with simulators, emulators, and real devices.",
     "config": {"command": "npx", "args": ["-y", "@mobilenext/mobile-mcp@latest"]}},
    {"id": "gods-eye-view", "name": "God's Eye View",
     "description": "3D geospatial intelligence: live flights, ships, satellites, earthquakes, fires, weather, CCTV, military awareness. Requires Node.js 24+. Use gods_eye_setup first.",
     "config": {"command": "node", "args": ["server/mcp/stdio.js", "--api-base", "http://localhost:4173"]},
     "needs_key": ""},
]


class MCPCatalogueArgs(Args):
    search: str = Field("", max_length=100, description="Filter by name or description")


@tool("mcp_catalogue", "Browse available MCP servers to install.",
      MCPCatalogueArgs, Tier.T0, summary=lambda a: "MCP catalogue")
async def mcp_catalogue(a: MCPCatalogueArgs) -> dict:
    results = CATALOGUE
    if a.search:
        q = a.search.lower()
        results = [s for s in CATALOGUE if q in s["name"].lower() or q in s["description"].lower()]
    return {"servers": [{"id": s["id"], "name": s["name"], "description": s["description"],
                         "needs_key": s.get("needs_key", "")} for s in results],
            "total": len(results)}


class MCPInstallArgs(Args):
    server_id: str = Field(..., min_length=1, max_length=50)


@tool("mcp_install", "Enable an MCP server from the catalogue.",
      MCPInstallArgs, Tier.T2, sensitive=True,
      summary=lambda a: f"Install MCP: {a.server_id}")
async def mcp_install(a: MCPInstallArgs) -> dict:
    entry = next((s for s in CATALOGUE if s["id"] == a.server_id), None)
    if not entry:
        raise ToolError(f"Unknown MCP server: {a.server_id}. Use mcp_catalogue to browse.")
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    settings = hub.settings
    mcp = dict(settings.get("mcp_servers", {}))
    if a.server_id in mcp:
        return {"already_installed": True, "server": a.server_id}
    config = dict(entry["config"])
    if entry.get("needs_key"):
        config.setdefault("env", {})
        key_name = entry["needs_key"].upper()
        config["env"][f"{key_name}_API_KEY"] = f"${{secret:{entry['needs_key']}}}"
    if a.server_id == "gods-eye-view":
        from .gods_eye_tools import _get_install_dir
        install_dir = _get_install_dir(settings)
        if not (install_dir / "server" / "mcp" / "stdio.js").exists():
            raise ToolError("God's Eye View is not installed yet. Run gods_eye_setup first.")
        config["args"] = [str(install_dir / "server" / "mcp" / "stdio.js"), "--api-base", "http://localhost:4173"]
    mcp[a.server_id] = config
    await hub.update_settings({"mcp_servers": mcp})
    return {"installed": True, "server": a.server_id, "name": entry["name"],
            "needs_key": entry.get("needs_key", "")}


class MCPUninstallArgs(Args):
    server_id: str = Field(..., min_length=1, max_length=50)


@tool("mcp_uninstall", "Remove an MCP server from your configuration.",
      MCPUninstallArgs, Tier.T2, sensitive=True,
      summary=lambda a: f"Uninstall MCP: {a.server_id}")
async def mcp_uninstall(a: MCPUninstallArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    settings = hub.settings
    mcp = dict(settings.get("mcp_servers", {}))
    if a.server_id not in mcp:
        raise ToolError(f"MCP server '{a.server_id}' is not installed.")
    mcp[a.server_id] = None
    await hub.update_settings({"mcp_servers": mcp})
    return {"uninstalled": True, "server": a.server_id}
