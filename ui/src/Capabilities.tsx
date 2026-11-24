import { useEffect, useState } from 'react';
import { Check, KeyRound, Loader2, Plus, ShieldAlert, Trash2, TriangleAlert } from 'lucide-react';
import type { McpServerConfig, Settings as SettingsT } from './protocol';
import { send } from './socket';
import { KEY_STORE, parsePairs, splitCommand } from './text';
import { useHud } from './store';

const patch = (p: Partial<SettingsT> | Record<string, unknown>) => send({ type: 'settings.set', patch: p });

// ---- Autonomy ------------------------------------------------------------------------------------

export function AutonomySection({ settings }: { settings: SettingsT }) {
  const toolCount = useHud((s) => s.integrations?.tool_count);
  const trusted = settings.autonomy === 'trusted';
  return (
    <section className="card" aria-labelledby="s-auto">
      <div className="card__head">
        <h2 id="s-auto">Autonomy</h2>
        {toolCount !== undefined && <span className="tag">{toolCount} tools available</span>}
      </div>
      <div className="segmented segmented--2" role="radiogroup" aria-labelledby="s-auto">
        <button role="radio" aria-checked={!trusted} className={!trusted ? 'is-on' : ''} onClick={() => patch({ autonomy: 'guarded' })}>
          <strong>Guarded</strong>
          <span>Jarvis asks before each click, keystroke or page action once it has read a screen, page, file or email. Safest.</span>
        </button>
        <button role="radio" aria-checked={trusted} className={trusted ? 'is-on is-warn' : ''}
          onClick={() => { if (trusted || window.confirm('In Trusted mode Jarvis clicks, types and browses without asking, including after reading web pages and emails that could contain misleading instructions.\n\nSending email, calls, shell commands, file writes, deletions, power actions and external servers will still ask.\n\nTurn on Trusted mode?')) patch({ autonomy: 'trusted' }); }}>
          <strong>Trusted</strong>
          <span>Jarvis operates apps and websites hands-off. It still asks before sending email, calling, running shell commands, writing or deleting files, and power actions.</span>
        </button>
      </div>
      {trusted && (
        <p className="callout callout--warn"><TriangleAlert size={14} aria-hidden="true" />
          Trusted mode is on. A web page or email could contain text that tries to steer Jarvis; with no approval step for clicks and typing, watch what it does and use the kill switch if it goes wrong.</p>
      )}
    </section>
  );
}

// ---- Email ---------------------------------------------------------------------------------------

const PRESETS: { name: string; imap_host: string; imap_port: number; smtp_host: string; smtp_port: number; help: string }[] = [
  { name: 'Gmail', imap_host: 'imap.gmail.com', imap_port: 993, smtp_host: 'smtp.gmail.com', smtp_port: 465, help: 'Needs 2-Step Verification and an app password: myaccount.google.com → Security → App passwords.' },
  { name: 'Outlook / Microsoft 365', imap_host: 'outlook.office365.com', imap_port: 993, smtp_host: 'smtp.office365.com', smtp_port: 587, help: 'Many Microsoft accounts no longer allow password sign-in for IMAP; if it is refused, use Outlook on the web through the browser tools instead.' },
  { name: 'Yahoo', imap_host: 'imap.mail.yahoo.com', imap_port: 993, smtp_host: 'smtp.mail.yahoo.com', smtp_port: 465, help: 'Needs an app password from Yahoo account security.' },
  { name: 'iCloud', imap_host: 'imap.mail.me.com', imap_port: 993, smtp_host: 'smtp.mail.me.com', smtp_port: 587, help: 'Needs an app-specific password from appleid.apple.com.' },
];

export function EmailSection({ settings }: { settings: SettingsT }) {
  const status = useHud((s) => s.integrations?.email);
  const [form, setForm] = useState(settings.email);
  const [password, setPassword] = useState('');
  const [help, setHelp] = useState('');
  const [error, setError] = useState('');
  useEffect(() => { setForm(settings.email); }, [settings.email]);
  const dirty = JSON.stringify(form) !== JSON.stringify(settings.email);
  const set = <K extends keyof SettingsT['email']>(k: K, v: SettingsT['email'][K]) => setForm((f) => ({ ...f, [k]: v }));
  const save = () => {
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.address)) { setError('Enter a full email address, for example name@example.com.'); return; }
    if (!form.imap_host || !form.smtp_host) { setError('Choose a provider or enter both server names.'); return; }
    setError('');
    patch({ email: form });
  };
  const ready = status?.configured && status.has_password;
  return (
    <section className="card" aria-labelledby="s-email">
      <div className="card__head">
        <h2 id="s-email">Email</h2>
        <span className={`pcard__state ${ready ? 'pcard__state--ok' : ''}`}>{ready ? <><Check size={14} aria-hidden="true" />Ready to read and send</> : 'Not set up'}</span>
      </div>
      <p className="muted">Lets Jarvis list, read and send mail from your account. Sending always asks for your approval first.</p>
      <div className="presets">
        {PRESETS.map((p) => (
          <button key={p.name} className="chip" type="button"
            onClick={() => { setForm((f) => ({ ...f, imap_host: p.imap_host, imap_port: p.imap_port, smtp_host: p.smtp_host, smtp_port: p.smtp_port })); setHelp(p.help); }}>{p.name}</button>
        ))}
      </div>
      {help && <p className="field__help">{help}</p>}
      <div className="field field--inline">
        <label htmlFor="em-addr">Email address</label>
        <input id="em-addr" type="text" inputMode="email" autoComplete="off" spellCheck={false} value={form.address} placeholder="name@example.com"
          aria-invalid={!!error} aria-describedby={error ? 'em-err' : undefined} onChange={(e) => set('address', e.target.value.trim())} />
      </div>
      <div className="field field--inline">
        <label htmlFor="em-imap">Incoming server (IMAP)</label>
        <div className="field__row">
          <input id="em-imap" type="text" spellCheck={false} value={form.imap_host} placeholder="imap.example.com" onChange={(e) => set('imap_host', e.target.value.trim())} />
          <input className="port" type="number" aria-label="IMAP port" min={1} max={65535} value={form.imap_port} onChange={(e) => set('imap_port', Number(e.target.value) || 993)} />
        </div>
      </div>
      <div className="field field--inline">
        <label htmlFor="em-smtp">Outgoing server (SMTP)</label>
        <div className="field__row">
          <input id="em-smtp" type="text" spellCheck={false} value={form.smtp_host} placeholder="smtp.example.com" onChange={(e) => set('smtp_host', e.target.value.trim())} />
          <input className="port" type="number" aria-label="SMTP port" min={1} max={65535} value={form.smtp_port} onChange={(e) => set('smtp_port', Number(e.target.value) || 465)} />
        </div>
      </div>
      {error && <p className="field__error" id="em-err" role="alert">{error}</p>}
      <div className="field__row">
        <button className="btn btn--primary" type="button" disabled={!dirty} onClick={save}>Save account</button>
      </div>
      <form className="field" onSubmit={(e) => { e.preventDefault(); if (password.trim() && send({ type: 'secret.set', provider: 'email', value: password.trim() })) setPassword(''); }}>
        <label htmlFor="em-pass">App password {status?.has_password && <span className="ok">· stored in {KEY_STORE}</span>}</label>
        <div className="field__row">
          <input id="em-pass" type="password" autoComplete="off" spellCheck={false} value={password}
            placeholder={status?.has_password ? 'Enter a new app password to replace the stored one' : 'Paste the app password'} onChange={(e) => setPassword(e.target.value)} />
          <button className="btn btn--primary" type="submit" disabled={!password.trim()}><KeyRound size={14} aria-hidden="true" /> Save</button>
          {status?.has_password && (
            <button className="btn" type="button" onClick={() => { if (window.confirm('Remove the stored email app password?')) send({ type: 'secret.set', provider: 'email', value: '' }); }}>
              <Trash2 size={14} aria-hidden="true" /> Remove
            </button>
          )}
        </div>
        <p className="field__help">Use an app password, not your normal password. Jarvis never shows it again.</p>
      </form>
    </section>
  );
}

// ---- MCP servers ---------------------------------------------------------------------------------


export function McpSection({ settings }: { settings: SettingsT }) {
  const live = useHud((s) => s.integrations?.mcp ?? null);
  const [name, setName] = useState('');
  const [target, setTarget] = useState('');
  const [extra, setExtra] = useState('');
  const [error, setError] = useState('');
  const servers = Object.entries(settings.mcp_servers).filter((e): e is [string, McpServerConfig] => !!e[1]);
  const add = () => {
    const n = name.trim();
    const t = target.trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]{0,31}$/.test(n)) { setError('Name: letters, digits and underscore only, starting with a letter.'); return; }
    if (settings.mcp_servers[n]) { setError('A server with that name already exists.'); return; }
    if (!t) { setError('Enter a URL or a command.'); return; }
    const pairs = parsePairs(extra);
    if (pairs === null) { setError('Extra values must be one NAME=value per line.'); return; }
    const hasPairs = Object.keys(pairs).length > 0;
    let cfg: McpServerConfig;
    if (/^https?:\/\//i.test(t)) cfg = hasPairs ? { url: t, headers: pairs } : { url: t };
    else {
      const [command, ...args] = splitCommand(t);
      if (!command) { setError('Enter a URL or a command.'); return; }
      if (!window.confirm(`Jarvis will run this program on your computer every time it starts:\n\n${t}\n\nOnly add servers you trust. Continue?`)) return;
      cfg = hasPairs ? { command, args, env: pairs } : { command, args };
    }
    setError('');
    if (patch({ mcp_servers: { [n]: cfg } })) { setName(''); setTarget(''); setExtra(''); }
  };
  return (
    <section className="card" aria-labelledby="s-mcp">
      <h2 id="s-mcp">Connected tool servers (MCP)</h2>
      <p className="muted">Connect any Model Context Protocol server to give Jarvis more abilities — calendars, chat, databases, smart-home, anything with an MCP server. Its tools ask for approval unless the server marks them read-only. For a connector that gives you a connection URL and asks you to approve in the browser (Wispr Flow Notetaker, Notion, Linear…), paste the URL and leave the keys box empty: Jarvis opens the sign-in page for you and keeps the sign-in encrypted on this computer.</p>
      {servers.length === 0 ? <p className="muted">No servers connected yet.</p> : (
        <ul className="pcards">
          {servers.map(([n, cfg]) => {
            const st = live?.find((x) => x.name === n);
            return (
              <li key={n} className={`pcard pcard--${st?.state === 'ready' ? 'ok' : st?.state === 'error' ? 'error' : 'testing'}`}>
                <div className="pcard__head">
                  <h4>{n}</h4>
                  <span className="tag">{'url' in cfg ? 'Remote' : 'Local program'}</span>
                  <span className={`pcard__state pcard__state--${st?.state === 'ready' ? 'ok' : st?.state === 'error' ? 'error' : 'testing'}`}>
                    {st?.state === 'ready' ? <><Check size={14} aria-hidden="true" />{st.tools} tools</>
                      : st?.state === 'error' ? <><ShieldAlert size={14} aria-hidden="true" />Failed</>
                      : st?.state === 'signin' ? <><Loader2 size={14} className="spin" aria-hidden="true" />Approve in your browser…</>
                      : <><Loader2 size={14} className="spin" aria-hidden="true" />Connecting…</>}
                  </span>
                  <button className="btn btn--icon btn--sm" aria-label={`Remove ${n}`} onClick={() => { if (window.confirm(`Disconnect and remove ${n}?`)) patch({ mcp_servers: { [n]: null } }); }}><Trash2 size={14} aria-hidden="true" /></button>
                </div>
                <code className="pcard__detail mono">{'url' in cfg ? cfg.url : [cfg.command, ...(cfg.args ?? [])].join(' ')}</code>
                {st?.state === 'signin' && <p className="field__help">A sign-in page has opened in your browser. Approve access there and this connector finishes connecting by itself (it waits five minutes).</p>}
                {st?.state === 'error' && <p className="field__error">{st.error || 'The server could not be started.'} Check the command, then remove and add it again.</p>}
              </li>
            );
          })}
        </ul>
      )}
      <form className="mcp-add" onSubmit={(e) => { e.preventDefault(); add(); }}>
        <div className="field">
          <label htmlFor="mcp-name">Name</label>
          <input id="mcp-name" type="text" spellCheck={false} value={name} placeholder="calendar" onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="field mcp-add__target">
          <label htmlFor="mcp-target">Server URL or command</label>
          <input id="mcp-target" type="text" spellCheck={false} value={target} placeholder="https://example.com/mcp   or   npx -y @scope/some-mcp-server" onChange={(e) => setTarget(e.target.value)} />
        </div>
        <button className="btn btn--primary" type="submit"><Plus size={14} aria-hidden="true" /> Connect</button>
        <div className="field mcp-add__extra">
          <label htmlFor="mcp-extra">Keys it needs (optional) — one NAME=value per line; headers for a URL, environment variables for a command</label>
          <textarea id="mcp-extra" rows={2} spellCheck={false} value={extra} placeholder={'Authorization=Bearer ${secret:notion_token}'} onChange={(e) => setExtra(e.target.value)} />
        </div>
      </form>
      {error && <p className="field__error" role="alert">{error}</p>}
    </section>
  );
}
