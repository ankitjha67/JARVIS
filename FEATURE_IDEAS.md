# Jarvis — feature ideas and roadmap

Researched 2026-11-20. 84 ideas that are **not built yet**, checked against `PRD.md` and `CONTEXT.md`.

**How reliable this is.** Web research was partial: about half the searches failed and the fallback search hit
its daily limit. Ideas credited to products whose pages could not be read (Apple Intelligence, Rabbit/Humane,
Rewind/Limitless, Claude skills/computer use, ChatGPT memory/agent) come from general knowledge. Effort and
feasibility are estimates; nothing here was measured. Sources actually read are listed at the end.

**Legend.** Effort: S ≤ 1 day, M 2–5 days, L 1–3 weeks. Where: Local = runs on this laptop (i5-12500H, 16 GB,
RTX 3050 4 GB); Cloud = needs a model key; HW/Acct = needs hardware or an account.
Status: NEW, PLANNED (in the PRD, not built), PARTIAL (some exists).

## The goal: one assistant that does what the others do

The owner's target is OpenClaw, Hermes Agent, Claude Code computer use and ChatGPT desktop control in one.
("Jev" was also named; it could not be identified — please say which product that is.)

| What those products are known for | In Jarvis today | Gap → idea |
|---|---|---|
| Acts on the computer: apps, files, shell, browser (all of them) | Yes — 42 tools, UI Automation, Playwright browser | Pixel-level control for apps with no accessibility tree → 18, 23 |
| Sees the screen (Claude computer use, ChatGPT desktop) | No | 18, 19, 23 |
| Always-on, reachable from your phone, acts while you are away (OpenClaw) | No — window on one PC | 10, 11, 13, 76 |
| Long-term memory that grows (OpenClaw, Hermes, ChatGPT) | No — conversation history only | 1–4 |
| Writes its own skills and improves itself (Hermes) | Partly — self-repair with approval (new in 0.7.0) | 62, 63, 7, 8 |
| Sub-agents and long background jobs (Claude Code, Hermes) | No | 64, 65, 71 |
| Connectors to everything through MCP (all of them) | Client exists; header keys only | Sign-in (OAuth) for connectors; 66, 67 |
| Natural voice conversation | Wake word, listening mode, Wispr Flow, cloud engines (new in 0.7.0) | 29–31, 37 |

## 1. Memory and personalisation

1. **Long-term fact and preference memory** ("remember that…"), searchable and editable. The base for all personalisation. M · Local · PLANNED (FR-M3). Risk: remembering wrong or sensitive facts.
2. **Automatic memory extraction** after each conversation, into a review queue. M · Cloud · NEW. Risk: silent accumulation — make it approve-to-keep.
3. **Layered memory**: session scratch, daily summary, durable facts. M · Local+Cloud · NEW.
4. **Search past conversations** ("what did we decide about X"). S · Local (SQLite FTS5) · NEW.
5. **Editable persona and standing instructions.** S · Local · NEW.
6. **Entity book**: people, projects, aliases ("my broker"). M · Local · NEW. Risk: personal data — encrypt.
7. **Habit learning**: suggest a macro after the same sequence three times. M · Local · NEW.
8. **Learn from corrections and denials** ("never do it that way again"). S · Local · NEW.

## 2. Proactive and scheduled automation

9. **Timers, alarms, reminders by voice.** S · Local · PLANNED (FR-T15).
10. **Scheduled agent tasks** (one-off and recurring) with an inbox of runs. M · Cloud · PLANNED. Risk: unattended actions — limit to safe tiers or pre-approved grants.
11. **Heartbeat**: a quiet periodic check that speaks only when needed. M · Cloud · NEW. Risk: cost, notification fatigue.
12. **Monitor until a condition** ("tell me when the download finishes / price drops"). M · Local+Cloud · NEW.
13. **Event triggers**: new email from X, file appears, USB inserted, battery low, process exits. M · Local · NEW.
14. **Morning briefing**: calendar, mail, weather, markets, overnight results, spoken. M · Cloud · NEW. Depends on 38 and 45.
15. **End-of-day recap** from the audit log. S · Cloud · NEW.
16. **Opt-in proactive suggestions** (low disk, long job finished). M · Local · PLANNED (M4).
17. **Routines by voice** ("when I say good night: lock, mute, pause"). M · Local · PARTIAL.

## 3. Vision and screen understanding

18. **Screen capture to a vision model** (window or region). M · Cloud · PLANNED (FR-T11). Risk: screen contents leave the machine — per-capture consent.
19. **"What am I looking at?" hotkey.** S after 18 · Cloud · NEW.
20. **Guided "show me how"**: highlight on screen where to click. L · Cloud + overlay · NEW.
21. **Region select**: OCR, copy, translate, explain, add to calendar. M · Local OCR + Cloud · NEW.
22. **Local screen-history recall** (searchable text snapshots). L · Local · NEW. Risk: very privacy-sensitive; adds load.
23. **Pixel-level computer use** (click by coordinates, drag). L · Cloud vision · PARTIAL. Risk: mis-clicks, slow, costly.
24. **Webcam tools**: read a document, presence detection for auto-lock. M · Cloud/Local · NEW.
25. **Live screen-share conversation.** L · Cloud · NEW. Risk: continuous egress, cost.

## 4. Voice and conversation

26. **Cloud speech-to-text for the request.** S–M · Cloud · **BUILT in 0.7.0** (Groq, OpenAI, Deepgram, ElevenLabs, custom; needs a key and a live test).
27. **Larger local speech model on the GPU** (Whisper large-v3-turbo). M · Local, tight on 4 GB · NEW.
28. **Wake word learned from the owner's own voice.** M · Local · **BUILT in 0.7.0** as sound matching (needs enrolment and a live test).
29. **Neural speech output with streaming** (Piper/Kokoro). M · Local · PLANNED (FR-V3). Cloud voices built in 0.7.0.
30. **Continuous conversation**: follow-ups without the name, "goodbye" to end. M · Local · PARTIAL.
31. **Barge-in by voice** while Jarvis is speaking. M · Local · PARTIAL.
32. **Fast-path intents with no model** ("mute", "open Chrome"). M · Local · PLANNED (FR-A7).
33. **Speaker verification**: only the owner's voice can trigger actions. M · Local · NEW.
34. **Hindi and Hinglish** input and replies. M · Cloud · NEW.
35. **Dictation anywhere** with clean-up. M · Cloud · PARTIAL (Wispr Flow does this today).
36. **Response style presets**: brief, detailed, witty. S · Local · NEW.
37. **Real-time speech-to-speech mode.** L · Cloud, realtime audio account · NEW.

## 5. Communication

38. **Inbox triage**: classify, summarise, draft replies for approval. M · Cloud · PARTIAL.
39. **Gmail/Outlook through their APIs** instead of app passwords. M · Acct · NEW.
40. **WhatsApp, Telegram, Slack messaging.** M · Acct · PARTIAL (reachable through the browser today).
41. **Contact resolution** ("email Rahul"). S after 6 · Local · NEW.
42. **Follow-up tracker** ("remind me if they have not replied by Friday"). S after 10 · Local · NEW.
43. **Email-to-calendar extraction** (invites, tickets, bills). M · Cloud · NEW.
44. **Windows notification triage.** M · Local · PLANNED (M4).

## 6. Productivity and calendar

45. **Calendar tools**: read, create, find free slots. M · Acct or MCP · NEW.
46. **Task list and notes** (local Markdown, Todoist, Notion, Obsidian). S–M · NEW.
47. **Clipboard history with search and transforms.** M · Local · PARTIAL. Risk: captures passwords.
48. **Selected-text commands on a hotkey** (rewrite, shorten, translate). M · Cloud · NEW.
49. **Focus mode**: block distractions, Pomodoro. M · Local · NEW.
50. **Automatic time tracking** with a weekly report. S · Local · NEW.
51. **Workspace layouts**: restore apps and window positions per project. M · Local · PARTIAL.
52. **Quick settings toggles**: brightness, night light, Wi-Fi, Bluetooth. M · Local · PLANNED (FR-T14).
53. **Document generation**: Word, Excel, PowerPoint, PDF. M · Local+Cloud · NEW.
54. **Meeting capture**: transcript, summary, action items. L · Local capture + Cloud · NEW. Wispr's Notetaker connector may cover this once connected.

## 7. Knowledge over personal files

55. **Index chosen folders and answer with citations.** L · Local index · NEW. Risk: indexing load at ~85 % RAM.
56. **Instant filename search** (Everything / Windows Search). S · Local · PARTIAL.
57. **Web research mode** with a cited report. M · Cloud · NEW.
58. **Drop a file on the HUD** to summarise or query it. S · Cloud · NEW.
59. **Web clipper / read-later.** S · NEW.
60. **YouTube and podcast summariser.** S · Cloud · NEW.
61. **Automatic daily note.** S · Local · NEW.

## 8. Skills, agents and extensibility

62. **Skills as folders with a SKILL.md**, loaded when relevant. M · Local · PLANNED (M3). Risk: third-party skills are untrusted code.
63. **Record-and-replay macros**: demonstrate once, Jarvis writes the skill. L · NEW.
64. **Background sub-agents** with a task board. L · Cloud · NEW.
65. **Plan mode**: show the plan, edit, then execute. M · Cloud · NEW.
66. **Expose Jarvis as an MCP server.** M · Local · NEW. Risk: wider attack surface.
67. **One-click MCP catalogue.** S–M · PARTIAL. Browser sign-in (OAuth) for connectors was built in 0.7.1; the catalogue is not.
68. **Sandboxed code execution in Docker.** M · Local, heavy on 16 GB · NEW.
69. **Generative UI**: charts, tables, forms in the transcript. M · Local · NEW.

## 9. Developer tools

70. **Git and GitHub tools**: status, diff summary, commit message, PR and CI watch. S · Local · NEW.
71. **Delegate coding tasks to a coding-agent CLI** and report back. M · Acct · NEW.
72. **Long-command watcher**: announce when a build or training run ends. S · Local · NEW.
73. **Project launcher.** S · Local · NEW.
74. **Error explainer** for selected tracebacks. S · Cloud · NEW.

## 10. Smart home, IoT and phone

75. **Home Assistant bridge** (lights, plugs, AC, scenes). M · HW · NEW.
76. **Remote access from the phone** (Telegram bot or private tunnel) with approvals on the phone. L · Acct · NEW. **Conflicts with the PRD's loopback-only rule — needs a deliberate decision.**
77. **Android bridge**: SMS, notifications, ring phone. M–L · HW · PARTIAL.
78. **Bluetooth and audio device switching.** S · Local · NEW.

## 11. Security and privacy

79. **Redact secrets and personal data before any cloud call.** M · Local · NEW.
80. **Windows Hello confirmation for the riskiest actions.** M · Local · NEW.
81. **Encrypted database and "forget the last hour / everything".** M · Local · NEW.
82. **Dry-run and undo** for file and shell changes. M · Local · NEW.

## 12. HUD and self-maintenance

83. **Mini-orb overlay, tray icon, conversation switcher, start with Windows, installer.** L in total · Local · PLANNED.
84. **Self-diagnostics and usage panel**: mic and provider health, token and cost totals, memory watchdog. M · Local · PARTIAL — the fault log, automatic restarts and reviewed self-repair were built in 0.7.0.

## Recommended order

1. **Screen capture + "what am I looking at" (18, 19)** — the largest gap against Claude computer use and ChatGPT desktop.
2. **Long-term memory with a review queue (1, 2, 4)** — everything personal depends on it.
3. **Timers, reminders and scheduled tasks (9, 10)** — the base for anything proactive.
4. **Fast-path intents (32)** — makes simple commands feel instant.
5. **Sign-in support for MCP connectors (67)** — unlocks Wispr Notetaker, calendar, mail and more with one piece of work.
6. **Calendar and inbox triage (45, 38).**
7. **Skills folders and plan mode (62, 65)** — the Hermes-style "grows with you" loop.
8. **Background sub-agents (64)** — long jobs without blocking the conversation.
9. **Morning briefing and heartbeat (14, 11).**
10. **Mini-orb, tray and conversation switcher (83).**
11. **File knowledge base with citations (55).**
12. **Phone access through Telegram (76)** — very useful, but it breaks a stated rule and needs a decision first.

Cloud speech-to-text and the owner-trained wake word were the research's first two picks; both were built in 0.7.0 and now need testing with a real voice and key.

## Sources read

- https://docs.openclaw.ai/concepts/features (partial)
- https://clawdocs.org/guides/heartbeat
- https://docs.screenpipe.com/home and /task-library
- https://help.openai.com/en/articles/10291617-scheduled-tasks-in-chatgpt
- https://www.aboutamazon.com/news/devices/new-alexa-top-features
- https://www.home-assistant.io/voice_control/
- https://github.com/leon-ai/leon
- https://github.com/openinterpreter/open-interpreter
- https://manual.raycast.com/ai/ai-extensions
- https://support.microsoft.com/en-us/windows/retrace-your-steps-with-recall-aa03f8a0-a78b-4b3e-b0a1-2eb8ac48701c
- https://blogs.windows.com/windowsexperience/2025/10/16/making-every-windows-11-pc-an-ai-pc/
