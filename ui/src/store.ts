import { create } from 'zustand';
import type {
  AgentState, ApprovalRequest, AuditItem, CalibrationItem, Fault, Integrations, ProviderStatus, Recovery, RunPage, ServerMessage, Settings, Telemetry, Tier, ToolStatus, VoiceTraining,
} from './protocol';

export type View = 'console' | 'audit' | 'repair' | 'settings' | 'runs';
export type Conn = 'connecting' | 'online' | 'offline' | 'rejected';

export type TranscriptItem =
  | { kind: 'user'; id: string; text: string }
  | { kind: 'assistant'; id: string; text: string; streaming: boolean; provider: string | null; model: string | null }
  | { kind: 'notice'; id: string; level: 'info' | 'warn' | 'error'; text: string };

export interface ToolRow {
  callId: string;
  tool: string;
  tier: Tier;
  summary: string;
  args: Record<string, unknown>;
  status: 'running' | 'waiting' | ToolStatus;
  durationMs: number | null;
  error: string | null;
  startedAt: number;
}

/** One line of the live event stream. Every entry comes from a real core message. */
export interface StreamEvent {
  id: number;
  at: number;
  level: 'info' | 'ok' | 'warn' | 'error';
  text: string;
}

export interface SessionStats {
  startedAt: number;
  runs: number;
  actions: number;
  approvals: number;
  tokensIn: number;
  tokensOut: number;
}

const HISTORY = 60;
const EVENTS = 80;

export interface HudState {
  conn: Conn;
  attempt: number;
  version: string;
  hasWindow: boolean;
  canBackground: boolean;
  hotkeys: { summon: string; kill: string };
  view: View;
  agent: AgentState;
  agentDetail: string;
  busy: boolean;
  settings: Settings | null;
  providers: ProviderStatus[];
  transcript: TranscriptItem[];
  activity: ToolRow[];
  approvals: ApprovalRequest[];
  runs: RunPage | null;
  telemetry: Telemetry | null;
  series: { cpu: number[]; ram: number[]; gpu: number[]; down: number[]; up: number[] };
  audit: { items: AuditItem[]; hasMore: boolean; loading: boolean; loaded: boolean };
  focusTick: number;
  lastSpoken: { id: string; text: string } | null;
  pulse: number; // increments on each finished tool call; drives the Core's shockwave
  events: StreamEvent[];
  stats: SessionStats;
  route: { provider: string; model: string } | null; // model that served the latest turn
  integrations: Integrations | null;
  calibration: { state: 'idle' | 'listening' | 'done' | 'unavailable'; items: CalibrationItem[] };
  selfheal: { faults: Fault[]; repairs: Recovery[] };
  training: VoiceTraining | null;
  // A spoken request in progress: Jarvis heard its name and is listening until the user finishes.
  voice: { active: boolean; text: string; level: number; levels: number[]; startedAt: number; windowS: number };
  voiceCommand: { id: number; text: string } | null; // spoken after the wake word; the command bar sends it
  setView: (v: View) => void;
  setConn: (c: Conn, attempt?: number) => void;
  setAuditLoading: () => void;
  apply: (m: ServerMessage) => void;
}

let seq = 0;
const nid = () => `i${++seq}`;
let eseq = 0;
const push = (arr: number[], v: number) => [...arr.slice(-(HISTORY - 1)), v];
const log = (s: HudState, level: StreamEvent['level'], text: string): StreamEvent[] =>
  [...s.events.slice(-(EVENTS - 1)), { id: ++eseq, at: Date.now(), level, text }];
const short = (model: string) => model.split('/').pop() ?? model;

function providerEvents(s: HudState, next: ProviderStatus[]): StreamEvent[] {
  let events = s.events;
  for (const p of next) {
    const prev = s.providers.find((x) => x.id === p.id);
    if (prev && prev.state === p.state && prev.model === p.model) continue;
    if (!prev && (p.state === 'no_key' || p.state === 'unreachable')) continue; // quiet first sighting
    const level = p.state === 'ok' ? 'ok' : p.state === 'error' ? 'error' : 'info';
    const text = p.state === 'ok' ? `${p.label} ready · ${short(p.model ?? '')}`
      : p.state === 'testing' ? `${p.label} testing models for tools and speed`
      : `${p.label} ${p.state.replace('_', ' ')}`;
    events = [...events.slice(-(EVENTS - 1)), { id: ++eseq, at: Date.now(), level, text }];
  }
  return events;
}

/** Pure reducer for every server message. Exported for tests. */
export function reduce(s: HudState, m: ServerMessage): Partial<HudState> {
  switch (m.type) {
    case 'runs.page':
      return { runs: {
        tasks: m.tasks, saved: m.saved, schedules: m.schedules, detail: m.detail ?? s.runs?.detail ?? null,
        result: m.poll ? s.runs?.result ?? null : m.result,
        error: m.error ?? (m.poll ? s.runs?.error ?? null : null),
      } };
    case 'ready':
      return {
        version: m.version, hasWindow: m.has_window, canBackground: !!m.can_background, hotkeys: m.hotkeys, agent: m.state, busy: m.busy,
        settings: m.settings, providers: m.providers, approvals: m.approvals, integrations: m.integrations,
        events: log({ ...s, events: providerEvents(s, m.providers) }, 'ok', `core link established · v${m.version}`),
        transcript: m.history.map((h) =>
          h.role === 'user'
            ? { kind: 'user' as const, id: nid(), text: h.text }
            : { kind: 'assistant' as const, id: nid(), text: h.text, streaming: false, provider: h.provider, model: h.model }),
      };
    case 'state':
      return {
        agent: m.state, agentDetail: m.detail, busy: m.state !== 'idle' && m.state !== 'error',
        events: m.state === s.agent ? s.events : log(s, m.state === 'error' ? 'error' : 'info', `state → ${m.state.replace('_', ' ')}`),
      };
    case 'telemetry': {
      const { type: _t, ...t } = m;
      return {
        telemetry: t,
        series: {
          cpu: push(s.series.cpu, t.cpu_percent),
          ram: push(s.series.ram, t.ram_percent),
          gpu: push(s.series.gpu, t.gpu ? t.gpu.percent : 0),
          down: push(s.series.down, t.net_down_kbps),
          up: push(s.series.up, t.net_up_kbps),
        },
      };
    }
    case 'providers':
      return { providers: m.providers, events: providerEvents(s, m.providers) };
    case 'history':
      return {
        activity: [], approvals: [],
        transcript: m.messages.map((h) =>
          h.role === 'user'
            ? { kind: 'user' as const, id: nid(), text: h.text }
            : { kind: 'assistant' as const, id: nid(), text: h.text, streaming: false, provider: h.provider, model: h.model }),
        events: log(s, 'info', 'new conversation'),
      };
    case 'user.echo':
      return {
        transcript: [...s.transcript, { kind: 'user', id: nid(), text: m.text }], busy: true,
        stats: { ...s.stats, runs: s.stats.runs + 1 },
        events: log(s, 'info', `request received · ${m.text.length} chars`),
      };
    case 'assistant.start':
      return {
        route: { provider: m.provider_label, model: m.model },
        events: log(s, 'info', `route → ${m.provider_label} · ${short(m.model)}`),
        transcript: [...s.transcript,
          { kind: 'assistant', id: nid(), text: '', streaming: true, provider: m.provider_label, model: m.model }],
      };
    case 'assistant.delta': {
      const t = s.transcript.slice();
      const last = t[t.length - 1];
      if (last && last.kind === 'assistant' && last.streaming) t[t.length - 1] = { ...last, text: last.text + m.text };
      return { transcript: t };
    }
    case 'assistant.done': {
      const t = s.transcript.slice();
      const last = t[t.length - 1];
      let spoken = s.lastSpoken;
      if (last && last.kind === 'assistant' && last.streaming) {
        if (!last.text.trim()) t.pop(); // a turn that only called tools leaves no bubble
        else {
          t[t.length - 1] = { ...last, streaming: false };
          if (!m.cancelled && !m.has_tools) spoken = { id: last.id, text: last.text };
        }
      }
      if (m.cancelled) t.push({ kind: 'notice', id: nid(), level: 'warn', text: 'Cancelled.' });
      const tin = m.input_tokens ?? 0;
      const tout = m.output_tokens ?? 0;
      return {
        transcript: t, lastSpoken: spoken,
        stats: { ...s.stats, tokensIn: s.stats.tokensIn + tin, tokensOut: s.stats.tokensOut + tout },
        events: m.cancelled ? log(s, 'warn', 'run cancelled')
          : tin + tout > 0 ? log(s, 'info', `turn complete · ${tin} in / ${tout} out tokens`) : s.events,
      };
    }
    case 'tool.start':
      return {
        events: log(s, 'info', `tool ${m.tool} · T${m.tier}`),
        activity: [...s.activity.filter((r) => r.callId !== m.call_id), {
          callId: m.call_id, tool: m.tool, tier: m.tier, summary: m.summary, args: m.args, status: 'running' as const,
          durationMs: null, error: null, startedAt: Date.now(),
        }].slice(-200),
      };
    case 'tool.end': {
      const row = s.activity.find((r) => r.callId === m.call_id);
      return {
        pulse: s.pulse + 1,
        stats: { ...s.stats, actions: s.stats.actions + (m.status === 'ok' ? 1 : 0) },
        events: log(s, m.status === 'ok' ? 'ok' : m.status === 'error' || m.status === 'refused' ? 'error' : 'warn',
          `${row?.tool ?? 'tool'} ${m.status}${m.status === 'ok' ? ` · ${m.duration_ms} ms` : ''}`),
        activity: s.activity.map((r) =>
          r.callId === m.call_id ? { ...r, status: m.status, durationMs: m.duration_ms, error: m.error } : r),
      };
    }
    case 'approval.request': {
      const { type: _t, ...a } = m;
      return {
        stats: { ...s.stats, approvals: s.stats.approvals + 1 },
        events: log(s, 'warn', `approval required · ${a.tool} · T${a.tier}`),
        approvals: [...s.approvals.filter((x) => x.approval_id !== a.approval_id), a],
        activity: s.activity.map((r) => (r.callId === a.call_id ? { ...r, status: 'waiting' as const } : r)),
      };
    }
    case 'approval.resolved': {
      const gone = s.approvals.find((a) => a.approval_id === m.approval_id);
      return {
        events: log(s, m.decision === 'allow' ? 'ok' : 'warn', `approval ${m.decision === 'allow' ? 'granted' : m.decision === 'deny' ? 'denied' : 'timed out'}`),
        approvals: s.approvals.filter((a) => a.approval_id !== m.approval_id),
        activity: s.activity.map((r) =>
          gone && r.callId === gone.call_id && r.status === 'waiting' ? { ...r, status: 'running' as const } : r),
      };
    }
    case 'settings':
      return { settings: m.settings };
    case 'voice.session':
      if (m.state === 'listening') {
        const opening = !s.voice.active;
        return {
          view: 'console',
          voice: { ...s.voice, active: true, text: opening ? m.text : (m.text || s.voice.text),
            startedAt: opening ? Date.now() : s.voice.startedAt, windowS: m.window_s ?? s.voice.windowS },
        };
      }
      if (m.state === 'final') {
        return {
          voice: { ...s.voice, active: false, text: '', level: 0, levels: [] },
          voiceCommand: { id: (s.voiceCommand?.id ?? 0) + 1, text: m.text },
          events: log(s, 'ok', 'spoken request received'),
        };
      }
      return { voice: { ...s.voice, active: false, text: '', level: 0, levels: [] } };
    case 'voice.level':
      return s.voice.active ? { voice: { ...s.voice, level: m.level, levels: [...s.voice.levels.slice(-39), m.level] } } : {};
    case 'voice.training': {
      const { type: _t, ...t } = m;
      return { training: t.state === 'cancelled' ? null : t,
        events: t.state === 'done' ? log(s, 'ok', `voice training complete · ${Math.round((t.result?.after ?? 0) * 100)}% understood`) : s.events };
    }
    case 'selfheal':
      return { selfheal: { faults: m.faults, repairs: m.repairs } };
    case 'wake.calibration':
      return { calibration: { state: m.state, items: m.items } };
    case 'integrations': {
      const { type: _t, ...rest } = m;
      const before = s.integrations?.mcp ?? [];
      let events = s.events;
      for (const srv of rest.mcp) {
        const prev = before.find((x) => x.name === srv.name);
        if (prev?.state === srv.state) continue;
        events = log({ ...s, events }, srv.state === 'ready' ? 'ok' : srv.state === 'error' ? 'error' : 'info',
          srv.state === 'ready' ? `mcp ${srv.name} ready · ${srv.tools} tools` : `mcp ${srv.name} ${srv.state}`);
      }
      return { integrations: rest, events };
    }
    case 'audit.page':
      return {
        audit: { items: m.append ? [...s.audit.items, ...m.items] : m.items, hasMore: m.has_more, loading: false, loaded: true },
      };
    case 'ui.focus': {
      const spoken = (m.text ?? '').trim();
      return {
        view: 'console', focusTick: s.focusTick + 1,
        pulse: m.reason === 'wake' ? s.pulse + 1 : s.pulse, // the Core answers the wake word with a shockwave
        voiceCommand: spoken ? { id: (s.voiceCommand?.id ?? 0) + 1, text: spoken } : s.voiceCommand,
        events: log(s, m.reason === 'wake' ? 'ok' : 'info',
          m.reason === 'wake' ? (spoken ? 'wake word heard · command received' : 'wake word heard · "Jarvis"') : 'summoned by hotkey'),
      };
    }
    case 'notice':
      return {
        transcript: [...s.transcript, { kind: 'notice', id: nid(), level: m.level, text: m.message }],
        events: log(s, m.level === 'warn' ? 'warn' : 'info', m.message.toLowerCase().slice(0, 60)),
      };
    case 'error': {
      const t = s.transcript.map((i) => (i.kind === 'assistant' && i.streaming ? { ...i, streaming: false } : i))
        .filter((i) => !(i.kind === 'assistant' && !i.text.trim()));
      return {
        transcript: [...t, { kind: 'notice', id: nid(), level: 'error', text: m.message }],
        events: log(s, 'error', `fault · ${m.code}`),
      };
    }
    default:
      return {};
  }
}

export const initialState = {
  conn: 'connecting' as Conn, attempt: 0, version: '', hasWindow: false, canBackground: false,
  hotkeys: { summon: 'Ctrl+Alt+J', kill: 'Ctrl+Alt+Backspace' },
  view: 'console' as View, agent: 'idle' as AgentState, agentDetail: '', busy: false, settings: null, providers: [],
  transcript: [], activity: [], approvals: [], telemetry: null, runs: null,
  series: { cpu: [], ram: [], gpu: [], down: [], up: [] },
  audit: { items: [], hasMore: false, loading: false, loaded: false }, focusTick: 0, lastSpoken: null, pulse: 0,
  events: [] as StreamEvent[],
  stats: { startedAt: Date.now(), runs: 0, actions: 0, approvals: 0, tokensIn: 0, tokensOut: 0 },
  route: null,
  integrations: null,
  voiceCommand: null,
  calibration: { state: 'idle' as const, items: [] as CalibrationItem[] },
  selfheal: { faults: [] as Fault[], repairs: [] as Recovery[] },
  training: null,
  voice: { active: false, text: '', level: 0, levels: [] as number[], startedAt: 0, windowS: 10 },
};

export const useHud = create<HudState>((set) => ({
  ...initialState,
  setView: (view) => set({ view }),
  setConn: (conn, attempt = 0) => set((s) => ({
    conn, attempt,
    events: conn === s.conn ? s.events
      : conn === 'offline' ? log(s, 'error', 'core link lost · reconnecting') : s.events,
  })),
  setAuditLoading: () => set((s) => ({ audit: { ...s.audit, loading: true } })),
  apply: (m) => set((s) => reduce(s, m)),
}));
