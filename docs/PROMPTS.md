# Clickachu — Execution Prompts

Copy-paste prompts for building Clickachu with a coding agent (Claude Code), phase by phase.
Each prompt is self-contained and points at sections of [PLAN.md](PLAN.md).

## How to use
1. Run prompts **in order**. Don't start the next one until the current one's **Acceptance** list passes.
2. One prompt per session (or `/clear` between prompts). PLAN.md + CLAUDE.md carry the shared knowledge.
3. Each prompt is split into numbered parts. If a session runs long, it's fine to say
   "continue from part N" in a fresh session.
4. All changes stay local. The agent never runs `git init`, `git add`, `git commit` or `git push`;
   you handle version control yourself whenever you choose.
5. If the plan turns out wrong, fix **PLAN.md first**, then continue.
6. 🖐 = a check you must do yourself (global hotkeys, real apps).

| ID | Title | Plan phase | Est. |
|---|---|---|---|
| P1 | Setup: repo, CLAUDE.md, scaffold both apps | 0 | 0.5 h |
| P2 | Backend spine: sidecar, auth, WebSocket, Ask mode | 1 | 2 h |
| P3 | UI shell: quick bar + orb | 1 | 2 h |
| P4 | Context: capture, chips, redaction, replace | 2 | 3.5 h |
| P5 | Memory + project profiles | 3 | 3 h |
| P6 | Agent core: tools framework, policy, orchestrator, panel | 4 | 2.5 h |
| P7 | Agent tools: desktop, files, processes, browser | 4 | 2.5 h |
| P8 | Proactive watcher, fullscreen, tray, voice | 5 | 3.5 h |
| P9 | Hardening, README, demo | 6 | 2 h |

---

### P1 — Setup: repo, CLAUDE.md, scaffold both apps
```text
Read docs/PLAN.md fully. We are building "Clickachu" exactly as specified there (§2, §4, §5).

Part 1 — Repo
- Do NOT run any git commands (no init, add, commit or push); I manage git myself.
- .gitignore for Node (node_modules, out, dist), Python (.venv, __pycache__,
  .pytest_cache, .ruff_cache), Electron build output, .env*, *.db.
- CLAUDE.md (< 120 lines): 3-line summary; "docs/PLAN.md is the source of truth, cite § numbers";
  layout (app/ Electron+React+TS via electron-vite, core/ Python 3.12 via uv + FastAPI);
  commands (`cd app && npm run dev | typecheck | lint`, `cd core && uv run pytest | uv run ruff check .`);
  the 6 hard rules from PLAN §3 verbatim; protocol types must stay in sync between
  app/src/shared/protocol.ts and core/clickachu/api/protocol.py; style (TS strict, functional React,
  Zustand; Python type hints, Pydantic v2, no bare except, small modules); never commit secrets
  (API key lives in Windows Credential Manager via keyring); never run git init/add/commit/push —
  leave all changes uncommitted for the user.
- Minimal README.md linking to docs/PLAN.md.

Part 2 — app/
- electron-vite + React + TS strict, three renderer pages: src/orb, src/quickbar, src/panel
  (index.html + main.tsx + placeholder component each), multi-page input in electron.vite.config.ts.
- Tailwind; src/shared/theme.css variables: blue #4f7cff, violet #8b5cf6, magenta #e040fb,
  surface rgba(16,16,28,0.86), text #eaeaf5, muted #9a9ab0, border rgba(255,255,255,0.08).
- Deps: zustand, react-markdown, rehype-highlight, three, electron-store. ESLint + Prettier.
  Scripts: dev, build, typecheck, lint.
- electron/main/index.ts opens only the panel page in a normal window for now.
- electron/preload/index.ts: contextBridge exposing an empty typed `window.clickachu`.
  webPreferences: contextIsolation true, nodeIntegration false, sandbox true.

Part 3 — core/
- `uv init` package clickachu (Python 3.12) at core/clickachu/.
- Deps: fastapi, uvicorn[standard], pydantic>=2, openai, keyring, psutil, pywin32, comtypes,
  pywinauto, send2trash, playwright, faster-whisper, sounddevice, numpy.
  Dev: pytest, pytest-asyncio, ruff, httpx.
- __main__.py: argparse --port, uvicorn on 127.0.0.1; GET /health → {"ok": true, "version": "0.1.0"}.
- Empty packages from PLAN §3 (api, llm, agent, tools, automation, context, memory, projects,
  watchers, voice, db).
- config.py: data dir %APPDATA%\Clickachu with logs/, models/, browser-profile/.
- tests/test_health.py (httpx ASGI transport).

Acceptance:
- `npm run dev` opens a window with the panel placeholder; typecheck + lint pass.
- `uv run pytest` passes; `uv run python -m clickachu --port 8765` serves /health.
```

---

### P2 — Backend spine: sidecar, auth, WebSocket, Ask mode
```text
Read CLAUDE.md and docs/PLAN.md §5 (model configuration), §6, §7, §11, §12 (ask path only).

Part 1 — Sidecar handshake (§6)
- app/electron/main/sidecar.ts: 32-byte hex token; free port (net server on port 0); spawn
  `uv run --project <repo>/core python -m clickachu --port <port>` with CLICKACHU_TOKEN in env
  (never as a CLI arg). Log stdout/stderr to %APPDATA%\Clickachu\logs\sidecar.log; resolve on
  `READY <port>`, reject after 15 s; restart on crash with backoff 1/2/5 s then emit 'sidecar-failed';
  tree-kill on quit (`taskkill /pid <pid> /T /F`). TODO note for packaged paths.
- Preload: `window.clickachu.backend(): Promise<{baseUrl, token}>`.
- Python: read token from env (refuse to start without it unless --dev-insecure); middleware
  requiring `Authorization: Bearer`, Host == 127.0.0.1:<port>, Origin allowlist (dev
  http://localhost:5173; prod file:// and app://); print+flush `READY <port>` from lifespan startup.

Part 2 — WebSocket hub + protocol (§7)
- api/protocol.py: Pydantic models for every WS message (discriminated union on `type`);
  app/src/shared/protocol.ts with matching TS types.
- api/ws.py: /ws; first message {type:"auth", token} within 2 s else close 4401; Hub with
  broadcast(); dispatch incoming messages by type to registered handlers.
- app/src/shared/ws.ts: reconnecting client (backoff ≤ 5 s), auth on open, typed on()/send().
  app/src/shared/store.ts: Zustand store — connection status, runs map
  {run_id → mode, text, steps[], status}, orbState.

Part 3 — LLM + Ask mode (§11, §12)
- llm/provider.py: LLMProvider protocol + TextDelta / ToolCall / Done(usage).
- llm/openrouter.py: openai.AsyncOpenAI, base_url from settings (default
  https://openrouter.ai/api/v1), key from keyring (service "Clickachu", user "openrouter"),
  streaming, 3 retries on 429/5xx, 60 s timeout, usage/cost captured.
- Temporary settings store (JSON file in data dir; moves to SQLite in P5): router_model, ask_model,
  agent_model, base_url. GET/PUT /settings; PUT /secrets/openrouter (write-only).
- llm/prompts.py: SYSTEM_BASE + ASK_ACTIONS per §11.
- agent/orchestrator.py: Ask mode only — build messages, stream `token` events, run.finished with
  usage, orb.state thinking → idle, cancel via {type:"cancel"}.
- tests/fakes.py FakeProvider; tests for auth middleware (missing/wrong token, bad Host, bad Origin,
  good), WS auth, ask-mode event sequence, cancel.

Part 4 — Temporary panel UI
- Panel placeholder: connection status, settings fields (API key password input, 3 model IDs),
  a prompt box that sends `run` and renders streamed text.

Acceptance:
- `npm run dev` starts Python automatically; panel shows "connected".
- Killing python in Task Manager → it restarts and the panel reconnects.
- curl /health without the token → 401.
- 🖐 After saving key + models, "What is a Windows Job Object?" streams a real answer; Cancel stops
  it within 500 ms.
- uv run pytest passes.
```

---

### P3 — UI shell: quick bar + orb
```text
Read CLAUDE.md and docs/PLAN.md §9.1, §9.2, §9.5. Orb visual reference: a glowing wireframe /
particle sphere, blue → violet → magenta, soft noise displacement, sparkles around it.

Part 1 — Quick bar
- windows/quickbar.ts: 620 px frameless transparent window, alwaysOnTop, skipTaskbar, not resizable,
  show:false. openQuickBar() positions near the cursor, clamped to the display work area; renderer
  reports content height via IPC (min 120, max 45% of work area). Hide on blur unless a run is active.
- hotkeys.ts: Alt+Q → openQuickBar(); Ctrl+Alt+P → toggle panel. Unregister on quit; log
  registration failures.
- QuickBar.tsx + shared components: dark glass card (theme.css), ContextChip (placeholder
  "No selection"), ActionChips (rendered, wired in P4), input, streamed Markdown answer
  (react-markdown + rehype-highlight, copy buttons on code blocks), ResultActions (Copy works;
  others stubbed), follow-up input. Enter send · Shift+Enter newline · Esc close/cancel.
  Auto-focus input on show.

Part 2 — Orb
- windows/orb.ts: 120×120 transparent frameless, alwaysOnTop('screen-saver'), skipTaskbar,
  focusable false, hasShadow false, starts with setIgnoreMouseEvents(true, {forward:true}).
  Preload: setClickThrough(bool), drag start/move/end (main moves window; on end snap to nearest edge
  with 12 px margin), openQuickBar(), showContextMenu(). Persist position in electron-store; validate
  it's on a connected display at launch.
- orbScene.ts + Orb.tsx: Three.js, transparent background; ~2,500 points on a Fibonacci sphere +
  faint lat/long wireframe; 3D simplex-noise radial displacement (small inline noise function);
  additive blending; vertex colours #4f7cff → #8b5cf6 → #e040fb; sparse outer sparkles.
  State machine per §9.1 table with 300 ms lerped transitions and badges. 30 fps idle / 60 active;
  pause when document.hidden. Pointer enter circle → clickable; leave → click-through. Click →
  quick bar; drag > 4 px → move; right-click → context menu. Dim to 0.4 opacity after 5 s idle.
  Subscribe to orb.state.

Acceptance:
- 🖐 From any app, Alt+Q opens the bar at the cursor; a question streams an answer; Esc
  closes and focus returns to the previous app. Bar never appears in the taskbar or Alt+Tab.
- 🖐 Orb sits on the edge; clicks pass through except on the sphere; drag snaps and persists across
  restarts; asking a question switches it to "thinking" and back.
- Orb idle CPU < 3% in Task Manager.
```

---

### P4 — Context: capture, chips, redaction, replace
```text
Read CLAUDE.md and docs/PLAN.md §3 (hard rule 6), §9.2, §10, §11.

Part 1 — COM worker
- automation/com_worker.py: one daemon thread, CoInitializeEx(COINIT_APARTMENTTHREADED), job queue of
  (callable, future); `async def run_com(fn, *args, timeout)`; graceful shutdown.

Part 2 — Capture (§10 steps 1–8)
- context/selection.py: foreground hwnd/process/title; wait for modifier release; UIA TextPattern
  selection; terminal detection (process list + VS Code/Cursor xterm class check); clipboard fallback
  with GetClipboardSequenceNumber polling, Ctrl+Shift+C for terminals, SendInput, restore previous
  clipboard text. Never send plain Ctrl+C to a terminal.
- context/redact.py with the §10 patterns.
- context/capture.py: ContextSnapshot (§7), friendly app names, 20k truncation, redaction,
  LRU (50 entries, 30 min TTL); project matching stub (returns None until P5).
- POST /context/capture. Electron: Alt+Q calls capture BEFORE showing the bar, then passes
  the snapshot via IPC. ContextChip shows app · project · "N lines selected" (click to view, × to drop).
- `run` carries context_id; orchestrator adds the context block with the selection wrapped in
  <untrusted_data source="selection">.

Part 3 — Chips + replace
- Chips: Summarise, Explain, Translate (target language setting, default English), Rewrite, Fix
  (ask-mode for now), Do… (focuses input). Hidden without selection except Do…. Click sends `run`
  with `action`.
- POST /context/replace (replace + insert, terminal-aware paste, AttachThreadInput fallback, clipboard
  restore after 300 ms). If focusing fails: copy and return {ok:false, reason:"focus"} → UI shows
  "Copied — paste with Ctrl+V".
- ResultActions: Replace selection (Ctrl+Enter) and Insert call it, then hide the bar.
- Debug setting (default off) that logs the outgoing prompt, to verify redaction.

Tests: terminal detection and snapshot assembly with Win32 mocked; redaction fixtures for each
pattern plus false-positive guards.

Acceptance (🖐 record results in docs/CHECKLIST.md):
- Notepad, Edge, VS Code editor: selection captured (note uia/clipboard).
- VS Code terminal running `npm run dev`, with AND without a selection: process NOT killed; selection
  captured only when present. Same for Windows Terminal.
- Clipboard unchanged after each capture.
- Notepad paragraph → Summarise → Replace selection works.
- Selected `sk-...` text appears as [REDACTED] in the debug prompt log.
```

---

### P5 — Memory + project profiles
```text
Read CLAUDE.md and docs/PLAN.md §7 (memory/project routes), §8, §9.3, §11 (memory block), §14, §15
(detect + profiles only).

Part 1 — Database + memory store
- db/schema.sql exactly as §8 incl. FTS5 sync triggers; db/conn.py opens
  %APPDATA%\Clickachu\clickachu.db, applies schema idempotently, `schema_version` table, one
  connection per thread.
- Migrate settings from the temporary JSON file into the settings table (once).
- memory/store.py: create/update/delete/list/search (FTS5 bm25, scope filters, enabled, not
  superseded)/mark_used/supersede/export_json/import_json; refuse content matching redaction patterns.
- memory/dedup.py: same-scope FTS hit above threshold → router_model call same|update|new (LLM
  injectable for tests).
- Memory HTTP routes; global "memory off" honoured on read and write.

Part 2 — Memory in use
- Orchestrator: pinned + top-8 memories for (text + project + app) in the system prompt; mark_used.
- Explicit remember: user message starting "remember" / "from now on" / "always" (or router says so)
  → extract {content, category, scope} from the USER message only (ask_model JSON) → save with dedup
  → broadcast memory.saved. Expose as a reusable function (the agent `remember` tool uses it in P6).
- Quick bar toast "Saved: … · Undo".

Part 3 — Real side panel
- Panel per §9.3 (380 px, right edge, slide-in, Ctrl+Alt+P) with tabs Chat · Memory · Log · Settings.
  Move settings fields into Settings. Memory tab: grouped by scope, search, inline edit, pin, enable,
  delete, master "memory off", export/import JSON.

Part 4 — Projects
- projects/detect.py: propose commands from package.json scripts (npm/pnpm/yarn from lockfile),
  docker-compose services, pyproject scripts, Makefile targets; guess ports.
- projects/profiles.py CRUD (commands_json {name, cmd, cwd, port?, trusted:false}).
- Settings → Projects: folder picker (Electron dialog) → detect → editable list → save (also stores a
  project-scoped memory with the path). Allowed-roots manager (add/remove, can_write); adding a project
  offers to add its folder with write access.
- Capture: implement project matching (title contains project/folder name; VS Code "— <folder> —").

Tests: CRUD, FTS ranking sanity, scope filtering, supersede, redaction refusal, dedup with fake LLM,
detection on fixture folders.

Acceptance:
- uv run pytest passes; DB file exists in %APPDATA%\Clickachu.
- 🖐 "Remember that I prefer pnpm over npm" → toast → in Memory tab → after restart, "How should I
  install deps?" uses it; with memory off it doesn't.
- 🖐 Add the TeamFuse folder → sensible commands → saved; selecting text in VS Code with TeamFuse open
  shows "VS Code · TeamFuse".
```

---

### P6 — Agent core: tools framework, policy, orchestrator, panel
```text
Read CLAUDE.md and docs/PLAN.md §9.2 (agent hand-off), §9.3, §11 (router, AGENT_SYSTEM), §12, §13.1,
§13.2.

Part 1 — Framework
- tools/base.py: Tier, Tool ABC (§13.1), ToolContext (run_id, step_id, snapshot, db, workers),
  ToolResult {ok, data, summary, untrusted_text?}.
- tools/registry.py: register/get/schemas() (OpenAI tool JSON from Args.model_json_schema()).
- tools/policy.py: decide() per §13.2 with path normalisation (resolve, case-insensitive, reject ..
  escapes and UNC), sensitive-domain list from settings.
- First tools: recall, search_files (allowed roots, skip node_modules/.git/.venv, name match, optional
  ext, newest first, ≤20), remember (LOW; reuses P5 function; refuses when the content isn't grounded
  in the user's own message).

Part 2 — Orchestrator + router
- llm/router.py per §11.
- Agent mode per §12: tool loop, Pydantic validation with one retry, policy, approval await
  (5 min timeout = deny; "always" → trusted_actions via trust_key), execution on the right worker with
  timeout, verify, action_log, step events, ≤4,000-char results wrapped as untrusted, MAX_STEPS=8,
  cancel semantics, orb.state transitions. GET /log?run_id=.

Part 3 — Panel
- Agent runs from the quick bar collapse the bar and open the panel on Chat.
- StepTimeline (icon, summary, duration, verified tick, expandable args/result).
- ApprovalCard (tool, monospace preview; line-diff view for write_file; Approve / Always / Deny;
  Enter = approve, Esc = deny when focused); orb "attention" while pending.
- Header: run title, Stop, pin; auto-hide 4 s after run end unless pinned/hovered.
- Log tab (recent action_log, filters tool/decision/ok). Chat tab history; quick bar "Open in panel"
  moves the last answer there.

Tests: ≥25 table-driven policy cases incl. path escapes; registry schema validity; FakeProvider
orchestrator tests — auto tool, approve/deny/always/timeout, validation retry, step cap, cancel during
approval, blocked tool.

Acceptance:
- uv run pytest passes; event sequences match §7.
- 🖐 "Find files named resume" → timeline shows search_files ✓ and an answer listing results.
- 🖐 A temporary CONFIRM test tool shows an approval card; Deny is respected (remove the test tool after).
```

---

### P7 — Agent tools: desktop, files, processes, browser
```text
Read CLAUDE.md and docs/PLAN.md §5, §13.2, §13.3, §15.

Each tool gets: Args model, tier, worker, timeout, preview, verify, trust_key where relevant.

Part 1 — Desktop (COM worker)
- get_active_context, list_windows, inspect_window (depth ≤3, ≤150 nodes; compact lines: control type,
  name, automation id), open_app (§13.3 resolution order; find window by process + title; handle the
  Windows 11 Notepad hand-off), focus_window, type_text (type_keys / set_edit_text fallback; verify).
- Elevated windows: catch access-denied → "can't control admin windows".

Part 2 — Files + processes (IO)
- read_file, open_file, open_url (os.startfile), create_folder, move_file, write_file (unified diff
  preview), trash_file (send2trash; preview lists every path), get_process_logs, stop_process,
  run_project_command.
- watchers/process_manager.py per §15: Popen + Job Object (KILL_ON_JOB_CLOSE), reader threads into
  deque(maxlen=1000), port readiness polling, managed-process registry, kill all on shutdown.
  run_project_command takes only project + command name and looks up the literal cmd from the
  profile; trust_key "<project>:<command>". Expose line/exit callbacks (used in P8).

Part 3 — Browser (Playwright)
- automation/browser_worker.py: own thread + asyncio loop; lazy
  `chromium.launch_persistent_context(user_data_dir=<data>/browser-profile, channel=<msedge|chrome>,
  headless=False, viewport=None)`; one page per run; `run_browser(coro_fn, timeout)` bridge.
- Tools: browser_open, browser_search (configurable search URL), browser_read (accessibility snapshot
  → compact tree with stable refs like e12, ref→locator map per page, ≤6,000 chars),
  browser_screenshot (saved to data dir), browser_click(ref), browser_fill(ref, text),
  browser_submit(ref) (CONFIRM; preview = URL + element label + filled values).
- Page text always untrusted; sensitive domains force ASK.
- Settings: channel, search URL, sensitive domains, "Open Clickachu browser" button for manual logins.
  Document the channel requirement in README (no auto-download).

Tests: Args/preview/trust_key per tool; process manager with a tiny script that prints and exits
non-zero; policy denies paths outside roots.

Acceptance:
- 🖐 "Find my latest resume and open it" → search_files → open_file.
- 🖐 "Open Notepad and write Hello World" → open_app ✓ → type_text (approval) ✓ verified.
- 🖐 "Start TeamFuse" → approvals (none after "Always") → ports verified.
- 🖐 "Search the web for ioredis retry strategy and summarise the top result" → summary with URL.
- 🖐 On a login form, browser_submit shows an approval card.
```

---

### P8 — Proactive watcher, fullscreen, tray, voice
```text
Read CLAUDE.md and docs/PLAN.md §9.1, §9.4, §9.5, §16, §17.

Part 1 — Proactive watcher
- watchers/filters.py: §16 regex; signature normalisation (strip timestamps, non-port numbers,
  paths → basename); 5 min debounce per signature; max 3 pending.
- ProcessManager callbacks → suggestions row → broadcast `suggestion` (template text, no LLM).
- Orb badge + hover bubble (Investigate / Dismiss / Later). Investigate → snapshot whose selection is
  the last 60 redacted log lines → quick bar with action "explain". Later re-surfaces after 30 min.
  Settings toggle "Proactive suggestions".

Part 2 — Fullscreen + tray
- watchers/fullscreen.py: 1 s poll on the COM worker; foreground rect == monitor rect and class not in
  {Progman, WorkerW, Shell_TrayWnd} → orb.state hidden, restore afterwards. Suggestions during
  fullscreen use a Windows Notification.
- tray.ts: static orb PNG icon; menu Open panel · Pause proactive · Memory off · Open Clickachu
  browser · Settings · Quit. Same menu on orb right-click.

Part 3 — Voice
- voice/stt.py: lazy-load faster-whisper base.en int8 on a background thread (download to data/models,
  progress in Settings); sounddevice 16 kHz mono; RMS events ~15 Hz; auto-stop after 1.2 s below an
  adaptive threshold or 30 s. Routes /voice/start, /voice/stop → {text}.
- Ctrl+Alt+V toggles recording: orb "listening" with amplitude from RMS; quick bar shows "Listening…";
  transcript fills the input and auto-sends after 600 ms unless the user types.
- Settings: mic device picker, voice on/off.

Tests: filter fixtures (Node ECONNREFUSED, Python Traceback, EADDRINUSE) and debounce.

Acceptance:
- 🖐 Start TeamFuse with Redis stopped → crash → orb badge within 2 s → Investigate → explanation
  mentions Redis and offers the profile command to start it.
- 🖐 Fullscreen video hides the orb; it returns afterwards. Tray items work.
- 🖐 Ctrl+Alt+V, "find my latest resume and open it" → transcript < 2 s after you stop → run completes.
```

---

### P9 — Hardening, README, demo
```text
Read CLAUDE.md and docs/PLAN.md §2, §3, §18, §19, §20, §21.

Part 1 — Hardening
- Every §21 risk has its mitigation; list each with file references in docs/CHECKLIST.md.
- Error states: reconnecting banner, missing API key prompt, plain-language model errors, approval
  timeout notice, admin-window message.
- Settings shows today's token/cost total (sum of run.finished usage).
- Security sweep: grep shell=True, subprocess, eval, exec, os.system — justify or remove each; token
  never in logs or command lines; WS rejects unauthenticated clients.
- ruff, eslint, typecheck, pytest all green; remove dead code and finished TODOs.

Part 2 — Docs + demo
- README.md: what it is, screenshot placeholders, prerequisites, setup (uv sync, npm install,
  browser channel note, set API key in Settings), hotkeys, privacy (what stays local vs goes to the
  LLM), architecture (§3), troubleshooting (hotkey conflicts, UIA limits, elevated apps).
- docs/DEMO.md: §20 as a checklist with expected results and recovery steps.
- Walk through DEMO.md as far as possible without hotkeys; report which steps need me.

Acceptance: all checks green; CHECKLIST.md complete; 🖐 DEMO.md runs 3× without intervention.
```

---

## Utility prompts (use any time)

**Debug a failing check**
```text
Acceptance check "<failing item>" fails. Symptoms: <what you saw; logs from
%APPDATA%\Clickachu\logs>. Read CLAUDE.md and the relevant PLAN.md section, reproduce, find the root
cause (don't patch symptoms), fix it, and add a test if the logic is testable.
```

**Review changes**
```text
Review the local changes (don't commit anything) against docs/PLAN.md and CLAUDE.md's hard rules. Report and fix: rule
violations, COM/Playwright calls outside their workers, missing policy checks, untrusted text not
wrapped, secrets in logs. Then summarise.
```

**Update the plan**
```text
We've learned <finding>. Update docs/PLAN.md (relevant §), then adjust any affected prompts in
docs/PROMPTS.md. Don't change code in this step.
```
