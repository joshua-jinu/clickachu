"""Compact UI observations for inference; Activity retains the complete tool output."""


def ui_context(result, include_observation_id=True):
    data = result.data
    if not result.ok or not isinstance(data, dict) or not data.get("entries"):
        return result.llm_text()
    lines = ["Live UI controls:"]
    previous = None
    for entry in sorted(data["entries"], key=lambda e: (e["ref"][0], int(e["ref"][1:]))):
        context = entry.get("context", "")
        if context != previous:
            if context:
                lines.append(f"Group: {context}")
            previous = context
        lines.append(
            f"{entry['ref']} [{entry['type']}] {entry['text']}"
            + (
                f"; value={entry['value']}"
                if entry.get("value") and entry["value"] != entry["text"]
                else ""
            )
            + (" (disabled)" if not entry.get("enabled", True) else "")
        )
    lines.extend([f"Window: {data.get('title', '')}", result.summary])
    if include_observation_id:
        lines.append(f"observation_id={data.get('observation_id', '')}")
    if data.get("limited"):
        lines.append(
            "Partial sample; inspect a narrower query/deeper view if the target is absent."
        )
    return "\n".join(lines)
