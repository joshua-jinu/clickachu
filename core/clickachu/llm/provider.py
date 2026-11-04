"""Provider-neutral LLM interface (PLAN §11)."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from clickachu.api.protocol import Usage


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ReasoningDelta:
    text: str


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments_json: str
    extra_content: dict[str, Any] | None = None


@dataclass(frozen=True)
class Done:
    usage: Usage = field(default_factory=Usage)


LLMEvent = TextDelta | ReasoningDelta | ToolCall | Done


class LLMError(Exception):
    """Provider failure with a message safe to show the user."""


class LLMProvider(Protocol):
    def stream(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
    ) -> AsyncIterator[LLMEvent]: ...
