"""One-shot JSON-returning LLM calls (router / extraction / dedup judging)."""

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from clickachu.llm.provider import LLMProvider, TextDelta

# (system, user) -> parsed JSON object, or None when the reply wasn't a JSON object.
AskJson = Callable[[str, str], Awaitable[dict[str, Any] | None]]

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_json_object(text: str) -> dict[str, Any] | None:
    cleaned = _FENCE.sub("", text.strip())
    for candidate in (cleaned, _outer_braces(cleaned)):
        if candidate is None:
            continue
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _outer_braces(text: str) -> str | None:
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if 0 <= start < end else None


def make_ask_json(provider: LLMProvider, model: str, *, max_tokens: int = 300) -> AskJson:
    async def ask(system: str, user: str) -> dict[str, Any] | None:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        parts: list[str] = []
        async for event in provider.stream(model, messages, max_tokens=max_tokens):
            if isinstance(event, TextDelta):
                parts.append(event.text)
        return parse_json_object("".join(parts))

    return ask
