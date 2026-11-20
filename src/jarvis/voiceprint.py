"""The owner's own "Jarvis", matched by sound instead of spelling (FR-V7).

Speech-to-text writes some voices' "hey Jarvis" as other words entirely ("Here's our list"), and no amount of
spelling tolerance recovers that. This module skips the words: the user says the name a few times, each
recording is turned into a short sequence of speech-embedding vectors (the same embedding model the wake
library already ships), and later utterances are compared to those templates with dynamic time warping.
Loudness is normalised first, so a soft voice matches the same templates as a loud one.

Only the embedding vectors are stored (data/wake/voiceprint.npz) — never audio. They stay on this computer.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from .config import DATA_DIR

log = logging.getLogger(__name__)

PRINT_PATH = DATA_DIR / "wake" / "voiceprint.npz"
SAMPLE_RATE = 16000
HOP = 1280  # one embedding every 80 ms…
WINDOW = 12400  # …each covering about 775 ms of audio
MIN_CLIP_S, MAX_CLIP_S = 0.25, 2.5  # an enrolment recording must be one short phrase
MAX_TEMPLATES = 12
# Distances measured on synthesized voices at four speaking rates, full level and -34 dB: the same phrase again
# 0.04-0.09; rhymes ("service", "harvest", "hey Travis") 0.04-0.08, so those can wake it too; ordinary
# phrases and long sentences 0.13-0.18. The limits below sit in the gap.
THRESHOLD_FLOOR, THRESHOLD_CEIL, THRESHOLD_DEFAULT = 0.075, 0.105, 0.095
LONG_UTTERANCE_S, LONG_FACTOR = 2.5, 0.85  # inside a long sentence there are more chances to match: be stricter

_features: Any = None
_features_lock = threading.Lock()


def trim(pcm: Any) -> Any:
    """Cut leading and trailing quiet so a template is just the phrase."""
    import numpy as np

    level = np.abs(pcm.astype(np.float32))
    if not len(level) or level.max() < 1:
        return pcm[:0]
    # Smooth over 20 ms so a single click does not count as speech.
    kernel = np.ones(320, np.float32) / 320
    smooth = np.convolve(level, kernel, mode="same")
    loud = np.flatnonzero(smooth > 0.12 * smooth.max())
    if not len(loud):
        return pcm[:0]
    return pcm[max(0, loud[0] - 800): loud[-1] + 800]


def embed(pcm: Any, pad_s: float = 0.3) -> Any:
    """int16 mono 16 kHz → unit-length embedding vectors, one per 80 ms. Level-normalised."""
    import numpy as np

    audio = pcm.astype(np.float32)
    audio = audio / max(1.0, float(np.abs(audio).max())) * 16000.0
    pad = np.zeros(int(SAMPLE_RATE * pad_s), np.float32)
    audio = np.concatenate([pad, audio, pad])
    if len(audio) < WINDOW + 4 * HOP:
        audio = np.concatenate([audio, np.zeros(WINDOW + 4 * HOP - len(audio), np.float32)])
    with _features_lock:
        vectors = _extractor_unlocked()._get_embeddings(audio.astype(np.int16))
    vectors = np.asarray(vectors, np.float32)
    return vectors / (np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-9)


def _extractor_unlocked() -> Any:
    global _features
    if _features is None:
        from openwakeword.utils import AudioFeatures

        from .wake import ensure_models

        _word, melspec, embedding = ensure_models()
        _features = AudioFeatures(melspec_model_path=str(melspec), embedding_model_path=str(embedding),
                                  inference_framework="onnx")
    return _features


def spot(template: Any, utterance: Any) -> tuple[float, int, int]:
    """Best match of the whole template against any stretch of the utterance (subsequence DTW).

    Returns (distance, first, last): the average cosine distance along the best path, and the range of
    utterance vectors it covers. 0 is identical; unrelated speech is typically above 0.12.
    """
    import numpy as np

    cost = 1.0 - template @ utterance.T
    n, m = cost.shape
    acc = np.full((n + 1, m + 1), np.inf, np.float32)
    acc[0, :] = 0.0  # the match may begin anywhere in the utterance
    start = np.zeros((n + 1, m + 1), np.int32)
    start[0, :] = np.arange(m + 1)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            options = (acc[i - 1, j - 1], acc[i - 1, j], acc[i, j - 1])
            k = int(np.argmin(options))
            acc[i, j] = cost[i - 1, j - 1] + options[k]
            start[i, j] = (start[i - 1, j - 1], start[i - 1, j], start[i, j - 1])[k]
    end = int(np.argmin(acc[n, 1:])) + 1
    first = int(start[n, end])
    return float(acc[n, end]) / n, first, end - 1


class Voiceprint:
    """Templates of the owner saying the name, and the distance below which an utterance counts as the name."""

    def __init__(self, templates: list[Any] | None = None, threshold: float = THRESHOLD_DEFAULT) -> None:
        self.templates: list[Any] = templates or []
        self.threshold = threshold

    def __bool__(self) -> bool:
        return bool(self.templates)

    @classmethod
    def load(cls, path: Path = PRINT_PATH) -> Voiceprint:
        import numpy as np

        try:
            with np.load(path) as data:
                templates = [data[k] for k in sorted(data.files) if k.startswith("tpl")]
                return cls(templates, float(data["threshold"]))
        except FileNotFoundError:
            return cls()
        except Exception:
            log.warning("voiceprint file could not be read; enrol again", exc_info=True)
            return cls()

    def save(self, path: Path = PRINT_PATH) -> None:
        import numpy as np

        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(path, threshold=np.float32(self.threshold),
                 **{f"tpl{i:02d}": t for i, t in enumerate(self.templates)})

    @staticmethod
    def clear(path: Path = PRINT_PATH) -> None:
        path.unlink(missing_ok=True)

    @classmethod
    def enrol(cls, clips: list[Any], keep: list[Any] | None = None) -> Voiceprint:
        """Build a voiceprint from recordings of the name. `keep` are templates from earlier enrolments."""
        import numpy as np

        templates = list(keep or [])
        for clip in clips:
            phrase = trim(clip)
            if MIN_CLIP_S * SAMPLE_RATE <= len(phrase) <= MAX_CLIP_S * SAMPLE_RATE:
                templates.append(embed(phrase, pad_s=0.15))
        templates = templates[-MAX_TEMPLATES:]
        threshold = THRESHOLD_DEFAULT
        if len(templates) >= 2:
            # How far apart the owner's own repetitions are: each template to its nearest other one.
            nearest = [min(max(spot(a, b)[0], spot(b, a)[0]) for j, b in enumerate(templates) if j != i)
                       for i, a in enumerate(templates)]
            threshold = float(np.clip(float(np.median(nearest)) * 1.6, THRESHOLD_FLOOR, THRESHOLD_CEIL))
        return cls(templates, threshold)

    def match(self, pcm: Any) -> tuple[float, float, float] | None:
        """Is the name somewhere in this utterance? Returns (distance, start_s, end_s) of the best match or None."""
        if not self.templates or len(pcm) < MIN_CLIP_S * SAMPLE_RATE:
            return None
        pad_s = 0.3
        vectors = embed(pcm, pad_s=pad_s)
        best: tuple[float, int, int] | None = None
        for template in self.templates:
            found = spot(template, vectors)
            if best is None or found[0] < best[0]:
                best = found
        limit = self.threshold * (LONG_FACTOR if len(pcm) > LONG_UTTERANCE_S * SAMPLE_RATE else 1.0)
        if best is None or best[0] > limit:
            return None
        distance, first, last = best
        start_s = max(0.0, first * HOP / SAMPLE_RATE - pad_s)
        end_s = max(start_s, (last * HOP + WINDOW) / SAMPLE_RATE - pad_s)
        return distance, start_s, end_s

    def distance(self, pcm: Any) -> float:
        """Best distance regardless of the threshold (diagnostics and tests)."""
        if not self.templates or len(pcm) < MIN_CLIP_S * SAMPLE_RATE:
            return 9.0
        vectors = embed(pcm)
        return min(spot(t, vectors)[0] for t in self.templates)
