"""Memory store: CRUD, FTS ranking, scope filters, supersede, redaction refusal, JSON IO (§14)."""

from pathlib import Path
from typing import Any

import pytest

from clickachu.api.protocol import MemoryCreate, MemoryExport, MemoryUpdate
from clickachu.db.conn import Database
from clickachu.memory.store import MemoryNotFound, MemoryRefused, MemoryStore, fts_query

KEY = "sk-abc123DEF456ghi789JKL012mno345"


@pytest.fixture
def store(db: Database) -> MemoryStore:
    return MemoryStore(db)


def mem(content: str, scope_type: str = "global", scope_key: str = "", **kw: Any) -> MemoryCreate:
    return MemoryCreate(content=content, scope_type=scope_type, scope_key=scope_key, **kw)


def test_crud(store: MemoryStore) -> None:
    created = store.create(mem("I prefer pnpm over npm", category="preference"))
    assert created.id and created.enabled and not created.pinned and created.use_count == 0
    assert store.get(created.id).content == "I prefer pnpm over npm"

    updated = store.update(created.id, MemoryUpdate(content="I prefer yarn", pinned=True))
    assert (updated.content, updated.pinned) == ("I prefer yarn", True)
    assert updated.updated_at >= created.updated_at

    assert [m.id for m in store.list()] == [created.id]
    store.delete(created.id)
    assert store.list() == []
    with pytest.raises(MemoryNotFound):
        store.get(created.id)


def test_fts_follows_edits_and_deletes(store: MemoryStore) -> None:
    m = store.create(mem("I prefer pnpm over npm"))
    assert [h.id for h in store.search("pnpm")] == [m.id]

    store.update(m.id, MemoryUpdate(content="I like yarn"))
    assert store.search("pnpm") == []
    assert [h.id for h in store.search("yarn")] == [m.id]

    # Flag-only updates must not disturb the index.
    store.update(m.id, MemoryUpdate(pinned=True))
    assert [h.id for h in store.search("yarn")] == [m.id]

    store.delete(m.id)
    assert store.search("yarn") == []


def test_fts_ranking_sanity(store: MemoryStore) -> None:
    store.create(mem("My favourite colour is green"))
    best = store.create(mem("Install dependencies with pnpm, never npm install"))
    store.create(mem("Use tabs for indentation in Makefiles"))
    store.create(mem("Dependencies are vendored in the monorepo"))

    hits = store.search("how do I install dependencies with pnpm?")
    assert hits[0].id == best.id
    assert all("green" not in h.content for h in hits)


def test_search_survives_fts_syntax_in_text(store: MemoryStore) -> None:
    store.create(mem("Never use AND NEAR operators"))
    assert fts_query('"" OR (') is None
    assert store.search('what is "NEAR" AND* (x) -y?')  # no OperationalError


def test_scope_filtering(store: MemoryStore) -> None:
    g = store.create(mem("deploys go through the build script"))
    p = store.create(mem("deploys use the staging build script", "project", "TeamFuse"))
    other = store.create(mem("deploys use the other build script", "project", "Other"))
    a = store.create(mem("deploys from the editor build script", "app", "Code.exe"))

    ids = {m.id for m in store.search("deploys build script", project="teamfuse", app="code.exe")}
    assert ids == {g.id, p.id, a.id}
    assert {m.id for m in store.search("deploys build script")} == {g.id}
    assert other.id not in ids

    exact = store.search("deploys build script", scope_type="project", scope_key="Other")
    assert [m.id for m in exact] == [other.id]
    assert [m.id for m in store.list(scope_type="app")] == [a.id]


def test_project_scope_requires_key(store: MemoryStore) -> None:
    with pytest.raises(MemoryRefused):
        store.create(mem("x lives here", "project", ""))
    assert store.create(mem("global ignores key", "global", "ignored")).scope_key == ""


def test_disabled_and_superseded_are_not_retrieved(store: MemoryStore) -> None:
    off = store.create(mem("alpha rule one"))
    old = store.create(mem("alpha rule two"))
    new = store.create(mem("alpha rule three"))
    store.update(off.id, MemoryUpdate(enabled=False))
    store.supersede(old.id, new.id)

    assert [m.id for m in store.search("alpha rule")] == [new.id]
    assert {m.id for m in store.search("alpha rule", include_disabled=True)} == {off.id, new.id}
    assert {m.id for m in store.list()} == {off.id, new.id}
    assert store.get(old.id).superseded_by == new.id
    assert store.recent() == [store.get(new.id)]


def test_deleting_replacement_restores_superseded(store: MemoryStore) -> None:
    old = store.create(mem("beta preference old"))
    new = store.create(mem("beta preference new"))
    store.supersede(old.id, new.id)
    store.delete(new.id)
    assert store.get(old.id).superseded_by is None
    assert [m.id for m in store.search("beta preference")] == [old.id]


def test_pinned_respects_scope(store: MemoryStore) -> None:
    g = store.create(mem("always be brief", pinned=True))
    p = store.create(mem("TeamFuse ships on Fridays", "project", "TeamFuse", pinned=True))
    store.create(mem("not pinned"))
    assert {m.id for m in store.pinned()} == {g.id}
    assert {m.id for m in store.pinned(project="TeamFuse")} == {g.id, p.id}


def test_mark_used(store: MemoryStore) -> None:
    m = store.create(mem("gamma"))
    store.mark_used([m.id, m.id])
    got = store.get(m.id)
    assert got.use_count == 2 and got.last_used_at is not None


@pytest.mark.parametrize(
    "content",
    [f"my key is {KEY}", "password: hunter2", "token=abc123", "Authorization: Bearer " + "a" * 30],
)
def test_secrets_are_refused(store: MemoryStore, content: str) -> None:
    with pytest.raises(MemoryRefused):
        store.create(mem(content))
    ok = store.create(mem("harmless"))
    with pytest.raises(MemoryRefused):
        store.update(ok.id, MemoryUpdate(content=content))
    assert store.get(ok.id).content == "harmless"
    assert [m.content for m in store.list()] == ["harmless"]


def test_export_import_roundtrip(db: Database, store: MemoryStore, tmp_path: Path) -> None:
    store.create(mem("one", pinned=True))
    store.create(mem("two", "project", "TeamFuse", category="instruction"))
    exported = store.export_json()
    assert [m.content for m in exported.memories] == ["one", "two"]

    other = MemoryStore(Database(tmp_path / "other.db"))
    result = other.import_json(MemoryExport.model_validate(exported.model_dump()))
    assert (result.imported, result.skipped) == (2, 0)
    assert [(m.content, m.pinned, m.scope_key) for m in other.list()] == [
        ("one", True, ""),
        ("two", False, "TeamFuse"),
    ]
    again = other.import_json(exported)
    assert (again.imported, again.skipped) == (0, 2)


def test_import_skips_secrets(store: MemoryStore) -> None:
    payload = MemoryExport.model_construct(version=1, memories=[mem("fine"), mem(f"leak {KEY}")])
    result = store.import_json(payload)
    assert (result.imported, result.skipped) == (1, 1)
