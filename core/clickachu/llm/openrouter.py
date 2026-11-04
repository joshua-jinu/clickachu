"""Gemini / OpenAI-compatible LLM provider via the `openai` SDK (PLAN §11)."""

import logging
from collections.abc import AsyncIterator
from typing import Any

import openai

from clickachu.api.protocol import Usage
from clickachu.llm.provider import Done, LLMError, LLMEvent, TextDelta, ToolCall

log = logging.getLogger(__name__)

TIMEOUT_S = 60.0
# The SDK retries connection errors, 408/409/429 and 5xx with exponential backoff.
MAX_RETRIES = 3
OPENROUTER_HEADERS = {
    "HTTP-Referer": "https://github.com/clickachu/clickachu",
    "X-Title": "Clickachu",
}


class OpenRouterProvider:
    def __init__(self, *, base_url: str, api_key: str) -> None:
        self._base_url = base_url
        self._is_openrouter = "openrouter.ai" in base_url.lower()
        self._is_gemini = "generativelanguage.googleapis.com" in base_url.lower()
        self._is_ollama = "11434" in base_url.lower() or "ollama" in base_url.lower()
        self._client = openai.AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "ollama",
            timeout=TIMEOUT_S,
            max_retries=1 if self._is_ollama else MAX_RETRIES,
            default_headers=OPENROUTER_HEADERS if self._is_openrouter else None,
        )

    async def stream(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
    ) -> AsyncIterator[LLMEvent]:
        kwargs: dict[str, Any] = {}
        if tools:
            kwargs["tools"] = tools
        if self._is_openrouter:
            # OpenRouter extension: include cost in the final usage chunk.
            kwargs["extra_body"] = {"usage": {"include": True}}
        elif self._is_gemini or "gemini" in model.lower():
            # Disable internal thinking budget on Gemini for fast TTFT,
            # deterministic tool calling, and so small max_tokens calls (router/dedup) aren't
            # consumed by thinking tokens.
            kwargs["reasoning_effort"] = "none"
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
                f"Model '{model}' not found in Ollama — "
                f"run 'ollama pull {model}' to download it."
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
            return (
                f"{label} service is temporarily unavailable ({code}) — "
                "please try again shortly."
            )
        return f"Model request failed ({code}): {msg}"

    def _format_api_error(self, exc: openai.APIError, *, mid_stream: bool = False) -> str:
        prefix = "Model stream interrupted" if mid_stream else "Model request failed"
        label = self._provider_label()
        if self._is_ollama and isinstance(exc, openai.APIConnectionError):
            return (
                f"{prefix}: could not reach Ollama at {self._base_url} — "
                "make sure Ollama is running."
            )
        if isinstance(exc, openai.APIConnectionError):
            return f"{prefix}: could not reach {label} — check your internet connection."
        if isinstance(exc, openai.APITimeoutError):
            return f"{prefix}: request timed out — the model service took too long to respond."
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
