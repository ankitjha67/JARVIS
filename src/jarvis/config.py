"""Static configuration: paths, network, provider catalogue, default settings."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

# JARVIS_ROOT lets self-repair run the test-suite against a copy of the package that lives elsewhere.
ROOT = Path(os.environ.get("JARVIS_ROOT") or Path(__file__).resolve().parents[2])
DATA_DIR = Path(os.environ.get("JARVIS_DATA_DIR", ROOT / "data"))
UI_DIST = ROOT / "ui" / "dist"
DB_PATH = DATA_DIR / "jarvis.db"
LOG_PATH = DATA_DIR / "jarvis.log"

HOST = "127.0.0.1"  # loopback only (SEC-2)
PORT = int(os.environ.get("JARVIS_PORT", "47821"))
DEV = os.environ.get("JARVIS_DEV") == "1"
DEV_ORIGINS = ("http://127.0.0.1:5173", "http://localhost:5173")

KEYRING_SERVICE = "jarvis"
PROTOCOL_VERSION = 1

APPROVAL_TIMEOUT_S = 120
TOOL_TIMEOUT_S = 120
HISTORY_WINDOW = 40  # messages replayed to the model per run


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    kind: Literal["openai", "anthropic"]
    base_url: str | None
    local: bool
    needs_key: bool
    default_model: str | None = None
    key_help: str = ""


PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec("lmstudio", "LM Studio", "openai", "http://127.0.0.1:1234/v1", True, False),
    ProviderSpec("ollama", "Ollama", "openai", "http://127.0.0.1:11434/v1", True, False),
    ProviderSpec(
        "nvidia", "NVIDIA NIM", "openai", "https://integrate.api.nvidia.com/v1", False, True,
        key_help="build.nvidia.com → API key (nvapi-…)",
    ),
    ProviderSpec(
        "anthropic", "Anthropic", "anthropic", None, False, True,
        default_model="claude-opus-5-5", key_help="console.anthropic.com → API keys",
    ),
    ProviderSpec(
        "openai", "OpenAI", "openai", "https://api.openai.com/v1", False, True,
        key_help="platform.openai.com → API keys",
    ),
    ProviderSpec(
        "gemini", "Google Gemini", "openai",
        "https://generativelanguage.googleapis.com/v1beta/openai/", False, True,
        key_help="aistudio.google.com → Get API key",
    ),
    ProviderSpec(
        "openrouter", "OpenRouter", "openai", "https://openrouter.ai/api/v1", False, True,
        key_help="openrouter.ai → Keys",
    ),
    ProviderSpec(
        "groq", "Groq", "openai", "https://api.groq.com/openai/v1", False, True,
        key_help="console.groq.com → API keys",
    ),
)
PROVIDER_BY_ID = {p.id: p for p in PROVIDERS}

PrivacyMode = Literal["local", "hybrid", "cloud"]

DEFAULT_SETTINGS: dict[str, Any] = {
    "privacy_mode": "hybrid",
    "routing": {
        "mode": "auto",  # auto | manual
        "order": ["anthropic", "openai", "gemini", "nvidia", "openrouter", "groq", "lmstudio", "ollama"],
        "manual_provider": None,
    },
    "models": {},  # provider id -> chosen model id
    "voice": {"speak": False, "auto_send": True, "quiet_ms": 1200,
              "wake_word": False, "wake_sensitivity": 0.5, "wake_keys": "",
              "wake_ack": True, "wake_reply": "Yes?", "wake_aliases": [],
              "stt_model": "base.en", "corrections": {}, "trained_at": None,
              "tts_voice": "",  # "" = the first installed male English voice
              "wake_listen": True,  # after the name, keep listening for the whole request
              "listen_window_s": 10, "listen_pause_s": 2.5,
              # Speech engines (see speech.py). "local"/"system" stay on this computer.
              "stt_engine": "local", "stt_cloud_model": "", "tts_engine": "system", "tts_cloud_model": "",
              "tts_cloud_voice": ""},  # how long to listen; act sooner after this much quiet
    "max_steps": 25,
    "autonomy": "guarded",  # guarded | trusted (see policy.decide)
    "email": {"address": "", "imap_host": "", "imap_port": 993, "smtp_host": "", "smtp_port": 465},
    "response_style": {"name": "default", "instruction": ""},
    "persona": "",
    "mcp_servers": {},  # name -> {"command", "args", "env"} or {"url", "headers"}
    "custom_providers": {},  # id -> {"label": str, "base_url": str, "needs_key": bool}
}

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
RESERVED_SECRETS = frozenset({p.id for p in PROVIDERS} | {"email"})


def custom_specs(settings: dict[str, Any]) -> list[ProviderSpec]:
    """User-defined OpenAI-compatible endpoints (FR-P11)."""
    out = []
    for pid, cfg in (settings.get("custom_providers") or {}).items():
        if not isinstance(cfg, dict) or pid in PROVIDER_BY_ID or not NAME_RE.match(pid):
            continue
        base = str(cfg.get("base_url", ""))
        local = base.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]"))
        out.append(ProviderSpec(pid, str(cfg.get("label") or pid)[:40], "openai", base, local,
                                bool(cfg.get("needs_key", True)), key_help="the key for this endpoint"))
    return out


def all_specs(settings: dict[str, Any]) -> list[ProviderSpec]:
    return [*PROVIDERS, *custom_specs(settings)]


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
