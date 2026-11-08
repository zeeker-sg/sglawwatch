"""commentaries: saved RSS fixture, HTTP layer (Jina + docling) and row shape."""

import hashlib
import json
import re
import tomllib
from pathlib import Path

import httpx
import pytest
from tenacity import RetryError

from resources import commentaries

TOML = tomllib.loads((Path(__file__).resolve().parent.parent / "zeeker.toml").read_text())
COMMENTARY_COLUMNS = set(TOML["resource"]["commentaries"]["columns"])
FRAGMENT_COLUMNS = set(TOML["resource"]["commentaries_fragments"]["columns"])

PDF_MD = "# Corporate law changes\n\nSome converted markdown."


def _router(request: httpx.Request) -> httpx.Response:
    if request.url.host == "r.jina.ai":
        return httpx.Response(200, text="# Jina markdown for " + request.url.path[1:])
    if request.url.path.endswith(".pdf"):
        return httpx.Response(200, content=b"%PDF-1.7 fake")
    if request.url.path == "/v1/convert/file":
        return httpx.Response(200, json={"document": {"md_content": PDF_MD}})
    return httpx.Response(404)


@pytest.fixture(autouse=True)
def _fast_retries(no_retry_sleep):
    no_retry_sleep(commentaries.extract_via_jina, commentaries.extract_via_docling)


@pytest.fixture
def feed(fixture_bytes, patch_feedparser):
    patch_feedparser(fixture_bytes("commentaries.xml"))


async def test_fetch_data_rows(feed, mock_async_http):
    seen = mock_async_http(_router)
    rows = await commentaries.fetch_data(None)

    assert len(rows) == 4
    assert {r["content_type"] for r in rows} == {"web", "pdf"}
    for row in rows:
        assert set(row) == COMMENTARY_COLUMNS
        assert row["id"] == hashlib.md5(row["link"].encode()).hexdigest()
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", row["pub_date"])
        assert row["title"] and row["author"] and row["description"].startswith("<p>")
        assert row["full_text"]
        assert all(isinstance(v, str) for v in row.values())

    first = next(r for r in rows if r["author"] == "Allen & Gledhill")
    # feedparser understood the feed's "06 Nov 2026 10:30:00" date format
    assert first["pub_date"] == "2026-11-06T10:30:00"
    assert first["full_text"].startswith("# Jina markdown for https://www.allenandgledhill.com/")
    pdf = next(r for r in rows if r["content_type"] == "pdf")
    assert pdf["full_text"] == PDF_MD
    assert any(r.url.host == "localhost" and r.url.port == 5001 for r in seen)


async def test_existing_links_are_skipped(feed, mock_async_http):
    from sqlite_utils import Database

    mock_async_http(_router)
    first = await commentaries.fetch_data(None)
    db = Database(memory=True)
    db["commentaries"].insert_all(first, pk="id")

    seen = mock_async_http(_router)
    assert await commentaries.fetch_data(db["commentaries"]) == []
    assert seen == []


async def test_jina_request_shape(mock_async_http, monkeypatch):
    seen = mock_async_http(lambda req: httpx.Response(200, text="body"))
    assert await commentaries.extract_via_jina("https://firm.test/a?x=1") == "body"
    assert str(seen[0].url) == "https://r.jina.ai/https://firm.test/a?x=1"
    assert seen[0].headers["x-retain-images"] == "none"
    assert "authorization" not in seen[0].headers

    monkeypatch.setenv("JINA_API_TOKEN", "tok")
    seen = mock_async_http(lambda req: httpx.Response(200, text="body"))
    await commentaries.extract_via_jina("https://firm.test/a")
    assert seen[0].headers["authorization"] == "Bearer tok"


async def test_jina_retries_then_succeeds(mock_async_http):
    statuses = iter([429, 500, 200])
    seen = mock_async_http(lambda req: httpx.Response(next(statuses), text="finally"))
    assert await commentaries.extract_via_jina("https://firm.test/a") == "finally"
    assert len(seen) == 3


async def test_jina_gives_up_after_three_attempts(mock_async_http):
    seen = mock_async_http(lambda req: httpx.Response(500))
    with pytest.raises(RetryError):
        await commentaries.extract_via_jina("https://firm.test/a")
    assert len(seen) == 3


async def test_docling_download_then_multipart_upload(mock_async_http, monkeypatch):
    monkeypatch.setattr(commentaries, "DOCLING_API_KEY", "dk")
    seen = mock_async_http(_router)
    url = (
        "https://www.singaporelawwatch.sg/Portals/0/Commentaries/Rajah Tann/2611-01 Law Changes.pdf"
    )
    assert await commentaries.extract_via_docling(url) == PDF_MD

    download, upload = seen
    assert download.method == "GET"
    assert download.url.raw_path.decode().endswith("Rajah%20Tann/2611-01%20Law%20Changes.pdf")
    assert upload.method == "POST" and upload.url.path == "/v1/convert/file"
    assert upload.headers["authorization"] == "Bearer dk"
    assert upload.headers["content-type"].startswith("multipart/form-data")
    body = upload.read()
    assert b'name="files"; filename="document.pdf"' in body
    assert b"%PDF-1.7 fake" in body
    assert b'name="to_formats"' in body and b"\r\n\r\nmd\r\n" in body
    assert b'name="image_export_mode"' in body and b"placeholder" in body


@pytest.mark.parametrize(
    "payload",
    [
        {"document": {"md_content": "A"}},
        {"output": {"content": "A"}},
        {"content": "A"},
    ],
)
async def test_docling_response_shapes(mock_async_http, payload):
    def handler(req):
        if req.url.path == "/v1/convert/file":
            return httpx.Response(200, json=payload)
        return httpx.Response(200, content=b"pdf")

    mock_async_http(handler)
    assert await commentaries.extract_via_docling("https://x.test/a.pdf") == "A"


async def test_docling_empty_response_gives_empty_text(mock_async_http):
    def handler(req):
        if req.url.path == "/v1/convert/file":
            return httpx.Response(200, json=json.loads("{}"))
        return httpx.Response(200, content=b"pdf")

    mock_async_http(handler)
    assert await commentaries.extract_via_docling("https://x.test/a.pdf") == ""


async def test_docling_failure_is_retried_and_extract_content_degrades(mock_async_http):
    def handler(req):
        if req.url.path == "/v1/convert/file":
            return httpx.Response(504)
        return httpx.Response(200, content=b"pdf")

    seen = mock_async_http(handler)
    assert await commentaries.extract_content("https://x.test/a.PDF?dl=1") == ("pdf", "")
    assert len([r for r in seen if r.method == "POST"]) == 3


async def test_jina_failure_degrades_to_empty_web_text(mock_async_http):
    mock_async_http(lambda req: httpx.Response(500))
    assert await commentaries.extract_content("https://x.test/page") == ("web", "")


async def test_failed_extraction_still_stores_row_and_fragment_falls_back(feed, mock_async_http):
    mock_async_http(lambda req: httpx.Response(500))
    rows = await commentaries.fetch_data(None)
    assert len(rows) == 4 and all(r["full_text"] == "" for r in rows)

    fragments = commentaries.fetch_fragments_data(None, rows)
    assert len(fragments) == 4
    by_id = {r["id"]: r for r in rows}
    for frag in fragments:
        assert set(frag) == FRAGMENT_COLUMNS
        assert frag["fragment_index"] == 0
        assert frag["text"] == by_id[frag["commentary_id"]]["description"]


async def test_one_bad_entry_does_not_abort_the_batch(feed, mock_async_http, monkeypatch):
    mock_async_http(_router)

    async def flaky(url):
        if "allenandgledhill" in url:
            raise RuntimeError("kaboom")
        return "web", "ok"

    monkeypatch.setattr(commentaries, "extract_content", flaky)
    rows = await commentaries.fetch_data(None)
    # both allenandgledhill.com entries blow up and are dropped; the others are kept
    assert len(rows) == 2 and all("allenandgledhill" not in r["link"] for r in rows)


def test_parse_pub_date_prefers_feedparser_then_strptime_then_now():
    from datetime import datetime

    import feedparser

    entry = feedparser.FeedParserDict({"published_parsed": (2026, 11, 6, 10, 30, 0, 0, 0, 0)})
    assert commentaries.parse_pub_date(entry) == "2026-11-06T10:30:00"
    assert (
        commentaries.parse_pub_date({"published": "Fri, 06 Nov 2026 10:30:00 +0800"})
        == "2026-11-06T10:30:00+08:00"
    )
    assert commentaries.parse_pub_date({"published": "06 Nov 2026"}) == "2026-11-06T00:00:00"
    assert datetime.fromisoformat(commentaries.parse_pub_date({"published": "garbage"}))


def test_is_pdf_detection():
    assert commentaries.is_pdf("https://x.test/a.PDF")
    assert commentaries.is_pdf("https://x.test/a.pdf?download=1")
    assert not commentaries.is_pdf("https://x.test/pdf/page")


@pytest.mark.xfail(
    strict=True,
    reason="Real bug (not fixed here): fetch_data only dedups against the table, not within a batch.",
)
async def test_duplicate_links_within_one_feed_are_collapsed(patch_feedparser, mock_async_http):
    """The live feed repeats some links; ids are link hashes, so rows must not collide.

    Observed in the real Commentaries.xml (an allenandgledhill.com article listed twice).
    """
    item = (
        "<item><title>T</title><link>https://firm.test/a</link><description>d</description>"
        "<author>F</author><pubDate>06 Nov 2026 10:30:00</pubDate></item>"
    )
    patch_feedparser(f"<rss version='2.0'><channel>{item}{item}</channel></rss>".encode())
    mock_async_http(_router)
    rows = await commentaries.fetch_data(None)
    assert len({r["id"] for r in rows}) == len(rows)
