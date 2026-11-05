# Clickachu

> Windows 11 desktop companion: an always-on orb, a hotkey quick bar, and a side panel backed by a local Python sidecar that captures context, remembers preferences, and carries out approved actions on your PC.

**Principle**: Capture → Understand → Propose → Approve → Act → Verify → Remember.

---

## What It Is

Clickachu is an agentic desktop companion built specifically for Windows 11. Unlike web-based chat interfaces, Clickachu lives directly in your desktop environment:
- **Floating Orb**: A lightweight, GPU-efficient 3D particle sphere that sits quietly on your screen and animates state changes (idle, thinking, attention, acting, error).
- **Quick Bar (`Alt+Q`)**: A fast, translucent command palette docked above your cursor that instantly captures the foreground app and selected text (code, logs, articles) with zero-configuration action chips (`Summarise`, `Explain`, `Fix`, `Translate`, `Rewrite`).
- **Side Panel (`Ctrl+Alt+P`)**: A slide-in workspace featuring interactive Chat with step-by-step tool execution timelines, diff previews, project manager, persistent memory browser, action logs, and configuration.
- **Human-in-the-Loop Policy Engine**: The LLM proposes tool calls checked by the policy engine. Routine observed navigation, search, scrolling, item selection, and playback run automatically. Consequential changes (such as purchases, sending, deletion, installation, or security changes), unidentified targets, and host scripts still require confirmation. Desktop approvals apply to the individual action, never the whole window.

---

## Screenshots

<!-- Screenshot Placeholders -->
| Component | Visual Preview |
|---|---|
| **Floating Orb** | ![Orb Preview](docs/screenshots/orb.png) <br> *(Particle sphere animating attention / thinking)* |
| **Quick Bar (`Alt+Q`)** | ![Quick Bar Preview](docs/screenshots/quickbar.png) <br> *(Context chip, action chips, instant answer & replace)* |
| **Side Panel (`Ctrl+Alt+P`)** | ![Side Panel Preview](docs/screenshots/panel.png) <br> *(Multi-step agent timeline, Chat, Memory & Settings)* |
| **Approval Card** | ![Approval Card Preview](docs/screenshots/approval.png) <br> *(Diff view for file writes, Enter=Approve, Esc=Deny)* |

---

## Prerequisites

| Requirement | Supported Version | Notes |
|---|---|---|
| **Operating System** | Windows 11 | Target platform only (uses Win32 UIA, Job Objects, and SendInput) |
| **Node.js** | 22 LTS | Check with `node -v` |
| **Python** | 3.12 | Managed via `uv python install 3.12` |
| **uv** | Latest | Fast Python virtual environment & package manager |
| **System Browser** | Microsoft Edge or Google Chrome | Used for Playwright browser automation without downloading bundled binaries |
| **Gemini API Key** | Free tier (Google AI Studio) | Stored securely in Windows Credential Manager |
| **Docker Desktop** | Optional | Only needed for container-managed scenarios (e.g. Redis) |

---

## Setup & Installation

### 1. Install Dependencies

Clone the repository and install dependencies for both the Electron frontend and the Python backend:

```bash
# Clone the repository
cd clickachu

# 1. Setup the Python core sidecar
cd core
uv sync
cd ..

# 2. Setup the Electron app
cd app
npm install
cd ..
```

### 2. Browser Channel Note

Clickachu's browser automation uses Playwright connected to your installed system browser channel (`msedge` by default on Windows, or `chrome`). Clickachu intentionally does **not** download separate Chromium binaries.
- Ensure **Microsoft Edge** or **Google Chrome** is installed on your PC.
- You can change your preferred browser channel in **Side Panel → Settings → Browser Automation**.

### 3. Launch Clickachu

Start the application in development mode:

```bash
cd app
npm run dev
```

Electron will spawn the Python sidecar automatically, establish an authenticated WebSocket channel, register global hotkeys, and render the orb.

### 4. Configure Your API Key in Settings

1. Press **Ctrl+Alt+P** or right-click the Orb and choose **Settings**.
2. Under **Models & API**, paste your free **Google Gemini API key** (`AIzaSy...` from [aistudio.google.com/apikey](https://aistudio.google.com/apikey)).
3. Clickachu defaults to `gemini-2.5-flash-lite` for fast routing and `gemini-2.5-flash` for Ask & Agent tool execution via Google's OpenAI-compatible Gemini endpoint (`https://generativelanguage.googleapis.com/v1beta/openai/`).
4. Click **Save**.
5. Your API key is encrypted and stored in Windows Credential Manager via `keyring`. It is never written to disk, settings files, or version control.

---

## Ollama on this PC or another computer

In **Settings → Ollama (Local / Remote)**, choose **This PC** or **Another computer**.
For another computer, enter its IP address (for example `10.72.23.238`), click
**Test connection / refresh models**, choose an installed model, and **Save**. The standard
port and `/v1` endpoint are added automatically for bare IPs/hostnames; full HTTP(S) URLs
and custom ports are supported. Testing an address does not change the active configuration.
Local and remote main/fast models and CPU compatibility preferences are remembered separately.
There is no automatic fallback to another server when a connection fails.

Before remote use:

1. Put both devices on the same trusted Wi-Fi or reachable private network.
2. Install Ollama and download the model **on the host**.
3. For a Mac running the Ollama app, run `launchctl setenv OLLAMA_HOST "0.0.0.0:11434"`,
   then completely quit and reopen Ollama. Allow incoming Ollama connections in the firewall.
4. Keep the host awake and powered. Find its Wi-Fi IP and enter it in Clickachu.
5. Leave CPU compatibility off to use the host GPU. Windows Ollama can stay closed for remote use.

Clickachu connects directly to the selected endpoint and does not require Windows `OLLAMA_HOST`.
Saving **This PC** removes that override from Clickachu's process and the current Windows user's
saved environment. It cannot change variables in already-open terminals or other running apps.
In any PowerShell window previously configured for remote Ollama, run:

```powershell
Remove-Item Env:OLLAMA_HOST -ErrorAction SilentlyContinue
```

Start/restart Windows Ollama for local inference. Machine-wide environment settings are not changed.
Remote prompts and context travel to the selected host. Ollama's API has no built-in authentication;
keep it on a trusted private network and do not forward port 11434 publicly. Update the address in
Settings if the host IP changes. The Settings panel includes these setup and cleanup instructions.

---

## Global Hotkeys

| Hotkey | Action | Description |
|---|---|---|
| <kbd>Alt</kbd> + <kbd>Q</kbd> | **Quick Bar** | Captures foreground window selection, opens Quick Bar with action chips. Press <kbd>Esc</kbd> to cancel and close. |
| <kbd>Ctrl</kbd> + <kbd>Alt</kbd> + <kbd>P</kbd> | **Toggle Side Panel** | Slides in the side panel from the right. Shows Chat, Memory, Log, and Settings. |
| <kbd>Ctrl</kbd> + <kbd>Alt</kbd> + <kbd>V</kbd> | **Voice Input** | Push-to-talk voice recording. Transcribes speech locally via faster-whisper and populates the Quick Bar. |
| <kbd>Ctrl</kbd> + <kbd>Enter</kbd> | **Replace Selection** | Replaces the captured selection in the source application with the generated answer. |
| <kbd>Enter</kbd> / <kbd>Esc</kbd> | **Approve / Deny** | When focused on an ApprovalCard, <kbd>Enter</kbd> approves the action; <kbd>Esc</kbd> denies it. |

---

## Privacy & Security Model

Clickachu is designed with local-first security and defense against prompt injection:

```
┌─────────────────────────────────────────────────────────────┐
│                       LOCAL PC                              │
│  • Memory Store (SQLite + FTS5) in %APPDATA%\Clickachu     │
│  • Credentials in Windows Credential Manager               │
│  • Job Objects & Process Management                        │
│  • Local Whisper STT Model                                 │
│  • Policy Engine & Allowed Root Directory Bounds           │
└──────────────────────────────┬──────────────────────────────┘
                               │ Prompts (Redacted + Wrapped)
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                  GOOGLE GEMINI (LLM)                        │
│  • Receives untrusted context wrapped in data tags          │
│  • All secrets, tokens, passwords automatically REDACTED   │
│  • Returns PROPOSALS only (never executes)                 │
└─────────────────────────────────────────────────────────────┘
```

### What Stays 100% Local
- **All Databases**: Memories, action logs, suggestions, and project profiles are stored in `%APPDATA%\Clickachu\clickachu.db` (SQLite with WAL mode).
- **Credentials**: Your Gemini API key is stored in Windows Credential Manager via `keyring`.
- **Local Voice Transcription**: Speech-to-text runs locally on your machine via `faster-whisper` (`base.en`). Audio never leaves your PC.
- **Process Supervision**: Background process monitoring runs within Windows Job Objects on your machine.

### Background Web Research

With automatic lookup enabled, the selected model classifies external factual questions for background
research regardless of their wording or topic name. Recent-event language also forces a current lookup,
and prompts use the computer's current date. Greetings and clear desktop actions skip this extra model
call; other requests use a small classification call on the same model, not a second loaded model.
Search terms are sent
to a public search endpoint; this does not open Edge or use your browser profile. Disable automatic
lookup in Settings to keep ordinary questions local; explicit online requests still search.
The same selected model controls research through validated JSON decisions: choose a query and source,
evaluate returned evidence, refine a query, switch sources, read a retrieved page, or answer with
citations. Search transport does not rewrite topics or decide factual relevance by word overlap.
The controller chooses Bing, Wikipedia search, or Google News, including an optional news time window.
It can take at most five research actions per run, within the normal task time/token budgets.
Repeated identical actions and citations to unobserved sources are rejected. Extra research rounds
cost additional inference time on a local model; this does not guarantee every answer is correct. Public
endpoints and source pages can block access or be unavailable. Clickachu reports those failures rather
than treating them as proof that a topic does not exist. Local memory retrieval remains SQLite FTS5.

### What Goes to the LLM
- Only the specific prompt and selected context necessary to answer your request are sent to Gemini.
- **Secret Redaction**: Every outgoing prompt is scrubbed by `context/redact.py` before leaving your PC. API keys (`sk-...`, `AIza...`, `ghp_...`, AWS keys, bearer tokens) and passwords are automatically redacted to `[REDACTED]`.
- **Untrusted Data Wrapping**: All external content (selected text, web pages, tool stdout/stderr) is strictly wrapped in `<untrusted_data source="...">...</untrusted_data>`. The system prompt instructs the model to treat all wrapped blocks as inert data to prevent prompt injection.
- **Memories Are Explicit**: Memories are never written automatically from tool output or web content. They are only written from your own direct messages (e.g. `"Remember that..."`) or explicit approval.

---

## Architecture (PLAN §3)

```
┌──────────────────────────── Electron (Node, TypeScript) ────────────────────────────┐
│ main process                                                                         │
│  • sidecar.ts   spawn Python, random port + token, wait for READY, restart on crash  │
│  • hotkeys.ts   globalShortcut: Alt+Q / Ctrl+Alt+P / Ctrl+Alt+V                      │
│  • windows/     orb.ts · quickbar.ts · panel.ts                                      │
│  • tray.ts      menu: open panel, pause watcher, memory off, quit                    │
│  • preload      exposes window.clickachu = { backend(), show/hide, setClickThrough } │
│ renderer (React + Vite, 3 entry pages)                                               │
│  • orb.html  (Three.js particle sphere)  • quickbar.html  • panel.html               │
└───────────────┬──────────────────────────────────────────────────────────────────────┘
                │ HTTP (CRUD) + WebSocket (runs, events)   127.0.0.1:<random>, Bearer token
┌───────────────▼──────────────────────────── Python core (FastAPI) ──────────────────┐
│ api/            http.py · ws.py (broadcast hub)                                      │
│ llm/            provider.py (interface) · openrouter.py · router.py · prompts.py    │
│ agent/          orchestrator.py (ask + agent runs, step cap, cancel)                 │
│ tools/          base · registry · policy · apps · windows · files · processes ·     │
│                 browser · memory_tools · ui_input                                    │
│ automation/     com_worker.py   (single STA thread: pywinauto, UIA, SendInput)       │
│                 browser_worker.py (own asyncio loop: Playwright)                     │
│ context/        capture.py · selection.py · builder.py · redact.py                   │
│ memory/         store.py (SQLite + FTS5) · dedup.py                                  │
│ projects/       profiles.py · detect.py                                              │
│ watchers/       process_manager.py · filters.py · fullscreen.py                      │
│ voice/          stt.py (faster-whisper, sounddevice)                                 │
│ db/             schema.sql · conn.py                                                 │
└──────────────────────────────────────────────────────────────────────────────────────┘
Data: %APPDATA%\Clickachu\  → clickachu.db · browser-profile\ · logs\ · models\
```

### Hard Rules
1. The LLM only ever returns text or **tool requests**. It never executes anything directly.
2. The **policy engine** in code decides execution permissions from tool tiers, argument validation, and directory boundaries.
3. All tool output, selections, file contents, and web pages reach the LLM wrapped as `<untrusted_data source="...">...</untrusted_data>`.
4. Memories are only written from the user's explicit messages or approvals.
5. Every executed tool call is recorded in `action_log`.
6. COM/UIA/SendInput calls run strictly on the dedicated single-threaded COM worker; Playwright runs on its own browser worker.

---

## Troubleshooting

### Hotkey Conflicts
- If **Alt+Q** or **Ctrl+Alt+P** fails to trigger, another application (e.g. GeForce Experience, PowerToys, or Discord) may have registered the shortcut first.
- Clickachu logs a warning in console if a shortcut cannot be registered. You can still open the Quick Bar and Side Panel by right-clicking the Orb or clicking the system tray icon.

### UI Automation (UIA) Limits & Clipboard Fallback
- Certain custom or legacy applications do not support Microsoft UI Automation TextPattern (e.g. some game consoles, custom canvas editors, or older Win32 controls).
- Clickachu automatically falls back to an atomic clipboard copy chord (`Ctrl+C` for GUI apps, `Ctrl+Shift+C` for Windows Terminal/VS Code terminals), restores your previous clipboard history immediately, and tags the context chip with `Captured via clipboard`.

### Elevated / Administrator Windows (UIPI)
- Windows User Interface Privilege Isolation (UIPI) strictly forbids lower-privilege processes from sending synthetic keystrokes, window focus commands, or UIA automation requests to elevated (Administrator) windows.
- When an elevated window is targeted, Clickachu detects the permission boundary and displays a clear message: `"can't control admin windows"`.
- **Solution**: If you need Clickachu to automate tasks in Administrator command prompts or elevated tools, launch Clickachu itself as Administrator.

---

## Development & Testing Commands

```bash
# App checks
cd app
npm run typecheck    # TypeScript verification (node + web)
npm run lint         # ESLint check
npm run dev          # Launch in development mode

# Core checks
cd core
uv run pytest        # Run test suite
uv run ruff check .  # Python linting and formatting
```

See [docs/CHECKLIST.md](docs/CHECKLIST.md) for verification status and [docs/DEMO.md](docs/DEMO.md) for the end-to-end demo guide.

## Hybrid agent runtime

Clickachu supports isolated Python, individually approved host scripts, on-demand tool discovery,
window observations/actions, and durable task checkpoints. See [the hybrid agent guide](docs/HYBRID_AGENT.md)
for setup, permissions, model capabilities, and current limits.
