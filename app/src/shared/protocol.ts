/**
 * HTTP + WebSocket protocol types (PLAN §7).
 *
 * Mirrored by `core/clickachu/api/protocol.py` — same names, change both together.
 */

export type RunAction = 'summarise' | 'explain' | 'translate' | 'rewrite' | 'fix' | 'ask' | 'do'
export type RunMode = 'ask' | 'agent'
export type ApprovalDecision = 'approve' | 'always' | 'deny'
export type SuggestionDecision = 'accept' | 'dismiss' | 'later'
export type Tier = 'read' | 'low' | 'confirm' | 'blocked'
export type OrbStateName =
  'idle' | 'listening' | 'thinking' | 'acting' | 'attention' | 'error' | 'hidden'
export type SelectionMethod = 'uia' | 'clipboard' | 'none'
export type ReplaceMode = 'replace' | 'insert'
export type ReplaceFailure = 'focus' | 'expired'
export type MemoryScopeType = 'global' | 'project' | 'app'
export type MemoryCategory = 'preference' | 'instruction' | 'fact'

export type LLMProviderType = 'gemini' | 'ollama'

// ---------------------------------------------------------------- HTTP

export const DEFAULT_GEMINI_BASE_URL = 'https://generativelanguage.googleapis.com/v1beta/openai/'
export const DEFAULT_ROUTER_MODEL = 'gemini-2.5-flash-lite'
export const DEFAULT_ASK_MODEL = 'gemini-2.5-flash'
export const DEFAULT_AGENT_MODEL = 'gemini-2.5-flash'

export const DEFAULT_OLLAMA_BASE_URL = 'http://127.0.0.1:11434/v1'
export const DEFAULT_OLLAMA_MODEL = 'llama3.2:3b'

export interface HealthResponse {
  ok: boolean
  version: string
}

export interface OllamaModelInfo {
  name: string
  size: number
  modified_at?: string | null
}

export interface OllamaStatusResponse {
  running: boolean
  models: OllamaModelInfo[]
  error?: string | null
}

export interface Settings {
  provider: LLMProviderType
  router_model: string
  ask_model: string
  agent_model: string
  base_url: string
  ollama_base_url: string
  ollama_model: string
  translate_language: string
  /** Logs every outgoing prompt (after redaction) so redaction can be verified (§10). */
  debug_log_prompts: boolean
  /** Global "memory off" (§14): no reads into prompts, no writes. */
  memory_enabled: boolean
  /** Domains that always require user confirmation even for LOW-tier browser tools (§13.2). */
  sensitive_domains: string[]
  /** Browser channel for Playwright: 'msedge' or 'chrome' (PLAN §5, §13.3). */
  browser_channel: string
  /** Search URL template for browser_search. */
  browser_search_url: string
  /** Proactive suggestions from process monitoring (PLAN §16). */
  proactive_suggestions: boolean
  /** Whether voice input is enabled (PLAN §17). */
  voice_enabled: boolean
  /** Audio input device id / name; null for default. */
  voice_device?: string | null
}

export interface SettingsView extends Settings {
  has_openrouter_key: boolean
  voice_model_status: string
}

export type SettingsUpdate = Partial<Settings>

export interface SecretUpdate {
  key: string
}

export interface ProjectRef {
  id: number
  name: string
}

export interface ContextSnapshot {
  id: string
  /** ISO 8601 */
  captured_at: string
  hwnd: number
  process: string
  app: string
  window_title: string
  project: ProjectRef | null
  selection: string | null
  selection_method: SelectionMethod
  selection_truncated: boolean
  is_terminal: boolean
}

export interface ReplaceRequest {
  context_id: string
  text: string
  mode: ReplaceMode
}

export interface ReplaceResponse {
  ok: boolean
  /** "focus": target couldn't be focused, text left on the clipboard. "expired": snapshot gone. */
  reason?: ReplaceFailure | null
}

export interface VoiceStopResponse {
  text: string
}

export interface AudioDevice {
  id: number
  name: string
  is_default: boolean
}

export interface VoiceStatusResponse {
  model_status: string
  recording: boolean
}

// ---------------------------------------------------------------- HTTP: memory (§14)

export interface MemoryCreate {
  content: string
  scope_type?: MemoryScopeType
  /** Project name / process name; ignored for global memories. */
  scope_key?: string
  category?: MemoryCategory
  pinned?: boolean
}

export interface MemoryUpdate {
  content?: string
  scope_type?: MemoryScopeType
  scope_key?: string
  category?: MemoryCategory
  pinned?: boolean
  enabled?: boolean
}

export interface Memory {
  id: number
  scope_type: MemoryScopeType
  scope_key: string
  category: MemoryCategory
  content: string
  pinned: boolean
  enabled: boolean
  use_count: number
  superseded_by: number | null
  created_at: string
  updated_at: string
  last_used_at: string | null
}

export interface MemoryExport {
  version: number
  memories: MemoryCreate[]
}

export interface MemoryImportResult {
  imported: number
  skipped: number
}

// ---------------------------------------------------------------- HTTP: projects (§15)

export interface ProjectCommand {
  name: string
  cmd: string
  cwd: string
  port?: number | null
  trusted: boolean
}

export interface ProjectCreate {
  name: string
  path: string
  commands: ProjectCommand[]
  env?: Record<string, string>
}

export interface ProjectUpdate {
  name?: string
  path?: string
  commands?: ProjectCommand[]
  env?: Record<string, string>
}

export interface Project {
  id: number
  name: string
  path: string
  commands: ProjectCommand[]
  env: Record<string, string>
  created_at: string
  approved_at: string | null
}

export interface ProjectDetectRequest {
  path: string
}

/** A detected profile; not saved until the user reviews it. */
export interface ProjectProposal {
  name: string
  path: string
  commands: ProjectCommand[]
}

export interface AllowedRootCreate {
  path: string
  can_write?: boolean
}

export interface AllowedRootUpdate {
  can_write: boolean
}

export interface AllowedRoot {
  id: number
  path: string
  can_write: boolean
}

export interface ActionLogEntry {
  id: number
  run_id: string
  step_id: string
  tool: string
  args_json: string
  tier: string
  decision: string
  ok: boolean | null
  result_summary: string | null
  verified: boolean | null
  started_at: string
  duration_ms: number | null
}

// ---------------------------------------------------------------- WS: client → server

export interface AuthMessage {
  type: 'auth'
  token: string
}

export interface RunMessage {
  type: 'run'
  run_id: string
  text: string
  context_id?: string | null
  action?: RunAction | null
  mode?: RunMode | null
  conversation_id?: string | null
}

export interface ConversationMessage {
  id: number
  role: string
  content: string
  created_at: string
}

export interface ConversationSummary {
  id: string
  started_at: string
  title: string
  message_count: number
}

export interface ConversationDetail {
  id: string
  started_at: string
  messages: ConversationMessage[]
}

export interface ApproveMessage {
  type: 'approve'
  run_id: string
  step_id: string
  decision: ApprovalDecision
}

export interface CancelMessage {
  type: 'cancel'
  run_id: string
}

export interface SuggestionRespondMessage {
  type: 'suggestion.respond'
  suggestion_id: string
  decision: SuggestionDecision
}

export type ClientMessage =
  AuthMessage | RunMessage | ApproveMessage | CancelMessage | SuggestionRespondMessage

// ---------------------------------------------------------------- WS: server → client

export interface Usage {
  input_tokens: number
  output_tokens: number
  cost_usd: number
}

export interface MemoryRef {
  id: number
  content: string
  scope: string
}

export interface RunStartedEvent {
  type: 'run.started'
  run_id: string
  mode: RunMode
}

export interface TokenEvent {
  type: 'token'
  run_id: string
  delta: string
}

export interface RunDetailEvent {
  type: 'run.detail'
  run_id: string
  kind: 'plan' | 'reasoning'
  delta: string
}

export interface StepStartedEvent {
  type: 'step.started'
  run_id: string
  step_id: string
  tool: string
  summary: string
  tier: Tier
}

export interface ApprovalRequestEvent {
  type: 'approval.request'
  run_id: string
  step_id: string
  tool: string
  args: Record<string, unknown>
  preview: string
}

export interface StepFinishedEvent {
  type: 'step.finished'
  run_id: string
  step_id: string
  ok: boolean
  summary: string
  verified: boolean | null
}

export interface RunFinishedEvent {
  type: 'run.finished'
  run_id: string
  ok: boolean
  text: string
  usage: Usage
}

export interface RunErrorEvent {
  type: 'run.error'
  run_id: string
  message: string
}

export interface OrbStateEvent {
  type: 'orb.state'
  state: OrbStateName
}

export interface MemorySavedEvent {
  type: 'memory.saved'
  memory: MemoryRef
}

export interface SuggestionEvent {
  type: 'suggestion'
  id: string
  title: string
  body: string
  context_id?: string | null
}

export interface VoiceLevelEvent {
  type: 'voice.level'
  rms: number
}

export interface VoiceDoneEvent {
  type: 'voice.done'
  text: string
}

export type ServerMessage =
  | RunStartedEvent
  | TokenEvent
  | RunDetailEvent
  | StepStartedEvent
  | ApprovalRequestEvent
  | StepFinishedEvent
  | RunFinishedEvent
  | RunErrorEvent
  | OrbStateEvent
  | MemorySavedEvent
  | SuggestionEvent
  | VoiceLevelEvent
  | VoiceDoneEvent

export type ServerMessageType = ServerMessage['type']
export type ServerMessageOf<T extends ServerMessageType> = Extract<ServerMessage, { type: T }>
