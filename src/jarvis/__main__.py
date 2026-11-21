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

from . import __version__
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
    handlers = [handler, logging.StreamHandler(sys.stderr)]
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(_RedactFilter())
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, handlers=handlers, force=True)
    for noisy in ("httpx", "httpx2", "httpcore", "openai", "anthropic", "uvicorn.access", "pywebview"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main() -> None:
    parser = argparse.ArgumentParser(prog="jarvis", description="Jarvis desktop assistant")
    parser.add_argument("--no-window", action="store_true", help="run the core only and print the HUD URL")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args()
    _setup_logging(args.verbose)
    log = logging.getLogger("jarvis")

    # A fixed token is honoured only for development so the Vite dev server can reconnect across restarts.
    token = os.environ.get("JARVIS_TOKEN") or pysecrets.token_urlsafe(32)
    url = f"http://{HOST}:{PORT}/#t={token}"

    window_ref: dict[str, Any] = {}

    def window_control(action: str) -> None:
        win = window_ref.get("win")
        if not win:
            return
        if action == "minimize":
            win.minimize()
        elif action == "toggle_maximize":
            _toggle_max(win, window_ref)
        elif action == "close":
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
            run_window(url, window_ref, title=title)
    except KeyboardInterrupt:
        pass
    finally:
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
