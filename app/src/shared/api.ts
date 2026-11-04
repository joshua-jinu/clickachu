/** Typed HTTP calls to the sidecar (PLAN §7), always with the Bearer token. */
import type {
  ActionLogEntry,
  AllowedRoot,
  AllowedRootCreate,
  AllowedRootUpdate,
  ConversationDetail,
  ConversationSummary,
  Memory,
  MemoryCreate,
  MemoryExport,
  MemoryImportResult,
  MemoryScopeType,
  MemoryUpdate,
  Project,
  ProjectCreate,
  ProjectDetectRequest,
  ProjectProposal,
  ProjectUpdate,
  ReplaceRequest,
  ReplaceResponse,
  SecretUpdate,
  SettingsUpdate,
  SettingsView,
  VoiceStatusResponse,
  VoiceStopResponse,
  AudioDevice,
  ContextSnapshot,
  OllamaStatusResponse,
} from './protocol'

/** Thrown for non-2xx responses; `detail` is the server's message when it sent one. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(detail)
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const { baseUrl, token } = await window.clickachu.backend()
  const res = await fetch(`${baseUrl}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    const text = await res.text()
    let detail = text
    try {
      const parsed = JSON.parse(text) as { detail?: unknown }
      if (typeof parsed.detail === 'string') detail = parsed.detail
    } catch {
      // Not JSON; keep the raw text.
    }
    throw new ApiError(res.status, detail || `${method} ${path} → ${res.status}`)
  }
  return (res.status === 204 ? undefined : await res.json()) as T
}

function query(params: Record<string, string | boolean | undefined>): string {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== '') qs.set(k, String(v))
  const s = qs.toString()
  return s ? `?${s}` : ''
}

export const api = {
  getSettings: () => request<SettingsView>('GET', '/settings'),
  getOllamaStatus: () => request<OllamaStatusResponse>('GET', '/ollama/status'),
  putSettings: (patch: SettingsUpdate) => request<SettingsView>('PUT', '/settings', patch),
  openBrowser: () => request<{ ok: boolean }>('POST', '/browser/open'),
  replaceContext: (body: ReplaceRequest) =>
    request<ReplaceResponse>('POST', '/context/replace', body),
  putOpenRouterKey: (key: string) =>
    request<void>('PUT', '/secrets/openrouter', { key } satisfies SecretUpdate),

  listMemories: (opts: { q?: string; scope_type?: MemoryScopeType } = {}) =>
    request<Memory[]>('GET', `/memories${query(opts)}`),
  createMemory: (body: MemoryCreate) => request<Memory>('POST', '/memories', body),
  updateMemory: (id: number, patch: MemoryUpdate) =>
    request<Memory>('PATCH', `/memories/${id}`, patch),
  deleteMemory: (id: number) => request<void>('DELETE', `/memories/${id}`),
  exportMemories: () => request<MemoryExport>('GET', '/memories/export'),
  importMemories: (body: MemoryExport) =>
    request<MemoryImportResult>('POST', '/memories/import', body),

  listProjects: () => request<Project[]>('GET', '/projects'),
  detectProject: (path: string) =>
    request<ProjectProposal>('POST', '/projects/detect', { path } satisfies ProjectDetectRequest),
  createProject: (body: ProjectCreate) => request<Project>('POST', '/projects', body),
  updateProject: (id: number, patch: ProjectUpdate) =>
    request<Project>('PATCH', `/projects/${id}`, patch),
  deleteProject: (id: number) => request<void>('DELETE', `/projects/${id}`),

  listRoots: () => request<AllowedRoot[]>('GET', '/allowed-roots'),
  addRoot: (body: AllowedRootCreate) => request<AllowedRoot>('POST', '/allowed-roots', body),
  updateRoot: (id: number, body: AllowedRootUpdate) =>
    request<AllowedRoot>('PATCH', `/allowed-roots/${id}`, body),
  removeRoot: (id: number) => request<void>('DELETE', `/allowed-roots/${id}`),

  getContext: (id: string) => request<ContextSnapshot>('GET', `/context/${id}`),
  getVoiceStatus: () => request<VoiceStatusResponse>('GET', '/voice/status'),
  getVoiceDevices: () => request<AudioDevice[]>('GET', '/voice/devices'),
  startVoice: (device?: string) =>
    request<{ ok: boolean }>('POST', `/voice/start${query({ device })}`),
  stopVoice: () => request<VoiceStopResponse>('POST', '/voice/stop'),

  getLog: (runId?: string) => request<ActionLogEntry[]>('GET', `/log${query({ run_id: runId })}`),

  listConversations: () => request<ConversationSummary[]>('GET', '/conversations'),
  getConversation: (id: string) =>
    request<ConversationDetail>('GET', `/conversations/${encodeURIComponent(id)}`),
  deleteConversation: (id: string) =>
    request<void>('DELETE', `/conversations/${encodeURIComponent(id)}`),
}
