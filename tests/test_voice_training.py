"""Voice training: alignment, what is learned, what is refused, and the run state machine."""

from __future__ import annotations

from jarvis import voice_training as vt
from jarvis.voice_training import PASSAGE, Trainer, align, apply_corrections, learn


def test_passage_covers_the_name_and_command_words():
    text = " ".join(PASSAGE).lower()
    assert text.count("jarvis") >= 5
    starts = sum(1 for s in PASSAGE if s.lower().startswith(("jarvis", "hey jarvis", "okay jarvis")))
    assert starts >= 3 and any(s.lower().rstrip(".").endswith("jarvis") for s in PASSAGE)   # start and end positions
    for word in ("open", "close", "search", "volume", "email", "file", "window", "timer", "chrome"):
        assert word in text
    assert all(len(vt.words(s)) <= 15 for s in PASSAGE)                                     # sayable in one breath


def test_align_counts_matches_and_pairs_substitutions():
    al = align("Jarvis, open Chrome and search for the weather today.", "jarvish open crow and search for the weather today")
    assert al.total == 9 and al.matched == 7
    assert ("jarvish", "jarvis") in al.substitutions and ("crow", "chrome") in al.substitutions
    split = align("Jarvis, good morning.", "jar vis good morning")
    assert ("jar vis", "jarvis") in split.merged
    assert align("Read my new email", "").accuracy == 0.0


def test_learn_keeps_pronunciation_patterns_and_refuses_dangerous_ones():
    alignments = [
        align("Jarvis, open Chrome", "jarvish open crow"),
        align("Hey Jarvis, what time is it", "hey service what time is it"),     # a common word: never an alias
        align("Jarvis, good morning", "the good morning"),                       # function word: never an alias
        align("turn the volume up", "turn the wallium up"),
        align("read my new email", "read my new elephant"),                      # unrelated word: a slip, not a pattern
        align("set a timer for five minutes", "set a time for five minutes"),    # "time" is itself a passage word
    ]
    aliases, corrections = learn(alignments)
    assert aliases == ["jarvish"]
    assert "the" not in aliases
    assert corrections.get("crow") == "chrome" and corrections.get("wallium") == "volume"
    assert "elephant" not in corrections and "time" not in corrections


def test_everyday_words_are_never_learned_as_the_wake_word():
    for word in ("service", "travis", "office", "just", "java"):
        aliases, _ = learn([align("Hey Jarvis, what time is it", f"hey {word} what time is it")])
        assert aliases == [], word


def test_scoring_a_real_reading_end_to_end(monkeypatch, tmp_path):
    """Real speech model on synthesized readings of three sentences, through the hub's own scoring."""
    import subprocess
    import wave

    import pytest

    np = pytest.importorskip("numpy")
    pytest.importorskip("faster_whisper")
    from jarvis import wake
    from jarvis.gateway import Hub
    from jarvis.store import Store

    if not any(wake.STT_DIR.glob("**/model.bin")):
        pytest.skip("speech model not downloaded")
    stt = wake.load_stt("base.en")
    trainer = Trainer()
    for i in range(3):
        wav = tmp_path / f"s{i}.wav"
        text = PASSAGE[i].replace("'", "")
        script = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, 'Sixteen', 'Mono'); "
                  f"$s.SetOutputToWaveFile('{wav.as_posix()}', $f); $s.Speak('{text}'); $s.SetOutputToNull(); $s.Dispose()")
        done = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], capture_output=True, timeout=60, check=False)
        if done.returncode != 0 or not wav.exists():
            pytest.skip("Windows speech synthesis unavailable")
        with wave.open(str(wav)) as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        assert trainer.offer(pcm, wake.transcribe(stt, pcm))["accepted"], PASSAGE[i]
    monkeypatch.setattr(vt, "MODELS", ("base.en",))          # do not download the larger model in a test
    hub = Hub("t" * 32, store=Store(":memory:"))
    try:
        result = hub._training_finish(trainer)
    finally:
        hub.store.close()
    assert result["sentences"] == 3 and result["model"] == "base.en"
    assert result["before"] >= 0.85 and result["after"] >= result["before"]
    assert result["name_recognised"] == result["name_sentences"] == 2      # sentences 1 and 2 contain the name


def test_apply_corrections_respects_case_and_punctuation():
    fixes = {"crow": "chrome", "wallium": "volume"}
    assert apply_corrections("Open Crow, then turn the wallium up.", fixes) == "Open Chrome, then turn the volume up."
    assert apply_corrections("Nothing to change here", fixes) == "Nothing to change here"
    assert apply_corrections("crowd", fixes) == "crowd"          # whole words only
    assert apply_corrections("anything", {}) == "anything"


def test_trainer_advances_on_a_match_and_asks_again_on_a_miss():
    t = Trainer()
    first = t.offer("pcm0", "something completely different about football")
    assert not first["accepted"] and t.index == 0 and "once more" in first["note"]
    ok = t.offer("pcm1", "Jarvis good morning")
    assert ok["accepted"] and t.index == 1 and t.heard == ["Jarvis good morning"] and t.audio == ["pcm1"]


def test_trainer_skips_a_sentence_after_repeated_misses_without_learning_from_it():
    t = Trainer()
    for _ in range(vt.MAX_ATTEMPTS - 1):
        assert not t.offer("x", "bananas")["accepted"]
    last = t.offer("x", "bananas")
    assert last["accepted"] and "skipped" in last["note"]
    assert t.index == 1 and t.skipped == [0] and t.heard == [] and t.audio == []
    t.skip()
    assert t.index == 2 and t.skipped == [0, 1]


def test_trainer_scores_against_only_the_sentences_that_were_kept():
    t = Trainer()
    t.skip()                                             # sentence 0 skipped
    t.offer("a", "hey jarvish what time is it right now")
    t.offer("b", "i would like you to open crow and search for the weather today")
    assert t.kept_sentences() == [PASSAGE[1], PASSAGE[2]]
    before, alignments = t.score(t.heard)
    aliases, corrections = learn(alignments)
    after, _ = t.score(t.heard, corrections)
    assert 0.8 < before < 1.0 and after > before
    assert aliases == ["jarvish"] and corrections == {"crow": "chrome"}


def test_full_run_finishes():
    t = Trainer()
    for sentence in PASSAGE:
        assert t.offer("pcm", sentence)["accepted"]
    assert t.finished and t.sentence() == "" and t.score(t.heard)[0] == 1.0
    assert t.offer("pcm", "extra") == {"accepted": False, "note": ""}
