import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { useHud } from './store';

const TAU = Math.PI * 2;
const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/** Run a canvas draw loop that pauses while the window is hidden and handles device pixel ratio. */
function useCanvas(draw: (ctx: CanvasRenderingContext2D, w: number, h: number, now: number, dt: number) => void) {
  const ref = useRef<HTMLCanvasElement>(null);
  const fn = useRef(draw);
  fn.current = draw;
  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;
    let raf = 0;
    let last = performance.now();
    const loop = (now: number) => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      if (w && h) {
        if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
          canvas.width = Math.round(w * dpr);
          canvas.height = Math.round(h * dpr);
        }
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        fn.current(ctx, w, h, now, Math.min((now - last) / 1000, 0.1));
      }
      last = now;
      raf = requestAnimationFrame(loop);
    };
    const resume = () => { cancelAnimationFrame(raf); last = performance.now(); raf = requestAnimationFrame(loop); };
    window.addEventListener('focus', resume);
    document.addEventListener('visibilitychange', resume);
    raf = requestAnimationFrame(loop);
    return () => { cancelAnimationFrame(raf); document.removeEventListener('visibilitychange', resume); window.removeEventListener('focus', resume); };
  }, []);
  return ref;
}

// ---- Backdrop: drifting depth grid and dust behind the whole HUD ---------------------------------

export function Backdrop() {
  const dust = useRef<{ x: number; y: number; z: number; s: number }[]>([]);
  const ref = useCanvas((ctx, w, h, now, dt) => {
    if (dust.current.length === 0) {
      dust.current = Array.from({ length: 70 }, () => ({ x: Math.random(), y: Math.random(), z: 0.2 + Math.random() * 0.8, s: Math.random() * TAU }));
    }
    const still = reducedMotion();
    ctx.clearRect(0, 0, w, h);
    const line = css('--grid');
    // Perspective floor grid receding to a horizon just below the centre.
    const horizon = h * 0.56;
    const t = still ? 0 : now / 1000;
    ctx.strokeStyle = line;
    ctx.lineWidth = 1;
    for (let i = 0; i <= 14; i++) {
      const p = ((i + (t * 0.12) % 1) / 14);
      const y = horizon + Math.pow(p, 2.2) * (h - horizon);
      ctx.globalAlpha = 0.5 * p;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(w, y);
      ctx.stroke();
    }
    for (let i = -12; i <= 12; i++) {
      ctx.globalAlpha = 0.22;
      ctx.beginPath();
      ctx.moveTo(w / 2 + i * 26, horizon);
      ctx.lineTo(w / 2 + i * 190, h);
      ctx.stroke();
    }
    // Dust drifting upward at different depths.
    ctx.fillStyle = css('--ion-bright');
    for (const d of dust.current) {
      if (!still) {
        d.y -= dt * 0.012 * d.z;
        d.x += Math.sin(t * 0.3 + d.s) * dt * 0.004;
        if (d.y < -0.02) { d.y = 1.02; d.x = Math.random(); }
      }
      ctx.globalAlpha = 0.1 + 0.3 * d.z * (0.6 + 0.4 * Math.sin(t * 1.3 + d.s));
      const s = 0.8 + d.z * 1.4;
      ctx.fillRect(d.x * w, d.y * h, s, s);
    }
    ctx.globalAlpha = 1;
  });
  return <canvas ref={ref} className="backdrop" aria-hidden="true" />;
}

// ---- Text and number motion ----------------------------------------------------------------------

const GLYPHS = 'ABCDEFGHJKLMNPQRSTUVWXYZ0123456789/<>-_';

/** Decode-in effect for headings: characters resolve left to right once, on mount. */
export function Scramble({ text, delay = 0 }: { text: string; delay?: number }) {
  const [shown, setShown] = useState(reducedMotion() ? text : '');
  useEffect(() => {
    if (reducedMotion()) { setShown(text); return; }
    let raf = 0;
    const start = performance.now() + delay;
    const tick = (now: number) => {
      const p = Math.max(0, (now - start) / 550);
      const solid = Math.floor(p * text.length);
      if (p >= 1) { setShown(text); return; }
      let out = text.slice(0, solid);
      for (let i = solid; i < Math.min(text.length, solid + 4); i++) out += text[i] === ' ' ? ' ' : GLYPHS[Math.floor(Math.random() * GLYPHS.length)];
      setShown(now < start ? '' : out);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [text, delay]);
  return <span aria-label={text}><span aria-hidden="true">{shown}</span></span>;
}

/** Smoothly animate a number towards its latest value. */
export function useTween(value: number, ms = 500): number {
  const [shown, setShown] = useState(value);
  const from = useRef(value);
  useEffect(() => {
    if (reducedMotion()) { setShown(value); return; }
    const start = performance.now();
    const origin = from.current;
    let raf = 0;
    const tick = (now: number) => {
      const p = Math.min(1, (now - start) / ms);
      const v = origin + (value - origin) * (1 - Math.pow(1 - p, 3));
      from.current = v;
      setShown(v);
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, ms]);
  return shown;
}

export function Num({ value, digits = 0, suffix = '' }: { value: number; digits?: number; suffix?: string }) {
  return <>{useTween(value).toFixed(digits)}{suffix}</>;
}

export function Block({ title, children, className = '', delay = 0, aside }: {
  title: string; children: ReactNode; className?: string; delay?: number; aside?: ReactNode;
}) {
  return (
    <section className={`block ${className}`} style={{ animationDelay: `${delay}ms` }} aria-label={title}>
      <h2 className="block__title"><i aria-hidden="true" /><Scramble text={title} delay={delay} />{aside && <span className="block__aside">{aside}</span>}</h2>
      {children}
    </section>
  );
}

// ---- Clock ---------------------------------------------------------------------------------------

const pad = (n: number, l = 2) => String(n).padStart(l, '0');

export function ClockBlock() {
  const [now, setNow] = useState(() => new Date());
  const startedAt = useHud((s) => s.stats.startedAt);
  useEffect(() => { const id = window.setInterval(() => setNow(new Date()), 100); return () => window.clearInterval(id); }, []);
  const up = Math.max(0, Math.floor((now.getTime() - startedAt) / 1000));
  return (
    <div className="clock block" style={{ animationDelay: '120ms' }}>
      <time className="clock__time" dateTime={now.toISOString()} aria-label={now.toLocaleTimeString()}>
        {pad(now.getHours())}:{pad(now.getMinutes())}:{pad(now.getSeconds())}<span className="clock__ms">.{Math.floor(now.getMilliseconds() / 100)}</span>
      </time>
      <dl className="clock__meta">
        <div><dt>Date</dt><dd>{now.toLocaleDateString(undefined, { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' }).toUpperCase()}</dd></div>
        <div><dt>Session</dt><dd>{pad(Math.floor(up / 3600))}:{pad(Math.floor(up / 60) % 60)}:{pad(up % 60)}</dd></div>
      </dl>
    </div>
  );
}

// ---- Process radar: the heaviest running apps, by memory (distance) and CPU (blip size) -----------

function angleFor(name: string): number {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return (h % 3600) / 3600 * TAU;
}

const NO_PROCESSES: { name: string; mem_mb: number; cpu_percent: number; count: number }[] = [];

export function Radar() {
  // A selector must return a stable reference, or the store re-renders this component forever.
  const top = useHud((s) => s.telemetry?.top ?? NO_PROCESSES);
  const data = useRef(top);
  data.current = top;
  const ref = useCanvas((ctx, w, h, now) => {
    const still = reducedMotion();
    const c = Math.min(w, h) / 2;
    const cx = w / 2;
    const cy = h / 2;
    const R = c - 4;
    const ion = css('--ion');
    const bright = css('--ion-bright');
    const amber = css('--amber');
    ctx.clearRect(0, 0, w, h);
    const sweep = still ? -Math.PI / 2 : (now / 1000) * 1.1 % TAU;
    // sweep trail
    if (!still) {
      const grad = ctx.createConicGradient(sweep, cx, cy);
      grad.addColorStop(0, 'rgba(56,182,255,0)');
      grad.addColorStop(0.82, 'rgba(56,182,255,0)');
      grad.addColorStop(1, 'rgba(56,182,255,0.38)');
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.arc(cx, cy, R, 0, TAU);
      ctx.fill();
    }
    ctx.strokeStyle = ion;
    ctx.lineWidth = 1;
    for (const f of [1, 0.66, 0.33]) {
      ctx.globalAlpha = f === 1 ? 0.9 : 0.35;
      ctx.beginPath();
      ctx.arc(cx, cy, R * f, 0, TAU);
      ctx.stroke();
    }
    ctx.globalAlpha = 0.3;
    ctx.beginPath();
    ctx.moveTo(cx - R, cy); ctx.lineTo(cx + R, cy); ctx.moveTo(cx, cy - R); ctx.lineTo(cx, cy + R);
    ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.strokeStyle = bright;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + Math.cos(sweep) * R, cy + Math.sin(sweep) * R);
    ctx.stroke();
    // blips
    const items = data.current;
    const maxMem = Math.max(1, ...items.map((p) => p.mem_mb));
    for (const p of items) {
      const a = angleFor(p.name);
      const dist = R * (0.22 + 0.7 * (p.mem_mb / maxMem));
      const lag = ((sweep - a) % TAU + TAU) % TAU; // radians since the sweep passed
      const fade = still ? 1 : Math.max(0.25, 1 - lag / TAU);
      const size = 2 + Math.min(4, p.cpu_percent / 6);
      ctx.globalAlpha = fade;
      ctx.fillStyle = p.cpu_percent >= 40 ? amber : bright;
      ctx.beginPath();
      ctx.arc(cx + Math.cos(a) * dist, cy + Math.sin(a) * dist, size, 0, TAU);
      ctx.fill();
      if (lag < 0.5 && !still) {
        ctx.globalAlpha = 1 - lag * 2;
        ctx.strokeStyle = bright;
        ctx.beginPath();
        ctx.arc(cx + Math.cos(a) * dist, cy + Math.sin(a) * dist, size + 3 + lag * 10, 0, TAU);
        ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;
  });
  const gb = (mb: number) => (mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`);
  return (
    <div className="radar">
      <canvas ref={ref} className="radar__canvas" role="img" aria-label="Radar of the heaviest running applications" />
      {top.length === 0 ? <p className="muted radar__list">Scanning processes…</p> : (
        <ol className="radar__list">
          {top.slice(0, 5).map((p) => (
            <li key={p.name}>
              <span className="radar__name" title={`${p.count} process${p.count === 1 ? '' : 'es'}`}>{p.name}</span>
              <span className="radar__mem">{gb(p.mem_mb)}</span>
              <span className={`radar__cpu${p.cpu_percent >= 40 ? ' amber' : ''}`}>{p.cpu_percent.toFixed(0)}%</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

// ---- Network waveform: download above the axis, upload below --------------------------------------

export function NetWave() {
  const series = useHud((s) => s.series);
  const data = useRef(series);
  data.current = series;
  const ref = useCanvas((ctx, w, h, now) => {
    const { down, up } = data.current;
    ctx.clearRect(0, 0, w, h);
    const mid = h / 2;
    const ion = css('--ion');
    const bright = css('--ion-bright');
    const peak = Math.max(200, ...down, ...up);
    const n = 60;
    const bar = w / n;
    const still = reducedMotion();
    for (let i = 0; i < n; i++) {
      const di = down.length - n + i;
      const d = di >= 0 ? down[di] : 0;
      const u = di >= 0 ? up[di] : 0;
      // A square-root scale keeps small traffic visible next to bursts; idle bars shimmer slightly.
      const idle = still ? 1 : 1 + 0.6 * Math.sin(now / 260 + i * 0.7);
      const dh = Math.max(idle, Math.sqrt(d / peak) * (mid - 2));
      const uh = Math.max(idle, Math.sqrt(u / peak) * (mid - 2));
      ctx.globalAlpha = 0.35 + 0.65 * (i / n);
      ctx.fillStyle = bright;
      ctx.fillRect(i * bar + 0.5, mid - dh, Math.max(1, bar - 1.5), dh);
      ctx.fillStyle = ion;
      ctx.fillRect(i * bar + 0.5, mid + 1, Math.max(1, bar - 1.5), uh);
    }
    ctx.globalAlpha = 1;
  });
  return <canvas ref={ref} className="wave" role="img" aria-label="Network traffic over the last minute" />;
}

// ---- Load history: CPU, memory and GPU over the last minute -----------------------------------------

export function LoadChart() {
  const series = useHud((s) => s.series);
  const data = useRef(series);
  data.current = series;
  const ref = useCanvas((ctx, w, h) => {
    ctx.clearRect(0, 0, w, h);
    const line = css('--line');
    ctx.strokeStyle = line;
    ctx.lineWidth = 1;
    for (const f of [0.25, 0.5, 0.75]) {
      ctx.beginPath(); ctx.moveTo(0, h * f + 0.5); ctx.lineTo(w, h * f + 0.5); ctx.stroke();
    }
    const n = 60;
    const plot = (values: number[], color: string, width: number, fill: boolean, dash: number[] = []) => {
      if (values.length < 2) return;
      const pts = values.slice(-n);
      const x = (i: number) => w - (pts.length - 1 - i) * (w / (n - 1));
      const y = (v: number) => h - 2 - (Math.min(v, 100) / 100) * (h - 4);
      ctx.beginPath();
      pts.forEach((v, i) => (i === 0 ? ctx.moveTo(x(i), y(v)) : ctx.lineTo(x(i), y(v))));
      ctx.strokeStyle = color;
      ctx.lineWidth = width;
      ctx.setLineDash(dash);
      ctx.stroke();
      ctx.setLineDash([]);
      if (fill) {
        ctx.lineTo(x(pts.length - 1), h); ctx.lineTo(x(0), h); ctx.closePath();
        const g = ctx.createLinearGradient(0, 0, 0, h);
        g.addColorStop(0, 'rgba(56,182,255,0.32)');
        g.addColorStop(1, 'rgba(56,182,255,0)');
        ctx.fillStyle = g;
        ctx.fill();
      }
      const lx = x(pts.length - 1);
      const ly = y(pts[pts.length - 1]);
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.arc(lx - 2, ly, 2.2, 0, TAU); ctx.fill();
    };
    plot(data.current.ram, css('--ion-deep'), 1.5, false);
    plot(data.current.gpu, css('--text-faint'), 1, false, [3, 3]);
    plot(data.current.cpu, css('--ion-bright'), 1.8, true);
  });
  return (
    <div className="load">
      <canvas ref={ref} className="load__canvas" role="img" aria-label="CPU, memory and GPU load over the last minute" />
      <ul className="load__legend" aria-hidden="true"><li className="load__cpu">CPU</li><li className="load__mem">MEM</li><li className="load__gpu">GPU</li></ul>
    </div>
  );
}

// ---- Live event stream ---------------------------------------------------------------------------

export function EventStream() {
  const events = useHud((s) => s.events);
  const ref = useRef<HTMLOListElement>(null);
  useLayoutEffect(() => { const el = ref.current; if (el) el.scrollTop = el.scrollHeight; }, [events]);
  if (events.length === 0) return <p className="muted stream__empty">Waiting for the core…</p>;
  return (
    <ol className="stream" ref={ref} aria-live="off">
      {events.slice(-40).map((e) => {
        const d = new Date(e.at);
        return (
          <li key={e.id} className={`stream__row stream__row--${e.level}`}>
            <time>{pad(d.getHours())}:{pad(d.getMinutes())}:{pad(d.getSeconds())}</time>
            <span>{e.text}</span>
          </li>
        );
      })}
    </ol>
  );
}

// ---- Minimal, safe Markdown for assistant replies (no HTML injection) ------------------------------

function inline(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*([^*]+)\*\*|`([^`]+)`)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    out.push(m[2] !== undefined ? <strong key={`${key}b${i++}`}>{m[2]}</strong> : <code key={`${key}c${i++}`}>{m[3]}</code>);
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  const flush = () => {
    if (!list) return;
    const items = list.items.map((it, i) => <li key={i}>{inline(it, `l${blocks.length}-${i}`)}</li>);
    blocks.push(list.ordered ? <ol key={blocks.length}>{items}</ol> : <ul key={blocks.length}>{items}</ul>);
    list = null;
  };
  for (const raw of text.split('\n')) {
    const line = raw.trimEnd();
    const ordered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    const bullet = /^\s*[-*•]\s+(.*)$/.exec(line);
    if (ordered || bullet) {
      const isOrdered = !!ordered;
      if (!list || list.ordered !== isOrdered) { flush(); list = { ordered: isOrdered, items: [] }; }
      list.items.push((ordered ?? bullet)![1]);
      continue;
    }
    flush();
    if (!line.trim()) continue;
    const heading = /^#{1,4}\s+(.*)$/.exec(line);
    blocks.push(heading
      ? <p key={blocks.length} className="md__h">{inline(heading[1], `h${blocks.length}`)}</p>
      : <p key={blocks.length}>{inline(line, `p${blocks.length}`)}</p>);
  }
  flush();
  return <div className="md">{blocks}</div>;
}

// ---- Listening display: appears with the sphere while Jarvis takes a spoken request ----------------------------

export function VoiceOverlay() {
  const voice = useHud((s) => s.voice);
  if (!voice.active) return null;
  const bars = Array.from({ length: 40 }, (_, i) => voice.levels[voice.levels.length - 40 + i] ?? 0);
  return (
    <div className="voice" role="status" aria-live="polite">
      <div className="voice__bars" aria-hidden="true">
        {bars.map((level, i) => <i key={i} style={{ transform: `scaleY(${Math.max(0.06, Math.min(1, level * 1.4))})` }} />)}
      </div>
      <p className={`voice__text${voice.text ? '' : ' voice__text--waiting'}`}>{voice.text || 'Listening… say what you need'}</p>
      <div className="voice__timer" aria-hidden="true"><i key={voice.startedAt} style={{ animationDuration: `${voice.windowS}s` }} /></div>
      <p className="voice__hint">Listening for up to {voice.windowS} seconds · acts when you have finished</p>
    </div>
  );
}
