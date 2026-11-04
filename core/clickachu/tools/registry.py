"""Tool registry and OpenAI function-calling schema generation (PLAN §13.1)."""

from typing import Any

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
from clickachu.tools.desktop import (
    FocusWindowTool,
    GetActiveContextTool,
    InspectWindowTool,
    ListWindowsTool,
    OpenAppTool,
    TypeTextTool,
)
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
from clickachu.tools.memory_tools import RecallTool, RememberTool
from clickachu.tools.process_tools import (
    GetProcessLogsTool,
    RunProjectCommandTool,
    StopProcessTool,
)


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
        """Return a relevant subset of tool schemas for local models to reduce latency and token bloat."""
        p_lower = prompt.lower()
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
        is_browser = any(
            w in p_lower
            for w in (
                "browse",
                "browser",
                "website",
                "web",
                "url",
                "search the web",
                "search online",
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
