import { useEffect } from 'react';
import { ClipboardList, ListChecks, Minus, Settings as Cog, Square, TerminalSquare, WifiOff, Wrench, X } from 'lucide-react';
import { AuditView } from './Audit';
import { Console } from './Console';
import { Backdrop, Scramble } from './Hud';
import { Onboarding } from './Onboarding';
import type { AgentState } from './protocol';
import { RepairView } from './Repair';
import { RunsView } from './Runs';
import { SettingsView } from './Settings';
import { send } from './socket';
import { useHud, type View } from './store';

const STATE_TEXT: Record<AgentState, string> = {
  idle: 'Idle', thinking: 'Thinking', acting: 'Executing', awaiting_approval: 'Needs approval', error: 'Fault',
};
const PRIVACY_TEXT = { local: 'Local only', hybrid: 'Hybrid', cloud: 'Cloud' } as const;

const NAV: { id: View; label: string; icon: typeof Cog; key: string }[] = [
  { id: 'console', label: 'Console', icon: TerminalSquare, key: '1' },
  { id: 'audit', label: 'Audit', icon: ClipboardList, key: '2' },
  { id: 'repair', label: 'Repair', icon: Wrench, key: '3' },
  { id: 'settings', label: 'Settings', icon: Cog, key: '4' },
  { id: 'runs', label: 'Runs', icon: ListChecks, key: '5' },
];

function TitleBar() {
  const agent = useHud((s) => s.agent);
  const conn = useHud((s) => s.conn);
  const privacy = useHud((s) => s.settings?.privacy_mode);
  const name = useHud((s) => s.settings?.assistant_name) || 'J.A.R.V.I.S';
  const hasWindow = useHud((s) => s.hasWindow);
  const killKey = useHud((s) => s.hotkeys.kill);
  const setView = useHud((s) => s.setView);
  const wake = useHud((s) => s.integrations?.wake);
  const online = conn === 'online';
  useEffect(() => { document.title = name.replace(/\./g, ''); }, [name]);
  return (
    <header className="titlebar">
      <span className="wordmark pywebview-drag-region" aria-label={name}><i aria-hidden="true" /><Scramble text={name} /></span>
      <span className="titlebar__tag pywebview-drag-region">desktop intelligence</span>
      <div className="chips" role="status">
        <span className={`chip-s chip-s--${online ? 'ok' : 'bad'}`}><i aria-hidden="true" />{online ? 'Core online' : 'Core offline'}</span>
        <span className={`chip-s chip-s--${online ? agent : 'bad'}`}><i aria-hidden="true" />{online ? STATE_TEXT[agent] : 'No link'}</span>
        {privacy && <button className="chip-s chip-s--btn" onClick={() => setView('settings')} title="Privacy mode — change in Settings">{PRIVACY_TEXT[privacy]}</button>}
        {wake && wake.state !== 'off' && (
          <button className={`chip-s chip-s--btn chip-s--${wake.state === 'error' || wake.silent ? 'bad' : wake.state === 'listening' ? 'wake' : 'quiet'}`} onClick={() => setView('settings')}
            title={wake.silent ? 'The microphone is sending silence — it may be muted' : wake.state === 'error' ? wake.error : `Listening for "${name.replace(/\./g, '')}" on ${wake.device || 'the microphone'} — audio stays on this machine`}>
            <i aria-hidden="true" />{wake.silent ? 'Mic silent' : wake.state === 'listening' ? `Say "${name.replace(/\./g, '')}"` : wake.state === 'error' ? 'Mic error' : 'Wake starting'}
          </button>
        )}
        <span className="chip-s chip-s--quiet" title="Kill switch: cancels every run and denies pending approvals">Stop · {killKey}</span>
      </div>
      <span className="titlebar__spacer pywebview-drag-region" />
      {hasWindow && (
        <div className="winctl">
          <button onClick={() => send({ type: 'window.control', action: 'minimize' })} aria-label="Minimize"><Minus size={14} aria-hidden="true" /></button>
          <button onClick={() => send({ type: 'window.control', action: 'toggle_maximize' })} aria-label="Maximize or restore"><Square size={12} aria-hidden="true" /></button>
          <button className="winctl__close" onClick={() => send({ type: 'window.control', action: 'close' })} aria-label="Close"><X size={14} aria-hidden="true" /></button>
        </div>
      )}
    </header>
  );
}

function Rail() {
  const view = useHud((s) => s.view);
  const setView = useHud((s) => s.setView);
  const pending = useHud((s) => s.approvals.length);
  const open = useHud((s) => s.selfheal.faults.filter((f) => f.status === 'open' || f.status === 'proposed').length);
  return (
    <nav className="rail" aria-label="Primary">
      {NAV.map(({ id, label, icon: Icon, key }) => (
        <button key={id} className={`rail__item${view === id ? ' is-active' : ''}`} aria-current={view === id ? 'page' : undefined} onClick={() => setView(id)} title={`${label} (Ctrl+${key})`}>
          <Icon size={18} aria-hidden="true" />
          <span>{label}</span>
          {id === 'repair' && open > 0 && view !== 'repair' && <b className="rail__badge" aria-label={`${open} faults`}>{open}</b>}
          {id === 'console' && pending > 0 && view !== 'console' && <b className="rail__badge" aria-label={`${pending} approvals waiting`}>{pending}</b>}
        </button>
      ))}
    </nav>
  );
}

function ConnectionBanner() {
  const conn = useHud((s) => s.conn);
  const attempt = useHud((s) => s.attempt);
  if (conn === 'online') return null;
  const text = conn === 'connecting' ? 'Connecting to core…'
    : conn === 'rejected' ? 'This window is not authorised for the running core. Close it and start the app again.'
    : `Core offline — reconnecting (attempt ${attempt})`;
  return <div className={`banner banner--${conn}`} role="alert"><WifiOff size={14} aria-hidden="true" />{text}</div>;
}

/** Speak finished replies with the system voices when enabled (FR-V3). */
function useSpeech() {
  const spoken = useHud((s) => s.lastSpoken);
  const enabled = useHud((s) => s.settings?.voice.speak ?? false);
  const chosen = useHud((s) => s.settings?.voice.tts_voice || s.integrations?.default_voice || '');
  useEffect(() => {
    if (!enabled || !spoken || typeof window.speechSynthesis === 'undefined') return;
    window.speechSynthesis.cancel();
    const plain = spoken.text.replace(/[*`#]/g, '').slice(0, 1200);
    const utterance = new SpeechSynthesisUtterance(plain);
    // Use the same voice as the wake reply. Browser voice names carry the person's name ("Microsoft George …").
    const person = /Microsoft (\w+)/.exec(chosen)?.[1];
    const match = person ? window.speechSynthesis.getVoices().find((v) => v.name.includes(person)) : undefined;
    if (match) utterance.voice = match;
    window.speechSynthesis.speak(utterance);
  }, [enabled, spoken, chosen]);
}

export function App() {
  const view = useHud((s) => s.view);
  const setView = useHud((s) => s.setView);
  const agent = useHud((s) => s.agent);
  const theme = useHud((s) => s.settings?.theme);
  const onboarded = useHud((s) => s.settings?.onboarding_complete);
  const settingsLoaded = useHud((s) => s.settings !== null);
  useSpeech();
  useEffect(() => {
    const el = document.documentElement;
    if (theme && theme !== 'ion') el.setAttribute('data-theme', theme);
    else el.removeAttribute('data-theme');
  }, [theme]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!e.ctrlKey || e.altKey) return;
      const nav = NAV.find((n) => n.key === e.key);
      if (nav) { e.preventDefault(); setView(nav.id); }
      else if (e.key === '.') { e.preventDefault(); send({ type: 'run.cancel' }); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [setView]);
  useEffect(() => { if (view !== 'console') document.getElementById('view-root')?.focus({ preventScroll: true }); }, [view]);
  if (settingsLoaded && !onboarded) return <Onboarding />;

  return (
    <div className={`app app--${agent}`}>
      <Backdrop />
      <div className="frame" aria-hidden="true"><i /><i /><i /><i /></div>
      <a className="skip" href="#view-root">Skip to content</a>
      <TitleBar />
      <ConnectionBanner />
      <div className="body">
        <Rail />
        <div id="view-root" className="view-root" tabIndex={-1}>
          {view === 'console' ? <Console /> : view === 'audit' ? <AuditView /> : view === 'repair' ? <RepairView /> : view === 'runs' ? <RunsView /> : <SettingsView />}
        </div>
      </div>
    </div>
  );
}
