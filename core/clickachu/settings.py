"""Settings store (PLAN §8): key/value rows in the `settings` table, values JSON-encoded."""

import json
import logging
import threading
from pathlib import Path

from pydantic import ValidationError

from clickachu.api.protocol import (
    DEFAULT_AGENT_MODEL,
    DEFAULT_ASK_MODEL,
    DEFAULT_GEMINI_BASE_URL,
    DEFAULT_ROUTER_MODEL,
    Settings,
    SettingsUpdate,
)
from clickachu.db.conn import Database

log = logging.getLogger(__name__)

# Marks the one-time import of the pre-SQLite settings.json (earlier phases).
MIGRATED_KEY = "_migrated_settings_json"
MIGRATED_GEMINI_KEY = "_migrated_gemini_defaults"
LEGACY_OPENROUTER_URL = "https://openrouter.ai/api/v1"


class SettingsStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._lock = threading.Lock()

    def get(self) -> Settings:
        with self._lock:
            return self._load()

    def update(self, patch: SettingsUpdate) -> Settings:
        with self._lock:
            merged = Settings.model_validate(
                {
                    **self._load().model_dump(),
                    **patch.model_dump(exclude_none=True),
                }
            )
            self._write(merged)
            return merged

    def import_legacy_json(self, path: Path) -> bool:
        """Copy the old settings.json into the table once; True if something was imported."""
        with self._lock:
            conn = self.db.conn()
            if conn.execute("SELECT 1 FROM settings WHERE key = ?", (MIGRATED_KEY,)).fetchone():
                return False
            if not path.exists():
                return False
            imported = False
            try:
                legacy = Settings.model_validate_json(path.read_text(encoding="utf-8"))
            except (ValidationError, OSError) as exc:
                log.error("unreadable legacy settings %s, skipping migration: %s", path, exc)
            else:
                self._write(legacy)
                imported = True
                log.info("migrated settings from %s", path)
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (MIGRATED_KEY, json.dumps(True)),
                )
            try:
                path.replace(path.with_name(path.name + ".migrated"))
            except OSError as exc:
                log.warning("could not rename %s after migration: %s", path, exc)
            return imported

    def _load(self) -> Settings:
        conn = self.db.conn()
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        raw: dict[str, object] = {}
        stored_keys = {row["key"] for row in rows}
        for row in rows:
            if row["key"] not in Settings.model_fields:
                continue
            try:
                raw[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                log.error("ignoring corrupt setting %r", row["key"])
        if (
            MIGRATED_GEMINI_KEY not in stored_keys
            and str(raw.get("base_url", "")).rstrip("/") == LEGACY_OPENROUTER_URL
        ):
            raw["base_url"] = DEFAULT_GEMINI_BASE_URL
            raw["router_model"] = DEFAULT_ROUTER_MODEL
            raw["ask_model"] = DEFAULT_ASK_MODEL
            raw["agent_model"] = DEFAULT_AGENT_MODEL
            try:
                migrated = Settings.model_validate(raw)
                self._write(migrated)
                with conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                        (MIGRATED_GEMINI_KEY, json.dumps(True)),
                    )
                log.info("migrated settings from OpenRouter to Gemini defaults")
                return migrated
            except ValidationError:
                pass
        try:
            return Settings.model_validate(raw)
        except ValidationError as exc:
            log.error("invalid stored settings, using defaults: %s", exc)
            return Settings()

    def _write(self, settings: Settings) -> None:
        conn = self.db.conn()
        with conn:
            conn.executemany(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                [(k, json.dumps(v)) for k, v in settings.model_dump().items()],
            )
