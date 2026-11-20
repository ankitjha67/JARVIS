"""Wake word: say "Jarvis" and the HUD comes forward (FR-V5), optionally with a command ("Jarvis, open Chrome").

Two local stages listen to the default microphone. Nothing is recorded to disk or sent anywhere.

1. Fast path — openWakeWord (ONNX) scores every 80 ms frame. It was trained on "hey Jarvis" and fires
   almost instantly on that phrase, but measurements showed plain "Jarvis" is unreliable in room noise.
2. Speech path — an energy detector cuts the audio into utterances and a small local Whisper model
   transcribes each one. If the text contains "Jarvis" anywhere, Jarvis wakes and the rest of what
   was said becomes the request. Measured on synthesized speech at 0, -30 and -40 dB with noise: "Jarvis" and
   "Jarvis, open Chrome" recognised every time; "Travis…", "service", "harvest" never matched.

Transcripts of speech that does not contain the wake word are discarded immediately and never logged.
"""

from __future__ import annotations

import logging
import os
import queue
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .voice_training import apply_corrections
from .voiceprint import Voiceprint, trim

log = logging.getLogger(__name__)

MODELS_DIR = DATA_DIR / "wake"
STT_DIR = DATA_DIR / "stt"
STT_MODEL = "base.en"  # tiny.en missed an accented "Jarvis" about 40 times in a row in real use
SAMPLE_RATE = 16000
FRAME = 1280  # 80 ms, the frame size the wake model expects
COOLDOWN_S = 2.0  # after a wake, ignore further triggers for this long (the same word reaching another path)
FINAL_WINDOW_S = 12.0  # how long after an early wake the end of that utterance may still arrive
VERIFY_SCORE = 0.10  # a faint match on the wake model: check the last moments of audio right away
BACKED_SCORE = 0.12  # the wake model half-heard "hey Jarvis": the words then only need to look roughly right
# Set JARVIS_WAKE_DEBUG=1 to log what short phrases were heard as (for diagnosing a missed wake). Off by default:
# normally no words are ever logged.
DEBUG_HEARD = os.environ.get("JARVIS_WAKE_DEBUG") == "1"
EARLY_FRAMES = 9  # ~0.7 s into an utterance, transcribe what there is so the name is caught before it ends
RING_FRAMES = 24  # ~1.9 s of recent audio kept in memory for those checks
LISTEN_WINDOW_S = 10.0  # default: how long Jarvis keeps listening after its name (Settings: 5-30 s)
LISTEN_PAUSE_S = 2.5  # default: act sooner once the user has been quiet this long (0 = always use the full window)
LOOKBACK_S = 6.0  # "open the calculator… Jarvis": a sentence finished this recently before the name is the request
LISTEN_OVERRUN_S = 6.0  # if the user is mid-sentence when the window ends, let them finish (up to this long)
PARTIAL_EVERY_FRAMES = 11  # refresh the live transcript about every 0.9 s
SILENCE_PEAK = 3  # of 32767: only true digital silence (a muted or blocked device) counts
SILENCE_AFTER_S = 8.0
STATS_EVERY_S = 20.0
_REQUIRED = ("melspectrogram.onnx", "embedding_model.onnx")

# (confidence, text, stage):
#   "early"   — the name was just heard: acknowledge at once and start listening for the request.
#   "partial" — words recognised so far while the user is still speaking (for the live display).
#   "final"   — the request is complete; `text` is the command ("" only when listening mode is off).
#   "ended"   — listening closed without a request (the user said the name and then nothing).
WakeHandler = Callable[[float, str, str], None]


def _model_files() -> tuple[Path, Path, Path] | None:
    word = sorted(MODELS_DIR.glob("hey_jarvis*.onnx"))
    if not word or not all((MODELS_DIR / n).is_file() for n in _REQUIRED):
        return None
    return word[-1], MODELS_DIR / _REQUIRED[0], MODELS_DIR / _REQUIRED[1]


def ensure_models() -> tuple[Path, Path, Path]:
    """Fetch the three small wake-model files (about 4 MB) on first use."""
    files = _model_files()
    if files:
        return files
    from openwakeword import utils

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    utils.download_models(["hey_jarvis"], target_directory=str(MODELS_DIR))
    files = _model_files()
    if not files:
        raise RuntimeError("the wake-word model could not be downloaded")
    return files


def load_model() -> Any:
    from openwakeword.model import Model

    word, melspec, embedding = ensure_models()
    return Model(wakeword_models=[str(word)], inference_framework="onnx",
                 melspec_model_path=str(melspec), embedding_model_path=str(embedding))


def load_stt(name: str = STT_MODEL) -> Any:
    """English speech model (about 145 MB, downloaded once into data/stt)."""
    from faster_whisper import WhisperModel

    STT_DIR.mkdir(parents=True, exist_ok=True)
    return WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=4, download_root=str(STT_DIR))


# How speech models spell "Jarvis" for different voices and accents. Anything within one letter of
# "jarvis" is also accepted (see _is_name), and the user can teach further spellings by calibrating.
NAME_SPELLINGS = frozenset({
    "jarvis", "jervis", "jarvus", "jarves", "jarvas", "jarviss", "jarvish", "jarviz", "jarwis", "javis",
    "jarvice", "jarvys", "jarvies", "jarbis", "jarfis", "jorvis", "jurvis", "charvis", "djarvis", "zarvis",
})
_NOT_NAMES = frozenset({"travis", "jarvik", "marvin", "service", "nervous", "harvest", "jars", "jarring",
                        "jeeves", "chafes", "chavez", "javas", "jobs", "jabs"})
# The sound of the name rather than its spelling: j/ch, an open vowel, an optional r/n/l, v/b/f, a vowel, s.
# Covers what speech models write for accented voices ("Janvas", "Jarbis", "Charvis", "Jaavis", "Jalvis").
_NAME_SOUND = re.compile(r"^(?:j|dj|zh|ch)[aeou]+h?[rnl]{0,2}[vbf]+[aeiouy]+(?:s|ss|z|sh|ce|se|x)$")
# The name written as two words ("jar this", "jar vis"), keyed by the first word.
_SPLIT_NAMES = {"jar": {"vis", "this", "viss", "vice", "wis", "bis", "fis", "us"},
                "char": {"vis", "this", "vice"}, "ja": {"vis", "vice"}, "jah": {"vis", "vice"},
                "jarv": {"is", "us"}, "jarve": {"is", "us"}}
_GREETINGS = frozenset({"hey", "hi", "hello", "ok", "okay", "yo", "hay", "a", "eh", "oh"})
_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


def _within_one_edit(word: str, target: str = "jarvis") -> bool:
    if word == target:
        return True
    if abs(len(word) - len(target)) > 1:
        return False
    if len(word) == len(target):
        return sum(a != b for a, b in zip(word, target, strict=True)) == 1
    short, long = (word, target) if len(word) < len(target) else (target, word)
    return any(long[:i] + long[i + 1:] == short for i in range(len(long)))


def _is_name(word: str) -> bool:
    word = word.lower()
    word = word.removesuffix("'s")
    if word in _NOT_NAMES:
        return False
    if word in NAME_SPELLINGS or (len(word) >= 5 and word[0] in "jgc" and _within_one_edit(word)):
        return True
    return 5 <= len(word) <= 9 and _NAME_SOUND.match(word) is not None


def normalise_phrase(text: str) -> str:
    """Lower-case words only, single-spaced: the form aliases are stored and compared in."""
    return " ".join(m.group(0).lower() for m in _WORD_RE.finditer(text))


def find_wake(text: str, aliases: tuple[str, ...] = ()) -> tuple[bool, str]:
    """Does the transcript contain the wake word, and what is being asked?

    The name may come anywhere: "Jarvis, open Chrome", "open Chrome, Jarvis" and "can you, Jarvis, open
    Chrome" all give the request "open Chrome" (the name and a greeting before it are taken out).
    `aliases` are extra spellings learned from the user's own voice (normalised phrases of 1-3 words).
    """
    words = list(_WORD_RE.finditer(text))
    lowered = [w.group(0).lower() for w in words]
    hit: tuple[int, int] | None = None  # (index of first word of the name, number of words it spans)
    for i, word in enumerate(lowered):
        for alias in aliases:
            parts = alias.split()
            if parts and lowered[i:i + len(parts)] == parts:
                hit = (i, len(parts))
                break
        if hit is None and _is_name(word):
            hit = (i, 1)
        if hit is None and i + 1 < len(lowered) and lowered[i + 1] in _SPLIT_NAMES.get(word, ()):
            hit = (i, 2)
        if hit:
            break
    if hit is None:
        return False, ""
    first, span = hit
    cut_from = first - 1 if first >= 1 and lowered[first - 1] in _GREETINGS else first
    before = text[:words[cut_from].start()]
    after = text[words[first + span - 1].end():]
    after = after.removeprefix("'s")
    before, after = before.strip(" .,!?;:-"), after.strip(" .,!?;:-")
    command = f"{before} {after}" if before and after else before or after
    return True, re.sub(r"\s+", " ", command).strip(" .,!?;:-")


def loose_wake(text: str) -> tuple[bool, str]:
    """A forgiving match, used only when the wake model itself half-recognised "hey Jarvis" in the same audio.

    Idle speech scores about 0.00-0.02 on that model and an accented "hey Jarvis" 0.14-0.45, so the score is
    most of the evidence; the words only have to be compatible: a short phrase, or a j/ch/g/y/z word near the start.
    """
    words = list(_WORD_RE.finditer(text))
    lowered = [w.group(0).lower() for w in words]
    if not lowered:
        return False, ""
    for i, word in enumerate(lowered[:3]):
        if len(word) >= 4 and word not in _NOT_NAMES and (word[0] in "jgyz" or word.startswith(("ch", "dj"))):
            return True, re.sub(r"\s+", " ", text[words[i].end():]).strip(" .,!?;:-")
    return (True, "") if len(lowered) <= 3 else (False, "")


def match_wake(text: str, aliases: tuple[str, ...], hint: float) -> tuple[bool, str]:
    """The name by its words; failing that, by the wake model's score plus roughly compatible words."""
    woke, command = find_wake(text, aliases)
    if not woke and hint >= BACKED_SCORE:
        return loose_wake(text)
    return woke, command


def transcribe(stt: Any, pcm: Any) -> str:
    """int16 mono 16 kHz → text. The level is normalised first because laptop array mics are very quiet."""
    import numpy as np

    audio = pcm.astype(np.float32)
    audio = audio / max(1.0, float(np.abs(audio).max())) * 0.6
    # No prompt or hotwords: measured on base.en, either one makes the model treat a leading "Jarvis" as
    # already said and drop it ("Jarvis, good morning" -> "Good morning"), which defeats the wake word.
    beam = 5 if len(audio) < SAMPLE_RATE * 3 else 2  # short clips are cheap: search harder for the right words
    segments, _info = stt.transcribe(audio, language="en", beam_size=beam, vad_filter=False,
                                     condition_on_previous_text=False, without_timestamps=True)
    return " ".join(s.text.strip() for s in segments).strip()


class SpeechSegmenter:
    """Cuts a stream of 80 ms frames into utterances using energy against an adapting noise floor."""

    START_FRAMES = 2      # consecutive loud frames that begin an utterance
    END_FRAMES = 8        # 640 ms of quiet ends it
    MAX_FRAMES = 75       # 6 s cap
    MIN_SPEECH_FRAMES = 3
    PRE_ROLL = 4          # frames kept from before the start so the first consonant is not clipped

    def __init__(self) -> None:
        self.noise = 12.0
        self._pre: deque[Any] = deque(maxlen=self.PRE_ROLL)
        self._frames: list[Any] = []
        self._loud = 0
        self._quiet = 0
        self._speech = 0
        self.active = False

    def threshold(self) -> float:
        # Low enough for a soft voice on a quiet laptop microphone; the noise floor adapts to the room.
        return max(self.noise * 2.2, 22.0)

    def frames_so_far(self) -> int:
        return len(self._frames) if self.active else 0

    def partial(self) -> Any | None:
        """The utterance in progress, for an early look before the speaker has finished."""
        import numpy as np

        return np.concatenate(self._frames) if self.active and self._frames else None

    def feed(self, samples: Any, rms: float) -> Any | None:
        """Returns a finished utterance (int16 array) or None."""
        import numpy as np

        loud = rms > self.threshold()
        if not self.active:
            if loud:
                self._loud += 1
            else:
                self._loud = 0
                self.noise += (rms - self.noise) * 0.05  # track the room only while nobody is speaking
            self._pre.append(samples.copy())
            if self._loud >= self.START_FRAMES:
                self.active = True
                self._frames = list(self._pre)
                self._speech, self._quiet = self._loud, 0
                self._pre.clear()
                self._loud = 0
            return None
        self._frames.append(samples.copy())
        if loud:
            self._speech += 1
            self._quiet = 0
        else:
            self._quiet += 1
        if self._quiet >= self.END_FRAMES or len(self._frames) >= self.MAX_FRAMES:
            frames, speech = self._frames, self._speech
            self.active, self._frames, self._speech, self._quiet = False, [], 0, 0
            if speech >= self.MIN_SPEECH_FRAMES:
                return np.concatenate(frames)
        return None


class WakeListener:
    """Owns the microphone thread and the transcriber thread. `apply` starts, stops or retunes them."""

    def __init__(self, on_wake: WakeHandler, on_status: Callable[[], None] | None = None) -> None:
        self._on_wake = on_wake
        self._on_status = on_status
        self._thread: threading.Thread | None = None
        self._stt_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._utterances: queue.Queue[Any] = queue.Queue(maxsize=5)
        self._last_fire = 0.0
        self._fire_lock = threading.Lock()
        self.threshold = 0.5
        self.state = "off"  # off | starting | listening | error
        self.error = ""
        self.device = ""
        self.speech = "off"  # off | loading | ready | unavailable — the speech path that hears plain "Jarvis"
        self.last_score = 0.0
        self.peak_score = 0.0  # best fast-path match in the last few seconds (shown in Settings for tuning)
        self.level = 0.0  # microphone input level, 0..1
        self.silent = False  # the microphone has delivered digital silence for a while: muted or blocked
        self.wakes = 0
        self.utterances = 0  # how many utterances the speech path has examined this session
        self.aliases: tuple[str, ...] = ()  # extra spellings of the name learned from the user's voice
        self.model_name = STT_MODEL  # the speech model that suits this user's voice (chosen by voice training)
        self.corrections: dict[str, str] = {}  # words this voice is misheard as → the intended word
        self._training: Callable[[Any, str], None] | None = None  # set while a training run is reading
        self._calibration: dict[str, Any] | None = None
        self._early_at = 0.0  # when the current utterance's early wake fired (0 = none pending)
        self.capture_enabled = True  # after the name, keep listening for the whole request (like a phone assistant)
        self.listen_window = LISTEN_WINDOW_S
        self.listen_pause = LISTEN_PAUSE_S
        self._capture_until = 0.0  # when the listening window closes; 0 = not listening for a request
        self._capture_parts: list[str] = []  # everything said since the name, one entry per utterance
        self._capture_closing = False  # a "close" marker is already queued behind the pending utterances
        self.on_level: Callable[[float], None] | None = None  # microphone level while listening for a request
        self._heard_in_window = False  # part of a request has been gathered in the current listening window
        self.near_misses: deque[str] = deque(maxlen=6)  # phrases the wake model half-recognised (memory only)
        self.request_stt: Callable[[Any], str] | None = None  # a cloud engine for the request (never for the name)
        self._recent: tuple[float, str] = (0.0, "")  # the last thing said before the name (memory only, a few seconds)
        self.voiceprint = Voiceprint()  # the owner's own way of saying the name, matched by sound (see voiceprint.py)

    def live(self) -> dict[str, float] | None:
        """Mic level and recent best match while listening; None otherwise."""
        if self.state != "listening":
            return None
        return {"level": round(self.level, 3), "score": round(self.peak_score, 3)}

    def status(self) -> dict[str, Any]:
        return {"state": self.state, "error": self.error, "device": self.device, "wakes": self.wakes,
                "silent": self.silent and self.state == "listening", "speech": self.speech,
                "utterances": self.utterances, "near_misses": list(self.near_misses),
                "voiceprint": len(self.voiceprint.templates)}

    def apply(self, enabled: bool, sensitivity: float, aliases: tuple[str, ...] | list[str] = (),
              model: str = STT_MODEL, corrections: dict[str, str] | None = None) -> None:
        self.aliases = tuple(a for a in (normalise_phrase(x) for x in aliases) if a)
        self.corrections = dict(corrections or {})
        if model != self.model_name:
            self.model_name = model
            if self._thread is not None and self._thread.is_alive():
                self.stop()  # restart below with the new speech model
        # Sensitivity 0..1 from Settings maps to the fast path's score threshold: higher sensitivity, lower bar.
        self.threshold = min(0.9, max(0.15, 1.0 - float(sensitivity)))
        running = self._thread is not None and self._thread.is_alive()
        if enabled and not running:
            self._stop = threading.Event()
            self._utterances = queue.Queue(maxsize=5)
            self._set("starting")
            self._thread = threading.Thread(target=self._run, args=(self._stop,), name="jarvis-wake", daemon=True)
            self._stt_thread = threading.Thread(target=self._run_stt, args=(self._stop, self._utterances),
                                                name="jarvis-wake-stt", daemon=True)
            self._thread.start()
            self._stt_thread.start()
        elif not enabled and running:
            self.stop()

    def stop(self) -> None:
        self._capture_until = 0.0
        self._capture_parts = []
        self._capture_closing = False
        self._stop.set()
        threads = [self._thread, self._stt_thread]
        self._thread = self._stt_thread = None
        for thread in threads:
            if thread and thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=4)
        self.speech = "off"
        self._set("off")

    def set_training(self, handler: Callable[[Any, str], None] | None) -> bool:
        """Route utterances to a voice-training run instead of wake detection (None restores normal listening)."""
        if handler is not None and (self.state != "listening" or self.speech != "ready"):
            return False
        self._training = handler
        return True

    def calibrate(self, on_done: Callable[[list[str]], None], count: int = 5, timeout: float = 30.0) -> bool:
        """Collect what the recognizer hears for the next few phrases, at the user's explicit request.

        While calibrating, phrases do not wake Jarvis; their transcripts are handed to `on_done` so the user can
        see how their "Jarvis" is being spelled and accept those spellings as aliases.
        """
        if self.state != "listening" or self.speech != "ready" or self._calibration is not None:
            return False
        self._calibration = {"heard": [], "clips": [], "left": count, "until": time.monotonic() + timeout,
                             "done": on_done}
        return True

    def _learn_voice(self, clips: list[Any]) -> None:
        """Turn the recordings from "Teach Jarvis your voice" into sound templates (kept as vectors, not audio)."""
        try:
            learned = Voiceprint.enrol(clips, keep=self.voiceprint.templates)
            if len(learned.templates) >= 2:
                learned.save()
                self.voiceprint = learned
                log.info("voiceprint: %d templates, threshold %.3f", len(learned.templates), learned.threshold)
        except Exception:
            log.exception("voiceprint enrolment failed")

    def forget_voice(self) -> None:
        Voiceprint.clear()
        self.voiceprint = Voiceprint()
        self._notify()

    def _by_sound(self, stt: Any, pcm: Any) -> tuple[bool, str]:
        """The owner's name by sound. Returns (matched, the rest of what was said)."""
        found = self.voiceprint.match(pcm) if self.voiceprint else None
        if DEBUG_HEARD and self.voiceprint:
            log.info("debug voiceprint: distance %.3f (limit %.3f) -> %s", self.voiceprint.distance(pcm),
                     self.voiceprint.threshold, found is not None)
        if found is None:
            return False, ""
        _distance, start_s, end_s = found
        parts = []
        for piece in (pcm[:int(start_s * SAMPLE_RATE)], pcm[int(end_s * SAMPLE_RATE):]):
            piece = trim(piece)
            if len(piece) >= 0.45 * SAMPLE_RATE:  # more than a stray syllable: words said around the name
                parts.append(apply_corrections(transcribe(stt, piece), self.corrections).strip(" .,!?;:-"))
        return True, " ".join(p for p in parts if p)

    def _calibration_step(self, text: str | None, pcm: Any = None) -> bool:
        """Feed one transcript (or None for a timer tick). Returns True while calibration is consuming phrases."""
        cal = self._calibration
        if cal is None:
            return False
        if text:
            cal["heard"].append(text)
            if pcm is not None:
                cal["clips"].append(pcm)
            cal["left"] -= 1
        if cal["left"] <= 0 or time.monotonic() >= cal["until"]:
            self._calibration = None
            self._learn_voice(cal["clips"])
            try:
                cal["done"](cal["heard"])
            except Exception:
                log.exception("calibration callback failed")
        return True

    def drain(self, timeout: float = 30.0) -> None:
        """Wait until queued utterances have been transcribed (tests and orderly shutdown)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and (self._utterances.unfinished_tasks or self.speech == "loading"):
            time.sleep(0.05)

    def _notify(self) -> None:
        if self._on_status:
            try:
                self._on_status()
            except Exception:
                log.debug("wake status callback failed", exc_info=True)

    def _set(self, state: str, error: str = "") -> None:
        changed = (state, error) != (self.state, self.error)
        self.state, self.error = state, error
        if changed:
            self._notify()

    def _deliver(self, score: float, text: str, stage: str) -> None:
        try:
            self._on_wake(score, text, stage)
        except Exception:
            log.exception("wake handler failed")

    @property
    def capturing(self) -> bool:
        return self._capture_until > 0

    def _fire_early(self, score: float, source: str) -> bool:
        """The name was just heard. Wake at once and, in listening mode, open the window for the request."""
        with self._fire_lock:
            now = time.monotonic()
            if now - self._last_fire <= COOLDOWN_S or self._capture_until > 0:
                return False  # another path already woke Jarvis for this word, or it is already listening
            self._last_fire = self._early_at = now
            self.wakes += 1
            if self.capture_enabled:
                self._open_capture(now)
        log.info("wake word heard via %s (confidence %.2f)", source, score)
        self._deliver(score, "", "early")
        return True

    def _open_capture(self, now: float) -> None:
        """Start a listening window (caller holds the lock)."""
        self._capture_until = now + self.listen_window
        self._capture_parts = []
        self._capture_closing = False
        self._heard_in_window = False

    def _fire_final(self, score: float, command: str, heard_name: bool, text: str = "") -> None:
        """An utterance ended. `command` is what followed the name in it; `text` is everything that was said.

        In listening mode nothing is acted on here: each utterance is added to the request being gathered, and
        the request is delivered when the listening window closes (see `_finish_capture`).
        """
        deliver: list[tuple[str, str]] = []
        with self._fire_lock:
            now = time.monotonic()
            pending = self._early_at > 0 and now - self._early_at <= FINAL_WINDOW_S
            self._early_at = 0.0
            listening = self._capture_until > 0
            if not listening:
                # Already acknowledged for this utterance (pending): never a second wake, however slow the transcription was.
                fresh = heard_name and not pending and now - self._last_fire > COOLDOWN_S
                if not pending and not fresh:
                    # Ordinary speech. Remember it for a few seconds only, in case the name follows it.
                    self._recent = (now, text.strip()) if len(text.split()) >= 2 else (0.0, "")
                    return
                if fresh:
                    self._last_fire = now
                    self.wakes += 1
                    deliver.append(("early", ""))
                    if self.capture_enabled:
                        self._open_capture(now)
                        listening = True
                if not listening:
                    deliver.append(("final", command))  # listening mode off: complete the wake right here
            if listening:
                said_at, said = self._recent
                self._recent = (0.0, "")
                if heard_name and not command and not self._capture_parts and said and now - said_at <= LOOKBACK_S:
                    piece = said  # the request came first and the name after a pause: act on that sentence
                elif heard_name:
                    piece = command  # "Jarvis, open Chrome": keep what followed the name
                elif pending and len(text.split()) <= 2:
                    piece = ""  # the wake model heard the name but the words came out as something else: not a request
                else:
                    piece = text
                piece = " ".join(piece.split()).strip(" ,;:-")
                if piece:
                    self._capture_parts.append(piece)
                    self._heard_in_window = True  # part of a request has been gathered: the pause rule now applies
                    deliver.append(("partial", " ".join(self._capture_parts)))
        for stage, payload in deliver:
            if stage == "early":
                log.info("wake word heard via speech at end of utterance (confidence %.2f)", score)
            self._deliver(score, payload, stage)

    def _finish_capture(self) -> None:
        """The listening window is over: act on everything that was said, or close quietly if nothing was."""
        with self._fire_lock:
            if self._capture_until == 0:
                return
            request = " ".join(self._capture_parts).strip(" .,!?;:-")
            self._capture_until = 0.0
            self._capture_parts = []
            self._capture_closing = False
        self._deliver(1.0 if request else 0.0, request, "final" if request else "ended")

    _close_capture = _finish_capture  # kept for callers that close a window that gathered nothing

    # -- speech path ---------------------------------------------------------------------------------

    def _run_stt(self, stop: threading.Event, utterances: queue.Queue[Any]) -> None:
        self.speech = "loading"
        try:
            stt = load_stt(self.model_name)
        except Exception as exc:  # noqa: BLE001 - the fast path keeps working without the speech model
            log.warning("speech path unavailable: %s", exc)
            self.speech = "unavailable"
            self._notify()
            while not stop.is_set():  # keep the queue from blocking the microphone thread
                try:
                    utterances.get(timeout=0.5)
                    utterances.task_done()
                except queue.Empty:
                    pass
            return
        self.voiceprint = Voiceprint.load()
        self.speech = "ready"
        self._notify()
        while not stop.is_set():
            try:
                kind, pcm, hint = utterances.get(timeout=0.3)
            except queue.Empty:
                self._calibration_step(None)  # lets a calibration time out when nobody speaks
                continue
            try:
                training = self._training
                if kind == "early":
                    # A quick look at the start of an utterance (or at the last moments of audio after a faint
                    # wake-model match). Skipped while training, calibrating, listening, or right after a wake.
                    if (training is None and self._calibration is None and not self.capturing
                            and time.monotonic() - self._last_fire > COOLDOWN_S):
                        text = apply_corrections(transcribe(stt, pcm), self.corrections)
                        hit = match_wake(text, self.aliases, hint)[0]
                        if DEBUG_HEARD:
                            log.info("debug early look: score %.2f heard %r -> %s", hint, text[:80], hit)
                        if hit:
                            self._fire_early(max(hint, 0.9), "speech")
                    continue
                if kind == "close":
                    # Queued by the microphone thread when the window ended; arriving here means every utterance
                    # spoken before it has already been transcribed and gathered.
                    self._finish_capture()
                    continue
                if kind == "partial":
                    # Words so far, for the live display. Only while Jarvis is listening for a request.
                    if self.capturing and utterances.qsize() == 0:
                        text = apply_corrections(transcribe(stt, pcm), self.corrections)
                        woke, rest = find_wake(text, self.aliases)
                        so_far = " ".join([*self._capture_parts, (rest if woke else text).strip()]).strip()
                        self._deliver(0.0, so_far, "partial")
                    continue
                text = ""
                if self.capturing and self.request_stt is not None and training is None and self._calibration is None:
                    try:
                        text = self.request_stt(pcm)  # the user has addressed Jarvis: use the engine they chose
                    except Exception as exc:  # noqa: BLE001 - offline, bad key, rate limit: fall back to local
                        log.warning("cloud speech-to-text failed, using the local model: %s", str(exc)[:160])
                if not text:
                    text = transcribe(stt, pcm)
                self.utterances += 1
                if training is not None:
                    training(pcm, text)  # a training run is reading: nothing wakes, nothing is corrected
                    continue
                if self._calibration_step(text, pcm):
                    continue
                text = apply_corrections(text, self.corrections)
                woke, command = match_wake(text, self.aliases, 0.0 if self.capturing else hint)
                if not woke and not self.capturing and time.monotonic() - self._last_fire > COOLDOWN_S:
                    woke, command = self._by_sound(stt, pcm)
                if DEBUG_HEARD and (woke or len(text.split()) <= 6):
                    log.info("debug utterance: score %.2f heard %r -> %s", hint, text[:80], woke)
                short = 0 < len(text.split()) <= 2 and len(text.strip(" .,!?")) >= 4
                if not woke and not self.capturing and text.strip() and (hint >= VERIFY_SCORE or short):
                    # The wake model thought it might be the name, or one or two words were said on their own,
                    # but the words did not match: keep the phrase (in memory only) so Settings can offer it as
                    # "is this how you say Jarvis?".
                    phrase = " ".join(text.split())[:60]
                    if phrase not in self.near_misses:
                        self.near_misses.append(phrase)
                        self._notify()
                self._fire_final(1.0 if woke else hint, command, woke, text)
                # A transcript that is neither addressed to Jarvis nor a request it is waiting for is dropped
                # here: it is not stored on disk, logged or sent anywhere.
            except Exception:
                log.exception("transcription failed")
            finally:
                utterances.task_done()

    # -- microphone ------------------------------------------------------------------------------------

    def _queue_close(self) -> None:
        """Ask the transcriber to end the listening window once it has caught up. Must not be dropped."""
        try:
            self._utterances.put(("close", None, 0.0), timeout=2.0)
        except queue.Full:
            self._finish_capture()  # the transcriber is stuck: close directly rather than listen forever

    def _queue(self, kind: str, pcm: Any, hint: float) -> None:
        try:
            self._utterances.put_nowait((kind, pcm, hint))
        except queue.Full:
            pass  # the transcriber is behind (continuous speech nearby): skip, never block the microphone

    def _run(self, stop: threading.Event) -> None:
        try:
            import numpy as np
            import sounddevice as sd

            model = load_model()
            try:
                self.device = str(sd.query_devices(kind="input")["name"])[:80]
            except Exception as exc:
                raise RuntimeError("No microphone was found.") from exc
            segmenter = SpeechSegmenter()
            ring: deque[Any] = deque(maxlen=RING_FRAMES)
            peak_at = 0.0
            loud_at = time.monotonic()
            verify_at = 0.0  # last time a faint match was sent for checking
            utt_peak = 0.0  # best wake-model score during the utterance in progress
            peeked = False  # whether the utterance in progress has had its early look
            partial_at = 0  # frame count at the last live-transcript refresh
            quiet_since = 0.0  # when the user last stopped speaking inside a listening window
            stats_at, win_peak, win_score, win_utts = time.monotonic(), 0, 0.0, self.utterances
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME) as stream:
                self._set("listening")
                log.info("wake word listening on %s", self.device)
                while not stop.is_set():
                    frame, _overflowed = stream.read(FRAME)
                    samples = np.squeeze(frame)
                    ring.append(samples.copy())
                    score = float(max(model.predict(samples).values()))
                    self.last_score = score
                    now = time.monotonic()
                    rms_raw = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
                    self.level = max(min(1.0, rms_raw / 32768.0 * 12), self.level * 0.85)  # fast attack, slow release
                    raw_peak = int(np.abs(samples).max())
                    win_peak, win_score = max(win_peak, raw_peak), max(win_score, score)
                    if now - stats_at >= STATS_EVERY_S:
                        # Numbers only — never audio or words. Lets a missed wake be diagnosed from the log.
                        log.info("wake stats: loudest %d/32767, noise floor %.0f, best match %.2f, "
                                 "utterances checked %d, wakes %d", win_peak, segmenter.noise, win_score,
                                 self.utterances - win_utts, self.wakes)
                        stats_at, win_peak, win_score, win_utts = now, 0, 0.0, self.utterances
                    if raw_peak > SILENCE_PEAK:
                        loud_at = now
                    silent = now - loud_at > SILENCE_AFTER_S
                    if silent != self.silent:
                        self.silent = silent
                        self._notify()
                    if score >= self.peak_score or now - peak_at > 3.0:
                        self.peak_score, peak_at = score, now
                    busy = self._training is not None or self._calibration is not None
                    capturing = self.capturing
                    if capturing:
                        if self.on_level:
                            # A snappier meter than the status level: the live display follows the voice.
                            self.on_level(min(1.0, rms_raw / 32768.0 * 14))
                        if segmenter.active:
                            quiet_since = 0.0
                        elif quiet_since == 0.0:
                            quiet_since = now
                        window_over = now >= self._capture_until
                        # Mid-sentence when the window ends: let the speaker finish, within reason.
                        overrun = segmenter.active and now < self._capture_until + LISTEN_OVERRUN_S
                        paused = (self.listen_pause > 0 and not segmenter.active and self._heard_in_window
                                  and now - quiet_since >= self.listen_pause)
                        if not self._capture_closing and ((window_over and not overrun) or paused):
                            if segmenter.active:  # past the overrun allowance: take what has been said so far
                                cut = segmenter.partial()
                                if cut is not None:
                                    self._queue("final", cut, 0.0)
                            self._capture_closing = True
                            self._queue_close()
                    else:
                        quiet_since = 0.0
                    if score >= self.threshold and not busy and not capturing:
                        model.reset()  # clear the model's audio buffer so one utterance fires once
                        self._fire_early(score, "wake model")
                    elif (score >= VERIFY_SCORE and not busy and not capturing and now - verify_at > 1.2
                          and now - self._last_fire > COOLDOWN_S):
                        # Not confident enough to wake on its own (accents score low on this model), but worth an
                        # immediate check of the last couple of seconds rather than waiting for the speaker to stop.
                        verify_at = now
                        self._queue("early", np.concatenate(list(ring)), score)
                    # How long a pause ends an utterance: longest when reading for training, longer than usual
                    # when taking a request (people pause mid-sentence), shortest when just listening for the name.
                    if self._training is not None:
                        segmenter.END_FRAMES, segmenter.MAX_FRAMES = 16, 190
                    elif self.capturing:
                        segmenter.END_FRAMES, segmenter.MAX_FRAMES = 8, 120  # utterances are gathered, so cut normally
                    else:
                        segmenter.END_FRAMES, segmenter.MAX_FRAMES = 7, 75
                    utterance = segmenter.feed(samples, rms_raw)
                    if segmenter.active:
                        utt_peak = max(utt_peak, score)
                        frames = segmenter.frames_so_far()
                        if not peeked and not busy and not self.capturing and frames >= EARLY_FRAMES:
                            peeked = True
                            partial = segmenter.partial()
                            if partial is not None:
                                self._queue("early", partial, utt_peak)
                        if self.capturing and frames - partial_at >= PARTIAL_EVERY_FRAMES:
                            partial_at = frames
                            partial = segmenter.partial()
                            if partial is not None:
                                self._queue("partial", partial, 0.0)
                    if utterance is not None:
                        self._queue("final", utterance, max(utt_peak, score))
                        utt_peak, peeked, partial_at = 0.0, False, 0
        except Exception as exc:  # noqa: BLE001 - audio and model errors are surfaced as a status
            if not stop.is_set():
                log.warning("wake word stopped: %s", exc)
                text = str(exc) or type(exc).__name__
                if isinstance(exc, MemoryError):
                    text = "Windows is out of memory, so the wake word could not run. Close some apps and turn it on again."
                if "Invalid" in text or "PortAudio" in type(exc).__name__ or "device" in text.lower():
                    text = "The microphone could not be opened. Check Windows microphone privacy settings."
                self._set("error", text[:200])
            return
        if not stop.is_set():
            self._set("off")
