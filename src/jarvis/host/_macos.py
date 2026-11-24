"""macOS implementations.

Built on tools that ship with macOS (osascript, pbcopy, afplay, say, open, pmset, networksetup, mdfind,
launchctl, log) and on PyObjC, which pywebview already installs. Scripts are fed to osascript on stdin and
receive every model- or user-supplied value as an argument, never interpolated into the script text (SEC-7).

Controlling other apps needs the user's consent in System Settings → Privacy & Security; when macOS
refuses, the error says exactly which switch to turn on.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from ..errors import ToolError

ACCESSIBILITY_HELP = (
    "macOS has not allowed Jarvis to control other apps. Open System Settings → Privacy & Security → "
    "Accessibility, switch on the app you start Jarvis from (Terminal, iTerm, …), then try again."
)
AUTOMATION_HELP = (
    "macOS blocked Jarvis from sending commands to another app. Open System Settings → Privacy & Security → "
    "Automation and allow the app you start Jarvis from to control “System Events”, then try again."
)
SCREEN_HELP = (
    "macOS has not allowed Jarvis to see the screen. Open System Settings → Privacy & Security → "
    "Screen & System Audio Recording, switch on the app you start Jarvis from, then restart Jarvis."
)


def _env() -> dict[str, str]:
    # pbcopy/pbpaste/say only treat text as UTF-8 under a UTF-8 locale.
    return {**os.environ, "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8"}


def _run(argv: list[str], *, text_in: str | None = None, timeout: float = 15) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(argv, input=text_in, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, check=False, env=_env(),
                              stdin=subprocess.DEVNULL if text_in is None else None)
    except FileNotFoundError as exc:
        raise ToolError(f"{Path(argv[0]).name} is not available on this Mac.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"{Path(argv[0]).name} did not answer within {int(timeout)} s.") from exc


def osascript(script: str, *args: str, jxa: bool = False, timeout: float = 20) -> str:
    """Run AppleScript (or JavaScript for Automation) and return what it returned."""
    argv = ["/usr/bin/osascript", *(["-l", "JavaScript"] if jxa else []), "-", *args]
    done = _run(argv, text_in=script, timeout=timeout)
    if done.returncode != 0:
        err = (done.stderr or "").strip()
        low = err.lower()
        if "assistive access" in low or "-1719" in low or "-25211" in low or "not allowed to send keystrokes" in low:
            raise ToolError(ACCESSIBILITY_HELP)
        if "-1743" in low or "not authorized to send apple events" in low or "not authorised" in low:
            raise ToolError(AUTOMATION_HELP)
        raise ToolError(f"macOS refused the request: {err[-240:] or 'no details given'}")
    return done.stdout.rstrip("\n")


# -- permissions ------------------------------------------------------------------------------------

def accessibility_granted() -> bool | None:
    """Whether this process may post input events. None when it cannot be determined."""
    try:
        import Quartz

        check = getattr(Quartz, "CGPreflightPostEventAccess", None)
        return bool(check()) if check else None
    except Exception:  # noqa: BLE001
        return None


def screen_recording_granted() -> bool | None:
    try:
        import Quartz

        check = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
        return bool(check()) if check else None
    except Exception:  # noqa: BLE001
        return None


def _require_accessibility() -> None:
    if accessibility_granted() is False:
        raise ToolError(ACCESSIBILITY_HELP)


# -- opening, clipboard, sound ----------------------------------------------------------------------

def open_target(target: str) -> None:
    done = _run(["/usr/bin/open", target])
    if done.returncode != 0:
        raise OSError((done.stderr or "open failed").strip()[:200])


def clipboard_get() -> str | None:
    done = _run(["/usr/bin/pbpaste"], timeout=5)
    return done.stdout if done.returncode == 0 and done.stdout else None


def clipboard_set(text: str) -> None:
    done = _run(["/usr/bin/pbcopy"], text_in=text, timeout=5)
    if done.returncode != 0:
        raise ToolError("The clipboard could not be changed.")


def clipboard_change_count() -> int | None:
    try:
        from AppKit import NSPasteboard

        return int(NSPasteboard.generalPasteboard().changeCount())
    except Exception:  # noqa: BLE001
        return None


def play_wav(wav: bytes) -> None:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(wav)
        path = tmp.name
    try:
        _run(["/usr/bin/afplay", path], timeout=180)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# -- speech -----------------------------------------------------------------------------------------

_MALE = {"alex", "daniel", "fred", "tom", "aaron", "arthur", "gordon", "oliver", "albert", "bruce", "junior",
         "ralph", "reed", "rocko", "eddy", "grandpa", "rishi", "evan", "nathan", "lee", "jamie"}
# "Daniel              en_GB    # Hello…"; long names such as "Eddy (English (UK))" leave a single space.
_VOICE_LINE = re.compile(r"^(?P<name>.+?)\s+(?P<lang>[a-z]{2,3}[_-][A-Z][A-Za-z0-9]{1,3})\s+#")


def list_voices() -> list[dict[str, str]]:
    done = _run(["/usr/bin/say", "-v", "?"], timeout=10)
    voices: dict[str, dict[str, str]] = {}
    for line in done.stdout.splitlines():
        m = _VOICE_LINE.match(line)
        if not m or not m["lang"].lower().startswith("en"):
            continue
        name = m["name"].strip()
        first = re.split(r"[ (]", name, maxsplit=1)[0].lower()
        voices.setdefault(name, {"name": name, "gender": "male" if first in _MALE else "", "language": m["lang"]})
    return sorted(voices.values(), key=lambda v: (v["gender"] != "male", v["name"]))


def speak(text: str, voice: str = "") -> None:
    _run(["/usr/bin/say", *(["-v", voice] if voice else [])], text_in=text, timeout=120)


# -- session ----------------------------------------------------------------------------------------

def lock_screen() -> None:
    try:
        import ctypes

        login = ctypes.CDLL("/System/Library/PrivateFrameworks/login.framework/Versions/Current/login")
        login.SACLockScreenImmediate()
        return
    except Exception:  # noqa: BLE001 - private framework: fall back to putting the display to sleep
        pass
    done = _run(["/usr/bin/pmset", "displaysleepnow"])
    if done.returncode != 0:
        raise ToolError("macOS refused to lock the screen.")


_POWER_EVENTS = {"restart": "aevtrrst", "shutdown": "aevtrsdn", "sign_out": "aevtlogo"}


def power(action: str) -> dict[str, Any]:
    if action == "sleep":
        done = _run(["/usr/bin/pmset", "sleepnow"])
        if done.returncode != 0:
            raise ToolError((done.stderr or "macOS refused to sleep.").strip()[:200])
        return {"action": action, "delay_s": 0}
    if action == "cancel":
        raise ToolError("On macOS the restart or shutdown is cancelled in the system dialog that is on screen "
                        "(press Cancel there).")
    # loginwindow shows the standard dialog with a 60-second countdown, so the user can still say no.
    osascript(f'tell application "loginwindow" to «event {_POWER_EVENTS[action]}»')
    return {"action": action, "delay_s": 60,
            "note": "macOS is showing its confirmation dialog; it proceeds after 60 seconds unless cancelled there."}


# -- windows ----------------------------------------------------------------------------------------

def visible_windows() -> list[dict[str, Any]]:
    """On-screen application windows, front to back. Needs no permission; titles need Screen Recording."""
    import Quartz

    options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    rows: list[dict[str, Any]] = []
    for info in Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []:
        if int(info.get("kCGWindowLayer", 1)) != 0:
            continue
        bounds = info.get("kCGWindowBounds") or {}
        width, height = int(bounds.get("Width", 0)), int(bounds.get("Height", 0))
        if width < 40 or height < 40:
            continue
        rows.append({
            "pid": int(info.get("kCGWindowOwnerPID", 0)),
            "process": str(info.get("kCGWindowOwnerName") or ""),
            "title": str(info.get("kCGWindowName") or ""),
            "rect": (int(bounds.get("X", 0)), int(bounds.get("Y", 0)), width, height),
        })
    return rows


def foreground_app() -> dict[str, Any] | None:
    rows = visible_windows()
    if not rows:
        return None
    top = rows[0]
    return {"title": top["title"] or top["process"], "process": top["process"], "pid": top["pid"]}


def window_rect(title: str = "") -> tuple[int, int, int, int] | None:
    """(left, top, width, height) of the front window, or of the first window whose title or app matches."""
    rows = visible_windows()
    if title:
        q = title.lower()
        rows = [r for r in rows if q in r["title"].lower() or q in r["process"].lower()]
    return rows[0]["rect"] if rows else None


_LIST_WINDOWS_JXA = r"""
function run(argv) {
  const se = Application('System Events');
  if (!se.uiElementsEnabled()) { return 'NO_AX'; }
  const out = [];
  const procs = se.applicationProcesses.whose({backgroundOnly: false})();
  for (const p of procs) {
    let wins, name, pid, front;
    try { name = p.name(); pid = p.unixId(); front = p.frontmost(); wins = p.windows(); } catch (e) { continue; }
    for (let i = 0; i < wins.length; i++) {
      const w = wins[i];
      let title = '', mini = false, pos = [0, 0], size = [0, 0];
      try { title = w.name() || ''; } catch (e) {}
      try { mini = !!w.attributes.byName('AXMinimized').value(); } catch (e) {}
      try { pos = w.position(); size = w.size(); } catch (e) {}
      out.push({pid: pid, process: name, index: i + 1, title: title, minimized: mini,
                focused: front && i === 0, rect: [pos[0], pos[1], size[0], size[1]]});
    }
  }
  return JSON.stringify(out);
}
"""

_WINDOW_ID_BASE = 1000  # id = pid * 1000 + the window's position within its app


def list_windows() -> tuple[list[dict[str, Any]], str]:
    """(rows, note). Rows carry id, title, process, pid, minimized, focused and rect."""
    raw = osascript(_LIST_WINDOWS_JXA, jxa=True, timeout=25)
    if raw.strip() == "NO_AX":
        counts: dict[int, int] = {}
        rows = []
        for n, r in enumerate(visible_windows()):
            counts[r["pid"]] = counts.get(r["pid"], 0) + 1
            rows.append({"id": r["pid"] * _WINDOW_ID_BASE + counts[r["pid"]], "title": r["title"] or r["process"],
                         "process": r["process"], "pid": r["pid"], "minimized": False, "focused": n == 0,
                         "rect": list(r["rect"])})
        return rows, ("Limited list: macOS Accessibility permission is off, so minimized windows are missing "
                      "and windows cannot be focused, resized or closed. " + ACCESSIBILITY_HELP)
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        raise ToolError("macOS returned an unreadable window list.") from exc
    rows = [{"id": int(d["pid"]) * _WINDOW_ID_BASE + int(d["index"]), "title": str(d.get("title") or d["process"]),
             "process": str(d["process"]), "pid": int(d["pid"]), "minimized": bool(d.get("minimized")),
             "focused": bool(d.get("focused")), "rect": [int(v) for v in d.get("rect", [0, 0, 0, 0])]}
            for d in data]
    return rows, ""


_WINDOW_ACTION = """
on run argv
  set thePid to (item 1 of argv) as integer
  set theIndex to (item 2 of argv) as integer
  set theTitle to item 3 of argv
  set theAction to item 4 of argv
  tell application "System Events"
    set p to first application process whose unix id is thePid
    set w to missing value
    if theTitle is not "" then
      try
        set w to first window of p whose name is theTitle
      end try
    end if
    if w is missing value then set w to window theIndex of p
    if theAction is "focus" then
      set value of attribute "AXMinimized" of w to false
      set frontmost of p to true
      perform action "AXRaise" of w
    else if theAction is "minimize" then
      set value of attribute "AXMinimized" of w to true
    else if theAction is "restore" then
      set value of attribute "AXMinimized" of w to false
    else if theAction is "maximize" then
      click (first button of w whose subrole is "AXZoomButton")
    else if theAction is "close" then
      click (first button of w whose subrole is "AXCloseButton")
    end if
  end tell
  return "ok"
end run
"""


def window_action(win: dict[str, Any], action: str) -> None:
    """action: focus | minimize | maximize | restore | close. `win` is a row from list_windows."""
    index = int(win["id"]) % _WINDOW_ID_BASE
    title = "" if win["title"] == win["process"] else str(win["title"])
    osascript(_WINDOW_ACTION, str(win["pid"]), str(index), title, action)


# -- apps -------------------------------------------------------------------------------------------

_APP_DIRS = ("/Applications", "/Applications/Utilities", "/System/Applications",
             "/System/Applications/Utilities", "/System/Library/CoreServices/Applications", "~/Applications")


def list_apps() -> list[dict[str, str]]:
    seen: dict[str, dict[str, str]] = {}
    for raw in _APP_DIRS:
        root = Path(raw).expanduser()
        try:
            entries = sorted(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            bundles = [entry] if entry.suffix == ".app" else []
            if not bundles and entry.is_dir() and not entry.name.startswith("."):
                try:  # one level of vendor folders, e.g. /Applications/Adobe Photoshop 2025/
                    bundles = [p for p in entry.iterdir() if p.suffix == ".app"]
                except OSError:
                    bundles = []
            for bundle in bundles:
                seen.setdefault(bundle.stem.lower(), {"name": bundle.stem, "app_id": str(bundle)})
    finder = Path("/System/Library/CoreServices/Finder.app")
    if finder.exists():
        seen.setdefault("finder", {"name": "Finder", "app_id": str(finder)})
    return list(seen.values())


def launch_app(app_id: str) -> None:
    done = _run(["/usr/bin/open", "-a", app_id])
    if done.returncode != 0:
        raise ToolError(f"macOS could not open that app: {(done.stderr or '').strip()[:160]}")


# -- volume and media keys --------------------------------------------------------------------------

def _volume_state() -> tuple[int | None, bool]:
    raw = osascript('set s to get volume settings\n'
                    'return ((output volume of s) as text) & "," & ((output muted of s) as text)')
    level, _, muted = raw.partition(",")
    return (int(level) if level.strip().isdigit() else None), muted.strip().lower() == "true"


def volume(percent: int | None, change: int | None, mute: bool | None) -> dict[str, Any]:
    current, _ = _volume_state()
    if current is None:
        raise ToolError("The current audio output has no volume control (for example HDMI or some USB devices).")
    target = percent if percent is not None else (current + change if change is not None else None)
    if target is not None:
        osascript(f"set volume output volume {max(0, min(100, int(target)))}")
    if mute is not None:
        osascript(f"set volume output muted {'true' if mute else 'false'}")
    level, muted = _volume_state()
    return {"volume_percent": level, "muted": muted}


_MEDIA_KEYS = {"play_pause": 16, "next": 17, "previous": 18}  # NX_KEYTYPE_PLAY / NEXT / PREVIOUS


def media_key(action: str) -> None:
    if action not in _MEDIA_KEYS:
        raise ToolError("macOS has no separate stop key; use play_pause.")
    _require_accessibility()
    try:
        import Quartz
        from AppKit import NSEvent
    except ImportError as exc:
        raise ToolError("Media keys need PyObjC, which is missing from this install.") from exc
    key = _MEDIA_KEYS[action]
    for down in (True, False):
        flags = 0xA00 if down else 0xB00
        data = (key << 16) | ((0xA if down else 0xB) << 8)
        event = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            14, (0, 0), flags, 0, 0, None, 8, data, -1)  # 14 = NSEventTypeSystemDefined, subtype 8 = media key
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event.CGEvent())


# -- keyboard and mouse -----------------------------------------------------------------------------

_TYPE_SCRIPT = """
on run argv
  set doClear to item 1 of argv
  set doEnter to item 2 of argv
  set usePaste to item 3 of argv
  set theLines to rest of rest of rest of argv
  tell application "System Events"
    set p to first application process whose frontmost is true
    try
      set el to value of attribute "AXFocusedUIElement" of p
      if (value of attribute "AXSubrole" of el) is "AXSecureTextField" then return "SECURE"
    end try
    if doClear is "1" then
      keystroke "a" using {command down}
      key code 51
    end if
    if usePaste is "1" then
      keystroke "v" using {command down}
    else
      set n to count of theLines
      repeat with i from 1 to n
        if (item i of theLines) is not "" then keystroke (item i of theLines)
        if i < n then key code 36
      end repeat
    end if
    if doEnter is "1" then
      delay 0.15
      key code 36
    end if
    return name of p
  end tell
end run
"""


def type_text(text: str, *, clear: bool = False, enter: bool = False) -> str:
    """Type at the cursor of the frontmost app. Returns the app's name. Refuses password fields (SEC-11)."""
    _require_accessibility()
    # Keystrokes can only produce characters on the current keyboard layout, and are slow for long text:
    # anything else goes through the clipboard, which is put back afterwards.
    paste = (not text.isascii()) or len(text) > 400
    previous: str | None = None
    if paste:
        previous = clipboard_get()
        clipboard_set(text)
    try:
        result = osascript(_TYPE_SCRIPT, "1" if clear else "0", "1" if enter else "0", "1" if paste else "0",
                           *([] if paste else text.split("\n")), timeout=90)
        if paste:
            time.sleep(0.3)  # let the app read the clipboard before it is restored
    finally:
        if paste and previous is not None:
            try:
                clipboard_set(previous)
            except ToolError:
                pass
    if result == "SECURE":
        raise ToolError("That is a password field. Jarvis does not type passwords — enter it yourself.")
    return result


_MODIFIERS = {"cmd": "command", "command": "command", "win": "command", "meta": "command", "super": "command",
              "ctrl": "control", "control": "control", "alt": "option", "option": "option", "opt": "option",
              "shift": "shift"}
_KEY_CODES = {
    "enter": 36, "return": 36, "tab": 48, "space": 49, "backspace": 51, "esc": 53, "escape": 53,
    "delete": 117, "del": 117, "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100, "f9": 101,
    "f10": 109, "f11": 103, "f12": 111,
}


def parse_hotkey(keys: str) -> tuple[list[str], str, str]:
    """'cmd+shift+s' → (['command', 'shift'], 'char', 's'); 'ctrl+f5' → (['control'], 'code', '96')."""
    mods: list[str] = []
    main: tuple[str, str] | None = None
    for part in keys.lower().replace(" ", "").split("+"):
        if part in _MODIFIERS:
            if _MODIFIERS[part] not in mods:
                mods.append(_MODIFIERS[part])
            continue
        if part in _KEY_CODES:
            key = ("code", str(_KEY_CODES[part]))
        elif len(part) == 1 and part.isalnum():
            key = ("char", part)
        else:
            raise ToolError(f"Unknown key “{part}”. Use names like cmd, ctrl, option, shift, enter, tab, f5 "
                            "or a letter.")
        if main is not None:
            raise ToolError("Give one key plus modifiers, for example cmd+shift+s.")
        main = key
    if main is None:
        raise ToolError("A shortcut needs a key as well as modifiers, for example cmd+s.")
    return mods, main[0], main[1]


_HOTKEY_SCRIPT = """
on run argv
  set kind to item 1 of argv
  set val to item 2 of argv
  tell application "System Events"
    set mods to {}
    repeat with m in (rest of rest of argv)
      set m to m as text
      if m is "command" then set end of mods to command down
      if m is "control" then set end of mods to control down
      if m is "option" then set end of mods to option down
      if m is "shift" then set end of mods to shift down
    end repeat
    if kind is "code" then
      if (count of mods) is 0 then
        key code (val as integer)
      else
        key code (val as integer) using mods
      end if
    else
      if (count of mods) is 0 then
        keystroke val
      else
        keystroke val using mods
      end if
    end if
  end tell
  return "ok"
end run
"""


def send_hotkey(keys: str) -> None:
    mods, kind, value = parse_hotkey(keys)
    _require_accessibility()
    osascript(_HOTKEY_SCRIPT, kind, value, *mods)


def click_at(x: float, y: float, *, right: bool = False, double: bool = False) -> None:
    _require_accessibility()
    import Quartz

    if right:
        down, up, button = Quartz.kCGEventRightMouseDown, Quartz.kCGEventRightMouseUp, Quartz.kCGMouseButtonRight
    else:
        down, up, button = Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp, Quartz.kCGMouseButtonLeft
    for click in range(2 if double else 1):
        for kind in (down, up):
            event = Quartz.CGEventCreateMouseEvent(None, kind, (float(x), float(y)), button)
            Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventClickState, click + 1)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        time.sleep(0.06)


_SNAPSHOT_JXA = r"""
function run(argv) {
  const se = Application('System Events');
  if (!se.uiElementsEnabled()) { return 'NO_AX'; }
  const kinds = {AXButton: 'button', AXMenuButton: 'button', AXTextField: 'edit', AXTextArea: 'edit',
    AXSearchField: 'edit', AXComboBox: 'combo', AXPopUpButton: 'combo', AXMenuItem: 'menuitem',
    AXCheckBox: 'checkbox', AXRadioButton: 'radio', AXLink: 'link', AXSlider: 'slider', AXRow: 'row',
    AXIncrementor: 'spinner', AXDisclosureTriangle: 'button'};
  const limit = parseInt(argv[0], 10), deadline = Date.now() + 7000;
  const p = se.applicationProcesses.whose({frontmost: true})[0];
  const app = p.name();
  let w;
  try { w = p.windows[0]; w.name(); } catch (e) { return JSON.stringify({app: app, window: '', elements: [], texts: []}); }
  let title = '';
  try { title = w.name() || ''; } catch (e) {}
  let all = [];
  try { all = w.entireContents(); } catch (e) {}
  const elements = [], texts = [];
  let truncated = false;
  for (const el of all) {
    if (Date.now() > deadline || elements.length >= limit) { truncated = true; break; }
    let role;
    try { role = el.role(); } catch (e) { continue; }
    if (role === 'AXStaticText') {
      if (texts.length < 60) { try { const t = el.value(); if (t) { texts.push(String(t).slice(0, 200)); } } catch (e) {} }
      continue;
    }
    const kind = kinds[role];
    if (!kind) { continue; }
    let name = '', pos, size;
    try { pos = el.position(); size = el.size(); } catch (e) { continue; }
    if (!pos || !size || size[0] <= 0 || size[1] <= 0) { continue; }
    try { name = el.name() || el.description() || el.title() || ''; } catch (e) {}
    const item = {type: kind, name: String(name).slice(0, 120), x: pos[0] + size[0] / 2, y: pos[1] + size[1] / 2};
    try { if (!el.enabled()) { item.disabled = true; } } catch (e) {}
    if (kind === 'edit' || kind === 'combo') {
      let secure = false;
      try { secure = el.subrole() === 'AXSecureTextField'; } catch (e) {}
      if (secure) { item.password_field = true; }
      else { try { const v = el.value(); if (v) { item.value = String(v).slice(0, 200); } } catch (e) {} }
    }
    if (!item.name && !item.value && kind !== 'edit' && kind !== 'combo') { continue; }
    elements.push(item);
  }
  return JSON.stringify({app: app, window: title, elements: elements, texts: texts, truncated: truncated});
}
"""


def ui_snapshot(limit: int = 140) -> dict[str, Any]:
    """Controls of the frontmost window, each with the screen point at its centre (x, y)."""
    raw = osascript(_SNAPSHOT_JXA, str(limit), jxa=True, timeout=40)
    if raw.strip() == "NO_AX":
        raise ToolError(ACCESSIBILITY_HELP)
    try:
        return dict(json.loads(raw))
    except json.JSONDecodeError as exc:
        raise ToolError("macOS returned an unreadable control list.") from exc


# -- settings ---------------------------------------------------------------------------------------

_PANES = {
    "wifi": "com.apple.wifi-settings-extension", "network": "com.apple.Network-Settings.extension",
    "bluetooth": "com.apple.BluetoothSettings", "display": "com.apple.Displays-Settings.extension",
    "night_light": "com.apple.Displays-Settings.extension", "nightlight": "com.apple.Displays-Settings.extension",
    "sound": "com.apple.Sound-Settings.extension", "notifications": "com.apple.Notifications-Settings.extension",
    "focus": "com.apple.Focus-Settings.extension", "power": "com.apple.Battery-Settings.extension",
    "battery": "com.apple.Battery-Settings.extension", "storage": "com.apple.settings.Storage",
    "update": "com.apple.Software-Update-Settings.extension",
    "privacy": "com.apple.settings.PrivacySecurity.extension",
    "accounts": "com.apple.Users-Groups-Settings.extension", "date_time": "com.apple.Date-Time-Settings.extension",
    "language": "com.apple.Localization-Settings.extension", "mouse": "com.apple.Mouse-Settings.extension",
    "keyboard": "com.apple.Keyboard-Settings.extension",
    "personalization": "com.apple.Appearance-Settings.extension", "about": "com.apple.SystemProfiler.AboutExtension",
    "vpn": "com.apple.NetworkExtensionSettingsUI.NESettingsUIExtension",
    "printers": "com.apple.Print-Scan-Settings.extension",
    "startup_apps": "com.apple.LoginItems-Settings.extension",
}


def settings_uri(page: str) -> str:
    """The System Settings link for a page name shared with Windows; unknown pages open the main window."""
    return "x-apple.systempreferences:" + _PANES.get(page, "")


def _wifi_device() -> str:
    done = _run(["/usr/sbin/networksetup", "-listallhardwareports"])
    match = re.search(r"Hardware Port: (?:Wi-Fi|AirPort)\s*\nDevice: (\S+)", done.stdout)
    if not match:
        raise ToolError("This Mac has no Wi-Fi adapter.")
    return match.group(1)


def set_wifi(enable: bool) -> None:
    done = _run(["/usr/sbin/networksetup", "-setairportpower", _wifi_device(), "on" if enable else "off"])
    if done.returncode != 0 or "error" in done.stdout.lower():
        raise ToolError(f"Could not switch Wi-Fi: {(done.stderr or done.stdout).strip()[:200]}")


def _brew_tool(name: str, purpose: str) -> str:
    path = shutil.which(name) or next((p for p in (f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}")
                                       if os.path.exists(p)), None)
    if not path:
        raise ToolError(f"macOS has no built-in command for {purpose}. Install the small helper with "
                        f"“brew install {name}”, or change it in System Settings.")
    return path


def set_bluetooth(enable: bool) -> None:
    done = _run([_brew_tool("blueutil", "switching Bluetooth"), "--power", "1" if enable else "0"])
    if done.returncode != 0:
        raise ToolError(f"Could not switch Bluetooth: {(done.stderr or done.stdout).strip()[:200]}")


def set_brightness(percent: int) -> None:
    done = _run([_brew_tool("brightness", "setting display brightness"), f"{max(0, min(100, percent)) / 100:.2f}"])
    if done.returncode != 0:
        raise ToolError(f"Could not set brightness: {(done.stderr or done.stdout).strip()[:200]}")


def get_brightness() -> int:
    done = _run([_brew_tool("brightness", "reading display brightness"), "-l"])
    match = re.search(r"brightness\s+([0-9.]+)", done.stdout)
    if not match:
        raise ToolError("This display does not report its brightness.")
    return round(float(match.group(1)) * 100)


# -- search, diagnostics ----------------------------------------------------------------------------

def find_files(query: str, max_results: int, root: str = "") -> list[str]:
    """Spotlight file-name search."""
    argv = ["/usr/bin/mdfind", *(["-onlyin", root] if root else []), "-name", query]
    done = _run(argv, timeout=15)
    return [line for line in done.stdout.splitlines() if line.strip()][:max_results]


def recent_errors(count: int) -> list[str]:
    """The newest error and fault lines of the unified log."""
    try:
        done = _run(["/usr/bin/log", "show", "--last", "5m", "--style", "compact", "--predicate",
                     "messageType == 16 OR messageType == 17"], timeout=25)
    except ToolError:
        return ["(the system log did not answer in time)"]
    lines = [line.strip()[:300] for line in done.stdout.splitlines() if line.strip() and not line.startswith("Timestamp")]
    return lines[-count:]


def service_status(name: str) -> dict[str, Any]:
    """A launchd job of the current user (as shown by `launchctl list`)."""
    done = _run(["/bin/launchctl", "list", name])
    if done.returncode != 0:
        raise ToolError(f"No launchd service named '{name}' is loaded for this user.")
    pid = re.search(r'"PID"\s*=\s*(\d+)', done.stdout)
    status = re.search(r'"LastExitStatus"\s*=\s*(-?\d+)', done.stdout)
    return {"service": name, "state": "RUNNING" if pid else "LOADED_NOT_RUNNING",
            "pid": int(pid.group(1)) if pid else None,
            "last_exit_status": int(status.group(1)) if status else None, "raw": done.stdout.strip()[:1000]}


_LAUNCH_DIRS = (("~/Library/LaunchAgents", "user"), ("/Library/LaunchAgents", "machine"),
                ("/Library/LaunchDaemons", "machine"))


def startup_items() -> list[dict[str, str]]:
    """Login items plus launchd agents and daemons installed by third parties."""
    items: list[dict[str, str]] = []
    try:
        names = osascript('tell application "System Events" to get the name of every login item', timeout=10)
        items += [{"name": n.strip(), "command": "(login item)", "scope": "user"} for n in names.split(",") if n.strip()]
    except ToolError:
        pass  # Automation permission not granted: launchd entries are still listed
    for raw, scope in _LAUNCH_DIRS:
        folder = Path(raw).expanduser()
        try:
            plists = sorted(folder.glob("*.plist"))
        except OSError:
            continue
        for plist in plists:
            try:
                with plist.open("rb") as fh:
                    data = plistlib.load(fh)
            except Exception:  # noqa: BLE001 - unreadable or malformed entries are skipped
                continue
            args = data.get("ProgramArguments") or ([data["Program"]] if data.get("Program") else [])
            items.append({"name": str(data.get("Label") or plist.stem),
                          "command": " ".join(str(a) for a in args)[:300], "scope": scope})
    return items
