"""Browser sign-in (OAuth 2.1) for remote MCP connectors (FR-X4).

Some connectors (Wispr Flow Notetaker, Notion, Linear, …) give you a URL and then ask you to approve access in
the browser instead of handing you a key. The MCP library runs the protocol; this module supplies the three
pieces it needs from the host:

- **Opening the browser** on the connector's sign-in page (https only).
- **Receiving the answer** on Jarvis's own loopback server (`/oauth/mcp/callback`), matched to the connector
  that is waiting by the one-time `state` value, and only for a few minutes.
- **Keeping the tokens**: on Windows encrypted with DPAPI for the current user in data/mcp_oauth/; on macOS
  in the login Keychain. They are never written to settings, logs or the audit trail. Removing the
  connector deletes them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import webbrowser
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .config import DATA_DIR, HOST, PORT

log = logging.getLogger(__name__)

TOKEN_DIR = DATA_DIR / "mcp_oauth"
KEYRING_SERVICE = "jarvis-mcp-oauth"
_USE_KEYRING = sys.platform != "win32"  # no DPAPI: the OS keyring (macOS Keychain) holds the tokens
CALLBACK_PATH = "/oauth/mcp/callback"
SIGN_IN_TIMEOUT_S = 300.0


def redirect_uri() -> str:
    return f"http://{HOST}:{PORT}{CALLBACK_PATH}"


def _protect(data: bytes) -> bytes:
    import win32crypt

    return bytes(win32crypt.CryptProtectData(data, "jarvis-mcp", None, None, None, 0))


def _unprotect(blob: bytes) -> bytes:
    import win32crypt

    return bytes(win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1])


class TokenStore:
    """Tokens and the registered client for one connector, encrypted at rest (mcp TokenStorage protocol)."""

    def __init__(self, name: str, directory: Path | None = None) -> None:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
        self.path = (directory or TOKEN_DIR) / f"{safe}.bin"
        self._account = safe

    def _load(self) -> dict[str, Any]:
        try:
            if _USE_KEYRING:
                import keyring

                raw = keyring.get_password(KEYRING_SERVICE, self._account)
                return dict(json.loads(raw)) if raw else {}
            return dict(json.loads(_unprotect(self.path.read_bytes())))
        except FileNotFoundError:
            return {}
        except Exception:  # noqa: BLE001 - unreadable (other user, damaged): sign in again
            log.warning("stored sign-in for %s could not be read; signing in again", self.path.stem)
            return {}

    def _save(self, data: dict[str, Any]) -> None:
        if _USE_KEYRING:
            import keyring

            keyring.set_password(KEYRING_SERVICE, self._account, json.dumps(data))
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(_protect(json.dumps(data).encode()))
        tmp.replace(self.path)

    def signed_in(self) -> bool:
        return "tokens" in self._load()

    def forget(self) -> None:
        if _USE_KEYRING:
            import keyring
            from keyring.errors import KeyringError

            try:
                keyring.delete_password(KEYRING_SERVICE, self._account)
            except KeyringError:
                pass  # nothing was stored
            return
        self.path.unlink(missing_ok=True)

    async def get_tokens(self) -> Any:
        from mcp.shared.auth import OAuthToken

        raw = (await asyncio.to_thread(self._load)).get("tokens")
        return OAuthToken.model_validate(raw) if raw else None

    async def set_tokens(self, tokens: Any) -> None:
        data = await asyncio.to_thread(self._load)
        data["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        await asyncio.to_thread(self._save, data)

    async def get_client_info(self) -> Any:
        from mcp.shared.auth import OAuthClientInformationFull

        raw = (await asyncio.to_thread(self._load)).get("client")
        return OAuthClientInformationFull.model_validate(raw) if raw else None

    async def set_client_info(self, client_info: Any) -> None:
        data = await asyncio.to_thread(self._load)
        data["client"] = client_info.model_dump(mode="json", exclude_none=True)
        await asyncio.to_thread(self._save, data)


class SignIns:
    """Sign-ins in progress, keyed by the one-time `state` value in the browser round trip."""

    def __init__(self, open_browser: Callable[[str], Any] = webbrowser.open) -> None:
        self._waiting: dict[str, asyncio.Future[tuple[str, str | None]]] = {}
        self._open = open_browser

    def provider(self, name: str, url: str, on_state: Callable[[str], Awaitable[None]] | None = None,
                 store: TokenStore | None = None) -> Any:
        """An httpx auth object for this connector. It does nothing unless the server asks for sign-in."""
        from mcp.client.auth import OAuthClientProvider
        from mcp.shared.auth import AuthorizationCodeResult, OAuthClientMetadata

        current: dict[str, str] = {}

        async def redirect(auth_url: str) -> None:
            parts = urlsplit(auth_url)
            state = (parse_qs(parts.query).get("state") or [""])[0]
            local = parts.hostname in ("127.0.0.1", "localhost")
            if not state or parts.scheme not in ("https", "http") or (parts.scheme == "http" and not local):
                raise RuntimeError("the connector's sign-in page is not a secure web address")
            current["state"] = state
            self._waiting[state] = asyncio.get_running_loop().create_future()
            if on_state:
                await on_state("signin")
            log.info("MCP connector %s needs sign-in; opening the browser at %s", name, parts.hostname)
            await asyncio.to_thread(self._open, auth_url)

        async def callback() -> Any:
            state = current.get("state", "")
            future = self._waiting.get(state)
            if future is None:
                raise RuntimeError("no sign-in is in progress")
            try:
                code, issuer = await asyncio.wait_for(future, timeout=SIGN_IN_TIMEOUT_S)
            except TimeoutError as exc:
                raise RuntimeError("sign-in was not completed in the browser within five minutes") from exc
            finally:
                self._waiting.pop(state, None)
            if on_state:
                await on_state("connecting")
            return AuthorizationCodeResult(code=code, state=state, iss=issuer)

        metadata = OAuthClientMetadata(client_name="Jarvis (desktop assistant)", redirect_uris=[redirect_uri()],  # type: ignore[list-item]
                                       grant_types=["authorization_code", "refresh_token"], response_types=["code"],
                                       token_endpoint_auth_method="none")
        return OAuthClientProvider(server_url=url, client_metadata=metadata, storage=store or TokenStore(name),
                                   redirect_handler=redirect, callback_handler=callback)

    def complete(self, state: str, code: str, issuer: str | None = None) -> bool:
        """Called by the loopback callback route. True if a sign-in was waiting for exactly this state."""
        future = self._waiting.get(state)
        if future is None or future.done() or not code:
            return False
        future.set_result((code, issuer))
        return True

    def fail(self, state: str, reason: str) -> bool:
        future = self._waiting.get(state)
        if future is None or future.done():
            return False
        future.set_exception(RuntimeError(f"sign-in was refused: {reason[:120]}"))
        return True
