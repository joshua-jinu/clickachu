"""Semantic routing with a safe research fallback; no entity-name allowlist."""

import asyncio
import re
from dataclasses import dataclass
from typing import Literal

from clickachu.llm.json_ask import make_ask_json
from clickachu.llm.provider import LLMError, LLMProvider
from clickachu.llm.research_subject import explicit_subject, preserve_subject
from clickachu.llm.router import (
    heuristic_mode,
    lookup_query,
    screen_question_requested,
    web_lookup_requested,
)
from clickachu.llm.time_context import date_context, today


@dataclass(frozen=True)
class RequestRoute:
    route: Literal["ask", "agent", "research"]
    query: str = ""
    recent: bool = False


_FRESH = re.compile(
    r"\b(?:recent(?:ly)?|latest|lately|today|yesterday|currently|breaking|news|upcoming"
    r"|(?:this|last|next)\s+(?:week|month|year)|right\s+now|as\s+of)\b",
    re.IGNORECASE,
)
_SOCIAL = re.compile(
    r"\s*(?:(?:hello|helo|hi|hey|namaste|thanks|thank\s+you)[,!?.\s]*)?"
    r"(?:(?:who\s+are\s+you|what\s+can\s+you\s+do(?:\s+for\s+me)?|how\s+are\s+you)"
    r"[!?.,\s]*)?\s*",
    re.IGNORECASE,
)
_OFFLINE = re.compile(
    r"\b(?:offline|(?:don't|do\s+not|never)\s+(?:search|browse|check|look|google|do\s+anything))\b",
    re.IGNORECASE,
)
# Where to watch/buy/stream something changes over time; model memory is never enough.
_AVAILABILITY = re.compile(
    r"\b(?:where|which\s+(?:ott|streaming|platform|service|app|site|website|store|channel))\b"
    r"[^.!?\n]{0,40}\b(?:watch|stream|buy|purchase|rent|download|listen|available|find|get)\b"
    r"|\b(?:ott|streaming)\s+(?:platform|service|app)s?\b",
    re.IGNORECASE,
)
_CACHE: dict[tuple[str, str, str, str], RequestRoute] = {}

ROUTING_SYSTEM = """Choose how a desktop assistant should handle the request.
Reply with JSON only: {"route":"ask"|"agent"|"research", "query":"...", "recent":false}.
research: external factual questions of ANY wording: people, organizations, products, places,
history, science, events, news, recommendations, availability, prices, schedules or changing facts.
Use research even if you think you know the answer. recent=true if the answer needs up-to-date news.
query: concise search terms resolving pronouns/follow-ups from the conversation, not an answer.
ask: greetings, assistant capabilities, creative writing, arithmetic, translation, or analysis of
material the user already supplied. agent: perform actions on the user's computer.
Do not classify a factual question as ask just because it lacks a search command.
If uncertain about an external factual question, choose research. Never invent a factual answer.
"""


def _research(
    text: str, history: list[dict[str, str]], query: str = "", recent=False
) -> RequestRoute:
    query = preserve_subject(text, query.strip()[:400] or lookup_query(text, history))
    recent = recent or bool(_FRESH.search(text))
    if recent and not re.search(r"\b(?:19|20)\d{2}\b", text):
        # A classifier must not smuggle its training year into a current-event query.
        query = re.sub(r"\b(?:19|20)\d{2}\b", "", query).strip()
    return RequestRoute("research", query, recent)


async def route_request(
    text: str,
    *,
    provider: LLMProvider,
    model: str,
    history: list[dict[str, str]] | None = None,
    mode=None,
    action=None,
    timeout_s=12.0,
) -> RequestRoute:
    history = history or []
    # These are clear UI/user constraints, not topic-specific knowledge rules.
    if _OFFLINE.search(text):
        return RequestRoute("ask")
    if screen_question_requested(text):
        # "What is currently on my screen?" is local observation, not a public web search.
        return RequestRoute("agent")
    if web_lookup_requested(text):
        return _research(text, history)
    if action not in (None, "ask"):
        return RequestRoute("agent" if action in ("do", "fix") else "ask")
    fast = heuristic_mode(text, mode=mode)
    if fast == "agent" and not re.match(r"\s*(?:list|write)\b", text, re.IGNORECASE):
        return RequestRoute("agent")
    if _SOCIAL.fullmatch(text) or re.fullmatch(r"[\d\s.+*/()=-]+", text):
        return RequestRoute("ask")
    if explicit_subject(text):
        return _research(text, history, explicit_subject(text))
    if _AVAILABILITY.search(text):
        return _research(text, history, recent=True)
    # Freshness is a mandatory safeguard even if a small model would misclassify it.
    if _FRESH.search(text):
        return _research(text, history, recent=True)
    key = (model, today().isoformat(), text.strip().lower(), str(history[-2:]))
    if key in _CACHE:
        return _CACHE[key]
    context = "\n".join(f"{m['role']}: {m['content'][:400]}" for m in history[-2:])
    ask = make_ask_json(provider, model, max_tokens=96)
    try:
        reply = await asyncio.wait_for(
            ask(
                ROUTING_SYSTEM + "\n" + date_context(),
                f"Previous conversation:\n{context}\nCurrent request: {text}",
            ),
            timeout=timeout_s,
        )
    except (TimeoutError, LLMError):
        reply = None
    route = reply.get("route") if reply else None
    if route == "ask":
        decision = RequestRoute("ask")
    elif route == "agent":
        decision = RequestRoute("ask" if mode == "ask" else "agent")
    else:
        # A failed classifier must not silently answer world facts from stale model memory.
        decision = _research(
            text,
            history,
            query=reply.get("query", "") if reply and isinstance(reply.get("query"), str) else "",
            recent=bool(reply and reply.get("recent") is True),
        )
    if len(_CACHE) >= 128:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = decision
    return decision
