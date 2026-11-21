import { useState, useEffect, useRef } from 'react';
import { Check, ChevronRight, KeyRound } from 'lucide-react';
import type { PrivacyMode, ThemeId } from './protocol';
import { send } from './socket';
import { useHud } from './store';
import { Scramble } from './Hud';

const patch = (p: Record<string, unknown>) => send({ type: 'settings.set', patch: p });

const STEPS = ['welcome', 'name', 'privacy', 'provider', 'theme', 'ready'] as const;
type Step = typeof STEPS[number];

const PRIVACY: { id: PrivacyMode; name: string; help: string }[] = [
  { id: 'local', name: 'Local only', help: 'Everything stays on this machine. Cloud providers are blocked.' },
  { id: 'hybrid', name: 'Hybrid', help: 'Use local and cloud providers in your preferred order.' },
  { id: 'cloud', name: 'Cloud', help: 'Cloud providers only. Best quality for complex tasks.' },
];

const THEMES: { id: ThemeId; name: string; help: string }[] = [
  { id: 'ion', name: 'Ion', help: 'Electric azure on deep navy. The original look.' },
  { id: 'holographic', name: 'Holographic OS', help: 'Cyan and cobalt glassmorphism.' },
  { id: 'sovereign', name: 'Sovereign Executive', help: 'Champagne gold on obsidian.' },
];

function StepDots({ current, total }: { current: number; total: number }) {
  return (
    <div className="ob-dots" role="progressbar" aria-valuenow={current + 1} aria-valuemax={total}>
      {Array.from({ length: total }, (_, i) => (
        <span key={i} className={`ob-dot${i === current ? ' is-active' : i < current ? ' is-done' : ''}`} />
      ))}
    </div>
  );
}

function WelcomeStep({ onNext }: { onNext: () => void }) {
  const name = useHud((s) => s.settings?.assistant_name) || 'J.A.R.V.I.S';
  const [visible, setVisible] = useState(false);
  useEffect(() => { const t = setTimeout(() => setVisible(true), 100); return () => clearTimeout(t); }, []);
  return (
    <div className={`ob-step ob-welcome${visible ? ' is-visible' : ''}`}>
      <div className="ob-core-ring">
        <div className="ob-core-glow" />
      </div>
      <h1 className="ob-title"><Scramble text={name} /></h1>
      <p className="ob-subtitle">Desktop Intelligence</p>
      <p className="ob-desc">
        Your personal AI assistant with 164 tools, 8 LLM providers,
        voice control, and a cinematic heads-up display.
      </p>
      <button className="ob-cta" onClick={onNext}>
        Get started <ChevronRight size={18} />
      </button>
      <p className="ob-hint">Takes about 30 seconds</p>
    </div>
  );
}

function NameStep({ onNext }: { onNext: () => void }) {
  const current = useHud((s) => s.settings?.assistant_name) || 'J.A.R.V.I.S';
  const [value, setValue] = useState(current);
  const commit = () => { if (value.trim()) { patch({ assistant_name: value.trim() }); onNext(); } };
  return (
    <div className="ob-step ob-step--slide">
      <h2 className="ob-step-title">Name your assistant</h2>
      <p className="ob-step-desc">Give it any name you like. This appears in the title bar, the wake word, and everywhere in the app.</p>
      <div className="ob-name-field">
        <input type="text" className="ob-name-input" value={value} maxLength={40} autoFocus spellCheck={false}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') commit(); }} />
        <p className="ob-name-preview"><Scramble text={value || 'J.A.R.V.I.S'} /></p>
      </div>
      <button className="ob-cta" onClick={commit} disabled={!value.trim()}>Continue <ChevronRight size={18} /></button>
    </div>
  );
}

function PrivacyStep({ onNext }: { onNext: () => void }) {
  const current = useHud((s) => s.settings?.privacy_mode ?? 'hybrid');
  const name = useHud((s) => s.settings?.assistant_name) || 'J.A.R.V.I.S';
  const shortName = name.replace(/\./g, '');
  const [selected, setSelected] = useState<PrivacyMode>(current);
  const commit = () => { patch({ privacy_mode: selected }); onNext(); };
  return (
    <div className="ob-step ob-step--slide">
      <h2 className="ob-step-title">Privacy mode</h2>
      <p className="ob-step-desc">Choose how {shortName} routes your requests. You can change this later in Settings.</p>
      <div className="ob-cards">
        {PRIVACY.map((p) => (
          <button key={p.id} className={`ob-card${selected === p.id ? ' is-selected' : ''}`}
            onClick={() => setSelected(p.id)}>
            <h3>{p.name}</h3>
            <p>{p.help}</p>
            {selected === p.id && <Check className="ob-card__check" size={18} />}
          </button>
        ))}
      </div>
      <button className="ob-cta" onClick={commit}>Continue <ChevronRight size={18} /></button>
    </div>
  );
}

function ProviderStep({ onNext }: { onNext: () => void }) {
  const providers = useHud((s) => s.providers);
  const anyReady = providers.some((p) => p.state === 'ok');
  const [key, setKey] = useState('');
  const [savingId, setSavingId] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const saveKey = (providerId: string, value: string) => {
    if (send({ type: 'secret.set', provider: providerId, value })) {
      setSavingId(providerId);
      setKey('');
      setTimeout(() => setSavingId(null), 2000);
    }
  };

  const cloudProviders = providers.filter((p) => !p.local && p.needs_key);
  const localProviders = providers.filter((p) => p.local);

  return (
    <div className="ob-step ob-step--slide">
      <h2 className="ob-step-title">Connect a provider</h2>
      <p className="ob-step-desc">
        At least one language model is needed. Paste a cloud API key below, or start a local server like LM Studio.
      </p>

      <div className="ob-provider-section">
        <h3 className="ob-section-label">Cloud providers</h3>
        <div className="ob-provider-list">
          {cloudProviders.map((p) => (
            <div key={p.id} className={`ob-provider${p.state === 'ok' ? ' is-ready' : ''}`}>
              <div className="ob-provider__head">
                <span className="ob-provider__name">{p.label}</span>
                {p.state === 'ok' ? (
                  <span className="ob-provider__status ob-provider__status--ok"><Check size={14} /> Ready</span>
                ) : p.state === 'testing' ? (
                  <span className="ob-provider__status ob-provider__status--testing">Testing...</span>
                ) : null}
              </div>
              {p.state !== 'ok' && (
                <form className="ob-key-form" onSubmit={(e) => { e.preventDefault(); if (key.trim()) saveKey(p.id, key.trim()); }}>
                  <input ref={inputRef} type="password" autoComplete="off" spellCheck={false}
                    placeholder={`Paste ${p.label} API key`} value={savingId === p.id ? '' : key}
                    onChange={(e) => setKey(e.target.value)}
                    onFocus={() => setSavingId(null)} />
                  <button className="btn btn--primary" type="submit" disabled={!key.trim() || savingId === p.id}>
                    <KeyRound size={14} /> {savingId === p.id ? 'Saved!' : 'Save'}
                  </button>
                </form>
              )}
              {p.key_help && p.state !== 'ok' && <p className="ob-key-hint">{p.key_help}</p>}
            </div>
          ))}
        </div>
      </div>

      {localProviders.length > 0 && (
        <div className="ob-provider-section">
          <h3 className="ob-section-label">Local (on this computer)</h3>
          <div className="ob-provider-list">
            {localProviders.map((p) => (
              <div key={p.id} className={`ob-provider${p.state === 'ok' ? ' is-ready' : ''}`}>
                <span className="ob-provider__name">{p.label}</span>
                {p.state === 'ok' ? (
                  <span className="ob-provider__status ob-provider__status--ok"><Check size={14} /> Connected</span>
                ) : (
                  <span className="ob-provider__status">Not running</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <button className="ob-cta" onClick={onNext} disabled={!anyReady}>
        {anyReady ? <>Continue <ChevronRight size={18} /></> : 'Waiting for a provider...'}
      </button>
      <button className="ob-skip" onClick={onNext}>Skip for now</button>
    </div>
  );
}

function ThemeStep({ onNext }: { onNext: () => void }) {
  const current = useHud((s) => s.settings?.theme ?? 'ion');
  const [selected, setSelected] = useState<ThemeId>(current);

  useEffect(() => {
    const el = document.documentElement;
    if (selected !== 'ion') el.setAttribute('data-theme', selected);
    else el.removeAttribute('data-theme');
    return () => { el.removeAttribute('data-theme'); };
  }, [selected]);

  const commit = () => { patch({ theme: selected }); onNext(); };
  return (
    <div className="ob-step ob-step--slide">
      <h2 className="ob-step-title">Choose your theme</h2>
      <p className="ob-step-desc">Set the visual tone. You can switch any time in Settings.</p>
      <div className="ob-cards ob-cards--themes">
        {THEMES.map((t) => (
          <button key={t.id} className={`ob-card ob-card--theme${selected === t.id ? ' is-selected' : ''}`}
            onClick={() => setSelected(t.id)}>
            <div className={`ob-theme-preview ob-theme-preview--${t.id}`}>
              <div className="ob-theme-bar" />
              <div className="ob-theme-body">
                <div className="ob-theme-ring" />
              </div>
            </div>
            <h3>{t.name}</h3>
            <p>{t.help}</p>
            {selected === t.id && <Check className="ob-card__check" size={18} />}
          </button>
        ))}
      </div>
      <button className="ob-cta" onClick={commit}>Continue <ChevronRight size={18} /></button>
    </div>
  );
}

function ReadyStep({ onFinish }: { onFinish: () => void }) {
  const name = useHud((s) => s.settings?.assistant_name) || 'J.A.R.V.I.S';
  const shortName = name.replace(/\./g, '');
  const [launched, setLaunched] = useState(false);
  useEffect(() => { const t = setTimeout(() => setLaunched(true), 300); return () => clearTimeout(t); }, []);
  return (
    <div className={`ob-step ob-ready${launched ? ' is-visible' : ''}`}>
      <div className="ob-core-ring ob-core-ring--small">
        <div className="ob-core-glow" />
      </div>
      <h2 className="ob-step-title">You're all set</h2>
      <p className="ob-step-desc">
        Press <kbd>Ctrl+Alt+J</kbd> anywhere to summon {shortName}.<br />
        Say <strong>"{shortName}"</strong> to wake by voice.<br />
        Press <kbd>Ctrl+Alt+Backspace</kbd> to kill-switch.
      </p>
      <button className="ob-cta ob-cta--launch" onClick={onFinish}>
        Launch {shortName} <ChevronRight size={18} />
      </button>
    </div>
  );
}

export function Onboarding() {
  const [step, setStep] = useState<Step>('welcome');
  const idx = STEPS.indexOf(step);

  const next = () => {
    const ni = idx + 1;
    if (ni < STEPS.length) setStep(STEPS[ni]);
  };

  const finish = () => {
    patch({ onboarding_complete: true });
  };

  return (
    <div className="ob-root">
      <div className="ob-backdrop" />
      <div className="ob-container">
        {step !== 'welcome' && step !== 'ready' && (
          <StepDots current={idx - 1} total={STEPS.length - 2} />
        )}
        {step === 'welcome' && <WelcomeStep onNext={next} />}
        {step === 'name' && <NameStep onNext={next} />}
        {step === 'privacy' && <PrivacyStep onNext={next} />}
        {step === 'provider' && <ProviderStep onNext={next} />}
        {step === 'theme' && <ThemeStep onNext={next} />}
        {step === 'ready' && <ReadyStep onFinish={finish} />}
      </div>
    </div>
  );
}
