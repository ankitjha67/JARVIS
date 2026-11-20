"""Automatic model selection for cloud providers (FR-P8).

Provider model lists cannot be trusted on their own: they include embedding, safety and retired models,
and many chat models reject tool calls. So each candidate is *probed* with a real one-tool request, and the
choice is made from what actually worked:

  smart — highest capability estimate among models that returned a correct tool call in reasonable time
  fast  — the most capable model whose measured latency is close to the fastest one

Capability is estimated from the model id (parameter count, tier words, version); the probe supplies the
ground truth for "works with tools" and "how fast".
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
import time
from dataclasses import asdict, dataclass
from typing import Any

from .base import Completion, Provider, ProviderError

PROBE_TIMEOUT_S = 20.0
PROBE_CONCURRENCY = 4
MAX_CANDIDATES = 16
BENCH_TTL_S = 24 * 3600
SLOW_MS = 6000  # above this a model is penalised for interactive use
TOO_SLOW_MS = 15000  # above this it is never auto-selected

_PROBE_SYSTEM = "You control a computer by calling tools. Call a tool when one fits the request."
_PROBE_USER = "Set the volume to 30 percent."
_PROBE_TOOL = {
    "name": "volume_set",
    "description": "Set the system output volume.",
    "parameters": {
        "type": "object",
        "properties": {"percent": {"type": "integer", "description": "Level from 0 to 100"}},
        "required": ["percent"],
    },
}

# Ids that are never general chat models, whatever the provider's list says.
_NOT_CHAT = re.compile(
    r"(?i)embed|rerank|retriev|whisper|tts|speech|audio|dall-?e|imagen|image-|-image|video|clip|moderat|guard|"
    r"safety|pii|parse|ocr|translate|detector|calibration|diffusion|realtime|transcri|starcoder|codestral-embed"
)

_TIER_WORDS: list[tuple[str, float]] = [
    (r"ultra|opus|fable|mythos", 3.0),
    (r"\bpro\b|large|\bmax\b|super|sonnet|\bplus\b|heavy", 2.0),
    (r"flash|lightning|turbo|\bfast\b|instant", -1.0),
    (r"nano|mini|bolt|\bxs\b|lite|small|haiku|tiny|\be\d+b\b", -2.0),
]


@dataclass
class Bench:
    model: str
    ok: bool
    tool_ok: bool
    latency_ms: int
    error: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def is_chat_candidate(model_id: str) -> bool:
    return not _NOT_CHAT.search(model_id)


def quality(model_id: str) -> float:
    """Capability estimate from the id alone. Higher is better; only the ordering matters."""
    name = model_id.lower().split("/")[-1]
    score = 0.0
    sizes = [float(n) * (1000 if unit == "t" else 1) for n, unit in re.findall(r"(?<![a-z\d.])(\d+(?:\.\d+)?)([bt])\b", name)]
    if sizes:
        score += 2.0 * math.log10(max(sizes))  # total parameters, in billions
    else:
        score += 3.0  # unknown size: assume a mid-sized hosted model
    words = re.sub(r"[-_.]", " ", name)
    for pattern, weight in _TIER_WORDS:
        if re.search(pattern, words) or re.search(pattern, name):
            score += weight
            break
    version = re.search(r"(?<![\d.])(\d{1,2})(?:[.-](\d{1,2}))?(?![\d.]*[bt]\b)", name)
    if version:  # newer generations of a family rank higher ("5-5" and "5.5" both read as 5.5)
        minor = int(version.group(2) or 0)
        number = int(version.group(1)) + (minor / 10 if minor < 10 else minor / 100)
        score += min(number, 10.0) * 0.2
    if "preview" in name or "beta" in name or "exp" in words.split():
        score -= 0.3
    return round(score, 3)


def shortlist(models: list[str], limit: int = MAX_CANDIDATES) -> list[str]:
    """Which models to spend a probe on: the most capable, plus the ones most likely to be fast."""
    chat = [m for m in models if is_chat_candidate(m)]
    if len(chat) <= limit:
        return chat
    ranked = sorted(chat, key=quality, reverse=True)
    top = ranked[: limit * 2 // 3]
    speedy = [m for m in chat if m not in top and re.search(_TIER_WORDS[2][0] + "|" + _TIER_WORDS[3][0], m.lower())]
    speedy.sort(key=quality, reverse=True)
    return top + speedy[: limit - len(top)]


def signature(models: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(models)).encode()).hexdigest()[:16]


async def probe(provider: Provider, model: str) -> Bench:
    """One real request: does the model answer, does it call the tool correctly, how long does it take."""
    started = time.perf_counter()

    async def run() -> bool:
        async for event in provider.stream(model, _PROBE_SYSTEM, [{"role": "user", "content": _PROBE_USER}],
                                           [_PROBE_TOOL]):
            if isinstance(event, Completion):
                return any(c.name == "volume_set" and not c.parse_error and c.args.get("percent") == 30
                           for c in event.tool_calls)
        return False

    try:
        tool_ok = await asyncio.wait_for(run(), timeout=PROBE_TIMEOUT_S)
        return Bench(model, True, tool_ok, int((time.perf_counter() - started) * 1000))
    except TimeoutError:
        return Bench(model, False, False, int(PROBE_TIMEOUT_S * 1000), "timed out")
    except ProviderError as exc:
        return Bench(model, False, False, int((time.perf_counter() - started) * 1000), exc.kind)
    except Exception as exc:  # noqa: BLE001 - a probe must never raise into the router
        return Bench(model, False, False, int((time.perf_counter() - started) * 1000), type(exc).__name__)


async def benchmark(provider: Provider, models: list[str]) -> list[Bench]:
    sem = asyncio.Semaphore(PROBE_CONCURRENCY)

    async def one(model: str) -> Bench:
        async with sem:
            first = await probe(provider, model)
            if not first.tool_ok:
                return first
            second = await probe(provider, model)  # a second sample steadies the latency figure
            if second.tool_ok:
                first.latency_ms = (first.latency_ms + second.latency_ms) // 2
            return first

    return list(await asyncio.gather(*(one(m) for m in shortlist(models))))


def ranked(results: list[Bench]) -> list[str]:
    """Every usable model, best first — the fallback order when the chosen model stalls."""
    usable = [b for b in results if b.tool_ok and b.latency_ms <= TOO_SLOW_MS]
    return [b.model for b in sorted(usable, key=lambda b: quality(b.model) - max(0, b.latency_ms - SLOW_MS) / 2000,
                                    reverse=True)]


def choose(results: list[Bench]) -> tuple[str | None, str | None]:
    """(smart, fast) from benchmark results; (None, None) when nothing handled tools."""
    usable = [b for b in results if b.tool_ok and b.latency_ms <= TOO_SLOW_MS]
    if not usable:
        return None, None

    def smart_score(b: Bench) -> float:
        return quality(b.model) - max(0, b.latency_ms - SLOW_MS) / 2000

    smart = max(usable, key=smart_score)
    quickest = min(b.latency_ms for b in usable)
    window = max(quickest * 2, 1500)
    fast = max((b for b in usable if b.latency_ms <= window), key=lambda b: (quality(b.model), -b.latency_ms))
    return smart.model, fast.model
