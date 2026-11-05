const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')

function loadSource(file, overrides = {}, globals = {}) {
  const filename = path.join(__dirname, '..', file)
  const { outputText } = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  })
  const exports = {}
  vm.runInNewContext(outputText, {
    exports,
    require: (name) => overrides[name] || require(name),
    Date, crypto, console, setInterval, clearInterval,
    ...globals,
  }, { filename })
  return exports
}

test('spoken introduction remains in the written answer throughout streaming', () => {
  const { stripVoiceTags, splitVoiceAndTechnical } = loadSource('src/shared/voiceSpeech.ts')
  const summary = 'I can help with your desktop tasks.'
  const raw = `<voice>${summary}</voice>\n\n- Open apps\n- Create documents`
  for (let end = '<voice>'.length + summary.length; end <= raw.length; end++) {
    assert.ok(splitVoiceAndTechnical(raw.slice(0, end)).cleanMarkdown.includes(summary))
  }
  assert.ok(stripVoiceTags(raw).startsWith(summary))
  assert.ok(stripVoiceTags(raw).includes('- Open apps'))
})

test('Alt+Q captures a background-visible HUD without stealing source focus', async () => {
  let focused = false
  let focusCalls = 0
  let captures = 0
  let resolveCapture
  const shown = []
  const bar = { isVisible: () => true, isFocused: () => focused, focus: () => { focusCalls++ } }
  const { createOpenWithContext } = loadSource('electron/main/capture.ts', {
    './windows/quickbar': { quickBarWindow: () => bar, openQuickBar: (...args) => shown.push(args) },
  }, {
    AbortSignal,
    fetch: async () => {
      captures++
      return { ok: true, json: () => new Promise((resolve) => { resolveCapture = resolve }) }
    },
  })
  const open = createOpenWithContext({ backend: async () => ({ baseUrl: 'http://local', token: 'test' }) })
  open()
  open()
  await new Promise(setImmediate)
  assert.equal(captures, 1)
  assert.equal(shown.length, 0)
  assert.equal(focusCalls, 0)
  resolveCapture({ id: 'ctx_new', selection: 'selected source' })
  await new Promise(setImmediate)
  assert.equal(shown.length, 1)
  assert.equal(shown[0][0].id, 'ctx_new')
  assert.equal(shown[0][2], false) // text shortcut never implicitly starts the microphone
  focused = true
  open()
  assert.equal(focusCalls, 1)
  assert.equal(shown.length, 1) // refocusing must not erase the captured source
})

test('panel handoff carries the source and conversation; new chat and Remove clear it', () => {
  const { applyPanelHandoff, recordChatRun, startNewChat, useStore } = loadSource('src/shared/store.ts', {
    './api': { api: { getLog: async () => [] } },
  })
  applyPanelHandoff({ conversationId: 'conv_source', contextId: 'ctx_source' })
  assert.equal(useStore.getState().activeConversationId, 'conv_source')
  assert.equal(useStore.getState().activeContextId, 'ctx_source')
  assert.equal(useStore.getState().chatHistory.length, 0)
  applyPanelHandoff({ runId: 'selected', question: 'Summarise', conversationId: 'conv_source', contextId: 'ctx_source' })
  recordChatRun({ runId: 'followup', prompt: 'Explain the second point' })
  assert.equal(useStore.getState().runs.followup.contextId, 'ctx_source')
  recordChatRun({ runId: 'removed', prompt: 'Hello', contextId: null })
  assert.equal(useStore.getState().activeContextId, null)
  recordChatRun({ runId: 'other', prompt: 'Hi', conversationId: 'conv_other' })
  assert.equal(useStore.getState().activeContextId, null)
  recordChatRun({ runId: 'reselect', prompt: 'Explain', contextId: 'ctx_new' })
  startNewChat()
  assert.equal(useStore.getState().activeContextId, null)
  assert.equal(useStore.getState().chatHistory.length, 0)
})

test('plan and native reasoning events do not replace or contaminate answer text', () => {
  const store = loadSource('src/shared/store.ts', { './api': { api: { getLog: async () => [] } } })
  const handlers = {}
  store.bindStore({
    onStatus: () => () => {},
    on: (name, handler) => { handlers[name] = handler; return () => {} },
  })
  store.recordChatRun({ runId: 'r', prompt: 'Write in Notepad' })
  handlers['run.detail']({ run_id: 'r', kind: 'plan', delta: 'Create a new document.' })
  handlers.token({ run_id: 'r', delta: 'First sentence. ' })
  handlers['run.detail']({ run_id: 'r', kind: 'reasoning', delta: 'Model-provided reasoning.' })
  handlers.token({ run_id: 'r', delta: 'Second sentence.' })
  const run = store.useStore.getState().runs.r
  assert.equal(run.text, 'First sentence. Second sentence.')
  assert.equal(run.plan, 'Create a new document.')
  assert.equal(run.reasoning, 'Model-provided reasoning.')
  handlers['run.detail']({ run_id: 'r', kind: 'activity', delta: 'Searching sources' })
  handlers['run.detail']({ run_id: 'r', kind: 'activity', delta: 'Writing answer' })
  assert.equal(store.useStore.getState().runs.r.activity, 'Writing answer')
  handlers['run.finished']({ run_id: 'r', ok: false, status: 'error', text: 'Verification failed' })
  assert.equal(store.useStore.getState().runs.r.status, 'error')
})

test('execution plan is visible without inventing a native reasoning trace', () => {
  const { RunDetails } = loadSource('src/shared/components/RunDetails.tsx')
  const html = renderToStaticMarkup(React.createElement(RunDetails, {
    run: { plan: 'Create an empty document.', status: 'running' },
  }))
  assert.match(html, /Execution plan/)
  assert.match(html, /Create an empty document/)
  assert.doesNotMatch(html, /Model reasoning/)
})

test('a tool timeout is not reported as an expired approval', () => {
  const { isApprovalTimeout } = loadSource('src/shared/timeouts.ts')
  assert.equal(isApprovalTimeout('web_fetch timed out after 12s'), false)
  assert.equal(isApprovalTimeout('Action denied: approval timed out'), true)
  const { StepTimeline } = loadSource('src/shared/components/StepTimeline.tsx', {
    '../timeouts': { isApprovalTimeout },
  })
  const html = renderToStaticMarkup(React.createElement(StepTimeline, {
    steps: [{ step_id: 'page', tool: 'web_fetch', status: 'failed', durationMs: 12000,
      summary: 'web_fetch timed out after 12s' }],
  }))
  assert.match(html, /Tool execution timed out/)
  assert.doesNotMatch(html, /Approval timed out/)
})
const ollamaHelpers = () => loadSource('src/shared/ollama.ts', {}, { URL })

test('Ollama switching remembers each server model and CPU preference across saved settings', () => {
  const { switchOllamaConnection, normalizeOllamaUrl } = ollamaHelpers()
  const local = {
    ollama_base_url: 'http://127.0.0.1:11434/v1', ollama_model: 'local-4b',
    ollama_summary_model: 'local-fast', ollama_cpu_only: true,
    ollama_local_profile: null, ollama_remote_profile: null,
  }
  let remote = switchOllamaConnection(local, false)
  assert.equal(remote.ollama_base_url, '')
  assert.equal(remote.ollama_cpu_only, false)
  remote = { ...remote, ollama_base_url: normalizeOllamaUrl('10.72.23.238'), ollama_model: 'qwen3.5:9b' }
  const restored = switchOllamaConnection(remote, true)
  assert.equal(restored.ollama_base_url, local.ollama_base_url)
  assert.equal(restored.ollama_model, 'local-4b')
  assert.equal(restored.ollama_summary_model, 'local-fast')
  assert.equal(restored.ollama_cpu_only, true)
  const reloaded = JSON.parse(JSON.stringify(restored))
  const again = switchOllamaConnection(reloaded, false)
  assert.equal(again.ollama_base_url, 'http://10.72.23.238:11434/v1')
  assert.equal(again.ollama_model, 'qwen3.5:9b')
  assert.equal(again.ollama_cpu_only, false)
})

test('Ollama incomplete remote drafts do not replace a working local profile', () => {
  const { switchOllamaConnection, normalizeOllamaUrl } = ollamaHelpers()
  const local = { ollama_base_url: 'http://127.0.0.1:11434/v1', ollama_model: 'local', ollama_cpu_only: true }
  const draft = switchOllamaConnection(local, false)
  const restored = switchOllamaConnection(draft, true)
  assert.equal(restored.ollama_model, 'local')
  assert.equal(restored.ollama_remote_profile, undefined)
  assert.equal(normalizeOllamaUrl('mac.local:12345'), 'http://mac.local:12345/v1')
  assert.equal(normalizeOllamaUrl('https://host/prefix/v1/'), 'https://host/prefix/v1')
  assert.throws(() => normalizeOllamaUrl('file:///etc'))
})

test('run errors and Stop acknowledgements release busy state and finish pending steps', () => {
  const store = loadSource('src/shared/store.ts', { './api': { api: { getLog: async () => [] } } })
  const handlers = {}
  store.bindStore({
    onStatus: () => () => {},
    on: (name, handler) => { handlers[name] = handler; return () => {} },
  })
  store.recordChatRun({ runId: 'crash', prompt: 'latest news' })
  handlers['step.started']({ run_id: 'crash', step_id: 'search', tool: 'web_search', summary: 'Searching', tier: 'read' })
  handlers['run.error']({ run_id: 'crash', message: 'Internal error' })
  assert.equal(store.useStore.getState().activeRunId, null)
  assert.equal(store.useStore.getState().runs.crash.status, 'error')
  assert.equal(store.useStore.getState().runs.crash.steps[0].status, 'failed')

  store.recordChatRun({ runId: 'stale', prompt: 'hello' })
  handlers.token({ run_id: 'stale', delta: 'Partial answer' })
  handlers['run.finished']({ run_id: 'stale', ok: false, status: 'cancelled', text: '' })
  assert.equal(store.useStore.getState().activeRunId, null)
  assert.equal(store.useStore.getState().runs.stale.status, 'cancelled')
  assert.equal(store.useStore.getState().runs.stale.text, 'Partial answer')

  store.recordChatRun({ runId: 'done', prompt: 'hello' })
  handlers['run.finished']({ run_id: 'done', ok: true, text: 'Completed answer' })
  handlers['run.finished']({ run_id: 'done', ok: false, status: 'cancelled', text: '' })
  assert.equal(store.useStore.getState().runs.done.status, 'done')
  assert.equal(store.useStore.getState().runs.done.text, 'Completed answer')
})
