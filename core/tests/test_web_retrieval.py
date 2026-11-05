import json
from email.utils import formatdate
from urllib.parse import parse_qs, urlsplit

import pytest

from clickachu.tools import web_tools as web


@pytest.fixture(autouse=True)
def reset_cache(monkeypatch):
    monkeypatch.setattr(web, "_CACHE", {})


def feed(title, snippet, published=None):
    return (
        "<rss><channel><item><title>" + title + "</title>"
        "<link>https://example.com/article</link><description>"
        + snippet
        + "</description>"
        + ("<pubDate>" + published + "</pubDate>" if published else "")
        + "</item></channel></rss>"
    )


@pytest.mark.parametrize(
    "query",
    [
        "how old is Ada Lovelace?",
        "independent chip benchmarks",
        "a one-word topic",
        "unknown subject",
    ],
)
def test_transport_preserves_model_query_without_topic_rewrites(monkeypatch, query):
    calls = []

    def download(url):
        calls.append(url)
        return url, feed("Candidate", "Evidence for the controller to assess")

    monkeypatch.setattr(web, "_download", download)
    web.search_web(query, provider="bing")
    assert parse_qs(urlsplit(calls[0]).query)["q"] == [query]


def test_selected_provider_and_query_are_preserved(monkeypatch):
    calls = []

    def download(url):
        calls.append(url)
        return url, json.dumps({"query": {"search": [{"title": "Topic", "snippet": "Evidence"}]}})

    monkeypatch.setattr(web, "_download", download)
    results = web.search_web("specific missing evidence", provider="wikipedia")
    assert results[0]["provider"] == "Wikipedia search"
    assert parse_qs(urlsplit(calls[0]).query)["srsearch"] == ["specific missing evidence"]


@pytest.mark.parametrize("days", [None, 1, 30, 365])
def test_time_window_is_chosen_by_controller(monkeypatch, days):
    calls = []

    def download(url):
        calls.append(url)
        return url, feed("Report", "Evidence", formatdate(usegmt=True))

    monkeypatch.setattr(web, "_download", download)
    web.search_web("regional industry reports", provider="news", days=days)
    query = parse_qs(urlsplit(calls[0]).query)["q"][0]
    assert query == "regional industry reports" + (f" when:{days}d" if days else "")


def test_empty_and_blocked_sources_are_observations_for_agent(monkeypatch):
    monkeypatch.setattr(web, "_download", lambda url: (url, "<rss><channel/></rss>"))
    result = web.WebSearchTool().run(web.WebSearchArgs(query="any subject"), None)
    assert not result.ok
    assert "No results" in result.summary
    assert not web.WebSearchTool().verify(None, result)


def test_duckduckgo_lite_preserves_names_and_parses_redirects_and_snippets(monkeypatch):
    page = """<table><tr><td><a class="result-link"
        href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fprovident-sunworth">
        Provident <b>Sunworth</b></a></td></tr>
        <tr><td class="result-snippet">A housing project &amp; community.</td></tr></table>"""
    monkeypatch.setattr(web, "_download", lambda url: (url, page))
    result = web.search_web("provident sunworth", provider="duckduckgo")[0]
    assert result["title"] == "Provident Sunworth"
    assert result["url"] == "https://example.com/provident-sunworth"
    assert result["snippet"] == "A housing project & community."
    assert web.WebSearchTool().verify(None, None) is None


def test_page_reader_prefers_content_and_omits_navigation_and_scripts():
    html = """<meta name="description" content="Public project overview.">
    <header>Global menu</header><nav>Hundreds of unrelated links</nav>
    <main><h1>Example Topic</h1><p>Relevant source facts.</p><script>ignore this</script></main>
    <footer>Contact menu</footer>"""
    assert web.page_text(html) == "Public project overview. Example Topic Relevant source facts."
