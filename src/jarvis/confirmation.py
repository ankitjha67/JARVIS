"""Confirmation gate and undo stack (Mark-LVII).

Pre-execution confirmation with structured plan display, and an undo stack
that records reversible actions so the user can say "undo that".
"""

from __future__ import annotations

import logging
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .policy import Tier

log = logging.getLogger(__name__)

_MAX_UNDO = 50

# Tools whose results carry enough info to reverse the action.
_REVERSIBLE_TOOLS: dict[str, str] = {
    "file_write": "restore_file",
    "file_append": "restore_file",
    "file_move": "move_back",
    "file_copy": "delete_copy",
    "clipboard_set": "restore_clipboard",
}

# Tools that are inherently destructive — no auto-undo.
_DESTRUCTIVE_TOOLS = frozenset({
    "shell_run", "file_delete", "process_kill", "power_shutdown", "power_restart",
})


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class UndoEntry:
    id: str
    tool_name: str
    args: dict[str, Any]
    result: Any
    reverse_action: dict[str, Any] | None
    timestamp: str
    undone: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool_name": self.tool_name,
            "args": self.args,
            "reverse_action": self.reverse_action,
            "timestamp": self.timestamp,
            "undone": self.undone,
        }


class UndoStack:
    """Records tool executions and supports undoing reversible actions."""

    def __init__(self, max_size: int = _MAX_UNDO) -> None:
        self._stack: deque[UndoEntry] = deque(maxlen=max_size)
        self._by_id: dict[str, UndoEntry] = {}

    def push(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: Any,
        reverse_action: dict[str, Any] | None = None,
    ) -> str:
        if reverse_action is None:
            reverse_action = self._infer_reverse(tool_name, args, result)
        eid = uuid.uuid4().hex[:12]
        entry = UndoEntry(
            id=eid,
            tool_name=tool_name,
            args=args,
            result=result,
            reverse_action=reverse_action,
            timestamp=_now(),
        )
        self._stack.append(entry)
        self._by_id[eid] = entry
        # Evict old entries from the lookup dict
        while len(self._by_id) > len(self._stack) + 10:
            oldest_id = next(iter(self._by_id))
            self._by_id.pop(oldest_id, None)
        return eid

    def undo_last(self) -> UndoEntry | None:
        for entry in reversed(self._stack):
            if entry.reverse_action and not entry.undone:
                entry.undone = True
                return entry
        return None

    def undo_by_id(self, entry_id: str) -> UndoEntry | None:
        entry = self._by_id.get(entry_id)
        if entry and entry.reverse_action and not entry.undone:
            entry.undone = True
            return entry
        return None

    def can_undo(self) -> bool:
        return any(e.reverse_action and not e.undone for e in self._stack)

    def list_undoable(self, limit: int = 10) -> list[dict[str, Any]]:
        result = []
        for entry in reversed(self._stack):
            if entry.reverse_action and not entry.undone:
                result.append(entry.to_json())
                if len(result) >= limit:
                    break
        return result

    def _infer_reverse(self, tool_name: str, args: dict[str, Any], result: Any) -> dict[str, Any] | None:
        if tool_name in _DESTRUCTIVE_TOOLS:
            return None

        if tool_name in ("file_write", "file_append"):
            original = None
            if isinstance(result, dict):
                original = result.get("previous_content") or result.get("backup")
            if original is not None:
                return {"tool": "file_write", "args": {"path": args.get("path"), "content": original},
                        "description": f"Restore original content of {args.get('path')}"}
            return None

        if tool_name == "file_move":
            return {"tool": "file_move", "args": {"src": args.get("dst"), "dst": args.get("src")},
                    "description": f"Move back from {args.get('dst')} to {args.get('src')}"}

        if tool_name == "file_copy":
            return {"tool": "file_delete", "args": {"path": args.get("dst")},
                    "description": f"Delete the copy at {args.get('dst')}"}

        if tool_name == "clipboard_set":
            prev = None
            if isinstance(result, dict):
                prev = result.get("previous")
            if prev is not None:
                return {"tool": "clipboard_set", "args": {"text": prev},
                        "description": "Restore previous clipboard content"}

        return None


class ConfirmationGate:
    """Decides whether an action needs structured confirmation beyond the normal approval flow."""

    # Multi-tool patterns that should show a plan before executing.
    _MULTI_STEP_PATTERNS = frozenset({
        "file_delete", "power_shutdown", "power_restart", "process_kill",
    })

    # Tools where batch operations need extra confirmation.
    _BATCH_CONFIRM = frozenset({
        "file_delete", "file_move", "shell_run",
    })

    def should_confirm(self, tool_name: str, args: dict[str, Any], tier: Tier) -> bool:
        if tier >= Tier.T3:
            return True
        if tool_name in self._MULTI_STEP_PATTERNS:
            return True
        # Batch operations on multiple files
        if tool_name in self._BATCH_CONFIRM:
            path = args.get("path", "")
            if isinstance(path, str) and ("*" in path or "?" in path):
                return True
        return False

    def format_plan(self, tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
        steps = []
        risk_level = "low"
        for i, call in enumerate(tool_calls, 1):
            name = call.get("name", "unknown")
            args = call.get("args", {})
            tier = call.get("tier", 0)
            if tier >= 3:
                risk_level = "high"
            elif tier >= 2 and risk_level != "high":
                risk_level = "medium"
            steps.append({
                "index": i,
                "tool": name,
                "summary": call.get("summary", name),
                "args": args,
                "tier": tier,
                "reversible": name not in _DESTRUCTIVE_TOOLS,
            })
        return {
            "steps": steps,
            "total": len(steps),
            "risk_level": risk_level,
            "has_destructive": any(s["tool"] in _DESTRUCTIVE_TOOLS for s in steps),
            "all_reversible": all(s["reversible"] for s in steps),
        }
