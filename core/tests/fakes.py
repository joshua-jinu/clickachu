"""Test doubles: scripted LLM provider and in-memory secret store."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

from clickachu.api.protocol import Settings
from clickachu.llm.provider import LLMEvent


class Hang:
    """Script marker: block forever (until cancelled) at this point in the stream."""


class FakeProvider:
    def __init__(self, script: Sequence[LLMEvent | Hang]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []
        self.cancelled = False

    async def stream(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
    ) -> AsyncIterator[LLMEvent]:
        self.calls.append({"model": model, "messages": messages, "tools": tools})
        for item in self.script:
            if isinstance(item, Hang):
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise
            else:
                await asyncio.sleep(0)
                yield item

    def factory(self, _settings: Settings, _api_key: str) -> "FakeProvider":
        return self


class FakeSecretStore:
    def __init__(self, key: str | None = "sk-test") -> None:
        self.key = key

    def get_openrouter_key(self) -> str | None:
        return self.key

    def set_openrouter_key(self, key: str) -> None:
        self.key = key


class QueueProvider:
    """Replies per call from a queue of event lists; the last entry repeats when it runs out."""

    def __init__(self, replies: Sequence[Sequence[LLMEvent]]) -> None:
        self.replies = [list(r) for r in replies]
        self.calls: list[dict[str, Any]] = []

    async def stream(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
    ) -> AsyncIterator[LLMEvent]:
        index = min(len(self.calls), len(self.replies) - 1)
        self.calls.append({"model": model, "messages": messages, "tools": tools})
        for item in self.replies[index]:
            await asyncio.sleep(0)
            yield item

    def factory(self, _settings: Settings, _api_key: str) -> "QueueProvider":
        return self
