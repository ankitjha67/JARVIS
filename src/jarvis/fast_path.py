"""Fast-path intent matcher: skip the LLM for simple, unambiguous commands (FR-A7)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Match:
    tool: str
    args: dict[str, Any]
    spoken: str


_PATTERNS: list[tuple[re.Pattern[str], str, dict[str, Any] | None, str]] = []


def _p(pattern: str, tool: str, args: dict[str, Any] | None, spoken: str) -> None:
    _PATTERNS.append((re.compile(pattern, re.IGNORECASE), tool, args, spoken))


# Volume
_p(r"^(?:set\s+)?volume\s+(?:to\s+)?(\d{1,3})(?:\s*%)?$", "volume_set",
   None, "Setting volume to {0}%.")
_p(r"^(?:turn\s+)?(?:volume\s+)?(?:up|louder)(?:\s+(\d{1,3}))?$", "volume_set",
   None, "Turning volume up.")
_p(r"^(?:turn\s+)?(?:volume\s+)?(?:down|quieter|softer)(?:\s+(\d{1,3}))?$", "volume_set",
   None, "Turning volume down.")
_p(r"^mute$", "volume_set", {"mute": True}, "Muted.")
_p(r"^unmute$", "volume_set", {"mute": False}, "Unmuted.")

# Media playback
_p(r"^(?:play|resume|unpause)$", "media_control", {"action": "play_pause"}, "Playing.")
_p(r"^pause$", "media_control", {"action": "play_pause"}, "Paused.")
_p(r"^(?:next\s+(?:track|song)|skip)$", "media_control", {"action": "next"}, "Skipping to next track.")
_p(r"^(?:prev(?:ious)?\s+(?:track|song)|go\s+back)$", "media_control", {"action": "previous"}, "Previous track.")
_p(r"^stop\s+(?:music|playback|playing)$", "media_control", {"action": "stop"}, "Stopped playback.")

# Weather (no location = auto-detect)
_p(r"^(?:what(?:'s| is) the )?weather(?:\s+report)?$", "weather_get", {}, "Checking the weather.")
_p(r"^weather\s+(?:in|for|at)\s+(.+)$", "weather_get", None, "Checking weather for {0}.")

# Time
_p(r"^what(?:'s| is) the time\??$", "shell_run", {"command": "Get-Date -Format 'hh:mm:ss tt'"}, "Checking the time.")
_p(r"^what time is it\??$", "shell_run", {"command": "Get-Date -Format 'hh:mm:ss tt'"}, "Checking the time.")

# System
_p(r"^(?:lock|lock\s+(?:the\s+)?(?:pc|computer|screen))$", "system_lock", {}, "Locking the screen.")
_p(r"^(?:sleep|go\s+to\s+sleep|sleep\s+(?:the\s+)?(?:pc|computer))$", "system_sleep", {},
   "Putting the computer to sleep.")

# Apps
_p(r"^open\s+(.+)$", "app_launch", None, "Opening {0}.")
_p(r"^(?:launch|start|run)\s+(.+)$", "app_launch", None, "Launching {0}.")
_p(r"^close\s+(.+)$", "window_close", None, "Closing {0}.")

# Screenshot
_p(r"^(?:take\s+a\s+)?screenshot$", "screen_capture", {}, "Taking a screenshot.")

# Clipboard
_p(r"^(?:what(?:'s| is) (?:in|on) (?:my )?clipboard|show clipboard|paste)$",
   "clipboard_read", {}, "Reading clipboard.")

# Timers
_p(r"^(?:set\s+(?:a\s+)?)?timer\s+(?:for\s+)?(.+)$", "timer_set", None, "Setting timer for {0}.")
_p(r"^(?:start\s+)?stopwatch$", "stopwatch", {"action": "start"}, "Stopwatch started.")
_p(r"^stop\s+stopwatch$", "stopwatch", {"action": "stop"}, "Stopwatch stopped.")
_p(r"^lap$", "stopwatch", {"action": "lap"}, "Lap recorded.")


def _build_args(pattern_args: dict[str, Any] | None, tool: str, groups: tuple[str, ...]) -> dict[str, Any]:
    if pattern_args is not None and not groups:
        return dict(pattern_args)

    if tool == "volume_set":
        if groups and groups[0]:
            val = int(groups[0])
            return {"percent": min(val, 100)}
        if pattern_args and "mute" in pattern_args:
            return dict(pattern_args)
        return {"change": 10} if "up" in str(groups) else {"change": -10}

    if tool == "weather_get" and groups and groups[0]:
        return {"location": groups[0].strip()}

    if tool in ("app_launch", "window_close") and groups and groups[0]:
        name = groups[0].strip()
        if tool == "app_launch":
            return {"name": name}
        return {"title": name}

    if tool == "timer_set" and groups and groups[0]:
        return {"duration": groups[0].strip(), "label": "Timer"}

    return dict(pattern_args) if pattern_args else {}


def _build_spoken(template: str, groups: tuple[str, ...]) -> str:
    try:
        return template.format(*groups) if groups else template
    except (IndexError, KeyError):
        return template


def match(text: str) -> Match | None:
    text = text.strip()
    if not text or len(text) > 200:
        return None
    for pattern, tool_name, args_template, spoken_template in _PATTERNS:
        m = pattern.match(text)
        if m:
            groups = m.groups()
            args = _build_args(args_template, tool_name, groups)
            spoken = _build_spoken(spoken_template, groups)
            log.debug("fast-path matched %r -> %s(%s)", text, tool_name, args)
            return Match(tool=tool_name, args=args, spoken=spoken)
    return None
