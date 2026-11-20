import { useState } from 'react';
import { Check, KeyRound, Plus, Trash2 } from 'lucide-react';
import type { Settings as SettingsT } from './protocol';
import { send } from './socket';
import { useHud } from './store';
import { detectProvider, isValidName } from './text';

const patch = (p: Record<string, unknown>) => send({ type: 'settings.set', patch: p });

/** One place for every key: paste-and-detect for model providers, custom endpoints, and a vault of named keys. */
const SPEECH_KEYS = [
  { id: 'deepgram', label: 'Deepgram', help: 'console.deepgram.com → API keys' },
  { id: 'elevenlabs', label: 'ElevenLabs', help: 'elevenlabs.io → Profile → API keys' },
];

export function KeysSection({ settings }: { settings: SettingsT }) {
  const providers = useHud((s) => s.providers);
  const vault = useHud((s) => s.integrations?.vault ?? null);
  const [quick, setQuick] = useState('');
  const [quickMsg, setQuickMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [cp, setCp] = useState({ id: '', label: '', base_url: '', key: '' });
  const [cpError, setCpError] = useState('');
  const [vk, setVk] = useState({ name: '', value: '' });
  const [vkError, setVkError] = useState('');
  const guess = quick.trim() ? detectProvider(quick.trim()) : null;
  const label = (id: string) => providers.find((p) => p.id === id)?.label ?? id;
  const customs = Object.entries(settings.custom_providers).filter((e): e is [string, NonNullable<typeof e[1]>] => !!e[1]);

  const saveQuick = () => {
    const key = quick.trim();
    if (!guess) { setQuickMsg({ ok: false, text: 'This key format is not recognised. Add it under a custom provider or the vault below.' }); return; }
    if (send({ type: 'secret.set', provider: guess, value: key })) { setQuick(''); setQuickMsg({ ok: true, text: `Saved as the ${label(guess)} key.` }); }
  };

  const [speechKey, setSpeechKey] = useState<Record<string, string>>({});
  const others = (vault ?? []).filter((n) => !SPEECH_KEYS.some((k) => k.id === n));
  const addCustom = () => {
    const id = cp.id.trim().toLowerCase();
    if (!isValidName(id)) { setCpError('Id: lowercase letters, digits and underscore, starting with a letter.'); return; }
    if (providers.some((p) => p.id === id) || settings.custom_providers[id]) { setCpError('That id is already in use.'); return; }
    if (!/^https?:\/\/[^\s]+$/i.test(cp.base_url.trim())) { setCpError('Base URL must start with http:// or https:// (usually ending in /v1).'); return; }
    setCpError('');
    const key = cp.key.trim();
    if (!patch({ custom_providers: { [id]: { label: cp.label.trim() || id, base_url: cp.base_url.trim(), needs_key: !!key } } })) return;
    if (key) send({ type: 'secret.set', provider: id, value: key });
    setCp({ id: '', label: '', base_url: '', key: '' });
  };

  const addVault = () => {
    const name = vk.name.trim().toLowerCase();
    if (!isValidName(name)) { setVkError('Name: lowercase letters, digits and underscore, starting with a letter.'); return; }
    if (providers.some((p) => p.id === name) || name === 'email') { setVkError('That name is reserved for a provider. Choose another.'); return; }
    if (!vk.value.trim()) { setVkError('Paste the key value.'); return; }
    setVkError('');
    if (send({ type: 'secret.set', provider: name, value: vk.value.trim() })) setVk({ name: '', value: '' });
  };

  return (
    <section className="card" aria-labelledby="s-keys">
      <h2 id="s-keys">API keys</h2>
      <p className="muted">Every key is stored in Windows Credential Manager. Jarvis never shows a stored key again, never sends it to a model, and masks it in logs and the audit trail.</p>

      <h3 className="card__sub">Language models</h3>
      <ul className="keycats">
        {providers.filter((p) => p.needs_key).map((p) => (
          <li key={p.id} className={p.has_key ? 'is-ok' : ''}><span>{p.label}</span><span className="muted">{p.has_key ? 'key saved' : p.key_help || 'no key'}</span></li>
        ))}
      </ul>
      <form className="field" onSubmit={(e) => { e.preventDefault(); saveQuick(); }}>
        <label htmlFor="k-quick">Paste any model-provider key</label>
        <div className="field__row">
          <input id="k-quick" type="password" autoComplete="off" spellCheck={false} value={quick} placeholder="sk-…, sk-ant-…, nvapi-…, AIza…, gsk_…, sk-or-…"
            onChange={(e) => { setQuick(e.target.value); setQuickMsg(null); }} />
          <button className="btn btn--primary" type="submit" disabled={!quick.trim()}><KeyRound size={14} aria-hidden="true" /> {guess ? `Save for ${label(guess)}` : 'Save'}</button>
        </div>
        {quickMsg ? <p className={quickMsg.ok ? 'field__help ok' : 'field__error'} role="status">{quickMsg.text}</p>
          : <p className="field__help">Jarvis recognises the provider from the key's prefix{guess ? <> — this looks like <strong>{label(guess)}</strong></> : ''}. Its best model is then chosen automatically.</p>}
      </form>

      <h3 className="card__sub">Speech services</h3>
      <p className="muted">For understanding requests and for Jarvis's voice (chosen under Voice → Speech engines). Groq and OpenAI reuse their model keys above. Wispr Flow needs no key. NVIDIA speech is not available over this API yet.</p>
      <ul className="keycats">
        {SPEECH_KEYS.map((k) => {
          const saved = vault?.includes(k.id) ?? false;
          return (
            <li key={k.id} className={saved ? 'is-ok' : ''}>
              <span>{k.label}</span>
              {saved ? (
                <span className="field__row"><span className="muted">key saved</span>
                  <button className="btn btn--icon btn--sm" aria-label={`Delete the ${k.label} key`} onClick={() => { if (window.confirm(`Delete the stored ${k.label} key?`)) send({ type: 'secret.set', provider: k.id, value: '' }); }}><Trash2 size={14} aria-hidden="true" /></button></span>
              ) : (
                <form className="field__row" onSubmit={(e) => { e.preventDefault(); const v = (speechKey[k.id] ?? '').trim(); if (v && send({ type: 'secret.set', provider: k.id, value: v })) setSpeechKey({ ...speechKey, [k.id]: '' }); }}>
                  <input type="password" autoComplete="off" spellCheck={false} aria-label={`${k.label} API key`} placeholder={k.help} value={speechKey[k.id] ?? ''} onChange={(e) => setSpeechKey({ ...speechKey, [k.id]: e.target.value })} />
                  <button className="btn" type="submit" disabled={!(speechKey[k.id] ?? '').trim()}>Save</button>
                </form>
              )}
            </li>
          );
        })}
      </ul>

      <h3 className="card__sub">Custom model endpoints</h3>
      <p className="muted">Any service with an OpenAI-compatible API: Together, Fireworks, DeepSeek, Mistral, xAI, a company gateway, or a server on this machine.</p>
      {customs.length > 0 && (
        <ul className="pcards">
          {customs.map(([id, cfg]) => {
            const st = providers.find((p) => p.id === id);
            return (
              <li key={id} className={`pcard pcard--${st?.state ?? 'unknown'}`}>
                <div className="pcard__head">
                  <h4>{cfg.label}</h4><span className="tag">{id}</span>
                  <span className={`pcard__state pcard__state--${st?.state ?? 'unknown'}`}>{st?.state === 'ok' ? <><Check size={14} aria-hidden="true" />Ready · {st.model?.split('/').pop()}</> : st?.detail || st?.state || 'Checking…'}</span>
                  <button className="btn btn--icon btn--sm" aria-label={`Remove ${cfg.label}`}
                    onClick={() => { if (window.confirm(`Remove ${cfg.label} and its stored key?`)) { patch({ custom_providers: { [id]: null } }); send({ type: 'secret.set', provider: id, value: '' }); } }}><Trash2 size={14} aria-hidden="true" /></button>
                </div>
                <code className="pcard__detail mono">{cfg.base_url}</code>
              </li>
            );
          })}
        </ul>
      )}
      <form className="grid-form" onSubmit={(e) => { e.preventDefault(); addCustom(); }}>
        <div className="field"><label htmlFor="cp-id">Id</label><input id="cp-id" type="text" spellCheck={false} value={cp.id} placeholder="together" onChange={(e) => setCp({ ...cp, id: e.target.value })} /></div>
        <div className="field"><label htmlFor="cp-label">Display name</label><input id="cp-label" type="text" value={cp.label} placeholder="Together AI" onChange={(e) => setCp({ ...cp, label: e.target.value })} /></div>
        <div className="field grid-form__wide"><label htmlFor="cp-url">Base URL</label><input id="cp-url" type="text" spellCheck={false} value={cp.base_url} placeholder="https://api.together.xyz/v1" onChange={(e) => setCp({ ...cp, base_url: e.target.value })} /></div>
        <div className="field grid-form__wide"><label htmlFor="cp-key">API key (leave empty if none is needed)</label><input id="cp-key" type="password" autoComplete="off" spellCheck={false} value={cp.key} onChange={(e) => setCp({ ...cp, key: e.target.value })} /></div>
        <button className="btn btn--primary" type="submit"><Plus size={14} aria-hidden="true" /> Add provider</button>
      </form>
      {cpError && <p className="field__error" role="alert">{cpError}</p>}

      <h3 className="card__sub">Connectors and other keys (vault)</h3>
      <p className="muted">Named keys for anything else — a GitHub token, a Notion or Slack key, a weather API. Use one in a connected tool server by writing <code className="mono">{'${secret:name}'}</code> as the value of an environment variable or header.</p>
      {vault === null ? <p className="muted">Loading…</p> : others.length === 0 ? <p className="muted">The vault is empty.</p> : (
        <ul className="vault">
          {others.map((name) => (
            <li key={name}>
              <KeyRound size={14} aria-hidden="true" /><code className="mono">{name}</code>
              <span className="muted mono">{`\${secret:${name}}`}</span>
              <button className="btn btn--icon btn--sm" aria-label={`Delete key ${name}`} onClick={() => { if (window.confirm(`Delete the stored key “${name}”? Servers that use it will stop working.`)) send({ type: 'secret.set', provider: name, value: '' }); }}><Trash2 size={14} aria-hidden="true" /></button>
            </li>
          ))}
        </ul>
      )}
      <form className="grid-form" onSubmit={(e) => { e.preventDefault(); addVault(); }}>
        <div className="field"><label htmlFor="vk-name">Name</label><input id="vk-name" type="text" spellCheck={false} value={vk.name} placeholder="github_token" onChange={(e) => setVk({ ...vk, name: e.target.value })} /></div>
        <div className="field"><label htmlFor="vk-value">Key</label><input id="vk-value" type="password" autoComplete="off" spellCheck={false} value={vk.value} onChange={(e) => setVk({ ...vk, value: e.target.value })} /></div>
        <button className="btn btn--primary" type="submit"><Plus size={14} aria-hidden="true" /> Store key</button>
      </form>
      {vkError && <p className="field__error" role="alert">{vkError}</p>}
    </section>
  );
}
