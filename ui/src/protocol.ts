// WebSocket wire protocol (v1). Mirror of src/jarvis/protocol.py — change both together.
// A pytest (test_protocol_parity_with_typescript) asserts the `type: '…'` literals here match the core.

export type AgentState = 'idle' | 'thinking' | 'acting' | 'awaiting_approval' | 'error';
export type PrivacyMode = 'local' | 'hybrid' | 'cloud';
export type Tier = 0 | 1 | 2 | 3;

export type ThemeId = 'ion' | 'holographic' | 'sovereign';

export interface Settings {
  privacy_mode: PrivacyMode;
  routing: { mode: 'auto' | 'manual'; order: string[]; manual_provider: string | null };
  models: Record<string, string | null>;
  voice: { speak: boolean; auto_send: boolean; quiet_ms: number; wake_word: boolean; wake_sensitivity: number; wake_keys: string;
    wake_ack: boolean; wake_reply: string; wake_aliases: string[];
    stt_model: string; corrections: Record<string, string>; trained_at: string | null; tts_voice: string; wake_listen: boolean;
    listen_window_s: number; listen_pause_s: number;
    stt_engine: string; stt_cloud_model: string; tts_engine: string; tts_cloud_model: string; tts_cloud_voice: string };
  max_steps: number;
  autonomy: 'guarded' | 'trusted';
  theme: ThemeId;
  email: { address: string; imap_host: string; imap_port: number; smtp_host: string; smtp_port: number };
  mcp_servers: Record<string, McpServerConfig | null>;
  custom_providers: Record<string, { label: string; base_url: string; needs_key?: boolean } | null>;
}

export type McpServerConfig =
  | { command: string; args?: string[]; env?: Record<string, string> }
  | { url: string; headers?: Record<string, string> };

export interface TrainingResult {
  sentences: number; skipped: number; model: string; models: { name: string; accuracy: number }[];
  aliases: string[]; corrections: Record<string, string>; before: number; after: number;
  name_sentences?: number; name_recognised?: number; name_total?: number;
}

export interface VoiceTraining {
  state: 'reading' | 'evaluating' | 'done' | 'cancelled' | 'unavailable' | 'failed';
  index: number; total: number; sentence: string; heard?: string; note?: string; accepted?: boolean; result?: TrainingResult;
}

export interface CalibrationItem { text: string; recognised: boolean; alias: string }

export interface ToolInfo { name: string; description: string; tier: Tier; module: string }

export interface Integrations {
  email: { configured: boolean; has_password: boolean };
  mcp: { name: string; state: 'connecting' | 'signin' | 'ready' | 'error'; error: string; tools: number; target: string }[];
  tool_count: number;
  tools?: ToolInfo[];
  wake: { state: 'off' | 'starting' | 'listening' | 'error'; error: string; device: string; wakes: number; silent: boolean;
    speech: 'off' | 'loading' | 'ready' | 'unavailable'; utterances: number; near_misses: string[]; voiceprint?: number };
  voices: { name: string; gender: string; language: string }[];
  default_voice: string;
  vault: string[];
  speech?: { stt: SpeechEngine[]; tts: SpeechEngine[] };
}

export interface ProviderStatus {
  id: string;
  label: string;
  local: boolean;
  needs_key: boolean;
  has_key: boolean;
  key_help: string;
  state: 'ok' | 'no_key' | 'unreachable' | 'error' | 'no_model' | 'blocked' | 'testing' | 'unknown';
  detail: string;
  latency_ms: number | null;
  models: string[];
  model: string | null; // used for demanding requests
  fast_model: string | null; // used for short, simple requests
  auto: boolean; // chosen by Jarvis from live tests
  alternates: string[]; // fallback models, best first
  bench: ModelBench[];
  bench_at: number | null;
}

export interface ModelBench {
  model: string;
  ok: boolean;
  tool_ok: boolean;
  latency_ms: number;
  error: string;
}

export interface HistoryItem {
  role: 'user' | 'assistant';
  text: string;
  provider: string | null;
  model: string | null;
  ts: string | null;
}

export interface Telemetry {
  cpu_percent: number;
  ram_percent: number;
  ram_used_gb: number;
  ram_total_gb: number;
  gpu: { name: string; percent: number; vram_used_gb: number; vram_total_gb: number; temp_c: number | null } | null;
  disks: { mount: string; free_gb: number; total_gb: number; percent: number }[];
  net_down_kbps: number;
  net_up_kbps: number;
  battery: { percent: number; plugged: boolean } | null;
  top: { name: string; mem_mb: number; cpu_percent: number; count: number }[];
  wake?: { level: number; score: number } | null; // microphone level and recent best match while listening
}

export interface ApprovalRequest {
  approval_id: string;
  run_id: string;
  call_id: string;
  tool: string;
  summary: string;
  args: Record<string, unknown>;
  tier: Tier;
  reason: string;
  can_remember: boolean;
  timeout_s: number;
}

export interface AuditItem {
  id: number;
  ts: string;
  run_id: string;
  tool: string;
  args: Record<string, unknown>;
  tier: Tier;
  decision: string;
  decided_by: string;
  status: string;
  duration_ms: number;
  error: string | null;
}

export type ToolStatus = 'ok' | 'error' | 'denied' | 'refused' | 'cancelled';

// ---- core → HUD -------------------------------------------------------------------------------
export type ServerMessage =
  | {
      type: 'ready';
      v: number;
      version: string;
      state: AgentState;
      settings: Settings;
      providers: ProviderStatus[];
      history: HistoryItem[];
      approvals: ApprovalRequest[];
      hotkeys: { summon: string; kill: string };
      busy: boolean;
      has_window: boolean;
      integrations: Integrations;
    }
  | { type: 'state'; state: AgentState; detail: string }
  | ({ type: 'telemetry' } & Telemetry)
  | { type: 'providers'; providers: ProviderStatus[] }
  | { type: 'history'; messages: HistoryItem[] }
  | { type: 'user.echo'; text: string; run_id: string }
  | { type: 'assistant.start'; run_id: string; provider: string; provider_label: string; model: string }
  | { type: 'assistant.delta'; run_id: string; text: string }
  | { type: 'assistant.done'; run_id: string; cancelled: boolean; has_tools?: boolean; input_tokens?: number; output_tokens?: number }
  | { type: 'tool.start'; run_id: string; call_id: string; tool: string; tier: Tier; summary: string; args: Record<string, unknown> }
  | { type: 'tool.end'; call_id: string; status: ToolStatus; duration_ms: number; error: string | null }
  | ({ type: 'approval.request' } & ApprovalRequest)
  | { type: 'approval.resolved'; approval_id: string; decision: 'allow' | 'deny' | 'timeout' }
  | { type: 'settings'; settings: Settings }
  | { type: 'audit.page'; items: AuditItem[]; has_more: boolean; append: boolean }
  | { type: 'ui.focus'; reason?: 'hotkey' | 'wake'; text?: string } // text: command spoken after the wake word
  | ({ type: 'integrations' } & Integrations)
  | { type: 'selfheal'; faults: Fault[]; repairs: Recovery[] }
  | { type: 'wake.calibration'; state: 'listening' | 'done' | 'unavailable'; items: CalibrationItem[] }
  | ({ type: 'voice.training' } & VoiceTraining)
  | { type: 'voice.session'; state: 'listening' | 'final' | 'ended'; text: string; window_s?: number }
  | { type: 'voice.level'; level: number }
  | { type: 'notice'; level: 'info' | 'warn'; message: string }
  | { type: 'error'; message: string; code: string; run_id?: string }
  | { type: 'memory.update'; id: string; op: string; [k: string]: unknown }
  | { type: 'schedule.fire'; name: string; [k: string]: unknown }
  | { type: 'agent.update'; agent_id: string; status: string; [k: string]: unknown }
  | { type: 'clipboard.new'; id: number; text: string; app: string }
  | { type: 'screen.capture'; text: string; app: string }
  | { type: 'telegram.message'; chat_id: string; text: string }
  | { type: 'ha.state'; entity_id: string; state: string; [k: string]: unknown };

// ---- HUD → core -------------------------------------------------------------------------------
export type ClientMessage =
  | { type: 'hello'; token: string; v: number }
  | { type: 'user.message'; text: string }
  | { type: 'run.cancel' }
  | { type: 'approval.respond'; approval_id: string; decision: 'allow' | 'deny'; remember: boolean }
  | { type: 'conversation.new' }
  | { type: 'settings.set'; patch: Partial<Settings> | Record<string, unknown> }
  | { type: 'secret.set'; provider: string; value: string }
  | { type: 'providers.refresh'; retest?: boolean }
  | { type: 'audit.query'; before_id?: number | null; tool?: string | null; min_tier?: number | null; limit?: number }
  | { type: 'window.control'; action: 'minimize' | 'toggle_maximize' | 'close' }
  | { type: 'wake.calibrate' }
  | { type: 'voice.train'; action: 'start' | 'skip' | 'cancel' }
  | { type: 'voice.preview'; voice: string }
  | { type: 'selfheal.action'; action: 'list' | 'propose' | 'apply' | 'rollback' | 'dismiss'; fault_id?: string }
  | { type: 'client.error'; message: string; stack?: string };

export interface SpeechEngine { id: string; label: string; note: string; model: string; voice: string; ready: boolean; selectable: boolean }
export interface Fault {
  id: string; source: 'core' | 'display'; kind: string; message: string; traceback: string; file: string; line: number;
  count: number; first: string; last: string; diagnosis: string; diff: string; check: string; note: string;
  status: 'open' | 'diagnosing' | 'checking' | 'proposed' | 'applied' | 'rolled_back' | 'dismissed';
}
export interface Recovery { at: string; what: string; detail: string; ok: boolean }

export const PROTOCOL_VERSION = 1;
