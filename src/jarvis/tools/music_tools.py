"""Music search and playback: YouTube Music, Spotify search, and streaming helpers."""

from __future__ import annotations

import asyncio
import re
import webbrowser

from pydantic import Field

from ..policy import Tier
from .registry import Args, ToolError, tool


# ---------------------------------------------------------------------------
# YouTube Music search (uses yt-dlp with ytmusic: prefix)
# ---------------------------------------------------------------------------

def _ytmusic_search(query: str, max_results: int, filter_type: str) -> list[dict]:
    import yt_dlp

    search_prefix = f"ytsearch{max_results}:{filter_type} {query}" if filter_type != "all" else f"ytsearch{max_results}:{query}"
    opts = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "extract_flat": True, "default_search": f"ytsearch{max_results}",
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"{query} {filter_type}" if filter_type != "all" else query, download=False)
    entries = info.get("entries") or [] if info else []
    results = []
    for e in entries:
        duration = e.get("duration")
        dur_str = None
        if isinstance(duration, (int, float)) and duration > 0:
            mins, secs = divmod(int(duration), 60)
            dur_str = f"{mins}:{secs:02d}"
        results.append({
            "title": e.get("title", ""),
            "url": e.get("url") or f"https://music.youtube.com/watch?v={e.get('id', '')}",
            "artist": e.get("channel") or e.get("uploader") or e.get("artist") or "",
            "duration": dur_str,
            "view_count": e.get("view_count"),
        })
    return results


class MusicSearchArgs(Args):
    query: str = Field(..., min_length=1, max_length=300, description="Song, artist or album to search")
    max_results: int = Field(5, ge=1, le=20)
    filter: str = Field("song", pattern="^(song|album|artist|playlist|all)$",
                        description="Filter: song, album, artist, playlist, or all")


@tool("music_search", "Search for music across YouTube Music. Returns song/album/artist results.",
      MusicSearchArgs, Tier.T0, summary=lambda a: f"Music search: {a.query[:60]}")
async def music_search(a: MusicSearchArgs) -> dict:
    results = await asyncio.to_thread(_ytmusic_search, a.query, a.max_results, a.filter)
    if not results:
        raise ToolError(f"No music results for: {a.query}")
    return {"results": results, "count": len(results)}


# ---------------------------------------------------------------------------
# Play music (opens in browser — YouTube Music or Spotify)
# ---------------------------------------------------------------------------

class MusicPlayArgs(Args):
    query: str = Field("", max_length=300, description="Song to search and play (uses YouTube Music)")
    url: str = Field("", max_length=500, description="Direct URL to open (YouTube Music or Spotify)")


@tool("music_play", "Play a song: search by name or open a direct YouTube Music / Spotify URL.",
      MusicPlayArgs, Tier.T1, sensitive=True, summary=lambda a: f"Play: {a.query or a.url[:40]}")
async def music_play(a: MusicPlayArgs) -> dict:
    if a.url:
        if not re.match(r"https?://(music\.youtube\.com|open\.spotify\.com|www\.youtube\.com|youtu\.be)/", a.url):
            raise ToolError("URL must be a YouTube Music or Spotify link.")
        await asyncio.to_thread(webbrowser.open, a.url)
        return {"opened": a.url}

    if not a.query:
        raise ToolError("Provide a query or a URL.")

    results = await asyncio.to_thread(_ytmusic_search, a.query, 1, "song")
    if not results:
        raise ToolError(f"No results for: {a.query}")
    url = results[0]["url"]
    await asyncio.to_thread(webbrowser.open, url)
    return {"playing": results[0]["title"], "artist": results[0]["artist"], "url": url}


# ---------------------------------------------------------------------------
# Lyrics search (via web scraping a public API)
# ---------------------------------------------------------------------------

def _fetch_lyrics(artist: str, title: str) -> str:
    import urllib.request
    import json

    url = f"https://api.lyrics.ovh/v1/{urllib.parse.quote(artist)}/{urllib.parse.quote(title)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Jarvis/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        lyrics = data.get("lyrics", "")
        if not lyrics:
            raise ToolError("Lyrics not found.")
        return lyrics.strip()[:5000]
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise ToolError(f"Could not fetch lyrics: {exc}") from exc


class LyricsArgs(Args):
    artist: str = Field(..., min_length=1, max_length=200)
    title: str = Field(..., min_length=1, max_length=200)


@tool("music_lyrics", "Fetch song lyrics by artist and title.",
      LyricsArgs, Tier.T0, taints=True,
      summary=lambda a: f"Lyrics: {a.artist} — {a.title}")
async def music_lyrics(a: LyricsArgs) -> dict:
    import urllib.parse
    lyrics = await asyncio.to_thread(_fetch_lyrics, a.artist, a.title)
    return {"artist": a.artist, "title": a.title, "lyrics": lyrics}
