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

## Roadmap extension — 20 November 2026

The numbered list above is the historical backlog. The current checkout contains modules for memory, skills, background tasks, research, vision, outcome verification, and Home Assistant, alongside the HUD and Android companion. This extension treats those as starting points. It is based on a repository and documentation review. Feature status here is an assessment of the visible code, and the proposals have not been tested end to end.

### Research benchmarks

| Product | Documented pattern | Application to Jarvis |
|---|---|---|
| [Hermes Agent](https://hermes-agent.nousresearch.com/docs) | Reusable skills, specialist bots, and recurring jobs | Give each saved Jarvis workflow a version, test cases, and a history of results. |
| [OpenClaw](https://docs.openclaw.ai/start/openclaw) and [ChatGPT Scheduled Tasks](https://help.openai.com/en/articles/10291617-scheduled-tasks-in-chatgpt) | Proactive checks with delivery controls, and monitoring that can retain context or stop on a condition | Show what changed, collapse duplicate alerts, and give each monitor a stopping rule. |
| [Power Automate Desktop](https://learn.microsoft.com/en-us/power-automate/desktop-flows/run-unattended-desktop-flows) | Unattended runs have explicit Windows session and device constraints | Route workflows based on whether the desktop is available, locked, or asleep. |
| [Temporal](https://docs.temporal.io/durable-execution) | Durable execution records activity and results to resume after failure | Build on the SQLite task engine and verify uncertain outcomes before retrying external changes. |
| [n8n](https://docs.n8n.io/advanced-ai/evaluations/overview/) | Test datasets catch regressions and compare model and prompt performance | Replay known Jarvis commands against candidate workflows and models. |

### P0 — Make everyday actions dependable

| ID | Feature | Example or first usable release |
|---|---|---|
| 85 | Run Center | See running, waiting, paused, and failed jobs in one HUD view. Each job shows the last confirmed step, next action, and any approval needed. |
| 86 | Turn a run into a workflow | Save a successful run with editable inputs such as folder, report date, and output name. Preview a version before publishing or scheduling it. |
| 87 | Safe resume after an interruption | Keep a receipt for each external change. If Jarvis crashes after submitting a form, check for the receipt before retrying. Ask for review if the result is uncertain. |
| 88 | Action previews with stale-target checks | Show exact files, recipients, or settings before approval. If the target changes while approval is pending, refresh the preview and request a new decision. |
| 89 | Workflow Test Lab | Replay saved examples on sample files and simulated connectors. Compare outcome accuracy, latency, cost, and approval behavior across model or skill versions. |
| 90 | Desktop readiness and wake recovery | Distinguish workflows that need an unlocked desktop from those that can run in the background. Let the user choose whether missed runs should catch up, expire, or wait. |
| 91 | Per-workflow model and privacy budgets | Set a maximum cloud spend, runtime, and allowed input types. Show the reason when a budget or privacy rule pauses a run. |

### P1 — Make Jarvis useful every day

| ID | Feature | Example or first usable release |
|---|---|---|
| 92 | Change inbox | Combine alerts from existing monitors, with old and new values, evidence, mute rules, and a stop date. Group several updates to the same item. |
| 93 | Project rooms | Pin folders, conversations, reference documents, saved workflows, and permissions to a project. Switch among Jarvis development, personal admin, and credit risk with their own context. |
| 94 | Memory with source and expiry | Show where a memory came from, when it was last verified, and which project can use it. Surface conflicts when a newer source disagrees. |
| 95 | Workflow health checks | Flag renamed browser fields, lost connector access, or changed spreadsheet columns. Propose a repair that the user can test and approve. |
| 96 | Evidence packs | Export a report with claim-to-source links, document page references, retrieval timestamps, and an inventory of input files. |
| 97 | Approval handoff to Android | Resume a waiting task from a paired phone with the same action preview and an expiring approval. Requires an authenticated transport design that preserves the desktop gateway's loopback binding. |
| 98 | Windows Explorer actions | Send selected files straight to Jarvis for a specific workflow such as compare, extract tables, summarize, or package for review. |
| 99 | Hinglish command test pack | Test real mixes of Hindi and English, tricky Indian names, dates, and amounts against the existing speech stack. Confirm the interpreted recipient or amount before consequential actions. |
| 100 | Shift-long handover | On demand, show what changed since the last review, which tasks are blocked, and exactly where to resume each one. Generate it from run state and audit events. |

### P2 — Ship workflows with clear outputs

| ID | Feature | Example or first usable release |
|---|---|---|
| 101 | Regulatory change desk | Compare two versions of an RBI, PRA, or Basel document. Produce a clause-level change register and a draft mapping to controls, policies, and implementation owners. |
| 102 | Excel reconciliation workbench | Compare two RWA or portfolio extracts using selected business keys. Export breaks, unmatched rows, source row references, and a variance waterfall. |
| 103 | Requirements-to-UAT tracker | Link PRD or BRD requirements to functional specs, test cases, and evidence. Flag untested requirements and tests affected by a document change. |
| 104 | Document intake with exception review | Pull invoice, property, or banking document fields into a tracker. Show the source page for each value and queue ambiguous or duplicate entries for review. |
| 105 | Pre-market research brief | Combine sourced exchange notices, overnight headlines, and watchlist changes into a time-stamped brief. Group duplicate stories and flag stale feeds. |
| 106 | Research report refresh | Rerun a saved research question with new sources. Highlight which claims changed, which citations disappeared, and what still needs checking. |
| 107 | Local AI operations dashboard | Compare success rate, latency, VRAM, and cost by task type across NVIDIA NIM, LM Studio, Ollama, and other configured providers. Use the results to tune existing routing. |
| 108 | Personal admin case files | Track a deposit refund, service complaint, or home-loan disbursement with its deadline, evidence, correspondence drafts, and next action. |

### Suggested first release

Start with **Run Center → save a run as a workflow → replay it in the Test Lab**. Use one read-only workbook comparison as the first workflow. The release is usable when the same workflow can run from the HUD and a schedule, survive a restart, and produce an output that links back to the input rows. Add consequential external actions after the stale-target checks and safe-resume behavior are verified.

### Implementation status

| IDs | Status in this checkout |
|---|---|
| 85 | First release integrated. The HUD Runs view shows background job status, steps, results, cancellation, and a link to pending approvals. |
| 86 | Partial. Completed reconciliation runs can be saved, revised as separate versions, and scheduled. Generic run capture is pending. |
| 87 | Partial. Interrupted local comparisons restart from persisted inputs, and retried schedule occurrences reuse the same run ID. External action receipts and reconciliation are pending. |
| 88 | Partial. Supported file writes, moves, and deletes recheck the target after interactive approval and refuse stale approvals. Browser, account, and recipient targets are pending. |
| 89, 95 | Partial. The comparison uses a deterministic sample fixture and checks saved input files, sheets, and business keys. Cross-model evaluation, connector drift, and repair proposals are pending. |
| 100 | Partial. On-demand handover shows recent actions since the last review and current open runs. Step-specific resume guidance is pending. |
| 101 | Partial. The regulatory comparison tool produces a paginated change register with page or line evidence and optional mappings from a supplied control CSV. Source monitoring and review workflow are pending. |
| 102 | Integrated. Compare CSV and XLSX extracts, download all breaks and cell differences with source rows, and chart an optional numeric metric in a variance waterfall. XLSX requires openpyxl. |
| 103 | Partial. UAT traceability links local requirement and test registers, flags uncovered requirements and orphan tests, and identifies linked tests for changed requirements. Direct BRD extraction and a review HUD are pending. |
| 104 | Partial. Document intake extracts configured labels from local files, cites pages or lines, and flags missing, ambiguous, or duplicate values. Tracker persistence and scanned-document handling are pending. |
| 108 | Integrated through the Console. Create and update local case files with deadlines, next actions, evidence references, drafts, and revision checks. |
| 90–94, 96–99, 105–107 | Pending as described. Existing approvals, scheduler, memory, research, Android companion, and provider benchmarks cover parts of this scope. |
