"""YouTube search, playback and transcript extraction via yt-dlp."""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
import webbrowser

from pydantic import Field

from ..policy import Tier
from .registry import Args, ToolError, tool


def _search(query: str, max_results: int) -> list[dict]:
    import yt_dlp

    opts = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "extract_flat": True, "default_search": f"ytsearch{max_results}",
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(query, download=False)
    entries = info.get("entries") or [] if info else []
    results = []
    for e in entries:
        duration = e.get("duration")
        if isinstance(duration, (int, float)) and duration > 0:
            mins, secs = divmod(int(duration), 60)
            dur_str = f"{mins}:{secs:02d}"
        else:
            dur_str = None
        results.append({
            "title": e.get("title", ""),
            "url": e.get("url") or f"https://www.youtube.com/watch?v={e.get('id', '')}",
            "channel": e.get("channel") or e.get("uploader") or "",
            "duration": dur_str,
            "view_count": e.get("view_count"),
        })
    return results


class SearchArgs(Args):
    query: str = Field(..., min_length=1, max_length=300, description="Search terms")
    max_results: int = Field(5, ge=1, le=20)


@tool("youtube_search", "Search YouTube and return a list of matching videos.",
      SearchArgs, Tier.T0, summary=lambda a: f"YouTube search: {a.query[:60]}")
async def youtube_search(a: SearchArgs) -> dict:
    results = await asyncio.to_thread(_search, a.query, a.max_results)
    if not results:
        raise ToolError(f"No YouTube results for: {a.query}")
    return {"results": results, "count": len(results)}


class PlayArgs(Args):
    url: str = Field(..., min_length=10, max_length=500, description="YouTube video URL")


@tool("youtube_play", "Open a YouTube video in the default browser.",
      PlayArgs, Tier.T1, sensitive=True, summary=lambda a: f"Play YouTube video")
async def youtube_play(a: PlayArgs) -> dict:
    if not re.match(r"https?://(www\.)?(youtube\.com|youtu\.be)/", a.url):
        raise ToolError("URL must be a YouTube link.")
    await asyncio.to_thread(webbrowser.open, a.url)
    return {"opened": a.url}


def _get_transcript(url: str) -> str:
    import yt_dlp

    with tempfile.TemporaryDirectory() as tmpdir:
        opts = {
            "quiet": True, "no_warnings": True, "skip_download": True,
            "writesubtitles": True, "writeautomaticsub": True,
            "subtitleslangs": ["en", "en-US", "en-GB"],
            "subtitlesformat": "vtt",
            "outtmpl": os.path.join(tmpdir, "%(id)s"),
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                raise ToolError("Could not fetch video info.")

            # Check for available subtitles
            subs = info.get("subtitles") or {}
            auto_subs = info.get("automatic_captions") or {}
            has_subs = any(lang.startswith("en") for lang in subs)
            has_auto = any(lang.startswith("en") for lang in auto_subs)

            if not has_subs and not has_auto:
                raise ToolError("No English subtitles or auto-captions available for this video.")

            ydl.download([url])

        # Find the subtitle file
        sub_file = None
        for f in os.listdir(tmpdir):
            if f.endswith(".vtt"):
                sub_file = os.path.join(tmpdir, f)
                break

        if not sub_file or not os.path.isfile(sub_file):
            raise ToolError("Subtitle file was not generated.")

        with open(sub_file, encoding="utf-8") as fh:
            raw = fh.read()

    # Strip VTT headers and timestamps, deduplicate lines
    lines: list[str] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        line = line.strip()
        if not line or line == "WEBVTT" or line.startswith("Kind:") or line.startswith("Language:"):
            continue
        if re.match(r"^\d{2}:\d{2}", line) or re.match(r"^<\d{2}:\d{2}", line):
            continue
        if line.startswith("NOTE"):
            continue
        clean = re.sub(r"<[^>]+>", "", line).strip()
        if clean and clean not in seen:
            seen.add(clean)
            lines.append(clean)

    transcript = " ".join(lines)
    if not transcript:
        raise ToolError("Subtitles file was empty after parsing.")
    title = info.get("title", "Unknown")
    return transcript[:15000]  # cap to avoid huge payloads


class TranscriptArgs(Args):
    url: str = Field(..., min_length=10, max_length=500, description="YouTube video URL")


@tool("youtube_transcript", "Extract the transcript (subtitles/auto-captions) from a YouTube video.",
      TranscriptArgs, Tier.T0, taints=True, summary=lambda a: "Get YouTube transcript")
async def youtube_transcript(a: TranscriptArgs) -> dict:
    text = await asyncio.to_thread(_get_transcript, a.url)
    return {"transcript": text, "length": len(text)}
