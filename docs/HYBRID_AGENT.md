# Hybrid desktop agent

Clickachu keeps direct tools for common actions and adds generated code for custom/batch work.
Normal Ask responses remain text-only. Agent requests load a small set of relevant tools plus
`discover_tools`; discovery adds schemas to the next model request without another routing model.
The optional model router is disabled by default. Explicit Ask/Agent selection still wins.

## Execution

- **Isolated Python** is enabled by default. Start Docker Desktop and prepare the image once:
  `docker pull python:3.12-slim`. Clickachu does not pull images automatically.
- Each isolated call starts a container with no network, a read-only root filesystem, dropped
  Linux capabilities, bounded CPU/memory/processes, and a writable conversation workspace.
  The generated script is mounted separately, read-only. Files persist; interpreter variables do not.
- `workspace_files` imports files from configured allowed roots, lists/reads artifacts, and
  exports them to allowed writable destinations after approval. Transfers are limited to 50 MB
  per file and do not overwrite files. There is no total workspace disk quota yet.
- **Host Python/PowerShell** is disabled by default. Enable it in Settings → Agent capabilities
  when needed. Every script requires individual approval showing its complete code and scope.
  Its working directory must be allowed and writable, but arbitrary code runs with the user's
  OS access: folder checks do not sandbox it. It may access files, applications, and the network.
- Host execution uses the sidecar Python environment or installed PowerShell. The isolated image
  initially contains Python's standard library. Additional libraries must be installed/prepared
  in the relevant environment; code execution does not automatically install dependencies.
- Output is bounded and redacted. Timeouts and cancellation terminate managed execution;
  Windows Job Objects tie child processes to the script lifetime. No automatic host fallback
  occurs when Docker is missing, stopped, or lacks the image.

## Desktop interaction

Prefer APIs and libraries, then browser DOM/accessibility controls, then screenshots and input.
`observe_desktop` reads a compact accessibility tree in one uniquely identified window. Each
control has a reference such as `c1`; request an image only for visual fallback. `desktop_action`
accepts control refs for clicks and text entry, keyboard shortcuts and scrolling. Each action
consumes its observation, checks window identity/position, and returns a fresh observation.
Dispatch is not marked as verified task completion.

For a search field, `fill` with `submit=true` can enter text and press Enter in one tool call.
The optional `wait_for_text`/`wait_control_type` condition polls locally for the result, avoiding
extra model calls. `wait_for_ui` can also be called independently. Waits exclude editor values
by default so a typed query cannot masquerade as a search result. Missing windows during
loading are retried until the bounded timeout. Host scripts invalidate previous UI references.

Script execution remains available during UI tasks so the agent can switch between methods.
Desktop approvals can apply to one action or the same window for the current task; window grants
do not survive the run. Host scripts still require individual approval.

Screenshots are sent only with image input enabled. Automatic mode recognizes Gemini models;
unknown/local models require an explicit image-capability setting. Screenshot pixels are not
text-redacted: only use image input for windows whose contents may be sent to the selected model.
Administrator windows are rejected. Running scripts and desktop actions can still fail due to
application state, missing dependencies, UAC, inaccessible controls, or model mistakes.

## Long tasks and recovery

Settings expose model-turn, elapsed-time, output-token and consecutive-failure budgets.
The default is 24 model turns, 600 seconds, 16,000 output tokens and three consecutive failures.
There is an additional action cap of four times the turn budget. Time budgets include approvals;
an already-running synchronous COM operation cannot always be interrupted immediately.

Checkpoints in SQLite retain the objective, recent evidence, pending action, usage and status.
Completed tool exchanges are compacted as units so tool calls remain paired with their results.
Ask to **continue** in the same conversation to load the checkpoint and recheck current state.
Actions are not automatically replayed, and approvals are not restored. A crash during an action
leaves a warning that it may have executed. Concurrent runs in the same conversation are rejected.
The current implementation resumes through a new model turn, not a suspended interpreter.

Progress names the actual operation about to execute. The Activity details dropdown reports
the plan, model-call count, action count and total elapsed time. Terminal stdout/stderr and UI
observations are streamed separately from the final answer. Provider
logs also include tool-schema size and time to first output. These measurements do not prove a
speed improvement: compare success and duration on representative tasks before/after changes.

## Reasoning display

Activity and tool results work with every model. The Provider reasoning dropdown appears only
when the provider sends a separate reasoning field; explanatory prose is never relabeled as
private thinking. Supported reasoning effort is configurable independently of the dropdown.
Gemini uses its compatible reasoning options and requests thought summaries when enabled;
Ollama uses supported effort controls. Some models cannot disable reasoning or expose it.
Unknown providers/models may need their own adapter; reasoning support is not universal.

For Ollama agent decisions, Clickachu requests a constrained JSON object containing one tool
name and its arguments, or a short final answer after execution. This avoids relying on a local
model's native tool-call parser. Available tool names and argument shapes constrain decoding;
the registry, Pydantic validation and execution policy still decide what may actually run.
Duplicate consecutive successful side effects are suppressed before re-execution. The controller
returns a recovery instruction with the existing state; continued repetition stops the run.
Opening any app returns fresh accessibility controls when available, avoiding a separate model
turn merely to request inspection. Unrelated selected text is omitted from app-control requests.
Each model turn binds desktop_action's schema to the latest observation ID and actual control
references. With no current observation the action tool is withheld until inspection. Invalid or
expired references are rejected before asking for approval, and previews include the control name.
The local model generates only the semantic action arguments; the controller supplies the bound
observation ID. Action-specific decoding requires nonempty keys, a ref/text for editing, or a
grounded click target. Schema compaction preserves real fields named `title` or `description`.
Small accessibility samples are labelled as limited; missing content requires inspection/waiting
or visual fallback rather than guessing a different button.

Local action prompts use a 9,000-character text budget, shorter tool descriptions, and the latest
observed controls. This is a text-size estimate, not an exact tokenizer limit. Original objectives
and system constraints are retained; oversized requests fail explicitly. Full outputs remain in
Activity. Native observations prefer actionable controls and default to 60 controls; `query` can
filter for a target. The `nodepad` and `note pad` spellings use the bounded Notepad writing flow.
Stable system instructions and the tool catalog precede changing results to permit prefix-cache
reuse. Full previous checkpoints are supplied on explicit resume; new tasks retain ordinary
conversation history and confirmed context. UI ancestry is grouped once in the model input,
while Activity keeps full observations. Local action decoding is deterministic and keeps the
same model resident for 15 minutes. Timings log model load, uncached input processing, output
generation and cached-token counts without logging prompt contents.
Control refs stay stable within a task/window while their native identity remains unchanged.
Removed control IDs are never reassigned to new controls. The current full UI state is ordered
before changing task history, and older snapshots of that same window are superseded in the
model input. Current evidence from other windows remains available. Window identity, enabled
state and freshness checks still run before input; model caching does not authorize stale actions.
The immediate UI tool list omits isolated code unless requested; discovery can load code for
method switching. Isolated containers cannot control the host desktop.

Generic media play/resume/pause requests can use `control_media` directly with no model call.
It targets the named application's Windows media session, issues explicit play/pause, and
reads back playback state and track metadata. This resumes an existing queue; it does not select
a requested song, log in, guarantee audible output, or buy a subscription. Specific songs use
UI search. When the direct method fails, its observed result is handed into the shared agent
loop in the same run. The model then inspects the live app and chooses the next method; no
application-specific button names, coordinates, or playback sequence are programmed in.
Editable ComboBox search fields use their UI Automation Value pattern.

If Ollama's runner reports Vulkan `device lost`, Settings → Agent capabilities → CPU compatibility
uses the native `/api/chat` runtime option `num_gpu=0`. It affects Clickachu requests, not global
Ollama configuration. It avoids GPU offload and may generate more slowly. This does not repair a
GPU driver. Local 5xx requests are not silently retried, and completed desktop actions remain.

Research preserves explicit subjects and uses DuckDuckGo as its primary engine. One search and
at most two matching page reads precede one short synthesis call. Transport fallback uses the same
engine; the model cannot keep searching or switch engines in this ordinary flow. Login/navigation
pages and name-mismatched candidates are rejected. Trafilatura extracts main content, and page
reads are cached briefly. Retrieval is not factual verification: claims still depend on the local
model interpreting its evidence correctly. Missing results never establish nonexistence.

Factual Notepad briefs such as a description about an organization use this retrieval pipeline
before composing the document. The controller requests approval for the generated content, writes
once and verifies the editor. Names are preserved literally; a misspelling is not silently changed
to another organization. Creative writing and explicit offline briefs do not require retrieval.

Settings optionally select an already downloaded **local summary model** for source summaries and
factual drafts. Empty means use the main local model. Desktop decisions retain the main model;
there is no additional model-routing call. The alternate summary model is released after use to
reduce resident memory. Smaller models can be faster but can omit or misinterpret evidence.

The native inspector now uses one cached UIA subtree read, tries raw view when the control view
is sparse, and keeps ancestor labels to distinguish repeated controls. Actions retain their COM
element and recheck its live identity. A unique process name can identify a window even when its
title changes to a song or document. Multiple matching windows require a specific title.
Windows OCR is an offline fallback (`mode=ocr`) for inaccessible text. OCR word references support
clicks and are rechecked for changed text/position before input. Password rectangles exposed by
UIA are masked in image/OCR capture. OCR-only observations do not permit arbitrary pixel clicks.
OCR cannot reveal icon semantics or hidden controls. App accessibility and capture support vary.

Bounded media requests have controller-owned completion checks. Once Windows confirms the desired
state, the run ends without another model call. Follow-ups resolve the last confirmed media app
or a unique available session. This is the same Windows media capability for all supported players,
not an app-specific playback script. Activity records total, model and tool execution time.
Explicit single-button requests also finish after one dispatch to the matching app/control.
The controller rejects unrelated refs before approval and checks the tool's dispatch evidence;
it does not use a model-generated success claim. This confirms the requested input, not an
arbitrary downstream app outcome. Compound/multiple-click requests retain the general loop.
Native Invoke patterns are preferred when supported, avoiding a physical click through overlays.
Inspection and loading waits keep the window handle/process already identified by the task.
Duplicate captions therefore do not force a new title lookup. A changed process or closed window
requires selecting the target again; its handle alone is never treated as permanent authority.

## Validation

Run backend tests with `uv run pytest -q`, frontend typechecking with `npm run typecheck`,
ESLint with `npm run lint`, and UI checks with `npm run test:ui` in their respective directories.
`tests/test_hybrid_agent.py` covers host execution, timeout/cancellation boundaries, file transfers,
checkpoints, tool discovery, image-message ordering and failure budgets using deterministic
fixtures. Docker and interactive Windows application behavior need environment-specific checks.
