# Local agent reliability and latency plan

Keep the existing Electron, Python and Ollama stack. Preserve work already in the checkout.
Use shared capabilities and completion rules across applications; no application-specific
click sequences. The user requires fully local model inference.

## Execution order

1. **Completion and context** — represent bounded requests as explicit goals, resolve follow-up
   targets from confirmed conversation state, and finish immediately on verified completion.
   Do not ask the model to restate a successful mechanical operation. Validate model-selected
   actions against the goal before another side effect. Record model and tool timing separately.
2. **Windows observation** — use the native UI Automation cache/bulk APIs, inspect control and
   raw views adaptively, preserve relevant state and ancestry, and provide an offline Windows
   OCR fallback. Bind actions to observations and reject stale/changed targets before approval.
3. **Research** — one primary engine, literal subject preservation, bounded page reads, cached
   retrieval, main-text extraction, short evidence and one synthesis call. No repeated searches
   driven by the model. Empty/blocked retrieval never proves that a subject does not exist.
4. **Factual writing** — compose retrieval and writing in the controller: gather source evidence,
   generate the document once, write it to a fresh editor, verify the text and finish. Resolve
   ambiguities from evidence or ask a concise question; never silently rename an entity.
5. **Validation and measurement** — add regressions for completed-action loops, follow-ups,
   incomplete trees, OCR safety, research and sourced writing. Run the existing required checks.
   Test representative tasks through the real Clickachu UI with the selected local model.
   Separate app results, test fixtures and actions performed by the coding assistant.

## Acceptance evidence

- Equivalent simple commands and follow-ups complete without repeated model/action loops.
- Real UI inspection exposes usable app controls or reports a bounded, actionable limitation.
- No guessed reference, out-of-window coordinate or changed OCR target can receive input.
- Research uses its primary engine once for a simple subject and cites retrieved evidence.
- Factual document requests cannot be answered from an unsupported model claim of nonexistence.
- Report measured request time, model calls and actual outcomes, including any failures.

## Progress

- Implemented completion contracts, confirmed context, bounded retrieval and sourced writing.
- Installed native UIA, Windows OCR, DDGS and Trafilatura in the project environment/lockfile.
- Added cached UIA snapshots, adaptive raw view, local OCR and stale-target checks.
- Added optional installed local summary model and separate model/tool timing in Activity.
- Regression suite: 519 passed, including window-selection, click completion, local-decoding, UI-cache and source-filter regressions.
  Frontend production build, lint and four UI checks passed.

## Live acceptance log (Clickachu UI, local inference)

| Request | Result | Time |
| --- | --- | --- |
| Open Spotify and play music | Windows reported playing; one action, zero model calls | 6.1 s |
| Stop the music playing in Spotify | Windows reported paused; one action, zero model calls | 0.5 s |
| Play the music | Resolved prior app and confirmed playing; one action, zero model calls | 0.5 s |
| Pause it | Resolved prior app and confirmed paused; one action, zero model calls | 0.5 s |
| Inspect the UI in Spotify, first attempt | Failed because changing window title omitted app name | 0.2 s |
| Inspect the UI in Spotify, after generic process-name resolution | 60 controls, including search and player controls; cached UIA backend | 0.8 s |
| Write a description about Kalvium in Notepad | One search, two matching page reads, one main-model draft; after reviewing/approving, Clickachu wrote and verified 455 characters in a fresh document | 25.8 s model + 8.0 s tools, plus manual approval wait |
| Open Calculator and click Nine, final controller | Correct observed control; one approved dispatch, immediate completion, no extra model call or repeated click | 21.25 s model + 3.45 s tools, plus approval wait; 2 model calls / 2 actions |

These requests were submitted through Clickachu; the coding assistant did not press its media
buttons to rescue them. Playback state does not establish audible output or guarantee that a
different client/user will not subsequently pause it. Inspector success alone does not establish
that the local model can reliably complete arbitrary multi-step UI tasks.

The first UI research run finished in 29.4 seconds: one DuckDuckGo search, a page timeout, a
successful official-page read, and one summary call. The optional 1.5B local model added an
unsupported funding statement; this was not accepted as an accurate answer. A subsequent
same-evidence CLI comparison with deterministic sampling produced a more accurate answer with
the main 4B model (32.1 seconds versus 13.2 seconds for the smaller model, including model loading).
The experimental summary override was cleared through Settings. All summaries therefore retain
the main `qwen3:4b-instruct` model, with CPU compatibility enabled. A source-only prompt and
deterministic decoding reduce variability but do not prove every generated claim is grounded.
Tool timeouts no longer display as expired approvals.

Sourced writing passed a live end-to-end test. The document is left open and unsaved. The coding
assistant reviewed and approved the preview, but did not type the document or rescue the write.
The Calculator test dispatched a click on the observed `Seven` button and returned 46 controls;
the model reported display 7. It took 105.3 seconds of model time and 4.6 seconds of tools, plus
approval wait. A wrong isolated-code attempt failed because Docker was unavailable before the
model recovered to UI input. This is not accepted as satisfactory latency. The immediate UI
catalog now loads code only on explicit request or discovery, and the local prompt explicitly
states that isolated Python cannot operate the host desktop. Public web retrieval still needs
network access; all model inference stays local.

## Decision latency measurements

The selected 4B model remains unchanged (Q4_K_M, CPU, 4096-token context). At diagnosis the PC
reported 15.7 GiB total RAM and 1.5 GiB available, with the model using about 3.2 GB in system
memory and zero GPU memory. Memory pressure may add variability; it was not isolated as a cause.

A no-execution local inference fixture requested the next action for opening Calculator:

| Measurement | Wall time | Uncached input processing | Output generation | Cached/input tokens |
| --- | --- | --- | --- | --- |
| Earlier prompt arrangement | 12.83 s | 10.00 s | 2.59 s | 276 / 700 |
| Stable-prefix arrangement, first request | 8.77 s | 6.87 s | 1.80 s | 280 / 697 |
| Stable-prefix arrangement, changed next request | 2.45 s | 0.33 s | 2.09 s | 691 / 697 |

Every fixture selected `open_app(Calculator)`. These are representative inference measurements,
not end-to-end UI timings or a statistically controlled universal speedup. Prefix reuse benefits
consecutive similar decisions; fresh content still costs time. The old 30-second live decision
also included conversation/checkpoint input and potentially model loading. The older CPU adapter
did not expose its breakdown, so its exact split cannot be reconstructed.

Changes: stable instructions/catalog before changing results, full checkpoint only on resume,
grouped UI ancestry, controller-supplied observation IDs, action-specific argument decoding,
deterministic action sampling, 15-minute model residency and metadata-only inference timing logs.
The schema compressor also had a correctness bug: it removed real argument fields named `title`
while removing schema metadata. Those fields are now preserved and regression-tested.

The final full-UI synthetic benchmark in `core/benchmarks/ui_latency.py` selected the correct
`Seven` and `Eight` refs in **5.16 s** and **4.89 s**. Each prompt contained the full 46-control
fixture; 1275 of 1375 input tokens were cached, with 2.23/2.09 seconds of uncached processing and
2.87/2.77 seconds of generation. These are warm inference-only fixtures, not real PC clicks.
The initial cold fixture before correcting tool-history ordering took 28.47 seconds and
unnecessarily re-inspected; it was rejected. Tool-result history now explicitly records that a
UI result was returned, and closing instructions avoid Ollama treating trailing assistant text
as a response prefill. Stable native refs, ordered full UI state, and superseded-snapshot removal
keep unchanged controls reusable without omitting the current tree. Safety regressions cover
reordering, removed refs, empty trees, stale OCR and controller-bound observation IDs.

The next live run chose `open_app` in 9.58 seconds and the correct observed `Eight` ref in
17.83 seconds. Native tools used 3.67 seconds. A subsequent 19.48-second model turn proposed
the same click again; it was stopped before approval. This exposed a missing controller
completion condition for a literal single click. The new general click contract finishes on
the tool's matching app/control dispatch evidence, rejects unrelated controls before approval,
and avoids that extra model turn. Tests cover numeric labels, arbitrary app/button names,
multiple-click exclusions, explicit fixture approval and the native Invoke path.

A duplicate-title test then exposed title-based reinspection failing when two Calculator windows
were open. Inspection and waits now reuse the task's already identified window handle and PID,
while rejecting a changed process/closed window. The subsequent real Clickachu UI test passed:
`open_app`, then exactly one `desktop_action` on the observed `Nine` button, then controller
completion. The coding assistant only submitted the task and approved the reviewed input.
The local model used 21.25 seconds total; tools used 3.45 seconds. No further model call was made
after the click. This validates the bounded request, not arbitrary navigation in every app.

Implementation and verification for this pass are complete. The development app and local Ollama
were restarted with the final code. Remaining limits: CPU processing of new UI can still take
10–30 seconds; the local model can make semantic mistakes; some apps expose incomplete UI;
Docker was unavailable during its live attempt, so isolated execution was not live-validated.

## Selected text and personal memory follow-up (5 November)

Alt+Q previously treated a visible HUD as if it were focused. A background-visible HUD therefore
reopened with null context, removing the action chips. Repeated shortcut presses could also steal
focus during capture. The shortcut now captures the foreground source before focusing the HUD,
coalesces pending captures, preserves an already-focused HUD, and opens in text mode rather than
implicitly starting the microphone. A live Notepad check captured 153 characters through UIA and
displayed Summarise, Explain, Translate, Rewrite, Fix and Do. Further live input checks were stopped
when computer-use detected the user's input. Manual summarise routing and context propagation are
covered by regression tests; do not mistake these fixtures for a completed live model answer.

The HUD-to-panel handoff now preserves conversation and context IDs, including opening the panel
before submitting the first run. Follow-up messages carry the selected source. Remove and New chat
clear it; expired selection requests explain how to recapture with Alt+Q. Selected-text questions
bypass research routing instead of losing the source or querying the web.

Automatic personal memory was absent: only explicit Remember requests saved notes. Clear leading
first-person facts and preferences now save locally without an extraction-model call. Supported
examples include name, home city, school, occupation/employer, likes/dislikes and preferences. This
is conservative phrase extraction, not arbitrary fact inference or model training. Ambiguous
statements still need an explicit Remember request. Old chats are not retroactively mined.

Writes use the existing SQLite memory store and saved/Undo notice. Clear single-field corrections
supersede previous values; Undo restores them. Duplicate notes do not create another row. Disabled
memories stay disabled, and Memory Off blocks automatic writes and recall. Quoted/pasted source
prompts, selected documents, rewrite/translate source text, code, recognized secrets and transient
statements are excluded. Explicit model-extracted notes must also be grounded in the user's text.

Local recall now reserves a small preference budget even when FTS finds no keyword match. Personal
questions retrieve the requested profile fields and bypass public research; regular local prompts
retain the three-note budget, while personal questions can retrieve up to eight. Persistence,
duplicates, corrections, Undo, disabled/off state, source exclusion and one-answer-call behavior are
tested with a temporary database/provider fixtures. This does not establish perfect memory for
every phrasing or guarantee the local model obeys every recalled preference.

Verification: 547 backend tests and six renderer/main-process UI regressions passed, plus the
production frontend build, TypeScript checks, ESLint and targeted Python lint. Memory persistence
and full selected-source answering were verified with fixtures, not a completed live model run.

## Media selection, app grounding and repeat recovery (5 November)

The supplied logs exposed several controller gaps rather than one missing UI library:

- Bare `play`/`pause` bypassed the deterministic playback path, and `plsy` missed it entirely.
  Clear short playback commands now use confirmed conversation media context. A unique one-edit
  verb correction is allowed only in that context; song and app names are preserved literally.
- Named content had no completion contract. A verified transport state for the old queue could
  therefore be mistaken for success. Content requests now start the generic app/UI workflow,
  exclude queue-resume controls from the model catalog, and require matching title, requested
  artist and playing state from Windows media read-back. No app-specific Spotify search code
  or song-name allowlist was added.
- Discovery could return an empty success, while read tools were exempt from repeat checks.
  Unmatched app names now expose general desktop capabilities. Duplicate discovery calls are
  suppressed, and unchanged read evidence is detected across observation-ID changes. Reopening
  an already opened app is also suppressed across intervening observations.
- Fresh app requests carried unrelated action-result history. Explicit app launches now start
  in the named app, omit unrelated prior tool history, reject unrequested app launches, and bind
  native input to the observed process of the requested application when that identity is known.
- Structured local action generation could inherit reasoning settings or publish malformed,
  repetitive model output. Structured requests now disable thinking, suppress that action-only
  trace, and abort bounded repetitive output before any action is dispatched. Ordinary answer
  reasoning remains separate. Invalid/partial JSON is never executed.

Fresh UI data alone did not solve the model's decision errors. In no-execution fixtures, the
installed 4B local model still selected another observation, a wait, or reopening Chrome when the
needed controls were already present. UI-step constraints now expose the applicable operation
and observed refs when the next search/result operation is clear. Missing or ambiguous targets
retain inspection and general navigation. Clickable rows can require activation rather than a
single click; they are not automatically treated as successful playback or repeatedly forced.

When exactly one search field or matching Play button is observed, the controller can execute
that unambiguous operation without a model call, using the existing approval and stale-ref checks.
The native-control fixture completed open → search → select → verified playback with zero model
calls. This is a fixture result, not a claim that every real app exposes such controls. A brief
bounded metadata wait handles track-switch propagation without repeatedly sending playback input.

The inspector now retains named list/data-row ancestry and cached editor values. This associates
a row's Play button with its title/artist and allows read-back of browser addresses. Password
values remain excluded; no extra per-control COM query was introduced. Opening a browser alone,
or displaying a search page/typed address, does not complete a compound destination request.
Named destinations need resulting page evidence; explicit URLs need a matching address and a
loaded document. A request naming two native apps still permits the second requested app.

The final forced-model benchmark used 60-control synthetic states and executed no PC input:

| Decision fixture | Selected expected next control | Model wall time |
| --- | --- | --- |
| Song search | Search field, requested title/artist, submit | 42.14 s |
| Song result | Matching result's Play button | 25.95 s |
| Browser navigation | Address field, submit website name | 23.14 s |
| Browser search result | Matching destination link | 20.84 s |

These demonstrate next-control selection, not end-to-end PC task completion. The first request
included a cold model/prompt. CPU inference remains expensive when the deterministic UI path is
inapplicable. A live PC check was stopped when computer-use detected user input, so no fresh live
song-selection or full browser-navigation success is claimed for this pass.

A duplicate-instance startup issue was also corrected: a secondary process now exits immediately
and its activation event opens the existing HUD without implicitly enabling the microphone.
The old idle development app/sidecar was restarted so Python changes can take effect. The current
design still requires a restart for backend code updates; renderer hot reload alone is insufficient.
