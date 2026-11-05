"""Local-model decisions must become validated tools, never synthetic execution prose."""

import pytest

from clickachu.llm.local_actions import FINISH, action_messages, action_schema
from clickachu.llm.openrouter import OpenRouterProvider
from clickachu.llm.provider import Done, LLMError, TextDelta, ToolCall
from clickachu.tools.registry import create_default_registry


def test_required_decision_excludes_finish_and_uses_actual_argument_schema():
    tools = create_default_registry().schemas(names=["open_app"])
    schema = action_schema(tools, require_tool=True)
    assert len(schema["anyOf"]) == 1
    branch = schema["anyOf"][0]["properties"]
    assert branch["tool"]["const"] == "open_app"
    assert "name" in branch["arguments"]["required"]
    optional = action_schema(tools, require_tool=False)
    assert optional["anyOf"][-1]["properties"]["tool"]["const"] == FINISH


def test_schema_compaction_preserves_actual_fields_named_title_and_description():
    from clickachu.llm.local_actions import compact_schema

    original = {
        "type": "object",
        "title": "Metadata",
        "properties": {
            "title": {"type": "string", "description": "Window title"},
            "description": {"type": "string", "title": "User description"},
        },
        "required": ["title"],
    }
    compact = compact_schema(original)
    assert "title" not in compact and "title" in compact["properties"]
    assert "description" in compact["properties"]
    tools = create_default_registry().schemas(names=["observe_desktop"])
    parameters = action_schema(tools, True)["anyOf"][0]["properties"]["arguments"]
    assert "title" in parameters["properties"] and "title" in parameters["required"]


def test_tool_transcript_preserves_results_without_native_parser_dependencies():
    messages = [
        {"role": "user", "content": "Open the app"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"function": {"name": "open_app", "arguments": '{"name":"Store"}'}}],
        },
        {
            "role": "tool",
            "tool_call_id": "one",
            "content": "<untrusted_data>Opened Store</untrusted_data>",
        },
    ]
    converted = action_messages(messages, [], False)
    assert all(message["role"] != "tool" and not message.get("tool_calls") for message in converted)
    assert converted[-1]["content"] == messages[2]["content"]
    assert any("Requested actions:" in m["content"] and "Store" in m["content"] for m in converted)
    assert "Tool catalog" in converted[0]["content"]
    assert messages[1]["tool_calls"]  # The original transcript remains intact.


def test_long_local_history_is_bounded_and_latest_controls_and_goal_survive():
    from clickachu.llm.local_actions import MAX_PROMPT_CHARS
    from clickachu.llm.prompts import LOCAL_AGENT_SYSTEM

    messages = [
        {"role": "system", "content": LOCAL_AGENT_SYSTEM},
        {"role": "user", "content": "Find a specific song on Spotify and play it"},
    ]
    for i in range(12):
        messages.extend(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "observe_desktop",
                                "arguments": '{"title":"Spotify"}',
                            }
                        }
                    ],
                },
                {"role": "tool", "content": f"observation_id=old-{i}\n" + "old text\n" * 700},
            ]
        )
    messages.extend(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {"function": {"name": "observe_desktop", "arguments": '{"title":"Spotify"}'}}
                ],
            },
            {"role": "tool", "content": "observation_id=current\nc44 [Button] Play"},
        ]
    )
    result = action_messages(
        messages, create_default_registry().hybrid_schemas("open Spotify"), False
    )
    assert sum(len(str(m.get("content") or "")) + 32 for m in result) <= MAX_PROMPT_CHARS
    assert messages[1] in result
    assert any("observation_id=current" in m.get("content", "") for m in result)
    assert "old-0" not in str(result)
    assert "untrusted" in str(result)


def test_current_request_survives_checkpoint_and_other_user_role_context():
    messages = [
        {"role": "system", "content": "Rules"},
        {"role": "user", "name": "current_task", "content": "Complete my original request"},
        {"role": "user", "content": "Historical checkpoint " + "x" * 20000},
        {
            "role": "assistant",
            "tool_calls": [{"function": {"name": "read_file", "arguments": "{}"}}],
        },
        {"role": "tool", "content": "Latest observed result"},
    ]
    result = action_messages(messages, [], False)
    assert messages[1] in result
    assert any(m.get("content") == "Latest observed result" for m in result)
    assert messages[2] not in result


def test_stable_contract_precedes_changing_task_and_observations():
    tools = create_default_registry().schemas(names=["open_app"])
    first = action_messages(
        [
            {"role": "system", "content": "Safety constraints"},
            {"role": "user", "content": "First task"},
        ],
        tools,
        True,
    )
    second = action_messages(
        [
            {"role": "system", "content": "Safety constraints"},
            {"role": "user", "content": "Second task"},
        ],
        tools,
        False,
    )
    assert first[:2] == second[:2]
    assert "Tool catalog" in first[1]["content"]


async def test_observation_binding_is_controller_owned_and_keyboard_shape_is_constrained():
    from copy import deepcopy

    tools = create_default_registry().schemas(names=["desktop_action"])
    tools = deepcopy(tools)
    props = tools[0]["function"]["parameters"]["properties"]
    props["observation_id"]["enum"] = ["current-observation"]
    props["ref"]["enum"] = ["", "c1"]
    schema = action_schema(tools, True)
    branches = [v["properties"]["arguments"] for v in schema["anyOf"]]
    keys = next(b for b in branches if b["properties"]["action"]["const"] == "keys")
    assert "keys" in keys["required"] and "text" not in keys["properties"]
    assert keys["properties"]["keys"]["minLength"] == 1
    assert all("observation_id" not in b["properties"] for b in branches)
    provider = OpenRouterProvider.__new__(OpenRouterProvider)
    provider._is_ollama = True

    async def stream(*args, **kwargs):
        yield TextDelta('{"tool":"desktop_action","arguments":{"action":"keys","keys":"7"}}')
        yield Done()

    provider.stream = stream
    events = [e async for e in provider.stream_agent("fixture", [], tools, require_tool=True)]
    import json

    assert json.loads(events[0].arguments_json)["observation_id"] == "current-observation"


def test_ui_context_keeps_control_names_and_group_identity_without_repeating_ancestors():
    from clickachu.llm.ui_context import ui_context
    from clickachu.tools.base import ToolResult

    result = ToolResult(
        ok=True,
        summary="Observed controls",
        data={
            "title": "Example",
            "observation_id": "current",
            "entries": [
                {"ref": "c1", "type": "Button", "text": "Play", "context": "Player controls"},
                {"ref": "c2", "type": "Button", "text": "Next", "context": "Player controls"},
                {"ref": "c3", "type": "Button", "text": "Play", "context": "Recommendations"},
            ],
        },
    )
    prompt = ui_context(result)
    assert prompt.count("Player controls") == 1
    assert (
        "Recommendations" in prompt
        and "c1 [Button] Play" in prompt
        and "c3 [Button] Play" in prompt
    )


@pytest.mark.parametrize(
    "reply,required,expected",
    [
        ('{"tool":"open_app","arguments":{"name":"Store"}}', True, "open_app"),
        ('{"tool":"__finish__","arguments":{"answer":"Done."}}', False, "Done."),
    ],
)
async def test_adapter_returns_action_or_final_text(reply, required, expected):
    provider = OpenRouterProvider.__new__(OpenRouterProvider)
    provider._is_ollama = True
    requests = []

    async def stream(model, messages, **kwargs):
        requests.append(kwargs)
        yield TextDelta(reply)
        yield Done()

    provider.stream = stream
    events = [
        event
        async for event in provider.stream_agent(
            "fixture",
            [],
            create_default_registry().schemas(names=["open_app"]),
            require_tool=required,
        )
    ]
    if required:
        assert isinstance(events[0], ToolCall) and events[0].name == expected
        assert events[0].arguments_json == '{"name": "Store"}'
    else:
        assert isinstance(events[0], TextDelta) and events[0].text == expected
    assert isinstance(events[-1], Done)
    assert "response_schema" in requests[0] and "tools" not in requests[0]
    assert requests[0]["temperature"] == 0 and requests[0]["keep_alive"] == 900


@pytest.mark.parametrize(
    "reply",
    [
        "I executed the command!",
        '{"tool":"not_available","arguments":{}}',
        '{"tool":"open_app","arguments":"invalid"}',
        '{"tool":"__finish__","arguments":{"answer":"Done"}}',
    ],
)
async def test_invalid_or_premature_decisions_cannot_execute(reply):
    provider = OpenRouterProvider.__new__(OpenRouterProvider)
    provider._is_ollama = True

    async def stream(*args, **kwargs):
        yield TextDelta(reply)
        yield Done()

    provider.stream = stream
    with pytest.raises(LLMError):
        _ = [
            event
            async for event in provider.stream_agent(
                "fixture",
                [],
                create_default_registry().schemas(names=["open_app"]),
                require_tool=True,
            )
        ]


async def test_adapter_does_not_publish_or_execute_repetitive_reasoning():
    from clickachu.llm.provider import ReasoningDelta

    provider = OpenRouterProvider.__new__(OpenRouterProvider)
    provider._is_ollama = True

    async def stream(*args, **kwargs):
        yield ReasoningDelta("valid-looking action followed by repetitive junk " * 100)
        yield TextDelta('{"tool":"open_app","arguments":{"name":"Notepad"}}')
        yield Done()

    provider.stream = stream
    events = []
    with pytest.raises(LLMError, match="drifted"):
        async for event in provider.stream_agent(
            "fixture", [], create_default_registry().schemas(names=["open_app"]), require_tool=True
        ):
            events.append(event)
    assert events == []
