"""Explicit CPU compatibility mode via Ollama's native runtime options."""

import json
import logging
import time
import uuid

import httpx

from clickachu.api.protocol import Usage
from clickachu.llm.local_actions import repetitive_reasoning
from clickachu.llm.provider import Done, LLMError, ReasoningDelta, TextDelta, ToolCall

log = logging.getLogger(__name__)


def native_messages(messages):
    converted = []
    for message in messages:
        item = dict(message)
        content = item.get("content")
        if isinstance(content, list):
            item["content"] = "\n".join(p["text"] for p in content if p.get("type") == "text")
            images = []
            for part in content:
                if part.get("type") == "image_url":
                    url = part["image_url"]["url"]
                    if not url.startswith("data:image/") or ";base64," not in url:
                        raise LLMError("Local image input requires an inline image.")
                    images.append(url.split(";base64,", 1)[1])
            if images:
                item["images"] = images
        converted.append(item)
    return converted


async def stream_cpu(
    base_url,
    model,
    messages,
    *,
    max_tokens,
    json_mode=False,
    response_schema=None,
    tools=None,
    reasoning_effort="off",
    keep_alive=None,
    temperature=None,
):
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    body = {
        "model": model,
        "messages": native_messages(messages),
        "stream": True,
        "options": {"num_gpu": 0, "num_ctx": 4096, "num_predict": max_tokens},
    }
    if response_schema is not None or json_mode:
        body["format"] = response_schema if response_schema is not None else "json"
    if keep_alive is not None:
        body["keep_alive"] = keep_alive
    if temperature is not None:
        body["options"]["temperature"] = temperature
    if tools:
        body["tools"] = tools
    if reasoning_effort != "auto":
        body["think"] = reasoning_effort != "off"
    started = time.monotonic()
    structured_chars = 0
    reasoning_tail = ""
    log.info(
        "local request model=%s prompt_chars=%d schema_chars=%d max_tokens=%d",
        model,
        sum(len(str(m.get("content", ""))) for m in messages),
        len(json.dumps(response_schema)) if response_schema else 0,
        max_tokens,
    )
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=5)) as client:
            async with client.stream("POST", root + "/api/chat", json=body) as response:
                if response.status_code != 200:
                    raise LLMError(
                        f"Ollama CPU inference failed ({response.status_code}). "
                        "Completed actions remain in place; check Ollama's log."
                    )
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    if chunk.get("error"):
                        raise LLMError("Ollama CPU inference failed: " + str(chunk["error"])[:300])
                    message = chunk.get("message", {})
                    if response_schema is not None:
                        structured_chars += len(message.get("thinking") or "")
                        reasoning_tail = (reasoning_tail + (message.get("thinking") or ""))[-256:]
                        if structured_chars > 2000 or repetitive_reasoning(reasoning_tail):
                            raise LLMError(
                                "Local structured generation drifted; no action was executed."
                            )
                    if message.get("thinking"):
                        yield ReasoningDelta(message["thinking"])
                    if message.get("content"):
                        yield TextDelta(message["content"])
                    for call in message.get("tool_calls", []):
                        function = call["function"]
                        yield ToolCall(
                            id="cpu_" + uuid.uuid4().hex,
                            name=function["name"],
                            arguments_json=json.dumps(function["arguments"]),
                        )
                    if chunk.get("done"):
                        log.info(
                            "local timing model=%s total_ms=%d load_ms=%d prefill_ms=%d "
                            "input_tokens=%d cached_tokens=%d decode_ms=%d output_tokens=%d",
                            model,
                            int((time.monotonic() - started) * 1000),
                            chunk.get("load_duration", 0) // 1_000_000,
                            chunk.get("prompt_eval_duration", 0) // 1_000_000,
                            chunk.get("prompt_eval_count", 0),
                            chunk.get("prompt_eval_cached_count", 0),
                            chunk.get("eval_duration", 0) // 1_000_000,
                            chunk.get("eval_count", 0),
                        )
                        yield Done(
                            Usage(
                                input_tokens=chunk.get("prompt_eval_count", 0),
                                output_tokens=chunk.get("eval_count", 0),
                            )
                        )
                        return
        raise LLMError("Ollama CPU stream ended before completion.")
    except httpx.TimeoutException as exc:
        raise LLMError(
            "Ollama CPU inference timed out. Completed actions remain in place."
        ) from exc
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise LLMError("Ollama CPU connection or response failed. Check Ollama's log.") from exc
