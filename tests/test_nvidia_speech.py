"""NVIDIA hosted speech-to-text (gRPC): engine wiring, request shape, and failures. No network."""

from __future__ import annotations

import numpy as np
import pytest

from jarvis import secrets, speech

PCM = (np.sin(np.arange(16000) / 20) * 8000).astype(np.int16)
SETTINGS = {"privacy_mode": "hybrid", "voice": {}}


class _Stub:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def Recognize(self, request, metadata=None, timeout=None):
        self.calls.append((request, metadata, timeout))
        if self.error:
            raise self.error
        return self.reply


def _service(monkeypatch, stub):
    from types import SimpleNamespace

    service = SimpleNamespace(stub=stub, auth=SimpleNamespace(get_auth_metadata=lambda: [("authorization", "x")]))
    function_id = speech.NVIDIA_STT_FUNCTIONS["parakeet-tdt-0.6b-v2"]
    monkeypatch.setattr(speech, "_riva_services", {(function_id, "nvapi-test"): service})
    monkeypatch.setattr(secrets, "get_key", lambda name: "nvapi-test" if name == "nvidia" else "")
    return function_id


def test_engine_is_offered_and_ready_only_with_a_key(monkeypatch):
    monkeypatch.setattr(secrets, "has_key", lambda name: name == "nvidia")
    row = next(r for r in speech.catalogue(SETTINGS)["stt"] if r["id"] == "nvidia")
    assert row["selectable"] and row["ready"] and row["model"] == "parakeet-tdt-0.6b-v2"
    assert not next(r for r in speech.catalogue({"privacy_mode": "local"})["stt"] if r["id"] == "nvidia")["ready"]


def test_transcribes_raw_pcm_with_a_timeout(monkeypatch):
    pytest.importorskip("riva.client")
    from riva.client.proto import riva_asr_pb2

    reply = riva_asr_pb2.RecognizeResponse()
    reply.results.add().alternatives.add().transcript = "Open Chrome. "
    reply.results.add().alternatives.add().transcript = "Then search."
    stub = _Stub(reply)
    _service(monkeypatch, stub)
    assert speech.transcribe(PCM, "nvidia", SETTINGS) == "Open Chrome. Then search."
    request, metadata, timeout = stub.calls[0]
    assert request.audio == PCM.astype("<i2").tobytes() and request.config.sample_rate_hertz == 16000
    assert request.config.language_code == "en-US" and metadata and timeout == speech.TIMEOUT_S


def test_failure_is_reported_without_the_key_and_reconnects(monkeypatch):
    pytest.importorskip("riva.client")
    stub = _Stub(error=RuntimeError("PermissionDenied for Bearer nvapi-" + "a" * 40))
    function_id = _service(monkeypatch, stub)
    with pytest.raises(RuntimeError, match="NVIDIA transcription failed") as caught:
        speech._transcribe_riva(PCM, "nvapi-test", "parakeet-tdt-0.6b-v2")
    assert "a" * 40 not in str(caught.value)
    assert (function_id, "nvapi-test") not in speech._riva_services


def test_refuses_without_a_key_unknown_models_and_local_only_mode(monkeypatch):
    with pytest.raises(RuntimeError, match="no key"):
        speech._transcribe_riva(PCM, "", "parakeet-tdt-0.6b-v2")
    with pytest.raises(RuntimeError, match="unknown NVIDIA speech model"):
        speech._transcribe_riva(PCM, "nvapi-test", "canary-qwen-2.5b")
    with pytest.raises(RuntimeError, match="local-only"):
        speech.transcribe(PCM, "nvidia", {"privacy_mode": "local"})
