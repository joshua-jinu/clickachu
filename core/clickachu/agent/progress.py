"""Detect unchanged read loops without mistaking an observation ID for progress."""

import json
from dataclasses import dataclass, field


def stable_result(value):
    if isinstance(value, dict):
        return {
            key: stable_result(item)
            for key, item in value.items()
            if key not in ("observation_id", "captured_at", "timestamp")
        }
    if isinstance(value, list):
        return [stable_result(item) for item in value]
    return value


@dataclass
class ReadProgress:
    reads: dict[str, tuple[str, int]] = field(default_factory=dict)

    def observe(self, tool, args, result):
        if not result.ok:
            return 0
        if tool.tier.value != "read" and not (
            tool.name == "control_media" and args.action == "status"
        ):
            self.reads.clear()
            return 0
        signature = tool.name + ":" + args.model_dump_json()
        # Structured data has stable UI refs/values; the prose may include a random snapshot ID.
        # Some tools put the useful content in untrusted_text and only metadata in data
        # (browser_read is one example). Include both so changed page content counts as
        # progress even when the URL and interactive element count stay the same.
        payload = {
            "data": result.data,
            "text": result.untrusted_text or result.summary,
        }
        fingerprint = json.dumps(
            stable_result(payload), sort_keys=True, ensure_ascii=False, default=str
        )
        before, count = self.reads.get(signature, ("", 0))
        count = count + 1 if before == fingerprint else 1
        self.reads[signature] = (fingerprint, count)
        return count
