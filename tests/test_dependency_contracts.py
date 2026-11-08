"""Direct smoke tests for third-party behaviour the resources rely on.

Each test is tiny and names the dependency it protects, so a Dependabot bump
that changes behaviour fails with an obvious message.
"""

import httpx
import pytest


def test_feedparser_parses_nonstandard_rss_dates(fixture_bytes):
    import feedparser

    feed = feedparser.parse(fixture_bytes("headlines.xml"))
    assert not feed.bozo
    first = feed.entries[0]
    # The SLW feeds use "06 Nov 2026 00:01:00" (not RFC 822). commentaries.parse_pub_date
    # silently falls back to "now" if feedparser stops understanding it.
    assert first["published"] == "06 Nov 2026 00:01:00"
    assert tuple(first.published_parsed[:6]) == (2026, 11, 6, 0, 1, 0)
    assert first["author"] == "Straits Times: Selina Lum"
    assert first["category"] == "Straits Times"
    assert first["link"].startswith("https://www.singaporelawwatch.sg/Headlines/")
    assert first["title"]


def test_feedparser_entry_get_and_attribute_access(fixture_bytes):
    import feedparser

    entry = feedparser.parse(fixture_bytes("commentaries.xml")).entries[0]
    assert entry.get("missing", "dflt") == "dflt"
    assert hasattr(entry, "published_parsed")
    assert entry.get("summary", "").startswith("<p>")


def test_beautifulsoup_html_parser_selectors(fixture_bytes):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(fixture_bytes("chapter.html"), "html.parser")
    article = soup.select(".edn_article")[0]
    names = {el.name for el in article.find_all(["p", "table", "ul", "ol", "div", "h1"])}
    assert {"h1", "p", "table", "ol"} <= names
    assert article.find("table").find_parent("table") is None


def test_httpx_mock_transport_sync_and_async():
    def handler(request):
        return httpx.Response(200, text=request.url.raw_path.decode())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert client.get("https://example.test/a b.pdf").text == "/a%20b.pdf"

    import asyncio

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return (await client.get("https://example.test/x")).text

    assert asyncio.run(go()) == "/x"


def test_httpx_raise_for_status_treats_redirects_as_errors():
    resp = httpx.Response(
        301,
        headers={"location": "https://example.test/"},
        request=httpx.Request("GET", "http://example.test/"),
    )
    with pytest.raises(httpx.HTTPStatusError):
        resp.raise_for_status()


def test_httpx_socks_extra_is_installed():
    # headlines.get_summary builds httpx.AsyncClient(proxy=socks5h://...) for the Tailscale route;
    # that needs the httpx[socks] extra (socksio).
    import socksio  # noqa: F401

    httpx.AsyncClient(proxy=httpx.Proxy("socks5h://127.0.0.1:1080"))


def test_httpx_multipart_files_and_data_encoding():
    seen = {}

    def handler(request):
        seen["ctype"] = request.headers["content-type"]
        seen["body"] = request.read()
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        client.post(
            "https://example.test/upload",
            files=[("files", ("document.pdf", b"%PDF-1.4", "application/pdf"))],
            data={"to_formats": "md"},
        )
    assert seen["ctype"].startswith("multipart/form-data; boundary=")
    assert b'name="files"; filename="document.pdf"' in seen["body"]
    assert b'name="to_formats"' in seen["body"]


def test_sqlite_utils_and_zeeker_skip_api():
    import sqlite_utils
    from zeeker import Skip

    skip = Skip("why", kind="blocked")
    assert skip.kind == "blocked" and skip.reason == "why"
    db = sqlite_utils.Database(memory=True)
    db["t"].insert_all([{"id": "a", "n": 1}], pk="id")
    assert list(db["t"].rows) == [{"id": "a", "n": 1}]
