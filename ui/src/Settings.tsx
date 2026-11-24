import { useState, useMemo } from 'react';
import { ArrowDown, ArrowUp, Check, ChevronDown, ChevronRight, FlaskConical, KeyRound, RefreshCw, Search, Trash2, X } from 'lucide-react';
import { AutonomySection, EmailSection, McpSection } from './Capabilities';
import { KeysSection } from './Keys';
import { TrainingSection } from './Training';
import type { PrivacyMode, ProviderStatus, Settings as SettingsT, ThemeId, ToolInfo } from './protocol';
import { send } from './socket';
import { useHud } from './store';
import { IS_MAC, KEY_STORE, OS_NAME } from './text';

const patch = (p: Partial<SettingsT> | Record<string, unknown>) => send({ type: 'settings.set', patch: p });

type Tab = 'general' | 'models' | 'keys' | 'voice' | 'connections' | 'tools';
const TABS: { id: Tab; label: string; help: string }[] = [
  { id: 'general', label: 'General', help: 'Privacy, autonomy, shortcuts' },
  { id: 'models', label: 'Models', help: 'Routing and providers' },
  { id: 'keys', label: 'API keys', help: 'Every key, by category' },
  { id: 'voice', label: 'Voice', help: 'Wake word, training, speech engines' },
  { id: 'connections', label: 'Connections', help: 'Email and connectors' },
  { id: 'tools', label: 'Tools', help: 'All capabilities at a glance' },
];
const PROVIDER_GROUPS: { title: string; help: string; match: (p: ProviderStatus) => boolean }[] = [
  { title: 'On this computer', help: 'Private; nothing leaves the machine.', match: (p) => p.local },
  { title: 'Frontier labs', help: 'The makers of the most capable models.', match: (p) => ['anthropic', 'openai', 'gemini'].includes(p.id) },
  { title: 'Fast inference and aggregators', help: 'Many open models behind one key.', match: (p) => ['nvidia', 'groq', 'openrouter'].includes(p.id) },
  { title: 'Custom endpoints', help: 'Added by you under API keys.', match: () => true },
];
function groupProviders(providers: ProviderStatus[]) {
  const left = [...providers];
  return PROVIDER_GROUPS.map((g) => {
    const items = left.filter(g.match);
    items.forEach((p) => left.splice(left.indexOf(p), 1));
    return { ...g, items };
  }).filter((g) => g.items.length > 0);
}
function initialTab(): Tab {
  try { const t = window.localStorage.getItem('jarvis.settings.tab'); if (TABS.some((x) => x.id === t)) return t as Tab; } catch { /* storage unavailable */ }
  return 'general';
}

const PRIVACY: { id: PrivacyMode; name: string; help: string }[] = [
  { id: 'local', name: 'Local only', help: 'Nothing leaves this machine. Cloud providers are refused by the core.' },
  { id: 'hybrid', name: 'Hybrid', help: 'Local and cloud providers, tried in your preferred order.' },
  { id: 'cloud', name: 'Cloud', help: 'Cloud providers only. Best quality for multi-step tasks.' },
];
const THEMES: { id: ThemeId; name: string; help: string }[] = [
  { id: 'ion', name: 'Ion', help: 'Electric azure on deep navy. The original Jarvis look.' },
  { id: 'holographic', name: 'Holographic OS', help: 'Cyan and cobalt glassmorphism. Tactical cybernetic HUD.' },
  { id: 'sovereign', name: 'Sovereign Executive', help: 'Champagne gold on obsidian. Luxury editorial aesthetic.' },
];

const STATE_TEXT: Record<ProviderStatus['state'], string> = {
  ok: 'Ready', no_key: 'Needs an API key', unreachable: 'Not reachable', error: 'Error', no_model: 'Choose a model',
  blocked: 'Blocked by privacy mode', testing: 'Testing models…', unknown: 'Checking…',
};

function ProviderCard({ p, chosen }: { p: ProviderStatus; chosen: string | null | undefined }) {
  const [key, setKey] = useState('');
  const [saving, setSaving] = useState(false);
  const saveKey = (value: string) => {
    if (send({ type: 'secret.set', provider: p.id, value })) { setSaving(true); setKey(''); window.setTimeout(() => setSaving(false), 1500); }
  };
  return (
    <li className={`pcard pcard--${p.state}`}>
      <div className="pcard__head">
        <h4>{p.label}</h4>
        <span className="tag">{p.local ? 'Local' : 'Cloud'}</span>
        <span className={`pcard__state pcard__state--${p.state}`}>
          {p.state === 'ok' && <Check size={14} aria-hidden="true" />}{STATE_TEXT[p.state]}{p.state === 'ok' && p.latency_ms !== null ? ` · ${p.latency_ms} ms` : ''}
        </span>
      </div>
      {p.detail && p.state !== 'ok' && <p className="pcard__detail">{p.detail}</p>}
      {p.needs_key && (
        <form className="field" onSubmit={(e) => { e.preventDefault(); if (key.trim()) saveKey(key.trim()); }}>
          <label htmlFor={`key-${p.id}`}>API key {p.has_key && <span className="ok">· stored in {KEY_STORE}</span>}</label>
          <div className="field__row">
            <input id={`key-${p.id}`} type="password" autoComplete="off" spellCheck={false} value={key}
              placeholder={p.has_key ? 'Enter a new key to replace the stored one' : 'Paste your key'} onChange={(e) => setKey(e.target.value)} />
            <button className="btn btn--primary" type="submit" disabled={!key.trim() || saving}><KeyRound size={14} aria-hidden="true" /> {saving ? 'Saving…' : 'Save'}</button>
            {p.has_key && (
              <button className="btn" type="button" onClick={() => { if (window.confirm(`Remove the stored ${p.label} key?`)) saveKey(''); }}>
                <Trash2 size={14} aria-hidden="true" /> Remove
              </button>
            )}
          </div>
          <p className="field__help">{p.key_help}. Jarvis never shows a stored key again.</p>
        </form>
      )}
      {p.models.length > 0 && (
        <div className="field">
          <label htmlFor={`model-${p.id}`}>Model</label>
          {p.local ? (
            <select id={`model-${p.id}`} value={chosen ?? p.model ?? ''} onChange={(e) => patch({ models: { [p.id]: e.target.value || null } })}>
              {!(chosen ?? p.model) && <option value="">Select a model…</option>}
              {(p.model && !p.models.includes(p.model) ? [p.model, ...p.models] : p.models).map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          ) : (
            <select id={`model-${p.id}`} value={chosen ?? ''} onChange={(e) => patch({ models: { [p.id]: e.target.value || null } })}>
              <option value="">Automatic — best tested model</option>
              {p.models.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          )}
          {!p.local && !chosen && p.model && (
            <p className="field__help">
              Using <strong>{p.model}</strong> for demanding requests{p.fast_model && p.fast_model !== p.model ? <> and <strong>{p.fast_model}</strong> for short commands</> : ' and short commands'}.
              Chosen from live tests of tool use and speed{p.bench_at ? `, last run ${new Date(p.bench_at * 1000).toLocaleString()}` : ''}; re-tested daily and when the model list changes.
            </p>
          )}
          {!p.local && p.bench.length > 0 && (
            <details className="bench">
              <summary>Test results ({p.bench.filter((b) => b.tool_ok).length} of {p.bench.length} handle tools)</summary>
              <table className="table">
                <thead><tr><th scope="col">Model</th><th scope="col">Tools</th><th scope="col">Response</th></tr></thead>
                <tbody>
                  {p.bench.map((b) => (
                    <tr key={b.model}>
                      <td className="mono">{b.model}{b.model === p.model && <span className="tag bench__tag">smart</span>}{b.model === p.fast_model && <span className="tag bench__tag">fast</span>}</td>
                      <td>{b.tool_ok ? <><Check size={14} className="ok" aria-hidden="true" /> Works</> : <><X size={14} className="muted" aria-hidden="true" /> {b.ok ? 'No tool call' : b.error || 'Failed'}</>}</td>
                      <td className="mono">{b.ok ? `${(b.latency_ms / 1000).toFixed(1)} s` : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <button className="btn btn--ghost" type="button" disabled={p.state === 'testing'} onClick={() => send({ type: 'providers.refresh', retest: true })}>
                <FlaskConical size={14} aria-hidden="true" /> Test models again
              </button>
            </details>
          )}
          {p.id === 'lmstudio' && <p className="field__help">Selecting a model that is not loaded makes LM Studio load it. Models above ~4B parameters will not fit this GPU's 4 GB and run slowly on CPU.</p>}
        </div>
      )}
    </li>
  );
}

function SpeechEngines({ settings }: { settings: SettingsT }) {
  const speech = useHud((s) => s.integrations?.speech);
  const v = settings.voice;
  if (!speech) return null;
  const stt = speech.stt.find((e) => e.id === v.stt_engine);
  const tts = speech.tts.find((e) => e.id === v.tts_engine);
  const option = (e: { id: string; label: string; ready: boolean; selectable: boolean }) => (
    <option key={e.id} value={e.id} disabled={!e.selectable}>{e.label}{e.selectable && !e.ready ? ' — needs a key' : ''}</option>
  );
  return (
    <section className="card" aria-labelledby="s-speech">
      <h2 id="s-speech">Speech engines</h2>
      <p className="field__help">Your name for Jarvis is always recognised on this computer. These choices apply after that: who writes down your request, and whose voice answers. Cloud engines send that audio or text to the service you pick; “Local only” privacy mode turns them off.</p>
      <div className="field field--inline">
        <label htmlFor="stt-engine">Understand my request with</label>
        <select id="stt-engine" value={v.stt_engine} onChange={(e) => patch({ voice: { stt_engine: e.target.value, stt_cloud_model: '' } })}>{speech.stt.map(option)}</select>
      </div>
      {stt && <p className={`field__help${stt.ready ? '' : ' warn'}`}>{stt.ready ? stt.note : `Add a key named “${stt.id}” under Keys first. Until then Jarvis uses the local model.`}{v.wake_keys ? ' Wispr Flow is on above, so it takes requests first.' : ''}</p>}
      <div className="field field--inline">
        <label htmlFor="tts-engine">Answer in</label>
        <div className="field__row">
          <select id="tts-engine" value={v.tts_engine} onChange={(e) => patch({ voice: { tts_engine: e.target.value, tts_cloud_model: '', tts_cloud_voice: '' } })}>{speech.tts.map(option)}</select>
          <button className="btn" type="button" onClick={() => send({ type: 'voice.preview', voice: '' })}>Hear it</button>
        </div>
      </div>
      {tts && <p className={`field__help${tts.ready ? '' : ' warn'}`}>{tts.ready ? tts.note || `Voice: ${v.tts_cloud_voice || tts.voice || 'default'}` : `Add a key named “${tts.id}” under Keys first. Until then Jarvis uses the ${OS_NAME} voice.`}</p>}
      {v.tts_engine !== 'system' && (
        <div className="field field--inline">
          <label htmlFor="tts-cloud-voice">Voice id (optional)</label>
          <input id="tts-cloud-voice" type="text" spellCheck={false} defaultValue={v.tts_cloud_voice} placeholder={tts?.voice || 'default'}
            onBlur={(e) => { if (e.target.value.trim() !== v.tts_cloud_voice) patch({ voice: { tts_cloud_voice: e.target.value.trim() } }); }} />
        </div>
      )}
    </section>
  );
}

function VoicePicker({ settings }: { settings: SettingsT }) {
  const voices = useHud((s) => s.integrations?.voices ?? null);
  const fallback = useHud((s) => s.integrations?.default_voice ?? '');
  const current = settings.voice.tts_voice || fallback;
  if (!voices || voices.length === 0) return <p className="field__help">No English voices are installed in {OS_NAME}.</p>;
  return (
    <div className="field field--inline">
      <label htmlFor="tts-voice">Jarvis's voice</label>
      <div className="field__row">
        <select id="tts-voice" value={current} onChange={(e) => { patch({ voice: { tts_voice: e.target.value } }); send({ type: 'voice.preview', voice: e.target.value }); }}>
          {voices.map((voice) => <option key={voice.name} value={voice.name}>{voice.name.replace('Microsoft ', '')} · {voice.gender || 'voice'}</option>)}
        </select>
        <button className="btn" type="button" onClick={() => send({ type: 'voice.preview', voice: current })}>Hear it</button>
      </div>
    </div>
  );
}

function WakeControls({ settings }: { settings: SettingsT }) {
  const wake = useHud((s) => s.integrations?.wake);
  const live = useHud((s) => s.telemetry?.wake ?? null);
  const calibration = useHud((s) => s.calibration);
  const v = settings.voice;
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const learnable = calibration.items.filter((i) => !i.recognised && /^[a-z']+( [a-z']+){0,2}$/.test(i.alias) && i.alias.length >= 3);
  const uniqueAliases = [...new Set(learnable.map((i) => i.alias))].filter((a) => !v.wake_aliases.includes(a));
  const [keys, setKeys] = useState(v.wake_keys);
  return (
    <>
      <label className="check">
        <input type="checkbox" checked={v.wake_word} onChange={(e) => patch({ voice: { wake_word: e.target.checked } })} />
        <span><strong>Wake when I say “Jarvis”</strong>
          <small>Say “Jarvis” to bring it forward, or “Jarvis, open Chrome” to give the command in one breath. Speech is recognised on this machine only; nothing is recorded or sent anywhere, and anything that does not contain “Jarvis” is discarded at once.</small></span>
      </label>
      {v.wake_word && (
        <>
          <p className={`callout${wake?.state === 'error' ? ' callout--warn' : ''}`} role="status">
            {wake?.state === 'listening' ? `Listening on ${wake.device || 'the default microphone'} · woke ${wake.wakes} time${wake.wakes === 1 ? '' : 's'} · ${wake.speech === 'ready' ? `speech recognition ready, ${wake.utterances} phrases checked` : wake.speech === 'loading' ? 'speech recognition loading (first run downloads about 145 MB)…' : 'speech recognition unavailable — only “Hey Jarvis” will work'}`
              : wake?.state === 'error' ? wake.error
              : 'Starting the microphone…'}
          </p>
          {wake?.silent && (
            <p className="callout callout--warn" role="alert">The microphone is sending silence, so Jarvis cannot hear you. Check the keyboard's microphone-mute key, the input device and level in {OS_NAME} Sound settings, and that {IS_MAC ? 'the app you start Jarvis from is allowed to use the microphone (System Settings → Privacy & Security → Microphone)' : 'desktop apps are allowed to use the microphone (Settings → Privacy → Microphone)'}.</p>
          )}
          {live && (
            <dl className="micmeter">
              <div><dt>Microphone</dt><dd><span className="micmeter__bar" role="meter" aria-label="Microphone level" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(live.level * 100)}><i style={{ transform: `scaleX(${live.level})` }} /></span></dd></div>
              <div><dt>Best recent match</dt><dd className="mono">{Math.round(live.score * 100)}% <span className="muted">(wakes at {Math.round((1 - v.wake_sensitivity) * 100)}%)</span></dd></div>
            </dl>
          )}
          <div className="field field--inline">
            <label htmlFor="wake-sens">Sensitivity</label>
            <div className="field__row">
              <input id="wake-sens" type="range" min={0.1} max={0.85} step={0.05} value={v.wake_sensitivity}
                onChange={(e) => patch({ voice: { wake_sensitivity: Number(e.target.value) } })} aria-describedby="wake-sens-help" />
              <span className="mono">{Math.round(v.wake_sensitivity * 100)}%</span>
            </div>
          </div>
          <p className="field__help" id="wake-sens-help">Applies to the instant “Hey Jarvis” detector. Plain “Jarvis” is recognised from your words and takes about half a second longer.</p>
          <label className="check">
            <input type="checkbox" checked={v.wake_listen && !v.wake_keys} disabled={!!v.wake_keys} onChange={(e) => patch({ voice: { wake_listen: e.target.checked } })} />
            <span><strong>Keep listening for my request</strong>
              <small>{v.wake_keys ? 'Off while a dictation shortcut is set below — that app takes the request instead.'
                : 'After “Jarvis”, the sphere lights up and Jarvis listens until you finish speaking, then carries out what you asked and answers aloud. You can also say it in one breath: “Jarvis, open Chrome”.'}</small></span>
          </label>
          {v.wake_listen && !v.wake_keys && (
            <>
              <div className="field field--inline">
                <label htmlFor="listen-window">Listen for up to</label>
                <div className="field__row">
                  <input id="listen-window" type="range" min={5} max={30} step={1} value={v.listen_window_s}
                    onChange={(e) => patch({ voice: { listen_window_s: Number(e.target.value) } })} aria-describedby="listen-help" />
                  <span className="mono">{v.listen_window_s} s</span>
                </div>
              </div>
              <div className="field field--inline">
                <label htmlFor="listen-pause">Then act</label>
                <select id="listen-pause" value={String(v.listen_pause_s)} onChange={(e) => patch({ voice: { listen_pause_s: Number(e.target.value) } })}>
                  <option value="1.5">as soon as I pause for 1.5 seconds</option>
                  <option value="2.5">as soon as I pause for 2.5 seconds</option>
                  <option value="4">as soon as I pause for 4 seconds</option>
                  <option value="0">only when the full time is up</option>
                </select>
              </div>
              <p className="field__help" id="listen-help">Everything you say in that time is gathered into one request, however many sentences or pauses it has. Choose “only when the full time is up” if you like to think between sentences; choose a pause if you want quick commands to run quickly.</p>
            </>
          )}
          <label className="check">
            <input type="checkbox" checked={v.wake_ack} onChange={(e) => patch({ voice: { wake_ack: e.target.checked } })} />
            <span><strong>Answer with a chime and “{v.wake_reply || 'Yes?'}”</strong><small>So you know Jarvis heard you even when you are not looking at the screen.</small></span>
          </label>

          <VoicePicker settings={settings} />
          {(wake?.near_misses.length ?? 0) > 0 && (
            <div className="calib">
              <strong>Was this you saying “Jarvis”?</strong>
              <p className="field__help">Jarvis half-recognised its name in these phrases but the words did not match. If one is how your “Jarvis” comes out, accept it and Jarvis will answer to it. They are kept in memory only and vanish when Jarvis closes.</p>
              <div className="presets">
                {wake!.near_misses.map((phrase) => {
                  const alias = phrase.toLowerCase().replace(/[^a-z' ]/g, ' ').trim().split(/\s+/).slice(0, 2).join(' ');
                  const usable = /^[a-z']+( [a-z']+)?$/.test(alias) && alias.length >= 3 && !v.wake_aliases.includes(alias);
                  return (
                    <button key={phrase} type="button" className="chip" disabled={!usable} title={usable ? `Accept “${alias}” as your Jarvis` : 'Already accepted or not usable'}
                      onClick={() => patch({ voice: { wake_aliases: [...v.wake_aliases, alias].slice(0, 12) } })}>“{phrase}” → accept “{alias}”</button>
                  );
                })}
              </div>
            </div>
          )}

          <div className="calib">
            <div className="card__head">
              <strong>Teach Jarvis your voice</strong>
              <button className="btn" type="button" disabled={calibration.state === 'listening' || wake?.speech !== 'ready'}
                onClick={() => { setPicked({}); send({ type: 'wake.calibrate' }); }}>
                {calibration.state === 'listening' ? 'Listening…' : 'Start'}
              </button>
            </div>
            <p className="field__help">If Jarvis misses you, press Start and say “Hey Jarvis” or “Jarvis” five times, in your normal voice, pausing a second between each. Jarvis learns the sound of how you say it — so it no longer depends on spelling — and shows what it heard. Only sound fingerprints are kept, never recordings, and they stay on this computer.</p>
            <p className={`field__help${(wake?.voiceprint ?? 0) >= 2 ? ' ok' : ''}`} role="status">
              {(wake?.voiceprint ?? 0) >= 2 ? `Your voice is learned from ${wake!.voiceprint} recordings. Run it again any time to add more, including a soft voice.` : 'Your voice has not been learned yet.'}
            </p>
            {calibration.state === 'listening' && <p className="callout" role="status">Say “Jarvis” now — five times, with a short pause between each. Stops by itself after 30 seconds.</p>}
            {calibration.state === 'unavailable' && <p className="callout callout--warn" role="alert">Speech recognition is not ready yet. Wait for it to finish loading, then try again.</p>}
            {calibration.state === 'done' && (calibration.items.length === 0 ? (
              <p className="callout callout--warn" role="alert">Jarvis did not hear any speech. Watch the microphone bar above while you talk; if it does not move, the microphone is the problem.</p>
            ) : (
              <>
                <ul className="calib__list">
                  {calibration.items.map((it, i) => (
                    <li key={i} className={it.recognised ? 'is-ok' : ''}>
                      <span className="mono">“{it.text}”</span>
                      <span>{it.recognised ? 'recognised as Jarvis' : 'not recognised'}</span>
                    </li>
                  ))}
                </ul>
                {uniqueAliases.length === 0 ? (
                  <p className="field__help ok">{calibration.items.every((i) => i.recognised) ? 'Every attempt was recognised. Nothing to learn.' : 'Nothing new to learn from these attempts.'}</p>
                ) : (
                  <>
                    <p className="field__help">Tick the spellings that were you saying “Jarvis”. Leave out everyday words such as “service”, or Jarvis will wake whenever someone says them.</p>
                    <div className="presets">
                      {uniqueAliases.map((a) => (
                        <label key={a} className="chip calib__pick"><input type="checkbox" checked={!!picked[a]} onChange={(e) => setPicked({ ...picked, [a]: e.target.checked })} /> {a}</label>
                      ))}
                    </div>
                    <button className="btn btn--primary" type="button" disabled={!uniqueAliases.some((a) => picked[a])}
                      onClick={() => patch({ voice: { wake_aliases: [...v.wake_aliases, ...uniqueAliases.filter((a) => picked[a])].slice(0, 12) } })}>Accept ticked spellings</button>
                  </>
                )}
              </>
            ))}
            {v.wake_aliases.length > 0 && (
              <p className="field__help">Also answers to: {v.wake_aliases.map((a) => (
                <button key={a} type="button" className="chip calib__alias" title="Remove" aria-label={`Remove spelling ${a}`}
                  onClick={() => patch({ voice: { wake_aliases: v.wake_aliases.filter((x) => x !== a) } })}>{a} ×</button>
              ))}</p>
            )}
          </div>

          <form className="field field--inline" onSubmit={(e) => { e.preventDefault(); patch({ voice: { wake_keys: keys.trim() } }); }}>
            <label htmlFor="wake-keys">Then press this shortcut</label>
            <div className="field__row">
              <input id="wake-keys" type="text" spellCheck={false} value={keys} placeholder="e.g. ctrl+win+space" onChange={(e) => setKeys(e.target.value)} />
              <button className="btn" type="submit" disabled={keys.trim() === v.wake_keys}>Save</button>
            </div>
          </form>
          <div className="presets">
            <button className="btn" type="button" disabled={v.wake_keys === 'ctrl+win+space'} onClick={() => { setKeys('ctrl+win+space'); patch({ voice: { wake_keys: 'ctrl+win+space' } }); }}>Use Wispr Flow (Ctrl+Win+Space)</button>
            <button className="btn" type="button" disabled={!v.wake_keys} onClick={() => { setKeys(''); patch({ voice: { wake_keys: '' } }); }}>Use Jarvis's own listening</button>
          </div>
          <p className="field__help">{v.wake_keys ? 'Wispr Flow takes the request: Jarvis presses this shortcut when it hears its name, and again when you stop speaking, so Wispr types what you said into the command bar. If Wispr delivers nothing within five seconds, Jarvis uses what it heard itself. Wait for the chime before speaking.' : 'Optional. With Wispr Flow’s hands-free shortcut here, Wispr transcribes your request instead of Jarvis — usually more accurate.'}</p>
        </>
      )}
    </>
  );
}

const TIER_LABEL: Record<number, string> = { 0: 'Observe', 1: 'Reversible', 2: 'Consequential', 3: 'Destructive' };
const TIER_CLASS: Record<number, string> = { 0: 'tier--0', 1: 'tier--1', 2: 'tier--2', 3: 'tier--3' };

const MODULE_LABELS: Record<string, string> = {
  browser: 'Browser', comms: 'Communications', computer: 'Computer', desktop: 'Desktop',
  extra: 'Extra', files: 'Files', media: 'Media', shell: 'Shell', system: 'System',
  memory: 'Memory', scheduler: 'Scheduler', vision: 'Vision', file: 'File ops',
  clipboard: 'Clipboard', skill: 'Skills', agent: 'Agents', homeassistant: 'Home Assistant',
  telegram: 'Telegram', dev: 'Developer', security: 'Security', digest: 'Digest',
  preset: 'Presets', buffer: 'Buffer', revenucat: 'RevenueCat', chrome: 'Chrome',
  'mcp catalogue': 'MCP Catalogue',
};

const EMPTY_TOOLS: ToolInfo[] = [];

function ToolsCatalog() {
  const tools = useHud((s) => s.integrations?.tools ?? EMPTY_TOOLS);
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState<Record<string, boolean>>({});

  const groups = useMemo(() => {
    const q = query.toLowerCase();
    const filtered = q ? tools.filter((t) => t.name.includes(q) || t.description.toLowerCase().includes(q) || t.module.includes(q)) : tools;
    const map = new Map<string, ToolInfo[]>();
    for (const t of filtered) {
      const key = t.module || 'other';
      const arr = map.get(key);
      if (arr) arr.push(t); else map.set(key, [t]);
    }
    return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [tools, query]);

  if (tools.length === 0) return <section className="card"><p className="muted">Loading tool catalog…</p></section>;

  const allOpen = query.length > 0;
  const toggle = (mod: string) => setOpen((o) => ({ ...o, [mod]: !(allOpen || o[mod]) }));

  return (
    <section className="card" aria-labelledby="s-tools">
      <div className="card__head">
        <h2 id="s-tools">Tool catalog</h2>
        <span className="tag">{tools.length} tools</span>
      </div>
      <p className="field__help">Every capability Jarvis can use. Tools in higher tiers always ask before acting.</p>
      <div className="field">
        <div className="field__row">
          <Search size={14} className="muted" aria-hidden="true" />
          <input type="search" placeholder="Search tools…" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search tools" />
        </div>
      </div>
      {groups.length === 0 && <p className="muted">No tools match "{query}".</p>}
      {groups.map(([mod, items]) => {
        const expanded = allOpen || !!open[mod];
        return (
          <div key={mod} className="tool-group">
            <button className="tool-group__head" type="button" onClick={() => toggle(mod)} aria-expanded={expanded}>
              {expanded ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
              <strong>{MODULE_LABELS[mod] || mod.charAt(0).toUpperCase() + mod.slice(1)}</strong>
              <span className="muted">({items.length})</span>
            </button>
            {expanded && (
              <ul className="tool-list">
                {items.map((t) => (
                  <li key={t.name} className="tool-item">
                    <div className="tool-item__head">
                      <code className="tool-item__name">{t.name}</code>
                      <span className={`tag tag--sm ${TIER_CLASS[t.tier] ?? ''}`}>T{t.tier} {TIER_LABEL[t.tier] ?? ''}</span>
                    </div>
                    <p className="tool-item__desc">{t.description}</p>
                  </li>
                ))}
              </ul>
            )}
          </div>
        );
      })}
    </section>
  );
}

export function SettingsView() {
  const settings = useHud((s) => s.settings);
  const providers = useHud((s) => s.providers);
  const hotkeys = useHud((s) => s.hotkeys);
  const version = useHud((s) => s.version);
  const [tab, setTab] = useState<Tab>(initialTab);
  if (!settings) return <div className="view"><p className="muted">Loading settings…</p></div>;
  const order = settings.routing.order;
  const move = (i: number, d: -1 | 1) => {
    const next = order.slice();
    [next[i], next[i + d]] = [next[i + d], next[i]];
    patch({ routing: { order: next } });
  };
  const label = (id: string) => providers.find((p) => p.id === id)?.label ?? id;
  const voices = typeof window.speechSynthesis !== 'undefined';
  const pick = (t: Tab) => { setTab(t); try { window.localStorage.setItem('jarvis.settings.tab', t); } catch { /* storage unavailable */ } };
  return (
    <div className="view settings">
      <header className="view__head"><h1>Settings</h1></header>
      <div className="tabs" role="tablist" aria-label="Settings categories">
        {TABS.map((t) => (
          <button key={t.id} role="tab" id={`tab-${t.id}`} aria-selected={tab === t.id} className={tab === t.id ? 'is-on' : ''} onClick={() => pick(t.id)}>
            <strong>{t.label}</strong><span>{t.help}</span>
          </button>
        ))}
      </div>

      {tab === 'general' && <>
      <section className="card" aria-labelledby="s-name">
        <h2 id="s-name">Assistant name</h2>
        <label className="field"><span className="field__label">Display name</span>
          <input type="text" value={settings.assistant_name || 'J.A.R.V.I.S'} maxLength={40} spellCheck={false}
            onChange={(e) => patch({ assistant_name: e.target.value })} />
          <span className="field__help">Appears in the title bar, wake word, taskbar and throughout the app. Change it to anything you like.</span>
        </label>
      </section>

      <section className="card" aria-labelledby="s-privacy">
        <h2 id="s-privacy">Privacy mode</h2>
        <div className="segmented" role="radiogroup" aria-labelledby="s-privacy">
          {PRIVACY.map((m) => (
            <button key={m.id} role="radio" aria-checked={settings.privacy_mode === m.id} className={settings.privacy_mode === m.id ? 'is-on' : ''} onClick={() => patch({ privacy_mode: m.id })}>
              <strong>{m.name}</strong><span>{m.help}</span>
            </button>
          ))}
        </div>
      </section>

      <section className="card" aria-labelledby="s-theme">
        <h2 id="s-theme">Theme</h2>
        <div className="segmented" role="radiogroup" aria-labelledby="s-theme">
          {THEMES.map((t) => (
            <button key={t.id} role="radio" aria-checked={(settings.theme || 'ion') === t.id} className={(settings.theme || 'ion') === t.id ? 'is-on' : ''} onClick={() => patch({ theme: t.id })}>
              <strong>{t.name}</strong><span>{t.help}</span>
            </button>
          ))}
        </div>
      </section>

      <AutonomySection settings={settings} />
      </>}

      {tab === 'models' && <>
      <section className="card" aria-labelledby="s-route">
        <h2 id="s-route">Routing</h2>
        <div className="field field--inline">
          <label htmlFor="route-mode">Mode</label>
          <select id="route-mode" value={settings.routing.mode} onChange={(e) => patch({ routing: { mode: e.target.value } })}>
            <option value="auto">Auto — first ready provider in the order below</option>
            <option value="manual">Manual — always use one provider</option>
          </select>
        </div>
        {settings.routing.mode === 'manual' ? (
          <div className="field field--inline">
            <label htmlFor="route-manual">Provider</label>
            <select id="route-manual" value={settings.routing.manual_provider ?? ''} onChange={(e) => patch({ routing: { manual_provider: e.target.value || null } })}>
              <option value="">Select a provider…</option>
              {providers.map((p) => <option key={p.id} value={p.id}>{p.label} — {STATE_TEXT[p.state]}</option>)}
            </select>
          </div>
        ) : (
          <ol className="order">
            {order.map((id, i) => (
              <li key={id}>
                <span className="order__n">{i + 1}</span><span className="order__name">{label(id)}</span>
                <button className="btn btn--icon" disabled={i === 0} onClick={() => move(i, -1)} aria-label={`Move ${label(id)} up`}><ArrowUp size={14} aria-hidden="true" /></button>
                <button className="btn btn--icon" disabled={i === order.length - 1} onClick={() => move(i, 1)} aria-label={`Move ${label(id)} down`}><ArrowDown size={14} aria-hidden="true" /></button>
              </li>
            ))}
          </ol>
        )}
        <div className="field field--inline">
          <label htmlFor="max-steps">Step limit per request</label>
          <input id="max-steps" type="number" min={1} max={40} value={settings.max_steps}
            onChange={(e) => { const n = Number(e.target.value); if (Number.isInteger(n) && n >= 1 && n <= 40) patch({ max_steps: n }); }} />
        </div>
      </section>

      <section className="card" aria-labelledby="s-prov">
        <div className="card__head">
          <h2 id="s-prov">Providers</h2>
          <button className="btn btn--ghost" onClick={() => send({ type: 'providers.refresh' })}><RefreshCw size={14} aria-hidden="true" /> Re-check</button>
        </div>
        {providers.length === 0 ? <p className="muted">Checking providers…</p> : (
          groupProviders(providers).map((g) => (
            <div key={g.title} className="group">
              <h3 className="card__sub">{g.title} <small className="muted">{g.help}</small></h3>
              <ul className="pcards">{g.items.map((p) => <ProviderCard key={p.id} p={p} chosen={settings.models[p.id]} />)}</ul>
            </div>
          ))
        )}
      </section>
      </>}

      {tab === 'keys' && <KeysSection settings={settings} />}

      {tab === 'connections' && <>
      <EmailSection settings={settings} />
      <McpSection settings={settings} />
      </>}

      {tab === 'voice' && <>
      <section className="card" aria-labelledby="s-voice">
        <h2 id="s-voice">Voice</h2>
        <WakeControls settings={settings} />
        <label className="check">
          <input type="checkbox" checked={settings.voice.auto_send} onChange={(e) => patch({ voice: { auto_send: e.target.checked } })} />
          <span><strong>Send automatically after dictation</strong><small>When Wispr Flow inserts speech into the command bar, send it after a short pause. Typing or Esc holds it.</small></span>
        </label>
        <div className="field field--inline">
          <label htmlFor="quiet">Pause before sending (ms)</label>
          <input id="quiet" type="number" min={400} max={5000} step={100} value={settings.voice.quiet_ms} disabled={!settings.voice.auto_send}
            onChange={(e) => { const n = Number(e.target.value); if (Number.isInteger(n) && n >= 400 && n <= 5000) patch({ voice: { quiet_ms: n } }); }} />
        </div>
        <label className="check">
          <input type="checkbox" checked={settings.voice.speak} disabled={!voices} onChange={(e) => patch({ voice: { speak: e.target.checked } })} />
          <span><strong>Speak replies</strong><small>{voices ? `Uses the ${OS_NAME} voices installed on this machine. Any new input stops speech.` : 'No speech voices are available in this window.'}</small></span>
        </label>
      </section>

      <TrainingSection settings={settings} />

      <SpeechEngines settings={settings} />
      </>}

      {tab === 'tools' && <ToolsCatalog />}

      {tab === 'general' && (
      <section className="card" aria-labelledby="s-about">
        <h2 id="s-about">Shortcuts &amp; about</h2>
        <dl className="kv kv--wide">
          <div><dt>Summon Jarvis</dt><dd><kbd>{hotkeys.summon}</kbd></dd></div>
          <div><dt>Kill switch — stop everything</dt><dd><kbd>{hotkeys.kill}</kbd></dd></div>
          <div><dt>Console / Audit / Settings</dt><dd><kbd>Ctrl+1</kbd> <kbd>Ctrl+2</kbd> <kbd>Ctrl+4</kbd> · Repair <kbd>Ctrl+3</kbd></dd></div>
          <div><dt>Focus command bar · cancel run</dt><dd><kbd>Ctrl+L</kbd> <kbd>Ctrl+.</kbd></dd></div>
          <div><dt>Version</dt><dd>{version}</dd></div>
        </dl>
      </section>
      )}
    </div>
  );
}
