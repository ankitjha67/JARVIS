"""Entry point: start the core (uvicorn on a background thread) and the HUD window (main thread)."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import secrets as pysecrets
import sys
import threading
import time
from typing import Any

import uvicorn

from . import __version__, tray
from .config import DB_PATH, HOST, LOG_PATH, PORT, ensure_dirs
from .gateway import Hub, create_app
from .hotkeys import HotkeyThread
from .store import Store
from .protocol import msg
from .secrets import redact


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(str(record.getMessage()))
        record.args = ()
        return True


def _setup_logging(verbose: bool) -> None:
    ensure_dirs()
    handler: logging.Handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fmt = logging.Formatter('{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":%(message)r}')
    handlers = [handler]
    if sys.stderr is not None:  # there is no console when Windows starts Jarvis at sign-in
        handlers.append(logging.StreamHandler(sys.stderr))
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(_RedactFilter())
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, handlers=handlers, force=True)
    for noisy in ("httpx", "httpx2", "httpcore", "openai", "anthropic", "uvicorn.access", "pywebview"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main() -> None:
    parser = argparse.ArgumentParser(prog="jarvis", description="Jarvis desktop assistant")
    parser.add_argument("--no-window", action="store_true", help="run the core only and print the HUD URL")
    parser.add_argument("--background", action="store_true",
                        help="start without showing the window (used when starting at sign-in)")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--doctor", action="store_true",
                        help="check what works on this computer and print a report, then exit")
    parser.add_argument("--json", action="store_true", help="with --doctor: print the report as JSON")
    args = parser.parse_args()
    if args.doctor:
        from . import doctor

        sys.exit(doctor.main(as_json=args.json))
    if not args.no_window and tray.summon_running_copy():
        return  # Jarvis is already running, perhaps hidden: its window has been asked to come forward
    _setup_logging(args.verbose)
    log = logging.getLogger("jarvis")

    # A fixed token is honoured only for development so the Vite dev server can reconnect across restarts.
    token = os.environ.get("JARVIS_TOKEN") or pysecrets.token_urlsafe(32)
    url = f"http://{HOST}:{PORT}/#t={token}"

    window_ref: dict[str, Any] = {}
    background: dict[str, Any] = {"quitting": False, "told": False, "icon": None}

    def window_control(action: str) -> None:
        win = window_ref.get("win")
        if not win:
            return
        if action == "minimize":
            win.minimize()
        elif action == "toggle_maximize":
            _toggle_max(win, window_ref)
        elif action == "close":
            win.destroy()  # asks closing() below, which hides the window instead when Jarvis is to keep running

    def closing() -> bool:
        """Called for every way of closing the window. False keeps it: Jarvis hides and stays running."""
        icon = background["icon"]
        if background["quitting"] or icon is None or not icon.active:
            return True
        try:
            keep = bool((hub.store.get_settings().get("background") or {}).get("keep_running", True))
        except Exception:
            log.exception("could not read the background setting; closing normally")
            return True
        if not keep:
            return True
        # Hiding must not happen inside the close event itself, so it is done a moment later.
        threading.Timer(0.05, hide_window).start()
        return False

    def hide_window() -> None:
        win = window_ref.get("win")
        if not win:
            return
        try:
            win.hide()
        except Exception:
            log.exception("hiding the window failed")
            return
        icon = background["icon"]
        if icon is not None and not background["told"]:
            background["told"] = True
            line, _ = hub.background_status()
            icon.notify(f"{icon.name} is still running",
                        f"{line}. Click this icon to open it, or right-click it to quit.")

    def quit_app() -> None:
        background["quitting"] = True
        win = window_ref.get("win")
        if win:
            win.destroy()

    hub = Hub(token, window_control=None if args.no_window else window_control)
    server = uvicorn.Server(uvicorn.Config(create_app(hub), host=HOST, port=PORT, log_config=None,
                                           ws_max_size=200_000, access_log=False))
    server_thread = threading.Thread(target=server.run, name="jarvis-core", daemon=True)
    server_thread.start()
    deadline = time.monotonic() + 15
    while not server.started and server_thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        log.error("core failed to start on %s:%s (is another Jarvis running?)", HOST, PORT)
        sys.exit(1)

    def bring_forward() -> None:  # used by the wake word; it sends its own ui.focus with the reason
        win = window_ref.get("win")
        if not win:
            return
        from .shell import force_foreground

        try:
            # Restore through pywebview so the WebView2 control is resized and repainted with the window;
            # then raise and focus it with Win32, which pywebview cannot do from a background process.
            win.restore()
            win.show()
            if not force_foreground():
                log.warning("could not bring the Jarvis window to the front")
        except Exception:
            log.exception("bringing the window forward failed")

    def summon() -> None:
        bring_forward()
        hub.call_soon(lambda: hub.broadcast(msg("ui.focus", reason="hotkey")))

    def kill() -> None:
        hub.call_soon(lambda: hub.orchestrator.cancel("Stopped by kill switch."))

    hub.summon = bring_forward
    hotkeys = HotkeyThread(summon, kill)
    hotkeys.start()
    if not args.no_window and tray.AVAILABLE:
        stored = hub.store.get_settings()
        name = (stored.get("assistant_name") or "J.A.R.V.I.S").replace(".", "")
        icon = tray.Tray(name, on_open=summon, on_quit=quit_app,
                         on_toggle_wake=lambda on: hub.call_soon(lambda: hub.set_wake_word(on)),
                         status=hub.background_status)
        if icon.start():
            background["icon"] = icon
            hub.can_background = True
        else:
            log.warning("no notification-area icon, so closing the window will quit Jarvis")
        try:  # keep the sign-in entry pointing at this copy if it was moved, and the setting honest if removed
            if bool((stored.get("background") or {}).get("start_at_login")) != tray.starts_at_login():
                tray.set_start_at_login(bool((stored.get("background") or {}).get("start_at_login")))
        except OSError:
            log.warning("could not update the sign-in entry", exc_info=True)
    log.info("Jarvis %s core listening on %s:%s", __version__, HOST, PORT)

    try:
        if args.no_window:
            print(f"HUD: {url}", flush=True)
            while server_thread.is_alive():
                time.sleep(0.5)
        else:
            from .shell import run_window

            settings = Store(DB_PATH).get_settings()
            title = (settings.get("assistant_name") or "J.A.R.V.I.S").replace(".", "")
            hidden = args.background and background["icon"] is not None  # never hidden with no way to reach it
            run_window(url, window_ref, title=title, hidden=hidden, on_closing=closing)
    except KeyboardInterrupt:
        pass
    finally:
        if background["icon"] is not None:
            background["icon"].stop()
        hotkeys.stop()
        server.should_exit = True
        server_thread.join(timeout=8)


def _toggle_max(win: Any, ref: dict[str, Any]) -> None:
    if ref.get("maximized"):
        win.restore()
    else:
        win.maximize()
    ref["maximized"] = not ref.get("maximized")


if __name__ == "__main__":
    main()
