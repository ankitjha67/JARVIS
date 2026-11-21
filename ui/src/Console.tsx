import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import {
  AlertTriangle, Ban, Check, ChevronRight, CircleSlash, CornerDownLeft, Loader2, Plus, ShieldAlert, Square, X,
} from 'lucide-react';
import { Core } from './Core';
import { classifyInput } from './dictation';
import { Block, ClockBlock, EventStream, LoadChart, Markdown, NetWave, Num, Radar, VoiceOverlay } from './Hud';
import type { ApprovalRequest, Tier } from './protocol';
import { send } from './socket';
import { useHud, type ToolRow } from './store';

const TIER_LABEL: Record<Tier, string> = { 0: 'Observe', 1: 'Reversible', 2: 'Consequential', 3: 'Destructive' };

export function TierBadge({ tier }: { tier: Tier }) {
  return <span className={`tier tier--${tier}`} title={TIER_LABEL[tier]}>T{tier}<span className="sr-only"> {TIER_LABEL[tier]}</span></span>;
}

// ---- Vitals -------------------------------------------------------------------------------------

function Meter({ label, percent, value, unavailable, warn }: {
  label: string; percent: number | null; value: string; unavailable?: string; warn?: boolean;
}) {
  const hot = warn ?? (percent !== null && percent >= 85);
  return (
    <div className={`meter${hot ? ' meter--hot' : ''}`}>
      <span className="meter__label">{label}</span>
      <span className="meter__value">{unavailable ?? value}{hot && <span className="sr-only"> (needs attention)</span>}</span>
      <div className="meter__bar" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(percent ?? 0)}>
        <div className="meter__fill" style={{ transform: `scaleX(${unavailable ? 0 : Math.min(percent ?? 0, 100) / 100})` }} />
      </div>
    </div>
  );
}

const rate = (kbps: number) => (kbps >= 1000 ? `${(kbps / 1000).toFixed(1)} Mb/s` : `${Math.round(kbps)} kb/s`);

function Vitals() {
  const t = useHud((s) => s.telemetry);
  return (
    <Block title="System vitals" delay={180}>
      {!t ? (
        <div className="skeleton-stack" aria-label="Waiting for sensors"><i /><i /><i /><i /></div>
      ) : (
        <div className="meters">
          <Meter label="CPU" percent={t.cpu_percent} value={`${Math.round(t.cpu_percent)}%`} />
          <Meter label="Memory" percent={t.ram_percent} value={`${t.ram_used_gb.toFixed(1)}/${t.ram_total_gb.toFixed(0)} GB`} />
          <Meter label="GPU" percent={t.gpu?.percent ?? null} value={t.gpu ? `${Math.round(t.gpu.percent)}%${t.gpu.temp_c !== null ? ` ${t.gpu.temp_c}°C` : ''}` : ''} unavailable={t.gpu ? undefined : 'No sensor'} />
          {t.gpu && <Meter label="VRAM" percent={(t.gpu.vram_used_gb / t.gpu.vram_total_gb) * 100} value={`${t.gpu.vram_used_gb.toFixed(1)}/${t.gpu.vram_total_gb.toFixed(0)} GB`} />}
          {t.disks.map((d) => <Meter key={d.mount} label={`Disk ${d.mount}`} percent={d.percent} value={`${d.free_gb.toFixed(d.free_gb < 10 ? 1 : 0)} GB free`} />)}
          {t.battery && <Meter label="Battery" percent={t.battery.percent} warn={t.battery.percent <= 20 && !t.battery.plugged} value={`${Math.round(t.battery.percent)}% ${t.battery.plugged ? 'AC' : 'DC'}`} />}
        </div>
      )}
    </Block>
  );
}

// ---- Link card: the model in use and what this session has done -----------------------------------

function LinkCard({ compact }: { compact: boolean }) {
  const providers = useHud((s) => s.providers);
  const routing = useHud((s) => s.settings?.routing);
  const stats = useHud((s) => s.stats);
  const route = useHud((s) => s.route);
  const setView = useHud((s) => s.setView);
  const active = providers.find((p) => p.state === 'ok' && (routing?.mode !== 'manual' || p.id === routing.manual_provider));
  const testing = providers.some((p) => p.state === 'testing');
  const short = (m: string | null | undefined) => (m ? m.split('/').pop() : '—');
  if (!active) {
    return (
      <div className={`link link--empty${compact ? ' link--compact' : ''}`}>
        {providers.length === 0 ? <span>Checking model providers…</span>
          : testing ? <span role="status">Testing which models handle tools and how fast they are…</span>
          : <><span>No model provider is ready.</span><button className="btn btn--ghost" onClick={() => setView('settings')}>Open Settings</button></>}
      </div>
    );
  }
  return (
    <div className={`link${compact ? ' link--compact' : ''}`}>
      <div className="link__model">
        <span className="label">{active.label}{active.auto ? ' · auto-selected' : ''}</span>
        <strong title={active.model ?? ''}>{short(route?.model ?? active.model)}</strong>
        {!compact && active.fast_model && active.fast_model !== active.model && <span className="link__sub">fast path · {short(active.fast_model)}</span>}
      </div>
      <dl className="link__stats">
        <div><dt>Link</dt><dd>{active.latency_ms !== null ? <><Num value={active.latency_ms} /> ms</> : '—'}</dd></div>
        <div><dt>Requests</dt><dd><Num value={stats.runs} /></dd></div>
        <div><dt>Actions</dt><dd><Num value={stats.actions} /></dd></div>
        <div><dt>Tokens</dt><dd><Num value={stats.tokensIn + stats.tokensOut} /></dd></div>
      </dl>
    </div>
  );
}

// ---- Transcript ---------------------------------------------------------------------------------

const EXAMPLES = ['What is using the most memory right now?', 'Open Notepad', 'Set the volume to 30 percent'];

function Transcript({ onExample }: { onExample: (text: string) => void }) {
  const items = useHud((s) => s.transcript);
  const hotkey = useHud((s) => s.hotkeys.summon);
  const assistantName = useHud((s) => s.settings?.assistant_name)?.replace(/\./g, '') || 'Jarvis';
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [items]);
  return (
    <div className="transcript" ref={ref} role="log" aria-label="Conversation" tabIndex={0}
      onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40; }}>
      {items.length === 0 ? (
        <div className="empty empty--center">
          <p>Press <kbd>{hotkey}</kbd> anywhere, then speak with Wispr Flow or type.</p>
          <div className="examples">
            {EXAMPLES.map((e) => <button key={e} className="chip" onClick={() => onExample(e)}>{e}</button>)}
          </div>
        </div>
      ) : items.map((it) => {
        if (it.kind === 'user') return <div key={it.id} className="msg msg--user"><span className="label">You</span><p>{it.text}</p></div>;
        if (it.kind === 'assistant') return (
          <div key={it.id} className="msg msg--assistant">
            <span className="label">{assistantName}{it.model && <span className="msg__model"> · {it.model.split('/').pop()}</span>}</span>
            <Markdown text={it.text} />
            {it.streaming && <span className="caret" aria-hidden="true" />}
          </div>
        );
        return (
          <div key={it.id} className={`note note--${it.level}`} role={it.level === 'error' ? 'alert' : undefined}>
            {it.level === 'info' ? <Check size={14} aria-hidden="true" /> : <AlertTriangle size={14} aria-hidden="true" />}
            <span>{it.text}</span>
          </div>
        );
      })}
    </div>
  );
}

// ---- Command bar --------------------------------------------------------------------------------

function CommandBar({ draft, setDraft, onListening }: { draft: string; setDraft: (v: string) => void; onListening: (v: boolean) => void }) {
  const conn = useHud((s) => s.conn);
  const busy = useHud((s) => s.busy);
  const focusTick = useHud((s) => s.focusTick);
  const voice = useHud((s) => s.settings?.voice);
  const ready = useHud((s) => s.providers.some((p) => p.state === 'ok'));
  const ref = useRef<HTMLTextAreaElement>(null);
  const lastSent = useRef('');
  const armTimer = useRef<number | undefined>(undefined);
  const [armed, setArmed] = useState(false);
  const online = conn === 'online';

  const disarm = useCallback(() => { window.clearTimeout(armTimer.current); setArmed(false); }, []);

  const submit = useCallback((text: string) => {
    const clean = text.trim();
    disarm();
    if (!clean || !online || busy) return;
    window.speechSynthesis?.cancel(); // barge-in (FR-V4)
    if (send({ type: 'user.message', text: clean })) { lastSent.current = clean; setDraft(''); }
  }, [busy, disarm, online, setDraft]);

  useEffect(() => { if (online) ref.current?.focus(); }, [focusTick, online]); // disabled inputs cannot take focus
  // A command spoken after the wake word ("Jarvis, open Chrome") is sent straight away; if Jarvis is busy
  // or offline it is left in the bar for the user instead of being lost.
  const voiceCommand = useHud((s) => s.voiceCommand);
  const handledVoice = useRef(0);
  useEffect(() => {
    if (!voiceCommand || voiceCommand.id === handledVoice.current) return;
    handledVoice.current = voiceCommand.id;
    if (online && !busy) submit(voiceCommand.text); else setDraft(voiceCommand.text);
  }, [voiceCommand, online, busy, submit, setDraft]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.ctrlKey && e.key.toLowerCase() === 'l') { e.preventDefault(); ref.current?.focus(); } };
    window.addEventListener('keydown', onKey);
    return () => { window.removeEventListener('keydown', onKey); window.clearTimeout(armTimer.current); };
  }, []);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el) { el.style.height = 'auto'; el.style.height = `${Math.min(el.scrollHeight, 132)}px`; }
  }, [draft]);

  const reason = !online ? 'Core offline — reconnecting' : busy ? 'Working… Ctrl+. to cancel' : !ready ? 'No model ready — open Settings' : null;

  return (
    <div className={`command${armed ? ' command--armed' : ''}${busy ? ' command--busy' : ''}`}>
      <ChevronRight size={18} aria-hidden="true" className="command__prompt" />
      <label className="sr-only" htmlFor="command-input">Command</label>
      <textarea id="command-input" ref={ref} rows={1} value={draft} disabled={!online}
        placeholder={reason ?? 'Speak or type a command…'} autoComplete="off" spellCheck={false}
        onFocus={() => onListening(true)} onBlur={() => { onListening(false); disarm(); }}
        onChange={(e) => {
          const next = e.target.value;
          const verdict = classifyInput(draft.length, next.length, (e.nativeEvent as InputEvent).inputType);
          setDraft(next);
          if (!voice?.auto_send || busy) return;
          if (verdict === 'arm') {
            window.clearTimeout(armTimer.current);
            setArmed(true);
            armTimer.current = window.setTimeout(() => submit(next), voice.quiet_ms);
          } else if (verdict === 'disarm') disarm();
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(draft); }
          else if (e.key === 'Escape') { if (armed) disarm(); else setDraft(''); }
          else if (e.key === 'ArrowUp' && !draft && lastSent.current) { e.preventDefault(); setDraft(lastSent.current); }
        }} />
      {armed && <span className="command__armed" role="status">Sending… <kbd>Esc</kbd> to hold</span>}
      {busy ? (
        <button className="btn btn--icon btn--danger" onClick={() => send({ type: 'run.cancel' })} aria-label="Cancel current run" title="Cancel (Ctrl+.)">
          <Square size={16} aria-hidden="true" />
        </button>
      ) : (
        <button className="btn btn--icon btn--primary" disabled={!online || !draft.trim()} onClick={() => submit(draft)} aria-label="Send command" title="Send (Enter)">
          <CornerDownLeft size={16} aria-hidden="true" />
        </button>
      )}
    </div>
  );
}

// ---- Activity & approvals -----------------------------------------------------------------------

function ApprovalCard({ a }: { a: ApprovalRequest }) {
  const denyRef = useRef<HTMLButtonElement>(null);
  const [left, setLeft] = useState(a.timeout_s);
  const [sent, setSent] = useState(false);
  useEffect(() => { denyRef.current?.focus(); }, []); // focus the safe default
  useEffect(() => {
    const started = Date.now();
    const id = window.setInterval(() => setLeft(Math.max(0, a.timeout_s - Math.floor((Date.now() - started) / 1000))), 1000);
    return () => window.clearInterval(id);
  }, [a.timeout_s]);
  const respond = (decision: 'allow' | 'deny', remember = false) => {
    if (send({ type: 'approval.respond', approval_id: a.approval_id, decision, remember })) setSent(true);
  };
  const destructive = a.tier === 3;
  return (
    <section className={`approval approval--t${a.tier}`} role="alertdialog" aria-labelledby={`ap-${a.approval_id}`} aria-describedby={`apd-${a.approval_id}`}
      onKeyDown={(e) => {
        if (sent) return;
        const k = e.key.toLowerCase();
        if (k === 'escape' || k === 'd') { e.preventDefault(); respond('deny'); }
        else if (k === 'a' && !destructive) { e.preventDefault(); respond('allow'); } // T3 needs an explicit click
        else if (k === 's' && a.can_remember) { e.preventDefault(); respond('allow', true); }
      }}>
      <header className="approval__head">
        <ShieldAlert size={16} aria-hidden="true" />
        <h3 id={`ap-${a.approval_id}`}>{destructive ? 'Destructive action' : 'Approval needed'}</h3>
        <TierBadge tier={a.tier} />
        <span className="approval__timer" aria-label={`${left} seconds left`}>{left}s</span>
      </header>
      <div className="approval__clock" aria-hidden="true"><i style={{ transform: `scaleX(${left / a.timeout_s})` }} /></div>
      <p className="approval__summary">{a.summary}</p>
      <p className="approval__reason" id={`apd-${a.approval_id}`}>{a.reason}</p>
      <pre className="approval__args" tabIndex={0} aria-label="Exact arguments">{JSON.stringify(a.args, null, 2)}</pre>
      <div className="approval__actions">
        <button ref={denyRef} className="btn" disabled={sent} onClick={() => respond('deny')}>Deny <kbd>D</kbd></button>
        {a.can_remember && <button className="btn" disabled={sent} onClick={() => respond('allow', true)}>Allow for session <kbd>S</kbd></button>}
        <button className={`btn ${destructive ? 'btn--danger' : 'btn--attention'}`} disabled={sent} onClick={() => respond('allow')}>
          {destructive ? 'Allow once' : <>Allow <kbd>A</kbd></>}
        </button>
      </div>
    </section>
  );
}

function StatusIcon({ status }: { status: ToolRow['status'] }) {
  switch (status) {
    case 'running': return <Loader2 size={14} className="spin" aria-label="Running" />;
    case 'waiting': return <ShieldAlert size={14} className="amber" aria-label="Waiting for approval" />;
    case 'ok': return <Check size={14} className="ok" aria-label="Succeeded" />;
    case 'denied': return <Ban size={14} className="amber" aria-label="Denied" />;
    case 'refused': return <CircleSlash size={14} className="danger" aria-label="Refused by policy" />;
    case 'cancelled': return <X size={14} className="muted" aria-label="Cancelled" />;
    default: return <AlertTriangle size={14} className="danger" aria-label="Failed" />;
  }
}

/** Pending approvals sit at the top of the column so the decision buttons can never be clipped. */
function Approvals() {
  const approvals = useHud((s) => s.approvals);
  if (approvals.length === 0) return null;
  return <div className="approvals">{approvals.map((a) => <ApprovalCard key={a.approval_id} a={a} />)}</div>;
}

function Activity() {
  const rows = useHud((s) => s.activity);
  const [open, setOpen] = useState<string | null>(null);
  return (
    <Block title="Actions" className="activity block--grow" delay={420} aside={rows.length > 0 ? `${rows.length}` : undefined}>
      {rows.length === 0 ? (
        <p className="muted">No actions yet this session.</p>
      ) : (
        <ol className="rows">
          {rows.slice().reverse().map((r) => (
            <li key={r.callId} className={`row row--${r.status}`}>
              <button className="row__main" aria-expanded={open === r.callId} onClick={() => setOpen(open === r.callId ? null : r.callId)}>
                <StatusIcon status={r.status} />
                <span className="row__summary">{r.summary}</span>
                <TierBadge tier={r.tier} />
                <span className="row__time">{r.durationMs !== null && r.status === 'ok' ? `${r.durationMs} ms` : ''}</span>
              </button>
              {r.error && <p className="row__error">{r.error}</p>}
              {open === r.callId && <pre className="row__args">{r.tool} {JSON.stringify(r.args, null, 2)}</pre>}
            </li>
          ))}
        </ol>
      )}
    </Block>
  );
}

// ---- Model bench: the cloud models Jarvis tested, fastest first --------------------------------------

function ModelBench() {
  const provider = useHud((s) => s.providers.find((p) => p.state === 'ok' && p.auto && p.bench.length > 0));
  if (!provider) return null;
  const passed = provider.bench.filter((b) => b.tool_ok).slice(0, 5);
  const slowest = Math.max(1, ...passed.map((b) => b.latency_ms));
  return (
    <Block title="Model bench" delay={380} aside={`${provider.bench.filter((b) => b.tool_ok).length}/${provider.bench.length} pass`}>
      <ol className="mbench">
        {passed.map((b) => {
          const role = b.model === provider.model ? 'smart' : b.model === provider.fast_model ? 'fast' : null;
          return (
            <li key={b.model} className={role ? 'is-picked' : ''}>
              <span className="mbench__name" title={b.model}>{b.model.split('/').pop()}</span>
              <span className="mbench__role">{role ?? ''}</span>
              <span className="mbench__ms">{(b.latency_ms / 1000).toFixed(1)}s</span>
              <i style={{ transform: `scaleX(${b.latency_ms / slowest})` }} aria-hidden="true" />
            </li>
          );
        })}
      </ol>
    </Block>
  );
}

// ---- Console ------------------------------------------------------------------------------------

export function Console() {
  const [draft, setDraft] = useState('');
  const [listening, setListening] = useState(false);
  const hasHistory = useHud((s) => s.transcript.length > 0);
  const busy = useHud((s) => s.busy);
  const net = useHud((s) => s.telemetry);
  const hearing = useHud((s) => s.voice.active);
  return (
    <div className="console">
      <aside className="col col--left" aria-label="System">
        <Vitals />
        <Block title="Load · 60 s" delay={240}><LoadChart /></Block>
        <Block title="Event stream" className="block--grow" delay={300}><EventStream /></Block>
      </aside>

      <main className={`stage${hasHistory ? ' stage--talking' : ''}${hearing ? ' stage--hearing' : ''}`} aria-label="Console">
        <div className="stage__top">
          <Core listening={listening} compact={hasHistory} />
          {hearing ? <VoiceOverlay /> : <LinkCard compact={hasHistory} />}
          <button className="btn btn--ghost stage__new" disabled={!hasHistory || busy} onClick={() => send({ type: 'conversation.new' })}>
            <Plus size={14} aria-hidden="true" /> New
          </button>
        </div>
        <Transcript onExample={(t) => { setDraft(t); document.getElementById('command-input')?.focus(); }} />
        <CommandBar draft={draft} setDraft={setDraft} onListening={setListening} />
      </main>

      <aside className="col col--right" aria-label="Activity">
        <Approvals />
        <ClockBlock />
        <Block title="Process radar" delay={240}><Radar /></Block>
        <Block title="Net link" delay={330} aside={net ? `↓ ${rate(net.net_down_kbps)}  ↑ ${rate(net.net_up_kbps)}` : undefined}><NetWave /></Block>
        <ModelBench />
        <Activity />
      </aside>
    </div>
  );
}
