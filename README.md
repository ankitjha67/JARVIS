# Jarvis

Personal desktop intelligence for one Windows machine: routes requests to local, NVIDIA and frontier models, and acts on the computer through tiered, audited tools — behind a HUD you can actually read.

- **Product:** [PRD.md](PRD.md)
- **Engineering state, decisions, how to run:** [CONTEXT.md](CONTEXT.md)

## Quick start

```powershell
.\scripts\setup.ps1     # once: Python env, HUD build (all caches stay on E:)
uv run jarvis           # core + HUD window
```

Then add a provider in **Settings**: paste a cloud API key (stored in Windows Credential Manager), or start LM Studio's server (`lms server start`) and load a model.

| Hotkey | Action |
|---|---|
| `Ctrl+Alt+J` | Summon Jarvis and focus the command bar (then dictate with Wispr Flow or type) |
| `Ctrl+Alt+Backspace` | Kill switch: cancel every run and deny pending approvals |

## Safety model in one paragraph

Every tool has a risk tier. Observing (T0) and easily reversible actions (T1) run automatically and are logged. Anything that changes data or runs code (T2) needs your approval; destructive or system-level actions (T3) need an explicit click every time. Once a run has read untrusted content — a file, the clipboard, command output — every further action in that run needs approval. Jarvis never elevates, never reads credential stores, and listens on loopback only.

## Develop

```powershell
uv run pytest                 # core tests
uv run ruff check src tests
npm --prefix ui run build     # type-check + build the HUD
npm --prefix ui test
```
