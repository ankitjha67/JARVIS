"""Morning digest and periodic briefings (inspired by OpenJarvis).

Assembles a daily briefing from multiple sources: weather, calendar, pending
tasks, unread messages, memory highlights, and system health. Delivered via
the orchestrator or Telegram at the scheduled time.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)


class DigestBuilder:
    """Collects sections for a digest and renders them as Markdown."""

    def __init__(self) -> None:
        self._sections: list[tuple[str, str]] = []

    def add(self, title: str, body: str) -> None:
        if body.strip():
            self._sections.append((title, body.strip()))

    def render(self) -> str:
        if not self._sections:
            return "Nothing notable to report."
        now = datetime.now(UTC).strftime("%A, %B %d %Y")
        parts = [f"# Morning Digest — {now}\n"]
        for title, body in self._sections:
            parts.append(f"## {title}\n{body}\n")
        return "\n".join(parts)

    @property
    def empty(self) -> bool:
        return len(self._sections) == 0


async def build_digest(hub: Any) -> str:
    """Build a morning digest from all available sources on the Hub."""
    d = DigestBuilder()

    if hasattr(hub, "scheduler"):
        try:
            tasks = hub.scheduler.list_tasks(status="active")
            upcoming = [t for t in tasks if t.get("next_run_at")][:5]
            if upcoming:
                lines = []
                for t in upcoming:
                    lines.append(f"- **{t['name']}** — next: {t.get('next_run_at', '?')}")
                d.add("Upcoming Tasks", "\n".join(lines))
        except Exception:
            log.debug("digest: scheduler unavailable")

    if hasattr(hub, "memory"):
        try:
            pending = hub.memory.review_pending(limit=5)
            if pending:
                lines = [f"- {m.get('text', '')[:80]}" for m in pending]
                d.add("Memory Review Queue", "\n".join(lines))
        except Exception:
            log.debug("digest: memory review unavailable")

    if hasattr(hub, "clipboard"):
        try:
            recent = hub.clipboard.recent(limit=3)
            if recent:
                lines = [f"- {c['text'][:60]}…" for c in recent]
                d.add("Recent Clipboard", "\n".join(lines))
        except Exception:
            log.debug("digest: clipboard unavailable")

    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("E:\\")
        d.add("System Health", (
            f"- CPU: {cpu}%\n"
            f"- RAM: {mem.percent}% ({mem.used // (1024**3)}/{mem.total // (1024**3)} GB)\n"
            f"- E: drive: {disk.free // (1024**3)} GB free of {disk.total // (1024**3)} GB"
        ))
    except Exception:
        log.debug("digest: psutil unavailable")

    return d.render()


async def deliver_digest(hub: Any) -> dict[str, Any]:
    """Build and deliver the morning digest."""
    text = await build_digest(hub)

    delivered_to: list[str] = []

    if hasattr(hub, "telegram") and hub.telegram and hub.telegram._running:
        try:
            settings = hub.settings
            chat_id = settings.get("telegram", {}).get("owner_chat_id")
            if chat_id:
                await hub.telegram.send(chat_id, text)
                delivered_to.append("telegram")
        except Exception:
            log.debug("digest: telegram delivery failed")

    if hasattr(hub, "broadcast"):
        try:
            from .protocol import msg
            await hub.broadcast(msg("notice", level="info",
                                     message="Morning digest ready. Say 'read my digest' to hear it."))
            delivered_to.append("hud")
        except Exception:
            log.debug("digest: broadcast failed")

    return {"digest": text, "delivered_to": delivered_to}
