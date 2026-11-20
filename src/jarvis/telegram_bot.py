"""Telegram bot: remote access to Jarvis from the phone (idea 76).

Uses python-telegram-bot (async, polling mode — no public URL needed).
The bot accepts commands and messages from the owner only (by Telegram user ID or username).
Responses are sent back as Telegram messages.

Setup: save a Telegram bot token (from @BotFather) in the key vault as 'telegram',
and set your Telegram username or user ID in settings.telegram.allowed_users.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

Handler = Callable[[str, str], Awaitable[str]]


class TelegramBot:
    """Wraps python-telegram-bot to relay messages to Jarvis and back."""

    def __init__(self, token: str, allowed_users: list[str], on_message: Handler) -> None:
        self._token = token
        self._allowed = {u.lower().lstrip("@") for u in allowed_users}
        self._allowed_ids: set[int] = set()
        for u in allowed_users:
            try:
                self._allowed_ids.add(int(u))
            except ValueError:
                pass
        self._on_message = on_message
        self._app: Any = None
        self._task: asyncio.Task[None] | None = None

    def _is_allowed(self, user: Any) -> bool:
        if user is None:
            return False
        if user.id in self._allowed_ids:
            return True
        if user.username and user.username.lower() in self._allowed:
            return True
        return False

    async def start(self) -> None:
        try:
            from telegram import Update
            from telegram.ext import Application, CommandHandler, MessageHandler, filters
        except ImportError:
            log.warning("python-telegram-bot not installed; Telegram integration disabled")
            return

        self._app = Application.builder().token(self._token).build()

        async def handle_start(update: Update, context: Any) -> None:
            if not update.effective_user or not self._is_allowed(update.effective_user):
                if update.message:
                    await update.message.reply_text("Not authorised.")
                return
            if update.message:
                await update.message.reply_text(
                    "Jarvis is connected. Send me any message or command.\n\n"
                    "Commands:\n/ask <question> — ask Jarvis\n/status — system status\n"
                    "/remember <fact> — save to memory\n/tasks — list scheduled tasks\n"
                    "/screenshot — capture screen (if vision is enabled)"
                )

        async def handle_message(update: Update, context: Any) -> None:
            if not update.effective_user or not self._is_allowed(update.effective_user):
                return
            if not update.message or not update.message.text:
                return
            text = update.message.text.strip()
            if not text:
                return
            chat_id = str(update.message.chat_id)
            try:
                reply = await self._on_message(text, chat_id)
                await update.message.reply_text(reply[:4096], parse_mode="Markdown")
            except Exception as exc:
                log.exception("telegram handler error")
                await update.message.reply_text(f"Error: {str(exc)[:200]}")

        async def handle_status(update: Update, context: Any) -> None:
            if not update.effective_user or not self._is_allowed(update.effective_user):
                return
            if update.message:
                reply = await self._on_message("/status", str(update.message.chat_id))
                await update.message.reply_text(reply[:4096])

        async def handle_remember(update: Update, context: Any) -> None:
            if not update.effective_user or not self._is_allowed(update.effective_user):
                return
            if update.message and update.message.text:
                text = update.message.text.replace("/remember", "", 1).strip()
                if text:
                    reply = await self._on_message(f"remember that {text}", str(update.message.chat_id))
                    await update.message.reply_text(reply[:4096])

        async def handle_tasks(update: Update, context: Any) -> None:
            if not update.effective_user or not self._is_allowed(update.effective_user):
                return
            if update.message:
                reply = await self._on_message("/tasks", str(update.message.chat_id))
                await update.message.reply_text(reply[:4096])

        self._app.add_handler(CommandHandler("start", handle_start))
        self._app.add_handler(CommandHandler("status", handle_status))
        self._app.add_handler(CommandHandler("remember", handle_remember))
        self._app.add_handler(CommandHandler("tasks", handle_tasks))
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        await self._app.initialize()
        await self._app.start()
        self._task = asyncio.create_task(self._poll())
        log.info("Telegram bot started (polling)")

    async def _poll(self) -> None:
        if not self._app:
            return
        try:
            updater = self._app.updater
            if updater:
                await updater.start_polling(drop_pending_updates=True)
                while True:
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("telegram polling error")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._app:
            try:
                if self._app.updater and self._app.updater.running:
                    await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
            except Exception:
                log.debug("telegram shutdown error", exc_info=True)

    async def send(self, chat_id: str, text: str) -> None:
        """Send a proactive message to a specific chat."""
        if not self._app or not self._app.bot:
            return
        try:
            await self._app.bot.send_message(chat_id=int(chat_id), text=text[:4096], parse_mode="Markdown")
        except Exception:
            log.warning("failed to send telegram message to %s", chat_id)
