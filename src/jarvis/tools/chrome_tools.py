"""Chrome browser automation tools via Chrome DevTools Protocol."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import Field

from ..policy import Tier
from .registry import Args, ToolError, tool

_CDP_PORT = 9222


async def _cdp(method: str, params: dict[str, Any] | None = None, timeout: float = 15.0) -> Any:
    """Send a CDP command to Chrome's debugging port."""
    import httpx2 as httpx

    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(f"http://127.0.0.1:{_CDP_PORT}/json")
        r.raise_for_status()
        targets = r.json()

    pages = [t for t in targets if t.get("type") == "page"]
    if not pages:
        raise ToolError("No Chrome pages found. Launch Chrome with --remote-debugging-port=9222")

    import websockets
    ws_url = pages[0]["webSocketDebuggerUrl"]
    async with websockets.connect(ws_url) as ws:
        msg = {"id": 1, "method": method, "params": params or {}}
        await ws.send(json.dumps(msg))
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
        if "error" in resp:
            raise ToolError(f"CDP error: {resp['error'].get('message', 'unknown')}")
        return resp.get("result", {})


class ChromeListTabsArgs(Args):
    pass


@tool("chrome_list_tabs", "List open Chrome browser tabs.",
      ChromeListTabsArgs, Tier.T0, summary=lambda a: "List Chrome tabs")
async def chrome_list_tabs(a: ChromeListTabsArgs) -> dict:
    import httpx2 as httpx
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"http://127.0.0.1:{_CDP_PORT}/json")
            r.raise_for_status()
            targets = r.json()
    except Exception as exc:
        raise ToolError(f"Cannot connect to Chrome DevTools. Launch Chrome with --remote-debugging-port=9222. Error: {exc}")
    pages = [t for t in targets if t.get("type") == "page"]
    return {"tabs": [{"id": p["id"], "title": p.get("title", ""), "url": p.get("url", "")} for p in pages]}


class ChromeNavigateArgs(Args):
    url: str = Field(..., min_length=1, max_length=2000)


@tool("chrome_navigate", "Navigate the active Chrome tab to a URL.",
      ChromeNavigateArgs, Tier.T1, summary=lambda a: f"Navigate: {a.url[:60]}")
async def chrome_navigate(a: ChromeNavigateArgs) -> dict:
    result = await _cdp("Page.navigate", {"url": a.url})
    return {"navigated": True, "frame_id": result.get("frameId", "")}


class ChromeGetContentArgs(Args):
    pass


@tool("chrome_get_content", "Get the text content of the current Chrome page.",
      ChromeGetContentArgs, Tier.T0, taints=True, summary=lambda a: "Get Chrome page text")
async def chrome_get_content(a: ChromeGetContentArgs) -> dict:
    result = await _cdp("Runtime.evaluate", {
        "expression": "document.body.innerText",
        "returnByValue": True,
    })
    text = result.get("result", {}).get("value", "")
    return {"text": text[:10000], "truncated": len(text) > 10000}


class ChromeScreenshotArgs(Args):
    quality: int = Field(80, ge=10, le=100)


@tool("chrome_screenshot", "Take a screenshot of the current Chrome tab.",
      ChromeScreenshotArgs, Tier.T0, summary=lambda a: "Chrome screenshot")
async def chrome_screenshot(a: ChromeScreenshotArgs) -> dict:
    result = await _cdp("Page.captureScreenshot", {
        "format": "jpeg", "quality": a.quality,
    })
    return {"image_base64": result.get("data", ""), "format": "jpeg"}


class ChromeRunJsArgs(Args):
    expression: str = Field(..., min_length=1, max_length=5000)


@tool("chrome_run_js", "Execute JavaScript in the current Chrome tab.",
      ChromeRunJsArgs, Tier.T2, sensitive=True, taints=True,
      summary=lambda a: f"Chrome JS: {a.expression[:40]}")
async def chrome_run_js(a: ChromeRunJsArgs) -> dict:
    result = await _cdp("Runtime.evaluate", {
        "expression": a.expression,
        "returnByValue": True,
        "awaitPromise": True,
    })
    r = result.get("result", {})
    if r.get("type") == "object" and r.get("subtype") == "error":
        raise ToolError(f"JS error: {r.get('description', 'unknown')}")
    return {"value": r.get("value"), "type": r.get("type", "undefined")}


class ChromeClickArgs(Args):
    selector: str = Field(..., min_length=1, max_length=500)


@tool("chrome_click", "Click an element in Chrome by CSS selector.",
      ChromeClickArgs, Tier.T1, summary=lambda a: f"Click: {a.selector[:40]}")
async def chrome_click(a: ChromeClickArgs) -> dict:
    safe_sel = a.selector.replace("\\", "\\\\").replace("'", "\\'")
    result = await _cdp("Runtime.evaluate", {
        "expression": f"document.querySelector('{safe_sel}')?.click(); 'clicked'",
        "returnByValue": True,
    })
    val = result.get("result", {}).get("value")
    if val != "clicked":
        raise ToolError(f"Element not found: {a.selector}")
    return {"clicked": True, "selector": a.selector}


class ChromeTypeArgs(Args):
    selector: str = Field(..., min_length=1, max_length=500)
    text: str = Field(..., min_length=1, max_length=5000)


@tool("chrome_type", "Type text into a form field in Chrome.",
      ChromeTypeArgs, Tier.T1, summary=lambda a: f"Type into: {a.selector[:30]}")
async def chrome_type(a: ChromeTypeArgs) -> dict:
    safe_sel = a.selector.replace("\\", "\\\\").replace("'", "\\'")
    safe_text = a.text.replace("\\", "\\\\").replace("'", "\\'")
    result = await _cdp("Runtime.evaluate", {
        "expression": f"""
            (function() {{
                var el = document.querySelector('{safe_sel}');
                if (!el) return 'not_found';
                el.focus();
                el.value = '{safe_text}';
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
                return 'typed';
            }})()
        """,
        "returnByValue": True,
    })
    val = result.get("result", {}).get("value")
    if val == "not_found":
        raise ToolError(f"Element not found: {a.selector}")
    return {"typed": True, "selector": a.selector}
