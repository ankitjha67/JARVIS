"""WebSocket wire protocol (v1). Mirror of ui/src/protocol.ts — change both together.

Client messages are validated with pydantic. Server messages are plain dicts built through `msg()`,
which rejects any type not declared in SERVER_TYPES so the two sides cannot drift silently.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .config import PROTOCOL_VERSION


class _Client(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Hello(_Client):
    type: Literal["hello"]
    token: str = Field(..., min_length=16, max_length=128)
    v: int = PROTOCOL_VERSION


class UserMessage(_Client):
    type: Literal["user.message"]
    text: str = Field(..., min_length=1, max_length=20_000)


class RunCancel(_Client):
    type: Literal["run.cancel"]


class ApprovalRespond(_Client):
    type: Literal["approval.respond"]
    approval_id: str = Field(..., max_length=64)
    decision: Literal["allow", "deny"]
    remember: bool = False


class ConversationNew(_Client):
    type: Literal["conversation.new"]


class SettingsSet(_Client):
    type: Literal["settings.set"]
    patch: dict[str, Any]


class SecretSet(_Client):
    type: Literal["secret.set"]
    provider: str = Field(..., max_length=32)
    value: str = Field("", max_length=512)  # empty string removes the key


class ProvidersRefresh(_Client):
    type: Literal["providers.refresh"]
    retest: bool = False  # also re-run the model tests behind automatic selection


class AuditQuery(_Client):
    type: Literal["audit.query"]
    before_id: int | None = None
    tool: str | None = Field(None, max_length=64)
    min_tier: int | None = Field(None, ge=0, le=3)
    limit: int = Field(50, ge=1, le=200)


class VoicePreview(_Client):
    type: Literal["voice.preview"]
    voice: str = Field("", max_length=80)


class VoiceTrain(_Client):
    type: Literal["voice.train"]
    action: Literal["start", "skip", "cancel"]


class WakeCalibrate(_Client):
    type: Literal["wake.calibrate"]


class SelfHeal(_Client):
    type: Literal["selfheal.action"]
    action: Literal["list", "propose", "apply", "rollback", "dismiss"]
    fault_id: str = Field("", max_length=32)


class ClientError(_Client):
    """The display reports a crash of its own so it lands in the fault log."""
    type: Literal["client.error"]
    message: str = Field(..., max_length=600)
    stack: str = Field("", max_length=4000)


class WindowControl(_Client):
    type: Literal["window.control"]
    action: Literal["minimize", "toggle_maximize", "close"]


class RunCenter(_Client):
    type: Literal["runs.action"]
    action: Literal["list", "detail", "cancel", "reconcile", "save", "run", "schedule", "test",
                    "health", "pause_schedule", "resume_schedule", "handover", "revise"]
    id: str = Field("", max_length=64)
    name: str = Field("", max_length=100)
    schedule: str = Field("", max_length=60)
    inputs: dict[str, Any] = Field(default_factory=dict)
    poll: bool = False


ClientMessage = Annotated[
    Hello | UserMessage | RunCancel | ApprovalRespond | ConversationNew | SettingsSet | SecretSet
    | ProvidersRefresh | AuditQuery | WindowControl | WakeCalibrate | VoiceTrain | VoicePreview | SelfHeal | ClientError | RunCenter,
    Field(discriminator="type"),
]
client_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)

CLIENT_TYPES = frozenset({
    "hello", "user.message", "run.cancel", "approval.respond", "conversation.new", "settings.set", "secret.set",
    "providers.refresh", "audit.query", "window.control", "wake.calibrate", "voice.train", "voice.preview",
    "selfheal.action", "client.error", "runs.action",
})

SERVER_TYPES = frozenset({
    "ready", "state", "telemetry", "providers", "history", "user.echo", "assistant.start", "assistant.delta",
    "assistant.done", "tool.start", "tool.end", "approval.request", "approval.resolved", "settings",
    "audit.page", "ui.focus", "notice", "error", "integrations", "wake.calibration", "voice.training", "voice.session", "voice.level",
    "selfheal",
    "memory.update", "schedule.fire", "agent.update", "clipboard.new", "screen.capture",
    "telegram.message", "ha.state",
    "bg_task.update", "bg_task.step", "research.progress", "research.done",
    "runs.page", "routine.fire", "routine.result", "undo.pushed", "verify.result", "ladder.stats",
})

AgentState = Literal["idle", "thinking", "acting", "awaiting_approval", "error"]


def msg(type_: str, **fields: Any) -> dict[str, Any]:
    if type_ not in SERVER_TYPES:
        raise ValueError(f"undeclared server message type: {type_}")
    return {"type": type_, **fields}
