"""Outcome verification (Mark-LVII).

After a tool executes, verify the outcome matches the intent.
For file operations: check the file exists/changed.
For shell commands: check exit code and output.
For browser actions: check the page state changed.
For app launches: check the process is running.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_ERROR_PATTERNS = re.compile(
    r"(?i)(error|exception|traceback|failed|fatal|panic|segfault|"
    r"permission\s+denied|access\s+denied|not\s+found|no\s+such\s+file|"
    r"cannot\s+open|could\s+not|unable\s+to)"
)


@dataclass(frozen=True)
class VerificationResult:
    verified: bool
    confidence: float
    evidence: str
    suggestion: str = ""


Verifier = Callable[[str, dict[str, Any], Any], Awaitable[VerificationResult]]


def _extract_path(args: dict[str, Any]) -> str | None:
    for key in ("path", "file", "filepath", "filename", "target", "destination", "dest"):
        if key in args and isinstance(args[key], str):
            return args[key]
    return None


async def _verify_file_write(tool: str, args: dict[str, Any], result: Any) -> VerificationResult:
    path = _extract_path(args)
    if not path:
        return VerificationResult(True, 0.3, "No file path found in args to verify.")
    try:
        exists = await asyncio.to_thread(os.path.isfile, path)
        if exists:
            stat = await asyncio.to_thread(os.stat, path)
            return VerificationResult(True, 0.95, f"File exists: {path} ({stat.st_size} bytes)")
        return VerificationResult(False, 0.9, f"File not found after write: {path}",
                                  "The file write may have failed silently.")
    except OSError as exc:
        return VerificationResult(False, 0.7, f"Cannot stat file: {exc}")


async def _verify_file_delete(tool: str, args: dict[str, Any], result: Any) -> VerificationResult:
    path = _extract_path(args)
    if not path:
        return VerificationResult(True, 0.3, "No file path found in args to verify.")
    try:
        exists = await asyncio.to_thread(os.path.exists, path)
        if not exists:
            return VerificationResult(True, 0.95, f"Confirmed deleted: {path}")
        return VerificationResult(False, 0.9, f"File still exists after delete: {path}",
                                  "The delete may have been skipped or failed.")
    except OSError as exc:
        return VerificationResult(False, 0.5, f"Cannot check path: {exc}")


async def _verify_dir_create(tool: str, args: dict[str, Any], result: Any) -> VerificationResult:
    path = _extract_path(args)
    if not path:
        return VerificationResult(True, 0.3, "No path found in args.")
    try:
        exists = await asyncio.to_thread(os.path.isdir, path)
        if exists:
            return VerificationResult(True, 0.95, f"Directory exists: {path}")
        return VerificationResult(False, 0.9, f"Directory not found: {path}")
    except OSError as exc:
        return VerificationResult(False, 0.5, f"Cannot check: {exc}")


async def _verify_shell(tool: str, args: dict[str, Any], result: Any) -> VerificationResult:
    if isinstance(result, dict):
        content = result.get("result") or result.get("untrusted_content")
        if isinstance(content, dict):
            exit_code = content.get("exit_code", content.get("exitCode"))
            stdout = str(content.get("stdout", content.get("output", "")))
            stderr = str(content.get("stderr", ""))
            if exit_code is not None and int(exit_code) != 0:
                return VerificationResult(False, 0.85,
                                          f"Non-zero exit code: {exit_code}",
                                          "The command reported an error.")
            combined = stdout + stderr
            if _ERROR_PATTERNS.search(combined[:2000]):
                return VerificationResult(False, 0.6,
                                          "Error patterns detected in output.",
                                          "Review the command output for errors.")
            return VerificationResult(True, 0.8, "Exit code 0, no error patterns.")
    return VerificationResult(True, 0.4, "Shell result format unrecognised; skipped deep check.")


async def _verify_process_launch(tool: str, args: dict[str, Any], result: Any) -> VerificationResult:
    name = args.get("name", args.get("app", args.get("process", "")))
    if not name:
        return VerificationResult(True, 0.3, "No process name to verify.")
    try:
        import psutil
        name_lower = str(name).lower()
        found = any(name_lower in p.name().lower()
                     for p in psutil.process_iter(["name"])
                     if p.info and p.info.get("name"))
        if found:
            return VerificationResult(True, 0.9, f"Process matching '{name}' is running.")
        return VerificationResult(False, 0.7, f"No process matching '{name}' found.",
                                  "The app may not have started yet — it can take a few seconds.")
    except Exception as exc:
        return VerificationResult(True, 0.3, f"Process check unavailable: {exc}")


async def _verify_browser(tool: str, args: dict[str, Any], result: Any) -> VerificationResult:
    url = args.get("url", "")
    if isinstance(result, dict):
        content = result.get("result", result)
        if isinstance(content, dict):
            loaded_url = content.get("url", "")
            title = content.get("title", "")
            if url and loaded_url and url.rstrip("/") in loaded_url:
                return VerificationResult(True, 0.9, f"Browser loaded: {loaded_url}")
            if title:
                return VerificationResult(True, 0.7, f"Page has title: {title}")
    return VerificationResult(True, 0.4, "Browser state check limited.")


_BUILTIN_VERIFIERS: list[tuple[str, Verifier]] = [
    ("file_write", _verify_file_write),
    ("file_append", _verify_file_write),
    ("file_create", _verify_file_write),
    ("file_save", _verify_file_write),
    ("file_delete", _verify_file_delete),
    ("file_remove", _verify_file_delete),
    ("dir_create", _verify_dir_create),
    ("dir_make", _verify_dir_create),
    ("shell_run", _verify_shell),
    ("shell_exec", _verify_shell),
    ("app_launch", _verify_process_launch),
    ("app_open", _verify_process_launch),
    ("browser_open", _verify_browser),
    ("browser_navigate", _verify_browser),
]


class OutcomeVerifier:
    """Verifies tool execution outcomes against expectations."""

    def __init__(self) -> None:
        self._verifiers: list[tuple[str, Verifier]] = list(_BUILTIN_VERIFIERS)
        self._last_result: VerificationResult | None = None
        self._last_tool: str = ""
        self._last_args: dict[str, Any] = {}
        self._history: list[dict[str, Any]] = []

    def register_verifier(self, pattern: str, fn: Verifier) -> None:
        self._verifiers.append((pattern, fn))

    async def verify(self, tool_name: str, args: dict[str, Any], result: Any,
                     intent: str | None = None) -> VerificationResult:
        self._last_tool = tool_name
        self._last_args = args

        for pattern, verifier in self._verifiers:
            if fnmatch.fnmatch(tool_name, pattern) or pattern in tool_name:
                try:
                    vr = await asyncio.wait_for(verifier(tool_name, args, result), timeout=2.0)
                except asyncio.TimeoutError:
                    vr = VerificationResult(True, 0.2, "Verification timed out.")
                except Exception as exc:
                    log.debug("verifier for %s failed: %s", tool_name, exc)
                    vr = VerificationResult(True, 0.2, f"Verification error: {exc}")
                self._last_result = vr
                self._history.append({
                    "tool": tool_name, "verified": vr.verified,
                    "confidence": vr.confidence, "evidence": vr.evidence,
                })
                if len(self._history) > 100:
                    self._history = self._history[-50:]
                return vr

        vr = VerificationResult(True, 0.1, "No verifier registered for this tool.")
        self._last_result = vr
        return vr

    @property
    def last_result(self) -> VerificationResult | None:
        return self._last_result

    def stats(self) -> dict[str, Any]:
        if not self._history:
            return {"total": 0, "verified": 0, "failed": 0, "avg_confidence": 0}
        verified = sum(1 for h in self._history if h["verified"])
        failed = sum(1 for h in self._history if not h["verified"])
        avg_conf = sum(h["confidence"] for h in self._history) / len(self._history)
        return {
            "total": len(self._history),
            "verified": verified,
            "failed": failed,
            "avg_confidence": round(avg_conf, 3),
            "recent": self._history[-5:],
        }
