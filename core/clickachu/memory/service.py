"""Local personal memory: automatic clear facts, explicit notes, and bounded recall."""

import logging

from clickachu.api.protocol import Memory
from clickachu.llm.json_ask import AskJson, make_ask_json
from clickachu.llm.provider import LLMError, LLMProvider
from clickachu.memory.automatic import (
    asks_about_user,
    memory_slot,
    normalized,
    personal_facts,
    queried_slots,
)
from clickachu.memory.dedup import SaveOutcome, llm_judge
from clickachu.memory.remember import remember_from_message
from clickachu.memory.store import MemoryRefused, MemoryStore
from clickachu.settings import SettingsStore

log = logging.getLogger(__name__)

RECALL_LIMIT = 8


class MemoryService:
    def __init__(self, store: MemoryStore, settings: SettingsStore) -> None:
        self.store = store
        self._settings = settings

    @property
    def enabled(self) -> bool:
        return self._settings.get().memory_enabled

    def observe_user_message(self, user_text: str) -> list[SaveOutcome]:
        """Save obvious durable personal details without inference or source-text ingestion."""
        if not self.enabled:
            return []
        outcomes = []
        for fact in personal_facts(user_text):
            existing = self.store.list(scope_type="global", include_disabled=True)
            duplicate = next(
                (m for m in existing if normalized(m.content) == normalized(fact.data.content)),
                None,
            )
            if duplicate:
                # An explicitly disabled memory stays disabled.
                continue
            prior = next(
                (
                    m
                    for m in existing
                    if m.enabled and fact.slot and memory_slot(m.content) == fact.slot
                ),
                None,
            )
            try:
                data = fact.data.model_copy(update={"pinned": prior.pinned}) if prior else fact.data
                created = self.store.create(data)
            except MemoryRefused:
                continue
            if prior:
                self.store.supersede(prior.id, created.id)
            outcomes.append(SaveOutcome(created, "superseded" if prior else "created", prior))
        return outcomes

    def recall(
        self,
        text: str,
        *,
        project: str | None = None,
        app: str | None = None,
        process: str | None = None,
        limit: int = RECALL_LIMIT,
        fill_recent: bool = True,
    ) -> list[Memory]:
        """Pinned + FTS top-8 over text/project/app, in scope; marks them used. Empty if off.

        When fewer than 8 match, the most recently updated in-scope memories fill the rest: FTS
        can't link "install deps" to "prefers pnpm", and a small store fits the prompt anyway.
        """
        if not self.enabled:
            return []
        query = " ".join(p for p in (text, project, app, process) if p)
        chosen: dict[int, Memory] = {
            m.id: m for m in self.store.pinned(project=project, app=process)
        }
        # FTS cannot connect "install dependencies" with "prefers pnpm". Carry a small
        # preference budget even in the local short prompt, while leaving room for task hits.
        recent = self.store.recent(project=project, app=process, limit=50)
        personal_query = asks_about_user(text)
        if personal_query:
            # Personal facts can be older than many project/task notes. Fetch by scope,
            # then prioritize the requested field before spending the small prompt budget.
            profile = self.store.list(scope_type="global", include_disabled=False)
            wanted = queried_slots(text)
            for m in profile:
                if memory_slot(m.content) in wanted:
                    chosen.setdefault(m.id, m)
            recent = profile
        defaults = [
            m
            for m in recent
            if m.category in ("preference", "instruction")
            or (personal_query and memory_slot(m.content))
        ]
        for m in defaults[: max(1, limit - 1)]:
            chosen.setdefault(m.id, m)
        ranked = self.store.search(query, project=project, app=process, limit=limit)
        for m in ranked:
            chosen.setdefault(m.id, m)
        if fill_recent:
            for m in self.store.recent(project=project, app=process, limit=limit):
                if len([c for c in chosen.values() if not c.pinned]) >= limit:
                    break
                chosen.setdefault(m.id, m)
        memories = list(chosen.values())[:limit]
        self.store.mark_used([m.id for m in memories])
        return memories

    async def remember(
        self,
        provider: LLMProvider,
        *,
        ask_model: str,
        router_model: str,
        user_text: str,
        project: str | None = None,
        process: str | None = None,
        app: str | None = None,
    ) -> SaveOutcome | None:
        """Save what the user asked to be remembered; None when off, empty, refused or on error."""
        if not self.enabled:
            return None
        extract: AskJson = make_ask_json(provider, ask_model)
        judge = llm_judge(make_ask_json(provider, router_model or ask_model, max_tokens=60))
        try:
            return await remember_from_message(
                self.store, extract, judge, user_text, project=project, process=process, app=app
            )
        except LLMError as exc:
            log.warning("remember failed: %s", exc)
            return None
