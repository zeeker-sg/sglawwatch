"""about_singapore_law: HTML fixtures through httpx.get (MockTransport) and BeautifulSoup."""

import hashlib
import re
import tomllib
from pathlib import Path

import httpx
import pytest
from zeeker import Skip

from resources import about_singapore_law as asl

TOML = tomllib.loads((Path(__file__).resolve().parent.parent / "zeeker.toml").read_text())
CHAPTER_COLUMNS = set(TOML["resource"]["about_singapore_law"]["columns"])
FRAGMENT_COLUMNS = set(TOML["resource"]["about_singapore_law_fragments"]["columns"])

OVERVIEW = "https://www.singaporelawwatch.sg/About-Singapore-Law/Overview"
CH1 = (
    "http://www.singaporelawwatch.sg/About-Singapore-Law/Overview/ch-01-the-singapore-legal-system"
)
EMPTY_SECTION = b'<html><body><div class="edn_mainWrapper"></div></body></html>'


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(asl.time, "sleep", lambda *_: None)


@pytest.fixture
def site(fixture_bytes, mock_sync_http):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == OVERVIEW:
            return httpx.Response(200, content=fixture_bytes("section_overview.html"))
        if url.rstrip("/").endswith(("Commercial-Law", "Singapore-Legal-System")):
            return httpx.Response(200, content=EMPTY_SECTION)
        if "/Overview/ch-" in url:
            return httpx.Response(200, content=fixture_bytes("chapter.html"))
        return httpx.Response(404)

    return mock_sync_http(handler)


def test_discover_chapter_links_parses_section_page(site):
    links = asl.discover_chapter_links(OVERVIEW, "Overview")
    assert [link["title"] for link in links] == [
        "Ch. 01 The Singapore Legal System",
        "Ch. 02 Civil Procedure",
        "Ch. 03 Mediation",
        "Ch. 04 International and Domestic Arbitration in Singapore",
    ]
    first = links[0]
    assert set(first) == CHAPTER_COLUMNS
    assert first["id"] == hashlib.md5(CH1.encode()).hexdigest()[:12]
    assert first["item_url"] == CH1
    assert first["section"] == first["home_page"] == "Overview"
    assert first["content_length"] == 0
    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", first["last_scraped"])


def test_fetch_data_discovers_across_sections_and_filters_known(site):
    rows = asl.fetch_data(None)
    assert len(rows) == 4 and {r["section"] for r in rows} == {"Overview"}
    assert len(site) == 3  # one request per section page

    known_url = rows[0]["item_url"]

    class Known:
        rows = [{"item_url": known_url}]

    assert len(asl.fetch_data(Known())) == 3


def test_section_http_error_is_counted_and_all_failing_is_a_blocked_skip(mock_sync_http):
    mock_sync_http(lambda req: httpx.Response(503))
    with pytest.raises(Skip) as exc:
        asl.fetch_data(None)
    assert exc.value.kind == "blocked"
    assert "HTTPStatusError" in exc.value.reason


def test_section_connect_error_is_a_blocked_skip(mock_sync_http):
    def boom(request):
        raise httpx.ConnectError("proxy down", request=request)

    mock_sync_http(boom)
    with pytest.raises(Skip, match="ConnectError"):
        asl.fetch_data(None)


def test_scrape_chapter_content_extracts_typed_parts(site):
    parts = asl.scrape_chapter_content(
        "https://www.singaporelawwatch.sg/About-Singapore-Law/Overview/ch-01"
    )
    types = [p["type"] for p in parts]
    assert types[0] == "heading" and parts[0]["text"] == "Ch. 01 The Singapore Legal System"
    assert {"heading", "paragraph", "table", "list"} <= set(types)
    table = next(p for p in parts if p["type"] == "table")
    assert table["text"] == "Court | Jurisdiction\nHigh Court | Original and appellate"
    lst = next(p for p in parts if p["type"] == "list")
    assert lst["text"].splitlines()[0].startswith("• the nature of the factual")  # <ol> -> bullet
    assert len(lst["text"].splitlines()) == 4
    # footer ("Updated as at", "By:", disclaimer) is cut off
    joined = "\n".join(p["text"] for p in parts)
    assert "Updated as at" not in joined and "Disclaimer" not in joined
    assert "1.8.6" in joined  # last numbered paragraph before the footer is kept


def test_scrape_chapter_content_http_error_raises(mock_sync_http):
    mock_sync_http(lambda req: httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        asl.scrape_chapter_content("https://www.singaporelawwatch.sg/x")


def test_scrape_chapter_content_without_article_raises(mock_sync_http):
    mock_sync_http(lambda req: httpx.Response(200, content=b"<html><body>nothing</body></html>"))
    with pytest.raises(IndexError):
        asl.scrape_chapter_content("https://www.singaporelawwatch.sg/x")


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Real bug (not fixed here): section pages emit http:// chapter links and the live site "
        "answers them with 301 -> https; httpx.get does not follow redirects, so every chapter "
        "scrape fails with HTTPStatusError. Remove this marker once follow_redirects is enabled."
    ),
)
def test_http_chapter_url_that_redirects_to_https_can_be_scraped(fixture_bytes, mock_sync_http):
    def handler(request):
        if request.url.scheme == "http":
            return httpx.Response(
                301, headers={"location": str(request.url).replace("http:", "https:", 1)}
            )
        return httpx.Response(200, content=fixture_bytes("chapter.html"))

    mock_sync_http(handler)
    assert asl.scrape_chapter_content(CH1)


def test_fetch_fragments_data_end_to_end_row_shape(site):
    chapters = asl.fetch_data(None)[:2]
    frags = asl.fetch_fragments_data(None, chapters)

    assert frags
    for f in frags:
        assert set(f) == FRAGMENT_COLUMNS
        assert f["char_count"] == len(f["content_text"])
    for chapter in chapters:
        mine = [f for f in frags if f["item_id"] == chapter["id"]]
        assert [f["fragment_order"] for f in mine] == list(range(len(mine)))
        assert [f["id"].removeprefix(chapter["id"] + "_") for f in mine] == [
            "1.1.1",
            "1.1.2",
            "1.1.3",
            "1.8.5",
            "1.8.6",
        ]
    # table is attached backward to 1.1.2; list attached backward to 1.8.5
    by_num = {f["id"].split("_", 1)[1]: f["content_text"] for f in frags[:5]}
    assert "High Court | Original and appellate" in by_num["1.1.2"]
    assert "• whether, having regard" in by_num["1.8.5"]
    # "C. Legal Education" is a bold <p> directly after a list, so the grouping rules glue it
    # backward onto the list's fragment rather than forward onto 1.8.6 (current heuristic)
    assert by_num["1.8.5"].endswith("C. Legal Education")
    assert by_num["1.8.6"].startswith("1.8.6")


def test_fetch_fragments_skips_failed_and_contentless_chapters(mock_sync_http):
    def handler(request):
        url = str(request.url)
        if url.endswith("bad"):
            return httpx.Response(500)
        if url.endswith("empty"):
            return httpx.Response(200, content=b'<article class="edn_article"></article>')
        return httpx.Response(404)

    mock_sync_http(handler)
    chapters = [
        {"id": "aaa", "item_url": "https://www.singaporelawwatch.sg/bad"},
        {"id": "bbb", "item_url": "https://www.singaporelawwatch.sg/empty"},
    ]
    assert asl.fetch_fragments_data(None, chapters) == []
    assert asl.fetch_fragments_data(None, None) == []


def test_fragments_not_reemitted_when_already_stored(site):
    chapters = asl.fetch_data(None)[:1]
    first = asl.fetch_fragments_data(None, chapters)

    class Stored:
        rows = [{"id": f["id"]} for f in first[:2]]

    again = asl.fetch_fragments_data(Stored(), chapters)
    assert [f["id"] for f in again] == [f["id"] for f in first[2:]]
