"""Database bootstrap and the settings table (PLAN §8)."""

import threading
from pathlib import Path

import pytest

from clickachu.api.protocol import Settings, SettingsUpdate
from clickachu.db.conn import SCHEMA_VERSION, Database, SchemaTooNewError
from clickachu.settings import SettingsStore


def test_schema_applies_idempotently(tmp_path: Path) -> None:
    path = tmp_path / "clickachu.db"
    for _ in range(3):
        db = Database(path)
        rows = db.conn().execute("SELECT version FROM schema_version").fetchall()
        assert [r["version"] for r in rows] == [SCHEMA_VERSION]
        db.close()
    assert path.exists()


def test_expected_tables_and_wal(db: Database) -> None:
    conn = db.conn()
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert {
        "settings",
        "allowed_roots",
        "projects",
        "conversations",
        "messages",
        "memories",
        "memories_fts",
        "action_log",
        "trusted_actions",
        "suggestions",
        "schema_version",
        "memories_ai",
        "memories_ad",
        "memories_au",
    } <= names
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_newer_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "clickachu.db"
    db = Database(path)
    db.conn().execute("UPDATE schema_version SET version = 99")
    db.conn().commit()
    db.close()
    with pytest.raises(SchemaTooNewError):
        Database(path).conn()


def test_one_connection_per_thread(db: Database) -> None:
    main = db.conn()
    assert db.conn() is main
    seen: list[object] = []
    thread = threading.Thread(target=lambda: seen.append(db.conn()))
    thread.start()
    thread.join()
    assert seen[0] is not main


def test_settings_roundtrip_and_restart(tmp_path: Path) -> None:
    path = tmp_path / "clickachu.db"
    db = Database(path)
    store = SettingsStore(db)
    assert store.get() == Settings()
    store.update(SettingsUpdate(ask_model="a/b", memory_enabled=False))
    db.close()

    again = SettingsStore(Database(path)).get()
    assert again.ask_model == "a/b"
    assert again.memory_enabled is False


def test_legacy_json_migrates_once(tmp_path: Path, db: Database) -> None:
    legacy = tmp_path / "settings.json"
    legacy.write_text(
        Settings(ask_model="old/model", translate_language="French").model_dump_json()
    )
    store = SettingsStore(db)

    assert store.import_legacy_json(legacy) is True
    assert store.get().ask_model == "old/model"
    assert store.get().translate_language == "French"
    assert not legacy.exists()
    assert legacy.with_name("settings.json.migrated").exists()

    store.update(SettingsUpdate(ask_model="new/model"))
    legacy.write_text(Settings(ask_model="stale/model").model_dump_json())
    assert store.import_legacy_json(legacy) is False
    assert store.get().ask_model == "new/model"


def test_corrupt_legacy_json_is_skipped(tmp_path: Path, db: Database) -> None:
    legacy = tmp_path / "settings.json"
    legacy.write_text("{not json")
    store = SettingsStore(db)
    assert store.import_legacy_json(legacy) is False
    assert store.get() == Settings()


def test_openrouter_settings_migrate_to_gemini_once(db: Database) -> None:
    store = SettingsStore(db)
    conn = db.conn()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('base_url', ?)",
            ('"https://openrouter.ai/api/v1"',),
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('ask_model', ?)",
            ('"openrouter/free"',),
        )
    loaded = store.get()
    assert loaded.base_url == "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert loaded.router_model == "gemini-2.5-flash-lite"
    assert loaded.ask_model == "gemini-2.5-flash"
    assert loaded.agent_model == "gemini-2.5-flash"
