# JARVIS — Product Requirements Document

| | |
|---|---|
| **Version** | 0.8.0 (living document — updated every build session) |
| **Last updated** | 2026-11-21 |
| **Owner** | Ankit |
| **Status** | M0 Foundation in progress |
| **Companion doc** | [CONTEXT.md](CONTEXT.md) — engineering state, decisions, machine facts |

> How to read this: sections 1–5 say *what* and *why*; 6–9 say *how it behaves*; 10–13 say *how it is built and kept safe*; 14–17 say *when* and *how we know it works*. Requirement IDs (`FR-…`, `NFR-…`, `SEC-…`) are stable and are referenced from code, tests and the changelog in §18.

---

## 1. Vision

A personal, always-available desktop intelligence for one Windows machine that **understands spoken or typed intent, reasons with the best model available for the job, and acts on the computer** — launching, finding, reading, writing, arranging, automating — through an interface that feels like operating an instrument, not chatting with a website.

**One-line pitch:** *Say it, see it happen, stay in control.*

### 1.1 Product principles (ordered — earlier wins on conflict)

1. **The human holds the kill switch.** Nothing consequential happens without a visible, cancellable, logged decision.
2. **Real, not theatrical.** Every gauge shows a real measurement, every button does a real thing. No decorative fake data. (Engineering rule: master-engineer §9.)
3. **Local when it can be, frontier when it must be.** The user chooses the privacy posture; the router obeys it.
4. **Speed is a feature.** Simple commands ("mute", "open Chrome") must never wait on an LLM round-trip to the cloud.
5. **Futuristic ≠ unreadable.** The look is bold; text contrast, focus rings, and reduced-motion support are non-negotiable.
6. **Provider-agnostic.** No model or vendor is hard-wired; models are discovered at runtime.

### 1.2 Non-goals (explicit)

- Not a multi-user or networked service. Single user, single machine, loopback only.
- Not a replacement for Wispr Flow's speech recognition — Jarvis *consumes* Wispr dictation (see §8).
- Not an unattended autonomous agent with admin rights. Elevation is never automatic.
- Not a recreation of any film's interface. The name is an homage; the visual language is original.
- No telemetry leaves the machine except the prompts the user chooses to send to a cloud model.

---

## 2. User & context

**Primary persona — "The Operator" (Ankit).** Power user and builder; runs many Python/finance/AI projects; comfortable with terminals; already pays for Wispr Flow Pro and has LM Studio with local models; wants leverage and delight, and will notice anything fake.

| Goal | Pain today |
|---|---|
| Control the PC by voice/intent without hunting through windows | Each action is a manual multi-step (find app, find file, click through settings) |
| Use *all* models he has access to, cheapest/fastest that works | Each provider is a separate app/tab with separate context |
| Trust an agent with real access | Existing agents either can't act, or act with no guard-rails |
| An interface he enjoys looking at all day | Chat UIs are bland; "futuristic" skins are usually fake dashboards |

**Machine constraints that shape the product** (measured 2026-11-20, see CONTEXT §2): 16 GB RAM, RTX 3050 Laptop 4 GB VRAM, one 1536×864 display, **C: drive nearly full (1.1 GB free)** → everything (code, caches, data, models) lives on `E:`; local models above ~4B parameters run mostly on CPU and are slow.

### 2.1 Core user journeys

| # | Journey | Happy path | Must also handle |
|---|---|---|---|
| J1 | **Summon & command** | Global hotkey → HUD focuses → hold Wispr key, speak → text lands → auto-sends → action executes → spoken/visual confirmation | HUD hidden/minimised; dictation empty; no provider reachable |
| J2 | **Ask & answer** | Question → streamed answer in transcript (+ optional speech) | Provider timeout → automatic fallback to next provider, user told which model answered |
| J3 | **Act with approval** | "Delete the temp folder" → plan shown → approval card with exact command/paths → Allow → executes → result | Deny; timeout; partial failure; cancel mid-run |
| J4 | **Multi-step task** | "Find my latest resume PDF and open it" → search → pick → open, each step visible in Activity | Ambiguity → asks one clarifying question; nothing found → says so |
| J5 | **First run** | Launch → detects LM Studio → guides to add keys → works locally with zero keys | No local runtime and no keys → clear setup state, not a broken chat |
| J6 | **Emergency stop** | Kill-switch hotkey → all runs cancelled, pending approvals denied, state returns to idle | Tool mid-execution (subprocess killed) |
| J7 | **Review** | Open Audit → see every action, who/what approved it, arguments, result | Large history → paginated |

---

## 3. Scope by milestone

| Milestone | Theme | Exit criteria |
|---|---|---|
| **M0 — Foundation** | Core runs, HUD shows live system, chat works through any configured provider, first tools with approvals, audit log | J1(typed), J2, J3, J5, J6, J7 pass manually; tests green |
| **M1 — Voice loop** | Wispr auto-send, spoken replies (local TTS), wake word (optional), fast-path intents without LLM | J1 full voice loop < 1.5 s for fast-path commands |
| **M2 — Hands & eyes** | Screen capture + vision, UI Automation (click/type), browser control, window layout | "Click the Send button in this window" works on 5 reference apps |
| **M3 — Memory & skills** | Long-term memory (local embeddings), user-defined skills/macros, scheduled routines | Remembers preferences across restarts; routine runs on schedule |
| **M4 — Ambient** | Overlay/mini-orb mode, notifications triage, proactive suggestions (opt-in), multi-monitor | Overlay never steals focus; proactive mode off by default |
| **M5 — Hardening & packaging** | Installer, auto-start, signed updates, full threat-model verification | Fresh-machine install in < 5 min |

---

## 4. Functional requirements

Priority: **P0** = M0, **P1** = M1–M2, **P2** = later. Status is tracked in §18.

### 4.1 Conversation & agent loop

| ID | Requirement | P |
|---|---|---|
| FR-A1 | Accept typed input in a command bar; Enter sends, Shift+Enter newline, Esc clears | P0 |
| FR-A2 | Stream assistant output token-by-token; show which provider/model is answering | P0 |
| FR-A3 | Agent loop: model may call tools, receives results, continues until done or step limit (default 12) | P0 |
| FR-A4 | Cancel an in-flight run at any time (button + kill-switch hotkey) | P0 |
| FR-A5 | Conversations persist across restarts; new/switch/delete conversation | P0 (persist) / P1 (switcher UI) |
| FR-A6 | One clarifying question when intent is ambiguous rather than guessing at consequential actions | P0 (prompted) |
| FR-A7 | Fast-path intent matcher executes unambiguous simple commands locally without an LLM | P1 |
| FR-A8 | Context compaction when the conversation exceeds the model's window | P1 |

### 4.2 Model providers & routing

| ID | Requirement | P |
|---|---|---|
| FR-P1 | **Local:** LM Studio and Ollama via OpenAI-compatible endpoints, auto-detected | P0 |
| FR-P2 | **NVIDIA:** NIM hosted API (`integrate.api.nvidia.com/v1`); later, local NIM containers | P0 / P2 |
| FR-P3 | **Frontier:** Anthropic (native API), OpenAI, Google Gemini, plus OpenRouter and Groq as aggregators | P0 |
| FR-P4 | Models are **discovered at runtime** from each provider's model list; nothing is hard-coded except overridable defaults | P0 |
| FR-P5 | Routing mode **Manual** (user picks provider+model) or **Auto** (ordered preference list, first healthy wins) | P0 |
| FR-P6 | Automatic fallback to the next healthy provider on connection error, timeout, 429 or 5xx — before any output has been streamed | P0 |
| FR-P7 | **Privacy mode:** `Local only` (cloud providers are refused at the router, not just hidden), `Hybrid`, `Cloud` | P0 |
| FR-P8 | Task-class routing: tiny local model for intent/routing, strong model for planning, vision model for screen | P1 |
| FR-P9 | Per-provider health, latency and token usage shown in the HUD; cost estimate where pricing is known | P1 |
| FR-P10 | API keys entered in Settings are stored in Windows Credential Manager, never in files or the database | P0 |

### 4.3 Computer control (tools)

Every tool declares a JSON schema and a **risk tier** (§10.2).

| ID | Capability | Tools | Tier | P |
|---|---|---|---|---|
| FR-T1 | System status | `system_status` (CPU, RAM, GPU, disk, battery, network) | T0 | P0 |
| FR-T2 | Apps | `apps_search`, `apps_open` (Start-menu index), `process_list`; `process_kill` | T0/T1; T2 | P0 |
| FR-T3 | Windows | `windows_list`, `window_focus`, `window_minimize/maximize`; `window_close` | T0/T1; T2 | P0 |
| FR-T4 | Files (read) | `files_list`, `files_search`, `files_read` (size-capped, sensitive paths blocked) | T0 | P0 |
| FR-T5 | Files (write) | `files_write`, `files_move`; `files_delete` (to Recycle Bin) | T2; T3 | P0 write / P1 move, delete |
| FR-T6 | Shell | `shell_run` (PowerShell, timeout, output cap, non-elevated) | T2, escalates to T3 by pattern | P0 |
| FR-T7 | Audio & media | `volume_set`, `volume_mute`, `media_control` (play/pause/next/prev) | T1 | P0 |
| FR-T8 | Web | `web_open` (default browser, http/https only) | T1 | P0 |
| FR-T9 | Clipboard | `clipboard_read` (T0, taints the run), `clipboard_write` (T1) | — | P0 |
| FR-T10 | Power | `power_lock` (T1); `power_sleep/restart/shutdown` (T3) | — | P0 lock / P1 rest |
| FR-T11 | Screen | `screen_capture` → vision model; region and active-window modes | T0 + egress consent | P1 |
| FR-T12 | Input | `ui_click`, `ui_type`, `ui_hotkey` via UI Automation tree first, pixels second | T2 | P1 |
| FR-T13 | Browser | Tab list, navigate, read page, fill (never credentials/payment) | T1–T2 | P2 |
| FR-T14 | Settings | Display brightness, night light, Wi-Fi/Bluetooth toggle, focus assist | T1 | P2 |
| FR-T15 | Scheduling | Reminders, timers, routines | T1 | P2 |

### 4.3a Capability families added in 0.3.0 ("handle any task")

User instruction (2026-11-20): *Jarvis should be able to handle any task on the laptop — computer control, web browser, email, calls, MCP, everything.* 42 built-in tools now exist, plus any number from connected MCP servers.

| ID | Family | Tools | How it works | Status |
|---|---|---|---|---|
| FR-T12 | **Desktop apps** | `ui_snapshot`, `ui_click`, `ui_type`, `ui_hotkey` | Windows UI Automation: the foreground window is read as a list of controls with refs; the model acts by ref. Works with any text model — no vision needed | ◐ snapshot and gating verified live; click/type not yet executed on a real app |
| FR-T13 | **Web browser** | `browser_open/snapshot/read/click/type/press/navigate/tabs` | Playwright drives the installed Edge (or Chrome) in Jarvis's own window and profile (`data/browser`); sign-ins persist; the user's everyday profile is untouched | ☑ verified live, including a model-driven task |
| FR-T16 | **Email** | `email_list`, `email_read`, `email_send` | IMAP/SMTP with an app password in Windows Credential Manager; presets for Gmail, Outlook, Yahoo, iCloud | ◐ validation and error paths tested; not run against a real mailbox (no account configured) |
| FR-T17 | **Calls** | `call_start` | Opens the Windows calling app (`tel:`) — Phone Link with a paired phone | ◐ not executed (would dial) |
| FR-T18 | **MCP servers** | `mcp__<server>__<tool>` | Any Model Context Protocol server (local program or URL) added in Settings; its tools join the registry | ☑ verified live against a local stdio server |
| FR-T5 | **Files** | `files_mkdir`, `files_move` (also copy), `files_delete` (Recycle Bin) | Never overwrites; never touches system or credential paths | ☑ executed in tests |
| FR-T10/T14 | **Power & settings** | `power_action`, `settings_open` | Restart/shutdown wait 30 s and can be cancelled; Settings pages open by name, then are operated through UI Automation | ◐ classification tested; not executed |

**Not built yet:** screen capture with a vision model (for apps that expose no accessibility tree), drag-and-drop, calendar and messaging as built-ins (available today through MCP servers or the browser).

### 4.4 Voice

| ID | Requirement | P |
|---|---|---|
| FR-V1 | Command bar is a first-class Wispr Flow dictation target; a global hotkey brings Jarvis forward and focuses it | P0 |
| FR-V2 | **Auto-send after dictation:** a burst of inserted text followed by a quiet period (default 1.2 s) submits automatically; toggle in Settings | P1 |
| FR-V3 | Spoken replies: system voices in M0 (toggle, off by default), local neural TTS (Piper) in M1 | P0 basic / P1 neural |
| FR-V4 | Barge-in: any new input stops speech immediately | P0 |
| FR-V5 | Optional local wake word ("Jarvis") that summons the HUD and triggers the Wispr hotkey | P2 |
| FR-V6 | Fallback built-in STT (faster-whisper, CPU) for when Wispr is not running | P2 |

### 4.5 HUD

| ID | Requirement | P |
|---|---|---|
| FR-U1 | **Console** view: Core visual, transcript, command bar, vitals panel, activity panel | P0 |
| FR-U2 | Core visual reflects real agent state: idle, listening (input focused/receiving), thinking, acting, awaiting approval, error | P0 |
| FR-U3 | Vitals from real sensors at 1 Hz with 60-sample history | P0 |
| FR-U4 | Activity feed: every tool call with tier badge, arguments, duration, outcome | P0 |
| FR-U5 | Approval cards: exact action, arguments, tier, reason; Allow / Deny / Allow for session (T2 only); keyboard operable | P0 |
| FR-U6 | **Settings** view: providers & keys, routing, privacy mode, voice, permissions | P0 |
| FR-U7 | **Audit** view: paginated, filterable action history | P0 |
| FR-U8 | Frameless window with custom title bar, tray icon, global summon hotkey, global kill-switch hotkey | P0 (hotkeys, frameless) / P1 (tray) |
| FR-U9 | Compact overlay mode: Core + command bar only, always-on-top | P2 |
| FR-U10 | Full empty / loading / error / offline states for every panel | P0 |

### 4.6 Memory & audit

| ID | Requirement | P |
|---|---|---|
| FR-M1 | SQLite store for conversations, messages, audit entries, settings, session grants | P0 |
| FR-M2 | Append-only audit: timestamp (UTC), tool, redacted args, tier, decision, decider, result status, duration | P0 |
| FR-M3 | Long-term memory: facts/preferences with local embeddings (nomic-embed via LM Studio), user-editable | P2 |

---

## 5. Non-functional requirements

| ID | Requirement | Target |
|---|---|---|
| NFR-1 | Cold start to interactive HUD | ≤ 4 s |
| NFR-2 | Idle footprint (core + HUD, no local model loaded) | ≤ 450 MB RAM, ≤ 2 % CPU |
| NFR-3 | HUD frame rate | 60 fps idle animation on the Intel iGPU; auto-degrade when on battery or reduced-motion |
| NFR-4 | Input-to-first-token | ≤ 400 ms overhead added by Jarvis on top of provider latency |
| NFR-5 | Fast-path command latency (M1) | ≤ 300 ms end-to-end |
| NFR-6 | No writes to `C:` beyond Windows Credential Manager entries | Data, caches, logs on `E:` |
| NFR-7 | Works with zero internet when a local runtime is available | Fonts and all assets bundled; no CDN |
| NFR-8 | Accessibility | WCAG 2.2 AA contrast, full keyboard operation, visible focus, `prefers-reduced-motion`, no colour-only status |
| NFR-9 | Reliability | A provider or tool failure never crashes the core; every failure is surfaced with a recovery action |
| NFR-10 | Observability | Structured JSON logs with secret redaction; rotating files |
| NFR-11 | Testability | Policy engine, router, tool validation and protocol covered by unit tests; no test needs network or a GPU |

---

## 6. Information architecture

```
JARVIS
├── Console            (default; everything needed to command and observe)
│   ├── Vitals         (left)   CPU · RAM · GPU · VRAM · Disk · Net · Battery
│   ├── Core + Transcript + Command bar (centre)
│   └── Activity + Approvals (right)
├── Audit              (history of actions, filter by tier / tool / outcome)
└── Settings
    ├── Models         (providers, keys, routing order, privacy mode)
    ├── Voice          (auto-send, spoken replies, voice, rate)
    ├── Permissions    (tier policy, session grants, blocked paths)
    └── About          (versions, data locations, hotkeys)
```

Navigation: a labelled icon rail (3 destinations — within 7±2). Depth never exceeds two levels. Approvals are **not** a destination; they interrupt in place on the Console and badge the rail when another view is open.

### 6.1 Console wireframe (1280×800 reference)

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│ ◈ JARVIS      ● IDLE     HYBRID ▾    lmstudio · qwen2.5-coder-7b ▾   14:02   – □ ✕│
├────┬───────────────────┬───────────────────────────────────┬─────────────────────┤
│ ▣  │ VITALS            │                                   │ ACTIVITY            │
│Cons│ CPU   ◜◝ 23%  ▁▂▃ │            ╭───────────╮          │ ▸ apps_open   T1 ✓  │
│    │ RAM   ◜◝ 61%  ▃▃▄ │            │   CORE    │          │   "chrome"   120ms  │
│ ≡  │ GPU   ◜◝  4%  ▁▁▁ │            │ (state)   │          │ ▸ shell_run   T2 ⏸  │
│Audt│ VRAM  0.4/4.0 GB  │            ╰───────────╯          │ ┌─ APPROVAL ──────┐ │
│    │ DISK  E: 102 GB   │  ─────────── transcript ───────── │ │ Run PowerShell   │ │
│ ⚙  │ NET   ↓1.2 ↑0.1   │  You   open chrome                │ │ Get-ChildItem …  │ │
│Sett│ BATT  84% ⚡       │  Jarvis  Opening Chrome.          │ │ [Deny]  [Allow]  │ │
│    │                   │                                   │ └──────────────────┘ │
│    │ PROVIDERS         │ ┌───────────────────────────────┐ │                     │
│    │ ● lmstudio  82ms  │ │ ›  Speak or type a command…  ⏎│ │                     │
│    │ ○ nvidia   no key │ └───────────────────────────────┘ │                     │
└────┴───────────────────┴───────────────────────────────────┴─────────────────────┘
```

### 6.2 State catalogue (every panel)

| Surface | Empty | Loading | Error | Success |
|---|---|---|---|---|
| Transcript | "Ready. Press Ctrl+Alt+J anywhere, then speak or type." + 3 example commands as buttons | Streaming caret | Inline error row with **Retry** and **Switch model** | Message settles, model tag shown |
| Vitals | — | Skeleton arcs until first sample | "Sensor unavailable" per-metric (e.g. no NVIDIA GPU) | Live values |
| Activity | "No actions yet this session." | Row with spinner + elapsed time | Row in danger style + error text + expandable detail | Check + duration |
| Providers | "No model providers reachable." + **Open Settings** | Probing dots | Reason per provider (no key / refused / timeout) | Latency |
| Audit | "Nothing recorded yet." | Skeleton rows | Retry | Table |
| Connection | — | "Connecting to core…" overlay | "Core offline — reconnecting (attempt n)" banner; command bar disabled with reason | Banner clears |

---

## 7. Interaction design

### 7.1 Agent state machine (drives the Core visual, the state pill and the tray)

```
            user input                tool call (auto)            done
   IDLE ───────────────▶ THINKING ───────────────────▶ ACTING ──────────▶ THINKING ─▶ IDLE
     ▲  focus/typing        │  tool needs approval        │ error
     │◀── LISTENING         ▼                             ▼
     │                 AWAITING_APPROVAL ──allow──▶ ACTING     ERROR ──(ack / 4 s)──▶ IDLE
     │                       │ deny / timeout 120 s
     └───────────────────────┴──────────── kill switch from ANY state ──▶ IDLE
```

Illegal transitions are rejected server-side; the HUD only renders what the core reports (single source of truth).

### 7.2 Approval flow

1. Core pauses the run and emits `approval.request` with tool, **exact** arguments, tier, human-readable summary and the reason for escalation (tier, tainted run, or pattern).
2. HUD raises a card in Activity, moves keyboard focus to **Deny** (safe default), plays the attention motion once.
3. `A` allow · `D` or `Esc` deny · `S` allow for session (T2 only, untainted runs only). T3 requires an explicit click on Allow — no session grant, no keyboard-shortcut allow.
4. No response in 120 s → auto-deny; the model is told the user did not approve.

### 7.3 Keyboard map

| Scope | Keys | Action |
|---|---|---|
| Global | `Ctrl+Alt+J` | Summon / focus command bar |
| Global | `Ctrl+Alt+Backspace` | **Kill switch** — cancel all runs, deny pending approvals |
| App | `Ctrl+1/2/3` | Console / Audit / Settings |
| App | `Ctrl+L` | Focus command bar |
| App | `Ctrl+.` | Cancel current run |
| Command bar | `Enter` / `Shift+Enter` / `Esc` / `↑` | Send / newline / clear / recall last |

### 7.4 Motion

Durations 120 / 200 / 320 ms; ease-out on enter, ease-in on exit; exits at ~65 % of enter. Only `transform` and `opacity` animate. Core animation is canvas-drawn and pauses when the window is hidden. `prefers-reduced-motion` freezes the Core to a static state glyph and disables stagger.

---

## 8. Wispr Flow integration

Wispr's Voice Interface API is closed to new partners (verified 2026-11-20), so Jarvis integrates at the **OS level**, which also keeps Wispr's own model, dictionary and Pro features in the loop:

| Layer | Behaviour |
|---|---|
| **Target** | The command bar is a standard text field, so Wispr inserts dictation exactly as it does anywhere else. |
| **Summon** | `Ctrl+Alt+J` shows the HUD and focuses the command bar, so "summon → hold Wispr key → speak" is two keys. |
| **Auto-send (M1)** | Text arriving as a burst (≥ 8 chars within 150 ms — the signature of pasted dictation, unlike typing) arms a 1.2 s quiet timer; when it elapses the message is sent. Typing or Esc disarms. |
| **One-key voice (M2)** | Optional: the summon hotkey also synthesises Wispr's configured hotkey so a single key starts dictation. |
| **Vocabulary** | Docs list app names and tool verbs worth adding to Wispr's dictionary for accuracy. |
| **Fallback** | If Wispr is not running, the HUD says so and typed input still works; built-in STT is P2. |

---

## 9. Visual design system — "Ion" v2

Original design language: a dark instrument room lit by one electric-azure light source. Full-bleed navy, **no boxed panels** — readouts float on a drifting depth grid, framed by corner brackets; amber is reserved for attention and tints the whole room when Jarvis needs a decision.

> v2 (2026-11-20) replaced the v1 panel layout after user feedback that it looked generic. Reference studied: the cinematic assistant-HUD genre (particle-sphere core, ring markers, radar, waveform, live log, large clock). The genre was used as direction; the layout, drawing code and content are original, and **every moving element is bound to real data** (principle 2).

| Element | Motion | Real signal behind it |
|---|---|---|
| Core particle sphere (2,200 points) | Rotation speed, cloud radius and turbulence per agent state; shockwave per finished tool call | Agent state machine, tool events |
| Core rings | Counter-rotating arcs and dotted ring; load arcs; four tagged markers | CPU, memory, GPU, network |
| Backdrop | Receding floor grid, drifting dust, slow scanline | Ambient only (no data implied) |
| Process radar | Rotating sweep; blips flash as the sweep passes | Top applications: distance = memory, size = CPU |
| Net link | Mirrored bar waveform | Download / upload over 60 s |
| Load chart | Scrolling lines | CPU, memory, GPU over 60 s |
| Event stream | Rows rise in, older rows fade | Core messages: state, routing, tools, approvals |
| Model bench | Latency bars | Results of the live model tests (§12) |
| Link card | Number tweens, light sweep | Active model, link latency, requests, actions, tokens |
| Boot | Staggered blur-in, heading decode | First connection |

The token table below lists v1 values; current values live in `ui/src/styles.css` `:root` (`--void #020611`, `--ion #38B6FF`, `--ion-bright #A8E2FF`, `--text-faint #7095BA`).

### 9.1 Tokens

| Token | Value | Use |
|---|---|---|
| `--void` | `#05080D` | App background |
| `--panel` | `#0B1118` | Panel fill |
| `--panel-raised` | `#111A24` | Cards, inputs |
| `--line` | `#1E2C3A` | Hairlines, borders |
| `--line-strong` | `#2F4558` | Emphasised borders, focus-adjacent |
| `--text` | `#E6F1F8` | Primary text (≈16:1 on void) |
| `--text-dim` | `#9DB2C4` | Secondary text (≈8.6:1) |
| `--text-faint` | `#7A90A3` | Labels, units (lowest-contrast text allowed) |
| `--ion` | `#3DE0FF` | Primary accent, live data, focus ring |
| `--ion-deep` | `#0E7C99` | Accent fills behind text |
| `--amber` | `#FFB547` | Attention: approvals, warnings |
| `--danger` | `#FF5C6C` | Errors, T3, destructive |
| `--ok` | `#4FE3A1` | Success, healthy |

Status is never colour-only: every state pairs colour with an icon or word.

### 9.2 Type

| Role | Family | Notes |
|---|---|---|
| Display / labels | **Chakra Petch** 500–600, uppercase labels tracked +0.08em | Angular, technical, legible at 11–13 px label sizes |
| Data / code / numerals | **JetBrains Mono** 400–500, tabular figures | Gauges, logs, arguments |
| Long-form prose | System UI (Segoe UI Variable) | Assistant answers stay comfortable to read |

Scale: 11 · 12 · 13 · 14 · 16 · 20 · 28 · 44. Body minimum 14 px; label minimum 11 px uppercase only. Fonts are bundled locally (NFR-7).

### 9.3 Form

- 4 px spacing grid (4 · 8 · 12 · 16 · 24 · 32 · 48).
- Panels: 1 px `--line` border, 10 px chamfer on top-left and bottom-right via `clip-path`, corner tick marks; no drop shadows — depth comes from fill steps.
- Glow is rationed: Core, focus ring, and the single active element only.
- Permanent chrome ≤ 15 % of the viewport (game-HUD rule): rail 64 px, title bar 40 px.
- Hit targets ≥ 32 px desktop (40 px for primary and approval actions).

### 9.4 The Core

Canvas-rendered concentric instrument: segmented outer ring (rotation speed ∝ activity), inner arc gauges bound to **real** CPU/GPU load, centre aperture whose behaviour encodes state:

| State | Motion | Colour | Label |
|---|---|---|---|
| Idle | Slow breathing, 0.1 Hz | ion, low intensity | `STANDING BY` |
| Listening | Aperture opens, ring ticks follow input cadence | ion, high | `LISTENING` |
| Thinking | Counter-rotating segments | ion | `THINKING` |
| Acting | Radial pulses outward per tool call | ion → ok flash on success | `EXECUTING` |
| Awaiting approval | Ring locks, amber sweep | amber | `AWAITING APPROVAL` |
| Error | Ring breaks, single shake | danger | `FAULT` |

---

## 10. Security & safety model

Jarvis is, by design, a program that executes model-chosen actions on a personal machine. Its threat model is therefore the threat model of a **remote-influenceable local agent**.

### 10.1 Threats and mitigations

| ID | Threat | Mitigation |
|---|---|---|
| SEC-1 | **Indirect prompt injection** — text in a file, web page, clipboard or screenshot instructs the model to act | Tool output is wrapped and labelled as untrusted data; a run that has ingested untrusted content is **tainted**: every T1+ action then requires approval and session grants are ignored |
| SEC-2 | **Malicious web page calls the local API** (CSRF / DNS rebinding to `127.0.0.1`) | Loopback bind only; random per-launch token required on the WebSocket; strict `Origin` and `Host` allow-list; no CORS; no state-changing REST endpoints |
| SEC-3 | **Destructive or irreversible action** (data loss, shutdown) | Tier T3: always confirmed, never remembered; deletes go to Recycle Bin; shell patterns for recursive delete, format, registry, boot config, firewall/Defender, account changes escalate to T3 |
| SEC-4 | **Privilege escalation** | Core runs as the standard user; never self-elevates; `shell_run` refuses `-Verb RunAs`/`runas`/`sudo` patterns; UAC prompts are left to the human |
| SEC-5 | **Secret exposure** — API keys leaking into logs, prompts, DB, or the UI bundle | Keys only in Windows Credential Manager; redaction filter on logs and audit args; UI only ever learns "key present: yes/no" |
| SEC-6 | **Sensitive data exfiltration to cloud models** | Privacy mode enforced in the router; sensitive paths (`.ssh`, browser profiles, credential stores, `.env`, key files) are unreadable by tools; screen capture to a cloud model requires per-session consent |
| SEC-7 | **Command injection through tool arguments** | No string-built shell lines for non-shell tools — `os.startfile`/Win32 APIs with validated args; URL scheme allow-list; path canonicalisation before policy checks |
| SEC-8 | **Runaway agent** | Step limit per run, wall-clock limit per tool, output caps, kill-switch hotkey that also kills child processes |
| SEC-9 | **Persistence abuse** — agent schedules tasks, adds startup entries, installs software | Patterns for scheduled tasks, Run keys, services, `winget/choco/pip/npm install` escalate to T3 |
| SEC-10 | **Supply chain** | Pinned lockfiles (`uv.lock`, `package-lock.json`); minimal dependency set; no post-install scripts trusted blindly; no CDN at runtime |
| SEC-11 | **Credential entry by the agent** | Input tools (M2) refuse password fields and payment forms; the model is instructed never to type secrets |
| SEC-12 | **Audit tampering / deniability** | Append-only audit table (no UPDATE/DELETE path in code); every decision records who decided |

### 10.1a Autonomy modes (0.3.0)

Controlling arbitrary apps and web pages means reading untrusted content constantly, so under the original rules every click would need approval. The user chooses the trade-off in Settings:

| Mode | Behaviour |
|---|---|
| **Guarded** (default) | As §10.2: after any outside content is read, every action asks. |
| **Trusted** | T1 and non-sensitive T2 actions (clicks, typing, navigation) run without asking, even after outside content was read. |

In **both** modes these always ask: every T3, and every tool marked `sensitive` — `shell_run`, `email_send`, `call_start`, `files_write`, `files_delete`, `process_kill`, `power_action`, and any MCP tool its server has not marked read-only. Residual risk in Trusted mode, stated plainly: a click on a "Send" or "Buy" button is still just a click, so a misleading page or email could steer Jarvis into an action in an app or site. The kill switch and audit log remain.

Additional rules: Jarvis refuses to type into password fields (desktop and web), payment-card fields and one-time-code fields (SEC-11); after the user denies an action, the model gets one final turn with no tools so it reports back instead of trying workarounds; URLs must be plain http(s) with a valid host name (a `javascript:` URL was caught by a new test and is now rejected).

### 10.2 Risk tiers

| Tier | Meaning | Examples | Default policy |
|---|---|---|---|
| **T0 Observe** | Reads non-sensitive state | status, list windows, search files | Auto, logged |
| **T1 Reversible** | Changes easily undone state | open app, volume, focus window, open URL | Auto, logged; approval if run is tainted |
| **T2 Consequential** | Changes data or runs code | shell, write file, close window, kill process | Approve each time; "allow for session" possible when untainted |
| **T3 Destructive** | Irreversible or system-level | delete, shutdown, persistence, security settings | Approve each time, explicit click, never remembered |

The policy decision is made **in the core**, from the tool's declared tier plus argument inspection — never by the model, never by the UI.

---

## 11. Architecture

```
┌──────────────────────────── jarvis.exe (Python 3.12, one process) ─────────────────────────────┐
│                                                                                                 │
│  Shell (pywebview/WebView2)        Core (asyncio)                                               │
│  ┌──────────────────────┐   WS    ┌────────────────────────────────────────────────────────┐   │
│  │ HUD  React + TS      │◀──────▶ │ Gateway (FastAPI: token, Origin, protocol validation)  │   │
│  │ Canvas Core, Zustand │  JSON   │   │                                                    │   │
│  └──────────────────────┘         │   ▼                                                    │   │
│  Global hotkeys (Win32)──────────▶│ Orchestrator ── state machine ── run registry          │   │
│                                   │   │         │              │                            │   │
│                                   │   ▼         ▼              ▼                            │   │
│                                   │ Router    Policy        Tool registry ──▶ Win32/psutil  │   │
│                                   │   │      (tiers,taint)     │                            │   │
│                                   │   ▼                        ▼                            │   │
│                                   │ Providers             Audit + Store (SQLite, E:)        │   │
│                                   └───┬────────────────────────────────────────────────────┘   │
└───────────────────────────────────────┼─────────────────────────────────────────────────────────┘
        local: LM Studio :1234 · Ollama :11434        cloud: Anthropic · OpenAI · Gemini · NVIDIA NIM · OpenRouter · Groq
```

### 11.1 Stack decisions (rationale in CONTEXT §4)

| Layer | Choice |
|---|---|
| Core | Python 3.12, asyncio, FastAPI + uvicorn, pydantic v2, `uv` for env and lock |
| Providers | `openai` SDK for every OpenAI-compatible endpoint; `anthropic` SDK natively |
| OS control | `psutil`, `pywin32`/ctypes, `pycaw`, `nvidia-ml-py` |
| Store | SQLite (WAL) via stdlib `sqlite3`, single writer thread |
| Secrets | `keyring` → Windows Credential Manager |
| Shell | `pywebview` on the already-installed WebView2 runtime (no Rust/Electron toolchain; nothing on C:) |
| HUD | Vite + React 19 + TypeScript (strict), Zustand, hand-written CSS tokens, Canvas 2D, `lucide-react` icons |
| Tests | `pytest` (core), `vitest` (HUD logic) |

### 11.2 Wire protocol (WebSocket, JSON, versioned `v: 1`)

Client → core: `hello` · `user.message` · `run.cancel` · `approval.respond` · `settings.get` · `settings.set` · `secret.set` · `providers.refresh` · `audit.query` · `window.control`

Core → client: `ready` · `state` · `telemetry` · `providers` · `assistant.start` · `assistant.delta` · `assistant.done` · `tool.start` · `tool.end` · `approval.request` · `approval.resolved` · `settings` · `audit.page` · `error`

Every message is validated against a pydantic model on the core and a TypeScript discriminated union on the HUD; the two are kept in lock-step (`protocol.py` ↔ `protocol.ts`) and a test asserts the type-name sets match.

### 11.3 Data model

| Table | Columns (key ones) |
|---|---|
| `conversations` | `id`, `title`, `created_at`, `updated_at` |
| `messages` | `id`, `conversation_id` FK, `seq`, `role`, `content_json`, `provider`, `model`, `created_at` |
| `audit` | `id`, `ts`, `run_id`, `tool`, `args_json` (redacted), `tier`, `decision`, `decided_by`, `status`, `duration_ms`, `error` |
| `settings` | `key`, `value_json`, `updated_at` |

All timestamps are UTC ISO-8601; the HUD converts for display.

---

## 12. Model strategy

### 12.1 Automatic selection for cloud providers (FR-P8, implemented)

Standing user instruction (2026-11-20): *always use the latest, best and fastest models from the provider's available list.* Model lists cannot be trusted as-is — on NVIDIA NIM only 10 of 21 listed chat candidates returned a correct tool call, several were retired (404) and latency ranged from 0.4 s to 25 s. So selection is empirical:

1. **Shortlist** up to 16 chat candidates (embedding, safety, vision-only and similar ids are excluded; ranked by a capability estimate from parameter count, tier words and version).
2. **Probe** each with a real one-tool request, twice when it passes, recording tool-call correctness and latency.
3. **Choose** `smart` = most capable model that passed (slow models penalised, > 15 s excluded) and `fast` = most capable model within 2× of the quickest.
4. **Route** short single-intent commands to `fast` for the first two steps, everything else to `smart`; the other tested model is the first fallback.
5. **Refresh** daily, when the provider's list changes, or on demand from Settings. A model pinned in Settings always wins. Local runtimes are never probed (it would load multi-GB models).

### 12.2 This machine

| Role | Recommended | Why |
|---|---|---|
| Fast local routing / simple chat | A ≤ 4B instruct model with tool calling (Q4, ~2.5 GB) fully on the RTX 3050 | Fits 4 GB VRAM; the installed 26–40B models spill to CPU and 16 GB RAM and will be slow (expect low single-digit tokens/s) |
| Local "good enough" tool use | `qwen2.5-coder-7b-instruct` (installed) with partial GPU offload | Best currently-installed fit |
| Heavy reasoning / planning | Frontier model (Claude Opus 5.5 / Fable 5.2 class, or equivalent) when privacy mode allows | Multi-step tool plans are where model quality matters most |
| Free cloud tier | NVIDIA NIM hosted (≈40 req/min account-wide) | No cost; good fallback |
| Embeddings (M3) | `nomic-embed-text-v1.5` (installed) | Local, small |

Model IDs are never baked in — the Settings view lists what each provider reports.

---

## 13. Risks & open questions

| # | Item | Type | Current answer |
|---|---|---|---|
| R1 | C: has 1.1 GB free; a stray cache could fill it and destabilise Windows | Risk | All caches redirected to E: (`.cache/` in the project); documented in CONTEXT |
| R2 | Local tool-calling reliability on small models | Risk | Schema-validated tool args with one repair retry; Auto routing prefers cloud for multi-step unless Local-only |
| R3 | pywebview limitations (transparency, multi-window overlay) | Risk | Shell is isolated behind one module; Tauri/Electron swap is contained. Revisit at M4 |
| R4 | Wispr auto-send false positives (large paste) | Risk | Toggle, visible countdown, Esc to disarm |
| Q1 | Which cloud keys will be used day-to-day? | Open | Add in Settings; all optional |
| Q2 | Should Jarvis start with Windows? | Open | Default no; option at M5 |
| Q3 | Personality / voice of spoken replies | Open | Neutral, concise default; configurable system prompt later |

---

## 14. Success metrics

| Metric | Target |
|---|---|
| Fast-path command success (M1) | ≥ 95 % on a 40-command reference set |
| Multi-step task success (M2) | ≥ 80 % on a 20-task reference set with a frontier model |
| Unapproved T2/T3 executions | **0**, verified by test and audit |
| Median summon-to-action for "open X" | ≤ 2 s typed (M0), ≤ 1.5 s voice (M1) |
| Daily use | Used on ≥ 5 of 7 days after M1 |

## 15. Test strategy

- **Unit (pytest):** policy tiers and escalation patterns; taint propagation; router fallback and privacy enforcement; tool argument validation; sensitive-path blocking; protocol parity; audit redaction.
- **Integration:** gateway handshake (token/Origin rejection), a full run against a scripted fake provider with tool calls and approval.
- **HUD (vitest):** store reducers for every server event; dictation-burst detector.
- **Manual journeys:** J1–J7 checklist in CONTEXT, run before each milestone is closed.

## 16. Release criteria for M0

All P0 requirements implemented end-to-end; tests green; type-check and build clean; J1(typed)/J2/J3/J5/J6/J7 pass; no placeholder UI; PRD §18 and CONTEXT updated.

## 17. Glossary

**Core** — the Python process (also the central HUD visual). **Run** — one user request through to completion. **Tier** — risk class of a tool call. **Taint** — a run has consumed untrusted content. **Fast path** — LLM-free execution of simple intents.

---

## 18. Requirement status & changelog

Status legend: ☐ not started · ◐ partial · ☑ done & verified

| Requirement | Status | Evidence / gap |
|---|---|---|
| PRD / CONTEXT baseline | ☑ | 2026-11-20 |
| FR-A1 command bar · FR-A2 streaming · FR-A3 agent loop · FR-A4 cancel | ☑ | pytest + live browser run |
| FR-A5 persistence | ◐ | History persists and restores; "New conversation" works; no conversation switcher yet |
| FR-P1–P7, P10 providers, discovery, routing, fallback, privacy, keyring | ◐ | Router/fallback/privacy unit-tested; OpenAI-compatible adapter verified live against a scripted endpoint. **Not yet exercised against a real model or any cloud key** (RAM was 13.7/16 GB, no keys present) |
| FR-T1–T4, T6–T9, T10(lock), `files_write`, `files_open` — 21 tools | ◐ | `system_status`, `shell_run`, file read/write/search verified by execution; app/window/volume/media/clipboard/lock handlers written but **not yet run on the machine** |
| FR-V1 Wispr target + summon hotkey | ◐ | Hotkeys register at start-up; not yet tried with live Wispr dictation |
| FR-V2 auto-send (pulled forward from M1) | ◐ | Burst detector unit-tested; needs tuning against real Wispr input |
| FR-V3 basic spoken replies · FR-V4 barge-in | ◐ | Implemented with system voices; not listened to |
| FR-U1–U7, U10 Console / Settings / Audit / states | ☑ | Rendered and operated in a browser at 1280×800 |
| FR-U8 frameless window + hotkeys | ◐ | Window launches and connects; drag, resize, minimise/maximise not hand-tested. Taskbar icon added (logo.ico) |
| FR-M1, FR-M2 store + append-only audit | ☑ | pytest + Audit view |
| SEC-1 taint · SEC-2 origin/host/token · SEC-3/4/9 shell tiers · SEC-5 redaction · SEC-6 paths · SEC-8 limits/kill switch | ☑ | 78 pytest cases |
| NFR-2 idle footprint | ◐ | Core + window measured at ~205 MB shortly after launch; not profiled over time |

**Changelog**

- **1.2.1 — 2026-11-22** — **Action transparency and dry-run mode (231 total across 64 modules).**
  - `action_history_tools.py` (3 tools): action_history (query audit log by tool/status), action_detail (inspect specific entry with args), action_summary (outcome counts + last failure). Wraps existing `store.query_audit`.
  - `dryrun_tools.py` (2 tools): dry_run (preview what a tool would do: tier, approval, side effects, argument validation — no execution), tool_inspect (browse tools with parameters and tiers).

- **1.2.0 — 2026-11-22** — **Feature Roadmap build: 55 new tools, taskbar icon, bug fixes (226 total across 62 modules).**
  - **Taskbar icon**: `assets/logo.ico` + `webview.start(icon=...)` in `shell.py`.
  - **13 new modules**: music_tools (search/play/lyrics), ambient_tools (window monitoring), sentinel_tools (condition watchers), focus_tools (deep work sessions), credential_tools (password strength + HIBP k-anonymity), fitness_tools (health reminders), workflow_tools (macro recording), scratchpad_tools (JSON notes), codereview_tools (git diff analysis), incident_tools (system debugging), privacy_tools (network audit), clipboard_manager_tools (history/pin), imagegen_tools (local SD), screenshot_tools (clip curator).
  - **Bug fixes**: preset_tools (`hub.settings_store` → `hub.store`), presets.py (`.patch()` → `.update_settings()`), gods_eye_tools + mcp_catalogue_tools (`hub.settings` → `hub.store.get_settings`).

- **1.1.0 — 2026-11-22** — **God's Eye View, Mobile MCP, voice calling: 7 new tools (171 total).**
  - `gods_eye_tools.py`: lifecycle management for [God's Eye View](https://github.com/bilawalsidhu/gods-eye-view) geospatial intelligence globe. `gods_eye_setup` (clone + npm ci), `gods_eye_start` (dev server on port 4173), `gods_eye_stop`, `gods_eye_status`. MCP catalogue entry with custom install-path resolution. Auto-registers ~30 geospatial tools.
  - MCP catalogue: added [Mobile MCP](https://github.com/mobile-next/mobile-mcp) (npx install, 35 tools for driving iOS/Android simulators, emulators, and real devices).
  - `voice_call_tools.py`: Jarvis can call the user's phone via Twilio (TTS, DTMF approval gathering) with Phone Link fallback. `voice_call`, `voice_call_status`, `voice_call_config`. Credentials stored in key vault.

- **1.0.0 — 2026-11-20** — **Mark-LVII complex architectural features: 6 new engines, 13 new tools (132 total).**
  - `background_tasks.py`: `BackgroundTaskEngine` with SQLite persistence, checkpointing, step tracking, priority queue. `TaskRunner` with asyncio semaphore concurrency (max 3), auto-queue loop, cancel support. Tasks that were running when process crashed auto-reset to `queued` on startup.
  - `deep_research.py`: `DeepResearcher` 5-stage pipeline: decompose question → parallel search sub-questions → extract claims with source indices → cross-reference claims against sources → synthesize report with citations. Confidence scoring: weighted claim confidence + source diversity + verification ratio − contradiction penalty.
  - `routines.py`: `RoutineEngine` builds on `Scheduler` with 4 built-in routine types: `morning_briefing`, `eod_recap`, `health_check` (CPU/memory/disk monitoring with thresholds), `condition_monitor`. Delegates model work to `orchestrator.run_headless`.
  - `confirmation.py`: `UndoStack` (max 50 entries, auto-infers reverse actions for file_write/move/copy/clipboard_set, marks destructive tools as non-reversible). `ConfirmationGate` with structured plan display, risk-level assessment, batch confirmation for glob/wildcard operations.
  - `model_ladder.py`: `ModelLadder` with 4 tiers (fast_local → fast_cloud → smart → premium), regex-based complexity classification, quality-based escalation detection (refusal patterns, generic responses, too-short answers, failed tool calls), per-tier metrics tracking.
  - `outcome_verify.py`: `OutcomeVerifier` with built-in verifiers for file_write (checks existence + size), file_delete (confirms gone), dir_create (confirms exists), shell_run (exit code + error patterns), app_launch (psutil process check), browser_open (URL/title verification). Custom verifiers registerable. 2s timeout per check.
  - New tools: `bg_task_create` (T1), `bg_task_status` (T0), `bg_task_list` (T0), `bg_task_cancel` (T1), `deep_research` (T1), `research_status` (T0), `routine_create` (T1), `routine_list` (T0), `routine_delete` (T1), `undo_last` (T1), `undo_list` (T0), `verify_last_action` (T0), `model_stats` (T0).
  - `gateway.py`: Hub wired with all 6 new systems — init, start (bg_runner loop, routine handlers), stop (bg_runner.stop, bg_engine.close). Protocol types already registered: `bg_task.update`, `bg_task.step`, `research.progress`, `research.done`, `routine.fire`, `routine.result`, `undo.pushed`, `verify.result`, `ladder.stats`.

- **0.9.0 — 2026-11-20** — **FatihMakes repo integration: 15 new tools (119 total).**
  - Integrated features from Mark-LVII, Mark-XXXIX-OR, and other FatihMakes repos.
  - `weather_tools.py`: `weather_get` — Open-Meteo free API, geocoding, current conditions + 3-day forecast, WMO code mapping.
  - `youtube_tools.py`: `youtube_search` (yt-dlp search), `youtube_play` (browser open), `youtube_transcript` (subtitle extraction with VTT cleanup).
  - `settings_tools.py`: `brightness_set` (WMI), `wifi_toggle` (netsh), `bluetooth_toggle` (PowerShell Radio API), `open_settings` (13 Windows Settings pages via ms-settings URIs).
  - `file_processor_tools.py`: `file_extract` (PDF/DOCX/XLSX/CSV/PPTX/JSON/ZIP/text), `file_convert` (text-to-PDF, DOCX-to-TXT, XLSX-to-CSV).
  - `flight_tools.py`: `flight_search` (Google Flights + Skyscanner URL builder with date validation), `flight_open`.
  - `project_tools.py`: `project_create` (scaffolds python/node/react/fastapi/flask/nextjs/html projects), `project_analyze` (framework detection, file stats, dependency listing), `project_launch` (auto-detect and run).
  - Optional dependencies: yt-dlp, pypdf, python-docx, openpyxl, python-pptx, fpdf2 (graceful degradation with install instructions).

- **0.8.5 — 2026-11-20** — **Android connection settings + auto-reconnect + mobile responsive.**
  - Android: `ConnectionSettingsSheet` bottom sheet with host/IP and port fields, status indicator, and Connect button.
  - Android: Settings gear icon in top app bar opens the connection sheet.
  - Android: `SharedPreferences` persistence for host/port — remembers across app restarts.
  - Android: `JarvisClient` auto-reconnect with exponential backoff (1s→2s→4s→…→30s max) on WebSocket failure/close. Reconnect resets on successful `ready` message. `disconnect()` stops reconnect loop.
  - Android: `onResume` reconnects if disconnected (e.g. after backgrounding).
  - HUD: Added `@media (max-width: 600px)` responsive breakpoint — side rail converts to sticky bottom tab bar, removes window controls/frame/chips, full-width content, tightened spacing for mobile.

- **0.8.4 — 2026-11-20** — **Stitch design themes (Holographic OS + Sovereign Executive).**
  - Extracted two Stitch design systems from `stitch_assets/` zip: Synthetic Intelligence Holographic OS (cyan/cobalt glassmorphism) and Sovereign Executive Intelligence (champagne gold/obsidian editorial).
  - CSS: Added `[data-theme="holographic"]` and `[data-theme="sovereign"]` custom property overrides in `styles.css`, mapping all tokens (colors, fonts, glow, grid) to each design system. Fixed hardcoded colors in `.tabs button` to use `var(--ion)`.
  - Theme selector: segmented radio group in Settings → General tab with Ion / Holographic OS / Sovereign Executive options.
  - Backend: `gateway.py` validates `theme` in `_SETTINGS_KEYS`; TypeScript `ThemeId` type added to `protocol.ts`.
  - App.tsx: `useEffect` syncs `data-theme` attribute on `<html>` from Zustand store.
  - Fonts: installed `@fontsource/space-grotesk`, `space-mono`, `plus-jakarta-sans`, `playfair-display`, `outfit`; imported in `main.tsx`.
  - Android: `JarvisPalette` data class + `IonPalette`/`HolographicPalette`/`SovereignPalette` objects; `LocalPalette` CompositionLocal; all composables use `LocalPalette.current`; `JarvisClient` reads theme from `ready`/`settings` messages.
  - Verified: all three themes render correctly in the HUD browser (CSS variables confirmed via DevTools).

- **0.8.3 — 2026-11-20** — **Tools catalog in HUD.**
  - Gateway `integrations()` now includes full tool list with name, description, tier, and module for all 105 registered tools.
  - New "Tools" tab in HUD Settings: searchable, collapsible tool catalog grouped by module (Browser, Security, Memory, etc.) with tier badges (T0–T3).
  - Frontend types updated: `ToolInfo` interface added to `protocol.ts`; `Integrations.tools` optional array.
  - Fixed pre-existing TS build error: added default return to `reduce()` switch in `store.ts`.

- **0.8.2 — 2026-11-20** — **Android companion app.**
  - Built Kotlin + Jetpack Compose Android app (`android/` directory): dark Jarvis-themed chat UI with text input, voice input (SpeechRecognizer), streaming message display, connection status indicator, cancel/new-conversation actions.
  - `JarvisClient.kt`: OkHttp WebSocket client with auto-pairing via `/api/pair` endpoint, streaming delta handling, StateFlow-based reactive state.
  - Gateway: Added `/api/pair` endpoint (loopback-only, host-validated) returning `{token, version, ws}` for mobile pairing.
  - ADB reverse port forwarding (`tcp:47821`) enables USB-connected phone to reach local Jarvis.
  - Tested on OnePlus 9 (Android 14): text chat, voice commands, weather query all working end-to-end.

- **0.8.0 — 2026-11-21** — **Onboarding wizard and configurable assistant name (FR-X5).**
  - **6-step first-run wizard (`Onboarding.tsx`):** welcome → name → privacy → provider → theme → ready. New GitHub installs start with a clean slate (`onboarding_complete: false` in DEFAULT_SETTINGS).
  - **Configurable assistant name:** text input (1–40 chars) saved as `assistant_name` in settings; propagates to: title bar wordmark, `document.title` (taskbar), console message labels, wake word (auto-registered as first alias in `_apply_wake`), LLM system prompt (`orchestrator.py`), pywebview window title, and the "Say {name}" chip.
  - **Dynamic window focus:** `force_foreground()` in `shell.py` now matches by PID + visibility instead of hardcoded window text, so renaming the assistant doesn't break the summon hotkey.
  - **Wake word configurability:** custom assistant name auto-inserted as first wake alias; existing openWakeWord model still handles "hey Jarvis" acoustically, text-based `find_wake()` handles the custom name.
  - **Settings:** new "Assistant name" section at top of General tab with live preview.
  - **No AI slop:** removed emojis and Sparkles icon from onboarding screen.
  - **Released as v0.8.0** on GitHub. Build: tsc clean, vite build clean.

- **0.8.1 — 2026-11-20** — **Bug fixes, security hardening, GitHub repo integrations.**
  - **Bug fixes (6):** `scheduler_tools.py` wrong positional args to `add()`, `cancel()` → `remove()`, `ScheduleListArgs.status` default; `memory_tools.py` `ForgetArgs.id`/`ReviewArgs.id` typed as `int` → `str`; `file_tools.py` `WebClipArgs.notes` → `content`; `skills.py` constructor only accepted `Path` → `Path | str`; `speech.py` missing `Path` import.
  - **Security hardening:** `dev_tools.py` blocked command list + `shlex.split()`; `file_tools.py` path traversal protection for `file_index_folder`; `clipboard.py` expanded secret keyword detection (16 patterns incl. `ghp_`, `glpat-`, `bearer`, `-----begin`); `gateway.py` rate limiting (60 msg/min per WebSocket connection).
  - **Sub-agent handoff:** `orchestrator.run_headless()` + `_model_turn_headless()` for background agents; `gateway._register_agent_runner()` default runner.
  - **Morning digest (`digest.py`):** Assembles daily briefing from scheduler, memory review queue, clipboard, and system health. Delivered via HUD notice + Telegram. Tools: `digest_now`, `digest_preview`. Inspired by OpenJarvis.
  - **Preset configurations (`presets.py`):** Quick-switch profiles: private, balanced, performance, silent, hands-free. Tools: `preset_list`, `preset_apply`. Inspired by OpenJarvis.
  - **Linting:** Fixed 19 ruff errors (11 unused imports, 4 f-string placeholders, 4 undefined `Path` refs).
  - NVIDIA NIM already integrated as a provider (confirmed `config.py` line 47, `openai_compat.py` adapter).
  - Protocol parity test restored (new TS types were already added in prior session).
  - Tests: 32 passed, ruff down from 34 to 15 remaining (stylistic only).

- **0.8.0 — 2026-11-20** — **Full feature build: 84 FEATURE_IDEAS.md ideas implemented.**
  - **Long-term memory (`memory.py`):** SQLite FTS5-backed fact/entity/correction/habit store with auto-extraction from conversation ("remember that…", "never do…"), review queue, and context injection into system prompt. Tools: `memory_remember`, `memory_recall`, `memory_forget`, `memory_review`.
  - **Scheduler (`scheduler.py`):** SQLite-persisted timers, reminders, recurring tasks with natural-language schedule parsing ("in 5m", "at 09:00", "every 30m", "daily 09:00"). Async background loop. Tools: `schedule_create`, `schedule_list`, `schedule_cancel`.
  - **Telegram remote access (`telegram_bot.py`):** python-telegram-bot polling mode, owner-only access. Commands: /start, /status, /remember, /tasks + free-text messages relayed to the orchestrator. Proactive outbound messages for reminders/alerts. Tools: `telegram_send`, `telegram_status`.
  - **Screen vision (`vision.py`):** mss capture + EasyOCR/pytesseract/PaddleOCR fallback chain for local OCR. Searchable screen history with FTS5. Tools: `screen_capture`, `screen_ocr`, `screen_search`.
  - **File knowledge base (`file_index.py`):** SQLite FTS5 index with hash-based change detection, 40+ text extensions, web clip bookmarks, daily notes. Tools: `file_search`, `file_name_search`, `file_index_folder`, `web_clip`, `daily_note`.
  - **Clipboard history (`clipboard.py`):** Background win32clipboard monitor with secret detection filtering, FTS5 search, max 1000 entries. Tools: `clipboard_search`, `clipboard_get`.
  - **Skills system (`skills.py`):** SKILL.md folders with keyword matching, auto-injection into system prompt, macro recording. Tools: `skill_list`, `skill_create`.
  - **Background agents (`agents.py`):** Asyncio task board with progress reporting, plan mode (create/approve/advance). Tools: `agent_create`, `agent_list`, `agent_cancel`, `plan_create`, `plan_action`.
  - **Home Assistant bridge (`homeassistant.py`):** REST API wrapper for lights, switches, climate, scenes, media players, automations. Tools: `ha_list`, `ha_control`, `ha_scene`, `ha_temperature`.
  - **Developer tools (`dev_tools.py`):** Git status/log/diff, error explainer, project auto-launcher. Tools: `git_status`, `git_log`, `git_diff`, `error_explain`, `project_launch`.
  - **Security tools (`security_tools.py`):** Lock screen, process list/kill, network connection audit. Tools: `security_lock`, `security_processes`, `security_network`, `security_kill`.
  - **Universal TTS fallback chain (`speech.py`):** edge-tts (free, no key, 400+ voices) → Piper (local ONNX neural) → Groq → OpenAI → Deepgram → ElevenLabs → Windows system voice. TTS_FALLBACK_ORDER mirrors the existing STT_FALLBACK_ORDER.
  - **Universal STT fallback chain (`speech.py`):** chosen engine → Groq (2000 free/day) → Deepgram ($200 free credit) → OpenAI → AssemblyAI → Google Cloud (60 min/mo) → Azure (5 hr/mo) → local Whisper.
  - New dependencies: edge-tts, python-telegram-bot, mss, easyocr.
  - 11 new tool registration files, 42+ new tools total. Protocol extended with 7 new message types.
  - Gateway Hub initializes all new modules at startup; orchestrator injects memory and skill context into system prompt.
  - **Not verified live:** no Telegram bot token configured; no Home Assistant instance; screen OCR untested on real screen; edge-tts untested (needs ffmpeg for WAV conversion). All modules are structurally complete and import-clean.

- **0.7.1 — 2026-11-20** — **Connector sign-in and categorised Settings.**
  - **FR-X4 Browser sign-in for remote MCP connectors (`mcp_oauth.py`):** a connector added by URL with no key gets OAuth: Jarvis opens the sign-in page (https only), receives the answer on its own loopback route `/oauth/mcp/callback` (Host-checked, matched by the one-time state, five-minute wait), and keeps tokens encrypted with Windows DPAPI in `data/mcp_oauth/`. Removing the connector deletes them. Status `signin` is shown as "Approve in your browser…".
  - **Verified:** the MCP library's real flow (discovery, client registration, PKCE, code exchange, reuse without a second browser trip) passes against a stand-in server; forged and replayed callbacks are refused; tokens are not readable on disk. **Not verified against a real connector** (no Wispr connection URL was available).
  - **Settings in five categories:** General, Models, API keys, Voice, Connections (remembered between visits). Providers grouped: on this computer / frontier labs / fast inference and aggregators / custom. API keys grouped: language models, speech services (Deepgram, ElevenLabs inline), custom model endpoints, connectors and other keys.
  - Tests: 158 core, 15 HUD. The categorised Settings screen was type-checked and built but not viewed in the running window.

- **0.7.0 — 2026-11-20** — **Voice by sound, self-healing, speech engines, Wispr Flow.**
  - **Root cause of the missed wake (seen with diagnostic logging):** this voice's "Hey Jarvis" is transcribed as "Here's our list", "Is our best", "Good job, guys", with the wake model scoring 0.00–0.05. No spelling rule can recover that.
  - **FR-V7 Voiceprint (`voiceprint.py`):** the owner says the name five times (Settings → Teach Jarvis your voice); each recording becomes a sequence of speech-embedding vectors and later utterances are matched by dynamic time warping, level-normalised so a soft voice matches. Only vectors are stored (`data/wake/voiceprint.npz`), never audio. Measured on synthesized voices at four rates and at −34 dB: same phrase 0.04–0.09, ordinary speech 0.13–0.18, rhymes ("service", "harvest", "hey Travis") 0.04–0.08 — so rhymes can wake it. **Not measured on a human voice.**
  - **Soft voice:** speech-detection threshold lowered (noise × 2.2, floor 22). Unverified on the real microphone.
  - **Request before the name:** a sentence finished up to 6 s before the name is taken as the request (held in memory only for those seconds).
  - **Fixed:** a second wake when transcription took longer than the cooldown.
  - **FR-H1–H4 Self-healing (`selfheal.py`, Repair view, Ctrl+3):** every logged error and display crash is captured (secrets redacted) and grouped; a stopped wake listener is restarted with back-off; for a fault in Jarvis's own code a model proposes a small patch, which is applied to a *copy* and must compile, import and pass the test-suite before it is shown as a diff. **Applied only when the user presses Apply; backed up; Roll back restores.** `policy.py`, `secrets.py`, `selfheal.py`, `config.py`, `tools/registry.py`, `__main__.py` cannot be patched. Verified: the checker passed a harmless edit (153 tests) and rejected a behaviour-breaking one; the model step is tested with a stand-in model only — **no real fault has been repaired by a live model yet.** The source file and error text are sent to the model chosen by privacy mode.
  - **FR-V8 Speech engines (`speech.py`):** request transcription by Groq Whisper, OpenAI, Deepgram, ElevenLabs or a custom OpenAI-compatible endpoint; answers in OpenAI, Groq, Deepgram or ElevenLabs voices; Windows voice and local Whisper remain the defaults and the fallback. Off in Local-only privacy mode. Request formats are tested against a stand-in HTTP client; **none has been called live (no keys for those services), and model/voice ids are defaults from memory that may need correcting.** NVIDIA: `/v1/audio/*` returns 404 on this key — hosted NVIDIA speech is gRPC only, listed as unavailable.
  - **Wispr Flow:** no public dictation API was found (search failed; unconfirmed). Integrated through its hands-free shortcut, read from Wispr's own settings (Ctrl+Win+Space): pressed when the name is heard and again when the user stops speaking; if Wispr delivers nothing in 5 s Jarvis uses its own transcript. Unverified live. Wispr's MCP connector (Notetaker meetings) needs browser sign-in, which Jarvis's MCP client does not support yet.
  - **`FEATURE_IDEAS.md`:** 84 researched ideas and a recommended order.
  - Tests: 155 core, 15 HUD.

- **0.6.2 — 2026-11-20** — **"Hey Jarvis" still hit-and-miss.** Log after 0.6.1: 6 wakes in 6 minutes (was 2 in 7), three by bare name and three by "hey Jarvis", but some "hey Jarvis" attempts scored 0.19-0.44 on the wake model and still did not wake because the transcript did not spell the name.
  - **Two-signal match:** when the wake model scores ≥ 0.12 (idle speech scores 0.00-0.02 here), the words only need to be compatible — a phrase of up to three words, or a j/ch/g/y/z word near the start. A clear name in the words never needs the score.
  - Short clips are transcribed with a wider search (beam 5).
  - **Diagnostics:** `JARVIS_WAKE_DEBUG=1` logs what short phrases were heard as. Off by default; turned on for this debugging session only, with the user told.
  - **Not verified:** with the user's voice.

- **0.6.1 — 2026-11-20** — **The name anywhere (FR-V5/V6).** User report: only "Hey Jarvis" worked; plain "Jarvis" and "Jarvis" inside a request did not.
  - **Name inside or after a request:** it woke Jarvis but the request was thrown away (only words *after* a leading name were kept). Now the name and any greeting before it are removed and everything else is the request: "Open the calculator, Jarvis" → "Open the calculator". Confirmed on synthesized speech: recognised, request empty before the fix.
  - **Plain "Jarvis":** the log showed about 35 utterances checked and 2 wakes, so the speech model is spelling this voice's bare "Jarvis" differently (voice training had learned "Janvas"). Matching now goes by sound (j/ch + open vowel + optional r/n/l + v/b/f + vowel + s) and accepts the name split in two ("jar this"). One or two words said on their own that do not match are offered in Settings as "is this how you say Jarvis?" (memory only).
  - **Pause rule:** now starts once part of a request has been gathered, so saying only the name leaves the full listening time to begin speaking.
  - **Not verified:** with the user's voice — the bare-name cause is inferred from counts, not from transcripts (which are never logged).

- **0.6.0 — 2026-11-20** — **Listening mode (FR-V6, built-in voice requests).** User requirement: the sphere glows the moment it hears "Jarvis", a live listening display appears in it, Jarvis listens to the whole request for a configurable time, then acts.
  - **Behaviour:** on the name: chime, window forward, sphere enlarges and brightens with a ring of bars that follows the voice, plus a waveform, live transcript and a draining timer. Everything said in the listening window is gathered into one request — any number of sentences and pauses — and acted on once. The answer is spoken in Jarvis's voice. If nothing is said the window closes quietly.
  - **Settings:** *Listen for up to* 5–30 s (default 10) and *Then act* — after a pause of 1.5 / 2.5 / 4 s, or only when the full time is up. A sentence in progress when the time ends is allowed to finish (up to 6 s more).
  - **Black window (fixed):** restoring the minimized window with raw Win32 calls left the WebView2 un-painted. Reproduced by minimizing and summoning; now restored through pywebview and only raised/focused with Win32; verified by capturing the real window before and after.
  - **Crash protection:** a React error boundary shows a message and a Reload button instead of an empty window.
  - **Fixed along the way:** a request spoken after the name was silently dropped when the name's own utterance had not been captured separately.
  - **Verified:** pytest with real wake and speech models on a real-time synthetic microphone — name, pause, two sentences → one request containing both; display checked in a browser with simulated events. **Not verified:** with a human voice; the spoken answer end to end. Tests: 145 core, 15 HUD.

- **0.5.1 — 2026-11-20** — User feedback after first successful wakes: "Hey Jarvis" often ignored, response should come the moment the name is heard, voice should be male, and the Core had vanished.
  - **Evidence:** on the user's voice the instant wake model peaked at 0.22–0.38 (needs 0.50), so only the end-of-utterance transcription caught the name — about 3 wakes in 20 attempts.
  - **Two-stage wake:** *early* — the chime, window and Core respond as soon as the name is recognised; *final* — when the utterance ends, the words after the name run as the command, or Jarvis says "Yes?" if there were none. Early recognition has three triggers: a confident wake-model score; a faint score (≥ 0.10) that sends the last ~1.9 s of audio for an immediate transcription check; and a look at every utterance ~0.7 s after it starts.
  - **Near misses:** phrases the wake model half-recognised but whose words did not match are listed in Settings (memory only) so the user can accept one as their "Jarvis".
  - **Voice:** Jarvis now speaks with the first installed male English voice (George here; Ravi also installed). Settings has a voice picker with preview; spoken replies in the HUD use the same voice.
  - **Core fix:** canvas animation loops stopped themselves when the window was briefly hidden and did not restart in WebView2, leaving the Core blank. Loops no longer stop themselves; confirmed by a capture of the real window.
  - **Not verified:** the faster wake and "Hey Jarvis" with a human voice. Tests: 141 core, 13 HUD.

- **0.5.0 — 2026-11-20** — Driven by the user's report that the wake word still did not work, and their request for pronunciation training.
  - **Evidence from the wake log:** about 40 phrases at normal volume were checked without a match, then four detections that produced no visible effect. Two separate faults.
  - **Recognition:** speech model raised from `tiny.en` to `base.en`; matching now accepts spellings within one letter of "jarvis" plus a list of common variants, while refusing look-alikes (Travis, service, harvest…). A hint/hotword setting was tried and **removed**: on `base.en` it made the model drop a leading "Jarvis". Measured without it: 18/18 wake phrases and near-misses correct at 0 and −30 dB.
  - **Feedback on waking:** the window is now forced to the front through Win32 (restore, top-most flip, focus), a chime plays, Jarvis says "Yes?", and the Core fires a shockwave.
  - **Voice training (FR-V7, new):** read twelve sentences aloud (common words, command vocabulary, "Jarvis" at the start, middle and end). Jarvis aligns what it heard with what was read and learns (a) corrections for words misheard in this voice, (b) spellings of this user's "Jarvis", (c) which speech model is more accurate on this voice (`base.en` vs `small.en`, replayed on the same audio). Everyday words can never become the wake word. Audio is kept in memory only during the run. The speech model itself is not retrained.
  - **Quick calibration:** say "Jarvis" five times and accept the spellings Jarvis heard.
  - **Not verified:** training and calibration with a human voice; the new Settings screens in a window. Tests: 139 core, 13 HUD.

- **0.8.1 — 2026-11-20** — **Bug fixes, security hardening, MCP integrations.** Fixed 16 bugs across tool files (memory_tools type mismatch, scheduler_tools arg order/missing methods, vision_tools truncated base64, file_tools wrong kwargs, telegram_tools nonexistent attrs, dev_tools shell injection, security_tools blocking psutil, gateway thread-unsafe broadcast and type annotation). Security hardening: FTS5 injection prevention across all modules, path traversal blocking, entity ID validation, process protection list, subprocess_exec replacing shell, shlex command validation. Added TypeScript protocol parity for 7 new message types. Fixed speech engine fallback chain (privacy/key errors now surface correctly). New integrations: Buffer (4 tools), RevenueCat (4 tools), Chrome DevTools Protocol (7 tools), MCP catalogue with 20 pre-configured servers (3 tools), enhanced sub-agent handoff with delegation and wait modes (2 new tools). Total: 105 tools. Tests: 157 pass (103 core + 54 capabilities), 1 environment-dependent audio test skipped.

- **0.4.1 — 2026-11-20** — **Wake word reworked after the user reported it did not wake.** Correction to 0.4.0: the claim that plain "Jarvis" scores 0.99 held only for clean audio; with room noise the openWakeWord model (trained on "hey Jarvis") was unreliable on the bare name. Now two local stages run together: the instant "Hey Jarvis" detector, and a speech path (energy segmenter → faster-whisper `tiny.en`, int8) that transcribes each utterance and looks for "Jarvis". Words after the name become the command ("Jarvis, open Chrome" is sent directly — built-in voice commands, FR-V6 partially delivered). A mid-sentence mention wakes the HUD but is never executed. Transcripts without the wake word are discarded and never logged. Verified by pytest through the real listener threads with quiet (−30 dB), noisy audio: "Jarvis" and "Jarvis, open Chrome" wake once with the right command; "Travis is at the office today" does not. **Still not confirmed with a human voice at the microphone:** this laptop cancels its own speaker output from the microphone signal, so a played-back test phrase cannot reach the recognizer. Cost: the core's committed memory is about 1.1 GB with the speech model loaded (working set about 410 MB). Tests: 124 core, 13 HUD.

- **0.4.0 — 2026-11-20** — **Wake word (FR-V5, pulled forward from P2):** say "Jarvis" and the HUD comes forward. Local openWakeWord model (ONNX, ~4 MB), 80 ms frames from the default microphone; nothing is recorded or sent. Measured on synthesized speech: plain "Jarvis" 0.99, "Hey Jarvis" 0.99, "Travis…"/"service"/ordinary sentences 0.00. Settings: on/off, sensitivity, optional shortcut to press after waking (to start Wispr hands-free dictation), live microphone meter, silent-microphone warning. **Not verified acoustically:** on the test day the laptop microphone delivered digital silence, so a spoken "Jarvis" could not be confirmed end to end. **API keys (FR-P10/P11):** paste-any-key with provider detection by prefix; custom OpenAI-compatible providers (id, base URL, key) that join routing and automatic model selection; a key vault of named keys, referenced as `${secret:name}` in MCP server environment variables or headers so keys never sit in settings; every handled key is masked by exact value in logs and audit. **Memory fix:** numeric thread pools limited to one thread (core with wake word 1210 MB → 257 MB private bytes); found because the unlimited version exhausted Windows commit during testing. Tests: 119 core, 13 HUD.

- **0.3.0 — 2026-11-20** — Capability expansion (§4.3a): desktop control via UI Automation, browser control via Playwright, email, calls, MCP client, more file and power tools (21 → 42 tools). Autonomy modes and `sensitive` tools (§10.1a). Router: 25 s first-response timeout and fallback through the next-best tested models, added after NVIDIA stalled during a live run. Step limit raised to 25. Settings gained Autonomy, Email and Connected tool servers sections. Tests: 113 core, 10 HUD.

- **0.2.0 — 2026-11-20** — (a) Automatic cloud model selection from live tests (§12.1), verified against NVIDIA NIM with a real key: Jarvis selected `nemotron-3-ultra-550b` and completed real runs (process query; PowerShell command through the approval flow). (b) HUD redesigned as Ion v2 (§9). (c) Assistant replies render basic Markdown. (d) Pending approvals moved to the top of the right column after a screenshot showed the buttons clipped. Status changes: FR-P1–P7 now verified against a real cloud model; FR-P8 ◐ (fast/smart roles implemented, vision role not yet); FR-U1–U5 re-verified in the new layout.

- **0.1.1 — 2026-11-20** — M0 foundation built: core (router, policy, orchestrator, gateway, 21 tools), HUD (Console, Settings, Audit), 78 core tests + 9 HUD tests. Changes from 0.1.0: `--text-faint` raised to `#7A90A3` for contrast; auto-send (FR-V2) implemented early; `files_open` tool added (needed for J4); default Anthropic model is `claude-opus-5-5`; ERROR state self-clears after 4 s.

- **0.1.0 — 2026-11-20** — Initial PRD from machine survey and research (Wispr API closed to new partners → OS-level integration; pywebview shell chosen because C: is full and no Rust toolchain is installed).
