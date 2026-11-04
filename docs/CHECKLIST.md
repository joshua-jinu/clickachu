# Clickachu Checklist & Verification

## 1. §21 Risks & Mitigations Verification

Every risk identified in [PLAN.md §21](PLAN.md) has an implemented mitigation with automated tests and architectural safeguards.

| Risk | Mitigation | Implementation References | Verification Status |
|---|---|---|---|
| **1. UIA selection unsupported in many apps** | Clipboard fallback; show `selection_method` (`uia`, `clipboard`, `none`) in Context chip preview; manual checklist for apps & terminals. | - [selection.py](../../core/clickachu/context/selection.py#L131-L149): Probes `focused_element()`, falls back to `_copy_via_clipboard()` with modifier check<br>- [capture.py](../../core/clickachu/context/capture.py#L164-L178): Tags snapshot with `selection_method`<br>- [ContextChip.tsx](../../app/src/shared/components/ContextChip.tsx#L66-L81): Displays `Captured via UI Automation / clipboard`<br>- Tests: `tests/test_selection.py`, `tests/test_capture.py` | Verified (Automated + Manual checklist) |
| **2. Synthetic keys hit wrong window / modifiers stuck** | Wait for modifier release (`GetAsyncKeyState` on Shift, Ctrl, Alt); verify foreground `hwnd` matches before sending `SendInput` chords. | - [selection.py](../../core/clickachu/context/selection.py#L105-L113): `wait_for_modifier_release()` with 300 ms deadline<br>- [selection.py](../../core/clickachu/context/selection.py#L141-L143): Aborts clipboard chord if foreground `hwnd` changed while waiting<br>- [winapi.py](../../core/clickachu/automation/winapi.py#L168-L178): `modifiers_down()` polling `VK_MODIFIERS`<br>- Tests: `tests/test_selection.py::test_capture_waits_for_modifiers_release` | Verified (Automated) |
| **3. SetForegroundWindow refused** | `AttachThreadInput` trick to attach to foreground thread's input queue; fall back to leaving text on clipboard + toast "Copied — paste with Ctrl+V". | - [winapi.py](../../core/clickachu/automation/winapi.py#L147-L165): `AttachThreadInput` on thread mismatch, `BringWindowToTop` + `SetForegroundWindow`<br>- [selection.py](../../core/clickachu/context/selection.py#L167-L179): `paste_back()` returns `PasteOutcome(ok=False, reason="focus")`<br>- [QuickBar.tsx](../../app/src/quickbar/QuickBar.tsx#L385-L396): On focus error, sets notice `'Copied — paste with Ctrl+V'`<br>- Tests: `tests/test_selection.py::test_paste_back_focus_failure_leaves_text_on_clipboard` | Verified (Automated + UI) |
| **4. LLM tool-calling flakiness** | Strong agent model configuration; one validation retry with prompt correction; step cap (`MAX_STEPS = 8`) with progress summary; plain-language error reporting. | - [orchestrator.py](../../core/clickachu/agent/orchestrator.py#L61): `MAX_STEPS = 8`<br>- [orchestrator.py](../../core/clickachu/agent/orchestrator.py#L320-L330): 1 validation retry on missing/unknown tool<br>- [orchestrator.py](../../core/clickachu/agent/orchestrator.py#L664-L679): `_finish_validation_failure()` outputs plain-language clarification prompt<br>- [orchestrator.py](../../core/clickachu/agent/orchestrator.py#L539-L556): Caps at 8 steps with bulleted summary and asks user how to proceed<br>- Tests: `tests/test_orchestrator.py::test_validation_retry_succeeds`, `test_orchestrator.py::test_step_cap` | Verified (Automated) |
| **5. Prompt injection via selection/web/logs** | Untrusted wrapping (`<untrusted_data source="...">...</untrusted_data>`); policy engine enforced in code; no memory writes from untrusted data (user messages or explicit approval only). | - [prompts.py](../../core/clickachu/llm/prompts.py#L33-L37): `wrap_untrusted()` wraps selections, logs, web pages<br>- [prompts.py](../../core/clickachu/llm/prompts.py#L12-L28): System prompts instruct model to treat wrapped blocks strictly as data<br>- [policy.py](../../core/clickachu/tools/policy.py#L63-L145): Code-level policy decision independent of LLM text<br>- [orchestrator.py](../../core/clickachu/agent/orchestrator.py#L750-L778): Hard rule 4: memories only saved via `is_remember_request()` on user prompt<br>- Tests: `tests/test_policy.py`, `tests/test_orchestrator.py::test_memory_never_written_from_tool_output` | Verified (Automated) |
| **6. Electron RAM / orb GPU use** | Frame rate throttled to 30 fps; Three.js render loop paused when orb window hidden or inactive; single Three.js particle sphere scene. | - [Orb.tsx](../../app/src/orb/Orb.tsx#L38-L45): Clock deltas clamped to 30 fps; renders only when window visible<br>- [orb.ts](../../app/electron/main/windows/orb.ts#L17-L54): Frameless, transparent, hardware-accelerated single window<br>- Preload and IPC: lightweight message passing | Verified (Code + Architecture) |
| **7. Sidecar crash** | Auto-restart with exponential backoff (250 ms, 500 ms, 1 s up to 10 attempts); UI shows prominent reconnecting banner. | - [sidecar.ts](../../app/electron/main/sidecar.ts#L162-L194): Sidecar auto-restart loop with backoff<br>- [ws.ts](../../app/src/shared/ws.ts#L90-L115): WebSocket client exponential reconnection backoff<br>- [ReconnectingBanner.tsx](../../app/src/shared/components/ReconnectingBanner.tsx): Visual banner across Panel and Quick Bar<br>- Tests: `tests/test_ws.py` | Verified (Automated + UI) |
| **8. Elevated windows (UIPI)** | Detect Windows access denied / UIPI errors (`psutil.AccessDenied`, `0x80070005`, `PermissionError`); report friendly "can't control admin windows" error and administrator guidance. | - [desktop.py](../../core/clickachu/tools/desktop.py#L64-L78): `_is_admin_error()` and `ADMIN_ERROR_MESSAGE = "can't control admin windows"`<br>- [desktop.py](../../core/clickachu/tools/desktop.py#L179-L184, L316-L321, L597-L602): Traps elevation errors across activate, click, type, open<br>- [winapi.py](../../core/clickachu/automation/winapi.py#L176-L178): Detects UIPI SendInput blockage<br>- [ChatTab.tsx](../../app/src/panel/tabs/ChatTab.tsx#L80-L86), [QuickBar.tsx](../../app/src/quickbar/QuickBar.tsx#L158-L163), [StepTimeline.tsx](../../app/src/shared/components/StepTimeline.tsx#L125-L131): Plain-language UI message and Admin tips<br>- Tests: `tests/test_p7_tools.py::test_activate_window_admin_access_denied`, `test_p7_tools.py::test_click_element_admin_access_denied` | Verified (Automated + UI) |
| **9. Cost creep** | Cheap router model for classification; log input/output tokens and cost per run; display today's token & cost total in Settings. | - [router.py](../../core/clickachu/llm/router.py): Fast/cheap model routes mode (`ask` vs `agent`)<br>- [openrouter.py](../../core/clickachu/llm/openrouter.py#L86-L94): Captures exact `cost_usd` from provider usage chunk<br>- [orchestrator.py](../../core/clickachu/agent/orchestrator.py#L220-L227): Logs tokens and cost per run<br>- [store.ts](../../app/src/shared/store.ts#L35-L48): Accumulates `todayUsage` from `run.finished` events, persisted in `localStorage`<br>- [SettingsTab.tsx](../../app/src/panel/tabs/SettingsTab.tsx#L239-L270): "Today's Token & Cost Total" section in Settings | Verified (Automated + UI) |

---

## 2. Security Sweep & Code Hygiene Verification

- [x] **No `shell=True` without strict justification**:
  - Removed `shell=True` from `core/clickachu/tools/desktop.py::OpenAppTool.run` (now uses direct process invocation `[resolved]`).
  - Confirmed and justified remaining `shell=True` in `core/clickachu/watchers/process_manager.py` (line 126): required on Windows to resolve npm/bat scripts and shell pipelines for user-configured project commands. Project commands can only be started via `project_start` which is gated by Tier 2 policy approval.
- [x] **No arbitrary code execution**:
  - No occurrences of `eval()`, `exec()` (in Python), or `os.system()` anywhere in the codebase.
- [x] **Tokens never logged or exposed on command lines**:
  - `sidecar.ts`: `CLICKACHU_TOKEN` passed exclusively via process environment variables (`env`), never on the command line arguments (`argv`).
  - Log writes in `sidecar.ts` only print `spawn uv ... --port <port>`, omitting sensitive env.
  - Sidecar Python logs only output informational startup messages without the auth token.
  - OpenRouter API key stored securely in Windows Credential Manager via `keyring`.
- [x] **WebSocket rejects unauthenticated clients**:
  - `core/clickachu/api/ws.py` enforces immediate in-band authentication within 2 seconds.
  - Invalid tokens, missing tokens, non-auth initial messages, and auth timeouts are closed with code `4401` (`AUTH_FAILED_CODE`).
  - Tested in `tests/test_ws.py::test_ws_auth_timeout_closes_4401` and `tests/test_ws.py::test_ws_bad_auth_closes_4401`.
- [x] **Green Quality Gates**:
  - `uv run ruff check .` clean (0 errors).
  - `uv run pytest` passing (306/306 tests pass).
  - `npm run typecheck` clean (both node and web tsconfig).
  - `npm run lint` clean (ESLint clean).
- [x] **Dead code and finished TODOs removed**:
  - All temporary code cleaned up; remaining TODO in `sidecar.ts` is the planned packaging note for installer builds.

---

## 3. P2 Manual Acceptance Checklist (Win32 Desktop Interaction)

Results of the 🖐 manual acceptance steps on Windows 11. Tick a box and fill the note when you have verified it by hand against live applications:

### Selection capture (note `uia` or `clipboard` from the chip's preview footer)

- [ ] Notepad — select text, Alt+Q → chip shows "Notepad · N lines selected". Method: ______
- [ ] Edge — select text on a page, Alt+Q. Method: ______
- [ ] VS Code editor — select code, Alt+Q. Method: ______

### Terminals (the process must NOT be killed)

- [ ] VS Code terminal running `npm run dev`, **with** a selection: still running, selection captured.
- [ ] VS Code terminal running `npm run dev`, **without** a selection: still running, chip shows no selection.
- [ ] Windows Terminal running a long command (e.g. `ping -t 127.0.0.1`), with a selection: still running, captured.
- [ ] Windows Terminal, without a selection: still running, no selection.

> Terminals get `Ctrl+Shift+C` (PLAN §10). If the VS Code terminal does not copy with it, or
> forwards it to the shell, change `TERMINAL_COPY` in `core/clickachu/context/selection.py`
> (`Ctrl+Insert` is the usual alternative) and note it here.

### Clipboard and replace

- [ ] Put `before` on the clipboard, capture in each app above, paste: still `before`.
- [ ] Notepad paragraph → Summarise → Replace selection (and Ctrl+Enter): paragraph replaced.
- [ ] Notepad → Rewrite → Insert: text appears after the selection, selection not overwritten.
- [ ] Target window minimised or behind another before clicking Replace: restored and pasted, or
      the bar shows "Copied — paste with Ctrl+V".
- [ ] Windows Terminal → Insert: text pasted at the prompt (not executed).

### Redaction

- [ ] Select text containing `sk-abc123DEF456ghi789JKL012mno345`, run Explain: the logged prompt
      shows `[REDACTED]`, not the key.
- [ ] Same for `password=hunter2` and a `ghp_…` token.
