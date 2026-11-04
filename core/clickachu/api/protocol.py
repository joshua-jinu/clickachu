"""HTTP + WebSocket protocol models (PLAN §7).

Mirrored by `app/src/shared/protocol.ts` — same names, change both together.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

RunAction = Literal["summarise", "explain", "translate", "rewrite", "fix", "ask", "do"]
RunMode = Literal["ask", "agent"]
ApprovalDecision = Literal["approve", "always", "deny"]
SuggestionDecision = Literal["accept", "dismiss", "later"]
Tier = Literal["read", "low", "confirm", "blocked"]
OrbStateName = Literal["idle", "listening", "thinking", "acting", "attention", "error", "hidden"]
SelectionMethod = Literal["uia", "clipboard", "none"]
ReplaceMode = Literal["replace", "insert"]
ReplaceFailure = Literal["focus", "expired"]
MemoryScopeType = Literal["global", "project", "app"]
MemoryCategory = Literal["preference", "instruction", "fact"]


LLMProviderType = Literal["gemini", "ollama"]

# ---------------------------------------------------------------- HTTP


class HealthResponse(BaseModel):
    ok: bool
    version: str


DEFAULT_GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_ROUTER_MODEL: str = "gemini-2.5-flash-lite"
DEFAULT_ASK_MODEL: str = "gemini-2.5-flash"
DEFAULT_AGENT_MODEL: str = "gemini-2.5-flash"

DEFAULT_OLLAMA_BASE_URL: str = "http://127.0.0.1:11434/v1"
DEFAULT_OLLAMA_MODEL: str = "llama3.2:3b"

DEFAULT_SENSITIVE_DOMAINS: tuple[str, ...] = (
    "paypal.com",
    "stripe.com",
    "wise.com",
    "venmo.com",
    "chase.com",
    "bankofamerica.com",
    "wellsfargo.com",
    "citibank.com",
    "capitalone.com",
    "mail.google.com",
    "outlook.live.com",
    "outlook.office.com",
)


class OllamaModelInfo(BaseModel):
    name: str
    size: int = 0
    modified_at: str | None = None


class OllamaStatusResponse(BaseModel):
    running: bool
    models: list[OllamaModelInfo] = []
    error: str | None = None


class Settings(BaseModel):
    """Model configuration (PLAN §5). Empty model IDs mean "not configured yet"."""

    provider: LLMProviderType = "gemini"
    router_model: str = DEFAULT_ROUTER_MODEL
    ask_model: str = DEFAULT_ASK_MODEL
    agent_model: str = DEFAULT_AGENT_MODEL
    base_url: str = DEFAULT_GEMINI_BASE_URL
    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    translate_language: str = "English"
    # Logs every outgoing prompt (after redaction) so redaction can be verified (§10).
    debug_log_prompts: bool = False
    # Global "memory off" (§14): no reads into prompts, no writes.
    memory_enabled: bool = True
    # Domains that always require user confirmation even for LOW-tier browser tools (§13.2).
    sensitive_domains: list[str] = Field(
        default_factory=lambda: list(DEFAULT_SENSITIVE_DOMAINS)
    )
    # Browser channel for Playwright: 'msedge' or 'chrome' (PLAN §5, §13.3).
    browser_channel: str = "msedge"
    # Search URL template for browser_search.
    browser_search_url: str = "https://www.google.com/search?q={query}"
    # Proactive suggestions from process monitoring (PLAN §16).
    proactive_suggestions: bool = True
    # Whether voice input is enabled (PLAN §17).
    voice_enabled: bool = True
    # Audio input device id / name; None for default.
    voice_device: str | None = None

    @property
    def active_base_url(self) -> str:
        return self.ollama_base_url if self.provider == "ollama" else self.base_url

    @property
    def active_router_model(self) -> str:
        return self.ollama_model if self.provider == "ollama" else self.router_model

    @property
    def active_ask_model(self) -> str:
        return self.ollama_model if self.provider == "ollama" else self.ask_model

    @property
    def active_agent_model(self) -> str:
        return self.ollama_model if self.provider == "ollama" else self.agent_model


class SettingsView(Settings):
    has_openrouter_key: bool = False
    voice_model_status: str = "unloaded"


class SettingsUpdate(BaseModel):
    provider: LLMProviderType | None = None
    router_model: str | None = None
    ask_model: str | None = None
    agent_model: str | None = None
    base_url: str | None = Field(default=None, pattern=r"^https?://")
    ollama_base_url: str | None = Field(default=None, pattern=r"^https?://")
    ollama_model: str | None = None
    translate_language: str | None = Field(None, min_length=1)
    debug_log_prompts: bool | None = None
    memory_enabled: bool | None = None
    sensitive_domains: list[str] | None = None
    browser_channel: str | None = None
    browser_search_url: str | None = None
    proactive_suggestions: bool | None = None
    voice_enabled: bool | None = None
    voice_device: str | None = None


class VoiceStopResponse(BaseModel):
    text: str


class AudioDevice(BaseModel):
    id: int
    name: str
    is_default: bool


class VoiceStatusResponse(BaseModel):
    model_status: str
    recording: bool


class SecretUpdate(BaseModel):
    key: str = Field(min_length=1)


class ProjectRef(BaseModel):
    id: int
    name: str


class ContextSnapshot(BaseModel):
    id: str
    captured_at: datetime
    hwnd: int
    process: str
    app: str
    window_title: str
    project: ProjectRef | None = None
    selection: str | None = None
    selection_method: SelectionMethod = "none"
    selection_truncated: bool = False
    is_terminal: bool = False


class ReplaceRequest(BaseModel):
    context_id: str
    text: str
    mode: ReplaceMode


class ReplaceResponse(BaseModel):
    ok: bool
    # "focus": target window couldn't be focused, text was left on the clipboard.
    # "expired": the snapshot is gone from the LRU (or its window closed).
    reason: ReplaceFailure | None = None


# ---------------------------------------------------------------- HTTP: memory (§14)


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    scope_type: MemoryScopeType = "global"
    # Project name / process name; ignored (stored as "") for global memories.
    scope_key: str = ""
    category: MemoryCategory = "fact"
    pinned: bool = False


class MemoryUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=2000)
    scope_type: MemoryScopeType | None = None
    scope_key: str | None = None
    category: MemoryCategory | None = None
    pinned: bool | None = None
    enabled: bool | None = None


class Memory(BaseModel):
    id: int
    scope_type: MemoryScopeType
    scope_key: str
    category: MemoryCategory
    content: str
    pinned: bool
    enabled: bool
    use_count: int
    superseded_by: int | None = None
    created_at: str
    updated_at: str
    last_used_at: str | None = None


class MemoryExport(BaseModel):
    version: int = 1
    memories: list[MemoryCreate]


class MemoryImportResult(BaseModel):
    imported: int
    skipped: int


# ---------------------------------------------------------------- HTTP: projects (§15)


class ProjectCommand(BaseModel):
    name: str = Field(min_length=1)
    cmd: str = Field(min_length=1)
    cwd: str
    port: int | None = Field(default=None, ge=1, le=65535)
    trusted: bool = False


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1)
    path: str = Field(min_length=1)
    commands: list[ProjectCommand] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    path: str | None = Field(default=None, min_length=1)
    commands: list[ProjectCommand] | None = None
    env: dict[str, str] | None = None


class Project(BaseModel):
    id: int
    name: str
    path: str
    commands: list[ProjectCommand]
    env: dict[str, str]
    created_at: str
    approved_at: str | None = None


class ProjectDetectRequest(BaseModel):
    path: str = Field(min_length=1)


class ProjectProposal(BaseModel):
    """A detected profile; not saved until the user reviews it."""

    name: str
    path: str
    commands: list[ProjectCommand]


class AllowedRootCreate(BaseModel):
    path: str = Field(min_length=1)
    can_write: bool = False


class AllowedRootUpdate(BaseModel):
    can_write: bool


class AllowedRoot(BaseModel):
    id: int
    path: str
    can_write: bool


class ActionLogEntry(BaseModel):
    id: int
    run_id: str
    step_id: str
    tool: str
    args_json: str
    tier: str
    decision: str
    ok: bool | None = None
    result_summary: str | None = None
    verified: bool | None = None
    started_at: str
    duration_ms: int | None = None


# ---------------------------------------------------------------- HTTP: conversations


class ConversationMessage(BaseModel):
    id: int
    role: str
    content: str
    created_at: str


class ConversationSummary(BaseModel):
    id: str
    started_at: str
    title: str = ""
    message_count: int = 0


class ConversationDetail(BaseModel):
    id: str
    started_at: str
    messages: list[ConversationMessage] = Field(default_factory=list)


# ---------------------------------------------------------------- WS: client → server


class AuthMessage(BaseModel):
    type: Literal["auth"] = "auth"
    token: str


class RunMessage(BaseModel):
    type: Literal["run"] = "run"
    run_id: str = Field(min_length=1)
    text: str
    context_id: str | None = None
    action: RunAction | None = None
    mode: RunMode | None = None
    conversation_id: str | None = None


class ApproveMessage(BaseModel):
    type: Literal["approve"] = "approve"
    run_id: str
    step_id: str
    decision: ApprovalDecision


class CancelMessage(BaseModel):
    type: Literal["cancel"] = "cancel"
    run_id: str


class SuggestionRespondMessage(BaseModel):
    type: Literal["suggestion.respond"] = "suggestion.respond"
    suggestion_id: str
    decision: SuggestionDecision


ClientMessage = Annotated[
    AuthMessage | RunMessage | ApproveMessage | CancelMessage | SuggestionRespondMessage,
    Field(discriminator="type"),
]
client_message_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


# ---------------------------------------------------------------- WS: server → client


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class MemoryRef(BaseModel):
    id: int
    content: str
    scope: str


class RunStartedEvent(BaseModel):
    type: Literal["run.started"] = "run.started"
    run_id: str
    mode: RunMode


class TokenEvent(BaseModel):
    type: Literal["token"] = "token"
    run_id: str
    delta: str


class StepStartedEvent(BaseModel):
    type: Literal["step.started"] = "step.started"
    run_id: str
    step_id: str
    tool: str
    summary: str
    tier: Tier


class ApprovalRequestEvent(BaseModel):
    type: Literal["approval.request"] = "approval.request"
    run_id: str
    step_id: str
    tool: str
    args: dict[str, Any]
    preview: str


class StepFinishedEvent(BaseModel):
    type: Literal["step.finished"] = "step.finished"
    run_id: str
    step_id: str
    ok: bool
    summary: str
    verified: bool | None


class RunFinishedEvent(BaseModel):
    type: Literal["run.finished"] = "run.finished"
    run_id: str
    ok: bool
    text: str
    usage: Usage


class RunErrorEvent(BaseModel):
    type: Literal["run.error"] = "run.error"
    run_id: str
    message: str


class OrbStateEvent(BaseModel):
    type: Literal["orb.state"] = "orb.state"
    state: OrbStateName


class MemorySavedEvent(BaseModel):
    type: Literal["memory.saved"] = "memory.saved"
    memory: MemoryRef


class SuggestionEvent(BaseModel):
    type: Literal["suggestion"] = "suggestion"
    id: str
    title: str
    body: str
    context_id: str | None = None


class VoiceLevelEvent(BaseModel):
    type: Literal["voice.level"] = "voice.level"
    rms: float


class VoiceDoneEvent(BaseModel):
    type: Literal["voice.done"] = "voice.done"
    text: str


ServerMessage = Annotated[
    RunStartedEvent
    | TokenEvent
    | StepStartedEvent
    | ApprovalRequestEvent
    | StepFinishedEvent
    | RunFinishedEvent
    | RunErrorEvent
    | OrbStateEvent
    | MemorySavedEvent
    | SuggestionEvent
    | VoiceLevelEvent
    | VoiceDoneEvent,
    Field(discriminator="type"),
]
server_message_adapter: TypeAdapter[ServerMessage] = TypeAdapter(ServerMessage)
