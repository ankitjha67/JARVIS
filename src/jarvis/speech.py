"""Speech engines with universal fallback chains (FR-V8, expanded).

Every STT and TTS engine falls back to the next in the chain if it fails. Free engines (edge-tts,
Groq free tier, Google Cloud free tier, Azure free tier) are preferred over paid ones.

STT fallback chain: chosen engine → Groq (free 2000 req/day) → AssemblyAI → Google Cloud (60 min/mo free)
                    → Azure (5 hr/mo free) → local Whisper (always works).
TTS fallback chain: chosen engine → edge-tts (free, no key, 400+ voices) → Piper (local, neural)
                    → Groq PlayAI → Windows voice (always works).

Keys: model providers reuse their existing keys. Deepgram and ElevenLabs keys are in the vault.
edge-tts needs NO key. Piper needs no key (local ONNX models).
"""

from __future__ import annotations

import io
import logging
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2 as httpx

from . import secrets
from .config import all_specs

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
TIMEOUT_S = 20.0


@dataclass(frozen=True)
class Engine:
    id: str
    label: str
    kind: str  # local | openai | deepgram | elevenlabs | unavailable
    model: str = ""
    voice: str = ""
    note: str = ""


STT_ENGINES: tuple[Engine, ...] = (
    Engine("local", "On this computer (Whisper base)", "local", note="Private. Small model; can mishear accents."),
    Engine("groq", "Groq · Whisper large v3 turbo", "openai", "whisper-large-v3-turbo",
           note="FREE: 2,000 req/day. Fast and accurate."),
    Engine("openai", "OpenAI · GPT-4o mini transcribe", "openai", "gpt-4o-mini-transcribe"),
    Engine("deepgram", "Deepgram · Nova 3", "deepgram", "nova-3",
           note="$200 free credit on signup. Key: deepgram"),
    Engine("elevenlabs", "ElevenLabs · Scribe", "elevenlabs", "scribe_v1",
           note="Key: elevenlabs"),
    Engine("assemblyai", "AssemblyAI · Best", "assemblyai", "best",
           note="Free tier available. Key: assemblyai"),
    Engine("google_cloud", "Google Cloud Speech", "google_cloud", "latest_long",
           note="FREE: 60 min/month. Key: google_cloud"),
    Engine("azure", "Azure Speech", "azure", "",
           note="FREE: 5 hours/month. Key: azure_speech"),
    Engine("nvidia", "NVIDIA speech (gRPC only, not HTTP)", "unavailable"),
)
TTS_ENGINES: tuple[Engine, ...] = (
    Engine("system", "Windows voice (on this computer)", "local", note="Private and instant."),
    Engine("edge_tts", "Microsoft Edge TTS (free, no key)", "edge_tts", "", "en-US-GuyNeural",
           note="FREE: no key needed, 400+ voices, natural quality."),
    Engine("piper", "Piper TTS (local neural voice)", "piper", "en_US-lessac-medium", "",
           note="FREE: local ONNX, fast, natural. No key needed."),
    Engine("openai", "OpenAI · GPT-4o mini TTS", "openai", "gpt-4o-mini-tts", "onyx"),
    Engine("groq", "Groq · PlayAI TTS (free tier)", "openai", "playai-tts", "Fritz-PlayAI",
           note="FREE: same limits as STT."),
    Engine("deepgram", "Deepgram · Aura 2", "deepgram", "aura-2-orion-en",
           note="$200 free credit. Key: deepgram"),
    Engine("elevenlabs", "ElevenLabs · Flash v2.5", "elevenlabs", "eleven_flash_v2_5", "JBFqnCBsd6RMkjVDRZzb",
           note="Key: elevenlabs"),
    Engine("nvidia", "NVIDIA Magpie TTS (gRPC only, not HTTP)", "unavailable"),
)

STT_FALLBACK_ORDER = ["groq", "deepgram", "openai", "assemblyai", "google_cloud", "azure", "elevenlabs", "local"]
TTS_FALLBACK_ORDER = ["edge_tts", "piper", "groq", "openai", "deepgram", "elevenlabs", "system"]


def _engine(engines: tuple[Engine, ...], engine_id: str, settings: dict[str, Any]) -> tuple[Engine, str] | None:
    """The engine and its base URL. Custom OpenAI-compatible endpoints are accepted by their provider id."""
    for engine in engines:
        if engine.id == engine_id:
            base = next((s.base_url or "" for s in all_specs(settings) if s.id == engine_id), "")
            return engine, base
    for spec in all_specs(settings):
        if spec.id == engine_id and spec.kind == "openai" and spec.base_url:
            return Engine(spec.id, spec.label, "openai"), spec.base_url
    return None


def catalogue(settings: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """What the Settings screen offers, and which engines have a key saved."""
    cloud_ok = settings.get("privacy_mode") != "local"

    def rows(engines: tuple[Engine, ...]) -> list[dict[str, Any]]:
        out = [{"id": e.id, "label": e.label, "note": e.note, "model": e.model, "voice": e.voice,
                "ready": e.kind == "local" or (e.kind != "unavailable" and cloud_ok and secrets.has_key(e.id)),
                "selectable": e.kind != "unavailable"} for e in engines]
        known = {e.id for e in engines}
        for spec in all_specs(settings):
            if spec.id not in known and spec.id in (settings.get("custom_providers") or {}):
                out.append({"id": spec.id, "label": f"{spec.label} (custom endpoint)", "note": "OpenAI-compatible audio API",
                            "model": "", "voice": "", "selectable": True,
                            "ready": (spec.local or cloud_ok) and (not spec.needs_key or secrets.has_key(spec.id))})
        return out

    return {"stt": rows(STT_ENGINES), "tts": rows(TTS_ENGINES)}


def to_wav(pcm: Any, rate: int = SAMPLE_RATE) -> bytes:
    """int16 mono samples (numpy array or bytes) → a WAV file in memory."""
    data = pcm if isinstance(pcm, bytes | bytearray) else pcm.astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(data)
    return buf.getvalue()


def _check(response: Any, what: str) -> Any:
    if response.status_code >= 400:
        raise RuntimeError(f"{what} failed ({response.status_code}): {secrets.redact(response.text[:200])}")
    return response


def _transcribe_one(pcm: Any, engine_id: str, settings: dict[str, Any], model: str = "",
                    client: Any = None) -> str:
    """Send one utterance to a single engine. Raises on any failure."""
    found = _engine(STT_ENGINES, engine_id, settings)
    if found is None or found[0].kind in ("local", "unavailable"):
        raise RuntimeError(f"{engine_id} is not a cloud speech-to-text engine")
    if settings.get("privacy_mode") == "local":
        raise RuntimeError("privacy mode is local-only")
    engine, base = found
    key = secrets.get_key(engine.id)
    model = model or engine.model
    wav = to_wav(pcm)
    http = client or httpx.Client(timeout=TIMEOUT_S)
    try:
        if engine.kind == "openai":
            if not key and engine.id not in ("groq",):
                raise RuntimeError(f"no key saved for {engine.id}")
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            r = _check(http.post(base.rstrip("/") + "/audio/transcriptions", headers=headers,
                                 files={"file": ("request.wav", wav, "audio/wav")},
                                 data={"model": model or "whisper-1", "language": "en", "response_format": "json"}),
                       "transcription")
            return str(r.json().get("text", "")).strip()
        if engine.kind == "deepgram":
            if not key:
                raise RuntimeError("no key saved for deepgram")
            r = _check(http.post("https://api.deepgram.com/v1/listen", headers={"Authorization": f"Token {key}",
                                 "Content-Type": "audio/wav"}, params={"model": model, "smart_format": "true",
                                                                         "language": "en"}, content=wav),
                       "transcription")
            return str(r.json()["results"]["channels"][0]["alternatives"][0].get("transcript", "")).strip()
        if engine.kind == "elevenlabs":
            if not key:
                raise RuntimeError("no key saved for elevenlabs")
            r = _check(http.post("https://api.elevenlabs.io/v1/speech-to-text", headers={"xi-api-key": key},
                                 files={"file": ("request.wav", wav, "audio/wav")},
                                 data={"model_id": model, "language_code": "en"}), "transcription")
            return str(r.json().get("text", "")).strip()
        if engine.kind == "assemblyai":
            if not key:
                raise RuntimeError("no key saved for assemblyai")
            upload_r = _check(http.post("https://api.assemblyai.com/v2/upload",
                                        headers={"Authorization": key, "Content-Type": "application/octet-stream"},
                                        content=wav), "upload")
            upload_url = upload_r.json()["upload_url"]
            r = _check(http.post("https://api.assemblyai.com/v2/transcript",
                                 headers={"Authorization": key},
                                 json={"audio_url": upload_url, "language_code": "en"}), "transcription")
            tid = r.json()["id"]
            import time
            for _ in range(60):
                time.sleep(1)
                poll = http.get(f"https://api.assemblyai.com/v2/transcript/{tid}",
                                headers={"Authorization": key})
                data = poll.json()
                if data.get("status") == "completed":
                    return str(data.get("text", "")).strip()
                if data.get("status") == "error":
                    raise RuntimeError(f"AssemblyAI error: {data.get('error', 'unknown')}")
            raise RuntimeError("AssemblyAI transcription timed out")
        if engine.kind == "google_cloud":
            raise RuntimeError("Google Cloud Speech requires the google-cloud-speech SDK (install separately)")
        if engine.kind == "azure":
            raise RuntimeError("Azure Speech requires the azure-cognitiveservices-speech SDK (install separately)")
        raise RuntimeError(f"unsupported STT engine kind: {engine.kind}")
    finally:
        if client is None:
            http.close()


def transcribe(pcm: Any, engine_id: str, settings: dict[str, Any], model: str = "",
               client: Any = None) -> str:
    """Transcribe with automatic fallback through the chain."""
    if settings.get("privacy_mode") == "local":
        found = _engine(STT_ENGINES, engine_id, settings)
        if found and found[0].kind not in ("local",):
            raise RuntimeError("privacy mode is local-only")
    errors = []
    engines_to_try = [engine_id] + [e for e in STT_FALLBACK_ORDER if e != engine_id]
    for eid in engines_to_try:
        is_primary = eid == engine_id
        if eid == "local":
            continue
        found = _engine(STT_ENGINES, eid, settings)
        if found is None or found[0].kind in ("unavailable",):
            if is_primary:
                raise RuntimeError(f"{eid} is not a cloud speech-to-text engine")
            continue
        if found[0].kind != "local" and settings.get("privacy_mode") == "local":
            continue
        if not is_primary:
            key = secrets.get_key(eid)
            if found[0].kind not in ("local", "edge_tts", "piper") and not key and eid not in ("groq",):
                continue
        try:
            result = _transcribe_one(pcm, eid, settings, model if is_primary else "", client)
            if result:
                if not is_primary:
                    log.info("STT fallback: %s → %s succeeded", engine_id, eid)
                return result
        except Exception as exc:
            if is_primary:
                raise
            errors.append(f"{eid}: {exc}")
            log.debug("STT %s failed, trying next: %s", eid, exc)
    raise RuntimeError(f"all STT engines failed: {'; '.join(errors)}")


def _synthesize_edge_tts(text: str, voice: str = "en-US-GuyNeural") -> bytes:
    """Free Microsoft Edge TTS — no key needed, 400+ voices."""
    import asyncio
    import tempfile

    async def _gen() -> bytes:
        import edge_tts
        comm = edge_tts.Communicate(text, voice or "en-US-GuyNeural")
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            tmp = f.name
        await comm.save(tmp)
        import subprocess
        wav_tmp = tmp.replace(".mp3", ".wav")
        try:
            subprocess.run(["ffmpeg", "-y", "-i", tmp, "-ar", "24000", "-ac", "1", wav_tmp],
                           capture_output=True, timeout=10)
            data = Path(wav_tmp).read_bytes()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            data = Path(tmp).read_bytes()
        finally:
            Path(tmp).unlink(missing_ok=True)
            Path(wav_tmp).unlink(missing_ok=True)
        return data

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(lambda: asyncio.run(_gen())).result(timeout=30)
    return asyncio.run(_gen())


def _synthesize_piper(text: str, model: str = "en_US-lessac-medium") -> bytes:
    """Local neural TTS using Piper (ONNX). No key needed."""
    try:
        from piper import PiperVoice
        voice = PiperVoice.load(model)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(voice.config.sample_rate)
            for audio_bytes in voice.synthesize_stream_raw(text):
                out.writeframes(audio_bytes)
        return buf.getvalue()
    except ImportError:
        raise RuntimeError("piper-tts not installed")
    except Exception as exc:
        raise RuntimeError(f"piper failed: {exc}")


def _synthesize_one(text: str, engine_id: str, settings: dict[str, Any], model: str = "", voice: str = "",
                    client: Any = None) -> bytes:
    """Synthesize with a single engine. Raises on failure."""
    found = _engine(TTS_ENGINES, engine_id, settings)
    if found is None or found[0].kind in ("unavailable",):
        raise RuntimeError(f"{engine_id} is not available")
    engine, base = found

    if engine.kind == "edge_tts":
        return _synthesize_edge_tts(text, voice or engine.voice)
    if engine.kind == "piper":
        return _synthesize_piper(text, model or engine.model)
    if engine.kind == "local":
        raise RuntimeError("use the system fallback for local voice")

    if settings.get("privacy_mode") == "local":
        raise RuntimeError("privacy mode is local-only")
    key = secrets.get_key(engine.id)
    model, voice = model or engine.model, voice or engine.voice
    http = client or httpx.Client(timeout=TIMEOUT_S)
    try:
        if engine.kind == "openai":
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            r = _check(http.post(base.rstrip("/") + "/audio/speech", headers=headers,
                                 json={"model": model, "voice": voice or "alloy", "input": text,
                                       "response_format": "wav"}), "speech")
            return bytes(r.content)
        if engine.kind == "deepgram":
            if not key:
                raise RuntimeError("no key saved for deepgram")
            r = _check(http.post("https://api.deepgram.com/v1/speak", headers={"Authorization": f"Token {key}"},
                                 params={"model": voice or model, "encoding": "linear16", "container": "wav",
                                         "sample_rate": "24000"}, json={"text": text}), "speech")
            return bytes(r.content)
        if engine.kind == "elevenlabs":
            if not key:
                raise RuntimeError("no key saved for elevenlabs")
            r = _check(http.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
                                 headers={"xi-api-key": key}, params={"output_format": "pcm_16000"},
                                 json={"text": text, "model_id": model}), "speech")
            return to_wav(bytes(r.content), 16000)
        raise RuntimeError(f"unsupported TTS engine kind: {engine.kind}")
    finally:
        if client is None:
            http.close()


def synthesize(text: str, engine_id: str, settings: dict[str, Any], model: str = "", voice: str = "",
               client: Any = None) -> bytes:
    """Synthesize with automatic fallback through the chain."""
    errors = []
    engines_to_try = [engine_id] + [e for e in TTS_FALLBACK_ORDER if e != engine_id]
    for eid in engines_to_try:
        if eid == "system":
            continue
        found = _engine(TTS_ENGINES, eid, settings)
        if found is None or found[0].kind in ("unavailable",):
            continue
        if found[0].kind not in ("local", "edge_tts", "piper") and settings.get("privacy_mode") == "local":
            continue
        try:
            result = _synthesize_one(text, eid, settings, model if eid == engine_id else "",
                                     voice if eid == engine_id else "", client)
            if result:
                if eid != engine_id:
                    log.info("TTS fallback: %s → %s succeeded", engine_id, eid)
                return result
        except Exception as exc:
            errors.append(f"{eid}: {exc}")
            log.debug("TTS %s failed, trying next: %s", eid, exc)
    raise RuntimeError(f"all TTS engines failed: {'; '.join(errors)}")


_play_lock = threading.Lock()


def play(wav: bytes) -> None:
    """Play a WAV through the default output and wait for it to finish."""
    from . import host

    with _play_lock:
        host.play_wav(wav)


def say(text: str, settings: dict[str, Any], fallback: Any) -> None:
    """Speak with the chosen engine without blocking; any failure falls back to the system voice."""
    voice_cfg = settings.get("voice") or {}
    engine_id = str(voice_cfg.get("tts_engine") or "system")
    if engine_id == "system" or not text.strip():
        fallback()
        return

    def run() -> None:
        try:
            play(synthesize(text[:900], engine_id, settings, str(voice_cfg.get("tts_cloud_model") or ""),
                            str(voice_cfg.get("tts_cloud_voice") or "")))
        except Exception as exc:  # noqa: BLE001 - never leave the user without an answer
            log.warning("cloud voice failed, using the Windows voice: %s", secrets.redact(str(exc))[:200])
            fallback()

    threading.Thread(target=run, name="jarvis-cloud-voice", daemon=True).start()
