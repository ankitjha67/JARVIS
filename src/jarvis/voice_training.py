"""Voice training (FR-V7): learn one user's pronunciation from a passage read aloud.

The speech model itself is not retrained — that needs hours of audio and a GPU. Three things are adapted
instead, all from comparing what was read with what was heard:

1. Corrections — words the recognizer consistently mishears for this voice ("crow" for "Chrome") are mapped
   back to the intended word and applied to every later transcript.
2. Wake aliases — however the recognizer spells this user's "Jarvis" becomes an accepted spelling.
3. Model choice — the passage audio is replayed through a larger model, and the more accurate one is kept.

The passage is read one sentence at a time so each utterance lines up with a known sentence. Audio is held in
memory only for the length of the training run and is never written to disk.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any

# An original passage: the most frequent English words, the vocabulary of everyday computer commands, numbers,
# and "Jarvis" at the start, middle and end of sentences. Sentences are short enough to say in one breath.
PASSAGE: tuple[str, ...] = (
    "Jarvis, good morning.",
    "Hey Jarvis, what time is it right now?",
    "I would like you to open Chrome and search for the weather today.",
    "Please turn the volume up, and then play some music.",
    "Jarvis, close this window and show me my desktop.",
    "Read my new email and tell me who it is from.",
    "Can you find the file that I was working on last week?",
    "There are three people who will be here at about ten thirty.",
    "Write a short message to them and say that we can start.",
    "Okay Jarvis, set a timer for five minutes.",
    "Make the screen a little brighter, because it is too dark in this room.",
    "Thank you, that is all for now, Jarvis.",
)

_WORD_RE = re.compile(r"[a-z]+(?:'[a-z]+)?")
MODELS = ("base.en", "small.en")
MIN_MATCH_TO_ACCEPT = 0.45  # below this the user probably said something else: ask them to read it again
MAX_ATTEMPTS = 3


def words(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


PASSAGE_VOCABULARY = frozenset(w for sentence in PASSAGE for w in words(sentence))
# Short function words are never worth "correcting": a mapping onto them would rewrite ordinary speech.
_NEVER_MAP_FROM = PASSAGE_VOCABULARY | frozenset({
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at", "it", "is", "be", "was", "are", "i", "you",
    "he", "she", "we", "they", "this", "that", "for", "with", "as", "by", "not", "do", "have", "has", "had", "so",
    "if", "no", "yes", "up", "out", "my", "me", "your", "our", "their", "there", "here", "what", "who", "when",
})


# Everyday words a recognizer may substitute for "Jarvis". Accepting one would wake Jarvis in normal talk.
_NEVER_ALIAS = frozenset({
    "service", "services", "server", "servers", "travis", "nervous", "harvest", "various", "jobs", "job", "charges",
    "java", "jars", "jar", "drivers", "driver", "office", "advice", "device", "devices", "survive", "starve",
    "just", "yes", "this", "these", "those", "there", "germans", "german", "jersey", "journey", "january",
})


@dataclass
class Alignment:
    matched: int
    total: int
    substitutions: list[tuple[str, str]]  # (heard, expected), one-for-one replacements only
    merged: list[tuple[str, str]]  # (heard phrase of 2-3 words, expected single word)

    @property
    def accuracy(self) -> float:
        return self.matched / self.total if self.total else 0.0


def align(expected: str, heard: str) -> Alignment:
    """Word-level comparison of a known sentence with its transcript."""
    exp, got = words(expected), words(heard)
    matcher = difflib.SequenceMatcher(a=exp, b=got, autojunk=False)
    matched = 0
    subs: list[tuple[str, str]] = []
    merged: list[tuple[str, str]] = []
    for op, a0, a1, b0, b1 in matcher.get_opcodes():
        if op == "equal":
            matched += a1 - a0
        elif op == "replace":
            if a1 - a0 == b1 - b0:
                subs.extend((got[b0 + k], exp[a0 + k]) for k in range(a1 - a0))
            elif a1 - a0 == 1 and 2 <= b1 - b0 <= 3:
                merged.append((" ".join(got[b0:b1]), exp[a0]))  # one word heard as several ("jar vis")
    return Alignment(matched, len(exp), subs, merged)


def learn(alignments: list[Alignment]) -> tuple[list[str], dict[str, str]]:
    """Turn the mistakes of a training run into (wake aliases, corrections)."""
    aliases: list[str] = []
    votes: dict[str, dict[str, int]] = {}
    for al in alignments:
        for heard, expected in [*al.substitutions, *al.merged]:
            if expected == "jarvis":
                # A common word must never become the wake word, however this voice was transcribed.
                if (heard not in aliases and len(heard) >= 3
                        and not (set(heard.split()) & (_NEVER_MAP_FROM | _NEVER_ALIAS))):
                    aliases.append(heard)
                continue
            if " " in heard or heard in _NEVER_MAP_FROM or len(heard) < 3 or len(expected) < 3:
                continue
            if difflib.SequenceMatcher(a=heard, b=expected).ratio() < 0.4:
                continue  # unrelated words: a slip of the tongue, not a pronunciation pattern
            votes.setdefault(heard, {}).setdefault(expected, 0)
            votes[heard][expected] += 1
    corrections = {heard: max(options, key=lambda k: options[k]) for heard, options in votes.items()}
    return aliases[:12], dict(list(corrections.items())[:150])


def apply_corrections(text: str, corrections: dict[str, str]) -> str:
    """Rewrite words this user's voice is known to be misheard as. Case and punctuation around them are kept."""
    if not corrections:
        return text

    def fix(match: re.Match[str]) -> str:
        word = match.group(0)
        replacement = corrections.get(word.lower())
        if replacement is None:
            return word
        return replacement.capitalize() if word[0].isupper() else replacement

    return re.sub(r"[A-Za-z]+(?:'[A-Za-z]+)?", fix, text)


@dataclass
class Trainer:
    """State of one training run. Fed one transcribed utterance at a time."""

    index: int = 0
    attempts: int = 0
    audio: list[Any] = field(default_factory=list)  # one int16 array per accepted sentence
    heard: list[str] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(PASSAGE)

    @property
    def finished(self) -> bool:
        return self.index >= len(PASSAGE)

    def sentence(self) -> str:
        return PASSAGE[self.index] if not self.finished else ""

    def offer(self, pcm: Any, text: str) -> dict[str, Any]:
        """Judge one utterance against the current sentence. Returns what to tell the user."""
        if self.finished:
            return {"accepted": False, "note": ""}
        al = align(self.sentence(), text)
        self.attempts += 1
        if al.accuracy >= MIN_MATCH_TO_ACCEPT or self.attempts >= MAX_ATTEMPTS:
            close = al.accuracy >= MIN_MATCH_TO_ACCEPT
            if close:
                self.audio.append(pcm)
                self.heard.append(text)
            else:
                self.skipped.append(self.index)  # never learn from audio that does not match the sentence
            self.index += 1
            self.attempts = 0
            return {"accepted": True, "heard": text, "accuracy": round(al.accuracy, 2),
                    "note": "" if close else "That did not match the sentence, so it was skipped."}
        return {"accepted": False, "heard": text, "accuracy": round(al.accuracy, 2),
                "note": "That did not sound like this sentence. Please read it once more."}

    def skip(self) -> None:
        if not self.finished:
            self.skipped.append(self.index)
            self.index += 1
            self.attempts = 0

    def kept_sentences(self) -> list[str]:
        return [PASSAGE[i] for i in range(len(PASSAGE)) if i not in self.skipped][: len(self.heard)]

    def score(self, transcripts: list[str], corrections: dict[str, str] | None = None) -> tuple[float, list[Alignment]]:
        """Word accuracy of a set of transcripts against the sentences that were read."""
        alignments = [align(expected, apply_corrections(text, corrections or {}))
                      for expected, text in zip(self.kept_sentences(), transcripts, strict=False)]
        total = sum(a.total for a in alignments)
        return (sum(a.matched for a in alignments) / total if total else 0.0), alignments
