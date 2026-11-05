"""Tool registry and OpenAI function-calling schema generation (PLAN §13.1)."""

import re
from copy import deepcopy
from typing import Any

from clickachu.llm.router import notepad_write_requested, web_lookup_requested
from clickachu.tools.base import Tier, Tool
from clickachu.tools.browser_tools import (
    BrowserClickTool,
    BrowserFillTool,
    BrowserOpenTool,
    BrowserReadTool,
    BrowserScreenshotTool,
    BrowserSearchTool,
    BrowserSubmitTool,
)
from clickachu.tools.computer import (
    DesktopActionTool,
    ObserveDesktopTool,
    WaitForUITool,
    current_observation,
)
from clickachu.tools.desktop import (
    FocusWindowTool,
    GetActiveContextTool,
    InspectWindowTool,
    ListWindowsTool,
    OpenAppTool,
    TypeTextTool,
)
from clickachu.tools.discovery import DiscoverToolsTool
from clickachu.tools.execution import ExecuteCodeTool
from clickachu.tools.files import (
    CreateFolderTool,
    MoveFileTool,
    OpenFileTool,
    OpenUrlTool,
    ReadFileTool,
    SearchFilesTool,
    TrashFileTool,
    WriteFileTool,
)
from clickachu.tools.media import ControlMediaTool
from clickachu.tools.memory_tools import RecallTool, RememberTool
from clickachu.tools.notepad import WriteNotepadTool
from clickachu.tools.process_tools import (
    GetProcessLogsTool,
    ListProcessesTool,
    RunProjectCommandTool,
    StopProcessTool,
)
from clickachu.tools.web_tools import WebFetchTool, WebSearchTool
from clickachu.tools.workspace import WorkspaceFilesTool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool[Any]] = {}

    def register(self, tool: Tool[Any]) -> Tool[Any]:
        name = getattr(tool, "name", "").strip()
        if not name:
            raise ValueError("tool must have a non-empty name")
        if name in self._tools:
            raise ValueError(f"duplicate tool name {name!r}")
        self._tools[name] = tool
        return tool

    def get(self, name: str) -> Tool[Any] | None:
        return self._tools.get(name)

    def all(self) -> list[Tool[Any]]:
        return list(self._tools.values())

    def schemas(
        self,
        *,
        include_blocked: bool = False,
        names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """OpenAI tool JSON built from each tool's `Args.model_json_schema()` (§13.1)."""
        out: list[dict[str, Any]] = []
        name_set = set(names) if names is not None else None
        for tool in self._tools.values():
            if name_set is not None and tool.name not in name_set:
                continue
            if not include_blocked and tool.tier == Tier.BLOCKED:
                continue
            schema = _clean_tool_schema(dict(tool.Args.model_json_schema()))
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": schema,
                    },
                }
            )
        return out

    def scoped_schemas(self, prompt: str) -> list[dict[str, Any]]:
        """Limit local-model tools to the relevant workflow to reduce latency and ambiguity."""
        p_lower = prompt.lower()
        if notepad_write_requested(prompt):
            return self.schemas(names=["write_notepad"])
        if web_lookup_requested(prompt):
            return self.schemas(
                names=[
                    "web_search",
                    "web_fetch",
                ]
            )
        is_file = any(
            w in p_lower
            for w in (
                "file",
                "folder",
                "dir",
                "directory",
                "write",
                "read",
                "path",
                "markdown",
                ".md",
                ".txt",
                ".json",
                ".py",
                "save",
                "delete",
                "trash",
                "desktop",
                "onedrive",
                "document",
                "recipe",
                "recepie",
                "note",
            )
        )
        is_browser = web_lookup_requested(prompt) or any(
            w in p_lower
            for w in (
                "browse",
                "browser",
                "website",
                "web",
                "url",
                "search the web",
                "search online",
                "online",
                "http",
                "google",
            )
        )
        is_app = any(
            w in p_lower
            for w in (
                "app",
                "window",
                "open",
                "oppen",
                "launch",
                "start",
                "focus",
                "switch",
                "type",
                "notepad",
                "calc",
                "terminal",
                "cmd",
                "powershell",
            )
        )

        selected_names: set[str] = set()
        if is_file or (not is_browser and not is_app):
            selected_names.update(
                [
                    "write_file",
                    "read_file",
                    "open_file",
                    "create_folder",
                    "move_file",
                    "trash_file",
                    "search_files",
                    "open_url",
                ]
            )
        if is_browser:
            selected_names.update(
                [
                    "browser_open",
                    "browser_search",
                    "browser_read",
                    "browser_click",
                    "browser_fill",
                    "browser_submit",
                    "browser_screenshot",
                ]
            )
        if is_app or is_file:
            selected_names.update(
                ["open_app", "focus_window", "list_windows", "inspect_window", "type_text"]
            )

        if not selected_names:
            return self.schemas()
        return self.schemas(names=list(selected_names))

    def bind_ui_state(self, schemas, run_id):
        """Only expose real, current native refs; never ask a model to invent observation IDs."""
        if not isinstance(self.get("desktop_action"), DesktopActionTool):
            return schemas  # Integration tools can have their own observation mechanism.
        current = current_observation(run_id)
        bound = []
        for schema in schemas:
            if schema["function"]["name"] != "desktop_action":
                bound.append(schema)
                continue
            if current is None:
                continue  # Inspect first. No action can be grounded without an observation.
            key, observation = current
            item = deepcopy(schema)
            properties = item["function"]["parameters"]["properties"]
            properties["observation_id"]["enum"] = [key]
            properties["ref"]["enum"] = ["", *observation.controls]
            if not observation.visual:
                properties.pop("x", None)
                properties.pop("y", None)
            bound.append(item)
        return bound

    def hybrid_schemas(self, prompt: str) -> list[dict[str, Any]]:
        # Custom registries (including integrations and tests) retain their own tool set.
        if self.get("discover_tools") is None:
            return self.schemas()
        names = {"discover_tools", "get_active_context"}
        p = prompt.lower()
        if notepad_write_requested(prompt):
            names.add("write_notepad")
        elif web_lookup_requested(prompt):
            names.update(("web_search", "web_fetch"))
        elif re.search(r"\b(?:play|pause|resume|music|media)\b", p):
            names.update(
                (
                    "control_media",
                    "open_app",
                    "observe_desktop",
                    "desktop_action",
                    "wait_for_ui",
                    "list_windows",
                )
            )
        elif any(term in p for term in ("browser", "website", "http", "web page")):
            names.update(("browser_open", "browser_read", "browser_click", "browser_fill"))
        elif any(
            term in p
            for term in ("window", "app", "click", "type", "screen", "open", "store", "search in")
        ):
            names.update(
                (
                    "list_windows",
                    "open_app",
                    "observe_desktop",
                    "desktop_action",
                    "wait_for_ui",
                )
            )
        elif "process" in p:
            names.add("list_processes")
        else:
            names.update(("execute_code", "search_files", "read_file", "write_file"))
        if re.search(r"\b(?:code|script|python|powershell|terminal|batch|calculate|data)\b", p):
            names.add("execute_code")
        return self.schemas(names=list(names))


def _clean_tool_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Flatten Optional[T] `anyOf` unions and strip titles for Gemini/OpenAI compatibility."""
    cleaned = dict(schema)
    cleaned.pop("title", None)
    props = cleaned.get("properties")
    if isinstance(props, dict):
        new_props: dict[str, Any] = {}
        for name, prop in props.items():
            if not isinstance(prop, dict):
                new_props[name] = prop
                continue
            p = dict(prop)
            p.pop("title", None)
            any_of = p.get("anyOf")
            if isinstance(any_of, list):
                non_null = [
                    branch
                    for branch in any_of
                    if isinstance(branch, dict) and branch.get("type") != "null"
                ]
                if len(non_null) == 1:
                    p.pop("anyOf", None)
                    if p.get("default") is None:
                        p.pop("default", None)
                    merged = dict(non_null[0])
                    merged.update(p)
                    p = merged
            new_props[name] = p
        cleaned["properties"] = new_props
    return cleaned


def create_default_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(ExecuteCodeTool())
    reg.register(WorkspaceFilesTool())
    reg.register(ObserveDesktopTool())
    reg.register(DesktopActionTool())
    reg.register(WaitForUITool())
    reg.register(DiscoverToolsTool(reg))
    # Memory
    reg.register(RecallTool())
    reg.register(RememberTool())
    # Desktop
    reg.register(GetActiveContextTool())
    reg.register(ListWindowsTool())
    reg.register(InspectWindowTool())
    reg.register(OpenAppTool())
    reg.register(FocusWindowTool())
    reg.register(TypeTextTool())
    reg.register(WriteNotepadTool())
    reg.register(ControlMediaTool())
    reg.register(WebSearchTool())
    reg.register(WebFetchTool())
    # Files
    reg.register(SearchFilesTool())
    reg.register(ReadFileTool())
    reg.register(OpenFileTool())
    reg.register(OpenUrlTool())
    reg.register(CreateFolderTool())
    reg.register(MoveFileTool())
    reg.register(WriteFileTool())
    reg.register(TrashFileTool())
    # Processes
    reg.register(GetProcessLogsTool())
    reg.register(ListProcessesTool())
    reg.register(StopProcessTool())
    reg.register(RunProjectCommandTool())
    # Browser
    reg.register(BrowserOpenTool())
    reg.register(BrowserSearchTool())
    reg.register(BrowserReadTool())
    reg.register(BrowserScreenshotTool())
    reg.register(BrowserClickTool())
    reg.register(BrowserFillTool())
    reg.register(BrowserSubmitTool())
    return reg


_default_registry = create_default_registry()


def default_registry() -> ToolRegistry:
    return _default_registry


def register(tool: Tool[Any]) -> Tool[Any]:
    return _default_registry.register(tool)


def get(name: str) -> Tool[Any] | None:
    return _default_registry.get(name)


def schemas(*, include_blocked: bool = False) -> list[dict[str, Any]]:
    return _default_registry.schemas(include_blocked=include_blocked)
