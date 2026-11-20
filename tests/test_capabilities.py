"""Autonomy modes, the new tool families, and the MCP bridge."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis import tools
from jarvis.gateway import validate_settings_patch
from jarvis.mcp_client import McpManager, _Server
from jarvis.policy import Tier, decide
from jarvis.tools.registry import REGISTRY, ToolError


def ask(tier, **kw):
    return decide("x", tier, "", session_grants=set(), **kw).action == "ask"


def test_trusted_mode_runs_ui_actions_even_when_tainted():
    assert not ask(Tier.T1, tainted=True, autonomy="trusted")
    assert not ask(Tier.T2, tainted=True, autonomy="trusted", sensitive=False)


def test_trusted_mode_still_asks_for_sensitive_and_destructive():
    assert ask(Tier.T2, tainted=False, autonomy="trusted", sensitive=True)
    assert ask(Tier.T3, tainted=False, autonomy="trusted", sensitive=False)
    assert ask(Tier.T3, tainted=False, autonomy="trusted", sensitive=True)


def test_guarded_mode_is_unchanged():
    assert ask(Tier.T1, tainted=True, autonomy="guarded")
    assert ask(Tier.T2, tainted=False, autonomy="guarded")


@pytest.mark.parametrize("name", ["shell_run", "email_send", "call_start", "files_write", "files_delete",
                                  "process_kill", "power_action"])
def test_outward_or_irreversible_tools_are_sensitive(name):
    assert REGISTRY[name].sensitive


@pytest.mark.parametrize("name", ["ui_snapshot", "browser_snapshot", "browser_read", "email_list", "email_read",
                                  "browser_tabs"])
def test_tools_that_read_outside_content_taint_the_run(name):
    assert REGISTRY[name].taints and REGISTRY[name].tier == Tier.T0


def test_every_schema_is_still_provider_safe():
    import re
    for schema in tools.schemas():
        assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", schema["name"]) and schema["parameters"]["type"] == "object"


def test_email_send_validates_recipients_and_is_never_auto():
    send = REGISTRY["email_send"]
    ok = send.args_model(to=["a@example.com"], subject="Hi", body="Hello")
    assert send.classify_call(ok)[0] == Tier.T2
    with pytest.raises(ToolError):
        send.classify_call(send.args_model(to=["not-an-address"], subject="Hi", body="x"))
    with pytest.raises(ToolError):
        send.classify_call(send.args_model(to=["a@example.com"], subject="Hi\nBcc: x@y.z", body="x"))


async def test_email_tools_explain_missing_setup():
    with pytest.raises(ToolError, match="not set up"):
        await REGISTRY["email_list"].handler(REGISTRY["email_list"].args_model())


def test_call_number_validation():
    call = REGISTRY["call_start"].args_model
    call(number="+91 98765 43210")
    for bad in ("tel:123;ext", "12", "abc", "+1 (555) 000-0000 & calc"):
        with pytest.raises(ValueError):
            call(number=bad)


def test_browser_rejects_non_web_urls_and_bad_refs():
    b = REGISTRY["browser_open"]
    assert b.classify_call(b.args_model(url="example.com"))[0] == Tier.T1
    for bad in ("file:///c:/x", "javascript:alert(1)", "chrome://settings"):
        with pytest.raises(ToolError):
            b.classify_call(b.args_model(url=bad))
    with pytest.raises(ValueError):
        REGISTRY["browser_click"].args_model(ref='b1"] , body')


def test_hotkey_parsing_and_refusals():
    hk = REGISTRY["ui_hotkey"]
    assert hk.classify_call(hk.args_model(keys="ctrl+s"))[0] == Tier.T2
    assert hk.classify_call(hk.args_model(keys="win+l"))[0] == Tier.T1
    with pytest.raises(ToolError):
        hk.classify_call(hk.args_model(keys="ctrl+alt+delete"))
    with pytest.raises(ToolError):
        hk.classify_call(hk.args_model(keys="ctrl+banana"))


def test_delete_and_power_are_t3_and_guarded(tmp_path):
    d = REGISTRY["files_delete"]
    target = tmp_path / "old.txt"
    target.write_text("x")
    assert d.classify_call(d.args_model(path=str(target)))[0] == Tier.T3
    for bad in ("C:\\", "C:\\Users", "~", r"~\.ssh\id_rsa", r"C:\Windows\System32\drivers"):
        with pytest.raises(ToolError):
            d.classify_call(d.args_model(path=bad))
    p = REGISTRY["power_action"]
    assert p.classify_call(p.args_model(action="shutdown"))[0] == Tier.T3
    assert p.classify_call(p.args_model(action="cancel"))[0] == Tier.T1


async def test_move_copy_and_recycle_roundtrip(tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("hello")
    mv = REGISTRY["files_move"]
    args = mv.args_model(source=str(src), destination=str(tmp_path / "b.txt"), as_copy=True)
    assert mv.classify_call(args)[0] == Tier.T2
    await mv.handler(args)
    assert (tmp_path / "b.txt").read_text() == "hello" and src.exists()
    with pytest.raises(ToolError):  # never overwrites
        mv.classify_call(mv.args_model(source=str(src), destination=str(tmp_path / "b.txt")))
    await REGISTRY["files_mkdir"].handler(REGISTRY["files_mkdir"].args_model(path=str(tmp_path / "sub" / "deep")))
    assert (tmp_path / "sub" / "deep").is_dir()
    await REGISTRY["files_delete"].handler(REGISTRY["files_delete"].args_model(path=str(tmp_path / "b.txt")))
    assert not (tmp_path / "b.txt").exists()


def test_settings_validation_for_new_keys():
    assert validate_settings_patch({"autonomy": "trusted"})
    assert validate_settings_patch({"mcp_servers": {"cal": {"url": "https://x.example/mcp"}, "gone": None}})
    assert validate_settings_patch({"mcp_servers": {"fs": {"command": "npx", "args": ["-y", "server"]}}})
    assert validate_settings_patch({"email": {"address": "a@b.co", "imap_host": "imap.b.co", "smtp_port": 587}})
    for bad in ({"autonomy": "yolo"}, {"mcp_servers": {"bad name": {"url": "https://x"}}},
                {"mcp_servers": {"x": {"url": "ftp://x"}}}, {"mcp_servers": {"x": {"command": "a", "url": "https://b"}}},
                {"mcp_servers": {"x": {"command": "a", "args": [1]}}}, {"email": {"imap_port": 0}},
                {"email": {"address": "has space@x.y"}}):
        with pytest.raises(ValueError):
            validate_settings_patch(bad)


class FakeSession:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        if name == "boom":
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="it broke")], isError=True)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=f"ran {name}")], isError=False)


async def test_mcp_tools_register_with_safe_defaults_and_unregister():
    manager = McpManager()
    server = _Server("my cal", {"url": "https://x"})
    server.session = FakeSession()
    read = SimpleNamespace(name="list.events", description="List events", inputSchema={"type": "object", "properties": {}},
                           annotations=SimpleNamespace(readOnlyHint=True))
    write = SimpleNamespace(name="create_event", description="Create", inputSchema=None, annotations=None)
    boom = SimpleNamespace(name="boom", description="", inputSchema={"properties": {}}, annotations=None)
    for spec in (read, write, boom):
        manager._register(server, spec)
    try:
        r, w = REGISTRY["mcp__my_cal__list_events"], REGISTRY["mcp__my_cal__create_event"]
        assert r.tier == Tier.T0 and not r.sensitive and r.taints          # read-only: auto, but untrusted output
        assert w.tier == Tier.T2 and w.sensitive and w.taints              # unknown effect: always asks
        assert w.schema()["parameters"]["type"] == "object"
        assert w.validate({"title": "x"}) == {"title": "x"}
        assert (await w.handler({"title": "x"}))["text"] == "ran create_event"
        assert server.session.calls[-1] == ("create_event", {"title": "x"})
        with pytest.raises(ToolError, match="it broke"):
            await REGISTRY["mcp__my_cal__boom"].handler({})
    finally:
        manager._unregister(server)
    assert not any(n.startswith("mcp__my_cal") for n in REGISTRY)


# -- keys, custom providers, wake word ----------------------------------------------------------------

def test_custom_providers_join_the_provider_list():
    from jarvis.config import all_specs, custom_specs

    settings = {"custom_providers": {
        "together": {"label": "Together", "base_url": "https://api.together.xyz/v1"},
        "mybox": {"label": "My box", "base_url": "http://127.0.0.1:8080/v1", "needs_key": False},
        "openai": {"label": "shadow", "base_url": "https://evil.example/v1"},   # may not replace a built-in
        "Bad Id": {"label": "x", "base_url": "https://x.example/v1"},
    }}
    specs = {s.id: s for s in custom_specs(settings)}
    assert set(specs) == {"together", "mybox"}
    assert not specs["together"].local and specs["together"].needs_key
    assert specs["mybox"].local and not specs["mybox"].needs_key          # loopback counts as local for privacy mode
    assert [s.base_url for s in all_specs(settings) if s.id == "openai"] == ["https://api.openai.com/v1"]


def test_custom_provider_and_voice_settings_validation():
    ok = {"custom_providers": {"together": {"label": "T", "base_url": "https://api.together.xyz/v1"}, "old": None}}
    assert validate_settings_patch(ok)
    assert validate_settings_patch({"voice": {"wake_word": True, "wake_sensitivity": 0.6, "wake_keys": "ctrl+win+space"}})
    for bad in ({"custom_providers": {"openai": {"base_url": "https://x.example/v1"}}},
                {"custom_providers": {"x": {"base_url": "file:///c:/x"}}},
                {"custom_providers": {"x": {"base_url": "https://x.example", "extra": 1}}},
                {"voice": {"wake_sensitivity": 5}}, {"voice": {"wake_keys": "ctrl+s; calc"}},
                {"mcp_servers": {"x": {"url": "https://x.example", "headers": {"A": "b\nInjected: 1"}}}}):
        with pytest.raises(ValueError):
            validate_settings_patch(bad)


def test_secret_names_and_exact_value_redaction(monkeypatch):
    from jarvis import secrets

    stored: dict[str, str] = {}
    monkeypatch.setattr(secrets.keyring, "set_password", lambda svc, name, value: stored.__setitem__(name, value))
    monkeypatch.setattr(secrets.keyring, "get_password", lambda svc, name: stored.get(name))
    secrets.set_key("notion_token", "zzTopSecretValue-123")
    assert secrets.get_key("notion_token") == "zzTopSecretValue-123"
    # A key with no recognisable prefix is still masked once Jarvis has handled it.
    assert "zzTopSecretValue-123" not in secrets.redact("header: zzTopSecretValue-123 end")
    for bad in ("Has Space", "UPPER", "1starts_with_digit", "x" * 40, "semi;colon"):
        with pytest.raises(ValueError):
            secrets.set_key(bad, "value12345")


def test_mcp_secret_references_are_expanded_and_missing_ones_fail(monkeypatch):
    from jarvis import mcp_client, secrets

    monkeypatch.setattr(secrets, "get_key", lambda name: {"notion_token": "abc123456"}.get(name))
    assert mcp_client._resolve({"Authorization": "Bearer ${secret:notion_token}", "X": "plain"}) == {
        "Authorization": "Bearer abc123456", "X": "plain"}
    with pytest.raises(RuntimeError, match="was not found"):
        mcp_client._resolve({"K": "${secret:missing}"})


def test_wake_listener_threshold_mapping_and_idle_state():
    from jarvis.wake import WakeListener

    w = WakeListener(lambda score, command, stage: None)
    w.apply(False, 0.5)
    assert w.state == "off" and w.threshold == 0.5
    w.apply(False, 0.85)
    assert abs(w.threshold - 0.15) < 1e-9      # most sensitive setting
    w.apply(False, 0.1)
    assert w.threshold == 0.9                  # least sensitive setting


def test_wake_model_scores_the_word_and_not_near_misses():
    """Runs the real model on synthesized speech when the files and Windows speech are available."""
    import subprocess
    import wave

    np = pytest.importorskip("numpy")
    from jarvis import wake

    if wake._model_files() is None:
        pytest.skip("wake-word model not downloaded")
    import tempfile
    from pathlib import Path

    out = Path(tempfile.mkdtemp())
    script = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, 'Sixteen', 'Mono'); "
              rf"$s.SetOutputToWaveFile('{out}/yes.wav', $f); $s.Speak('Jarvis'); "
              f"$s.SetOutputToWaveFile('{out}/other.wav', $f); $s.Speak('Travis is at the office today'); $s.SetOutputToNull(); $s.Dispose()")
    done = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], capture_output=True, timeout=60, check=False)
    if done.returncode != 0 or not (out / "yes.wav").exists() or not (out / "other.wav").exists():
        pytest.skip("Windows speech synthesis unavailable")

    def best(name: str) -> float:
        model = wake.load_model()
        with wave.open(str(out / name)) as wav:
            pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
        pcm = np.concatenate([np.zeros(16000, np.int16), pcm, np.zeros(16000, np.int16)])
        return max(float(max(model.predict(pcm[i:i + wake.FRAME]).values())) for i in range(0, len(pcm) - wake.FRAME, wake.FRAME))

    assert best("yes.wav") > 0.5 > best("other.wav")


def test_find_wake_matches_the_name_and_extracts_the_command():
    from jarvis.wake import find_wake

    assert find_wake("Jarvis") == (True, "")
    assert find_wake("Jarvis.") == (True, "")
    assert find_wake("Hey Jarvis, open Chrome.") == (True, "open Chrome")
    assert find_wake("okay jarvis what time is it?") == (True, "what time is it")
    assert find_wake("Jervis, mute") == (True, "mute")
    assert find_wake("Open the calculator, Jarvis.") == (True, "Open the calculator")   # the name may come last…
    assert find_wake("Can you Jarvis open Notepad?") == (True, "Can you open Notepad")  # …or in the middle
    assert find_wake("What time is it, hey Jarvis?") == (True, "What time is it")
    assert find_wake("Can you jar this open notepad?") == (True, "Can you open notepad")  # the name split in two
    for heard in ("Janvas", "Jarbis.", "Charvis", "Jalvis", "Jaavis", "Jorvice", "jar vis"):
        assert find_wake(heard) == (True, ""), heard
    for other in ("the jar is empty", "he gives jobs", "Jeeves is here", "the job is done", "jewels and gloves",
                  "java script", "chiefs and chives", "jobless juries"):
        assert find_wake(other) == (False, ""), other
    for text in ("Travis is at the office", "Please check the service", "We harvest the garden", "", "jar of vis", "Jarvison"):
        assert find_wake(text) == (False, ""), text


def test_speech_segmenter_cuts_one_utterance_and_ignores_room_noise():
    np = pytest.importorskip("numpy")
    from jarvis.wake import FRAME, SpeechSegmenter

    rng = np.random.default_rng(0)
    seg = SpeechSegmenter()
    out = []

    def feed(amplitude: float, frames: int) -> None:
        for _ in range(frames):
            samples = rng.normal(0, amplitude, FRAME).astype(np.int16)
            done = seg.feed(samples, float(np.sqrt(np.mean(samples.astype(np.float32) ** 2))))
            if done is not None:
                out.append(done)

    feed(10, 40)        # quiet room: nothing
    assert out == [] and not seg.active
    feed(400, 10)       # 0.8 s of speech-level sound
    feed(10, 12)        # quiet again ends the utterance
    assert len(out) == 1 and 10 * FRAME <= len(out[0]) <= 25 * FRAME
    feed(400, 1)        # a single click is not an utterance
    feed(10, 12)
    assert len(out) == 1


def _synth(tmp_path, text):
    import subprocess
    import wave

    import numpy as np

    wav = tmp_path / "say.wav"
    script = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, 'Sixteen', 'Mono'); "
              f"$s.SetOutputToWaveFile('{wav.as_posix()}', $f); $s.Speak('{text}'); $s.SetOutputToNull(); $s.Dispose()")
    done = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], capture_output=True, timeout=60, check=False)
    if done.returncode != 0 or not wav.exists():
        pytest.skip("Windows speech synthesis unavailable")
    with wave.open(str(wav)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


@pytest.mark.parametrize(("spoken", "expect_wake", "expect_command"), [
    ("Jarvis", True, ""),
    ("Jarvis, open Chrome", True, "open chrome"),
    ("Travis is at the office today", False, ""),
])
def test_wake_listener_end_to_end_on_quiet_noisy_audio(monkeypatch, tmp_path, spoken, expect_wake, expect_command):
    """Everything except the hardware: a quiet, noisy 'microphone' feeds the real listener threads and models."""
    import threading

    np = pytest.importorskip("numpy")
    sd = pytest.importorskip("sounddevice")
    pytest.importorskip("faster_whisper")
    from jarvis import wake

    if wake._model_files() is None or not any(wake.STT_DIR.glob("**/model.bin")):
        pytest.skip("wake or speech model not downloaded")
    speech = _synth(tmp_path, spoken)
    rng = np.random.default_rng(1)
    quiet = (speech * 0.03).astype(np.int16)  # 30 dB down, like this laptop's microphone array
    audio = np.concatenate([np.zeros(32000, np.int16), quiet, np.zeros(48000, np.int16)])
    audio = (audio + rng.normal(0, 5, len(audio))).astype(np.int16)
    finished = threading.Event()

    class FakeStream:
        def __init__(self, **kwargs):
            self.pos = 0

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, n):
            if self.pos + n > len(audio):
                finished.set()
                return np.zeros((n, 1), np.int16), False
            chunk = audio[self.pos:self.pos + n].reshape(-1, 1)
            self.pos += n
            return chunk, False

    monkeypatch.setattr(sd, "InputStream", FakeStream)
    monkeypatch.setattr(sd, "query_devices", lambda kind=None: {"name": "Test microphone"})
    heard: list[tuple[float, str, str]] = []
    listener = wake.WakeListener(lambda score, command, stage: heard.append((score, command, stage)))
    listener.capture_enabled = False   # this test pins the wake itself; listening mode is tested separately
    listener.apply(True, 0.5)
    try:
        assert finished.wait(timeout=90), "listener never consumed the audio"
        listener.drain(60)
    finally:
        listener.stop()
    stages = [stage for _score, _command, stage in heard]
    if expect_wake:
        assert stages == ["early", "final"], stages     # acknowledged once, completed once — never doubled
        assert listener.wakes == 1
        command = heard[-1][1].lower()
        # The command words depend on the speech model and the synthetic noise ("chrome" can come out as
        # "crew" 30 dB down); what this pins is that a command is captured when one was spoken.
        assert bool(command) == bool(expect_command)
        if expect_command:
            assert command.startswith(expect_command.split()[0])
    else:
        assert heard == [] and listener.wakes == 0
    assert listener.state == "off"


def test_wake_matching_tolerates_accents_but_not_other_words():
    from jarvis.wake import find_wake

    for heard in ("Javis", "Jarvish", "Jarwis", "Jarviz", "Jarves", "Charvis", "Jarvi's", "jarvis's", "Garvis"):
        assert find_wake(heard)[0], heard
    assert find_wake("Javis open chrome") == (True, "open chrome")
    assert find_wake("Hey Jarvish, mute the volume") == (True, "mute the volume")
    for other in ("Travis", "service", "harvest", "nervous", "Marvin", "jars", "Paris", "various", "carve"):
        assert not find_wake(f"{other} is here")[0], other


def test_learned_aliases_wake_and_carry_the_command():
    from jarvis.wake import find_wake, normalise_phrase

    assert normalise_phrase("  Jar,  Vis! ") == "jar vis"
    assert find_wake("Germans, open notepad") == (False, "")
    assert find_wake("Germans, open notepad", ("germans",)) == (True, "open notepad")
    assert find_wake("jar vis what time is it", ("jar vis",)) == (True, "what time is it")
    assert find_wake("we met some germans yesterday", ("germans",)) == (True, "we met some yesterday")


def test_calibration_collects_transcripts_instead_of_waking():
    from jarvis.wake import WakeListener

    woke: list = []
    results: list[list[str]] = []
    w = WakeListener(lambda score, command, stage: woke.append(command))
    assert not w.calibrate(results.append)          # not listening yet: refused
    w.state, w.speech = "listening", "ready"
    assert w.calibrate(results.append, count=2)
    assert not w.calibrate(results.append)          # one calibration at a time
    assert w._calibration_step("Jarvish") and w._calibration_step("Javis")
    assert results == [["Jarvish", "Javis"]] and woke == []
    assert not w._calibration_step("Jarvis")        # finished: normal detection resumes
    assert w.calibrate(results.append, count=5, timeout=0.0)
    w._calibration_step(None)                       # nobody spoke: times out with what it has
    assert results[-1] == []


def test_acknowledgement_chime_is_a_valid_short_wav():
    import io
    import wave

    from jarvis import ack

    with wave.open(io.BytesIO(ack._build_chime())) as wav:
        assert wav.getnchannels() == 1 and wav.getsampwidth() == 2
        assert 0.15 < wav.getnframes() / wav.getframerate() < 0.5


def test_voice_settings_accept_aliases_and_reject_junk():
    assert validate_settings_patch({"voice": {"wake_ack": False, "wake_reply": "At your service.", "wake_aliases": ["jarvish", "jar vis"]}})
    for bad in ({"voice": {"wake_aliases": ["UPPER"]}}, {"voice": {"wake_aliases": ["a b c d"]}},
                {"voice": {"wake_aliases": ["x"] * 13}}, {"voice": {"wake_reply": "<script>"}}, {"voice": {"wake_ack": "yes"}}):
        with pytest.raises(ValueError):
            validate_settings_patch(bad)


def _listener(seen, listen=False):
    from jarvis.wake import WakeListener

    w = WakeListener(lambda score, text, stage: seen.append((stage, text)))
    w.capture_enabled = listen
    return w


def test_wake_stages_early_then_final_and_no_double_wake():
    seen: list[tuple[str, str]] = []
    w = _listener(seen)
    assert w._fire_early(0.9, "wake model")
    assert not w._fire_early(0.9, "speech")              # the same word reaching the other path
    w._fire_final(1.0, "open chrome", True)              # the utterance ends with a command
    assert seen == [("early", ""), ("final", "open chrome")] and w.wakes == 1
    w._fire_final(1.0, "", True)                         # a stray second ending for the same utterance is ignored
    assert len(seen) == 2

    seen.clear()
    late = _listener(seen)
    late._fire_final(1.0, "", True)                      # the name was only caught when the utterance ended
    assert seen == [("early", ""), ("final", "")] and late.wakes == 1

    seen.clear()
    quiet = _listener(seen)
    quiet._fire_final(0.0, "", False, "what a lovely day")   # ordinary speech: nothing happens
    assert seen == [] and quiet.wakes == 0

    seen.clear()
    fast = _listener(seen)
    fast._fire_early(0.8, "wake model")
    fast._fire_final(0.8, "", False)                     # wake model heard it, the transcript did not: still completes
    assert seen == [("early", ""), ("final", "")]


def test_listening_mode_gathers_everything_until_the_window_closes():
    """Say the name, then as many sentences as you like: Jarvis acts once, on all of it, when the window ends."""
    seen: list[tuple[str, str]] = []
    w = _listener(seen, listen=True)
    assert w._fire_early(0.9, "speech") and w.capturing
    w._fire_final(1.0, "", True, "Jarvis")                         # the name on its own adds nothing
    assert seen == [("early", "")]
    assert not w._fire_early(0.9, "wake model")                    # already listening: no second chime
    w._fire_final(0.0, "", False, "Open the calculator.")
    assert seen[-1] == ("partial", "Open the calculator.") and w.capturing   # heard, but not acted on yet
    w._fire_final(0.0, "", False, "And set the volume to thirty.")
    assert seen[-1] == ("partial", "Open the calculator. And set the volume to thirty.")
    assert [stage for stage, _ in seen].count("final") == 0
    w._finish_capture()                                            # the window closes
    assert seen[-1] == ("final", "Open the calculator. And set the volume to thirty") and not w.capturing
    w._fire_final(0.0, "", False, "and now some unrelated chatter")
    assert [stage for stage, _ in seen].count("final") == 1 and w.wakes == 1


def test_listening_mode_one_breath_silence_and_misheard_name():
    seen: list[tuple[str, str]] = []
    w = _listener(seen, listen=True)
    w._fire_early(0.9, "speech")
    w._fire_final(1.0, "what time is it", True, "Jarvis, what time is it")   # said in one breath
    assert seen[-1] == ("partial", "what time is it")
    w._finish_capture()
    assert seen[-1] == ("final", "what time is it")

    seen.clear()
    idle = _listener(seen, listen=True)
    idle._fire_early(0.9, "speech")
    idle._finish_capture()                               # the user said the name and then nothing
    assert seen == [("early", ""), ("ended", "")] and not idle.capturing
    idle._finish_capture()
    assert len(seen) == 2                                # closing twice reports once

    seen.clear()
    accent = _listener(seen, listen=True)
    accent._fire_early(0.7, "wake model")                # the wake model heard the name…
    accent._fire_final(0.7, "", False, "Hey Travis")     # …but the words came out differently: not a request
    assert seen == [("early", "")] and accent.capturing
    accent._fire_final(0.0, "", False, "open notepad")
    accent._finish_capture()
    assert seen[-1] == ("final", "open notepad")

    seen.clear()
    late = _listener(seen, listen=True)
    late._fire_final(1.0, "", True, "Jarvis")            # name caught only at the end of the utterance
    assert seen == [("early", "")] and late.capturing    # still opens the listening window


def test_the_name_inside_or_after_a_request_carries_the_whole_request():
    seen: list[tuple[str, str]] = []
    w = _listener(seen, listen=True)
    w._fire_final(1.0, "Open the calculator", True, "Open the calculator, Jarvis")   # name said last
    assert seen == [("early", ""), ("partial", "Open the calculator")] and w.capturing
    assert w._heard_in_window                              # so a pause closes the window instead of the full wait
    w._finish_capture()
    assert seen[-1] == ("final", "Open the calculator")

    seen.clear()
    alone = _listener(seen, listen=True)
    alone._fire_early(0.9, "speech")
    alone._fire_final(1.0, "", True, "Jarvis")
    assert not alone._heard_in_window                      # just the name: wait the full time for the request


def test_a_half_heard_hey_jarvis_needs_only_roughly_matching_words():
    from jarvis.wake import BACKED_SCORE, match_wake

    for heard in ("Hey Charles", "A Germans.", "Hey, job is", "Hey Travis"):
        assert match_wake(heard, (), 0.0) == (False, ""), heard          # words alone: not the name
        assert match_wake(heard, (), BACKED_SCORE + 0.1)[0], heard       # backed by the wake model: accepted
    assert match_wake("Hey Charles, open notepad please", (), 0.3) == (True, "open notepad please")
    assert match_wake("and then we went to the market for some fruit", (), 0.3) == (False, "")
    assert match_wake("Jarvis, mute", (), 0.0) == (True, "mute")         # a clear name never needs the score


def test_the_owners_name_is_matched_by_sound_even_when_the_words_come_out_wrong(monkeypatch, tmp_path):
    """Enrol on recordings of the name; a quiet repeat matches, other speech does not, nothing but vectors is saved."""
    np = pytest.importorskip("numpy")
    pytest.importorskip("openwakeword")
    from jarvis import voiceprint, wake

    if wake._model_files() is None:
        pytest.skip("wake model not downloaded")
    rng = np.random.default_rng(7)

    def mic(pcm, gain=1.0):
        x = np.concatenate([np.zeros(5000, np.int16), pcm, np.zeros(9000, np.int16)]).astype(np.float32) * gain
        return (x + rng.normal(0, 5, len(x))).astype(np.int16)

    name = _synth(tmp_path, "Hey Jarvis")
    other = _synth(tmp_path, "What time is it right now")
    longer = _synth(tmp_path, "The weather today is quite nice and we should go for a walk in the park")
    vp = voiceprint.Voiceprint.enrol([mic(name), mic(name, 0.5), mic(name, 0.2)])
    assert len(vp.templates) == 3 and voiceprint.THRESHOLD_FLOOR <= vp.threshold <= voiceprint.THRESHOLD_CEIL
    assert vp.match(mic(name, 0.02)) is not None                 # a soft voice: 34 dB quieter than enrolment
    assert vp.match(mic(other)) is None and vp.match(mic(longer)) is None
    assert vp.distance(mic(other)) > vp.threshold * 1.2          # with room to spare
    assert voiceprint.Voiceprint.enrol([np.zeros(16000, np.int16), mic(longer)]).templates == []   # not short phrases

    path = tmp_path / "vp.npz"
    vp.save(path)
    again = voiceprint.Voiceprint.load(path)
    assert len(again.templates) == 3 and abs(again.threshold - vp.threshold) < 1e-6
    assert not voiceprint.Voiceprint.load(tmp_path / "missing.npz")

    # In the listener: words that do not spell the name still wake it, and the listening window opens.
    seen: list[tuple[str, str]] = []
    w = _listener(seen, listen=True)
    w.voiceprint = vp
    woke, rest = w._by_sound(None, mic(name, 0.3))               # nothing said around the name: no transcription needed
    assert woke and rest == ""
    assert w._by_sound(None, mic(other)) == (False, "")
    monkeypatch.setattr(voiceprint, "PRINT_PATH", path)
    monkeypatch.setattr(voiceprint.Voiceprint, "clear", staticmethod(lambda p=path: p.unlink(missing_ok=True)))
    w.forget_voice()
    assert not w.voiceprint and not path.exists()


def test_a_sentence_followed_by_the_name_after_a_pause_is_the_request():
    from jarvis import wake

    seen: list[tuple[str, str]] = []
    w = _listener(seen, listen=True)
    w._fire_final(0.0, "", False, "Open the calculator.")            # said first; nothing happens yet
    assert seen == [] and not w.capturing
    w._fire_early(0.9, "speech")                                      # then the name, after a pause
    w._fire_final(1.0, "", True, "Jarvis")
    assert seen == [("early", ""), ("partial", "Open the calculator.")] and w._heard_in_window
    w._finish_capture()
    assert seen[-1] == ("final", "Open the calculator")

    seen.clear()
    stale = _listener(seen, listen=True)
    stale._fire_final(0.0, "", False, "We should leave soon.")
    stale._recent = (stale._recent[0] - wake.LOOKBACK_S - 1, stale._recent[1])   # said too long ago
    stale._fire_final(1.0, "", True, "Jarvis")
    assert seen == [("early", "")]                                    # just the name: wait for a request

    seen.clear()
    own = _listener(seen, listen=True)
    own._fire_final(0.0, "", False, "We should leave soon.")
    own._fire_final(1.0, "what time is it", True, "Jarvis, what time is it")   # the name brings its own request
    assert seen[-1] == ("partial", "what time is it")


def test_listening_settings_validation():
    assert validate_settings_patch({"voice": {"wake_listen": True, "listen_window_s": 20, "listen_pause_s": 0}})
    assert validate_settings_patch({"voice": {"listen_window_s": 5, "listen_pause_s": 2.5}})
    for bad in ({"voice": {"listen_window_s": 120}}, {"voice": {"listen_window_s": 1}},
                {"voice": {"listen_pause_s": 0.2}}, {"voice": {"listen_pause_s": 60}}):
        with pytest.raises(ValueError):
            validate_settings_patch(bad)


def test_a_male_voice_is_the_default_when_one_is_installed(monkeypatch):
    from jarvis import ack

    monkeypatch.setattr(ack, "_voices_cache", [
        {"name": "Microsoft Zira Desktop - English (United States)", "gender": "female", "language": "409"},
        {"name": "Microsoft Ravi - English (India)", "gender": "male", "language": "4009"},
        {"name": "Microsoft George - English (United Kingdom)", "gender": "male", "language": "809"},
    ])
    assert ack.default_voice().startswith("Microsoft George")
    monkeypatch.setattr(ack, "_voices_cache", [{"name": "Microsoft Zira Desktop", "gender": "female", "language": "409"}])
    assert ack.default_voice() == "Microsoft Zira Desktop"      # no male voice: use what exists
    monkeypatch.setattr(ack, "_voices_cache", [])
    assert ack.default_voice() == ""


def test_listening_mode_end_to_end_name_pause_two_sentences(monkeypatch, tmp_path):
    """Real models, synthetic microphone in real time: the name, a pause, then a request in two sentences."""
    import threading
    import time as _time

    np = pytest.importorskip("numpy")
    sd = pytest.importorskip("sounddevice")
    pytest.importorskip("faster_whisper")
    from jarvis import wake

    if wake._model_files() is None or not any(wake.STT_DIR.glob("**/model.bin")):
        pytest.skip("wake or speech model not downloaded")
    name = _synth(tmp_path, "Jarvis")
    first = _synth(tmp_path, "What time is it right now")
    second = _synth(tmp_path, "And please open the calculator")
    rng = np.random.default_rng(2)

    def quiet(seconds: float):
        return np.zeros(int(16000 * seconds), np.int16)

    audio = np.concatenate([quiet(1.5), name // 4, quiet(1.4), first // 4, quiet(1.0), second // 4, quiet(5.0)])
    audio = (audio + rng.normal(0, 5, len(audio))).astype(np.int16)
    finished = threading.Event()

    class FakeStream:
        def __init__(self, **kwargs):
            self.pos = 0

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, n):
            _time.sleep(n / 16000)                     # real time, so the listening window behaves as it does live
            if self.pos + n > len(audio):
                finished.set()
                return np.zeros((n, 1), np.int16), False
            chunk = audio[self.pos:self.pos + n].reshape(-1, 1)
            self.pos += n
            return chunk, False

    monkeypatch.setattr(sd, "InputStream", FakeStream)
    monkeypatch.setattr(sd, "query_devices", lambda kind=None: {"name": "Test microphone"})
    seen: list[tuple[str, str]] = []
    levels: list[float] = []
    listener = wake.WakeListener(lambda score, text, stage: seen.append((stage, text)))
    listener.on_level = levels.append
    listener.listen_window, listener.listen_pause = 12.0, 2.0
    listener.apply(True, 0.5)
    try:
        assert finished.wait(timeout=120), "listener never consumed the audio"
        listener.drain(60)
        deadline = _time.monotonic() + 10
        while listener.capturing and _time.monotonic() < deadline:
            _time.sleep(0.1)
    finally:
        listener.stop()
    stages = [stage for stage, _ in seen]
    assert stages[0] == "early" and stages.count("early") == 1, stages
    finals = [text for stage, text in seen if stage == "final"]
    assert len(finals) == 1, seen                                       # acted exactly once…
    request = finals[0].lower()
    assert "time" in request and "calculator" in request, request       # …on both sentences together
    assert "jarvis" not in request
    assert stages.index("final") > max(i for i, st in enumerate(stages) if st == "partial")
    assert levels and max(levels) > 0.05                                # the live meter moved with the voice


# ---- browser sign-in for remote connectors -----------------------------------------------------------


def test_connector_sign_in_tokens_are_encrypted_at_rest(tmp_path):
    import asyncio

    pytest.importorskip("win32crypt")
    from mcp.shared.auth import OAuthToken

    from jarvis.mcp_oauth import TokenStore

    store = TokenStore("wispr notes/../x", tmp_path)
    assert store.path.parent == tmp_path and "/" not in store.path.name and ".." not in store.path.stem   # no path escape
    assert asyncio.run(store.get_tokens()) is None and not store.signed_in()
    asyncio.run(store.set_tokens(OAuthToken(access_token="secret-access-token-123", token_type="Bearer",
                                            refresh_token="secret-refresh-456", expires_in=3600)))
    raw = store.path.read_bytes()
    assert b"secret-access-token-123" not in raw and b"secret-refresh-456" not in raw       # not readable on disk
    again = asyncio.run(TokenStore("wispr notes/../x", tmp_path).get_tokens())
    assert again.access_token == "secret-access-token-123" and again.refresh_token == "secret-refresh-456"
    store.path.write_bytes(b"damaged")
    assert asyncio.run(store.get_tokens()) is None                                           # unreadable → sign in again
    store.forget()
    assert not store.path.exists()


def test_connector_sign_in_end_to_end_against_a_stand_in_server(tmp_path):
    """The real MCP sign-in protocol (discovery, registration, PKCE, code exchange) through Jarvis's handlers."""
    import asyncio
    import base64
    import hashlib
    from urllib.parse import parse_qs, urlsplit

    import httpx2
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from starlette.requests import Request as Req

    globals()["Req"] = Req  # annotations are strings in this file; FastAPI resolves them from module globals

    pytest.importorskip("win32crypt")
    from jarvis.mcp_oauth import SignIns, TokenStore, redirect_uri

    base = "https://notes.example"
    server = FastAPI()

    @server.middleware("http")
    async def trace(request, call_next):
        response = await call_next(request)
        requests_seen.append(f"{request.method} {request.url.path} -> {response.status_code}")
        return response

    requests_seen: list[str] = []
    issued: dict[str, dict] = {}
    seen = {"registered": None, "token_calls": 0}

    @server.get("/.well-known/oauth-protected-resource/mcp")
    @server.get("/.well-known/oauth-protected-resource")
    async def resource():
        return {"resource": f"{base}/mcp", "authorization_servers": [base]}

    @server.get("/.well-known/oauth-authorization-server")
    async def meta():
        return {"issuer": base, "authorization_endpoint": f"{base}/authorize", "token_endpoint": f"{base}/token",
                "registration_endpoint": f"{base}/register", "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"], "token_endpoint_auth_methods_supported": ["none"]}

    @server.post("/register")
    async def register(request: Req):
        body = await request.json()
        seen["registered"] = body
        return JSONResponse({**body, "client_id": "client-1"}, status_code=201)

    @server.post("/token")
    async def token(request: Req):
        form = await request.form()
        seen["token_calls"] += 1
        grant = issued.pop(str(form.get("code")), None)
        verifier = str(form.get("code_verifier", ""))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        if grant is None or grant["challenge"] != challenge or form.get("redirect_uri") != redirect_uri():
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        return {"access_token": "live-token", "token_type": "Bearer", "expires_in": 3600, "refresh_token": "r1"}

    @server.get("/mcp")
    async def mcp_endpoint(request: Req):
        if request.headers.get("authorization") != "Bearer live-token":
            return JSONResponse({"error": "unauthorized"}, status_code=401, headers={
                "WWW-Authenticate": f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource"'})
        return {"ok": True}

    sign_ins = SignIns(open_browser=lambda url: opened.append(url))
    opened: list[str] = []
    states: list[str] = []

    async def scenario():
        async def on_state(state):
            states.append(state)

        auth = sign_ins.provider("notes", f"{base}/mcp", on_state, TokenStore("notes", tmp_path))

        async def approve_in_browser():
            while not opened:
                await asyncio.sleep(0.01)
            q = parse_qs(urlsplit(opened[0]).query)
            assert q["redirect_uri"] == [redirect_uri()] and q["code_challenge_method"] == ["S256"]
            issued["code-1"] = {"challenge": q["code_challenge"][0]}
            assert not sign_ins.complete("some-other-state", "code-1")      # a forged callback is ignored
            assert sign_ins.complete(q["state"][0], "code-1", base)         # what the loopback route does
            assert not sign_ins.complete(q["state"][0], "code-1")           # and it cannot be replayed

        async with httpx2.AsyncClient(auth=auth, transport=httpx2.ASGITransport(app=server)) as http:
            approving = asyncio.create_task(approve_in_browser())
            first = await asyncio.wait_for(http.get(f"{base}/mcp"), timeout=20)
            await asyncio.wait_for(approving, timeout=10)
            second = await http.get(f"{base}/mcp")                          # signed in: no second browser trip
        return first, second

    try:
        first, second = asyncio.run(scenario())
    except BaseException:
        print("REQUESTS", requests_seen, "OPENED", opened, "STATES", states)
        raise
    assert first.status_code == 200 and second.status_code == 200 and len(opened) == 1
    assert states == ["signin", "connecting"] and seen["token_calls"] == 1
    assert seen["registered"]["redirect_uris"] == [redirect_uri()] and seen["registered"]["token_endpoint_auth_method"] == "none"
    assert TokenStore("notes", tmp_path).signed_in()
    assert b"live-token" not in TokenStore("notes", tmp_path).path.read_bytes()


def test_sign_in_refuses_an_insecure_page_and_the_callback_route_needs_a_waiting_state(tmp_path):
    import asyncio

    from fastapi.testclient import TestClient

    from jarvis.gateway import Hub, create_app
    from jarvis.mcp_oauth import CALLBACK_PATH, SignIns, TokenStore
    from jarvis.store import Store

    opened: list[str] = []
    sign_ins = SignIns(open_browser=opened.append)
    provider = sign_ins.provider("x", "https://x.example/mcp", None, TokenStore("x", tmp_path))
    with pytest.raises(RuntimeError, match="secure"):
        asyncio.run(provider.context.redirect_handler("http://evil.example/authorize?state=abc"))
    with pytest.raises(RuntimeError, match="secure"):
        asyncio.run(provider.context.redirect_handler("file:///c:/windows/system32/calc.exe?state=abc"))
    assert opened == []

    hub = Hub("t" * 32, store=Store(":memory:"))
    with TestClient(create_app(hub), base_url="http://127.0.0.1:47821") as client:
        assert client.get(CALLBACK_PATH, params={"state": "nobody-waiting", "code": "c"}).status_code == 400
        assert client.get(CALLBACK_PATH, params={"state": "s", "code": "c"}, headers={"host": "evil.example"}).status_code == 403
