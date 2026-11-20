import { useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import type { Fault } from './protocol';
import { send } from './socket';
import { useHud } from './store';

const STATUS: Record<Fault['status'], string> = {
  open: 'Open', diagnosing: 'Diagnosing…', checking: 'Testing the repair…', proposed: 'Repair ready',
  applied: 'Repair applied', rolled_back: 'Rolled back', dismissed: 'Dismissed',
};

function DiffBlock({ diff }: { diff: string }) {
  return (
    <pre className="diff" aria-label="Proposed change">
      {diff.split('\n').map((line, i) => (
        <span key={i} className={line.startsWith('+') && !line.startsWith('+++') ? 'diff__add' : line.startsWith('-') && !line.startsWith('---') ? 'diff__del' : line.startsWith('@@') ? 'diff__hunk' : undefined}>{line}{'\n'}</span>
      ))}
    </pre>
  );
}

function FaultCard({ fault }: { fault: Fault }) {
  const [details, setDetails] = useState(false);
  const act = (action: 'propose' | 'apply' | 'rollback' | 'dismiss') => send({ type: 'selfheal.action', action, fault_id: fault.id });
  const busy = fault.status === 'diagnosing' || fault.status === 'checking';
  const where = fault.file ? `${fault.file}:${fault.line}` : fault.source === 'display' ? 'display' : 'core';
  return (
    <li className={`card fault fault--${fault.status}`}>
      <div className="card__head">
        <strong>{fault.kind}</strong>
        <span className="mono muted">{where}</span>
        <span className={`chip-s chip-s--${fault.status === 'proposed' || fault.status === 'applied' ? 'ok' : busy ? 'thinking' : 'quiet'}`}><i aria-hidden="true" />{STATUS[fault.status]}</span>
      </div>
      <p className="fault__msg">{fault.message}</p>
      <p className="field__help">Seen {fault.count} time{fault.count === 1 ? '' : 's'} · last {fault.last.replace('T', ' ')}</p>
      {fault.diagnosis && <p className="callout" role="status"><strong>Cause:</strong> {fault.diagnosis}</p>}
      {fault.note && <p className={`callout${fault.status === 'applied' ? '' : ' callout--warn'}`} role="status">{fault.note}</p>}
      {fault.status === 'proposed' && (
        <>
          <p className="field__help ok">{fault.check}</p>
          <DiffBlock diff={fault.diff} />
          <p className="field__help">Read the change before applying it. The original files are backed up and can be restored with Roll back.</p>
        </>
      )}
      {fault.status !== 'proposed' && fault.check && fault.status === 'open' && <pre className="fault__trace">{fault.check}</pre>}
      <div className="presets">
        {(fault.status === 'open' || fault.status === 'rolled_back') && fault.source === 'core' && fault.file && (
          <button className="btn" type="button" onClick={() => act('propose')} title="Sends this error and the source file to the model chosen by your privacy mode">Diagnose and prepare a repair</button>
        )}
        {busy && <span className="muted">This can take a few minutes: the repair is tried on a copy of the code first.</span>}
        {fault.status === 'proposed' && <button className="btn btn--primary" type="button" onClick={() => act('apply')}>Apply repair</button>}
        {fault.status === 'applied' && <button className="btn" type="button" onClick={() => act('rollback')}>Roll back</button>}
        {fault.status !== 'applied' && fault.status !== 'dismissed' && !busy && <button className="btn" type="button" onClick={() => act('dismiss')}>Dismiss</button>}
        {fault.traceback && <button className="btn" type="button" aria-expanded={details} onClick={() => setDetails(!details)}>{details ? 'Hide details' : 'Details'}</button>}
      </div>
      {details && <pre className="fault__trace">{fault.traceback}</pre>}
    </li>
  );
}

export function RepairView() {
  const { faults, repairs } = useHud((s) => s.selfheal);
  const conn = useHud((s) => s.conn);
  useEffect(() => { if (conn === 'online') send({ type: 'selfheal.action', action: 'list' }); }, [conn]);
  const active = faults.filter((f) => f.status !== 'dismissed');
  return (
    <div className="view repair">
      <header className="view__head">
        <h1>Repair</h1>
        <p className="muted">Faults Jarvis has noticed in itself. Services that stop are restarted automatically. Code is changed only when you press Apply on a repair you have read.</p>
        <button className="btn" type="button" onClick={() => send({ type: 'selfheal.action', action: 'list' })}><RefreshCw size={14} aria-hidden="true" /> Refresh</button>
      </header>
      <section aria-labelledby="repair-faults">
        <h2 id="repair-faults">Faults</h2>
        {active.length === 0 ? <p className="muted">No faults recorded. Jarvis is healthy.</p>
          : <ul className="fault__list">{active.map((f) => <FaultCard key={f.id} fault={f} />)}</ul>}
      </section>
      <section aria-labelledby="repair-auto">
        <h2 id="repair-auto">Automatic recoveries</h2>
        {repairs.length === 0 ? <p className="muted">Nothing has needed restarting.</p> : (
          <ul className="calib__list">
            {repairs.map((r, i) => (
              <li key={i} className={r.ok ? 'is-ok' : ''}><span className="mono">{r.at.replace('T', ' ')}</span><span><strong>{r.what}</strong> — {r.detail}</span></li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
