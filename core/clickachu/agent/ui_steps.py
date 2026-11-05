"""Constrain a UI decision to an already observed, unambiguous next operation.

The model still chooses the control and input. No app-specific selectors or coordinates are used.
When evidence is insufficient, retain the normal inspect/act/wait tool set.
"""

import re
from copy import deepcopy
from dataclasses import dataclass

from clickachu.agent.goals import media_name_matches, requested_destination


@dataclass(frozen=True)
class UIStep:
    schemas: list[dict]
    instruction: str
    automatic_arguments: dict | None = None


def navigation_destination(text):
    return requested_destination(text)


def constrain_ui_step(
    schemas, data, *, goal=None, request="", search_submitted=False, used_refs=()
):
    if not data or not data.get("entries"):
        return None
    desktop = next((s for s in schemas if s["function"]["name"] == "desktop_action"), None)
    if desktop is None:
        return None
    props = desktop["function"]["parameters"]["properties"]
    if len(props.get("observation_id", {}).get("enum", [])) != 1:
        return None
    entries = [e for e in data["entries"] if e.get("enabled", True)]
    content, artist = (
        (goal.content, goal.artist) if goal and goal.kind == "media_selection" else ("", "")
    )
    destination = navigation_destination(request) if not content else ""
    if not content and not destination:
        return None
    refs, operation, instruction = [], "", ""
    if destination and search_submitted:
        refs = [
            e["ref"]
            for e in entries
            if e["type"] == "Hyperlink"
            and media_name_matches(destination, e.get("text", "") + " " + e.get("context", ""))
        ]
        if refs:
            operation = "click"
            instruction = "The requested destination link is visible. Open that matching link."
    if content:
        matches = [
            e
            for e in entries
            if e["type"] in ("Button", "Hyperlink", "DataItem", "ListItem")
            and e["ref"] not in used_refs
            and media_name_matches(content, e.get("text", "") + " " + e.get("context", ""))
            and (
                not artist
                or media_name_matches(
                    artist, e.get("text", "") + " " + e.get("context", ""), ignore_article=True
                )
            )
        ]
        play = [
            e for e in matches if e["type"] == "Button" and re.search(r"\bplay\b", e["text"], re.I)
        ]
        if matches and (search_submitted or play):
            refs = [e["ref"] for e in play or matches]
            operation = "click"
            instruction = (
                "The requested result is visible. Click its observed matching result/play control."
            )
    if not refs and not search_submitted:
        edits = [
            e
            for e in entries
            if e["type"] in ("Edit", "ComboBox")
            and re.search(
                r"\b(?:search|find|look for|what do you want to play|address)\b",
                e.get("text", "") + " " + e.get("context", ""),
                re.I,
            )
        ]
        # A native app's embedded browser address is not its content search box.
        if content:
            edits = [
                e for e in edits if not re.search(r"\baddress\b|https?://|xpui\.", e["text"], re.I)
            ]
        elif destination:
            edits = [
                e
                for e in edits
                if re.search(r"\baddress\b", e.get("text", "") + " " + e.get("context", ""), re.I)
            ]
        if edits:
            refs, operation = [e["ref"] for e in edits], "fill"
            query = " ".join(p for p in (content, artist) if p) if content else destination
            instruction = (
                f"The needed editable control is visible. Fill it with {query!r}"
                + (" as a search query" if content else " as the requested website/address")
                + ", submit=true, and wait for the resulting UI. Do not inspect or reopen the app."
            )
    if not refs:
        return None
    restricted = deepcopy(desktop)
    parameters = restricted["function"]["parameters"]
    props = parameters["properties"]
    actions = [operation]
    if (
        content
        and operation == "click"
        and any(e["ref"] in refs and e["type"] in ("DataItem", "ListItem") for e in entries)
    ):
        actions.append("double_click")
    props["action"]["enum"] = actions
    props["ref"]["enum"] = refs
    if operation == "fill":
        props["submit"] = {"type": "boolean", "const": True}
        parameters["required"] = list(dict.fromkeys([*parameters.get("required", []), "submit"]))
    choices = [restricted]
    if (
        destination
        and not search_submitted
        and re.search(r" (?:and|then) open ", request, re.I)
        and not re.match(r"https?://|(?:[\w-]+\.)+[A-Za-z]{2,}(?:/|$)", destination, re.I)
    ):
        # "open A and open B" can name a second native app rather than a website.
        opener = next((s for s in schemas if s["function"]["name"] == "open_app"), None)
        if opener:
            opener = deepcopy(opener)
            opener["function"]["parameters"]["properties"]["name"]["enum"] = [destination]
            choices.append(opener)
    automatic = None
    if len(refs) == 1 and len(choices) == 1:
        automatic = {
            "observation_id": props["observation_id"]["enum"][0],
            "action": operation,
            "ref": refs[0],
        }
        if operation == "fill":
            automatic.update(text=query, submit=True)
            if content:
                automatic.update(wait_for_text=content)
        elif content and not any(
            e["ref"] == refs[0]
            and e["type"] == "Button"
            and re.search(r"\bplay\b", e["text"], re.I)
            for e in entries
        ):
            automatic = None  # Opening/selecting a row is not necessarily playing it.
        elif destination:
            # Avoid automatically selecting a similarly named but different website.
            target = next(e for e in entries if e["ref"] == refs[0])
            if target.get("text", "").strip().casefold() != destination.casefold():
                automatic = None
    return UIStep(choices, instruction, automatic)
