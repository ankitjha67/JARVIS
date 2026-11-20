"""Deep research pipeline with source verification (Mark-LVII).

Multi-step research: decompose question -> search multiple sources ->
cross-reference claims -> synthesize report with citations and confidence.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .providers import Completion
from .providers.router import Route

log = logging.getLogger(__name__)

ProgressCallback = Callable[[str, str, float], Awaitable[None]] | None


@dataclass
class Source:
    url: str
    title: str
    snippet: str
    relevance_score: float = 0.0
    verified: bool = False

    def to_json(self) -> dict[str, Any]:
        return {"url": self.url, "title": self.title, "snippet": self.snippet,
                "relevance_score": round(self.relevance_score, 2), "verified": self.verified}


@dataclass
class Claim:
    text: str
    sources: list[Source] = field(default_factory=list)
    confidence: float = 0.0
    contradicted_by: list[Source] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {"text": self.text, "confidence": round(self.confidence, 2),
                "sources": [s.to_json() for s in self.sources],
                "contradicted_by": [s.to_json() for s in self.contradicted_by]}


@dataclass
class ResearchReport:
    id: str
    question: str
    sub_questions: list[str] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    synthesis: str = ""
    sources: list[Source] = field(default_factory=list)
    confidence: float = 0.0
    created_at: str = ""
    status: str = "running"  # running | done | failed
    error: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id, "question": self.question, "sub_questions": self.sub_questions,
            "claims": [c.to_json() for c in self.claims], "synthesis": self.synthesis,
            "sources": [s.to_json() for s in self.sources],
            "confidence": round(self.confidence, 2), "created_at": self.created_at,
            "status": self.status, "error": self.error,
        }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _extract_json(text: str) -> Any:
    """Pull the first JSON object or array from model output, tolerating markdown fences."""
    text = re.sub(r"```(?:json)?\s*", "", text).replace("```", "")
    for start, end in [("{", "}"), ("[", "]")]:
        idx = text.find(start)
        if idx == -1:
            continue
        depth = 0
        for i in range(idx, len(text)):
            if text[i] == start:
                depth += 1
            elif text[i] == end:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[idx:i + 1])
                    except json.JSONDecodeError:
                        break
    return None


class DeepResearcher:
    """Runs multi-step research with source cross-referencing."""

    def __init__(self, router: Any, store: Any) -> None:
        self._router = router
        self._store = store
        self._active: dict[str, ResearchReport] = {}

    def active_reports(self) -> list[dict[str, Any]]:
        return [r.to_json() for r in self._active.values()]

    def get_report(self, report_id: str) -> ResearchReport | None:
        return self._active.get(report_id)

    async def research(self, question: str, on_progress: ProgressCallback = None,
                       max_sources: int = 10) -> ResearchReport:
        report = ResearchReport(
            id=uuid.uuid4().hex[:12], question=question, created_at=_now(),
        )
        self._active[report.id] = report

        try:
            if on_progress:
                await on_progress("decompose", "Breaking question into sub-questions", 0.05)
            report.sub_questions = await self._decompose(question)

            if on_progress:
                await on_progress("search", f"Searching {len(report.sub_questions)} sub-questions", 0.15)
            report.sources = await self._search_all(report.sub_questions, max_sources)

            if on_progress:
                await on_progress("extract", f"Extracting claims from {len(report.sources)} sources", 0.45)
            report.claims = await self._extract_claims(question, report.sources)

            if on_progress:
                await on_progress("verify", f"Cross-referencing {len(report.claims)} claims", 0.65)
            report.claims = await self._cross_reference(report.claims, report.sources)

            if on_progress:
                await on_progress("synthesize", "Writing final synthesis", 0.85)
            report.synthesis = await self._synthesize(question, report.claims, report.sources)

            report.confidence = self._compute_confidence(report)
            report.status = "done"
            if on_progress:
                await on_progress("done", "Research complete", 1.0)

        except asyncio.CancelledError:
            report.status = "failed"
            report.error = "Cancelled"
            raise
        except Exception as exc:
            log.exception("deep research failed for: %s", question[:80])
            report.status = "failed"
            report.error = str(exc)[:300]

        return report

    async def _ask(self, system: str, user_text: str) -> str:
        text = ""
        async for event in self._router.stream(system, [{"role": "user", "content": user_text}], [], "smart"):
            if isinstance(event, Completion):
                text = event.text or text
        return text

    async def _decompose(self, question: str) -> list[str]:
        system = (
            "You are a research planner. Given a research question, decompose it into 3-5 specific "
            "sub-questions that together cover the topic comprehensively. Output ONLY a JSON array of strings. "
            "Example: [\"What is X?\", \"How does X affect Y?\", \"What are the alternatives to X?\"]"
        )
        raw = await self._ask(system, f"Research question: {question}")
        parsed = _extract_json(raw)
        if isinstance(parsed, list) and all(isinstance(s, str) for s in parsed):
            return parsed[:5]
        lines = [l.strip().lstrip("0123456789.-) ") for l in raw.strip().splitlines() if l.strip()]
        return [l for l in lines if len(l) > 10][:5] or [question]

    async def _search_all(self, sub_questions: list[str], max_sources: int) -> list[Source]:
        all_sources: list[Source] = []
        seen_urls: set[str] = set()

        per_q = max(2, max_sources // max(len(sub_questions), 1))
        tasks = [self._search_one(q, per_q) for q in sub_questions]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                log.warning("sub-question search failed: %s", result)
                continue
            for src in result:
                if src.url not in seen_urls:
                    seen_urls.add(src.url)
                    all_sources.append(src)
                    if len(all_sources) >= max_sources:
                        return all_sources
        return all_sources

    async def _search_one(self, query: str, max_results: int) -> list[Source]:
        system = (
            "You are a web research assistant. For the given query, provide real, specific sources you know about. "
            "Output ONLY a JSON array of objects with keys: url, title, snippet, relevance_score (0-1). "
            "Only include sources you are confident exist. If unsure, return fewer results. "
            "relevance_score reflects how directly the source answers the query."
        )
        raw = await self._ask(system, f"Find sources for: {query}")
        parsed = _extract_json(raw)
        sources: list[Source] = []
        if isinstance(parsed, list):
            for item in parsed[:max_results]:
                if isinstance(item, dict) and "url" in item and "title" in item:
                    sources.append(Source(
                        url=str(item.get("url", "")),
                        title=str(item.get("title", "")),
                        snippet=str(item.get("snippet", ""))[:500],
                        relevance_score=min(1.0, max(0.0, float(item.get("relevance_score", 0.5)))),
                    ))
        if not sources:
            sources.append(Source(
                url=f"https://www.google.com/search?q={query.replace(' ', '+')}",
                title=f"Search: {query}", snippet="Model-based knowledge (no live web search available)",
                relevance_score=0.3,
            ))
        return sources

    async def _extract_claims(self, question: str, sources: list[Source]) -> list[Claim]:
        source_text = "\n".join(
            f"[{i+1}] {s.title} ({s.url}): {s.snippet}" for i, s in enumerate(sources)
        )
        system = (
            "You are a research analyst. Given a question and sources, extract the key factual claims. "
            "Output ONLY a JSON array of objects with keys: text (the claim), source_indices (array of 1-based "
            "indices into the source list), confidence (0-1 based on source quality and agreement). "
            "Extract 3-10 distinct claims that answer the research question."
        )
        user = f"Question: {question}\n\nSources:\n{source_text}"
        raw = await self._ask(system, user)
        parsed = _extract_json(raw)
        claims: list[Claim] = []
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict) and "text" in item:
                    indices = item.get("source_indices", [])
                    claim_sources = []
                    for idx in indices:
                        if isinstance(idx, int) and 1 <= idx <= len(sources):
                            claim_sources.append(sources[idx - 1])
                    claims.append(Claim(
                        text=str(item["text"]),
                        sources=claim_sources,
                        confidence=min(1.0, max(0.0, float(item.get("confidence", 0.5)))),
                    ))
        if not claims:
            claims.append(Claim(text=f"Unable to extract structured claims for: {question}", confidence=0.1))
        return claims

    async def _cross_reference(self, claims: list[Claim], sources: list[Source]) -> list[Claim]:
        if len(claims) <= 1:
            return claims

        claims_text = "\n".join(f"[{i+1}] {c.text} (confidence: {c.confidence})" for i, c in enumerate(claims))
        source_text = "\n".join(f"[{i+1}] {s.title}: {s.snippet}" for i, s in enumerate(sources))
        system = (
            "You are a fact-checker. Given claims and sources, identify which claims are contradicted by other "
            "sources and adjust confidence scores. Output ONLY a JSON array of objects with keys: "
            "claim_index (1-based), adjusted_confidence (0-1), contradicted_by_source_indices (array, possibly empty), "
            "verification_note (brief explanation)."
        )
        user = f"Claims:\n{claims_text}\n\nSources:\n{source_text}"
        raw = await self._ask(system, user)
        parsed = _extract_json(raw)
        if isinstance(parsed, list):
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                idx = item.get("claim_index", 0)
                if isinstance(idx, int) and 1 <= idx <= len(claims):
                    claim = claims[idx - 1]
                    new_conf = item.get("adjusted_confidence")
                    if isinstance(new_conf, (int, float)):
                        claim.confidence = min(1.0, max(0.0, float(new_conf)))
                    contra_indices = item.get("contradicted_by_source_indices", [])
                    for ci in contra_indices:
                        if isinstance(ci, int) and 1 <= ci <= len(sources):
                            claim.contradicted_by.append(sources[ci - 1])
                            sources[ci - 1].verified = True
                    for src in claim.sources:
                        src.verified = True
        return claims

    async def _synthesize(self, question: str, claims: list[Claim], sources: list[Source]) -> str:
        claims_text = "\n".join(
            f"- {c.text} [confidence: {c.confidence:.0%}, sources: {len(c.sources)}, "
            f"contradictions: {len(c.contradicted_by)}]"
            for c in claims
        )
        source_list = "\n".join(f"[{i+1}] {s.title} ({s.url})" for i, s in enumerate(sources))
        system = (
            "You are a research synthesizer. Given verified claims and their sources, write a clear, "
            "well-structured research summary that answers the original question. Include inline citations "
            "as [n] referencing the source list. Note areas of uncertainty or contradiction. "
            "Be thorough but concise. Do not use markdown headers."
        )
        user = (
            f"Question: {question}\n\nVerified Claims:\n{claims_text}\n\nSource List:\n{source_list}"
        )
        return await self._ask(system, user)

    def _compute_confidence(self, report: ResearchReport) -> float:
        if not report.claims:
            return 0.0
        avg_claim = sum(c.confidence for c in report.claims) / len(report.claims)
        source_diversity = min(1.0, len(report.sources) / 5.0)
        verified_ratio = sum(1 for s in report.sources if s.verified) / max(len(report.sources), 1)
        contradiction_penalty = sum(
            len(c.contradicted_by) for c in report.claims
        ) / max(len(report.claims) * 3, 1)
        return max(0.0, min(1.0,
            avg_claim * 0.4 + source_diversity * 0.2 + verified_ratio * 0.25 - contradiction_penalty * 0.15
        ))
