"""Run orchestration (PLAN §12): Ask mode and Agent mode tool loop."""

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from clickachu.api.protocol import (
    AllowedRoot,
    ApprovalDecision,
    ApprovalRequestEvent,
    ApproveMessage,
    CancelMessage,
    ClientMessage,
    ContextSnapshot,
    Memory,
    MemoryRef,
    MemorySavedEvent,
    OrbStateEvent,
    OrbStateName,
    RunDetailEvent,
    RunErrorEvent,
    RunFinishedEvent,
    RunMessage,
    RunStartedEvent,
    ServerMessage,
    Settings,
    StepFinishedEvent,
    StepStartedEvent,
    TokenEvent,
    Usage,
)
from clickachu.context.builder import context_block
from clickachu.context.capture import ContextStore
from clickachu.db.conn import Database
from clickachu.llm.prompts import (
    AGENT_SYSTEM,
    action_instruction,
    memory_block,
    system_base_prompt,
    wrap_untrusted,
)
from clickachu.llm.provider import Done, LLMError, LLMProvider, ReasoningDelta, TextDelta, ToolCall
from clickachu.llm.router import (
    _GREETING_OR_IDENTITY,
    notepad_write_requested,
    route_mode,
    web_lookup_requested,
)
from clickachu.memory.remember import is_remember_request
from clickachu.memory.service import MemoryService
from clickachu.memory.store import now_iso, scope_label
from clickachu.projects.roots import RootStore
from clickachu.secret_store import SecretStore
from clickachu.settings import SettingsStore
from clickachu.tools.base import Tool, ToolContext, ToolResult, ToolWorkers
from clickachu.tools.policy import PolicyAction, TrustStore, decide, resolve_tool_paths
from clickachu.tools.registry import ToolRegistry, default_registry

log = logging.getLogger(__name__)

ASK_MAX_TOKENS = 2048
MAX_STEPS = 8
MAX_TOOL_RESULT_CHARS = 4000
DEFAULT_APPROVAL_TIMEOUT_S = 300.0

_SELECTION_REFERENCE = re.compile(
    r"\b(?:this|these|it|selection|selected|code|above|below|error|here|lines|snippet|bug|fix|refactor|translate|summarise|summarize|explain)\b",
    re.IGNORECASE,
)


def should_include_selection(msg: RunMessage, *, agent: bool = False) -> bool:
    if msg.action is not None and msg.action not in ("ask", "do"):
        return True
    text = (msg.text or "").strip()
    if not text:
        return True
    if agent:
        return True
    if _GREETING_OR_IDENTITY.search(text):
        return False
    if _SELECTION_REFERENCE.search(text):
        return True
    return False


Emit = Callable[[ServerMessage], Awaitable[None]]
# (settings, api_key) -> provider; rebuilt per run so settings/key changes apply immediately.
ProviderFactory = Callable[[Settings, str], LLMProvider]


@dataclass
class _ActiveRun:
    task: asyncio.Task[None]
    cancelled: bool = False
    approvals: dict[str, asyncio.Future[ApprovalDecision]] = field(default_factory=dict)
    unverified_cleanup: list[Callable[[], None]] = field(default_factory=list)


class Orchestrator:
    def __init__(
        self,
        *,
        emit: Emit,
        settings: SettingsStore,
        secrets: SecretStore,
        provider_factory: ProviderFactory,
        contexts: ContextStore | None = None,
        memory: MemoryService | None = None,
        registry: ToolRegistry | None = None,
        workers: ToolWorkers | None = None,
        approval_timeout_s: float = DEFAULT_APPROVAL_TIMEOUT_S,
    ) -> None:
        # `is None`, not `or`: an empty ContextStore is falsy (it has __len__).
        self._contexts = contexts if contexts is not None else ContextStore()
        self._memory = memory
        self._emit = emit
        self._settings = settings
        self._db: Database = settings.db
        self._secrets = secrets
        self._provider_factory = provider_factory
        self._registry = registry if registry is not None else default_registry()
        self._workers = workers if workers is not None else ToolWorkers()
        self._approval_timeout_s = approval_timeout_s
        self._runs: dict[str, _ActiveRun] = {}

    async def handle_run(self, msg: ClientMessage) -> None:
        assert isinstance(msg, RunMessage)
        if msg.run_id in self._runs:
            await self._emit(RunErrorEvent(run_id=msg.run_id, message="duplicate run_id"))
            return
        task = asyncio.current_task()
        assert task is not None  # handlers always run inside a hub task
        active = _ActiveRun(task=task)
        self._runs[msg.run_id] = active
        try:
            await self._run(msg, active)
        finally:
            self._runs.pop(msg.run_id, None)

    async def handle_approve(self, msg: ClientMessage) -> None:
        assert isinstance(msg, ApproveMessage)
        active = self._runs.get(msg.run_id)
        if active is None:
            log.info("approve for unknown or finished run %s", msg.run_id)
            return
        fut = active.approvals.get(msg.step_id)
        if fut is None or fut.done():
            log.info("approve for unknown or resolved step %s in run %s", msg.step_id, msg.run_id)
            return
        fut.set_result(msg.decision)

    async def handle_cancel(self, msg: ClientMessage) -> None:
        assert isinstance(msg, CancelMessage)
        active = self._runs.get(msg.run_id)
        if active is None:
            log.info("cancel for unknown or finished run %s", msg.run_id)
            return
        active.cancelled = True
        for fut in list(active.approvals.values()):
            if not fut.done():
                fut.cancel()
        for cleanup in list(active.unverified_cleanup):
            try:
                cleanup()
            except Exception as exc:  # noqa: BLE001
                log.warning("cleanup on cancel failed: %s", exc)
        active.unverified_cleanup.clear()
        active.task.cancel()

    async def _run(self, msg: RunMessage, active: _ActiveRun) -> None:
        settings = self._settings.get()
        api_key = self._secrets.get_openrouter_key()
        if settings.provider == "gemini" and not api_key:
            await self._fail(msg.run_id, "No Gemini API key — add one in Settings.")
            return

        provider = self._provider_factory(settings, api_key or "")
        router_model = settings.active_router_model
        ask_model = settings.active_ask_model
        agent_model = settings.active_agent_model
        is_ollama = settings.provider == "ollama" or getattr(provider, "_is_ollama", False)
        conversation_id = msg.conversation_id or msg.run_id

        mode = await route_mode(
            msg.text,
            mode=msg.mode,
            action=msg.action,
            provider=provider,
            router_model=router_model,
            is_ollama=is_ollama,
        )

        if mode == "ask":
            if not ask_model:
                await self._fail(msg.run_id, "No ask_model configured — set one in Settings.")
                return
            await self._run_ask(msg, settings, provider, conversation_id)
        else:
            if not agent_model:
                await self._fail(msg.run_id, "No agent_model configured — set one in Settings.")
                return
            await self._run_agent(msg, active, settings, provider, agent_model, conversation_id)

    async def _run_ask(
        self,
        msg: RunMessage,
        settings: Settings,
        provider: LLMProvider,
        conversation_id: str,
    ) -> None:
        await self._emit(RunStartedEvent(run_id=msg.run_id, mode="ask"))
        await self._emit(OrbStateEvent(state="thinking"))

        snapshot = self._contexts.get(msg.context_id) if msg.context_id else None
        if msg.context_id and snapshot is None:
            log.info("run %s: context %s expired, continuing without", msg.run_id, msg.context_id)
        is_ollama = settings.provider == "ollama" or getattr(provider, "_is_ollama", False)
        history = self._load_history(conversation_id, limit=4 if is_ollama else 10)
        self._start_conversation(conversation_id, snapshot, msg.text)

        parts: list[str] = []
        usage = Usage()
        try:
            saved, memories = await self._memories_for(msg, settings, snapshot, provider)
            messages = _build_initial_messages(
                msg,
                settings,
                snapshot,
                memories,
                saved,
                agent=False,
                is_ollama=is_ollama,
                model_name=settings.active_ask_model,
                history=history,
            )
            if settings.debug_log_prompts:
                prompt = json.dumps(messages, ensure_ascii=False)
                log.info("outgoing prompt for run %s: %s", msg.run_id, prompt)
            async for event in provider.stream(
                settings.active_ask_model, messages, max_tokens=ASK_MAX_TOKENS
            ):
                if isinstance(event, TextDelta):
                    parts.append(event.text)
                    await self._emit(TokenEvent(run_id=msg.run_id, delta=event.text))
                elif isinstance(event, Done):
                    usage = event.usage
                elif isinstance(event, ReasoningDelta):
                    await self._emit(
                        RunDetailEvent(
                            run_id=msg.run_id,
                            kind="reasoning",
                            delta=event.text,
                        )
                    )
        except asyncio.CancelledError:
            log.info("run %s cancelled", msg.run_id)
            await self._emit(
                RunFinishedEvent(run_id=msg.run_id, ok=False, text="".join(parts), usage=usage)
            )
            await self._emit(OrbStateEvent(state="idle"))
            raise
        except LLMError as exc:
            log.warning("run %s failed: %s", msg.run_id, exc)
            await self._fail(msg.run_id, str(exc))
            return

        answer = "".join(parts)
        self._record_message(conversation_id, "assistant", answer)
        log.info(
            "run %s done: model=%s in=%d out=%d cost=$%.6f",
            msg.run_id,
            settings.active_ask_model,
            usage.input_tokens,
            usage.output_tokens,
            usage.cost_usd,
        )
        await self._emit(RunFinishedEvent(run_id=msg.run_id, ok=True, text=answer, usage=usage))
        await self._emit(OrbStateEvent(state="idle"))

    async def _run_agent(
        self,
        msg: RunMessage,
        active: _ActiveRun,
        settings: Settings,
        provider: LLMProvider,
        model: str,
        conversation_id: str,
    ) -> None:
        await self._emit(RunStartedEvent(run_id=msg.run_id, mode="agent"))
        orb_state: OrbStateName = "thinking"
        await self._emit(OrbStateEvent(state="thinking"))

        async def set_orb(state: OrbStateName) -> None:
            nonlocal orb_state
            if orb_state != state:
                orb_state = state
                await self._emit(OrbStateEvent(state=state))

        snapshot = self._contexts.get(msg.context_id) if msg.context_id else None
        if msg.context_id and snapshot is None:
            log.info("run %s: context %s expired, continuing without", msg.run_id, msg.context_id)
        is_ollama = settings.provider == "ollama" or getattr(provider, "_is_ollama", False)
        history = self._load_history(conversation_id, limit=4 if is_ollama else 8)
        self._start_conversation(conversation_id, snapshot, msg.text)

        text_parts: list[str] = []
        total_usage = Usage()
        validation_retries = 0
        nudges = 0
        step_seq = 0
        used_step_ids: set[str] = set()
        completed_summaries: list[str] = []
        current_step: tuple[str, str, str, str, float] | None = None

        try:
            saved, memories = await self._memories_for(msg, settings, snapshot, provider)
            roots = RootStore(self._db).list()
            messages = _build_initial_messages(
                msg,
                settings,
                snapshot,
                memories,
                saved,
                roots=roots,
                agent=True,
                is_ollama=is_ollama,
                model_name=model,
                history=history,
            )
            tool_schemas = (
                self._registry.scoped_schemas(msg.text) if is_ollama else self._registry.schemas()
            )
            notepad_workflow = (
                notepad_write_requested(msg.text)
                and self._registry.get("write_notepad") is not None
            )
            plan = (
                "Generate the requested text once, then request approval to write it into a fresh "
                "empty Notepad document. Verify the editor text and leave it open, unsaved."
                if notepad_workflow
                else "Search online for the conversation topic, read the results, and cite sources."
                if web_lookup_requested(msg.text)
                else "Inspect the relevant context, choose the next tool, and verify its result."
            )
            await self._emit(RunDetailEvent(run_id=msg.run_id, kind="plan", delta=plan))

            for _step_num in range(1, MAX_STEPS + 1):
                await set_orb("thinking")
                if settings.debug_log_prompts:
                    prompt = json.dumps(messages, ensure_ascii=False)
                    log.info("outgoing prompt for run %s: %s", msg.run_id, prompt)

                turn_text: list[str] = []
                tool_calls: list[ToolCall] = []
                generation_messages = messages
                if notepad_workflow:
                    # The application owns the workflow; the model only writes the document.
                    # No tool schemas, root lists, drafts or save-location roundtrips are needed.
                    document_request = msg.text
                    if snapshot and snapshot.selection and should_include_selection(msg):
                        document_request = (
                            context_block(
                                snapshot,
                                max_selection_chars=1500,
                            )
                            + "\n\n"
                            + msg.text
                        )
                    generation_messages = [
                        {
                            "role": "system",
                            "content": (
                                "Write the requested document for an empty Notepad editor. "
                                "Return only plain text. No Markdown fences, planning, tool calls, "
                                "questions about saving, or claims of execution. "
                                "Keep it concise unless the user requests length."
                            )
                            + (f"\n{memory_block(memories)}" if memories else ""),
                        },
                        *(history[-2:] if _SELECTION_REFERENCE.search(msg.text) else []),
                        {"role": "user", "content": document_request},
                    ]
                async for event in provider.stream(
                    model,
                    generation_messages,
                    tools=None if notepad_workflow else tool_schemas,
                    max_tokens=768 if notepad_workflow and is_ollama else ASK_MAX_TOKENS,
                ):
                    if isinstance(event, TextDelta):
                        turn_text.append(event.text)
                        if not notepad_workflow:
                            text_parts.append(event.text)
                            await self._emit(TokenEvent(run_id=msg.run_id, delta=event.text))
                    elif isinstance(event, ReasoningDelta):
                        await self._emit(
                            RunDetailEvent(
                                run_id=msg.run_id,
                                kind="reasoning",
                                delta=event.text,
                            )
                        )
                    elif isinstance(event, ToolCall):
                        tool_calls.append(event)
                    elif isinstance(event, Done):
                        total_usage = _add_usage(total_usage, event.usage)

                if notepad_workflow:
                    if total_usage.output_tokens >= (768 if is_ollama else ASK_MAX_TOKENS):
                        raise LLMError(
                            "Document generation reached its length limit; nothing was typed. "
                            "Request a shorter document and try again."
                        )
                    document = "".join(turn_text).strip()
                    # Some local models emit these wrappers despite content-only instructions.
                    document = re.sub(
                        r"<(?:think|thought)>.*?</(?:think|thought)>", "", document, flags=re.DOTALL
                    ).strip()
                    document = re.sub(r"^```(?:\w+)?\s*\n([\s\S]*?)\n```$", r"\1", document).strip()
                    tool_calls = [
                        ToolCall(
                            id=f"notepad_{msg.run_id}",
                            name="write_notepad",
                            arguments_json=json.dumps({"text": document}),
                        )
                    ]
                    turn_text = []

                if not tool_calls:
                    turn_combined = "".join(turn_text).strip()
                    action_intent = bool(
                        re.search(
                            r"\b(i will|let's|proceed to|going to|next,?\s*i|trying to)\s+"
                            r"(create|write|open|read|delete|move|run|execute|search)\b",
                            turn_combined,
                            re.IGNORECASE,
                        )
                    )
                    needs_lookup = web_lookup_requested(msg.text) and _step_num == 1
                    if (action_intent or needs_lookup) and nudges < 1:
                        nudges += 1
                        log.info(
                            "Agent stated intent without tool calls on step %d, nudging...",
                            _step_num,
                        )
                        messages.append({"role": "assistant", "content": turn_combined or None})
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "Use browser_search now for the topic in this conversation. "
                                    "Return a tool call; do not suggest a manual search."
                                    if needs_lookup
                                    else "Please proceed now and execute the required tool call "
                                    "using the function calling format."
                                ),
                            }
                        )
                        continue

                    final_text = "".join(text_parts)
                    self._record_message(conversation_id, "assistant", final_text)
                    await self._emit(
                        RunFinishedEvent(
                            run_id=msg.run_id, ok=True, text=final_text, usage=total_usage
                        )
                    )
                    await set_orb("idle")
                    return

                assistant_tool_calls: list[dict[str, Any]] = []
                for tc in tool_calls:
                    tc_dict: dict[str, Any] = {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": tc.arguments_json},
                    }
                    if tc.extra_content:
                        tc_dict["extra_content"] = tc.extra_content
                    assistant_tool_calls.append(tc_dict)

                messages.append(
                    {
                        "role": "assistant",
                        "content": "".join(turn_text) or None,
                        "tool_calls": assistant_tool_calls,
                    }
                )

                for tc in tool_calls:
                    tool = self._registry.get(tc.name)
                    if tool is None:
                        err_text = f"unknown tool {tc.name!r}"
                        if validation_retries < 1:
                            validation_retries += 1
                            messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tc.id,
                                    "content": wrap_untrusted(f"tool:{tc.name}", err_text),
                                }
                            )
                            continue
                        await self._finish_validation_failure(
                            msg.run_id, conversation_id, tc.name, err_text, text_parts, total_usage
                        )
                        return

                    try:
                        parsed_args = tool.Args.model_validate_json(tc.arguments_json or "{}")
                    except (ValidationError, ValueError) as exc:
                        err_text = f"validation error for {tool.name}: {exc}"
                        if validation_retries < 1:
                            validation_retries += 1
                            messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tc.id,
                                    "content": wrap_untrusted(f"tool:{tool.name}", err_text),
                                }
                            )
                            continue
                        await self._finish_validation_failure(
                            msg.run_id,
                            conversation_id,
                            tool.name,
                            err_text,
                            text_parts,
                            total_usage,
                        )
                        return

                    step_seq += 1
                    step_id = tc.id if tc.id and tc.id not in used_step_ids else f"step_{step_seq}"
                    used_step_ids.add(step_id)

                    latest_settings = self._settings.get()
                    roots = RootStore(self._db).list()
                    resolve_tool_paths(tool, parsed_args, roots)

                    args_json_str = parsed_args.model_dump_json()
                    preview_str = tool.preview(parsed_args)
                    started_at = now_iso()
                    started_mono = time.monotonic()
                    current_step = (
                        step_id,
                        tool.name,
                        args_json_str,
                        tool.tier.value,
                        started_mono,
                    )

                    await self._emit(
                        StepStartedEvent(
                            run_id=msg.run_id,
                            step_id=step_id,
                            tool=tool.name,
                            summary=preview_str,
                            tier=tool.tier.value,
                        )
                    )

                    trust_store = TrustStore(self._db)
                    decision = decide(
                        tool,
                        parsed_args,
                        roots=roots,
                        trusted=trust_store,
                        sensitive_domains=latest_settings.sensitive_domains,
                    )

                    if decision.action == PolicyAction.DENY:
                        blocked_msg = f"blocked by policy: {decision.reason}"
                        duration_ms = int((time.monotonic() - started_mono) * 1000)
                        self._log_action(
                            run_id=msg.run_id,
                            step_id=step_id,
                            tool=tool.name,
                            args_json=args_json_str,
                            tier=tool.tier.value,
                            decision="blocked",
                            ok=False,
                            result_summary=blocked_msg,
                            verified=None,
                            started_at=started_at,
                            duration_ms=duration_ms,
                        )
                        current_step = None
                        await self._emit(
                            StepFinishedEvent(
                                run_id=msg.run_id,
                                step_id=step_id,
                                ok=False,
                                summary=blocked_msg,
                                verified=None,
                            )
                        )
                        completed_summaries.append(f"{tool.name}: {blocked_msg}")
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": wrap_untrusted(f"tool:{tool.name}", blocked_msg),
                            }
                        )
                        continue

                    log_decision = decision.log_decision
                    if decision.action == PolicyAction.ASK:
                        await self._emit(
                            ApprovalRequestEvent(
                                run_id=msg.run_id,
                                step_id=step_id,
                                tool=tool.name,
                                args=parsed_args.model_dump(mode="json"),
                                preview=preview_str,
                            )
                        )
                        await set_orb("attention")
                        user_decision, timed_out = await self._await_approval(active, step_id)
                        if user_decision == "deny":
                            reason = "approval timed out" if timed_out else "denied by user"
                            duration_ms = int((time.monotonic() - started_mono) * 1000)
                            self._log_action(
                                run_id=msg.run_id,
                                step_id=step_id,
                                tool=tool.name,
                                args_json=args_json_str,
                                tier=tool.tier.value,
                                decision="denied",
                                ok=False,
                                result_summary=reason,
                                verified=None,
                                started_at=started_at,
                                duration_ms=duration_ms,
                            )
                            current_step = None
                            await self._emit(
                                StepFinishedEvent(
                                    run_id=msg.run_id,
                                    step_id=step_id,
                                    ok=False,
                                    summary=reason,
                                    verified=None,
                                )
                            )
                            completed_summaries.append(f"{tool.name}: {reason}")
                            messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tc.id,
                                    "content": wrap_untrusted(
                                        f"tool:{tool.name}", f"denied: {reason}"
                                    ),
                                }
                            )
                            if notepad_workflow:
                                final_text = f"Notepad operation {reason}; nothing was written."
                                self._record_message(conversation_id, "assistant", final_text)
                                await self._emit(
                                    RunFinishedEvent(
                                        run_id=msg.run_id,
                                        ok=False,
                                        text=final_text,
                                        usage=total_usage,
                                    )
                                )
                                await set_orb("idle")
                                return
                            continue
                        if user_decision == "always":
                            log_decision = "always"
                            tkey = tool.trust_key(parsed_args) or "*"
                            trust_store.add(tool.name, tkey)
                        else:
                            log_decision = "approved"

                    await set_orb("acting")
                    ctx = ToolContext(
                        run_id=msg.run_id,
                        step_id=step_id,
                        snapshot=snapshot,
                        db=self._db,
                        workers=self._workers,
                        user_text=msg.text,
                        memory=self._memory,
                        settings=latest_settings,
                        provider=provider,
                        emit=self._emit,
                    )
                    result, verified = await self._execute_and_verify(tool, parsed_args, ctx)
                    # Persist action evidence immediately, even if the next model call fails.
                    self._record_message(
                        conversation_id,
                        "assistant",
                        wrap_untrusted(
                            "action_result",
                            f"Tool {tool.name}: ok={result.ok}, verified={verified}. "
                            f"{result.summary[:1000]}",
                        ),
                    )
                    if verified is not True and ctx.unverified_cleanup:
                        active.unverified_cleanup.extend(ctx.unverified_cleanup)

                    duration_ms = int((time.monotonic() - started_mono) * 1000)
                    self._log_action(
                        run_id=msg.run_id,
                        step_id=step_id,
                        tool=tool.name,
                        args_json=args_json_str,
                        tier=tool.tier.value,
                        decision=log_decision,
                        ok=result.ok,
                        result_summary=result.summary,
                        verified=verified,
                        started_at=started_at,
                        duration_ms=duration_ms,
                    )
                    current_step = None
                    await self._emit(
                        StepFinishedEvent(
                            run_id=msg.run_id,
                            step_id=step_id,
                            ok=result.ok,
                            summary=result.summary,
                            verified=verified,
                        )
                    )
                    completed_summaries.append(f"{tool.name}: {result.summary}")
                    truncated = _truncate_result(result.llm_text())
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": wrap_untrusted(f"tool:{tool.name}", truncated),
                        }
                    )
                    if notepad_workflow:
                        # Tool evidence is sufficient: don't regenerate a long final answer.
                        final_text = result.summary
                        if result.ok and verified is not True:
                            final_text = (
                                "Notepad text could not be verified. Inspect the new document "
                                "before retrying. No file was saved."
                            )
                        self._record_message(conversation_id, "assistant", final_text)
                        await self._emit(TokenEvent(run_id=msg.run_id, delta=final_text))
                        await self._emit(
                            RunFinishedEvent(
                                run_id=msg.run_id,
                                ok=result.ok and verified is True,
                                text=final_text,
                                usage=total_usage,
                            )
                        )
                        await set_orb("idle")
                        return

            # MAX_STEPS reached (§12): summarise progress and ask user how to continue.
            bullets = "\n".join(f"- {s}" for s in completed_summaries[-MAX_STEPS:])
            cap_note = (
                f"\n\nReached the {MAX_STEPS}-step limit. Progress so far:\n{bullets}\n\n"
                "How would you like me to continue?"
            ).lstrip()
            if text_parts:
                cap_note = "\n\n" + cap_note
            text_parts.append(cap_note)
            await self._emit(TokenEvent(run_id=msg.run_id, delta=cap_note))
            final_text = "".join(text_parts)
            self._record_message(conversation_id, "assistant", final_text)
            await self._emit(
                RunFinishedEvent(run_id=msg.run_id, ok=True, text=final_text, usage=total_usage)
            )
            await set_orb("idle")
        except asyncio.CancelledError:
            log.info("agent run %s cancelled", msg.run_id)
            if current_step is not None:
                step_id, t_name, a_json, t_tier, s_mono = current_step
                dur = int((time.monotonic() - s_mono) * 1000)
                self._log_action(
                    run_id=msg.run_id,
                    step_id=step_id,
                    tool=t_name,
                    args_json=a_json,
                    tier=t_tier,
                    decision="denied",
                    ok=False,
                    result_summary="cancelled",
                    verified=None,
                    started_at=now_iso(),
                    duration_ms=dur,
                )
                await self._emit(
                    StepFinishedEvent(
                        run_id=msg.run_id,
                        step_id=step_id,
                        ok=False,
                        summary="cancelled",
                        verified=None,
                    )
                )
            await self._emit(
                RunFinishedEvent(
                    run_id=msg.run_id,
                    ok=False,
                    text="".join(text_parts),
                    usage=total_usage,
                )
            )
            await self._emit(OrbStateEvent(state="idle"))
            raise
        except LLMError as exc:
            log.warning("agent run %s failed: %s", msg.run_id, exc)
            self._record_message(
                conversation_id,
                "assistant",
                "Run interrupted before completion. Earlier recorded tool results still apply. "
                + str(exc),
            )
            await self._fail(msg.run_id, str(exc))

    async def _await_approval(
        self, active: _ActiveRun, step_id: str
    ) -> tuple[ApprovalDecision, bool]:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[ApprovalDecision] = loop.create_future()
        active.approvals[step_id] = fut
        try:
            decision = await asyncio.wait_for(fut, timeout=self._approval_timeout_s)
            return decision, False
        except TimeoutError:
            return "deny", True
        finally:
            active.approvals.pop(step_id, None)

    async def _execute_and_verify(
        self, tool: Tool[Any], parsed_args: Any, ctx: ToolContext
    ) -> tuple[ToolResult, bool | None]:
        try:
            raw_res = await self._workers.run(
                tool.worker, tool.run, parsed_args, ctx, timeout=tool.timeout_s
            )
            result = (
                raw_res
                if isinstance(raw_res, ToolResult)
                else ToolResult(ok=True, data=raw_res, summary=str(raw_res))
            )
        except TimeoutError:
            return (
                ToolResult(ok=False, summary=f"{tool.name} timed out after {tool.timeout_s:g}s"),
                None,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("tool %s raised error: %s", tool.name, exc)
            return ToolResult(ok=False, summary=f"{tool.name} failed: {exc}"), None

        if not result.ok:
            return result, None

        # Run verify only when overridden on the tool subclass.
        if type(tool).verify is Tool.verify:
            return result, None

        try:
            ver = await self._workers.run(
                tool.worker, tool.verify, parsed_args, result, timeout=tool.timeout_s
            )
            verified = None if ver is None else bool(ver)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("verify for %s failed: %s", tool.name, exc)
            verified = False
        return result, verified

    async def _finish_validation_failure(
        self,
        run_id: str,
        conversation_id: str,
        tool_name: str,
        err_text: str,
        text_parts: list[str],
        usage: Usage,
    ) -> None:
        log.warning("run %s stopping after repeated validation failure: %s", run_id, err_text)
        note = (
            f"I couldn't validate the parameters for `{tool_name}` ({err_text}). "
            "Could you clarify the details so I can try again?"
        )
        if text_parts:
            note = "\n\n" + note
        text_parts.append(note)
        await self._emit(TokenEvent(run_id=run_id, delta=note))
        final_text = "".join(text_parts)
        self._record_message(conversation_id, "assistant", final_text)
        await self._emit(RunFinishedEvent(run_id=run_id, ok=True, text=final_text, usage=usage))
        await self._emit(OrbStateEvent(state="idle"))

    def _log_action(
        self,
        *,
        run_id: str,
        step_id: str,
        tool: str,
        args_json: str,
        tier: str,
        decision: str,
        ok: bool | None,
        result_summary: str | None,
        verified: bool | None,
        started_at: str,
        duration_ms: int,
    ) -> None:
        conn = self._db.conn()
        with conn:
            conn.execute(
                "INSERT INTO action_log (run_id, step_id, tool, args_json, tier, decision,"
                " ok, result_summary, verified, started_at, duration_ms)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    step_id,
                    tool,
                    args_json,
                    tier,
                    decision,
                    None if ok is None else int(ok),
                    result_summary,
                    None if verified is None else int(verified),
                    started_at,
                    duration_ms,
                ),
            )

    def _start_conversation(
        self, conversation_id: str, snapshot: ContextSnapshot | None, user_text: str
    ) -> None:
        now = now_iso()
        ctx_json = snapshot.model_dump_json() if snapshot is not None else None
        conn = self._db.conn()
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO conversations (id, started_at, context_json)"
                " VALUES (?, ?, ?)",
                (conversation_id, now, ctx_json),
            )
            conn.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at)"
                " VALUES (?, 'user', ?, ?)",
                (conversation_id, user_text, now),
            )

    def _record_message(self, conversation_id: str, role: str, content: str) -> None:
        conn = self._db.conn()
        with conn:
            conn.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at)"
                " VALUES (?, ?, ?, ?)",
                (conversation_id, role, content, now_iso()),
            )

    def _load_history(self, conversation_id: str, limit: int = 10) -> list[dict[str, str]]:
        conn = self._db.conn()
        with conn:
            cursor = conn.execute(
                "SELECT role, content FROM messages WHERE conversation_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (conversation_id, limit),
            )
            rows = cursor.fetchall()
        history: list[dict[str, str]] = []
        for role, content in reversed(rows):
            cleaned = re.sub(r"<thought>.*?</thought>", "", content, flags=re.DOTALL).strip()
            if cleaned:
                if len(cleaned) > 1500:
                    cleaned = cleaned[:1500] + "... [truncated]"
                history.append({"role": role, "content": cleaned})
        return history

    async def _memories_for(
        self,
        msg: RunMessage,
        settings: Settings,
        snapshot: ContextSnapshot | None,
        provider: LLMProvider,
    ) -> tuple[Memory | None, list[Memory]]:
        """Explicit "remember …" first (so it is in the prompt), then retrieval (§14)."""
        if self._memory is None or not self._memory.enabled:
            return None, []
        if _GREETING_OR_IDENTITY.search(msg.text or ""):
            return None, []
        project = snapshot.project.name if snapshot and snapshot.project else None
        process = snapshot.process if snapshot else None
        app = snapshot.app if snapshot else None
        saved: Memory | None = None
        if is_remember_request(msg.text):
            outcome = await self._memory.remember(
                provider,
                ask_model=settings.active_ask_model or settings.active_agent_model,
                router_model=settings.active_router_model,
                user_text=msg.text,
                project=project,
                process=process,
                app=app,
            )
            # A duplicate wrote nothing, so there is nothing to announce (or to Undo).
            if outcome is not None and outcome.action != "duplicate":
                saved = outcome.memory
                await self._emit(
                    MemorySavedEvent(
                        memory=MemoryRef(
                            id=saved.id, content=saved.content, scope=scope_label(saved)
                        )
                    )
                )
        is_ollama = settings.provider == "ollama" or getattr(provider, "_is_ollama", False)
        memories = self._memory.recall(
            msg.text,
            project=project,
            app=app,
            process=process,
            limit=3 if is_ollama else 8,
            fill_recent=not is_ollama,
        )
        return saved, memories

    async def _fail(self, run_id: str, message: str) -> None:
        await self._emit(RunErrorEvent(run_id=run_id, message=message))
        await self._emit(OrbStateEvent(state="error"))


def _add_usage(a: Usage, b: Usage) -> Usage:
    return Usage(
        input_tokens=a.input_tokens + b.input_tokens,
        output_tokens=a.output_tokens + b.output_tokens,
        cost_usd=round(a.cost_usd + b.cost_usd, 8),
    )


def _truncate_result(text: str) -> str:
    if len(text) <= MAX_TOOL_RESULT_CHARS:
        return text
    suffix = "\n...[truncated]"
    return text[: MAX_TOOL_RESULT_CHARS - len(suffix)] + suffix


def allowed_folders_block(roots: Sequence[AllowedRoot]) -> str:
    """Guidance on configured allowed folders for the agent."""
    if not roots:
        return (
            "Allowed folders:\n"
            "No allowed folders are configured in Settings. File operations will fail until "
            "the user adds an allowed folder in Settings."
        )
    lines = ["Allowed folders on the user's system:"]
    for r in roots:
        perm = "read/write" if r.can_write else "read-only"
        lines.append(f"- {r.path} ({perm})")
    lines.append(
        "When using file tools (search_files, read_file, write_file, create_folder, "
        "move_file, trash_file), always use the exact absolute path within one of "
        "these allowed folders."
    )
    return "\n".join(lines)


def _build_initial_messages(
    msg: RunMessage,
    settings: Settings,
    snapshot: ContextSnapshot | None,
    memories: list[Memory] | None = None,
    just_saved: Memory | None = None,
    roots: Sequence[AllowedRoot] = (),
    *,
    agent: bool = False,
    is_ollama: bool = False,
    model_name: str = "",
    history: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    text = (msg.text or "").strip()
    is_greeting = bool(_GREETING_OR_IDENTITY.search(text))
    include_sel = False
    if snapshot is not None and not (is_greeting and not agent):
        include_sel = should_include_selection(msg, agent=agent)
    system = system_base_prompt(model_name)
    if agent:
        system += f"\n\n{AGENT_SYSTEM}"
        system += f"\n\n{allowed_folders_block(roots)}"
    if memories and not (is_greeting and not agent):
        system += f"\n\n{memory_block(memories, just_saved)}"
    if msg.action is not None and msg.action not in ("ask", "do"):
        task = action_instruction(msg.action, settings.translate_language)
        system += f"\n\nTask:\n{task}"
    user = msg.text
    if snapshot is not None:
        if is_greeting and not agent:
            # Standalone greetings or identity inquiries don't need screen context.
            pass
        else:
            max_sel = 3500 if is_ollama else None
            ctx = context_block(
                snapshot, include_selection=include_sel, max_selection_chars=max_sel
            )
            user = ctx + (f"\n\n{msg.text}" if msg.text else "")

    msgs: list[dict[str, Any]] = [{"role": "system", "content": system}]
    if history:
        for h in history:
            msgs.append({"role": h["role"], "content": h["content"]})
    msgs.append({"role": "user", "content": user})
    return msgs
