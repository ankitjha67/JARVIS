"""Voice call tools: Jarvis can call the user's phone to request approval or deliver urgent messages.

Supports two backends:
  1. Twilio (recommended) — cloud telephony with built-in TTS, DTMF input gathering, and
     webhook-based response collection. Needs a Twilio account SID, auth token, and phone number.
  2. Phone Link fallback — opens the Windows tel: handler (no TTS, no programmatic response).

Twilio credentials are stored in the key vault (never in settings):
  - twilio_sid     → Account SID
  - twilio_token   → Auth token
  - twilio_number  → Twilio phone number (E.164 format: +1...)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic import Field

from .. import secrets
from ..policy import Tier
from .registry import Args, ToolError, tool

log = logging.getLogger(__name__)


def _twilio_configured() -> bool:
    return all(secrets.get_key(k) for k in ("twilio_sid", "twilio_token", "twilio_number"))


def _twilio_client() -> Any:
    try:
        from twilio.rest import Client
    except ImportError:
        raise ToolError("The twilio package is not installed. Run: uv pip install twilio")
    sid = secrets.get_key("twilio_sid")
    token = secrets.get_key("twilio_token")
    if not sid or not token:
        raise ToolError("Twilio credentials not configured. Store twilio_sid, twilio_token, and twilio_number in Settings → Keys.")
    return Client(sid, token)


# ---------------------------------------------------------------------------
# Call the user with a spoken message
# ---------------------------------------------------------------------------

class VoiceCallArgs(Args):
    phone: str = Field(..., pattern=r"^\+[1-9]\d{6,14}$", description="Phone number in E.164 format (+1234567890)")
    message: str = Field(..., min_length=1, max_length=2000, description="Message to speak to the user via TTS")
    gather_response: bool = Field(
        False,
        description="If true, ask the user to press 1 to approve or 2 to deny after the message.",
    )


@tool(
    "voice_call",
    "Call the user's phone and speak a message using text-to-speech (Twilio). "
    "Use when Jarvis needs urgent approval or the user is away from the PC. "
    "Falls back to Windows Phone Link if Twilio is not configured.",
    VoiceCallArgs, Tier.T2, sensitive=True,
    summary=lambda a: f"Call {a.phone}: {a.message[:50]}",
)
async def voice_call(a: VoiceCallArgs) -> dict:
    if _twilio_configured():
        return await _call_twilio(a)
    return await _call_phone_link(a)


async def _call_twilio(a: VoiceCallArgs) -> dict:
    client = _twilio_client()
    from_number = secrets.get_key("twilio_number")
    if not from_number:
        raise ToolError("Twilio phone number not stored. Add twilio_number in Settings → Keys.")

    twiml_parts = [f'<Say voice="alice">{_escape_xml(a.message)}</Say>']

    if a.gather_response:
        twiml_parts = [
            '<Gather numDigits="1" action="" method="POST">',
            f'  <Say voice="alice">{_escape_xml(a.message)}</Say>',
            '  <Say voice="alice">Press 1 to approve. Press 2 to deny.</Say>',
            '</Gather>',
            '<Say voice="alice">No input received. Goodbye.</Say>',
        ]

    twiml = "<Response>" + "".join(twiml_parts) + "</Response>"

    try:
        call = await asyncio.to_thread(
            client.calls.create,
            twiml=twiml,
            to=a.phone,
            from_=from_number,
        )
    except Exception as exc:
        raise ToolError(f"Twilio call failed: {str(exc)[:300]}") from exc

    return {
        "provider": "twilio",
        "call_sid": call.sid,
        "status": call.status,
        "to": a.phone,
        "message_spoken": True,
    }


async def _call_phone_link(a: VoiceCallArgs) -> dict:
    import os
    import re
    digits = re.sub(r"[^\d+]", "", a.phone)
    try:
        await asyncio.to_thread(os.startfile, f"tel:{digits}")
    except OSError as exc:
        raise ToolError(
            "No calling app available and Twilio is not configured. "
            "Either pair your phone in Phone Link, or store Twilio credentials in Settings → Keys."
        ) from exc
    return {
        "provider": "phone_link",
        "dialling": digits,
        "message_spoken": False,
        "note": "Phone Link opened. TTS not available without Twilio. The message was NOT spoken — tell the user manually.",
        "intended_message": a.message,
    }


# ---------------------------------------------------------------------------
# Check call status (Twilio only)
# ---------------------------------------------------------------------------

class CallStatusArgs(Args):
    call_sid: str = Field(..., min_length=10, max_length=50, description="Twilio Call SID from voice_call")


@tool(
    "voice_call_status",
    "Check the status of a Twilio voice call (queued, ringing, in-progress, completed, failed, etc.).",
    CallStatusArgs, Tier.T0,
    summary=lambda a: f"Call status: {a.call_sid[:20]}",
)
async def voice_call_status(a: CallStatusArgs) -> dict:
    client = _twilio_client()
    try:
        call = await asyncio.to_thread(client.calls(a.call_sid).fetch)
    except Exception as exc:
        raise ToolError(f"Could not fetch call status: {str(exc)[:200]}") from exc
    return {
        "sid": call.sid,
        "status": call.status,
        "duration": call.duration,
        "to": call.to,
        "direction": call.direction,
    }


# ---------------------------------------------------------------------------
# Configuration status
# ---------------------------------------------------------------------------

class VoiceConfigArgs(Args):
    pass


@tool(
    "voice_call_config",
    "Check whether voice calling is configured (Twilio credentials and phone number).",
    VoiceConfigArgs, Tier.T0,
    summary=lambda a: "Voice call config",
)
async def voice_call_config(a: VoiceConfigArgs) -> dict:
    has_sid = bool(secrets.get_key("twilio_sid"))
    has_token = bool(secrets.get_key("twilio_token"))
    has_number = bool(secrets.get_key("twilio_number"))
    twilio_ready = has_sid and has_token and has_number
    return {
        "twilio_configured": twilio_ready,
        "twilio_sid_stored": has_sid,
        "twilio_token_stored": has_token,
        "twilio_number_stored": has_number,
        "fallback": "phone_link" if not twilio_ready else None,
        "setup_instructions": (
            "Store three keys in Settings → Keys: twilio_sid (Account SID), "
            "twilio_token (Auth Token), twilio_number (your Twilio phone number in +E.164 format). "
            "Free trial at twilio.com gives $15.50 credit."
        ) if not twilio_ready else "Ready to make calls.",
    }


def _escape_xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
