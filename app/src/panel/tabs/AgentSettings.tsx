import type { Settings } from '@shared/protocol'
import { inputClass } from '../ui'

export function AgentSettings({ form, onChange }: {
  form: Settings
  onChange: (settings: Settings) => void
}) {
  return (
    <div className="space-y-3 border-t border-white/10 pt-3">
      <h3 className="text-xs font-semibold text-ck-text">Agent capabilities</h3>
      <p className="text-[11px] text-ck-muted">
        Routine navigation, search, scrolling, and playback run automatically using observed controls.
        Consequential changes and unidentified controls still ask for approval.
      </p>
      {form.provider === 'ollama' && <>
        <label className="flex items-center gap-2 text-xs text-ck-muted">
          <input type="checkbox" checked={form.ollama_cpu_only}
            onChange={(e) => onChange({ ...form, ollama_cpu_only: e.target.checked })} />
          CPU compatibility for Ollama
        </label>
        <p className="text-[11px] text-ck-muted">
          Avoids GPU inference when the local runner crashes. Generation may be slower.
          Simple process and playback commands run directly without the model.
        </p>
      </>}
      <label className="flex items-center gap-2 text-xs text-ck-muted">
        <input type="checkbox" checked={form.code_execution_enabled}
          onChange={(e) => onChange({ ...form, code_execution_enabled: e.target.checked })} />
        Allow isolated Python execution
      </label>
      <p className="text-[11px] text-ck-muted">
        Uses Docker Desktop with a local python:3.12-slim image. Task files persist between calls;
        variables do not. No network or automatic fallback to host execution.
      </p>
      <label className="flex items-center gap-2 text-xs text-ck-muted">
        <input type="checkbox" checked={form.host_execution_enabled}
          onChange={(e) => onChange({ ...form, host_execution_enabled: e.target.checked })} />
        Allow approved Python and PowerShell on this PC
      </label>
      <p className="text-[11px] text-amber-200/80">
        Each host script requires approval and runs with your account’s access.
        Allowed folders constrain its starting directory, not what the script can access.
      </p>
      <div className="grid grid-cols-2 gap-3">
        {([
          ['agent_max_steps', 'Maximum model turns', 1, 100],
          ['agent_max_seconds', 'Time budget (seconds)', 10, 3600],
          ['agent_max_output_tokens', 'Output token budget', 256, 100000],
          ['agent_max_failures', 'Consecutive failure limit', 1, 10],
        ] as const).map(([key, label, min, max]) => (
          <label key={key} className="block text-xs text-ck-muted">
            {label}
            <input className={inputClass} type="number" required min={min} max={max}
              value={form[key]} onChange={(e) => onChange({ ...form, [key]: Number(e.target.value) })} />
          </label>
        ))}
      </div>
      <label className="block text-xs text-ck-muted">
        Screenshot input
        <select className={inputClass} value={form.vision_mode}
          onChange={(e) => onChange({ ...form, vision_mode: e.target.value as Settings['vision_mode'] })}>
          <option value="auto">Automatic (known Gemini models)</option>
          <option value="on">Enabled — selected model supports images</option>
          <option value="off">Disabled — text and accessibility only</option>
        </select>
      </label>
      <label className="block text-xs text-ck-muted">
        Reasoning effort
        <select className={inputClass} value={form.reasoning_effort}
          onChange={(e) => onChange({ ...form, reasoning_effort: e.target.value as Settings['reasoning_effort'] })}>
          <option value="off">Fastest supported setting</option>
          <option value="auto">Model default</option>
          <option value="low">Low</option>
          <option value="medium">Medium</option>
          <option value="high">High</option>
        </select>
      </label>
      <p className="text-[11px] text-ck-muted">
        Supported levels depend on the model and provider. Higher effort can take longer.
        Reasoning appears only when the provider returns it; Activity works with every model.
      </p>
      <label className="flex items-center gap-2 text-xs text-ck-muted">
        <input type="checkbox" checked={form.use_model_router}
          onChange={(e) => onChange({ ...form, use_model_router: e.target.checked })} />
        Use a separate model call for ambiguous Ask/Agent routing
      </label>
    </div>
  )
}
