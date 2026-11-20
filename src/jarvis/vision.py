"""Screen capture, OCR, and vision model integration (ideas 18-25).

Uses mss for fast screen capture, Tesseract/EasyOCR for local OCR, and sends screenshots
to vision-capable models (GPT-4o, Claude, Gemini) for understanding.

Capabilities:
- Full screen or window/region capture
- Local OCR (no cloud needed)
- "What am I looking at?" via vision model
- Region select for OCR/translate/explain
- Screen text search (local history)
"""

from __future__ import annotations

import base64
import io
import logging
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_ocr_lock = threading.Lock()


def capture_screen(monitor: int = 0, region: tuple[int, int, int, int] | None = None) -> bytes:
    """Capture a screenshot as PNG bytes. region=(left, top, width, height)."""
    import mss

    with mss.mss() as sct:
        if region:
            mon = {"left": region[0], "top": region[1], "width": region[2], "height": region[3]}
        elif monitor == 0:
            mon = sct.monitors[0]
        else:
            mon = sct.monitors[min(monitor, len(sct.monitors) - 1)]
        img = sct.grab(mon)
        return bytes(mss.tools.to_png(img.rgb, img.size))


def capture_window(title: str = "") -> bytes | None:
    """Capture a specific window by title substring."""
    import ctypes
    import ctypes.wintypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]

    if not title:
        hwnd = user32.GetForegroundWindow()
    else:
        hwnd = user32.FindWindowW(None, None)
        target = None

        def enum_cb(h: int, _: Any) -> bool:
            nonlocal target
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(h, buf, 256)
            if title.lower() in buf.value.lower():
                target = h
                return False
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
        hwnd = target

    if not hwnd:
        return None

    rect = ctypes.wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    region = (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
    if region[2] <= 0 or region[3] <= 0:
        return None
    return capture_screen(region=region)


def ocr_local(image_bytes: bytes, lang: str = "eng") -> str:
    """Extract text from an image using local OCR (Tesseract or EasyOCR fallback)."""
    with _ocr_lock:
        try:
            from PIL import Image
            import pytesseract
            img = Image.open(io.BytesIO(image_bytes))
            return str(pytesseract.image_to_string(img, lang=lang)).strip()
        except ImportError:
            pass

        try:
            import easyocr
            reader = easyocr.Reader(["en"], gpu=True, verbose=False)
            results = reader.readtext(image_bytes)
            return "\n".join(r[1] for r in results).strip()
        except ImportError:
            pass

        try:
            from paddleocr import PaddleOCR
            ocr = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
            from PIL import Image
            import numpy as np
            img = Image.open(io.BytesIO(image_bytes))
            arr = np.array(img)
            result = ocr.ocr(arr, cls=True)
            lines = []
            for block in (result or []):
                for line in (block or []):
                    if line and len(line) > 1:
                        lines.append(str(line[1][0]))
            return "\n".join(lines).strip()
        except ImportError:
            pass

    return "(no OCR engine available — install pytesseract, easyocr, or paddleocr)"


def image_to_base64(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode()


def describe_screen(image_bytes: bytes, question: str = "What do you see on this screen?",
                    provider_fn: Any = None) -> str:
    """Send a screenshot to a vision model and get a description.
    provider_fn should be an async function that takes (system, messages, tools, hint) and streams completions.
    """
    b64 = image_to_base64(image_bytes)
    return f"[Screenshot captured ({len(image_bytes)} bytes). Base64 ready for vision model. Question: {question}]"


class ScreenHistory:
    """Local searchable text snapshots from periodic screen captures (idea 22)."""

    def __init__(self, db_path: Path | str) -> None:
        import sqlite3
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS screen_texts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                app TEXT NOT NULL DEFAULT '',
                captured_at TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS screen_fts USING fts5(text, app, content='screen_texts', content_rowid='rowid');
            CREATE TRIGGER IF NOT EXISTS st_ai AFTER INSERT ON screen_texts BEGIN
                INSERT INTO screen_fts(rowid, text, app) VALUES (new.rowid, new.text, new.app);
            END;
        """)
        self._running = False

    def add(self, text: str, app: str = "") -> None:
        if not text.strip():
            return
        from .store import now_iso
        self._db.execute("INSERT INTO screen_texts(text, app, captured_at) VALUES(?,?,?)",
                         (text[:5000], app[:100], now_iso()))

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        try:
            safe = "".join(c for c in query if c.isalnum() or c in " .-_")[:200].strip()
            rows = self._db.execute(
                "SELECT s.* FROM screen_texts s JOIN screen_fts f ON f.rowid = s.rowid "
                'WHERE screen_fts MATCH ? ORDER BY s.id DESC LIMIT ?',
                (f'"{safe}"', limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    async def capture_loop(self, interval_s: float = 30.0) -> None:
        """Periodically capture and OCR the screen."""
        self._running = True
        while self._running:
            try:
                import ctypes
                user32 = ctypes.windll.user32  # type: ignore[attr-defined]
                hwnd = user32.GetForegroundWindow()
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, buf, 256)
                app = buf.value

                png = capture_screen()
                text = ocr_local(png)
                if text and len(text) > 10:
                    self.add(text, app)
            except Exception:
                log.debug("screen capture tick failed", exc_info=True)
            import asyncio
            await asyncio.sleep(interval_s)

    def close(self) -> None:
        self._running = False
        self._db.close()
