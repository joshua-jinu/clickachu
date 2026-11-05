import { useEffect, useState } from 'react'
import { api } from '@shared/api'
import type { OllamaStatusResponse, Settings } from '@shared/protocol'
import {
  isLocalOllama,
  LOCAL_OLLAMA_URL,
  normalizeOllamaUrl,
  switchOllamaConnection,
} from '@shared/ollama'
import { errorText, inputClass } from '../ui'

export function OllamaSettings({
  form,
  onChange,
}: {
  form: Settings
  onChange: (settings: Settings) => void
}) {
  const [checked, setChecked] = useState<{
    endpoint: string
    revision: number
    status: OllamaStatusResponse
  } | null>(null)
  const [revision, setRevision] = useState(0)
  const current = checked?.endpoint === form.ollama_base_url && checked.revision === revision
  const checking = !current
  const status: OllamaStatusResponse = current ? checked.status : { running: false, models: [] }
  const local = isLocalOllama(form.ollama_base_url)

  useEffect(() => {
    let cancelled = false
    const timer = setTimeout(async () => {
      try {
        const url = normalizeOllamaUrl(form.ollama_base_url)
        const result = await api.getOllamaStatus(url)
        if (!cancelled) setChecked({ endpoint: form.ollama_base_url, revision, status: result })
      } catch (err) {
        if (!cancelled)
          setChecked({
            endpoint: form.ollama_base_url,
            revision,
            status: { running: false, models: [], error: errorText(err) },
          })
      }
    }, 500)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [form.ollama_base_url, revision])

  return (
    <div className="space-y-3 rounded-lg border border-purple-500/30 bg-purple-950/10 p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold text-purple-200">Ollama connection</span>
        <span className={`text-[11px] ${status.running ? 'text-emerald-300' : 'text-amber-300'}`}>
          {checking ? 'Checking…' : status.running ? 'Connected' : 'Not connected'}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-2">
        {([true, false] as const).map((useLocal) => (
          <button
            type="button"
            key={String(useLocal)}
            aria-pressed={local === useLocal}
            onClick={() => onChange(switchOllamaConnection(form, useLocal))}
            className={`rounded-md px-3 py-2 text-xs ${local === useLocal ? 'bg-purple-600 text-white' : 'bg-white/5 text-ck-muted'}`}
          >
            {useLocal ? 'This PC' : 'Another computer'}
          </button>
        ))}
      </div>
      {!local && (
        <label className="block text-xs text-ck-muted">
          Server IP address or URL
          <input
            className={inputClass}
            value={form.ollama_base_url}
            onChange={(e) => onChange({ ...form, ollama_base_url: e.target.value })}
            placeholder="10.72.23.238 or http://mac-ip:11434"
            spellCheck={false}
          />
          <span className="text-[11px]">
            An IP address is enough. Port 11434 and /v1 are added automatically.
          </span>
        </label>
      )}
      {local && (
        <p className="text-[11px] text-ck-muted">
          Uses Ollama on this PC at {form.ollama_base_url || LOCAL_OLLAMA_URL}. Start the Windows
          Ollama app first. Restart it if it was launched with a remote OLLAMA_HOST override.
        </p>
      )}
      <button
        type="button"
        onClick={() => setRevision((n) => n + 1)}
        disabled={checking}
        className="rounded bg-white/10 px-3 py-1 text-xs text-ck-text disabled:opacity-50"
      >
        Test connection / refresh models
      </button>
      {!checking && status.error && (
        <p role="status" className="break-words text-[11px] text-amber-300">
          {status.error}{' '}
          {local
            ? 'Check that Windows Ollama is running.'
            : 'Check the address, host Ollama, firewall, and that both computers can reach each other.'}
        </p>
      )}
      <label className="block text-xs text-ck-muted">
        Models installed on {local ? 'this PC' : 'the other computer'}
        <select
          className={inputClass}
          value={form.ollama_model}
          disabled={checking || !status.running || status.models.length === 0}
          onChange={(e) => onChange({ ...form, ollama_model: e.target.value })}
        >
          {!status.models.some((m) => m.name === form.ollama_model) && (
            <option value={form.ollama_model}>
              {form.ollama_model ? `${form.ollama_model} (not detected)` : 'Choose a model'}
            </option>
          )}
          {status.models.map((m) => (
            <option key={m.name} value={m.name}>
              {m.name}
              {m.size > 0 ? ` (${(m.size / 1e9).toFixed(1)} GB)` : ''}
            </option>
          ))}
        </select>
      </label>
      {status.running && status.models.length === 0 && (
        <p className="text-[11px] text-ck-muted">
          No models installed on this server. Download a model there, then refresh.
        </p>
      )}
      <label className="block text-xs text-ck-muted">
        Main model (or enter an installed model name)
        <input
          className={inputClass}
          value={form.ollama_model}
          list="ck-ollama-models"
          onChange={(e) => onChange({ ...form, ollama_model: e.target.value })}
          placeholder="qwen3.5:9b"
          spellCheck={false}
        />
      </label>
      <label className="block text-xs text-ck-muted">
        Fast model — optional, on the same server
        <input
          className={inputClass}
          value={form.ollama_summary_model}
          list="ck-ollama-models"
          onChange={(e) => onChange({ ...form, ollama_summary_model: e.target.value })}
          placeholder="Use the main model"
          spellCheck={false}
        />
      </label>
      <datalist id="ck-ollama-models">
        {status.models.map((m) => (
          <option key={m.name} value={m.name} />
        ))}
      </datalist>
      <p className="text-[11px] text-ck-muted">
        Click Save to apply. Local and remote model choices are remembered separately.
        {local
          ? ' Saving clears Clickachu’s and your saved Windows user OLLAMA_HOST override.'
          : ' Prompts and context are sent to the selected computer; Windows actions still run here.'}
      </p>
      <details className="text-[11px] text-ck-muted">
        <summary className="cursor-pointer">
          Before using another computer / terminal cleanup
        </summary>
        <ol className="mt-2 list-decimal space-y-2 pl-4">
          <li>Connect both computers to the same trusted Wi-Fi or reachable private network.</li>
          <li>On the host, install Ollama and download the model you want to use.</li>
          <li>
            On a Mac, run{' '}
            <code className="select-text break-all">
              launchctl setenv OLLAMA_HOST "0.0.0.0:11434"
            </code>
            , then fully quit and reopen Ollama.
          </li>
          <li>Allow Ollama through the host firewall. Keep the host awake and plugged in.</li>
          <li>
            Enter the host’s current Wi-Fi IP above, test the connection, choose a model, and Save.
            Windows Ollama can stay closed for remote use.
          </li>
        </ol>
        <p className="mt-2">
          No Windows environment variable is needed. Existing PowerShell windows retain their own
          variables; to return that terminal to local Ollama, run:
        </p>
        <code className="mt-1 block select-text break-all">
          Remove-Item Env:OLLAMA_HOST -ErrorAction SilentlyContinue
        </code>
        <p className="mt-2">
          The host API has no built-in authentication. Use a trusted private network; do not expose
          port 11434 publicly. If the host’s IP changes, update it here.
        </p>
      </details>
    </div>
  )
}
