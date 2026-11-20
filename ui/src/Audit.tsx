import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { TierBadge } from './Console';
import { send } from './socket';
import { useHud } from './store';

const fmt = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'medium' });

export function AuditView() {
  const audit = useHud((s) => s.audit);
  const conn = useHud((s) => s.conn);
  const setLoading = useHud((s) => s.setAuditLoading);
  const [tool, setTool] = useState('');
  const [minTier, setMinTier] = useState('');
  const [failed, setFailed] = useState(false);

  const query = useCallback((beforeId: number | null) => {
    setLoading();
    setFailed(!send({ type: 'audit.query', before_id: beforeId, tool: tool.trim() || null, min_tier: minTier === '' ? null : Number(minTier), limit: 50 }));
  }, [minTier, setLoading, tool]);

  useEffect(() => {
    if (conn !== 'online') return;
    const id = window.setTimeout(() => query(null), 250); // debounce the filter inputs
    return () => window.clearTimeout(id);
  }, [conn, query]);

  const last = audit.items[audit.items.length - 1];
  return (
    <div className="view audit">
      <header className="view__head">
        <h1>Audit</h1>
        <p className="muted">Every action Jarvis attempted, who decided, and what happened. Entries cannot be edited or removed.</p>
      </header>
      <div className="filters">
        <div className="field field--inline">
          <label htmlFor="f-tool">Tool</label>
          <input id="f-tool" type="search" value={tool} placeholder="e.g. shell" onChange={(e) => setTool(e.target.value)} />
        </div>
        <div className="field field--inline">
          <label htmlFor="f-tier">Minimum tier</label>
          <select id="f-tier" value={minTier} onChange={(e) => setMinTier(e.target.value)}>
            <option value="">All</option><option value="1">T1 Reversible and above</option><option value="2">T2 Consequential and above</option><option value="3">T3 Destructive only</option>
          </select>
        </div>
        <button className="btn btn--ghost" onClick={() => query(null)}><RefreshCw size={14} aria-hidden="true" /> Refresh</button>
      </div>
      {failed ? (
        <div className="empty"><p>The core is not connected.</p><button className="btn" onClick={() => query(null)}>Retry</button></div>
      ) : !audit.loaded ? (
        <div className="skeleton-stack" aria-label="Loading audit log"><i /><i /><i /><i /><i /></div>
      ) : audit.items.length === 0 ? (
        <p className="muted">{tool || minTier ? 'No entries match these filters.' : 'Nothing recorded yet.'}</p>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th scope="col">Time</th><th scope="col">Tool</th><th scope="col">Tier</th><th scope="col">Decision</th><th scope="col">Result</th><th scope="col">Arguments</th></tr></thead>
            <tbody>
              {audit.items.map((a) => (
                <tr key={a.id} className={`audit--${a.status}`}>
                  <td className="nowrap"><time dateTime={a.ts}>{fmt.format(new Date(a.ts))}</time></td>
                  <td className="mono">{a.tool}</td>
                  <td><TierBadge tier={a.tier} /></td>
                  <td>{a.decision} <span className="muted">by {a.decided_by}</span></td>
                  <td>{a.status}{a.status === 'ok' ? ` · ${a.duration_ms} ms` : ''}{a.error && <span className="audit__err"> — {a.error}</span>}</td>
                  <td><code className="audit__args">{JSON.stringify(a.args)}</code></td>
                </tr>
              ))}
            </tbody>
          </table>
          {audit.hasMore && last && (
            <button className="btn table__more" disabled={audit.loading} onClick={() => query(last.id)}>{audit.loading ? 'Loading…' : 'Load older entries'}</button>
          )}
        </div>
      )}
    </div>
  );
}
