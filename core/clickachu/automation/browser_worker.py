"""Browser worker using Playwright persistent context (PLAN §5, §13.3).

Runs on its own dedicated thread with its own asyncio event loop (hard rule 6).
All Playwright operations run here via `run_browser(coro_or_fn, *args, timeout=...)`.
"""

import asyncio
import inspect
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from playwright.async_api import BrowserContext, Page, async_playwright

from clickachu.config import get_paths

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 30.0
MAX_TREE_CHARS = 6000

TAG_SCRIPT = """
() => {
    let refIndex = 1;
    const elements = document.querySelectorAll(
        'button, a, input, textarea, select, [role="button"], [role="link"], [role="checkbox"], ' +
        '[role="menuitem"], [role="tab"], [role="searchbox"], h1, h2, h3, h4, h5, h6'
    );
    const items = [];
    for (const el of elements) {
        if (el.offsetParent === null && el.offsetWidth === 0 && el.offsetHeight === 0) {
            continue;
        }
        const tag = el.tagName.toLowerCase();
        const isHeading = tag.startsWith('h') && tag.length === 2;
        let ref = '';
        if (!isHeading) {
            ref = 'e' + (refIndex++);
            el.setAttribute('data-clickachu-ref', ref);
        }
        const role = el.getAttribute('role') || (
            tag === 'input' ? (el.type || 'text') :
            (isHeading ? 'heading' : tag)
        );
        const name = (
            el.getAttribute('aria-label') ||
            el.innerText ||
            el.placeholder ||
            el.value ||
            el.name ||
            ''
        ).trim().replace(/\\s+/g, ' ');
        const value = (tag === 'input' || tag === 'textarea' || tag === 'select') ? el.value : '';
        const href = (tag === 'a' && el.getAttribute('href')) ? el.getAttribute('href') : '';
        items.push({
            ref,
            tag,
            role,
            name: name.substring(0, 80),
            value: value.substring(0, 80),
            href: href.substring(0, 100)
        });
    }
    return items;
}
"""

FORM_VALUES_SCRIPT = """
(el) => {
    const form = el.closest('form') || document.body;
    const inputs = form.querySelectorAll('input, textarea, select');
    const values = {};
    for (const inp of inputs) {
        if (inp.type === 'hidden' || inp.type === 'submit') continue;
        const key = inp.name || inp.getAttribute('aria-label') ||
            inp.placeholder || inp.id || 'input';
        values[key] = inp.value || '';
    }
    return values;
}
"""


class BrowserWorker:
    def __init__(self, headless: bool = False) -> None:
        self._headless = headless
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready_event = threading.Event()
        self._lock = threading.Lock()
        self._stopped = False
        self._playwright: Any = None
        self._context: BrowserContext | None = None
        self._run_pages: dict[str, Page] = {}
        self._ref_cache: dict[str, dict[str, Any]] = {}
        self._channel: str = "msedge"

    def set_channel(self, channel: str) -> None:
        self._channel = channel

    def start(self) -> None:
        with self._lock:
            if self._stopped:
                raise RuntimeError("Browser worker was shut down")
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._loop_entry, name="clickachu-browser", daemon=True
                )
                self._thread.start()
        self._ready_event.wait(timeout=5.0)

    def _loop_entry(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready_event.set()
        try:
            self._loop.run_forever()
        finally:
            if self._context:
                self._loop.run_until_complete(self._close_context())
            if self._playwright:
                self._loop.run_until_complete(self._playwright.stop())
            self._loop.close()

    async def _close_context(self) -> None:
        if self._context:
            try:
                await self._context.close()
            except Exception as exc:  # noqa: BLE001
                log.debug("error closing browser context: %s", exc)
            self._context = None

    async def _ensure_context(self) -> BrowserContext:
        if self._context is None:
            self._playwright = await async_playwright().start()
            paths = get_paths()
            channel = self._channel or "msedge"
            user_data_dir = str(paths.browser_profile)

            # Ensure data dir exists
            paths.browser_profile.mkdir(parents=True, exist_ok=True)

            log.info("launching browser persistent context (channel=%r)", channel)
            try:
                self._context = await self._playwright.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    channel=channel,
                    headless=self._headless,
                    viewport=None,
                )
            except Exception as exc:  # noqa: BLE001
                # If configured channel failed, try chrome or informative error
                alt_channel = "chrome" if channel == "msedge" else "msedge"
                try:
                    log.info("trying alternate channel %r", alt_channel)
                    self._context = await self._playwright.chromium.launch_persistent_context(
                        user_data_dir=user_data_dir,
                        channel=alt_channel,
                        headless=self._headless,
                        viewport=None,
                    )
                    self._channel = alt_channel
                except Exception:  # noqa: BLE001
                    raise RuntimeError(
                        f"Failed to launch browser with channel {channel!r}. "
                        f"Please ensure Microsoft Edge or Google Chrome is installed. ({exc})"
                    ) from exc
        return self._context

    async def get_page(self, run_id: str | None = None) -> Page:
        context = await self._ensure_context()
        if run_id and run_id in self._run_pages:
            page = self._run_pages[run_id]
            if not page.is_closed():
                return page

        # Reuse open page or open a new one (§13.3 one page per run)
        for p in context.pages:
            if not p.is_closed():
                if run_id:
                    self._run_pages[run_id] = p
                return p

        page = await context.new_page()
        if run_id:
            self._run_pages[run_id] = page
        return page

    async def open_url(self, url: str, run_id: str | None = None) -> Page:
        page = await self.get_page(run_id)
        await page.goto(url, wait_until="domcontentloaded")
        return page

    async def read_snapshot(self, run_id: str | None = None) -> tuple[str, dict[str, Any]]:
        """Extract interactive elements with stable refs (e1, e2...) clamped to <= 6000 chars."""
        page = await self.get_page(run_id)
        items: list[dict[str, Any]] = await page.evaluate(TAG_SCRIPT)
        ref_map: dict[str, Any] = {}
        lines: list[str] = [f"URL: {page.url}", f"Title: {await page.title()}", "---"]

        for item in items:
            ref = item.get("ref", "")
            role = item.get("role", "")
            name = item.get("name", "")
            val = item.get("value", "")
            href = item.get("href", "")

            if ref:
                ref_map[ref] = item
                label = f"{ref} [{role}]"
            else:
                label = f"[{role}]"

            if name:
                label += f" {name!r}"
            if val:
                label += f" (value: {val!r})"
            if href:
                label += f" -> {href}"
            lines.append(label)

        self._ref_cache = ref_map
        text = "\n".join(lines)
        if len(text) > MAX_TREE_CHARS:
            text = text[:MAX_TREE_CHARS] + "\n... [truncated to 6000 chars]"
        return text, ref_map

    async def click_ref(self, ref: str, run_id: str | None = None) -> None:
        page = await self.get_page(run_id)
        locator = page.locator(f'[data-clickachu-ref="{ref}"]')
        await locator.scroll_into_view_if_needed()
        await locator.click()

    async def fill_ref(self, ref: str, text: str, run_id: str | None = None) -> None:
        page = await self.get_page(run_id)
        locator = page.locator(f'[data-clickachu-ref="{ref}"]')
        await locator.scroll_into_view_if_needed()
        await locator.fill(text)

    async def get_submit_info(
        self, ref: str, run_id: str | None = None
    ) -> tuple[str, str, dict[str, str]]:
        """Return (url, element_label, form_values) for the approval preview."""
        page = await self.get_page(run_id)
        locator = page.locator(f'[data-clickachu-ref="{ref}"]')
        url = page.url
        info = self._ref_cache.get(ref, {})
        label = f"{info.get('role', 'element')} {info.get('name', ref)!r}"
        values: dict[str, str] = {}
        try:
            values = await locator.evaluate(FORM_VALUES_SCRIPT)
        except Exception as exc:  # noqa: BLE001
            log.debug("could not evaluate form values: %s", exc)
        return url, label, values

    async def submit_ref(self, ref: str, run_id: str | None = None) -> None:
        page = await self.get_page(run_id)
        locator = page.locator(f'[data-clickachu-ref="{ref}"]')
        await locator.scroll_into_view_if_needed()
        await locator.click()

    async def take_screenshot(self, run_id: str | None = None) -> str:
        page = await self.get_page(run_id)
        paths = get_paths()
        screenshots_dir = paths.root / "screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        fpath = screenshots_dir / f"screenshot_{ts}.png"
        await page.screenshot(path=str(fpath))
        return str(fpath)

    async def open_browser_window(self) -> None:
        page = await self.get_page()
        if page.url == "about:blank":
            await page.goto("https://www.google.com", wait_until="domcontentloaded")

    def run_coro(
        self, fn: Callable[..., Any], *args: Any, timeout: float = DEFAULT_TIMEOUT_S
    ) -> Any:
        self.start()
        assert self._loop is not None

        async def _call() -> Any:
            res = fn(*args)
            if inspect.isawaitable(res):
                return await res
            return res

        fut = asyncio.run_coroutine_threadsafe(_call(), self._loop)
        return fut.result(timeout=timeout)

    def shutdown(self, timeout: float = 3.0) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            loop = self._loop
            thread = self._thread
        if loop and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if thread and thread.is_alive():
            thread.join(timeout=timeout)


_worker = BrowserWorker()


def get_browser_worker() -> BrowserWorker:
    return _worker


async def run_browser(
    fn: Callable[..., Any], *args: Any, timeout: float = DEFAULT_TIMEOUT_S
) -> Any:
    """Bridge to execute browser coroutines on the browser worker's thread & event loop."""
    worker = get_browser_worker()
    worker.start()
    assert worker._loop is not None

    async def _call() -> Any:
        res = fn(*args)
        if inspect.isawaitable(res):
            return await res
        return res

    fut = asyncio.run_coroutine_threadsafe(_call(), worker._loop)
    return await asyncio.wait_for(asyncio.wrap_future(fut), timeout=timeout)


def shutdown_browser_worker() -> None:
    _worker.shutdown()
