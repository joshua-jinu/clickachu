"""Dedup on insert and explicit-remember extraction, with a fake LLM (PLAN §14)."""

from typing import Any

import pytest

from clickachu.api.protocol import Memory, MemoryCreate
from clickachu.db.conn import Database
from clickachu.llm.provider import LLMError
from clickachu.memory.dedup import Verdict, llm_judge, overlap, save_with_dedup
from clickachu.memory.remember import extract_memory, is_remember_request, remember_from_message
from clickachu.memory.store import MemoryStore


class FakeJudge:
    def __init__(self, verdict: Verdict) -> None:
        self.verdict = verdict
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, existing: Memory, new_content: str) -> Verdict:
        self.calls.append((existing.content, new_content))
        return self.verdict


@pytest.fixture
def store(db: Database) -> MemoryStore:
    return MemoryStore(db)


def data(content: str, scope_type: str = "global", scope_key: str = "") -> MemoryCreate:
    return MemoryCreate(content=content, scope_type=scope_type, scope_key=scope_key)  # type: ignore[arg-type]


async def test_no_candidate_means_no_llm_call(store: MemoryStore) -> None:
    store.create(data("My favourite colour is green"))
    judge = FakeJudge("same")
    out = await save_with_dedup(store, judge, data("I prefer pnpm over npm"))
    assert out.action == "created" and judge.calls == []


async def test_same_returns_existing_without_writing(store: MemoryStore) -> None:
    first = store.create(data("I prefer pnpm over npm"))
    judge = FakeJudge("same")
    out = await save_with_dedup(store, judge, data("Prefer pnpm rather than npm"))
    assert out.action == "duplicate" and out.memory.id == first.id
    assert len(store.list()) == 1
    assert judge.calls == [("I prefer pnpm over npm", "Prefer pnpm rather than npm")]


async def test_update_supersedes_old(store: MemoryStore) -> None:
    old = store.create(data("I prefer npm over yarn"))
    out = await save_with_dedup(store, FakeJudge("update"), data("I prefer pnpm over yarn"))
    assert out.action == "superseded" and out.replaced is not None
    assert out.replaced.id == old.id
    assert store.get(old.id).superseded_by == out.memory.id
    assert [m.id for m in store.search("prefer yarn")] == [out.memory.id]


async def test_new_keeps_both(store: MemoryStore) -> None:
    store.create(data("I prefer pnpm over npm"))
    out = await save_with_dedup(store, FakeJudge("new"), data("I prefer dark mode over light"))
    assert out.action == "created"
    assert len(store.list()) == 2


async def test_dedup_is_same_scope_only(store: MemoryStore) -> None:
    store.create(data("I prefer pnpm over npm"))
    judge = FakeJudge("same")
    out = await save_with_dedup(store, judge, data("I prefer pnpm over npm", "project", "TeamFuse"))
    assert out.action == "created" and judge.calls == []


def test_overlap_measure() -> None:
    assert overlap("prefer pnpm over npm", "prefer pnpm over npm") == 1.0
    assert overlap("prefer pnpm", "colour green") == 0.0
    assert overlap("", "anything") == 0.0


async def test_llm_judge_parses_and_falls_back() -> None:
    existing = Memory.model_construct(content="x")

    async def says(verdict: object) -> Any:
        return {"verdict": verdict}

    async def ask_update(_s: str, _u: str) -> dict[str, Any] | None:
        return await says("update")

    async def ask_garbage(_s: str, _u: str) -> dict[str, Any] | None:
        return {"verdict": "maybe"}

    async def ask_none(_s: str, _u: str) -> dict[str, Any] | None:
        return None

    async def ask_error(_s: str, _u: str) -> dict[str, Any] | None:
        raise LLMError("boom")

    assert await llm_judge(ask_update)(existing, "y") == "update"
    assert await llm_judge(ask_garbage)(existing, "y") == "new"
    assert await llm_judge(ask_none)(existing, "y") == "new"
    assert await llm_judge(ask_error)(existing, "y") == "new"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Remember that I prefer pnpm over npm", True),
        ("  remember: tabs not spaces", True),
        ("please remember my editor is VS Code", True),
        ("From now on answer in French", True),
        ("always use pnpm", True),
        ("How should I install deps?", False),
        ("Can you remember what I said?", False),
        ("Rememberance of things past", False),
        ("Summarise this. Always be brief.", False),
    ],
)
def test_is_remember_request(text: str, expected: bool) -> None:
    assert is_remember_request(text) is expected


def asker(reply: dict[str, Any] | None) -> Any:
    seen: list[tuple[str, str]] = []

    async def ask(system: str, user: str) -> dict[str, Any] | None:
        seen.append((system, user))
        return reply

    ask.seen = seen  # type: ignore[attr-defined]
    return ask


async def test_extract_uses_only_the_user_message() -> None:
    ask = asker({"content": "Prefers pnpm over npm.", "category": "preference", "scope": "global"})
    got = await extract_memory(ask, "Remember I prefer pnpm", project=None, process=None, app=None)
    assert got is not None
    assert (got.content, got.category, got.scope_type) == (
        "Prefers pnpm over npm.",
        "preference",
        "global",
    )
    assert ask.seen[0][1].endswith("User message: Remember I prefer pnpm")


async def test_extract_scope_comes_from_context_not_model() -> None:
    reply = {"content": "Deploys on Fridays", "category": "fact", "scope": "project"}
    got = await extract_memory(
        asker(reply),
        "remember we deploy Fridays",
        project="TeamFuse",
        process="Code.exe",
        app="VS Code",
    )
    assert got is not None and (got.scope_type, got.scope_key) == ("project", "TeamFuse")

    # Model asks for project scope but no project is open → global, never an invented key.
    got = await extract_memory(
        asker(reply), "remember we deploy Fridays", project=None, process=None, app=None
    )
    assert got is not None and (got.scope_type, got.scope_key) == ("global", "")

    app_reply = {"content": "Uses vim keys", "category": "preference", "scope": "app"}
    got = await extract_memory(
        asker(app_reply),
        "remember I use vim keys here",
        project=None,
        process="Code.exe",
        app="VS Code",
    )
    assert got is not None and (got.scope_type, got.scope_key) == ("app", "Code.exe")


@pytest.mark.parametrize("reply", [None, {"content": ""}, {"content": 5}, {"category": "fact"}])
async def test_extract_nothing(reply: dict[str, Any] | None) -> None:
    assert (
        await extract_memory(asker(reply), "always", project=None, process=None, app=None) is None
    )


async def test_extract_defaults_bad_category() -> None:
    got = await extract_memory(
        asker({"content": "x y", "category": "weird"}),
        "remember x",
        project=None,
        process=None,
        app=None,
    )
    assert got is not None and got.category == "fact"


async def test_remember_from_message_saves_and_refuses_secrets(store: MemoryStore) -> None:
    ok = await remember_from_message(
        store,
        asker({"content": "Prefers pnpm over npm", "category": "preference"}),
        FakeJudge("new"),
        "remember I prefer pnpm",
    )
    assert ok is not None and ok.action == "created"

    leaked = await remember_from_message(
        store,
        asker({"content": "API key is sk-abc123DEF456ghi789JKL012mno345", "category": "fact"}),
        FakeJudge("new"),
        "remember my api key",
    )
    assert leaked is None
    assert [m.content for m in store.list()] == ["Prefers pnpm over npm"]
