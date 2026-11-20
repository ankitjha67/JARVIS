"""Model ladder: automatic complexity-based model escalation (Mark-LVII).

Starts with the cheapest capable model. If the response quality is low
(detected via heuristics), automatically retries with a more capable model.
Tracks cost and latency per tier.
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .providers.base import Completion

log = logging.getLogger(__name__)

Tier = Literal["fast_local", "fast_cloud", "smart", "premium"]

TIER_ORDER: list[Tier] = ["fast_local", "fast_cloud", "smart", "premium"]

TIER_ROLE: dict[Tier, str] = {
    "fast_local": "fast",
    "fast_cloud": "fast",
    "smart": "smart",
    "premium": "smart",
}

LOCAL_PROVIDERS = frozenset({"lmstudio", "ollama"})
FAST_CLOUD_PROVIDERS = frozenset({"groq", "gemini"})
SMART_PROVIDERS = frozenset({"gemini", "openai", "anthropic", "nvidia", "openrouter"})
PREMIUM_PROVIDERS = frozenset({"anthropic", "openai"})

_COMPLEX_RE = re.compile(
    r"(?i)\b(analy[sz]e|compare|summari[sz]e|research|investigate|refactor|debug|review|"
    r"write\s+(a|an|the)\s+\w+|draft|plan|explain\s+(how|why|what)|step\s+by\s+step|"
    r"figure\s+out|deep\s+dive|comprehensive|in\s+detail)\b"
)
_CRITICAL_RE = re.compile(
    r"(?i)\b(security|audit|financial|legal|compliance|production|deploy|migration|"
    r"sensitive|confidential|critical|urgent|risk\s+assess|threat\s+model|penetration)\b"
)
_SIMPLE_COMMANDS = re.compile(
    r"(?i)(?:^|\b)(open|launch|close|mute|unmute|volume|brightness|time|date|weather|"
    r"screenshot|clipboard|paste|copy|minimize|maximize|focus|switch|lock)\b"
)
_REFUSAL_RE = re.compile(
    r"(?i)(i\s+(can'?t|cannot|don'?t\s+know|am\s+unable|'m\s+not\s+able)|"
    r"i\s+don'?t\s+have\s+(the\s+ability|enough\s+information)|"
    r"as\s+an?\s+(ai|language\s+model)|beyond\s+my\s+capabilit)"
)
_GENERIC_RE = re.compile(
    r"(?i)(here\s+are\s+some\s+(general|common)\s+(tips|suggestions|steps)|"
    r"generally\s+speaking|it\s+depends\s+on\s+many\s+factors|"
    r"i\s+recommend\s+consulting\s+a\s+professional)"
)


@dataclass
class TierStats:
    calls: int = 0
    total_latency_ms: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    successes: int = 0
    escalations: int = 0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.calls if self.calls else 0.0

    @property
    def success_rate(self) -> float:
        return self.successes / self.calls if self.calls else 0.0


class ModelLadder:
    """Automatic complexity-based model tier selection with escalation."""

    def __init__(self) -> None:
        self._stats: dict[Tier, TierStats] = defaultdict(TierStats)
        self._last_tier: Tier | None = None
        self._last_text: str = ""

    def select_tier(self, text: str, history_len: int = 0, has_tools: bool = True) -> Tier:
        """Classify request complexity and pick the cheapest adequate tier."""
        self._last_text = text
        words = text.split()
        word_count = len(words)

        if _CRITICAL_RE.search(text):
            tier: Tier = "premium"
        elif _COMPLEX_RE.search(text) or word_count > 60 or history_len > 20:
            tier = "smart"
        elif _SIMPLE_COMMANDS.search(text) and word_count <= 8:
            tier = "fast_local"
        elif word_count <= 10 and "?" not in text and not has_tools:
            tier = "fast_local"
        elif word_count <= 20 and "?" not in text:
            tier = "fast_cloud"
        elif "?" in text and word_count <= 30:
            tier = "fast_cloud"
        else:
            tier = "smart"

        self._last_tier = tier
        return tier

    def should_escalate(self, completion: Completion, current_tier: Tier) -> Tier | None:
        """Check whether the response quality warrants escalation. Returns the next tier or None."""
        next_idx = TIER_ORDER.index(current_tier) + 1
        if next_idx >= len(TIER_ORDER):
            return None

        text = (completion.text or "").strip()
        tool_calls = completion.tool_calls or []

        should = False

        if not text and not tool_calls:
            should = True

        if _REFUSAL_RE.search(text) and current_tier in ("fast_local", "fast_cloud"):
            should = True

        if _GENERIC_RE.search(text) and len(self._last_text.split()) > 15:
            should = True

        expected_min_len = 20 if len(self._last_text.split()) <= 10 else 50
        if text and len(text) < expected_min_len and not tool_calls and "?" in self._last_text:
            should = True

        all_failed = tool_calls and all(getattr(c, "parse_error", None) for c in tool_calls)
        if all_failed and current_tier in ("fast_local", "fast_cloud"):
            should = True

        if should:
            self._stats[current_tier].escalations += 1
            return TIER_ORDER[next_idx]
        return None

    def record_outcome(self, tier: Tier, latency_ms: float, input_tokens: int = 0,
                       output_tokens: int = 0, success: bool = True) -> None:
        s = self._stats[tier]
        s.calls += 1
        s.total_latency_ms += latency_ms
        s.total_input_tokens += input_tokens
        s.total_output_tokens += output_tokens
        if success:
            s.successes += 1

    def tier_to_role(self, tier: Tier) -> str:
        """Map a ladder tier to the router's role string."""
        return TIER_ROLE.get(tier, "smart")

    def tier_preference(self, tier: Tier) -> dict[str, Any]:
        """Hints the router can use to pick a provider within a tier."""
        if tier == "fast_local":
            return {"prefer_local": True}
        if tier == "premium":
            return {"prefer_premium": True}
        return {}

    def stats(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for tier_name in TIER_ORDER:
            s = self._stats.get(tier_name)
            if s and s.calls > 0:
                out[tier_name] = {
                    "calls": s.calls,
                    "avg_latency_ms": round(s.avg_latency_ms, 1),
                    "success_rate": round(s.success_rate, 3),
                    "total_input_tokens": s.total_input_tokens,
                    "total_output_tokens": s.total_output_tokens,
                    "escalations": s.escalations,
                }
        return out
