import { PROTOCOL_VERSION, type ClientMessage, type ServerMessage } from './protocol';
import { useHud } from './store';

// The per-launch token arrives in the URL fragment (never sent to any server) and is kept for reloads.
function readToken(): string | null {
  const m = /[#&]t=([A-Za-z0-9_-]{16,128})/.exec(window.location.hash);
  if (m) {
    try { sessionStorage.setItem('jarvis.t', m[1]); } catch { /* storage may be unavailable */ }
    history.replaceState(null, '', window.location.pathname);
    return m[1];
  }
  try { return sessionStorage.getItem('jarvis.t'); } catch { return null; }
}

const CORE_PORT = import.meta.env.VITE_CORE_PORT ?? '47821';
const wsUrl = import.meta.env.DEV ? `ws://127.0.0.1:${CORE_PORT}/ws` : `ws://${window.location.host}/ws`;

let ws: WebSocket | null = null;
let attempt = 0;
let timer: number | undefined;
const token = readToken();

export function connect(): void {
  const { setConn, apply } = useHud.getState();
  if (!token) { setConn('rejected'); return; }
  setConn(attempt === 0 ? 'connecting' : 'offline', attempt);
  const sock = new WebSocket(wsUrl);
  ws = sock;
  sock.onopen = () => sock.send(JSON.stringify({ type: 'hello', token, v: PROTOCOL_VERSION } satisfies ClientMessage));
  sock.onmessage = (ev) => {
    let msg: ServerMessage;
    try { msg = JSON.parse(String(ev.data)) as ServerMessage; } catch { return; }
    if (msg.type === 'ready') { attempt = 0; setConn('online'); }
    apply(msg);
  };
  sock.onclose = (ev) => {
    if (ws !== sock) return;
    ws = null;
    if (ev.code === 4401 || ev.code === 4403) { setConn('rejected'); return; } // stale token: needs a relaunch
    attempt += 1;
    setConn('offline', attempt);
    window.clearTimeout(timer);
    timer = window.setTimeout(connect, Math.min(500 * 2 ** Math.min(attempt, 4), 8000));
  };
}

export async function downloadWorkbook(id: string): Promise<void> {
  const base = import.meta.env.DEV ? `http://127.0.0.1:${CORE_PORT}` : '';
  const response = await fetch(`${base}/api/runs/${encodeURIComponent(id)}/workbook`, {
    headers: { Authorization: `Bearer ${token ?? ''}` },
  });
  if (!response.ok) throw new Error(`Workbook download failed (${response.status}).`);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = `reconciliation-${id}.xlsx`;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Returns false when the core is not connected, so callers can keep the user's input. */
export function send(msg: ClientMessage): boolean {
  if (!ws || ws.readyState !== WebSocket.OPEN) return false;
  ws.send(JSON.stringify(msg));
  return true;
}
