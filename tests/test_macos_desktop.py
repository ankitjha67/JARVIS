"""Drives a real macOS desktop: opens TextEdit, moves its window, types into it, reads its controls, and
starts the HUD window.

Off by default because it takes over the screen and keyboard for a minute. CI turns it on with
JARVIS_DESKTOP_TESTS=1; to run it on your own Mac:

    JARVIS_DESKTOP_TESTS=1 uv run pytest tests/test_macos_desktop.py -v
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("JARVIS_DESKTOP_TESTS") != "1",
    reason="needs a macOS desktop session and JARVIS_DESKTOP_TESTS=1",
)

from jarvis import host  # noqa: E402
from jarvis.host import _macos as mac  # noqa: E402
from jarvis.tools.registry import REGISTRY  # noqa: E402


def _wait(predicate, timeout: float = 30.0, every: float = 0.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(every)
    return None


def _textedit_windows() -> list[dict]:
    rows, _ = mac.list_windows()
    return [w for w in rows if w["process"] == "TextEdit"]


def _document_text() -> str:
    return mac.osascript('tell application "TextEdit" to get text of front document')


@pytest.fixture(scope="module")
def textedit():
    if mac.accessibility_granted() is False:
        pytest.skip("Accessibility permission is not granted to this process")
    mac.osascript('tell application "TextEdit"\nactivate\nmake new document\nend tell', timeout=60)
    assert _wait(_textedit_windows, timeout=60), "TextEdit never showed a window"
    yield
    mac.osascript('tell application "TextEdit" to quit saving no', timeout=30)


def test_window_list_shows_the_app_with_full_detail(textedit):
    rows, note = mac.list_windows()
    assert note == "", "the Accessibility list was expected, not the fallback"
    win = next(w for w in rows if w["process"] == "TextEdit")
    assert win["id"] == win["pid"] * 1000 + 1 and win["rect"][2] > 100 and win["rect"][3] > 100
    assert not win["minimized"]


def test_focus_makes_it_the_foreground_app(textedit):
    mac.window_action(_textedit_windows()[0], "focus")
    assert _wait(lambda: (host.foreground_app() or {}).get("process") == "TextEdit", timeout=10)


def test_minimize_and_restore_round_trip(textedit):
    win = _textedit_windows()[0]
    mac.window_action(win, "minimize")
    assert _wait(lambda: any(w["minimized"] for w in _textedit_windows()), timeout=15), "window did not minimize"
    mac.window_action(win, "restore")
    assert _wait(lambda: not any(w["minimized"] for w in _textedit_windows()), timeout=15), "window did not restore"
    mac.window_action(_textedit_windows()[0], "focus")


def test_typing_reaches_the_document(textedit):
    mac.window_action(_textedit_windows()[0], "focus")
    time.sleep(0.5)
    assert mac.type_text("Hello from Jarvis", clear=True) == "TextEdit"
    assert _wait(lambda: _document_text() == "Hello from Jarvis", timeout=10), _document_text()


def test_typing_non_ascii_and_multiple_lines_keeps_the_clipboard(textedit):
    host.clipboard_set("keep me")
    mac.window_action(_textedit_windows()[0], "focus")
    time.sleep(0.5)
    mac.type_text("café ✓ 日本", clear=True)
    assert _wait(lambda: _document_text() == "café ✓ 日本", timeout=10), _document_text()
    assert host.clipboard_get() == "keep me"
    mac.type_text("one\ntwo", clear=True)
    assert _wait(lambda: _document_text() == "one\ntwo", timeout=10), repr(_document_text())


def test_shortcut_selects_all_so_typing_replaces(textedit):
    mac.window_action(_textedit_windows()[0], "focus")
    time.sleep(0.5)
    mac.type_text("first", clear=True)
    mac.send_hotkey("cmd+a")
    mac.type_text("second")
    assert _wait(lambda: _document_text() == "second", timeout=10), _document_text()


def test_snapshot_lists_controls_and_a_click_lands(textedit):
    mac.window_action(_textedit_windows()[0], "focus")
    time.sleep(0.5)
    mac.type_text("snapshot me", clear=True)
    snap = mac.ui_snapshot()
    assert snap["app"] == "TextEdit"
    edits = [e for e in snap["elements"] if e["type"] == "edit"]
    assert edits, f"no text area among {[e['type'] for e in snap['elements']]}"
    assert any("snapshot me" in str(e.get("value", "")) for e in edits)
    mac.click_at(edits[0]["x"], edits[0]["y"])
    assert (host.foreground_app() or {}).get("process") == "TextEdit"


async def test_the_tools_themselves_work_end_to_end(textedit):
    listing = await REGISTRY["windows_list"].handler(REGISTRY["windows_list"].args_model())
    rows = listing["windows"] if isinstance(listing, dict) else listing
    assert any(w["process"] == "TextEdit" for w in rows)
    focus = REGISTRY["window_focus"]
    assert "focused" in await focus.handler(focus.args_model(title_contains="TextEdit"))
    snapshot = await REGISTRY["ui_snapshot"].handler(REGISTRY["ui_snapshot"].args_model())
    ref = next(e["ref"] for e in snapshot["elements"] if e["type"] == "edit")
    typed = await REGISTRY["ui_type"].handler(REGISTRY["ui_type"].args_model(text="via tools", ref=ref, clear=True))
    assert typed["typed_chars"] == 9
    assert _wait(lambda: _document_text() == "via tools", timeout=10), _document_text()
    hotkey = REGISTRY["ui_hotkey"]
    assert (await hotkey.handler(hotkey.args_model(keys="cmd+a")))["sent"] == "cmd+a"
    apps = await REGISTRY["apps_search"].handler(REGISTRY["apps_search"].args_model(name="textedit"))
    assert "TextEdit" in apps


def test_volume_can_be_set_and_read_back():
    before = mac.volume(None, 0, None)
    try:
        assert mac.volume(20, None, None)["volume_percent"] == 20
        assert mac.volume(None, 10, None)["volume_percent"] == 30
        assert mac.volume(None, None, True)["muted"] is True
    finally:
        mac.volume(before["volume_percent"], None, before["muted"])


def test_startup_audit_and_spotlight_and_services_answer():
    assert isinstance(mac.startup_items(), list)
    assert isinstance(mac.find_files("TextEdit", 5), list)
    assert mac.service_status("com.apple.Finder")["state"] in ("RUNNING", "LOADED_NOT_RUNNING")
    assert isinstance(mac.recent_errors(5), list)


def test_the_hud_window_opens(tmp_path):
    """Start the whole app and wait for its window; proves WKWebView, the frameless window and hotkeys boot."""
    from jarvis.config import UI_DIST

    if not (UI_DIST / "index.html").exists():
        pytest.skip("the HUD is not built")
    env = {**os.environ, "JARVIS_PORT": "47899", "JARVIS_DATA_DIR": str(tmp_path / "data")}
    log = tmp_path / "jarvis.out"
    with log.open("w") as out:
        proc = subprocess.Popen([sys.executable, "-m", "jarvis"], env=env, stdout=out, stderr=subprocess.STDOUT)
    try:
        def healthy() -> bool:
            try:
                with urllib.request.urlopen("http://127.0.0.1:47899/api/health", timeout=2) as r:
                    return r.status == 200
            except OSError:
                return False

        assert _wait(healthy, timeout=60), f"core did not start:\n{log.read_text()[-2000:]}"
        window = _wait(lambda: [w for w in mac.visible_windows() if w["pid"] == proc.pid], timeout=45)
        assert window, f"no HUD window appeared:\n{log.read_text()[-2000:]}"
        assert window[0]["rect"][2] >= 1000 and window[0]["rect"][3] >= 600
        assert proc.poll() is None, f"Jarvis exited early:\n{log.read_text()[-2000:]}"
        assert "could not be registered" not in log.read_text()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
