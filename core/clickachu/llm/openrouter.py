"""Gemini / OpenAI-compatible LLM provider via the `openai` SDK (PLAN §11)."""

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import openai

from clickachu.api.protocol import Usage
from clickachu.llm.provider import Done, LLMError, LLMEvent, ReasoningDelta, TextDelta, ToolCall

log = logging.getLogger(__name__)

TIMEOUT_S = 60.0
# The SDK retries connection errors, 408/409/429 and 5xx with exponential backoff.
MAX_RETRIES = 3
OPENROUTER_HEADERS = {
    "HTTP-Referer": "https://github.com/clickachu/clickachu",
    "X-Title": "Clickachu",
}


class OpenRouterProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        reasoning_effort: str = "off",
        ollama_cpu_only: bool = False,
        summary_model: str = "",
    ) -> None:
        self._base_url = base_url
        self._reasoning_effort = reasoning_effort
        self._ollama_cpu_only = ollama_cpu_only
        self._summary_model = summary_model
        self._is_openrouter = "openrouter.ai" in base_url.lower()
        self._is_gemini = "generativelanguage.googleapis.com" in base_url.lower()
        self._is_ollama = "11434" in base_url.lower() or "ollama" in base_url.lower()
        self._client = openai.AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "ollama",
            timeout=httpx.Timeout(
                TIMEOUT_S, connect=5.0, read=180.0 if self._is_ollama else TIMEOUT_S
            ),
            max_retries=0 if self._is_ollama else MAX_RETRIES,
            default_headers=OPENROUTER_HEADERS if self._is_openrouter else None,
        )

    def stream_json(self, model, messages, max_tokens=384):
        return self.stream(model, messages, max_tokens=max_tokens, json_mode=True)

    def stream_summary(self, model, messages, source_ids, max_tokens=224):
        schema = {
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
                "source_ids": {
                    "type": "array",
                    "items": {"type": "integer", "enum": source_ids},
                    "minItems": 1,
                    "maxItems": 3,
                },
            },
            "required": ["answer", "source_ids"],
            "additionalProperties": False,
        }
        summary_model = getattr(self, "_summary_model", "") if self._is_ollama else ""
        return self.stream(
            summary_model or model,
            messages,
            max_tokens=max_tokens,
            response_schema=schema if self._is_ollama else None,
            json_mode=not self._is_ollama,
            keep_alive=0 if summary_model and summary_model != model else None,
            temperature=0 if self._is_ollama else None,
        )

    async def stream(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
        tool_choice: str | None = None,
        json_mode: bool = False,
        response_schema: dict | None = None,
        keep_alive: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[LLMEvent]:
        if self._is_ollama and getattr(self, "_ollama_cpu_only", False):
            from clickachu.llm.ollama_cpu import stream_cpu

            async for event in stream_cpu(
                self._base_url,
                model,
                messages,
                max_tokens=max_tokens,
                json_mode=json_mode,
                response_schema=response_schema,
                tools=tools,
                reasoning_effort=self._reasoning_effort,
                keep_alive=keep_alive,
                temperature=temperature,
            ):
                yield event
            return
        started = time.monotonic()
        first_output = False
        log.info(
            "model request: model=%s messages=%d tools=%d prompt_chars=%d schema_chars=%d",
            model,
            len(messages),
            len(tools or []),
            sum(len(str(m.get("content") or "")) for m in messages),
            sum(len(str(tool)) for tool in tools or []),
        )
        kwargs: dict[str, Any] = {}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if response_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "next_action",
                    "schema": response_schema,
                },
            }
        if tools:
            kwargs["tools"] = tools
            if tool_choice:
                kwargs["tool_choice"] = tool_choice
        if self._is_openrouter:
            # OpenRouter extension: include cost in the final usage chunk.
            kwargs["extra_body"] = {"usage": {"include": True}}
        effort = self._reasoning_effort
        if self._is_gemini:
            # Pro and newer Gemini families cannot disable reasoning. Do not send unsupported
            # "none" to them. Tiny router/dedup requests should not inherit a high budget.
            can_disable = model.startswith("gemini-2.5-") and "pro" not in model
            if effort == "off" or max_tokens <= 128:
                kwargs["reasoning_effort"] = "none" if can_disable else "low"
            elif effort != "auto":
                kwargs["reasoning_effort"] = effort
            if effort != "off" and max_tokens > 128:
                kwargs["extra_body"] = {
                    "extra_body": {
                        "google": {
                            "thinking_config": {"include_thoughts": True},
                        }
                    }
                }
        elif self._is_ollama and effort != "auto":
            kwargs["reasoning_effort"] = "none" if effort == "off" else effort
        elif self._is_openrouter and effort not in ("off", "auto"):
            kwargs["extra_body"]["reasoning"] = {"effort": effort}
        try:
            stream = await self._client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]  # plain dicts in OpenAI shape
                max_tokens=max_tokens,
                stream=True,
                stream_options={"include_usage": True},
                **kwargs,
            )
        except openai.APIStatusError as exc:
            raise LLMError(self._format_status_error(exc, model)) from exc
        except openai.APIError as exc:
            raise LLMError(self._format_api_error(exc)) from exc

        # Tool call fragments arrive keyed by index; assemble until the stream ends.
        pending_tools: dict[int, dict[str, Any]] = {}
        usage = Usage()
        try:
            async with stream:
                async for chunk in stream:
                    if chunk.usage is not None:
                        usage = _usage(chunk.usage)
                    for choice in chunk.choices:
                        delta = choice.delta
                        reasoning = getattr(delta, "reasoning", None) or getattr(
                            delta, "reasoning_content", None
                        )
                        if isinstance(reasoning, str) and reasoning:
                            yield ReasoningDelta(reasoning)
                        if not first_output and (delta.content or delta.tool_calls):
                            first_output = True
                            log.info(
                                "model first output: model=%s elapsed=%.2fs",
                                model,
                                time.monotonic() - started,
                            )
                        if delta.content:
                            yield TextDelta(delta.content)
                        for tc in delta.tool_calls or []:
                            idx = tc.index if tc.index is not None else 0
                            slot = pending_tools.setdefault(
                                idx, {"id": "", "name": "", "arguments": "", "extra_content": None}
                            )
                            if tc.id:
                                slot["id"] = tc.id
                            if tc.function is not None:
                                slot["name"] += tc.function.name or ""
                                slot["arguments"] += tc.function.arguments or ""
                            extra_c = getattr(tc, "extra_content", None)
                            if extra_c:
                                slot["extra_content"] = extra_c
        except openai.APIStatusError as exc:
            raise LLMError(self._format_status_error(exc, model)) from exc
        except openai.APIError as exc:
            log.warning(
                "model stream failed: model=%s elapsed=%.2fs error=%s cause=%s",
                model,
                time.monotonic() - started,
                type(exc).__name__,
                type(exc.__cause__).__name__,
            )
            raise LLMError(self._format_api_error(exc, mid_stream=True)) from exc

        for index in sorted(pending_tools):
            slot = pending_tools[index]
            yield ToolCall(
                id=slot["id"] or f"call_{index + 1}",
                name=slot["name"],
                arguments_json=slot["arguments"],
                extra_content=slot.get("extra_content"),
            )
        yield Done(usage)
        log.info(
            "model complete: model=%s elapsed=%.2fs input_tokens=%d output_tokens=%d",
            model,
            time.monotonic() - started,
            usage.input_tokens,
            usage.output_tokens,
        )

    async def stream_action(self, model, messages, tools=None, max_tokens=768):
        async for event in self.stream(
            model, messages, tools=tools, max_tokens=max_tokens, tool_choice="required"
        ):
            yield event

    async def stream_agent(self, model, messages, tools=None, max_tokens=768, require_tool=False):
        if not self._is_ollama or not tools:
            async for event in self.stream(
                model,
                messages,
                tools=tools,
                max_tokens=max_tokens,
                tool_choice="required" if require_tool else None,
            ):
                yield event
            return
        from clickachu.llm.local_actions import (
            FINISH,
            action_messages,
            action_schema,
            bind_runtime_arguments,
        )

        parts = []
        usage = Done().usage
        async for event in self.stream(
            model,
            action_messages(messages, tools, require_tool),
            max_tokens=max_tokens,
            response_schema=action_schema(tools, require_tool),
            temperature=0,
            keep_alive=900,
        ):
            if isinstance(event, TextDelta):
                parts.append(event.text)
            elif isinstance(event, Done):
                usage = event.usage
            elif isinstance(event, ReasoningDelta):
                yield event
        try:
            decision = json.loads("".join(parts))
            name, arguments = decision["tool"], decision["arguments"]
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be an object")
            if name == FINISH and not require_tool:
                answer = arguments["answer"]
                if not isinstance(answer, str):
                    raise ValueError("answer must be text")
                yield TextDelta(answer)
            elif name in {tool["function"]["name"] for tool in tools}:
                yield ToolCall(
                    id="local_" + uuid.uuid4().hex,
                    name=name,
                    arguments_json=json.dumps(
                        bind_runtime_arguments(name, arguments, tools), ensure_ascii=False
                    ),
                )
            else:
                raise ValueError("tool is not available")
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMError(
                "Local model did not produce a valid action; no unvalidated action was executed."
            ) from exc
        yield Done(usage)

    def _provider_label(self) -> str:
        if self._is_gemini:
            return "Gemini"
        if self._is_openrouter:
            return "OpenRouter"
        if self._is_ollama:
            return "Ollama"
        return "LLM provider"

    def _format_status_error(self, exc: openai.APIStatusError, model: str) -> str:
        code = exc.status_code
        msg = _error_message(exc)
        label = self._provider_label()
        if self._is_ollama and code == 404:
            return (
                f"Model '{model}' not found in Ollama — run 'ollama pull {model}' to download it."
            )
        if code in (400, 401) and (
            code == 401 or "api_key_invalid" in msg.lower() or "api key not valid" in msg.lower()
        ):
            return f"Invalid {label} API key — please check or update your key in Settings."
        if code == 402:
            return f"{label} account has insufficient credits ({msg})."
        if code == 403:
            return f"{label} access forbidden ({msg}) — check your API key permissions."
        if code == 404:
            return f"Model '{model}' not found on {label} — check model selection in Settings."
        if code == 429:
            return f"{label} rate limit reached — please wait a moment before trying again. ({msg})"
        if code in (500, 502, 503, 504):
            if self._is_ollama:
                return (
                    f"Ollama could not complete inference ({code}); its model runner may have "
                    "stopped. Completed actions remain in place. Check Ollama's log; if it "
                    "reports Vulkan 'device lost', enable CPU compatibility in Settings."
                )
            return (
                f"{label} service is temporarily unavailable ({code}) — please try again shortly."
            )
        return f"Model request failed ({code}): {msg}"

    def _format_api_error(self, exc: openai.APIError, *, mid_stream: bool = False) -> str:
        prefix = "Model stream interrupted" if mid_stream else "Model request failed"
        label = self._provider_label()
        # APITimeoutError inherits APIConnectionError: check it first.
        if isinstance(exc, openai.APITimeoutError):
            return (
                f"{prefix}: {label} took too long to respond. "
                "The model may still be running; this does not mean it crashed."
            )
        if self._is_ollama and isinstance(exc, openai.APIConnectionError):
            if mid_stream:
                return (
                    f"{prefix}: the connection to Ollama was lost while generating. "
                    "Completed actions have not been undone. Check Ollama's server log for details."
                )
            return (
                f"{prefix}: could not reach Ollama at {self._base_url} — "
                "make sure Ollama is running."
            )
        if isinstance(exc, openai.APIConnectionError):
            return f"{prefix}: could not reach {label} — check your internet connection."
        return f"{prefix}: {exc.message}"


def _usage(raw: Any) -> Usage:
    # `cost` is an OpenRouter extension, so it lives in the model's extra fields when present.
    extra = getattr(raw, "model_extra", None) or {}
    cost = extra.get("cost") or 0.0
    return Usage(
        input_tokens=raw.prompt_tokens or 0,
        output_tokens=raw.completion_tokens or 0,
        cost_usd=float(cost),
    )


def _error_message(exc: openai.APIStatusError) -> str:
    body = exc.body
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            return err["message"]
    if isinstance(body, list) and body and isinstance(body[0], dict):
        err = body[0].get("error", body[0])
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            return err["message"]
    return exc.message
