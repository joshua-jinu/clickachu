"""Background public-web retrieval. No browser profile, login, or CAPTCHA automation."""

import ipaddress
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html import unescape
from html.parser import HTMLParser
from typing import Literal

from pydantic import BaseModel, Field

from clickachu.context.redact import redact
from clickachu.tools.base import Tier, Tool, ToolContext, ToolResult

MAX_BYTES = 1_000_000
_CACHE: dict[tuple[str, str, int | None, bool], tuple[float, list[dict[str, str]]]] = {}
_PAGES: dict[str, tuple[float, ToolResult]] = {}


def blocked_page(url: str, text: str) -> bool:
    path = urllib.parse.urlsplit(url).path.lower()
    body = text.lower()
    return (
        "/sorry/" in path
        or "unusual traffic from your computer network" in body
        or "verify you are human" in body
        or "our systems have detected unusual traffic" in body
        or "anomaly.js" in body
    )


def _public_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username:
        raise ValueError("Only public HTTP/HTTPS pages are supported.")
    for address in socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(address[4][0]).is_global:
            raise ValueError("Background web retrieval cannot access private network addresses.")
    return url


class _Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(url: str) -> tuple[str, str]:
    _public_url(url)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; Clickachu/0.1)",
            "Accept": "text/html,application/rss+xml,application/xml",
        },
    )
    with urllib.request.build_opener(_Redirects()).open(request, timeout=7) as response:
        final_url = response.url
        content = response.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            content = content[:MAX_BYTES]
        text = content.decode(response.headers.get_content_charset() or "utf-8", "replace")
        if response.status != 200 or blocked_page(final_url, text):
            raise ValueError(
                "The source blocked automated access; no usable results were retrieved."
            )
        return final_url, text


class _Text(HTMLParser):
    def __init__(self, page=False):
        super().__init__()
        self.parts = []
        self.hidden = 0
        self.page = page
        self.main_depth = 0
        self.main_parts = []
        self.descriptions = []
        self.hidden_tags = {"script", "style", "noscript"}
        if page:
            self.hidden_tags.update(("nav", "header", "footer", "svg"))

    def handle_starttag(self, tag, attrs):
        if tag in self.hidden_tags:
            self.hidden += 1
        if tag in ("main", "article"):
            self.main_depth += 1
        attributes = dict(attrs)
        if (
            self.page
            and tag == "meta"
            and (
                attributes.get("name", "").casefold() == "description"
                or attributes.get("property", "").casefold() == "og:description"
            )
        ):
            self.descriptions.append(attributes.get("content", ""))

    def handle_endtag(self, tag):
        if tag in self.hidden_tags:
            self.hidden = max(0, self.hidden - 1)
        if tag in ("main", "article"):
            self.main_depth = max(0, self.main_depth - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)
            if self.main_depth:
                self.main_parts.append(data)


def plain_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    return " ".join(" ".join(parser.parts).split())


def page_text(html: str) -> str:
    parser = _Text(page=True)
    parser.feed(html)
    parts = list(dict.fromkeys(parser.descriptions)) + (parser.main_parts or parser.parts)
    return " ".join(" ".join(parts).split())


def _rss_results(url: str, *, news: bool = False) -> list[dict[str, str]]:
    _, xml = _download(url)
    root = ET.fromstring(xml)
    results = []
    for item in root.findall("./channel/item")[:20]:
        published = item.findtext("pubDate") or "Not supplied"
        results.append(
            {
                "title": plain_text(unescape(item.findtext("title") or "")),
                "url": (item.findtext("link") or "").strip(),
                "snippet": plain_text(unescape(item.findtext("description") or ""))[:450],
                "published": published if news else "Not supplied (search indexing date omitted)",
                "provider": (
                    "Google News" if "news.google.com" in url else "Bing News" if news else "Bing"
                ),
            }
        )
    return results


def _encyclopedia_results(query: str) -> list[dict[str, str]]:
    params = urllib.parse.urlencode(
        {"action": "query", "list": "search", "srsearch": query, "srlimit": 5, "format": "json"}
    )
    _, body = _download("https://en.wikipedia.org/w/api.php?" + params)
    return [
        {
            "title": item["title"],
            "url": "https://en.wikipedia.org/wiki/"
            + urllib.parse.quote(item["title"].replace(" ", "_")),
            "snippet": plain_text(item.get("snippet", ""))[:450],
            "published": "Not supplied",
            "provider": "Wikipedia search",
        }
        for item in json.loads(body).get("query", {}).get("search", [])
    ]


class _LiteResults(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self.capture = ""
        self.parts = []
        self.url = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and "result-link" in attrs.get("class", "").split():
            self.capture, self.parts = "title", []
            link = urllib.parse.urljoin("https://duckduckgo.com", attrs.get("href", ""))
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)
            self.url = query.get("uddg", [link])[0]
        elif tag == "td" and "result-snippet" in attrs.get("class", "").split():
            self.capture, self.parts = "snippet", []

    def handle_data(self, data):
        if self.capture:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if self.capture == "title" and tag == "a":
            self.results.append(
                {
                    "title": " ".join(" ".join(self.parts).split()),
                    "url": self.url,
                    "snippet": "",
                    "published": "Not supplied",
                    "provider": "DuckDuckGo",
                }
            )
            self.capture = ""
        elif self.capture == "snippet" and tag == "td":
            if self.results:
                self.results[-1]["snippet"] = " ".join(" ".join(self.parts).split())[:450]
            self.capture = ""


def _lite_results(query: str) -> list[dict[str, str]]:
    _, html = _download("https://lite.duckduckgo.com/lite/?q=" + urllib.parse.quote_plus(query))
    parser = _LiteResults()
    parser.feed(html)
    return parser.results


def _duck_results(query, recent=False, days=None):
    # An explicitly selected backend prevents a library from trying unrelated engines.
    from ddgs import DDGS
    from ddgs.engines import ENGINES

    def fallback():
        if recent:
            terms = query + f" when:{days or 7}d"
            return _rss_results(
                "https://www.bing.com/news/search?format=rss&q="
                + urllib.parse.quote_plus(terms),
                news=True,
            )
        return _lite_results(query)

    if "duckduckgo" not in ENGINES.get("news" if recent else "text", {}):
        return fallback()
    try:
        client = DDGS(timeout=4)
        search = client.news if recent else client.text
        time_limit = (
            ("d" if days == 1 else "w" if (days or 7) <= 7 else "m" if days <= 31 else "y")
            if recent
            else ("d" if days == 1 else None)
        )
        entries = search(query, backend="duckduckgo", max_results=5, timelimit=time_limit)
        return [
            {
                "title": e.get("title", ""),
                "url": e.get("href", e.get("url", "")),
                "snippet": e.get("body", e.get("excerpt", ""))[:450],
                "published": e.get("date", "Not supplied"),
                "provider": "DuckDuckGo",
            }
            for e in entries
        ]
    except Exception:  # noqa: BLE001 - the maintained transport has provider-specific failures.
        return fallback()


def search_web(
    query: str,
    *,
    recent: bool = False,
    provider: str = "auto",
    days: int | None = None,
) -> list[dict[str, str]]:
    """Transport only: the research agent owns query meaning, relevance, and retries."""
    query = redact(query).strip()
    provider = "duckduckgo" if provider == "auto" else provider
    key = (query, provider, days, recent)
    cached = _CACHE.get(key)
    if cached and time.monotonic() - cached[0] < (60 if recent or provider == "news" else 300):
        return cached[1]
    if provider == "wikipedia":
        results = _encyclopedia_results(query)
    elif provider == "news":
        terms = query + (f" when:{days}d" if days else "")
        results = _rss_results(
            "https://www.bing.com/news/search?format=rss&q=" + urllib.parse.quote_plus(terms),
            news=True,
        )
    elif provider == "bing":
        results = _rss_results(
            "https://www.bing.com/search?format=rss&q=" + urllib.parse.quote_plus(query)
        )
    elif provider == "duckduckgo":
        results = _duck_results(query, recent, days)
    else:
        raise ValueError("Unknown search provider")
    results = [
        r
        for r in results
        if r["title"]
        and r["url"].startswith(("http://", "https://"))
        and not blocked_page(r["url"], r["snippet"])
    ][:5]
    if not results:
        raise ValueError("No results returned. Refine the query or choose another source.")
    if len(_CACHE) >= 64:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = (time.monotonic(), results)
    return results


class WebSearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=500, description="Public web search terms")
    recent: bool = Field(default=False, description="Prefer news sources")
    provider: Literal["auto", "bing", "wikipedia", "news", "duckduckgo"] = "auto"
    days: int | None = Field(default=None, ge=1, le=3650, description="Optional news time window")


class WebSearchTool(Tool[WebSearchArgs]):
    name = "web_search"
    description = "Search public web sources in the background without opening a browser."
    Args = WebSearchArgs
    tier = Tier.READ
    timeout_s = 25.0

    def preview(self, args):
        return f"Look up {args.query!r} in the background"

    def run(self, args, ctx: ToolContext):
        try:
            results = search_web(
                args.query, recent=args.recent, provider=args.provider, days=args.days
            )
            evidence = "\n\n".join(
                f"[{i}] {r['title']}\nURL: {r['url']}\n"
                f"Source publication date (not necessarily event date): {r['published']}\n"
                f"{r['snippet']}"
                for i, r in enumerate(results[:3], 1)
            )
            return ToolResult(
                ok=True,
                data={"results": results},
                untrusted_text=evidence,
                summary=(
                    f"Retrieved {len(results)} candidate results for {args.query!r} "
                    f"via {results[0]['provider']} in the background."
                ),
            )
        except (OSError, ValueError, ET.ParseError) as exc:
            return ToolResult(
                ok=False,
                summary=(
                    f"Background search unavailable: {exc} "
                    "I could not verify this online; this does not mean the topic does not exist."
                ),
            )

    def verify(self, args, result):
        # Retrieval succeeded, but relevance and factual support require the research controller.
        return None


class WebFetchArgs(BaseModel):
    url: str = Field(min_length=1, description="Public source URL from web search results")


class WebFetchTool(Tool[WebFetchArgs]):
    name = "web_fetch"
    description = "Read public page text in the background; no interactive browser or login."
    Args = WebFetchArgs
    tier = Tier.READ
    timeout_s = 12.0

    def run(self, args, ctx):
        cached = _PAGES.get(args.url)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
        try:
            url, html = _download(args.url)
            import trafilatura

            text = (
                trafilatura.extract(
                    html,
                    url=url,
                    include_comments=False,
                    include_tables=False,
                    favor_precision=True,
                )
                or page_text(html)
            )[:5000]
            if not text:
                raise ValueError("The source returned no readable page text.")
            result = ToolResult(
                ok=True,
                data={"url": url},
                summary=f"Read {url}",
                untrusted_text=f"URL: {url}\n{text}",
            )
            if len(_PAGES) >= 64:
                _PAGES.pop(next(iter(_PAGES)))
            _PAGES[args.url] = (time.monotonic(), result)
            return result
        except (OSError, ValueError) as exc:
            return ToolResult(ok=False, summary=f"Could not read this source: {exc}")
