"""Explicit "remember …" (PLAN §14): extract a memory from the USER's own message and save it.

Hard rule 4: only the user's typed message is ever passed here — never selections, files or
tool output. Reused by the agent `remember` tool in P6.
"""

import logging
import re

from clickachu.api.protocol import MemoryCreate
from clickachu.llm.json_ask import AskJson
from clickachu.memory.dedup import Judge, SaveOutcome, save_with_dedup
from clickachu.memory.store import MemoryRefused, MemoryStore, tokens

log = logging.getLogger(__name__)

# Generic framing words that don't prove grounding on their own.
_FRAMING_WORDS = frozenset(
    "always assistant fact future instruction like likes must never note prefer preference "
    "prefers remember should user uses want wants".split()
)


def is_grounded_in_user_message(content: str, user_text: str) -> bool:
    """True when `content` is grounded in the user's own typed message (Hard Rule 4, §14)."""
    if not content.strip() or not user_text.strip():
        return False
    user_lower = user_text.lower()
    user_toks = set(tokens(user_text))
    content_toks = [t for t in tokens(content) if t not in _FRAMING_WORDS]
    if not content_toks:
        return False
    matched = sum(
        1
        for tok in content_toks
        if tok in user_toks
        or tok in user_lower
        or (len(tok) >= 4 and tok[:4] in user_lower)
    )
    # Require at least one match and at least half of substantive content words grounded.
    return matched >= 1 and (matched * 2 >= len(content_toks))

_TRIGGER = re.compile(
    r"^\s*(?:(?:please|hey|ok(?:ay)?)[,\s]+)?(remember\b|from\s+now\s+on\b|always\b)",
    re.IGNORECASE,
)

EXTRACT_SYSTEM = """\
You turn a user's message into one durable note for a personal assistant to remember.
Reply with JSON only:
{"content": str, "category": "preference" | "instruction" | "fact",
 "scope": "global" | "project" | "app"}
- content: one short self-contained sentence in the third person or imperative, using only what \
the user said. Return {"content": ""} if there is nothing durable to remember.
- category: preference = likes/dislikes/habits; instruction = how the assistant should behave; \
fact = information about the user, their setup or their projects.
- scope: "project" only if the user ties it to the current project, "app" only if they tie it \
to the current app, otherwise "global"."""


def is_remember_request(text: str) -> bool:
    return _TRIGGER.match(text) is not None


async def extract_memory(
    ask: AskJson, user_text: str, *, project: str | None, process: str | None, app: str | None
) -> MemoryCreate | None:
    """Ask for {content, category, scope}; scope keys come from the context, not the model."""
    lines = []
    if project:
        lines.append(f"Current project: {project}")
    if app:
        lines.append(f"Current app: {app}")
    lines.append(f"User message: {user_text}")
    reply = await ask(EXTRACT_SYSTEM, "\n".join(lines))
    if not reply:
        return None
    content = reply.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    if not is_grounded_in_user_message(content, user_text):
        return None
    category = reply.get("category")
    if category not in ("preference", "instruction", "fact"):
        category = "fact"
    scope_type, scope_key = "global", ""
    if reply.get("scope") == "project" and project:
        scope_type, scope_key = "project", project
    elif reply.get("scope") == "app" and process:
        scope_type, scope_key = "app", process
    return MemoryCreate(
        content=content.strip()[:2000],
        category=category,
        scope_type=scope_type,
        scope_key=scope_key,
    )


async def remember_from_message(
    store: MemoryStore,
    ask: AskJson,
    judge: Judge,
    user_text: str,
    *,
    project: str | None = None,
    process: str | None = None,
    app: str | None = None,
) -> SaveOutcome | None:
    """Extract and save with dedup; None if nothing to remember or the content was refused."""
    data = await extract_memory(ask, user_text, project=project, process=process, app=app)
    if data is None:
        return None
    try:
        return await save_with_dedup(store, judge, data)
    except MemoryRefused as exc:
        log.info("not remembering: %s", exc)
        return None
