const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')

function loadSource(file, overrides = {}) {
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
