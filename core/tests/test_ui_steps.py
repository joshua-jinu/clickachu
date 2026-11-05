from copy import deepcopy

from clickachu.agent.goals import request_goal
from clickachu.agent.ui_steps import constrain_ui_step
from clickachu.llm.local_actions import action_schema
from clickachu.tools.registry import create_default_registry


def bound_tools():
    tools = create_default_registry().schemas(
        names=["open_app", "observe_desktop", "desktop_action", "wait_for_ui"]
    )
    props = next(
        t["function"]["parameters"]["properties"]
        for t in tools
        if t["function"]["name"] == "desktop_action"
    )
    props["observation_id"]["enum"] = ["current"]
    props["ref"]["enum"] = ["", "c1", "c2", "c3", "c4"]
    return tools


def test_visible_search_uses_editable_search_control_instead_of_reinspection():
    tools = bound_tools()
    before = deepcopy(tools)
    data = {
        "entries": [
            {"ref": "c1", "type": "Edit", "text": "https://embedded.app/index.html"},
            {"ref": "c2", "type": "ComboBox", "text": "Search all media"},
            {"ref": "c3", "type": "Button", "text": "Play"},
        ]
    }
    step = constrain_ui_step(
        tools, data, goal=request_goal("play Example Song by Example Artist on Any Player")
    )
    schema = action_schema(step.schemas, require_tool=True)
    variants = schema["anyOf"]
    assert len(variants) == 1
    props = variants[0]["properties"]["arguments"]["properties"]
    assert props["action"]["const"] == "fill"
    assert props["ref"]["enum"] == ["c2"]
    assert props["submit"]["const"] is True
    assert "submit" in variants[0]["properties"]["arguments"]["required"]
    assert tools == before
    assert step.automatic_arguments == {
        "observation_id": "current",
        "action": "fill",
        "ref": "c2",
        "text": "Example Song Example Artist",
        "submit": True,
        "wait_for_text": "Example Song",
    }


def test_matching_result_excludes_old_queue_and_wrong_artist():
    data = {
        "entries": [
            {"ref": "c1", "type": "Button", "text": "Play", "context": "Old Song by Other Artist"},
            {
                "ref": "c2",
                "type": "Button",
                "text": "Play Example Song",
                "context": "Example Artist",
            },
            {"ref": "c3", "type": "Button", "text": "Play Example Song", "context": "Wrong Artist"},
        ]
    }
    goal = request_goal("play Example Song by Example Artist on Any Player")
    step = constrain_ui_step(bound_tools(), data, goal=goal, search_submitted=True)
    props = step.schemas[0]["function"]["parameters"]["properties"]
    assert props["action"]["enum"] == ["click"] and props["ref"]["enum"] == ["c2"]


def test_missing_target_keeps_normal_inspection_tools_and_does_not_invent_refs():
    data = {"entries": [{"ref": "c1", "type": "Pane", "text": "Loading"}]}
    assert (
        constrain_ui_step(bound_tools(), data, goal=request_goal("play Example Song on Any Player"))
        is None
    )


def test_requested_browser_destination_uses_observed_address_field():
    data = {"entries": [{"ref": "c1", "type": "Edit", "text": "Address and search bar"}]}
    step = constrain_ui_step(
        bound_tools(), data, request="open Example Browser and visit Example Docs"
    )
    props = step.schemas[0]["function"]["parameters"]["properties"]
    assert props["action"]["enum"] == ["fill"] and props["ref"]["enum"] == ["c1"]
    assert "Example Docs" in step.instruction


def test_destination_search_results_produce_a_link_click_not_another_open():
    data = {"entries": [{"ref": "c1", "type": "Hyperlink", "text": "Example Docs"}]}
    step = constrain_ui_step(
        bound_tools(),
        data,
        request="open Example Browser and open Example Docs",
        search_submitted=True,
    )
    assert len(step.schemas) == 1
    assert step.schemas[0]["function"]["parameters"]["properties"]["action"]["enum"] == ["click"]


def test_two_app_request_keeps_second_requested_app_as_a_valid_option():
    data = {"entries": [{"ref": "c1", "type": "Edit", "text": "Address and search bar"}]}
    step = constrain_ui_step(bound_tools(), data, request="open Example Browser and open Notepad")
    assert step.schemas[1]["function"]["parameters"]["properties"]["name"]["enum"] == ["Notepad"]


def test_row_selection_is_not_automatically_treated_as_play_and_is_not_forced_twice():
    data = {
        "entries": [{"ref": "c1", "type": "DataItem", "text": "Example Song by Example Artist"}]
    }
    goal = request_goal("play Example Song by Example Artist on Any Player")
    step = constrain_ui_step(bound_tools(), data, goal=goal, search_submitted=True)
    assert step.automatic_arguments is None
    assert step.schemas[0]["function"]["parameters"]["properties"]["action"]["enum"] == [
        "click",
        "double_click",
    ]
    assert (
        constrain_ui_step(bound_tools(), data, goal=goal, search_submitted=True, used_refs={"c1"})
        is None
    )


def test_equal_clock_ticks_bind_the_newest_observation(monkeypatch):
    import time
    from clickachu.tools.computer import Observation, _observations, current_observation
    captured = time.monotonic()
    for key, ref in [('clock-old', 'c1'), ('clock-new', 'c2')]:
        monkeypatch.setitem(_observations, key, Observation('clock-tie', 111, (0, 0, 100, 100), captured, 10, {ref: ((), '', 'Button', ref)}))
    assert current_observation('clock-tie')[0] == 'clock-new'
