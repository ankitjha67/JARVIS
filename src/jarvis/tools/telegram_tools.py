"""Telegram tools: send messages via the Telegram bot (idea 76)."""

from __future__ import annotations


from pydantic import Field

from ..policy import Tier
from .registry import Args, ToolError, tool


class TelegramSendArgs(Args):
    text: str = Field(..., min_length=1, max_length=4000)
    chat_id: int | None = Field(None, description="Specific chat ID, or uses owner's chat")


@tool("telegram_send", "Send a message to the user via Telegram.",
      TelegramSendArgs, Tier.T1, sensitive=True, summary=lambda a: f"Telegram: {a.text[:40]}")
async def telegram_send(a: TelegramSendArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    if not hasattr(hub, 'telegram') or hub.telegram is None:
        raise ToolError("Telegram bot not configured. Set token in Settings → Integrations → Telegram.")
    chat_id_str = str(a.chat_id) if a.chat_id else None
    if not chat_id_str:
        raise ToolError("No chat ID available. Send /start to the bot first, then provide the chat_id.")
    await hub.telegram.send(chat_id_str, a.text)
    return {"sent": True, "chat_id": chat_id_str}


class TelegramStatusArgs(Args):
    pass


@tool("telegram_status", "Check Telegram bot connection status.",
      TelegramStatusArgs, Tier.T0, summary=lambda a: "Telegram status")
async def telegram_status(a: TelegramStatusArgs) -> dict:
    from . import context
    hub = context._hub  # type: ignore[attr-defined]
    if not hasattr(hub, 'telegram') or hub.telegram is None:
        return {"connected": False, "reason": "not configured"}
    connected = hub.telegram._app is not None and hub.telegram._task is not None
    return {"connected": connected}
