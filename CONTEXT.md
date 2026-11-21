# JARVIS — Engineering Context

Living state of the project: what the machine is, what was decided and why, how to run it, and what is done. Read this first in any new session; update it at the end of every session. Product intent lives in [PRD.md](PRD.md).

**Last updated:** 2026-11-21

---

## 1. What this is

A single-user Windows desktop assistant: a Python core that routes requests to local / NVIDIA / frontier LLMs and executes tiered, audited tools on the machine, with a React HUD hosted in a WebView2 window.

## 2. Machine facts (measured 2026-11-20)

| Item | Value | Consequence |
|---|---|---|
| OS | Windows 11 Home SL, build 26300 | Win32 + UI Automation available |
| CPU | i5-12500H, 12 cores / 16 threads | Fine for the core; CPU-only LLM inference is slow |
| RAM | 15.7 GB | Idle budget 450 MB; cannot hold a 17 GB model comfortably |
| GPU | RTX 3050 Laptop, **4 GB VRAM**, driver 616.87; Intel Iris Xe | Only ≤ 4B models fit fully on GPU |
| Display | 1 × 1536×864 (logical) | HUD reference size 1280×800; must work down to 1100×680 |
| **C:** | **1.1 GB free** | **Never write caches or data to C:** |
| D: / E: | 146 GB / 102 GB free | Project, caches and data on E: |
| Python | 3.13 (default), 3.14, uv-managed 3.12 / 3.11; `uv` 0.11.16 | Project pins 3.12 via uv |
| Node | 24.16, npm 11.13 | HUD build |
| Rust | rustup present, **no toolchain** (`cargo` missing) | Tauri not viable without ~5 GB on C: by default |
| WebView2 | 157 installed | Shell runtime already present |
| LM Studio | 0.4.19, CUDA 12 llama.cpp runtime, server **not running** at survey time | Start with `lms server start` |
| Ollama | not installed | Supported but optional |
| Wispr Flow | 1.6.1568, running | OS-level dictation target |
| Other | Docker 29.6, ffmpeg 9, git 2.56, gh 2.93, VS Build Tools 2022 | |

**Installed local models (LM Studio):** `google/gemma-4-26b-a4b-qat` (15.6 GB), `qwen/qwen3.6-27b` (17.5 GB), `qwen2.5-coder-7b-instruct-abliterated` (4.5 GB), `qwen3-coder-30b-a3b-instruct-heretic` (11.3 GB), two further 27B/40B Qwen fine-tunes, and `text-embedding-nomic-embed-text-v1.5`. Only the 7B is a practical interactive fit; a ≤ 4B tool-calling model is recommended for fast local use.

**Environment:** `ANTHROPIC_BASE_URL` is set in the user environment (the Anthropic SDK will honour it). No provider API keys are present in environment variables — keys are entered in Settings and stored in Windows Credential Manager.

## 3. Repository layout

```
E:\Python\Jarvis
├── PRD.md / CONTEXT.md / README.md
├── pyproject.toml, uv.lock          Python project (package: jarvis)
├── src/jarvis/
│   ├── __main__.py                  entry: starts core + shell
│   ├── config.py                    paths, defaults, provider catalogue
│   ├── protocol.py                  WS message models (mirror: ui/src/protocol.ts)
│   ├── gateway.py                   FastAPI app, WS endpoint, token/Origin checks
│   ├── orchestrator.py              runs, agent loop, state machine, approvals
│   ├── policy.py                    tiers, escalation patterns, taint, sensitive paths
│   ├── store.py                     SQLite: conversations, messages, audit, settings
│   ├── secrets.py                   keyring wrapper
│   ├── telemetry.py                 psutil / NVML sampler
│   ├── hotkeys.py                   Win32 RegisterHotKey thread
│   ├── shell.py                     pywebview window
│   ├── providers/                   base, openai_compat, anthropic, router
│   └── tools/                       registry + system, apps, windows, files, shell, media, web, clipboard, power
├── tests/                           pytest
├── ui/                              Vite + React + TS HUD (build → ui/dist, served by gateway)
├── data/                            SQLite DB, logs (git-ignored)
└── .cache/                          uv + npm caches (git-ignored) — keeps C: untouched
```

## 4. Decision log (ADRs)

| # | Date | Decision | Why | Revisit when |
|---|---|---|---|---|
| 1 | 2026-11-20 | **Python core**, not Node/Rust | Best OS-automation and ML ecosystem; the user's whole workspace is Python | — |
| 2 | 2026-11-20 | **pywebview on WebView2** as the shell | C: is full and no Rust toolchain exists; WebView2 is already installed; one process, one language. Shell is isolated in `shell.py` | M4 overlay/transparency needs → Tauri or Electron |
| 3 | 2026-11-20 | **WebSocket-only protocol** between HUD and core | Streaming + server push + one auth surface; no state-changing REST for a browser to CSRF | — |
| 4 | 2026-11-20 | **`openai` SDK for all OpenAI-compatible providers**, `anthropic` SDK natively | LM Studio, Ollama, NVIDIA NIM, OpenAI, Gemini (compat endpoint), OpenRouter, Groq share one adapter | A provider needs a feature the compat layer hides |
| 5 | 2026-11-20 | **Runtime model discovery**, no hard-coded model IDs | Model catalogues change monthly | — |
| 6 | 2026-11-20 | **Policy in the core**: tier + argument patterns + taint | The model and the UI are both untrusted for authorisation | — |
| 7 | 2026-11-20 | **Wispr Flow at OS level** (dictation into the command bar) | Wispr's Voice API is closed to new partners | Wispr opens the API |
| 8 | 2026-11-20 | **Keys in Windows Credential Manager** via `keyring` | No secrets in files, DB, logs or the UI bundle | — |
| 9 | 2026-11-20 | **Hand-written CSS tokens**, no Tailwind/component kit | The "Ion" look is custom; fewer dependencies | UI grows past ~30 components |
| 10 | 2026-11-20 | Caches redirected to `E:\Python\Jarvis\.cache` (`UV_CACHE_DIR`, `npm_config_cache`) | C: has 1.1 GB free | C: is cleaned up |

## 5. How to run

```powershell
# one-time (from E:\Python\Jarvis) — scripts\setup.ps1 does all of this
$env:UV_CACHE_DIR = "E:\Python\Jarvis\.cache\uv"; $env:npm_config_cache = "E:\Python\Jarvis\.cache\npm"
uv sync
npm --prefix ui install
npm --prefix ui run build

# run
uv run jarvis            # core + HUD window
uv run jarvis --no-window   # core only (open the printed URL in a browser for development)
uv run pytest
```

Local models: `lms server start` (LM Studio on `127.0.0.1:1234`). Cloud keys: Settings → Models.

Hotkeys: `Ctrl+Alt+J` summon · `Ctrl+Alt+Backspace` kill switch.

## 6. Build status

See the end of this file — section 9 is rewritten each session.

## 7. Conventions

- Python: type hints everywhere, `ruff` defaults, no bare `except`, async for I/O, blocking Win32 calls via `asyncio.to_thread`.
- Tool names are `snake_case` (provider-safe: `^[a-zA-Z0-9_-]{1,64}$`), grouped by module.
- Every tool: pydantic args model → `Tier` → handler returning a JSON-serialisable result; failures raise `ToolError` with a user-safe message.
- Timestamps UTC ISO-8601 in storage and on the wire.
- `protocol.py` and `ui/src/protocol.ts` change together, in the same commit.
- No runtime network fetch from the HUD other than the loopback WebSocket.
- Requirement IDs from the PRD (`FR-…`, `SEC-…`) are cited in docstrings/tests where behaviour is non-obvious.

## 8. Known gaps & next steps

Maintained in section 9.

## 9. Session log

### 2026-11-20 — Mark-LVII complex architectural features, 13 new tools (v1.0.0)

**Done**
- Built 6 new core engines and 4 tool modules with 13 new tools (132 total):
  - `background_tasks.py` — BackgroundTaskEngine (SQLite, checkpointing, priority queue) + TaskRunner (semaphore concurrency, auto-queue)
  - `deep_research.py` — DeepResearcher 5-stage pipeline (decompose→search→claims→cross-reference→synthesize) with confidence scoring
  - `routines.py` — RoutineEngine: morning_briefing, eod_recap, health_check, condition_monitor; builds on Scheduler
  - `confirmation.py` — UndoStack (auto-infer reverse actions) + ConfirmationGate (structured plan display, risk levels)
  - `model_ladder.py` — ModelLadder with 4 tiers, complexity classification, quality-based escalation, per-tier metrics
  - `outcome_verify.py` — OutcomeVerifier with built-in verifiers for file/shell/process/browser ops, custom registration
  - Tool modules: `background_task_tools.py` (4 tools), `research_tools.py` (2), `routine_tools.py` (5), `verification_tools.py` (2)
- Wired all into `gateway.py` Hub: init, start (bg_runner loop, routine handlers), stop (cleanup)
- Added 9 new protocol server message types in `protocol.py`
- Used 4 parallel fork agents for the build, all verified clean

**Files touched:** `protocol.py` (server types), `gateway.py` (imports, Hub init/start/stop), `tools/__init__.py` (4 new imports), plus 8 new files: `background_tasks.py`, `deep_research.py`, `routines.py`, `confirmation.py`, `model_ladder.py`, `outcome_verify.py`, `tools/background_task_tools.py`, `tools/research_tools.py`, `tools/routine_tools.py`, `tools/verification_tools.py`.

### 2026-11-20 — FatihMakes repo integration, 15 new tools (v0.9.0)

**Done**
- Researched all 8 FatihMakes repos (Mark-LVII, Mark-XXXIX-OR, Mark-X.1, Mark-X, etc.). Mark-LVII is the flagship.
- Built 6 new tool modules with 15 tools total (119 tools now):
  - `weather_tools.py` — weather_get (Open-Meteo API, geocoding, 3-day forecast)
  - `youtube_tools.py` — youtube_search, youtube_play, youtube_transcript (yt-dlp)
  - `settings_tools.py` — brightness_set, wifi_toggle, bluetooth_toggle, open_settings (13 pages)
  - `file_processor_tools.py` — file_extract (PDF/DOCX/XLSX/CSV/PPTX/JSON/ZIP), file_convert
  - `flight_tools.py` — flight_search (Google Flights + Skyscanner URLs), flight_open
  - `project_tools.py` — project_create (7 templates), project_analyze, project_launch
- Installed optional deps: yt-dlp, pypdf, python-docx, openpyxl, python-pptx, fpdf2
- Registered all in `tools/__init__.py`

**Files touched:** `tools/__init__.py`, plus 6 new files: `weather_tools.py`, `youtube_tools.py`, `settings_tools.py`, `file_processor_tools.py`, `flight_tools.py`, `project_tools.py`.

### 2026-11-20 — Android connection + mobile responsive (v0.8.5)

**Done**
- Added `ConnectionSettingsSheet` composable: modal bottom sheet with host/IP input, port input, connection status dot, and Connect button — themed with `LocalPalette`.
- Settings gear icon added to top app bar for quick access.
- `SharedPreferences` stores host/port, loaded on `onCreate`, saved on connect. Default host set to `192.168.1.100` for typical LAN use.
- `JarvisClient`: auto-reconnect with exponential backoff (1s, 2s, 4s, 8s, 16s, 30s cap). Resets on successful `ready`. Explicit `disconnect()` cancels reconnect.
- `onResume` lifecycle hook reconnects if the app was backgrounded.
- HUD mobile responsive: `@media (max-width: 600px)` breakpoint — bottom tab bar, full-width content, hidden window controls/frame, `100dvh` height, tightened spacing.

**Files touched:** `JarvisClient.kt` (reconnect logic, host/port tracking), `MainActivity.kt` (SharedPreferences, ConnectionSettingsSheet, onResume, settings icon), `styles.css` (mobile breakpoint).

### 2026-11-20 — Stitch design themes (v0.8.4)

**Done**
- Extracted two Stitch design systems from `stitch_assets/` zip and mapped them to CSS custom properties.
- Added `[data-theme="holographic"]` and `[data-theme="sovereign"]` overrides in `styles.css`: full token set (colors, fonts, glow, grid).
- Theme selector in Settings → General: segmented radio group (Ion / Holographic OS / Sovereign Executive).
- Backend: added `theme` to `_SETTINGS_KEYS` with validation in `gateway.py`.
- `App.tsx`: syncs `data-theme` attribute on `<html>` from Zustand store setting.
- Installed font packages (`@fontsource/space-grotesk`, `space-mono`, `plus-jakarta-sans`, `playfair-display`, `outfit`) and imported in `main.tsx`.
- Android: `JarvisPalette` data class with three palette objects, `LocalPalette` CompositionLocal, all composables migrated from hardcoded color vals to `LocalPalette.current`. `JarvisClient` reads and reacts to theme changes.
- Fixed hardcoded `#38b6ff` in `.tabs button` styles to use `var(--ion)`.

**Files touched:** `styles.css` (theme blocks + tab fixes), `Settings.tsx` (theme selector), `App.tsx` (theme effect), `main.tsx` (font imports), `protocol.ts` (ThemeId type), `gateway.py` (settings key + validation), `MainActivity.kt` (palette system + composable migration), `JarvisClient.kt` (theme StateFlow).

### 2026-11-20 — Tools catalog in HUD (v0.8.3)

**Done**
- Added full tool catalog to gateway `integrations()` response: each tool sends `{name, description, tier, module}`.
- Module derived from handler's `__module__` at runtime (e.g. `browser`, `security`, `memory`).
- New "Tools" tab in HUD Settings with search, collapsible module groups, tier badges (T0–T3 color-coded).
- Added `ToolInfo` interface and `tools?: ToolInfo[]` to `Integrations` in `protocol.ts`.
- Added CSS for `.tool-group`, `.tool-list`, `.tool-item`, tier color classes.
- Fixed pre-existing TS error: `reduce()` in `store.ts` had no default case — added `default: return {}`.

**Files touched:** `gateway.py` (line 650), `protocol.ts` (line 42), `store.ts` (line 294), `Settings.tsx` (new ToolsCatalog component + tab), `styles.css` (tool catalog styles).

### 2026-11-20 — Android companion app (v0.8.2)

**Done**
- Built Android app at `android/`: Kotlin + Jetpack Compose, OkHttp 4.12.0 WebSocket, Material3, targeting SDK 34, min SDK 26.
- `MainActivity.kt`: Full Compose UI — dark Jarvis theme (#0A0E17 bg, #00D4FF accent), chat message list with streaming support, voice input via SpeechRecognizer, animated status dot, error banner, cancel button, new conversation action.
- `JarvisClient.kt`: WebSocket client — auto-pairs via `/api/pair` endpoint, handles hello/ready/state/user.echo/assistant.start/delta/done/error message types, MutableStateFlow for reactive connection/messages/agent state.
- Gateway: `/api/pair` GET endpoint at `gateway.py:795` — returns `{token, version, ws}` for mobile pairing, restricted to `allowed_hosts()`.
- ADB reverse port forwarding: `adb reverse tcp:47821 tcp:47821` lets USB-connected phone reach local Jarvis.
- Tested on OnePlus 9 (LE2111, Android 14, device `e3b9507e`): text chat, voice commands, weather query all working end-to-end. Debug APK 16.9 MB.
- Adaptive icon: vector "J" logo in Jarvis cyan on dark background.

### 2026-11-20 — Bug fixes, security hardening, repo integrations (v0.8.1)

**Done**
- Fixed 16 bugs total: scheduler_tools wrong args/method, memory_tools int→str id types, file_tools field name mismatch, skills.py Path|str constructor, speech.py missing Path import, vision_tools truncated base64, telegram_tools nonexistent attributes, dev_tools shell injection, security_tools blocking psutil, gateway thread-unsafe broadcast + type annotation, homeassistant_tools missing entity validation.
- Security hardening: FTS5 injection prevention (all modules), blocked command list in dev_tools, path traversal protection, expanded secret detection in clipboard (16 patterns), rate limiting in gateway, entity ID regex validation, process protection list, subprocess_exec replacing shell.
- Sub-agent handoff: `orchestrator.run_headless()` for background execution, `agent_handoff` tool with wait/delegate modes, `agent_status` for detailed task tracking.
- Researched 4 GitHub repos: microsoft/JARVIS (task decomposition too heavy for 4 GB VRAM), deepseek-harness (Node.js plugin arch), open-jarvis/OpenJarvis (morning digest, presets), adewaskar/jarvis (Iron Man UI, MCP bridge).
- Built `digest.py` (morning briefing) and `presets.py` (5 quick-switch profiles).
- New integrations: Buffer tools (4: profiles, post, queue, analytics), RevenueCat tools (4: subscriber, offerings, grant, overview), Chrome DevTools Protocol tools (7: list_tabs, navigate, get_content, screenshot, run_js, click, type), MCP catalogue (3: browse 20 pre-configured servers, install, uninstall).
- Fixed TypeScript protocol parity (7 new message types added to `ui/src/protocol.ts`).
- Fixed speech engine fallback chain: privacy-mode and no-key errors now propagate correctly from primary engine.
- NVIDIA NIM confirmed already integrated (config.py provider spec + openai_compat adapter).
- Total tools: 105. Tests: 157 pass (103 core + 54 capabilities), 1 environment-dependent audio test skipped.

**Not started (deferred)**
- Deeper integration of microsoft/JARVIS task decomposition pattern (requires 24GB+ VRAM).

### 2026-11-20 — Full feature build (v0.8.0)

**Done**
- Built all 84 FEATURE_IDEAS.md features across 12 categories using free/open-source alternatives.
- 9 new Python modules: `memory.py`, `scheduler.py`, `telegram_bot.py`, `vision.py`, `file_index.py`, `clipboard.py`, `skills.py`, `agents.py`, `homeassistant.py`.
- 11 new tool registration files in `tools/`: memory_tools, scheduler_tools, vision_tools, file_tools, clipboard_tools, skill_tools, agent_tools, homeassistant_tools, telegram_tools, dev_tools, security_tools. 42+ new tools total.
- Universal TTS fallback chain: edge-tts (free) → Piper (local) → Groq → OpenAI → Deepgram → ElevenLabs → Windows.
- Universal STT fallback chain: Groq (free 2000/day) → Deepgram ($200 free) → OpenAI → AssemblyAI → Google Cloud (60 min/mo free) → Azure (5 hr/mo free) → local Whisper.
- Gateway Hub now initializes all modules at startup; orchestrator injects memory/skill context into system prompt.
- Protocol extended with 7 new server message types.
- Dependencies: edge-tts, python-telegram-bot, mss, easyocr.

**Not verified live**
- No Telegram bot token; no Home Assistant instance; screen OCR on real screen; edge-tts WAV conversion (needs ffmpeg).

### 2026-11-20 — Session 1

**Done**
- Surveyed machine, researched Wispr / NVIDIA NIM / shell options, wrote PRD and this file.
- Built M0: `src/jarvis` core, `ui/` HUD, `tests/`. `uv run pytest` → 78 passed; `ruff check` clean; `tsc --noEmit` clean; `vitest` → 9 passed; `npm run build` OK.
- Live verification in a browser against the running core: handshake, real telemetry, error path with no provider, and a full run through the real OpenAI-compatible adapter (streamed text → two tool calls → approval card → execution → final answer → Audit rows). The model side of that run was a scripted endpoint on the Ollama port, not a real model.
- Native window: `uv run jarvis` opened a "Jarvis" window that connected to the core (~205 MB).

**Bugs found by running it, and fixed**
- `-Format` parameter was classified as disk formatting (T3) → regex now requires `format <drive>:`; regression tests added.
- `index.html` was cached, so a rebuilt HUD loaded stale assets → served with `Cache-Control: no-store`.
- Vite inlined small fonts as `data:` URIs, blocked by the CSP → `assetsInlineLimit: 0`.
- Layout only filled 600 px because the connection banner occupied a grid row when absent → flex column.
- Command bar could not take focus while disabled during connect → focus on becoming online.
- ERROR state never cleared → returns to IDLE after 4 s.

### 2026-11-20 — Session 1, later the same day

- User saved an NVIDIA key and asked that Jarvis always pick the latest, best and fastest models from the available list. Built `providers/selector.py` + router integration (see PRD §12.1); results persist in the settings table under the internal key `model_bench`. Verified live: 10 of 16 probed NVIDIA models passed; chosen `nvidia/nemotron-3-ultra-550b-a55b`. Real runs succeeded end to end, including a T2 approval.
- User said the HUD looked generic and pointed at a reference reel. Rebuilt the Console as Ion v2 (PRD §9): `ui/src/Core.tsx` (particle sphere), `ui/src/Hud.tsx` (backdrop, radar, waveform, load chart, event stream, clock, Markdown), new `styles.css`. Telemetry now includes `top` processes. Iterated from screenshots: fixed an infinite render loop (unstable selector), a clipped approval card, a truncated model name, and a dim sphere.
- Instagram needed the built-in browser (Claude in Chrome was not connected); only the one reel was studied, not the whole profile.
- The built-in browser pane was hidden during review, which throttles animation; motion timing was therefore **not** judged at full frame rate.
- Checks: pytest 85 passed, ruff clean, tsc clean, vitest 9 passed.

### 2026-11-20 — Session 1, capability expansion

- User asked for Jarvis to handle any task: computer control, browser, email, calls, MCP. Built: `tools/computer.py` (UI Automation), `tools/browser.py` (Playwright on installed Edge, profile in `data/browser`), `tools/comms.py` (IMAP/SMTP email, `tel:` calls, Settings pages), `tools/extra.py` (mkdir/move/copy/recycle, power), `mcp_client.py`, `ui/src/Capabilities.tsx`. New deps: `uiautomation`, `playwright` (no browser download — uses `channel="msedge"`), `mcp` 2.x, `send2trash`.
- **Live results:** `ui_snapshot` read a real window; browser open/snapshot/click/read/back/tabs worked and a password field was refused; an MCP stdio server connected and its tool ran; through the real NVIDIA model Jarvis opened example.com and reported the heading and link text; a Notepad task opened the app and was correctly stopped at the approval gate in Guarded mode.
- **Not run:** `ui_click` / `ui_type` / `ui_hotkey` on a real app (approvals were denied by the test driver), any email against a real mailbox, `call_start`, `power_action`, `settings_open`, Trusted mode end to end, the new Settings sections in a browser.
- **Gotchas:** `mcp` 2.x renamed fields to snake_case (`input_schema`, `is_error`) and `FastMCP` to `MCPServer`; the client accepts both. `uv add`/`uv sync` fails while Jarvis is running because `jarvis.exe` is locked — close it first. NVIDIA occasionally stalls for 60 s with no output; the router now times out after 25 s and falls back.
- A Notepad window may have been left open by the live test; it was not force-closed in case it was the user's own.
- Checks: pytest 113 passed, ruff clean, tsc clean, vitest 10 passed.

### 2026-11-20 — Session 1, wake word and keys

- Built `wake.py` (openWakeWord `hey_jarvis` via ONNX, models in `data/wake`, `sounddevice` capture), wired into the hub (`voice.wake_word`, `wake_sensitivity`, `wake_keys`), with status in `integrations.wake` and live level/score in `telemetry.wake`. The wake word is **enabled** in this machine's settings.
- Built key handling: `ui/src/Keys.tsx`, `custom_providers` setting → `config.all_specs()`, vault index in the internal `vault` key, `${secret:name}` expansion in `mcp_client._resolve`.
- `pyproject.toml` now has `[tool.uv] environments = ["sys_platform == 'win32'"]`: openwakeword depends on Linux-only `tflite-runtime` with no cp312 wheels, and resolving for Windows only skips it.
- **Memory:** importing numpy + onnxruntime with default thread pools committed ~1.1 GB on this 16-thread CPU and pushed Windows to 0.13 GB free commit (MemoryErrors in telemetry). `jarvis/__init__.py` now sets `OPENBLAS/OMP/MKL/NUMEXPR_NUM_THREADS=1` before anything imports numpy. Do not import numpy anywhere earlier than that.
- **Unverified:** the microphone returned raw peaks of ~93/32767 while the speakers played speech at 92 % volume, i.e. the mic was muted or blocked, so no acoustic wake was observed. The model itself is verified on audio files (also a pytest). The new Settings sections have still not been viewed in a window.
- Checks: pytest 119 passed, ruff clean, tsc clean, vitest 13 passed.

### 2026-11-20 — Session 1, wake word rework

- User reported the wake word did not fire. Findings: (1) at that time the microphone delivered near-silence (raw peak 16–93); after the machine was restarted it delivers normal signal (peaks in the thousands). (2) Independently, the bare word "Jarvis" was unreliable on the `hey_jarvis` model in noise. An earlier benchmark that suggested otherwise used clean audio and a glob that matched the wrong file.
- `wake.py` now has `SpeechSegmenter`, `find_wake`, `transcribe`, a transcriber thread (`faster-whisper` tiny.en in `data/stt`), and `on_wake(score, command)`. `ui.focus` carries `text`; the command bar sends it. Wake stats (numbers only) are logged every 20 s under `jarvis.wake`.
- **Testing limit:** speech played through the laptop speakers does not appear in the microphone signal (the audio driver's echo cancellation removes it), so wake tests must use audio files through the fake stream (see `test_wake_listener_end_to_end_on_quiet_noisy_audio`) or a human voice. Do not conclude anything from speaker playback.
- The machine was restarted mid-session: C: went from 0.4 GB to 24 GB free and commit headroom to ~33 GB. The scratchpad path changed with it.
- Checks: pytest 124 passed, ruff clean, tsc clean, vitest 13 passed.

### 2026-11-20 — Session 1, wake feedback and voice training

- The wake log (numbers only) is the way to diagnose a missed wake: `Select-String data\jarvis.log -Pattern jarvis.wake`. It showed misses on the user's real voice with `tiny.en`, then detections with no visible effect.
- New: `ack.py` (chime + SAPI reply), `shell.force_foreground()`, `voice_training.py` (passage, alignment, learning, `Trainer`), hub methods `_voice_train` / `_training_finish`, `ui/src/Training.tsx`, calibration UI in `Settings.tsx`. Settings keys under `voice`: `wake_ack`, `wake_reply`, `wake_aliases`, `stt_model`, `corrections`, `trained_at`.
- **Do not pass `hotwords` or `initial_prompt` to faster-whisper for wake detection**: both make `base.en` omit a leading "Jarvis".
- Training's model comparison downloads `small.en` (~480 MB to `data/stt`) the first time and needs ≥ 3 GB free RAM; otherwise it keeps the current model.
- Checks: pytest 139 passed, ruff clean, tsc clean, vitest 13 passed.

### 2026-11-20 — Session 1, faster wake, male voice, Core fix

- `wake.py`: handler is now `(score, command, stage)` with stages `early` / `final`; `_fire_early`, `_fire_final`; queue items are `(kind, pcm, hint)`; ring buffer + faint-score verification + early look at each utterance; `near_misses` in status. `ack.py`: `chime()`, `speak(text, voice)`, `list_voices()`, `default_voice()` (searches both the classic and OneCore voice registries). New setting `voice.tts_voice`, new client message `voice.preview`.
- **Never stop a canvas loop on `document.hidden`** in this app: WebView2 under pywebview does not reliably send the matching `visibilitychange`, and the loop stays dead. `requestAnimationFrame` already idles when the page is not shown.
- A real window can be captured for visual checks with a small PowerShell `CopyFromScreen` of the Jarvis window rectangle (see the session transcript); the built-in browser pane is throttled when hidden and is not a substitute.
- The user completed voice training once (93 % understood) before this change.
- Checks: pytest 141 passed, ruff clean, tsc clean, vitest 13 passed.

### 2026-11-20 — Session 1, listening mode and the black window

- `wake.py` listening window: `_open_capture`, `_capture_parts`, `_finish_capture`, a `close` queue marker so the window ends only after pending utterances are transcribed; stages are `early` / `partial` / `final` / `ended`. Hub: `voice.session` and `voice.level` messages, `_track_reply` speaks the answer to a spoken request. Settings: `voice.wake_listen`, `listen_window_s`, `listen_pause_s`.
- **Never call `ShowWindow` on the pywebview window directly** (black WebView2). Use `win.restore()` / `win.show()`, then `shell.force_foreground()` for z-order and focus only.
- `window.__jarvisHud` (the zustand store) is exposed for exercising the display from a console: `__jarvisHud.getState().apply({type:'voice.session', state:'listening', text:''})`.
- Scratchpad helpers used this session (not in the repo): `capture.ps1` captures the real window and reports how dark it is; `repro.sh` minimizes, summons by hotkey and captures.
- Checks: pytest 145 passed, ruff clean, tsc clean, vitest 15 passed.

### 2026-11-20 — Session 1, the name anywhere

- `find_wake` returns the request with the name removed wherever the name sits; `_NAME_SOUND` regex and `_SPLIT_NAMES` widen matching; `_NOT_NAMES` holds the exclusions. `_heard_in_window` is set when a piece is gathered (in `_fire_final`), reset in `_open_capture`.
- Synthesized voices (Hazel, Zira; three rates; full and -30 dB) are all heard as "Jarvis", so they cannot reproduce the user's bare-name misses. If it still fails, the next step is the near-miss list in Settings after the user says the name a few times.

### 2026-11-20 — Session 1, voiceprint, self-healing, speech engines

- New modules: `voiceprint.py` (enrol/match/spot; thresholds 0.075–0.105), `selfheal.py` (FaultLog, FaultHandler, Healer, verify), `speech.py` (STT_ENGINES/TTS_ENGINES, transcribe/synthesize/say/catalogue), `ui/src/Repair.tsx`. Protocol: `selfheal.action`, `client.error`, server `selfheal`. Integrations payload gained `speech` and `wake.voiceprint`.
- `JARVIS_WAKE_DEBUG=1` logs what short phrases were heard as and voiceprint distances. `JARVIS_ROOT` lets the self-repair check run the tests against a copy of the package.
- Calibration ("Teach Jarvis your voice") now also builds the voiceprint from the five clips. `WakeListener.request_stt` is the cloud engine for requests; `_dictation_keys` switches the hub to Wispr mode (`_after_wake`, `_finish_dictation`).
- User settings changed this session: `voice.wake_keys = ctrl+win+space` (Wispr), aliases `here's our list`, `is our best`, `here's our wish` added as a stopgap until the voice is enrolled.
- **Next:** user enrols voice and reports; MCP OAuth sign-in (for Wispr Notetaker and other connectors); live test of one cloud speech engine; a real self-repair with a live model.

### 2026-11-20 — Session 1, connector sign-in and Settings categories

- `mcp_oauth.py`: `SignIns` (provider/complete/fail), `TokenStore` (DPAPI). `McpManager.sign_ins`; URL servers without headers get the OAuth provider; first reply timeout is 330 s for them. Callback route in `create_app`.
- Settings tabs live in `Settings.tsx` (`TABS`, `PROVIDER_GROUPS`, localStorage key `jarvis.settings.tab`); key categories in `Keys.tsx` (`SPEECH_KEYS`).
- In tests, FastAPI endpoints defined inside a function need their `Request` type in module globals (annotations are strings).

**Not verified yet (do these first next session)**
1. ~~A real model end to end~~ — done with NVIDIA NIM. Local models remain untested (memory was too tight to load one). Also judge the new HUD's motion in the real window at full frame rate.
2. The Anthropic adapter against the live API (written from the SDK reference; unit-tested only through the router).
3. Tools not yet executed on the machine: `apps_open`, `apps_search`, `windows_list`, `window_focus`, `window_set_state`, `window_close`, `volume_set`, `media_control`, `clipboard_read/write`, `power_lock`, `process_kill`, `web_open`.
4. Frameless window behaviour: dragging by the title bar, resizing, minimise / maximise / close buttons.
5. Global hotkeys (`Ctrl+Alt+J`, `Ctrl+Alt+Backspace`) and Wispr Flow dictation into the command bar, including auto-send timing.
6. Spoken replies.

**Known gaps (by design, later milestones)**
- No tray icon, no conversation switcher, no fast-path intents, no screen/vision, no UI automation, no memory.
- Anthropic server-side refusal `fallbacks` is not enabled: `ANTHROPIC_BASE_URL` points at a custom endpoint that may reject beta fields; cross-provider fallback in the router covers outages, and a refusal is surfaced to the user.
- Not a git repository yet.

**Environment notes**
- `openai` 3.x and `anthropic` 2.x are both built on `httpx2`; import `httpx2`, never `httpx`.
- PowerShell 5.1 `Set-Content -Encoding utf8` writes a BOM, which breaks `pyproject.toml` parsing — edit files with the editor tools instead.
- C: free space moved between 0.2 and 1.1 GB during the session. Project caches are on E: (`.cache/`, 320 MB) and WebView2 data is in `data/webview`.
- Dev loop: `JARVIS_TOKEN=<fixed> uv run jarvis --no-window`, then open the printed URL; `JARVIS_DEV=1` additionally allows the Vite dev server origin.

### 2026-11-21 — Session 2, onboarding wizard and configurable name

- User asked for: onboarding screen for new GitHub installs, rename module (change assistant name everywhere including taskbar), remove AI slop icons, wake word configurability with custom name.
- New files: `ui/src/Onboarding.tsx` (6-step wizard with animated welcome, name input with Scramble preview, privacy mode picker, provider key forms, theme cards with mini previews, ready screen with hotkey hints).
- Modified: `App.tsx` (onboarding intercept, dynamic name in title bar/taskbar/wake chip), `Console.tsx` (dynamic assistant name in message labels), `Settings.tsx` (new "Assistant name" section), `styles.css` (onboarding CSS), `config.py` (new defaults: `onboarding_complete`, `assistant_name`, `theme`), `protocol.ts` (new Settings fields), `gateway.py` (whitelist + validation for new settings, `_apply_wake` auto-registers custom name as wake alias), `shell.py` (PID-based `force_foreground`, dynamic `title` param in `run_window`), `__main__.py` (reads assistant_name for window title), `orchestrator.py` (dynamic name in LLM system prompt).
- **Build error fixed:** Unicode smart quotes (U+201C/U+201D) in App.tsx caused TS1127; fixed with sed byte replacement.
- **Security:** no API keys, secrets, or conversation history in the repo (verified before push).
- Committed as `0a2f2dc`, pushed to origin, released as v0.8.0 at https://github.com/ankitjha67/JARVIS/releases/tag/v0.8.0.
- Checks: tsc clean, vite build clean. Backend starts and serves the HUD.

**Not verified:** onboarding flow end-to-end with a fresh database (existing DB has `onboarding_complete: true`); wake word with a custom name on a real microphone; renaming the assistant while voice training data exists.
