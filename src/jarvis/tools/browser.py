"""Web browser control (FR-T13) through Playwright driving the installed Microsoft Edge (or Chrome).

Jarvis uses its own browser window with its own profile under data/browser, so sign-ins persist between
sessions and the user's everyday browser profile is never touched. Pages are read as a list of interactive
elements with refs, the same way the desktop is.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from pydantic import Field

from ..config import DATA_DIR
from ..policy import Tier, normalise_web_url
from .registry import Args, ToolError, tool
from .system import NoArgs

log = logging.getLogger(__name__)

_lock = asyncio.Lock()
_pw: Any = None
_ctx: Any = None
_page: Any = None

_SNAPSHOT_JS = r"""
() => {
  const sel = 'a[href],button,input,textarea,select,summary,[role=button],[role=link],[role=tab],[role=menuitem],' +
              '[role=checkbox],[role=radio],[role=textbox],[role=combobox],[role=option],[role=switch],[contenteditable=true]';
  document.querySelectorAll('[data-jref]').forEach(e => e.removeAttribute('data-jref'));
  const out = [];
  let n = 0;
  for (const el of document.querySelectorAll(sel)) {
    if (out.length >= 160) break;
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || st.visibility === 'hidden' || st.display === 'none' || el.disabled) continue;
    if (el.type === 'hidden') continue;
    const tag = el.tagName.toLowerCase();
    const label = (el.getAttribute('aria-label') || el.innerText || el.value || el.placeholder || el.title ||
                   el.getAttribute('alt') || el.name || '').replace(/\s+/g, ' ').trim().slice(0, 90);
    if (!label && tag !== 'input' && tag !== 'textarea' && tag !== 'select') continue;
    const ref = 'b' + (++n);
    el.setAttribute('data-jref', ref);
    const item = { ref, tag, label };
    const role = el.getAttribute('role'); if (role) item.role = role;
    if (tag === 'input') item.type = el.type || 'text';
    if (tag === 'a') item.href = (el.getAttribute('href') || '').slice(0, 120);
    if ((tag === 'input' || tag === 'textarea') && el.type !== 'password' && el.value) item.value = String(el.value).slice(0, 80);
    if (el.type === 'checkbox' || el.type === 'radio') item.checked = el.checked;
    if (r.bottom < 0 || r.top > innerHeight) item.offscreen = true;
    out.push(item);
  }
  const text = (document.body ? document.body.innerText : '').replace(/\n{3,}/g, '\n\n').slice(0, 3500);
  return { url: location.href, title: document.title, elements: out, text, scroll_y: Math.round(scrollY),
           page_height: document.documentElement.scrollHeight };
}
"""

# SEC-11: fields Jarvis refuses to fill.
_FIELD_CHECK_JS = r"""
(el) => {
  const blob = [el.type, el.name, el.id, el.autocomplete, el.getAttribute('aria-label'), el.placeholder]
    .filter(Boolean).join(' ').toLowerCase();
  if (el.type === 'password') return 'password';
  if (/\bcc-|card.?number|cardnumber|cvv|cvc|security.?code|iban|routing|account.?number/.test(blob)) return 'payment';
  if (/one.?time|otp|verification.?code|2fa|passcode|\bssn\b|social.?security|passport/.test(blob)) return 'secret';
  return '';
}
"""


async def _ensure() -> Any:
    """Start the browser on first use and return the active page."""
    global _pw, _ctx, _page
    if _page is not None and not _page.is_closed():
        return _page
    if _ctx is not None:
        pages = [p for p in _ctx.pages if not p.is_closed()]
        if pages:
            _page = pages[-1]
            return _page
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import async_playwright

    profile = DATA_DIR / "browser"
    profile.mkdir(parents=True, exist_ok=True)
    if _pw is None:
        _pw = await async_playwright().start()
    last: Exception | None = None
    for channel in ("msedge", "chrome"):
        try:
            _ctx = await _pw.chromium.launch_persistent_context(
                str(profile), channel=channel, headless=False, no_viewport=True,
                args=["--no-first-run", "--no-default-browser-check"],
            )
            break
        except PlaywrightError as exc:
            last = exc
            _ctx = None
    if _ctx is None:
        raise ToolError("Could not start Microsoft Edge or Chrome for browser control.") from last
    _ctx.on("close", lambda *_: _reset())
    _page = _ctx.pages[0] if _ctx.pages else await _ctx.new_page()
    _ctx.on("page", _adopt)  # follow links that open a new tab
    return _page


def _adopt(page: Any) -> None:
    global _page
    _page = page


def _reset() -> None:
    global _ctx, _page
    _ctx = None
    _page = None


async def shutdown() -> None:
    """Close the browser when the core stops."""
    global _pw
    try:
        if _ctx is not None:
            await _ctx.close()
        if _pw is not None:
            await _pw.stop()
    except Exception:
        log.debug("browser shutdown failed", exc_info=True)
    _reset()
    _pw = None


def _normalise(raw: str) -> str:
    url = normalise_web_url(raw)
    if url is None:
        raise ToolError("Only plain http and https URLs can be opened.")
    return url


async def _guard(coro: Any, what: str) -> Any:
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import TimeoutError as PlaywrightTimeout

    try:
        return await coro
    except PlaywrightTimeout as exc:
        raise ToolError(f"{what} timed out. The page may still be loading — take a browser_snapshot.") from exc
    except PlaywrightError as exc:
        message = str(exc).splitlines()[0][:200]
        raise ToolError(f"{what} failed: {message}") from exc


async def _settle(page: Any) -> None:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=8000)
    except Exception:  # noqa: BLE001 - a slow page is not a failure
        pass


class OpenArgs(Args):
    url: str = Field(..., min_length=4, max_length=2000, description="http(s) URL; a bare domain is treated as https")
    new_tab: bool = False


def _classify_open(a: OpenArgs) -> tuple[Tier, str]:
    _normalise(a.url)
    return Tier.T1, ""


@tool("browser_open", "Open a web page in Jarvis's browser window. Follow with browser_snapshot to see it.",
      OpenArgs, Tier.T1, summary=lambda a: f"Browse to {a.url}", classify=_classify_open)
async def browser_open(a: OpenArgs) -> dict:
    global _page
    async with _lock:
        page = await _ensure()
        if a.new_tab:
            page = _page = await _ctx.new_page()
        await _guard(page.goto(_normalise(a.url), wait_until="domcontentloaded", timeout=30000), "Loading the page")
        await page.bring_to_front()
        return {"url": page.url, "title": await page.title()}


@tool("browser_snapshot",
      "Read the current web page: its text (start) and its links, buttons and fields, each with a ref for "
      "browser_click / browser_type. Call again after the page changes.",
      NoArgs, Tier.T0, taints=True, summary=lambda a: "Read the current web page")
async def browser_snapshot(_: NoArgs) -> dict:
    async with _lock:
        page = await _ensure()
        await _settle(page)
        return await _guard(page.evaluate(_SNAPSHOT_JS), "Reading the page")


class ReadArgs(Args):
    max_chars: int = Field(12000, ge=500, le=40000)


@tool("browser_read", "Return the full visible text of the current web page.", ReadArgs, Tier.T0, taints=True,
      summary=lambda a: "Read the page text")
async def browser_read(a: ReadArgs) -> dict:
    async with _lock:
        page = await _ensure()
        text = await _guard(page.evaluate("() => document.body ? document.body.innerText : ''"), "Reading the page")
        return {"url": page.url, "title": await page.title(), "text": text[: a.max_chars],
                "truncated": len(text) > a.max_chars}


class RefArgs(Args):
    ref: str = Field(..., pattern=r"^b\d{1,4}$", description="Ref from the latest browser_snapshot")


def _locator(page: Any, ref: str) -> Any:
    return page.locator(f'[data-jref="{ref}"]').first


@tool("browser_click", "Click a link, button or other element from the latest browser_snapshot.", RefArgs, Tier.T2,
      summary=lambda a: f"Click page element {a.ref}")
async def browser_click(a: RefArgs) -> dict:
    async with _lock:
        page = await _ensure()
        target = _locator(page, a.ref)
        if await target.count() == 0:
            raise ToolError(f"No element {a.ref} on this page. Take a new browser_snapshot.")
        label = (await target.inner_text(timeout=2000) if await target.is_visible() else "")[:80]
        await _guard(target.click(timeout=8000), "The click")
        await _settle(_page or page)
        current = _page or page
        return {"clicked": " ".join(label.split()), "url": current.url, "title": await current.title()}


class TypeArgs(RefArgs):
    text: str = Field(..., max_length=5000)
    submit: bool = Field(False, description="Press Enter afterwards")


def _type_summary(a: TypeArgs) -> str:
    preview = " ".join(a.text.split())
    return f"Type “{preview[:60]}{'…' if len(preview) > 60 else ''}” into page element {a.ref}"


@tool("browser_type", "Fill a text field from the latest browser_snapshot. Refuses password, payment and code fields.",
      TypeArgs, Tier.T2, summary=_type_summary)
async def browser_type(a: TypeArgs) -> dict:
    async with _lock:
        page = await _ensure()
        target = _locator(page, a.ref)
        if await target.count() == 0:
            raise ToolError(f"No element {a.ref} on this page. Take a new browser_snapshot.")
        kind = await _guard(target.evaluate(_FIELD_CHECK_JS), "Checking the field")
        if kind:
            raise ToolError(f"That is a {kind} field. Jarvis does not enter credentials, codes or payment details — "
                            "fill it in yourself in the browser window.")
        await _guard(target.fill(a.text, timeout=8000), "Typing")
        if a.submit:
            await _guard(target.press("Enter", timeout=5000), "Submitting")
            await _settle(page)
        return {"typed_chars": len(a.text), "submitted": a.submit, "url": page.url}


class KeyArgs(Args):
    key: str = Field(..., pattern=r"^[A-Za-z0-9+]{1,30}$", description="Key such as Enter, Escape, Tab, PageDown, Control+A")


@tool("browser_press", "Press a key in the current web page (for example PageDown to scroll).", KeyArgs, Tier.T1,
      summary=lambda a: f"Press {a.key} in the browser")
async def browser_press(a: KeyArgs) -> dict:
    async with _lock:
        page = await _ensure()
        await _guard(page.keyboard.press(a.key), "The key press")
        return {"pressed": a.key}


class NavArgs(Args):
    action: str = Field(..., pattern="^(back|forward|reload|close_tab)$")


@tool("browser_navigate", "Go back, forward, reload, or close the current tab.", NavArgs, Tier.T1,
      summary=lambda a: f"Browser: {a.action.replace('_', ' ')}")
async def browser_navigate(a: NavArgs) -> dict:
    global _page
    async with _lock:
        page = await _ensure()
        if a.action == "back":
            await _guard(page.go_back(timeout=15000), "Going back")
        elif a.action == "forward":
            await _guard(page.go_forward(timeout=15000), "Going forward")
        elif a.action == "reload":
            await _guard(page.reload(timeout=30000), "Reloading")
        else:
            await page.close()
            _page = None
            page = await _ensure()
        return {"url": page.url, "title": await page.title()}


@tool("browser_tabs", "List the tabs open in Jarvis's browser window.", NoArgs, Tier.T0, taints=True,
      summary=lambda a: "List browser tabs")
async def browser_tabs(_: NoArgs) -> dict:
    async with _lock:
        await _ensure()
        tabs = []
        for p in _ctx.pages:
            if not p.is_closed():
                tabs.append({"title": re.sub(r"\s+", " ", await p.title())[:100], "url": p.url, "active": p is _page})
        return {"tabs": tabs}
