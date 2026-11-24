"""Dry-run / preview tools: show what a command would change before it runs.

Addresses the need for a dry-run mode that previews the effect of a tool
invocation without actually executing it.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from .. import host
from ..policy import Tier
from .registry import REGISTRY, Args, ToolError, tool


TIER_LABELS = {
    Tier.T0: "T0 — Observe (runs automatically, read-only)",
    Tier.T1: "T1 — Reversible (runs automatically, logged)",
    Tier.T2: "T2 — Modify (requires your approval)",
    Tier.T3: "T3 — Destructive (requires explicit approval every time)",
}

SIDE_EFFECTS: dict[str, str] = {
    "file_write": "Creates or overwrites a file on disk",
    "file_delete": "Permanently deletes a file",
    "file_move": "Moves/renames a file",
    "file_copy": "Copies a file to a new location",
    "dir_create": "Creates a new directory",
    "shell_run": f"Executes a shell command ({host.SHELL_NAME})",
    "app_launch": "Launches a Windows application",
    "app_close": "Closes an application window",
    "browser_open": "Opens a URL in the browser",
    "browser_click": "Clicks an element on a web page",
    "email_send": "Sends an email via IMAP/SMTP",
    "telegram_send": "Sends a Telegram message",
    "volume_set": "Changes system audio volume",
    "power_lock": "Locks the workstation",
    "power_sleep": "Puts the machine to sleep",
    "power_restart": "Restarts the machine",
    "power_shutdown": "Shuts down the machine",
    "twilio_call": "Places a phone call via Twilio",
    "clipboard_paste": "Writes to the system clipboard",
    "music_play": "Opens a music URL in the browser",
    "memory_remember": "Stores a fact in long-term memory",
    "memory_forget": "Removes a fact from long-term memory",
    "timer_set": "Sets a countdown timer",
    "schedule_create": "Creates a scheduled task",
}


class DryRunArgs(Args):
    tool_name: str = Field(..., min_length=1, description="Name of the tool to preview")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Arguments you would pass to the tool")


@tool(
    "dry_run",
    "Preview what a tool would do WITHOUT executing it. Shows the tool's tier, "
    "whether it needs approval, what side effects it has, and validates your arguments. "
    "Use this before running sensitive or destructive commands.",
    DryRunArgs, Tier.T0,
    summary=lambda a: f"Dry-run preview: {a.tool_name}",
)
async def dry_run(a: DryRunArgs) -> dict[str, Any]:
    target = REGISTRY.get(a.tool_name)
    if not target:
        close = [n for n in REGISTRY if a.tool_name.lower() in n.lower()]
        raise ToolError(
            f"Tool '{a.tool_name}' not found."
            + (f" Did you mean: {', '.join(close[:5])}?" if close else "")
        )

    tier_label = TIER_LABELS.get(target.tier, f"T{int(target.tier)}")
    needs_approval = int(target.tier) >= 2 or target.sensitive

    validation_ok = True
    validation_error = None
    if a.arguments and target.raw_schema is None:
        try:
            target.args_model.model_validate(a.arguments)
        except Exception as exc:
            validation_ok = False
            validation_error = str(exc)

    summary_text = None
    if a.arguments and validation_ok:
        try:
            parsed = target.validate(a.arguments)
            summary_text = target.summary(parsed)
        except Exception:
            pass

    side_effect = SIDE_EFFECTS.get(a.tool_name)

    params = target.schema().get("parameters", {})
    required = params.get("required", [])
    properties = params.get("properties", {})

    return {
        "tool": a.tool_name,
        "description": target.description,
        "tier": tier_label,
        "needs_approval": needs_approval,
        "sensitive": target.sensitive,
        "taints_run": target.taints,
        "side_effect": side_effect or "No known side effects catalogued",
        "summary": summary_text,
        "arguments_valid": validation_ok,
        "validation_error": validation_error,
        "required_params": required,
        "available_params": list(properties.keys()),
        "would_execute": False,
        "note": "This is a preview only. The tool was NOT executed.",
    }


class ToolInspectArgs(Args):
    tool_name: str | None = Field(None, description="Tool name to inspect (omit to list all)")
    category: str | None = Field(None, description="Filter by category substring (e.g. 'file', 'browser', 'email')")


@tool(
    "tool_inspect",
    "Inspect available tools: list all tools with their tiers, or get details about a "
    "specific tool including its parameters, tier, and whether it needs approval.",
    ToolInspectArgs, Tier.T0,
    summary=lambda a: f"Inspect tool{': ' + a.tool_name if a.tool_name else 's'}",
)
async def tool_inspect(a: ToolInspectArgs) -> dict[str, Any]:
    if a.tool_name:
        target = REGISTRY.get(a.tool_name)
        if not target:
            close = [n for n in REGISTRY if a.tool_name.lower() in n.lower()]
            raise ToolError(
                f"Tool '{a.tool_name}' not found."
                + (f" Did you mean: {', '.join(close[:5])}?" if close else "")
            )
        params = target.schema().get("parameters", {})
        return {
            "name": target.name,
            "description": target.description,
            "tier": TIER_LABELS.get(target.tier, f"T{int(target.tier)}"),
            "sensitive": target.sensitive,
            "taints": target.taints,
            "needs_approval": int(target.tier) >= 2 or target.sensitive,
            "parameters": params.get("properties", {}),
            "required": params.get("required", []),
        }

    tools_list = []
    for name, t in sorted(REGISTRY.items()):
        if a.category and a.category.lower() not in name.lower():
            continue
        tools_list.append({
            "name": name,
            "tier": f"T{int(t.tier)}",
            "sensitive": t.sensitive,
            "description": t.description[:80],
        })
    return {"tools": tools_list, "count": len(tools_list)}
