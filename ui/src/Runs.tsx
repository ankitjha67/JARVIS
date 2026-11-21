import { useEffect, useState, type FormEvent } from 'react';
import { downloadWorkbook, send } from './socket';
import { useHud } from './store';

export function RunsView() {
  const page = useHud((s) => s.runs);
  const online = useHud((s) => s.conn === 'online');
  const approvals = useHud((s) => s.approvals);
  const setView = useHud((s) => s.setView);
  const [left, setLeft] = useState('');
  const [right, setRight] = useState('');
  const [keys, setKeys] = useState('');
  const [leftSheet, setLeftSheet] = useState('');
  const [rightSheet, setRightSheet] = useState('');
  const [name, setName] = useState('');
  const [schedule, setSchedule] = useState('');
  const [selected, setSelected] = useState('');
  const [editing, setEditing] = useState('');
  const [metric, setMetric] = useState('');
  const [workbook, setWorkbook] = useState(false);
  const [downloadError, setDownloadError] = useState('');
  const result = page?.detail?.result;
  const workbookAvailable = !!result && typeof result === 'object' &&
    'workbook_available' in result && result.workbook_available === true;

  useEffect(() => {
    if (!online) return;
    const refresh = () => send(selected
      ? { type: 'runs.action', action: 'detail', id: selected, poll: true }
      : { type: 'runs.action', action: 'list', poll: true });
    refresh();
    const interval = window.setInterval(refresh, 2500);
    return () => window.clearInterval(interval);
  }, [online, selected]);

  function currentInputs() {
    return {
      left, right, keys: keys.split(',').map((value) => value.trim()),
      left_sheet: leftSheet || null, right_sheet: rightSheet || null,
      metric: metric.trim() || null, export_workbook: workbook,
    };
  }

  function reconcile(event: FormEvent) {
    event.preventDefault();
    const inputs = currentInputs();
    send(editing
      ? { type: 'runs.action', action: 'run', id: editing, inputs }
      : { type: 'runs.action', action: 'reconcile', inputs });
  }

  return (
    <main className="run-center">
      <h1>Run Center</h1>
      {!online && <p>Jarvis core is offline.</p>}
      {!!approvals.length && <button onClick={() => setView('console')}>Review {approvals.length} pending approvals</button>}
      {page?.error && <p role="alert">{page.error}</p>}
      <section aria-labelledby="reconcile-heading">
        <h2 id="reconcile-heading">Compare extracts</h2>
        <form onSubmit={reconcile}>
          <label>First CSV or XLSX path <input required value={left} onChange={(e) => setLeft(e.target.value)} /></label>
          <label>Second CSV or XLSX path <input required value={right} onChange={(e) => setRight(e.target.value)} /></label>
          <label>Business key columns <input required value={keys} onChange={(e) => setKeys(e.target.value)} placeholder="Account ID, Facility ID" /></label>
          <label>First worksheet (optional) <input value={leftSheet} onChange={(e) => setLeftSheet(e.target.value)} /></label>
          <label>Second worksheet (optional) <input value={rightSheet} onChange={(e) => setRightSheet(e.target.value)} /></label>
          <label>Variance metric (optional) <input value={metric} onChange={(e) => setMetric(e.target.value)} placeholder="RWA" /></label>
          <label><span><input type="checkbox" checked={workbook} onChange={(e) => setWorkbook(e.target.checked)} /> Build Excel report</span></label>
          <button disabled={!online} type="submit">Queue comparison</button>
          {editing && <button type="button" disabled={!online} onClick={() => send({
            type: 'runs.action', action: 'revise', id: editing, inputs: currentInputs(),
          })}>Save revised workflow</button>}
          <button disabled={!online} type="button" onClick={() => send({ type: 'runs.action', action: 'test' })}>Test with sample rows</button>
        </form>
      </section>
      <button disabled={!online} onClick={() => send({ type: 'runs.action', action: 'handover' })}>Generate handover</button>
      {page?.result && <pre aria-label="Action result">{JSON.stringify(page.result, null, 2)}</pre>}
      <section aria-labelledby="saved-heading">
        <h2 id="saved-heading">Saved workflows</h2>
        {page?.saved.length === 0 && <p>Save a completed comparison from its run details.</p>}
        {!!page?.saved.length && <label>Schedule (UTC) <input value={schedule} onChange={(e) => setSchedule(e.target.value)} placeholder="every 2h or daily 09:00" /></label>}
        {page?.saved.map((item) => <div key={item.id} className="run-center__row">
          <strong>{item.name} (v{item.version})</strong>
          <button onClick={() => {
            setLeft(item.inputs.left); setRight(item.inputs.right); setKeys(item.inputs.keys.join(', '));
            setLeftSheet(item.inputs.left_sheet ?? ''); setRightSheet(item.inputs.right_sheet ?? '');
            setMetric(item.inputs.metric ?? ''); setWorkbook(item.inputs.export_workbook ?? false);
            setEditing(item.id);
          }}>Load inputs</button>
          <button disabled={!online} onClick={() => send({ type: 'runs.action', action: 'run', id: item.id })}>Run again</button>
          <button disabled={!online} onClick={() => send({ type: 'runs.action', action: 'health', id: item.id })}>Check inputs</button>
          <button disabled={!online || !schedule.trim()} onClick={() => send({ type: 'runs.action', action: 'schedule', id: item.id, schedule })}>Schedule</button>
        </div>)}
      </section>
      {!!page?.schedules.length && <section aria-labelledby="schedule-heading">
        <h2 id="schedule-heading">Scheduled workflows</h2>
        {page.schedules.map((item) => <div key={item.id} className="run-center__row">
          <strong>{item.name}</strong>
          <span>{item.schedule} · {item.status}</span>
          {item.status === 'active' && item.next_run_at && <time dateTime={item.next_run_at}>{new Date(item.next_run_at).toLocaleString()}</time>}
          <button disabled={!online} onClick={() => send({
            type: 'runs.action', action: item.status === 'active' ? 'pause_schedule' : 'resume_schedule', id: item.id,
          })}>{item.status === 'active' ? 'Pause' : 'Resume'}</button>
        </div>)}
      </section>}
      <section aria-labelledby="run-heading">
        <h2 id="run-heading">Recent jobs</h2>
        {page?.tasks.length === 0 && <p>No background jobs yet.</p>}
        {page?.tasks.map((task) => <div key={task.id} className="run-center__row">
          <button aria-pressed={selected === task.id} onClick={() => setSelected(task.id)}>{task.name}</button>
          <span>{task.status} · {Math.round(task.progress * 100)}%</span>
          {['queued', 'running', 'paused'].includes(task.status) &&
            <button disabled={!online} onClick={() => send({ type: 'runs.action', action: 'cancel', id: task.id })}>Cancel</button>}
        </div>)}
        {page?.detail?.id === selected && <div>
          <h3>{page.detail.name}</h3>
          {workbookAvailable && <button disabled={!online} onClick={() => {
            setDownloadError('');
            void downloadWorkbook(selected).catch((error: Error) => setDownloadError(error.message));
          }}>Download Excel report</button>}
          {downloadError && <p role="alert">{downloadError}</p>}
          {page.detail.error && <p role="alert">{page.detail.error}</p>}
          <ul>{page.detail.steps.map((step) => <li key={step.id}>{step.description} · {step.status}</li>)}</ul>
          {page.detail.result != null && <pre aria-label="Run result">{JSON.stringify(page.detail.result, null, 2)}</pre>}
          {page.detail.goal === 'workflow/reconcile' && page.detail.status === 'done' && <form onSubmit={(event) => {
            event.preventDefault();
            send({ type: 'runs.action', action: 'save', id: selected, name });
          }}>
            <label>Workflow name <input required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} /></label>
            <button disabled={!online} type="submit">Save workflow</button>
          </form>}
        </div>}
      </section>
    </main>
  );
}
