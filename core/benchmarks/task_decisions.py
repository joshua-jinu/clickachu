"""Local-model decision checks on synthetic UI. No returned action is ever executed."""

import asyncio
import json
import time
from copy import deepcopy

from clickachu.agent.goals import request_goal
from clickachu.agent.ui_steps import constrain_ui_step
from clickachu.llm.openrouter import OpenRouterProvider
from clickachu.llm.prompts import LOCAL_AGENT_SYSTEM, wrap_untrusted
from clickachu.llm.provider import ToolCall
from clickachu.llm.ui_context import ui_context
from clickachu.tools.base import ToolResult
from clickachu.tools.registry import create_default_registry


async def main():
    registry = create_default_registry()
    provider = OpenRouterProvider(
        base_url="http://127.0.0.1:11434/v1",
        api_key="",
        ollama_cpu_only=True,
        reasoning_effort="high",
    )
    for label, app, request, special, expected in (
        (
            "media_search",
            "Spotify",
            "play Riptide by The Chainsmokers on Spotify",
            [
                (21, "ComboBox", "What do you want to play?", "Search"),
                (44, "Button", "Play", "Now playing Pink Lips"),
            ],
            {"action": "fill", "ref": "c21"},
        ),
        (
            "media_result",
            "Spotify",
            "play Riptide by The Chainsmokers on Spotify",
            [
                (21, "ComboBox", "Riptide The Chainsmokers", "Search"),
                (22, "Text", "Riptide by The Chainsmokers", "Songs search results"),
                (23, "Button", "Play Riptide by The Chainsmokers", "Songs search results"),
                (44, "Button", "Play", "Now playing Pink Lips"),
            ],
            {"action": "click", "ref": "c23"},
        ),
        (
            "browser_navigation",
            "Chrome",
            "open Chrome and open YouTube",
            [
                (6, "Edit", "Address and search bar", "Chrome toolbar"),
                (7, "Document", "New Tab", "Chrome page"),
            ],
            {"action": "fill", "ref": "c6"},
        ),
        (
            'browser_result', 'Chrome', 'open Chrome and open YouTube', [
                (6, 'Edit', 'Address and search bar', 'Chrome toolbar'),
                (7, 'Document', 'YouTube - Google Search', 'Chrome page'),
                (12, 'Hyperlink', 'YouTube', 'Official website search result'),
            ], {'action': 'click', 'ref': 'c12'},
        ),
    ):
        entries = [
            {
                "ref": f"c{i}",
                "type": "Text",
                "text": f"Fixture label {i}",
                "enabled": True,
                "context": "Application content",
            }
            for i in range(1, 61)
        ]
        for index, kind, text, context in special:
            entries[index - 1].update(type=kind, text=text, context=context)
        tools = deepcopy(
            registry.schemas(names=["open_app", "observe_desktop", "desktop_action", "wait_for_ui"])
        )
        props = next(
            t["function"]["parameters"]["properties"]
            for t in tools
            if t["function"]["name"] == "desktop_action"
        )
        props["observation_id"]["enum"] = ["fixture"]
        props["ref"]["enum"] = ["", *(e["ref"] for e in entries)]
        props.pop("x", None)
        props.pop("y", None)
        result = ToolResult(
            ok=True,
            summary=f"Observed 60 controls in {app}",
            data={"title": app, "hwnd": 77, "entries": entries},
        )
        messages = [
            {"role": "system", "content": LOCAL_AGENT_SYSTEM},
            {"role": "user", "name": "current_task", "content": request},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "opened",
                        "function": {"name": "open_app", "arguments": json.dumps({"name": app})},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "opened",
                "_ui_window": 77,
                "content": wrap_untrusted(
                    "tool:open_app", ui_context(result, include_observation_id=False)
                ),
            },
        ]
        if label.startswith("media"):
            messages.insert(
                1,
                {
                    "role": "system",
                    "content": "Use app search for Riptide by The Chainsmokers. "
                    "Select the matching result. "
                    "Do not resume the old queue. Completion requires matching playing metadata.",
                },
            )
        step = constrain_ui_step(
            tools,
            result.data,
            goal=request_goal(request),
            request=request,
            search_submitted=label.endswith('_result'),
        )
        if step:
            tools = step.schemas
            messages.append({"role": "system", "content": step.instruction})
        start = time.monotonic()
        events = [
            e
            async for e in provider.stream_agent(
                "qwen3:4b-instruct", messages, tools=tools, require_tool=True, max_tokens=384
            )
        ]
        call = next((e for e in events if isinstance(e, ToolCall)), None)
        args = json.loads(call.arguments_json) if call else {}
        correct = bool(
            call
            and call.name == "desktop_action"
            and all(args.get(k) == v for k, v in expected.items())
        )
        if label == "media_search":
            correct = correct and "riptide" in args.get("text", "").casefold()
        if label == "browser_navigation":
            correct = correct and "youtube" in args.get("text", "").casefold()
        print(
            json.dumps(
                {
                    "fixture": label,
                    "correct_next_control": correct,
                    "wall_s": round(time.monotonic() - start, 2),
                    "arguments": args,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(main())
