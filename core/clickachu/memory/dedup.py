"""Dedup on insert (PLAN §14): same-scope FTS hit above a threshold → one cheap LLM verdict.

bm25 magnitudes depend on corpus size (near zero in a small store), so FTS only nominates
candidates; "above threshold" is the share of the shorter text's content words they have in common.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from clickachu.api.protocol import Memory, MemoryCreate
from clickachu.llm.json_ask import AskJson
from clickachu.llm.provider import LLMError
from clickachu.memory.store import MemoryStore, tokens

log = logging.getLogger(__name__)

Verdict = Literal["same", "update", "new"]
# (existing memory, new content) -> verdict. Injectable so tests need no LLM.
Judge = Callable[[Memory, str], Awaitable[Verdict]]

OVERLAP_THRESHOLD = 0.34
CANDIDATES = 3

JUDGE_SYSTEM = """\
You compare two notes about a user. Reply with JSON only: {"verdict": "same" | "update" | "new"}.
- "same": the new note says the same thing as the existing one.
- "update": the new note replaces or contradicts the existing one about the same subject \
(e.g. a changed preference).
- "new": it is a different fact; both should be kept."""


@dataclass(frozen=True)
class SaveOutcome:
    memory: Memory
    # created: new row · duplicate: nothing written · superseded: new row replaced `replaced`
    action: Literal["created", "duplicate", "superseded"]
    replaced: Memory | None = None


def overlap(a: str, b: str) -> float:
    ta, tb = set(tokens(a)), set(tokens(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def find_candidate(store: MemoryStore, data: MemoryCreate) -> Memory | None:
    hits = store.search(
        data.content,
        scope_type=data.scope_type,
        scope_key="" if data.scope_type == "global" else data.scope_key,
        limit=CANDIDATES,
    )
    scored = [(overlap(data.content, h.content), h) for h in hits]
    scored = [(s, h) for s, h in scored if s >= OVERLAP_THRESHOLD]
    return max(scored, key=lambda sh: sh[0], default=(0.0, None))[1]


def llm_judge(ask: AskJson) -> Judge:
    async def judge(existing: Memory, new_content: str) -> Verdict:
        user = f"Existing note: {existing.content}\nNew note: {new_content}"
        try:
            reply = await ask(JUDGE_SYSTEM, user)
        except LLMError as exc:
            log.warning("dedup judge failed, keeping both: %s", exc)
            return "new"
        verdict = reply.get("verdict") if reply else None
        return verdict if verdict in ("same", "update", "new") else "new"

    return judge


async def save_with_dedup(
    store: MemoryStore,
    judge: Judge,
    data: MemoryCreate,
    *,
    source_message_id: int | None = None,
) -> SaveOutcome:
    candidate = find_candidate(store, data)
    verdict: Verdict = "new" if candidate is None else await judge(candidate, data.content)
    if candidate is not None and verdict == "same":
        return SaveOutcome(candidate, "duplicate")
    created = store.create(data, source_message_id=source_message_id)
    if candidate is not None and verdict == "update":
        store.supersede(candidate.id, created.id)
        return SaveOutcome(created, "superseded", replaced=candidate)
    return SaveOutcome(created, "created")
