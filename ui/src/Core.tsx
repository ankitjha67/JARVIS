import { useEffect, useRef } from 'react';
import type { AgentState } from './protocol';
import { useHud } from './store';

type Visual = AgentState | 'listening' | 'offline';

const LABEL: Record<Visual, string> = {
  idle: 'STANDING BY', listening: 'LISTENING', thinking: 'THINKING', acting: 'EXECUTING',
  awaiting_approval: 'AWAITING APPROVAL', error: 'FAULT', offline: 'CORE OFFLINE',
};

// Motion profile per state: rotation speed, cloud radius, turbulence, brightness.
const PROFILE: Record<Visual, { spin: number; radius: number; turb: number; glow: number }> = {
  idle: { spin: 0.14, radius: 0.86, turb: 0.025, glow: 0.92 },
  listening: { spin: 0.34, radius: 0.98, turb: 0.05, glow: 1.0 },
  thinking: { spin: 0.95, radius: 0.8, turb: 0.13, glow: 1.0 },
  acting: { spin: 1.5, radius: 0.9, turb: 0.07, glow: 1.0 },
  awaiting_approval: { spin: 0.05, radius: 0.84, turb: 0.01, glow: 0.95 },
  error: { spin: 0.25, radius: 0.9, turb: 0.3, glow: 0.85 },
  offline: { spin: 0.03, radius: 0.78, turb: 0.0, glow: 0.3 },
};

const POINTS = 2200;
const TAU = Math.PI * 2;

interface Cloud { x: Float32Array; y: Float32Array; z: Float32Array; r: Float32Array; phase: Float32Array }

function buildCloud(): Cloud {
  const c: Cloud = {
    x: new Float32Array(POINTS), y: new Float32Array(POINTS), z: new Float32Array(POINTS),
    r: new Float32Array(POINTS), phase: new Float32Array(POINTS),
  };
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < POINTS; i++) {
    const yy = 1 - (i / (POINTS - 1)) * 2;
    const ring = Math.sqrt(1 - yy * yy);
    const theta = i * golden;
    c.x[i] = Math.cos(theta) * ring;
    c.y[i] = yy;
    c.z[i] = Math.sin(theta) * ring;
    // Two populations: a bright shell and a volume that thickens towards the centre.
    c.r[i] = i % 3 === 0 ? 0.92 + Math.random() * 0.08 : 0.25 + Math.pow(Math.random(), 0.7) * 0.7;
    c.phase[i] = Math.random() * TAU;
  }
  return c;
}

function hexToRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.replace('#', ''), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const fmtRate = (kbps: number) => (kbps >= 1000 ? `${(kbps / 1000).toFixed(1)}M` : `${Math.round(kbps)}k`);

/** The Core: a rotating particle sphere inside instrument rings. Motion encodes agent state; the ring
 *  markers and arcs show live CPU, memory, GPU and network readings. Nothing here is decorative data. */
export function Core({ listening, compact }: { listening: boolean; compact: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const agent = useHud((s) => s.agent);
  const conn = useHud((s) => s.conn);
  const detail = useHud((s) => s.agentDetail);
  const hearing = useHud((s) => s.voice.active);
  const visual: Visual = conn !== 'online' ? 'offline' : hearing ? 'listening' : agent === 'idle' && listening ? 'listening' : agent;
  const live = useRef({ visual, cpu: 0, ram: 0, gpu: 0, net: 0, hasGpu: false, pulse: 0, pulseAt: 0, hearing: false, voice: 0 });
  live.current.visual = visual;

  useEffect(() => useHud.subscribe((s) => {
    const t = s.telemetry;
    live.current.cpu = t?.cpu_percent ?? 0;
    live.current.ram = t?.ram_percent ?? 0;
    live.current.gpu = t?.gpu?.percent ?? 0;
    live.current.hasGpu = !!t?.gpu;
    live.current.net = (t?.net_down_kbps ?? 0) + (t?.net_up_kbps ?? 0);
    if (s.pulse !== live.current.pulse) { live.current.pulse = s.pulse; live.current.pulseAt = performance.now(); }
    live.current.hearing = s.voice.active;
    live.current.voice = s.voice.active ? s.voice.level : 0;
  }), []);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx) return;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    const col = {
      ion: hexToRgb(css('--ion')), bright: hexToRgb(css('--ion-bright')), amber: hexToRgb(css('--amber')),
      danger: hexToRgb(css('--danger')), ok: hexToRgb(css('--ok')), faint: hexToRgb(css('--text-faint')),
      line: css('--line-strong'), text: css('--text-dim'),
    };
    const cloud = buildCloud();
    const cur = { ...PROFILE.idle };
    const shown = { cpu: 0, ram: 0, gpu: 0 };
    let voice = 0; // smoothed microphone level while a spoken request is being heard
    let flare = 0; // 0..1 glow boost that snaps on when the name is heard and eases off afterwards
    let angle = 0;
    let ringA = 0;
    let ringB = 0;
    let last = performance.now();
    let raf = 0;
    let intro = 0.35; // assembly animation on mount; never starts from nothing, so a stalled frame still shows the Core

    const draw = (now: number) => {
      const dt = Math.min((now - last) / 1000, 0.1);
      last = now;
      const still = reduced.matches;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const size = canvas.clientWidth;
      if (size === 0) { raf = requestAnimationFrame(draw); return; }
      if (canvas.width !== Math.round(size * dpr)) canvas.width = canvas.height = Math.round(size * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, size, size);

      const v = live.current.visual;
      const target = PROFILE[v];
      const k = Math.min(dt * 3, 1);
      cur.spin += (target.spin - cur.spin) * k;
      cur.radius += (target.radius - cur.radius) * k;
      cur.turb += (target.turb - cur.turb) * k;
      cur.glow += (target.glow - cur.glow) * k;
      intro = still ? 1 : Math.min(1, intro + dt / 1.4);
      const ease = 1 - Math.pow(1 - intro, 3);
      if (!still) { angle += cur.spin * dt; ringA += (0.1 + cur.spin * 0.25) * dt; ringB -= (0.06 + cur.spin * 0.18) * dt; }
      shown.cpu += (live.current.cpu - shown.cpu) * Math.min(dt * 4, 1);
      shown.ram += (live.current.ram - shown.ram) * Math.min(dt * 4, 1);
      shown.gpu += (live.current.gpu - shown.gpu) * Math.min(dt * 4, 1);

      voice += (live.current.voice - voice) * Math.min(dt * 14, 1);
      flare += ((live.current.hearing ? 1 : 0) - flare) * Math.min(dt * (live.current.hearing ? 12 : 3), 1);
      const c = size / 2;
      const R = size * 0.5 - 30; // leave room for marker labels
      const rgb = v === 'awaiting_approval' ? col.amber : v === 'error' ? col.danger : v === 'offline' ? col.faint : col.ion;
      const hot = v === 'awaiting_approval' ? col.amber : v === 'error' ? col.danger : col.bright;
      const stroke = (a: number, cc = rgb) => `rgba(${cc[0]},${cc[1]},${cc[2]},${a})`;
      const t = now / 1000;

      // ---- core glow -----------------------------------------------------------------------
      const sphereR = R * 0.7 * cur.radius * ease * (1 + 0.1 * flare + 0.16 * voice);
      const breathe = still ? 0.5 : 0.5 + 0.5 * Math.sin(t * (v === 'idle' ? 0.8 : 2.4));
      const glow = ctx.createRadialGradient(c, c, 0, c, c, sphereR * 1.5);
      const boost = 1 + 0.9 * flare + 0.8 * voice; // the sphere visibly brightens the instant the name is heard
      glow.addColorStop(0, stroke(Math.min(1, 0.55 * cur.glow * boost), hot));
      glow.addColorStop(0.18, stroke(Math.min(1, 0.3 * cur.glow * boost)));
      glow.addColorStop(0.6, stroke(Math.min(0.6, 0.07 * cur.glow * boost * 1.6)));
      glow.addColorStop(1, stroke(0));
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(c, c, sphereR * 1.5, 0, TAU);
      ctx.fill();

      // ---- particle sphere -----------------------------------------------------------------
      ctx.globalCompositeOperation = 'lighter';
      const cosA = Math.cos(angle);
      const sinA = Math.sin(angle);
      const tilt = 0.38;
      const cosT = Math.cos(tilt);
      const sinT = Math.sin(tilt);
      for (let i = 0; i < POINTS; i++) {
        const wob = 1 + cur.turb * Math.sin(t * 3.1 + cloud.phase[i]) + (v === 'listening' ? 0.05 * Math.sin(t * 6 - cloud.r[i] * 9) : 0);
        const rr = cloud.r[i] * wob;
        const x0 = cloud.x[i] * rr;
        const y0 = cloud.y[i] * rr;
        const z0 = cloud.z[i] * rr;
        const x1 = x0 * cosA + z0 * sinA;
        const z1 = -x0 * sinA + z0 * cosA;
        const y2 = y0 * cosT - z1 * sinT;
        const z2 = y0 * sinT + z1 * cosT;
        const depth = (z2 + 1) / 2; // 0 back … 1 front
        const persp = 0.82 + depth * 0.3;
        const px = c + x1 * sphereR * persp;
        const py = c + y2 * sphereR * persp;
        const inner = 1 - cloud.r[i];
        const twinkle = 0.55 + 0.45 * Math.sin(t * 1.7 + cloud.phase[i]);
        const alpha = (0.2 + depth * 0.8) * cur.glow * twinkle;
        const star = i % 37 === 0; // a few larger points give the cloud sparkle and depth cues
        const s = (0.9 + depth * 1.5) * (inner > 0.5 ? 1.3 : 1) * (star ? 2.1 : 1);
        ctx.fillStyle = star ? `rgba(255,255,255,${Math.min(1, alpha * 1.4)})` : inner > 0.45 ? stroke(Math.min(1, alpha * 1.3), hot) : stroke(alpha);
        ctx.fillRect(px - s / 2, py - s / 2, s, s);
      }
      // bright nucleus
      const nucleus = ctx.createRadialGradient(c, c, 0, c, c, sphereR * 0.42);
      nucleus.addColorStop(0, `rgba(255,255,255,${0.95 * cur.glow * (0.8 + 0.2 * breathe)})`);
      nucleus.addColorStop(0.12, `rgba(255,255,255,${0.7 * cur.glow})`);
      nucleus.addColorStop(0.4, stroke(0.45 * cur.glow, hot));
      nucleus.addColorStop(1, stroke(0));
      ctx.fillStyle = nucleus;
      ctx.beginPath();
      ctx.arc(c, c, sphereR * 0.42, 0, TAU);
      ctx.fill();
      ctx.globalCompositeOperation = 'source-over';

      // ---- rings ---------------------------------------------------------------------------
      const rInner = R * 0.74;
      const rDots = R * 0.86;
      const rOuter = R * 0.96;
      ctx.lineCap = 'butt';
      // thin full ring
      ctx.strokeStyle = stroke(0.35);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(c, c, rInner, 0, TAU * ease);
      ctx.stroke();
      // heavy rotating arcs
      ctx.lineWidth = 3;
      ctx.strokeStyle = stroke(0.95);
      const arcs = v === 'error' ? [[0.2, 0.7], [2.4, 0.5], [4.3, 0.8]] : [[0, 1.9], [Math.PI * 0.95, 1.2]];
      for (const [start, len] of arcs) {
        ctx.beginPath();
        ctx.arc(c, c, rInner + 5, ringA + start, ringA + start + len * ease);
        ctx.stroke();
      }
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = stroke(0.6);
      ctx.beginPath();
      ctx.arc(c, c, rInner - 6, ringB + 1.2, ringB + 1.2 + 2.6 * ease);
      ctx.stroke();
      // dotted ring
      ctx.fillStyle = stroke(0.75, hot);
      const dots = 96;
      for (let i = 0; i < dots * ease; i++) {
        const a = ringB * 0.6 + (i / dots) * TAU;
        const big = i % 8 === 0;
        ctx.beginPath();
        ctx.arc(c + Math.cos(a) * rDots, c + Math.sin(a) * rDots, big ? 1.6 : 0.9, 0, TAU);
        ctx.fill();
      }
      // tick ring
      for (let i = 0; i < 120; i++) {
        const a = (i / 120) * TAU - Math.PI / 2;
        const major = i % 10 === 0;
        ctx.strokeStyle = stroke(major ? 0.9 : 0.3);
        ctx.lineWidth = major ? 1.5 : 1;
        const len = (major ? 9 : 4) * ease;
        ctx.beginPath();
        ctx.moveTo(c + Math.cos(a) * rOuter, c + Math.sin(a) * rOuter);
        ctx.lineTo(c + Math.cos(a) * (rOuter + len), c + Math.sin(a) * (rOuter + len));
        ctx.stroke();
      }

      // ---- live load arcs on the outer ring (CPU left, memory right) ------------------------
      const loadArc = (value: number, from: number, dir: 1 | -1) => {
        const span = Math.PI * 0.42;
        ctx.lineWidth = 4;
        ctx.strokeStyle = stroke(0.16);
        ctx.beginPath();
        ctx.arc(c, c, rOuter - 7, from, from + dir * span, dir < 0);
        ctx.stroke();
        const high = value >= 85;
        ctx.strokeStyle = high ? stroke(1, col.amber) : stroke(1, hot);
        ctx.beginPath();
        ctx.arc(c, c, rOuter - 7, from, from + dir * span * Math.max(0.01, (value / 100) * ease), dir < 0);
        ctx.stroke();
      };
      loadArc(shown.cpu, Math.PI * 0.79, 1);
      loadArc(shown.ram, Math.PI * 0.21, -1);

      // ---- ring markers with live readings --------------------------------------------------
      ctx.font = `500 ${size < 300 ? 9 : 10}px "JetBrains Mono", monospace`;
      ctx.textBaseline = 'middle';
      const marker = (a: number, label: string, value: string, warn: boolean) => {
        const x1 = c + Math.cos(a) * (rOuter + 12);
        const y1 = c + Math.sin(a) * (rOuter + 12);
        const right = Math.cos(a) >= 0;
        ctx.fillStyle = warn ? stroke(1, col.amber) : stroke(1, hot);
        ctx.fillRect(x1 - 3, y1 - 3, 6, 6);
        ctx.globalAlpha = ease;
        ctx.textAlign = right ? 'left' : 'right';
        ctx.fillStyle = col.text;
        ctx.fillText(label, x1 + (right ? 9 : -9), y1 - 6);
        ctx.fillStyle = warn ? stroke(1, col.amber) : '#ffffff';
        ctx.fillText(value, x1 + (right ? 9 : -9), y1 + 6);
        ctx.globalAlpha = 1;
      };
      if (size >= 240) {
        marker(Math.PI * 1.13, 'CPU', `${Math.round(shown.cpu)}%`, shown.cpu >= 85);
        marker(Math.PI * 1.87, 'MEM', `${Math.round(shown.ram)}%`, shown.ram >= 85);
        marker(Math.PI * 0.87, 'GPU', live.current.hasGpu ? `${Math.round(shown.gpu)}%` : 'N/A', shown.gpu >= 85);
        marker(Math.PI * 0.13, 'NET', fmtRate(live.current.net), false);
      }

      // ---- voice ring: bars around the sphere that follow the speaker's voice ----------------------
      if (flare > 0.02) {
        const bars = 64;
        const base = sphereR * 1.12;
        ctx.lineCap = 'round';
        ctx.lineWidth = Math.max(2, size / 150);
        for (let i = 0; i < bars; i++) {
          const a = (i / bars) * TAU - Math.PI / 2;
          // Each bar has its own rhythm so the ring looks like sound, scaled by the real level.
          const wave = 0.35 + 0.65 * Math.abs(Math.sin(t * (5 + (i % 7)) + i * 1.7));
          const len = (3 + (R * 0.2) * voice * wave + 2 * Math.sin(t * 2 + i)) * flare;
          ctx.strokeStyle = stroke(Math.min(1, 0.35 + 0.65 * voice) * flare, hot);
          ctx.beginPath();
          ctx.moveTo(c + Math.cos(a) * base, c + Math.sin(a) * base);
          ctx.lineTo(c + Math.cos(a) * (base + Math.max(1, len)), c + Math.sin(a) * (base + Math.max(1, len)));
          ctx.stroke();
        }
        ctx.lineCap = 'butt';
      }

      // ---- state effects -------------------------------------------------------------------
      if (v === 'awaiting_approval' && !still) {
        const a = (t * 1.6) % TAU;
        ctx.strokeStyle = stroke(1, col.amber);
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.arc(c, c, rDots, a, a + 0.9);
        ctx.stroke();
      }
      if (v === 'thinking' && !still) {
        for (let i = 0; i < 3; i++) {
          const a = t * (1.4 + i * 0.5) + i * 2.1;
          ctx.strokeStyle = stroke(0.8, hot);
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.arc(c, c, sphereR * (1.08 + i * 0.07), a, a + 0.7);
          ctx.stroke();
        }
      }
      const age = (now - live.current.pulseAt) / 800;
      if (live.current.pulseAt && age < 1 && !still) {
        ctx.strokeStyle = stroke(1 - age, col.ok);
        ctx.lineWidth = 2.5;
        ctx.beginPath();
        ctx.arc(c, c, sphereR * 0.4 + (rOuter - sphereR * 0.4) * (1 - Math.pow(1 - age, 3)), 0, TAU);
        ctx.stroke();
      }
      raf = requestAnimationFrame(draw);
    };

    // Restart on focus/visibility as a second line of defence, and so `dt` does not jump after a pause.
    const resume = () => { cancelAnimationFrame(raf); last = performance.now(); raf = requestAnimationFrame(draw); };
    window.addEventListener('focus', resume);
    document.addEventListener('visibilitychange', resume);
    raf = requestAnimationFrame(draw);
    return () => { cancelAnimationFrame(raf); document.removeEventListener('visibilitychange', resume); window.removeEventListener('focus', resume); };
  }, []);

  return (
    <div className={`core core--${visual}${compact && !hearing ? ' core--compact' : ''}${hearing ? ' core--hearing' : ''}`}>
      <canvas ref={canvasRef} className="core__canvas" aria-hidden="true" />
      <div className="core__readout" role="status" aria-live="polite">
        <span className="core__label">{LABEL[visual]}</span>
        {(visual === 'acting' || visual === 'awaiting_approval') && detail && <span className="core__detail">{detail}</span>}
      </div>
    </div>
  );
}
