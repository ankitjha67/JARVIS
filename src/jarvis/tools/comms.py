"""Email over IMAP/SMTP, phone calls through the Windows calling app, and Windows Settings pages.

Email uses the account configured in Settings; its app password lives in Windows Credential Manager.
Sending mail and starting calls are `sensitive`: they always ask for approval, in every autonomy mode.
"""

from __future__ import annotations

import asyncio
import email
import email.policy
import html
import imaplib
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from typing import Any

from pydantic import Field

from .. import secrets
from ..policy import Tier
from . import context
from .registry import Args, ToolError, tool

_ADDR = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_BODY_CAP = 12_000


def _account() -> tuple[dict[str, Any], str]:
    cfg = context.settings().get("email") or {}
    password = secrets.get_key("email")
    if not cfg.get("address") or not cfg.get("imap_host") or not cfg.get("smtp_host"):
        raise ToolError("Email is not set up. Add the account in Settings → Email.")
    if not password:
        raise ToolError("No email app password is stored. Add it in Settings → Email.")
    return cfg, password


def _imap(cfg: dict[str, Any], password: str) -> imaplib.IMAP4_SSL:
    try:
        conn = imaplib.IMAP4_SSL(cfg["imap_host"], int(cfg.get("imap_port", 993)), timeout=20,
                                 ssl_context=ssl.create_default_context())
        conn.login(cfg["address"], password)
        return conn
    except imaplib.IMAP4.error as exc:
        raise ToolError("The mail server rejected the sign-in. Check the address and app password in Settings.") from exc
    except (OSError, ssl.SSLError) as exc:
        raise ToolError(f"Could not reach the mail server {cfg['imap_host']}.") from exc


def _quote(folder: str) -> str:
    if not re.fullmatch(r"[\w \[\]/.\-]{1,80}", folder):
        raise ToolError("Invalid folder name.")
    return '"' + folder + '"'


def _header(msg: email.message.Message, name: str) -> str:
    return str(msg.get(name, "") or "").replace("\r", " ").replace("\n", " ")[:200]


class ListArgs(Args):
    folder: str = Field("INBOX", max_length=80)
    search: str | None = Field(None, max_length=120, description="Words to look for in the message")
    from_address: str | None = Field(None, max_length=120, description="Only mail from this sender")
    unread_only: bool = False
    limit: int = Field(10, ge=1, le=30)


def _list(a: ListArgs) -> dict:
    cfg, password = _account()
    conn = _imap(cfg, password)
    try:
        status, _ = conn.select(_quote(a.folder), readonly=True)
        if status != "OK":
            raise ToolError(f"The folder {a.folder} does not exist.")
        criteria: list[str] = ["UNSEEN" if a.unread_only else "ALL"]
        for key, value in (("TEXT", a.search), ("FROM", a.from_address)):
            if value:
                criteria += [key, '"' + value.replace("\\", "").replace('"', "") + '"']
        status, data = conn.uid("SEARCH", "CHARSET", "UTF-8", *[c.encode("utf-8") for c in criteria])
        if status != "OK":
            raise ToolError("The mail server could not run that search.")
        uids = data[0].split()[-a.limit:][::-1]
        items = []
        for uid in uids:
            status, parts = conn.uid("FETCH", uid, "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if status != "OK" or not parts or not isinstance(parts[0], tuple):
                continue
            msg = email.message_from_bytes(parts[0][1], policy=email.policy.default)
            flags = parts[0][0].decode(errors="replace")
            items.append({"id": uid.decode(), "from": _header(msg, "From"), "subject": _header(msg, "Subject"),
                          "date": _header(msg, "Date"), "unread": "\\Seen" not in flags})
        return {"folder": a.folder, "messages": items, "total_matching": len(data[0].split())}
    finally:
        try:
            conn.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


@tool("email_list", "List recent emails (newest first), optionally filtered by words, sender or unread.",
      ListArgs, Tier.T0, taints=True, summary=lambda a: f"List email in {a.folder}")
async def email_list(a: ListArgs) -> dict:
    return await asyncio.to_thread(_list, a)


class ReadArgs(Args):
    id: str = Field(..., pattern=r"^\d{1,12}$", description="Message id from email_list")
    folder: str = Field("INBOX", max_length=80)


def _text_of(msg: email.message.EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    content = part.get_content()
    if part.get_content_type() == "text/html":
        content = re.sub(r"(?is)<(script|style).*?</\1>", " ", content)
        content = html.unescape(re.sub(r"(?s)<[^>]+>", " ", content))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n\s*", "\n\n", content)).strip()


def _read(a: ReadArgs) -> dict:
    cfg, password = _account()
    conn = _imap(cfg, password)
    try:
        if conn.select(_quote(a.folder), readonly=True)[0] != "OK":
            raise ToolError(f"The folder {a.folder} does not exist.")
        status, parts = conn.uid("FETCH", a.id.encode(), "(BODY.PEEK[])")
        if status != "OK" or not parts or not isinstance(parts[0], tuple):
            raise ToolError("That message was not found.")
        msg = email.message_from_bytes(parts[0][1], policy=email.policy.default)
        body = _text_of(msg)  # type: ignore[arg-type]
        attachments = [p.get_filename() for p in msg.iter_attachments() if p.get_filename()]  # type: ignore[attr-defined]
        return {"from": _header(msg, "From"), "to": _header(msg, "To"), "subject": _header(msg, "Subject"),
                "date": _header(msg, "Date"), "body": body[:_BODY_CAP], "truncated": len(body) > _BODY_CAP,
                "attachments": attachments[:20]}
    finally:
        try:
            conn.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


@tool("email_read", "Read one email in full.", ReadArgs, Tier.T0, taints=True, summary=lambda a: f"Read email {a.id}")
async def email_read(a: ReadArgs) -> dict:
    return await asyncio.to_thread(_read, a)


class SendArgs(Args):
    to: list[str] = Field(..., min_length=1, max_length=20)
    subject: str = Field(..., min_length=1, max_length=300)
    body: str = Field(..., min_length=1, max_length=50_000)
    cc: list[str] = Field(default_factory=list, max_length=20)


def _classify_send(a: SendArgs) -> tuple[Tier, str]:
    for addr in [*a.to, *a.cc]:
        if not _ADDR.match(parseaddr(addr)[1]):
            raise ToolError(f"“{addr}” is not a valid email address.")
    if any(c in a.subject for c in "\r\n"):
        raise ToolError("The subject must be a single line.")
    return Tier.T2, f"Sends an email from your account to {', '.join(a.to)}. It cannot be unsent."


def _send(a: SendArgs) -> dict:
    cfg, password = _account()
    msg = EmailMessage()
    msg["From"] = cfg["address"]
    msg["To"] = ", ".join(a.to)
    if a.cc:
        msg["Cc"] = ", ".join(a.cc)
    msg["Subject"] = a.subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=cfg["address"].split("@")[-1])
    msg.set_content(a.body)
    port = int(cfg.get("smtp_port", 465))
    tls = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(cfg["smtp_host"], port, timeout=30, context=tls) as smtp:
                smtp.login(cfg["address"], password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(cfg["smtp_host"], port, timeout=30) as smtp:
                smtp.starttls(context=tls)
                smtp.login(cfg["address"], password)
                smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise ToolError("The mail server rejected the sign-in. Check the app password in Settings.") from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise ToolError("The mail server refused the recipient address.") from exc
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        raise ToolError(f"The email could not be sent ({type(exc).__name__}).") from exc
    return {"sent_to": a.to, "cc": a.cc, "subject": a.subject}


@tool("email_send", "Send an email from the user's account. Show the user the exact text first when it is long.",
      SendArgs, Tier.T2, sensitive=True, classify=_classify_send,
      summary=lambda a: f"Send email to {', '.join(a.to)[:80]}: “{a.subject[:60]}”")
async def email_send(a: SendArgs) -> dict:
    return await asyncio.to_thread(_send, a)


# -- calls --------------------------------------------------------------------------------------------

class CallArgs(Args):
    number: str = Field(..., pattern=r"^\+?[0-9][0-9 ()\-]{2,22}$", description="Phone number, ideally with country code")


@tool("call_start",
      "Start a phone call to a number using the Windows calling app (Phone Link with a paired phone, or "
      "another app registered for calls).",
      CallArgs, Tier.T2, sensitive=True, summary=lambda a: f"Call {a.number}")
async def call_start(a: CallArgs) -> dict:
    digits = re.sub(r"[^\d+]", "", a.number)
    try:
        await asyncio.to_thread(os.startfile, f"tel:{digits}")
    except OSError as exc:
        raise ToolError("No app on this PC is set up to place calls. Pair your phone in Phone Link first.") from exc
    return {"dialling": digits, "note": "The calling app has been opened with the number; it may ask you to confirm."}


# -- Windows Settings pages ---------------------------------------------------------------------------

_SETTINGS_PAGES = {
    "wifi": "network-wifi", "network": "network-status", "bluetooth": "bluetooth", "display": "display",
    "night_light": "nightlight", "sound": "sound", "notifications": "notifications", "focus": "quiethours",
    "power": "powersleep", "battery": "batterysaver", "storage": "storagesense", "apps": "appsfeatures",
    "default_apps": "defaultapps", "update": "windowsupdate", "privacy": "privacy", "accounts": "yourinfo",
    "date_time": "dateandtime", "language": "regionlanguage", "mouse": "mousetouchpad", "keyboard": "keyboard",
    "personalization": "personalization", "about": "about", "vpn": "network-vpn", "printers": "printers",
    "startup_apps": "startupapps", "phone": "mobile-devices",
}


class SettingsArgs(Args):
    page: str = Field(..., description="One of: " + ", ".join(sorted(_SETTINGS_PAGES)))


def _classify_settings(a: SettingsArgs) -> tuple[Tier, str]:
    if a.page not in _SETTINGS_PAGES:
        raise ToolError("Unknown settings page. Use one of: " + ", ".join(sorted(_SETTINGS_PAGES)))
    return Tier.T1, ""


@tool("settings_open", "Open a page of Windows Settings (then use ui_snapshot / ui_click to change something).",
      SettingsArgs, Tier.T1, classify=_classify_settings, summary=lambda a: f"Open Windows Settings: {a.page}")
async def settings_open(a: SettingsArgs) -> dict:
    await asyncio.to_thread(os.startfile, f"ms-settings:{_SETTINGS_PAGES[a.page]}")
    return {"opened": a.page}
