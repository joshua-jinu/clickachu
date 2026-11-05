"""Load detailed tool schemas on demand instead of sending the full registry each turn."""

from typing import ClassVar

from pydantic import BaseModel, Field

from clickachu.tools.base import Tier, Tool, ToolContext, ToolResult


class DiscoverToolsArgs(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=200,
        description="Capability keywords, e.g. browser, files, desktop, memory",
    )


class DiscoverToolsTool(Tool[DiscoverToolsArgs]):
    name: ClassVar[str] = "discover_tools"
    description: ClassVar[str] = (
        "Find capabilities and load their full schemas for use on the next turn. "
        "Search browser, desktop, file, process, memory, or exact tool names. "
        "Call this when a task needs a capability absent from the current tools."
    )
    Args: ClassVar[type[BaseModel]] = DiscoverToolsArgs
    tier: ClassVar[Tier] = Tier.READ

    def __init__(self, registry):
        self.registry = registry

    def run(self, args: DiscoverToolsArgs, ctx: ToolContext) -> ToolResult:
        terms = args.query.lower().replace("files", "file").split()
        ranked = []
        for tool in self.registry.all():
            if tool.name == self.name or tool.tier == Tier.BLOCKED:
                continue
            haystack = (tool.name + " " + tool.description).lower()
            score = sum(3 if term in tool.name else 1 for term in terms if term in haystack)
            if score:
                ranked.append((score, tool.name))
        names = [name for _, name in sorted(ranked, key=lambda pair: -pair[0])[:8]]
        matched = bool(names)
        if not names:
            # App names need not occur in generic tool descriptions. Expose the computer
            # workflow instead of pretending an empty discovery advanced the user's task.
            names = [
                name
                for name in ("open_app", "observe_desktop", "desktop_action", "wait_for_ui")
                if self.registry.get(name)
            ]
        schemas = self.registry.schemas(names=names)
        return ToolResult(
            ok=bool(names),
            summary=(
                f"Loaded {len(names)} capabilities: {', '.join(names)}"
                if matched
                else f"No named capability matched {args.query!r}. "
                + (
                    f"Use the general desktop tools: {', '.join(names)}."
                    if names
                    else "No applicable capability is available; do not repeat this query."
                )
            ),
            data={"schemas": schemas, "matched": matched},
        )
