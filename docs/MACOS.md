# JARVIS on macOS — beta, testers wanted

JARVIS started as a Windows app. The macOS port is new. Every push is checked on a GitHub-hosted Apple
Silicon Mac: the test suite, a `--doctor` self-check, starting the core, and a desktop test that opens a real
app, moves and zooms its window, types into it, reads its controls, clicks, sends shortcuts and launches the
HUD window. What no machine can tell us is how it feels in a person's hands, and a list of things below has
**never been tried on a real Mac at all**. If you have one, ten minutes of your time is worth a lot.

Report what you find with the **macOS test report** issue form:
<https://github.com/ankitjha67/JARVIS/issues/new?template=macos-test-report.yml>

## Requirements

- Apple Silicon Mac (M1 or newer). Intel Macs are not supported yet — PyTorch, which the OCR feature pulls
  in, no longer ships Intel macOS builds.
- macOS 13 Ventura or newer (CI runs macOS 15).
- [uv](https://docs.astral.sh/uv/) and Node.js 18+ (`brew install uv node`).
- One LLM: a cloud API key (Anthropic, OpenAI, Gemini, NVIDIA, OpenRouter, Groq) or a local server
  (LM Studio or Ollama).

## Install and run

```zsh
git clone https://github.com/ankitjha67/JARVIS.git
cd JARVIS
./setup-macos.sh     # installs dependencies, builds the HUD, prints the doctor report
uv run jarvis
```

`uv run jarvis --doctor` prints a pass / warn / fail line for every platform feature. Paste it into your
report. `uv run jarvis --no-window` runs the core only and prints a URL for the HUD.

## Permissions macOS will ask for

JARVIS runs as a Python process started from your terminal, so macOS attributes its permissions to the
**terminal app you launched it from** (Terminal, iTerm, Warp, VS Code…). Grant them under
System Settings → Privacy & Security, then restart JARVIS.

| Permission | Needed for | Without it |
|---|---|---|
| Accessibility | Focusing, resizing and closing windows; typing and shortcuts in other apps; reading a window's controls; media keys | Those tools explain which switch to turn on. Listing windows still works, with less detail. |
| Automation → System Events | Window control, typing, login-item audit | macOS asks the first time; the tool reports a refusal clearly. |
| Screen & System Audio Recording | Screenshots and "what is on my screen" | Captures show only the desktop picture. |
| Microphone | Wake word, voice input, voice training | Voice features report that the microphone could not be opened. |

API keys are stored in the macOS Keychain. JARVIS never reads other Keychain items, and the shell tool
refuses commands that try to (`security find-generic-password`, `dump-keychain`).

## Hotkeys

| Hotkey | Action |
|---|---|
| Ctrl+Option+J | Summon JARVIS and focus the command bar |
| Ctrl+Option+Delete | Kill switch: cancel every run, deny pending approvals |

They are registered through Carbon and need no extra permission. They work while the HUD window is open.

## What works, and how we know

230 of the 231 tools are available on macOS (the Windows Night Light toggle is not offered).

**CI Mac** = exercised for real on the macOS runner on every push. **Untried** = written, never run on a Mac.

| Area | macOS implementation | Status |
|---|---|---|
| Core: providers, routing, policy, approvals, audit, memory, scheduler, research, agents | Same code as Windows | CI Mac |
| HUD window | pywebview on WKWebView, frameless | CI Mac: the window opens. Look, drag, resize, buttons: **untried** |
| Keys | macOS Keychain via `keyring` | CI Mac |
| Shell | `zsh` instead of PowerShell, with its own destructive-command rules (rm, brew, defaults, launchctl, osascript, diskutil…) | CI Mac |
| Files, documents, search | Same code; Spotlight (`mdfind`) for system-wide search; deletes go to the Trash | CI Mac |
| Clipboard and clipboard history | `pbcopy` / `pbpaste`, change detection through NSPasteboard | CI Mac |
| Apps: find and open | Scans the Applications folders, launches with `open -a` | CI Mac |
| Windows: list, focus, minimize, restore, zoom, close | System Events (Accessibility); falls back to the window server's list when Accessibility is off | CI Mac |
| Typing | System Events keystrokes; refuses password fields; long or non-ASCII text is pasted and the clipboard restored | CI Mac |
| Shortcuts (`cmd+s`, `ctrl+option+j`…) | System Events | CI Mac |
| Reading and clicking controls (`ui_snapshot`, `ui_click`) | Accessibility tree of the front window; clicks at the control's centre, so the pointer moves | CI Mac on TextEdit; complex apps (browsers, Electron): **untried**, expect it to be slow there |
| Volume and mute | `osascript` volume settings | CI Mac |
| Startup audit, Spotlight, launchd services, system log | Login items, launch agents and daemons; `mdfind`; `launchctl list`; `log show` | CI Mac |
| Global hotkeys | Carbon `RegisterEventHotKey` | CI Mac: registration. Pressing them: **untried** |
| Screenshots, OCR, vision | `mss` / Pillow | CI Mac: capture. With Screen Recording permission on a real desktop: **untried** |
| Media keys | Play/pause, next, previous through Quartz events (there is no separate stop key) | **Untried** |
| Lock, sleep, restart, shut down, sign out | Lock is immediate; the others show the macOS confirmation dialog with its 60-second countdown | **Untried** |
| Spoken replies, chime | `say` voices and `afplay`; cloud voices unchanged | Voices are listed on the CI Mac; nobody has listened: **untried** |
| Wake word, speech-to-text, voice training | Same models (ONNX, faster-whisper) | **Untried** |
| Wi-Fi on/off | `networksetup` | **Untried** |
| Bluetooth on/off, brightness | Need the small Homebrew helpers `blueutil` / `brightness`; the tool says so when missing | **Untried** |
| System Settings pages | `x-apple.systempreferences:` links | **Untried** |
| Phone calls | `tel:` links (FaceTime with a nearby iPhone) or Twilio | **Untried** |
| Browser automation, email, Telegram, Home Assistant, MCP servers, God's Eye View | Same code as Windows | **Untried** on a Mac |
| Phone Link, Wispr Flow auto-send, Windows Night Light | Windows only | Not available |

## A ten-minute test

1. Run the setup script and paste the doctor report into the issue form.
2. Start JARVIS, finish onboarding, send "what time is it?". Does the window look right? Can you drag it by
   the title bar, resize it, minimise and close it?
3. Try, one at a time: "open Safari", "list my open windows", "set the volume to 30 percent",
   "copy hello world to the clipboard", "find files named resume", "pause the music", "lock the screen".
4. Ask for something that needs approval — "run `ls ~/Desktop` in the shell" — and check the approval card.
5. Press Ctrl+Option+J from another app; start a long task and press Ctrl+Option+Delete.
6. Optional: turn on spoken replies and the wake word in Settings.

To run the automated desktop test on your own Mac (it takes over the keyboard for about a minute):

```zsh
JARVIS_DESKTOP_TESTS=1 uv run pytest tests/test_macos_desktop.py -v
```

Logs are in `data/jarvis.log`. Nothing leaves your machine except the requests to the model provider you chose.
