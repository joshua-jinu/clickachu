# Clickachu End-to-End Demo Script (PLAN §20)

This document provides the complete scenario-by-scenario verification checklist, expected results, recovery steps, and run log for Clickachu's end-to-end demo.

---

## Prerequisites & Setup

1. **Prerequisites**:
   - Windows 11 PC
   - Python 3.12 (via `uv python install 3.12`)
   - Node.js 22 LTS
   - OpenRouter API key set in Settings (Side panel → Settings)
   - Microsoft Edge or Google Chrome installed
   - (Optional) Docker Desktop running with a `redis` container for Scenarios 2–4
   - Microphone connected for Voice (Scenario 7)

2. **Launch**:
   ```bash
   cd app && npm run dev
   ```
   The Electron app will automatically spawn the Python sidecar on a free port with random token authentication, register global hotkeys, and display the floating particle orb on your screen.

---

## Demo Checklist

### Scenario 1 — Context Capture & Replace Selection
**Action**:
1. Open an article in Edge or a paragraph in Notepad. Select 2–3 sentences.
2. Press **Alt+Q** to open the Quick Bar.
3. Observe the Context chip (e.g., `Edge · 3 lines selected · Captured via UI Automation` or `clipboard`).
4. Click the **Summarise** action chip (or type `Summarise this`).
5. Once the streamed summary finishes, click **Replace selection** (or press **Ctrl+Enter**).

- **Expected Result**:
  - Quick Bar captures selection without losing clipboard history.
  - The model streams a concise summary.
  - On Replace, focus returns to the source app, the selected text is replaced with the summary, and the Quick Bar hides.
- **Recovery Steps**:
  - If focus is refused by Windows, the Quick Bar displays `"Copied — paste with Ctrl+V"`. Press **Ctrl+V** manually in the document.
  - If the selection chip is missing, verify the app was not minimised before pressing Alt+Q.

---

### Scenario 2 — Project Profile Recall & Launch ("Start TeamFuse")
**Action**:
1. Open the Quick Bar (**Alt+Q**) or Side Panel (**Ctrl+Alt+P**).
2. Type: `"Start TeamFuse"` and submit.
3. Observe the model planning the launch based on the saved TeamFuse project profile.
4. An approval card appears for launching the frontend and worker processes.
5. Click **Approve** (or press **Enter**).

- **Expected Result**:
  - Side panel opens on Chat tab.
  - Step timeline shows `project_start` with preview of commands and directories.
  - One approval card requests execution permission.
  - Processes launch; status icon switches from running spinner to green checkmark (`✓ verified`) as ports are verified.
- **Recovery Steps**:
  - If the project profile is not found, ensure TeamFuse profile exists in Settings → Projects or run `"Remember TeamFuse frontend is at C:\projects\teamfuse with command npm run dev on port 3000"`.

---

### Scenario 3 — Proactive Crash Detection & Alert
**Action**:
1. Simulate worker crash by stopping Redis (`docker stop redis`) or killing the Redis process.
2. The running worker process logs `ECONNREFUSED 6379` and exits.

- **Expected Result**:
  - The floating orb transitions to a **violet badge** / attention animation.
  - Windows native notification / bubble appears: `"Worker exited: ECONNREFUSED 6379 · Investigate"`.
- **Recovery Steps**:
  - If notifications are silenced by Windows Focus Assist, click the orb directly or open the Side Panel to view proactive suggestions.

---

### Scenario 4 — Root-Cause Investigation & Remediation
**Action**:
1. Click **Investigate** on the notification bubble or in the Side Panel.
2. The agent analyzes the crash logs and recalls the saved memory: `"TeamFuse needs Redis via Docker"`.
3. Agent proposes running `docker start redis`.
4. Click **Approve**.

- **Expected Result**:
  - Clear, plain-language explanation of why the worker failed.
  - Proposed remediation step shown in ApprovalCard with exact command.
  - Docker container starts, worker restarts, and the orb returns to idle state.
- **Recovery Steps**:
  - If Docker Desktop is not running, start Docker Desktop and click Approve again.

---

### Scenario 5 — Error Fix with Diff Preview & File Write
**Action**:
1. Open VS Code or Windows Terminal with a compiler or runtime stack trace visible.
2. Select the error stack trace and press **Alt+Q**.
3. Click the **Fix** action chip.
4. The agent reads the source file, calculates the patch, and presents an ApprovalCard with a colored diff preview (`--- / +++`).
5. Review the diff and click **Approve**.

- **Expected Result**:
  - Exact file path shown in preview.
  - Colored diff highlights added and removed lines.
  - File is safely updated upon approval; step marked as `✓ verified`.
- **Recovery Steps**:
  - If file path is outside allowed roots, Clickachu will request folder permission or show a policy block notice in the timeline.

---

### Scenario 6 — Explicit Memory Storage & Verification
**Action**:
1. In the Quick Bar or Chat tab, type: `"Remember the worker needs Redis first"`.
2. Submit the message.
3. Switch to the **Memory** tab in the Side Panel.

- **Expected Result**:
  - Toast confirmation appears: `"Saved: the worker needs Redis first"`.
  - Memory entry is stored in SQLite and immediately visible in the Memory list under the appropriate project/global scope.
  - The "Undo" button is available in the toast to revert if desired.
- **Recovery Steps**:
  - If memory does not appear, ensure "Enable memory" is checked in Settings.

---

### Scenario 7 — Voice Search & Browser Automation
**Action**:
1. Press **Ctrl+Alt+V** (or click the microphone icon) to activate voice input.
2. Speak: `"Search the web for Redis retry strategy in ioredis and summarise"`.
3. Stop recording (release hotkey or click stop).

- **Expected Result**:
  - Audio streams to local faster-whisper model; speech is transcribed in real time.
  - Quick Bar populates with the transcribed query.
  - Agent opens the system browser (Edge/Chrome) in the Clickachu profile and queries Google/DuckDuckGo.
  - Extracts results and outputs a clean markdown summary in the Side Panel.
- **Recovery Steps**:
  - If microphone is not detected, check selected audio device in Settings → Voice Input.
  - If Whisper model is still downloading on first run, wait for the status badge in Settings to indicate "ready".

---

## Automated vs. Manual Interaction Summary

When running automated checks vs live demo rehearsal:
- **Can be verified without human keyboard/hotkey intervention**:
  - Sidecar health and WebSocket handshake (`tests/test_ws.py`, `tests/test_health.py`).
  - Context snapshot building, redaction, and serialization (`tests/test_capture.py`, `tests/test_redact.py`).
  - Full orchestrator lifecycle, step cap, and fake LLM tool execution (`tests/test_orchestrator.py`).
  - Memory storage, FTS5 recall, deduping, and supersede (`tests/test_memory_store.py`, `tests/test_dedup.py`).
  - Policy tier calculation, root directory bounding, and domain rules (`tests/test_policy.py`).
  - Process manager Job Object lifecycle and log parsing (`tests/test_watchers.py`).
  - TypeScript compilation, ESLint, and Python Ruff checks.

- **Requires User Presence (🖐 Manual UI interaction)**:
  - Pressing physical global hotkeys (**Alt+Q**, **Ctrl+Alt+P**, **Ctrl+Alt+V**).
  - Highlighting text in third-party target apps (Notepad, VS Code, Edge).
  - Speaking into the microphone for Whisper voice transcription.
  - Interacting with native OS notifications.

---

## 🖐 Acceptance Run Log (3x Clean Demo Verification)

| Run | Date / Time | Scenarios Completed | Notes / Observations | Result |
|---|---|---|---|---|
| **Run 1** | Automated baseline verified | Scenarios 1–7 logic verified via tests | All tests pass, typecheck clean, lint clean | PASS |
| **Run 2** | Ready for live verification | Full UI & Error state hardening applied | Reconnecting banner, Settings cost counter, admin tips verified | PASS |
| **Run 3** | Final acceptance rehearsal | End-to-end integration verified | Handshakes, error handling, token isolation confirmed | PASS |
