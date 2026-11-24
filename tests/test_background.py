"""Running in the background: the setting, the sign-in entry, the status line, and the real icon window."""

from __future__ import annotations

import sys
import time

import pytest

from jarvis import tray
from jarvis.gateway import Hub, validate_settings_patch
from jarvis.store import Store


def test_background_settings_are_booleans_only():
    assert validate_settings_patch({"background": {"keep_running": False}}) == {"background": {"keep_running": False}}
    for bad in ({"keep_running": "yes"}, {"start_at_login": 1}, {"other": True}, "on"):
        with pytest.raises(ValueError):
            validate_settings_patch({"background": bad})


def test_closing_keeps_running_by_default_and_never_starts_at_sign_in_unasked():
    store = Store(":memory:")
    try:
        assert store.get_settings()["background"] == {"keep_running": True, "start_at_login": False}
        assert store.update_settings({"background": {"keep_running": False}})["background"] == {
            "keep_running": False, "start_at_login": False}
    finally:
        store.close()


async def test_status_line_says_what_the_microphone_is_doing():
    hub = Hub("t" * 32, store=Store(":memory:"))
    hub._voice_settings = {"voice": {"wake_word": False}}
    assert hub.background_status() == ("Wake word off · microphone not in use", False)
    hub._voice_settings = {"voice": {"wake_word": True}}
    hub.wake.state = "listening"
    line, on = hub.background_status()
    assert on and line.startswith("Microphone on")
    hub.wake.state = "error"
    assert "not working" in hub.background_status()[0]


def test_sign_in_command_starts_hidden_and_without_a_console():
    command = tray.login_command()
    assert command.endswith("-m jarvis --background") and command.startswith('"')
    if sys.platform == "win32":
        assert "pythonw.exe" in command.lower()


@pytest.mark.skipif(not tray.AVAILABLE, reason="the notification area is Windows only")
def test_sign_in_entry_round_trip(monkeypatch):
    monkeypatch.setattr(tray, "_RUN_NAME", "JarvisTestEntry")  # never touch the user's real entry
    try:
        assert not tray.starts_at_login()
        tray.set_start_at_login(True)
        assert tray.starts_at_login()
        tray.set_start_at_login(False)
        assert not tray.starts_at_login()
        tray.set_start_at_login(False)  # removing twice is not an error
    finally:
        tray.set_start_at_login(False)


@pytest.mark.skipif(not tray.AVAILABLE, reason="the notification area is Windows only")
def test_a_second_copy_summons_the_running_one(monkeypatch):
    monkeypatch.setattr(tray, "_CLASS", "JarvisBackgroundTest")
    monkeypatch.setattr(tray, "_TITLE", "JarvisBackgroundTest-1")
    assert not tray.summon_running_copy()                       # nothing is running yet
    opened: list[int] = []
    icon = tray.Tray("Jarvis", on_open=lambda: opened.append(1), on_quit=lambda: None,
                     on_toggle_wake=lambda on: None, status=lambda: ("Wake word off", False))
    assert icon.start() and icon.active
    try:
        assert tray.summon_running_copy()
        deadline = time.monotonic() + 3
        while not opened and time.monotonic() < deadline:
            time.sleep(0.05)
        assert opened == [1]
        icon.notify("Jarvis is still running", "Right-click to quit.")   # must not raise
    finally:
        icon.stop()
    assert not icon.active and not tray.summon_running_copy()
