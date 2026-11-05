"""Durable task evidence; resuming never replays already executed actions."""

import json
from datetime import UTC, datetime
from typing import Any

from clickachu.db.conn import Database


class TaskStore:
    def __init__(self, db: Database):
        self.db = db

    def save(self, conversation_id: str, run_id: str, state: dict[str, Any]) -> None:
        conn = self.db.conn()
        with conn:
            conn.execute(
                "INSERT INTO agent_tasks(conversation_id, run_id, state_json, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(conversation_id) DO UPDATE SET "
                "run_id=excluded.run_id, state_json=excluded.state_json, "
                "updated_at=excluded.updated_at",
                (conversation_id, run_id, json.dumps(state), datetime.now(UTC).isoformat()),
            )

    def get(self, conversation_id: str) -> dict[str, Any] | None:
        row = (
            self.db.conn()
            .execute(
                "SELECT state_json FROM agent_tasks WHERE conversation_id=?", (conversation_id,)
            )
            .fetchone()
        )
        return json.loads(row["state_json"]) if row else None


def compact_messages(messages: list[dict], keep_exchanges: int = 3) -> list[dict]:
    """Drop whole completed exchanges, retaining system context and the original request.

    Never cut between an assistant tool call and its result. Callers retain durable evidence
    separately, so compacting execution transcripts does not discard the task's progress.
    """
    boundaries = [
        i
        for i, message in enumerate(messages)
        if message.get("role") == "assistant" and message.get("tool_calls")
    ]
    if len(boundaries) <= keep_exchanges:
        return messages
    first = boundaries[0]
    return messages[:first] + messages[boundaries[-keep_exchanges] :]
