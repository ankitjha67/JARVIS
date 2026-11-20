import { Check, GraduationCap, SkipForward, X } from 'lucide-react';
import type { Settings as SettingsT } from './protocol';
import { send } from './socket';
import { useHud } from './store';

const pct = (v: number) => `${Math.round(v * 100)}%`;

/** Read-aloud voice training: one sentence at a time, then a report of what Jarvis learned. */
export function TrainingSection({ settings }: { settings: SettingsT }) {
  const t = useHud((s) => s.training);
  const wake = useHud((s) => s.integrations?.wake);
  const v = settings.voice;
  const ready = wake?.state === 'listening' && wake.speech === 'ready';
  const active = t?.state === 'reading' || t?.state === 'evaluating';
  const corrections = Object.entries(v.corrections);
  return (
    <section className="card" aria-labelledby="s-train">
      <div className="card__head">
        <h2 id="s-train">Voice training</h2>
        {v.trained_at && <span className="pcard__state pcard__state--ok"><Check size={14} aria-hidden="true" />Trained {new Date(v.trained_at).toLocaleDateString()}</span>}
      </div>
      <p className="muted">Read twelve short sentences aloud. Jarvis compares what it heard with what you read, learns the words it gets wrong for your voice, learns how you say “Jarvis”, and keeps whichever speech model understands you best. About two minutes. Your voice is processed on this machine and the audio is discarded when training ends.</p>

      {!active && (
        <div className="field__row">
          <button className="btn btn--primary" type="button" disabled={!ready} onClick={() => send({ type: 'voice.train', action: 'start' })}>
            <GraduationCap size={14} aria-hidden="true" /> {v.trained_at ? 'Train again' : 'Start training'}
          </button>
          {!ready && <span className="field__help">{v.wake_word ? 'Waiting for the microphone and speech recognition to be ready…' : 'Turn on “Wake when I say Jarvis” below first — training uses the same microphone listener.'}</span>}
        </div>
      )}
      {t?.state === 'unavailable' && <p className="callout callout--warn" role="alert">Speech recognition is not ready yet. Wait a moment and start again.</p>}
      {t?.state === 'failed' && <p className="callout callout--warn" role="alert">Training could not be scored. Nothing was changed; please try again.</p>}

      {t?.state === 'reading' && (
        <div className="train" role="group" aria-label="Voice training">
          <div className="train__progress" role="progressbar" aria-valuemin={0} aria-valuemax={t.total} aria-valuenow={t.index} aria-label={`Sentence ${t.index + 1} of ${t.total}`}>
            {Array.from({ length: t.total }, (_, i) => <i key={i} className={i < t.index ? 'is-done' : i === t.index ? 'is-now' : ''} />)}
          </div>
          <p className="label">Read this aloud · {t.index + 1} of {t.total}</p>
          <p className="train__sentence" aria-live="polite">{t.sentence}</p>
          {t.heard !== undefined && t.heard !== '' && <p className="train__heard">Heard{t.accepted === false ? ' (try again)' : ''}: <span className="mono">“{t.heard}”</span></p>}
          {t.note && <p className={`callout${t.accepted === false ? ' callout--warn' : ''}`} role="status">{t.note}</p>}
          <div className="field__row">
            <button className="btn" type="button" onClick={() => send({ type: 'voice.train', action: 'skip' })}><SkipForward size={14} aria-hidden="true" /> Skip sentence</button>
            <button className="btn btn--ghost" type="button" onClick={() => send({ type: 'voice.train', action: 'cancel' })}><X size={14} aria-hidden="true" /> Cancel</button>
          </div>
          <p className="field__help">Speak at your normal pace, then pause. Jarvis moves on by itself when it has heard the sentence.</p>
        </div>
      )}
      {t?.state === 'evaluating' && <p className="callout" role="status">Scoring your reading and trying a larger speech model on it. The first time, this downloads about 480 MB and can take a few minutes…</p>}

      {t?.state === 'done' && t.result && (t.result.sentences === 0 ? (
        <p className="callout callout--warn" role="alert">None of the sentences were heard clearly enough to learn from. Check the microphone bar under Voice while you speak, then try again.</p>
      ) : (
        <div className="train__result">
          <dl className="link__stats train__stats">
            <div><dt>Understood before</dt><dd>{pct(t.result.before)}</dd></div>
            <div><dt>With what it learned</dt><dd>{pct(t.result.after)}</dd></div>
            <div><dt>“Jarvis” recognised</dt><dd>{t.result.name_recognised ?? 0} of {t.result.name_sentences ?? 0}</dd></div>
            <div><dt>Speech model</dt><dd>{t.result.model}</dd></div>
          </dl>
          <p className="field__help">The “with what it learned” figure is measured on the same sentences you just read, so everyday speech will land somewhat lower.{t.result.skipped > 0 ? ` ${t.result.skipped} sentence${t.result.skipped === 1 ? ' was' : 's were'} skipped.` : ''}</p>
          {t.result.models.length > 1 && <p className="field__help">Models compared on your voice: {t.result.models.map((m) => `${m.name} ${pct(m.accuracy)}`).join(' · ')}.</p>}
          {t.result.aliases.length > 0 && <p className="field__help">Now also answers to: <span className="mono">{t.result.aliases.join(', ')}</span></p>}
        </div>
      ))}

      {corrections.length > 0 && (
        <details className="bench">
          <summary>{corrections.length} learned correction{corrections.length === 1 ? '' : 's'}</summary>
          <ul className="train__corr">
            {corrections.map(([heard, meant]) => (
              <li key={heard}>
                <span className="mono">{heard}</span><span aria-hidden="true">→</span><span className="mono">{meant}</span>
                <button className="btn btn--icon btn--sm" type="button" aria-label={`Forget the correction ${heard} to ${meant}`}
                  onClick={() => { const next = { ...v.corrections }; delete next[heard]; send({ type: 'settings.set', patch: { voice: { corrections: next } } }); }}><X size={14} aria-hidden="true" /></button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
