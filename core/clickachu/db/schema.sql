-- PLAN §8. Applied idempotently on every start (CREATE ... IF NOT EXISTS).
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS allowed_roots (
  id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, can_write INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, path TEXT NOT NULL,
  commands_json TEXT NOT NULL,          -- [{name, cmd, cwd, port?, trusted}]
  env_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL, approved_at TEXT);

CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, started_at TEXT NOT NULL, context_json TEXT);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY,
  scope_type TEXT NOT NULL CHECK (scope_type IN ('global','project','app')),
  scope_key TEXT NOT NULL DEFAULT '',   -- project name / process name / ''
  category TEXT NOT NULL CHECK (category IN ('preference','instruction','fact')),
  content TEXT NOT NULL,
  source_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
  pinned INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  use_count INTEGER NOT NULL DEFAULT 0,
  superseded_by INTEGER REFERENCES memories(id),
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_used_at TEXT);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
  content, scope_key, content='memories', content_rowid='id');

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, content, scope_key) VALUES (new.id, new.content, new.scope_key);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, content, scope_key)
    VALUES ('delete', old.id, old.content, old.scope_key);
END;

-- Only content/scope_key are indexed; skip re-indexing for pin/use_count/last_used_at updates.
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE OF content, scope_key ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, content, scope_key)
    VALUES ('delete', old.id, old.content, old.scope_key);
  INSERT INTO memories_fts(rowid, content, scope_key) VALUES (new.id, new.content, new.scope_key);
END;

CREATE TABLE IF NOT EXISTS action_log (
  id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, step_id TEXT NOT NULL,
  tool TEXT NOT NULL, args_json TEXT NOT NULL, tier TEXT NOT NULL,
  decision TEXT NOT NULL,               -- auto | approved | always | denied | blocked
  ok INTEGER, result_summary TEXT, verified INTEGER,
  started_at TEXT NOT NULL, duration_ms INTEGER);

CREATE TABLE IF NOT EXISTS trusted_actions (
  id INTEGER PRIMARY KEY, tool TEXT NOT NULL, arg_key TEXT NOT NULL,  -- e.g. "TeamFuse:dev"
  created_at TEXT NOT NULL, UNIQUE(tool, arg_key));

CREATE TABLE IF NOT EXISTS suggestions (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, signature TEXT NOT NULL,
  title TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
