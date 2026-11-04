"""Browser tools using Playwright (PLAN §13.3)."""

import json
import logging
from typing import ClassVar
from urllib.parse import quote_plus

from pydantic import BaseModel, Field

from clickachu.automation.browser_worker import get_browser_worker
from clickachu.tools.base import Tier, Tool, ToolContext, ToolResult, WorkerName

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- browser_open


class BrowserOpenArgs(BaseModel):
    url: str = Field(min_length=1, description="Web URL to navigate to")


class BrowserOpenTool(Tool[BrowserOpenArgs]):
    name: ClassVar[str] = "browser_open"
    description: ClassVar[str] = "Navigate the Clickachu browser to a web URL."
    Args: ClassVar[type[BaseModel]] = BrowserOpenArgs
    tier: ClassVar[Tier] = Tier.LOW
    worker: ClassVar[WorkerName] = "browser"

    def preview(self, args: BrowserOpenArgs) -> str:
        return f"Browse to {args.url}"

    async def run(self, args: BrowserOpenArgs, ctx: ToolContext) -> ToolResult:
        worker = get_browser_worker()
        if ctx.settings and ctx.settings.browser_channel:
            worker.set_channel(ctx.settings.browser_channel)
        try:
            page = await worker.open_url(args.url, run_id=ctx.run_id)
            title = await page.title()
            summary = f"Opened {page.url} ({title})"
            return ToolResult(
                ok=True,
                data={"url": page.url, "title": title},
                summary=summary,
                untrusted_text=f"Loaded {page.url}\nTitle: {title}",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("browser_open failed: %s", exc)
            return ToolResult(ok=False, summary=f"Failed to open URL {args.url}: {exc}")

    async def verify(self, args: BrowserOpenArgs, result: ToolResult) -> bool | None:
        if not result.ok:
            return False
        worker = get_browser_worker()
        page = await worker.get_page()
        return not page.is_closed() and page.url != "about:blank"


# ---------------------------------------------------------------- browser_search


class BrowserSearchArgs(BaseModel):
    query: str = Field(min_length=1, description="Search terms to look up")


class BrowserSearchTool(Tool[BrowserSearchArgs]):
    name: ClassVar[str] = "browser_search"
    description: ClassVar[str] = (
        "Search the web using the configured search URL in the Clickachu browser."
    )
    Args: ClassVar[type[BaseModel]] = BrowserSearchArgs
    tier: ClassVar[Tier] = Tier.LOW
    worker: ClassVar[WorkerName] = "browser"

    def preview(self, args: BrowserSearchArgs) -> str:
        return f"Search web for {args.query!r}"

    def target_urls(self, args: BrowserSearchArgs) -> list[str]:
        # Sensitive domain checking for the search engine if needed
        return []

    async def run(self, args: BrowserSearchArgs, ctx: ToolContext) -> ToolResult:
        worker = get_browser_worker()
        if ctx.settings and ctx.settings.browser_channel:
            worker.set_channel(ctx.settings.browser_channel)

        template = (
            ctx.settings.browser_search_url
            if ctx.settings and ctx.settings.browser_search_url
            else "https://www.google.com/search?q={query}"
        )
        url = template.format(query=quote_plus(args.query))
        try:
            page = await worker.open_url(url, run_id=ctx.run_id)
            title = await page.title()
            return ToolResult(
                ok=True,
                data={"url": page.url, "title": title, "query": args.query},
                summary=f"Searched for {args.query!r} ({page.url})",
                untrusted_text=f"Search URL: {page.url}\nTitle: {title}",
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, summary=f"Search failed for {args.query!r}: {exc}")

    async def verify(self, args: BrowserSearchArgs, result: ToolResult) -> bool | None:
        if not result.ok:
            return False
        worker = get_browser_worker()
        page = await worker.get_page()
        return not page.is_closed() and "search" in page.url.lower()


# ---------------------------------------------------------------- browser_read


class BrowserReadArgs(BaseModel):
    pass


class BrowserReadTool(Tool[BrowserReadArgs]):
    name: ClassVar[str] = "browser_read"
    description: ClassVar[str] = (
        "Read the active page accessibility tree with stable reference IDs (e.g. e1, e2) "
        "clamped to <= 6000 characters."
    )
    Args: ClassVar[type[BaseModel]] = BrowserReadArgs
    tier: ClassVar[Tier] = Tier.READ
    worker: ClassVar[WorkerName] = "browser"

    def preview(self, args: BrowserReadArgs) -> str:
        del args
        return "Read current web page"

    async def run(self, args: BrowserReadArgs, ctx: ToolContext) -> ToolResult:
        del args
        worker = get_browser_worker()
        try:
            tree_text, ref_map = await worker.read_snapshot(run_id=ctx.run_id)
            page = await worker.get_page(run_id=ctx.run_id)
            summary = f"Read page {page.url} ({len(ref_map)} interactive elements)"
            return ToolResult(
                ok=True,
                data={"url": page.url, "element_count": len(ref_map)},
                summary=summary,
                untrusted_text=tree_text,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, summary=f"Failed to read page: {exc}")


# ---------------------------------------------------------------- browser_screenshot


class BrowserScreenshotArgs(BaseModel):
    pass


class BrowserScreenshotTool(Tool[BrowserScreenshotArgs]):
    name: ClassVar[str] = "browser_screenshot"
    description: ClassVar[str] = (
        "Take a screenshot of the active browser page and save it to the data directory."
    )
    Args: ClassVar[type[BaseModel]] = BrowserScreenshotArgs
    tier: ClassVar[Tier] = Tier.READ
    worker: ClassVar[WorkerName] = "browser"

    def preview(self, args: BrowserScreenshotArgs) -> str:
        del args
        return "Take browser screenshot"

    async def run(self, args: BrowserScreenshotArgs, ctx: ToolContext) -> ToolResult:
        del args
        worker = get_browser_worker()
        try:
            path = await worker.take_screenshot(run_id=ctx.run_id)
            return ToolResult(
                ok=True,
                data={"path": path},
                summary=f"Saved browser screenshot to {path}",
                untrusted_text=f"Screenshot captured at: {path}",
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, summary=f"Screenshot failed: {exc}")


# ---------------------------------------------------------------- browser_click


class BrowserClickArgs(BaseModel):
    ref: str = Field(
        min_length=1, description="Element reference ID from browser_read (e.g. 'e12')"
    )


class BrowserClickTool(Tool[BrowserClickArgs]):
    name: ClassVar[str] = "browser_click"
    description: ClassVar[str] = "Click an element on the current page by its reference ID."
    Args: ClassVar[type[BaseModel]] = BrowserClickArgs
    tier: ClassVar[Tier] = Tier.LOW
    worker: ClassVar[WorkerName] = "browser"

    def preview(self, args: BrowserClickArgs) -> str:
        return f"Click element {args.ref}"

    async def run(self, args: BrowserClickArgs, ctx: ToolContext) -> ToolResult:
        worker = get_browser_worker()
        try:
            page = await worker.get_page(run_id=ctx.run_id)
            url_before = page.url
            await worker.click_ref(args.ref, run_id=ctx.run_id)
            url_after = page.url
            summary = f"Clicked element {args.ref}"
            if url_after != url_before:
                summary += f" (navigated to {url_after})"
            return ToolResult(
                ok=True,
                data={"ref": args.ref, "url": url_after},
                summary=summary,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, summary=f"Failed to click {args.ref}: {exc}")

    async def verify(self, args: BrowserClickArgs, result: ToolResult) -> bool | None:
        if not result.ok:
            return False
        worker = get_browser_worker()
        page = await worker.get_page()
        return not page.is_closed()


# ---------------------------------------------------------------- browser_fill


class BrowserFillArgs(BaseModel):
    ref: str = Field(min_length=1, description="Element reference ID from browser_read (e.g. 'e3')")
    text: str = Field(description="Text to type into the element")


class BrowserFillTool(Tool[BrowserFillArgs]):
    name: ClassVar[str] = "browser_fill"
    description: ClassVar[str] = "Fill an input or text field on the current page with text."
    Args: ClassVar[type[BaseModel]] = BrowserFillArgs
    tier: ClassVar[Tier] = Tier.LOW
    worker: ClassVar[WorkerName] = "browser"

    def preview(self, args: BrowserFillArgs) -> str:
        return f"Fill element {args.ref} with {args.text!r}"

    async def run(self, args: BrowserFillArgs, ctx: ToolContext) -> ToolResult:
        worker = get_browser_worker()
        try:
            await worker.fill_ref(args.ref, args.text, run_id=ctx.run_id)
            return ToolResult(
                ok=True,
                data={"ref": args.ref, "text": args.text},
                summary=f"Filled element {args.ref} with {args.text!r}",
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, summary=f"Failed to fill {args.ref}: {exc}")

    async def verify(self, args: BrowserFillArgs, result: ToolResult) -> bool | None:
        if not result.ok:
            return False
        worker = get_browser_worker()
        page = await worker.get_page()
        try:
            val = await page.locator(f'[data-clickachu-ref="{args.ref}"]').input_value()
            return val == args.text
        except Exception:  # noqa: BLE001
            return True


# ---------------------------------------------------------------- browser_submit


class BrowserSubmitArgs(BaseModel):
    ref: str = Field(
        min_length=1,
        description="Form submit button or element reference ID from browser_read (e.g. 'e12')",
    )


class BrowserSubmitTool(Tool[BrowserSubmitArgs]):
    name: ClassVar[str] = "browser_submit"
    description: ClassVar[str] = (
        "Submit a web form (CONFIRM tier; preview shows URL, element label, and filled values)."
    )
    Args: ClassVar[type[BaseModel]] = BrowserSubmitArgs
    tier: ClassVar[Tier] = Tier.CONFIRM
    worker: ClassVar[WorkerName] = "browser"

    def preview(self, args: BrowserSubmitArgs) -> str:
        worker = get_browser_worker()
        try:
            if worker._thread and worker._thread.is_alive():
                url, label, values = worker.run_coro(lambda: worker.get_submit_info(args.ref))
                vals_str = json.dumps(values, ensure_ascii=False)
                return f"Submit on {url} ({label}, values: {vals_str})"
        except Exception as exc:  # noqa: BLE001
            log.debug("could not compute dynamic submit preview: %s", exc)
        return f"Submit form via element {args.ref}"

    async def run(self, args: BrowserSubmitArgs, ctx: ToolContext) -> ToolResult:
        worker = get_browser_worker()
        try:
            page = await worker.get_page(run_id=ctx.run_id)
            url_before = page.url
            await worker.submit_ref(args.ref, run_id=ctx.run_id)
            # Wait briefly for potential navigation or response
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:  # noqa: BLE001
                pass
            url_after = page.url
            summary = f"Submitted form via {args.ref}"
            if url_after != url_before:
                summary += f" (navigated to {url_after})"
            return ToolResult(
                ok=True,
                data={"ref": args.ref, "url_before": url_before, "url_after": url_after},
                summary=summary,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, summary=f"Failed to submit {args.ref}: {exc}")

    async def verify(self, args: BrowserSubmitArgs, result: ToolResult) -> bool | None:
        if not result.ok:
            return False
        worker = get_browser_worker()
        page = await worker.get_page()
        return not page.is_closed()
