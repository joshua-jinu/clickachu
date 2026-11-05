r"""Measure local decision inference on synthetic UI evidence. Never executes any action.

Run from core: .venv\Scripts\python benchmarks/ui_latency.py
Requires the already installed local qwen3:4b-instruct model and running Ollama.
"""

import json
import time
import urllib.error
import urllib.request
from copy import deepcopy

from clickachu.llm.local_actions import action_messages, action_schema
from clickachu.llm.prompts import LOCAL_AGENT_SYSTEM
from clickachu.llm.ui_context import ui_context
from clickachu.tools.base import ToolResult
from clickachu.tools.registry import create_default_registry


def main():
    registry = create_default_registry()
    for step, digit in enumerate((7, 8), 1):
        goal = f"Click the {digit} button in Calculator"
        entries = [
            {
                "ref": f"c{i}",
                "type": "Button",
                "text": f"Fixture control {i}",
                "context": "Keypad",
                "enabled": True,
            }
            for i in range(1, 46)
        ]
        entries[36]["text"], entries[37]["text"] = "Seven", "Eight"
        entries.append(
            {
                "ref": "c46",
                "type": "Text",
                "text": f"Display is {0 if step == 1 else 7}",
                "context": "Result",
                "enabled": True,
            }
        )
        observed = ToolResult(
            ok=True,
            summary="Observed 46 fixture controls",
            data={"title": "Calculator", "observation_id": f"fixture-{step}", "entries": entries},
        )
        tools = deepcopy(registry.hybrid_schemas(goal))
        action = next(t["function"] for t in tools if t["function"]["name"] == "desktop_action")
        props = action["parameters"]["properties"]
        props["observation_id"]["enum"] = [f"fixture-{step}"]
        props["ref"]["enum"] = ["", *(e["ref"] for e in entries)]
        props.pop("x", None)
        props.pop("y", None)
        messages = [
            {"role": "system", "content": LOCAL_AGENT_SYSTEM},
            {"role": "user", "name": "current_task", "content": goal},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "opened",
                        "function": {"name": "open_app", "arguments": '{"name":"Calculator"}'},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "opened",
                "_ui_window": 77,
                "content": '<untrusted_data source="tool:open_app">\n'
                + ui_context(observed, include_observation_id=False)
                + "\n</untrusted_data>",
            },
        ]
        prompt = action_messages(messages, tools, False)
        body = {
            "model": "qwen3:4b-instruct",
            "messages": prompt,
            "format": action_schema(tools, False),
            "stream": False,
            "think": False,
            "keep_alive": 900,
            "options": {"num_gpu": 0, "num_ctx": 4096, "num_predict": 768, "temperature": 0},
        }
        start = time.monotonic()
        request = urllib.request.Request(
            "http://127.0.0.1:11434/api/chat",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=150) as response:
                result = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise RuntimeError(exc.read().decode("utf-8", "replace")) from exc
        decision = json.loads(result["message"]["content"])
        expected = {"action": "click", "ref": f"c{37 if digit == 7 else 38}"}
        correct = decision.get("tool") == "desktop_action" and all(
            decision.get("arguments", {}).get(k) == v for k, v in expected.items()
        )
        print(
            json.dumps(
                {
                    "fixture": step,
                    "correct": correct,
                    "wall_s": round(time.monotonic() - start, 2),
                    "decision": decision,
                    "input_tokens": result.get("prompt_eval_count"),
                    "cached_tokens": result.get("prompt_eval_cached_count"),
                    "prefill_s": round(result.get("prompt_eval_duration", 0) / 1e9, 3),
                    "decode_s": round(result.get("eval_duration", 0) / 1e9, 3),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
