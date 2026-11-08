"""headlines: saved RSS fixture -> rows, with httpx (Jina) and the openai SDK stubbed."""

import hashlib
import re
import tomllib
from pathlib import Path

import httpx
import pytest
from tenacity import RetryError

from resources import headlines

TOML = tomllib.loads((Path(__file__).resolve().parent.parent / "zeeker.toml").read_text())
HEADLINE_COLUMNS = set(TOML["resource"]["headlines"]["columns"])


def _jina_ok(request: httpx.Request) -> httpx.Response:
    assert request.url.host == "r.jina.ai"
    return httpx.Response(200, text=f"Full text for {request.url.path}")


@pytest.fixture
def llm_env(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://llm.test/v1")
    monkeypatch.setenv("LLM_MODEL", "stub-model")
    monkeypatch.setenv("JINA_API_TOKEN", "jina-test-token")


@pytest.fixture
def pipeline(llm_env, fresh_feed, patch_feedparser, mock_async_http, stub_openai, chat_completion):
    """Wire the whole headlines pipeline to the saved feed + fake Jina + fake LLM."""

    def _wire(jina=_jina_ok, llm=None):
        patch_feedparser(fresh_feed("headlines.xml"))
        jina_requests = mock_async_http(jina)
        llm_requests, _ = stub_openai(
            llm or (lambda req: httpx.Response(200, json=chat_completion("A tidy summary.")))
        )
        return jina_requests, llm_requests

    return _wire


async def test_fetch_data_rows_from_saved_feed(pipeline):
    jina_requests, llm_requests = pipeline()
    rows = await headlines.fetch_data(None)

    # 6 items in the fixture, 1 is an "ADV:" advertisement
    assert len(rows) == 5
    assert not any(r["title"].startswith("ADV") for r in rows)
    assert len(jina_requests) == 5 and len(llm_requests) == 5

    for row in rows:
        assert set(row) == HEADLINE_COLUMNS
        assert row["id"] == hashlib.md5(f"{row['date']}|{row['title']}".encode()).hexdigest()
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT00:01:00", row["date"])
        assert row["summary"] == "A tidy summary."
        assert row["text"].startswith("Full text for /")
        assert row["source_link"].startswith("https://www.singaporelawwatch.sg/Headlines/")
        assert row["category"] == "Straits Times"
        assert row["author"].startswith("Straits Times: ")
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT[\d:.]+", row["imported_on"])
        assert all(isinstance(v, str) for v in row.values())
    assert len({r["id"] for r in rows}) == 5


async def test_requests_carry_expected_headers_and_payload(pipeline):
    jina_requests, llm_requests = pipeline()
    await headlines.fetch_data(None)

    jina = jina_requests[0]
    assert jina.headers["authorization"] == "Bearer jina-test-token"
    assert jina.headers["x-target-selector"] == "article"
    assert jina.headers["x-retain-images"] == "none"
    assert str(jina.url).startswith("https://r.jina.ai/https://www.singaporelawwatch.sg/Headlines/")

    llm = llm_requests[0]
    assert llm.url == "http://llm.test/v1/chat/completions"
    # no real key configured -> the "ollama" placeholder is sent, never an OPENAI_API_KEY
    assert llm.headers["authorization"] == "Bearer ollama"


async def test_existing_ids_are_not_refetched(pipeline):
    from sqlite_utils import Database

    pipeline()
    first = await headlines.fetch_data(None)

    db = Database(memory=True)
    db["headlines"].insert_all(first, pk="id")
    jina_requests, llm_requests = pipeline()
    second = await headlines.fetch_data(db["headlines"])
    assert second == []
    assert jina_requests == [] and llm_requests == []


async def test_jina_http_errors_are_retried_then_all_dropped_with_blocked_skip(
    pipeline, no_retry_sleep
):
    from zeeker import Skip

    no_retry_sleep(headlines.get_jina_reader_content)
    jina_requests, llm_requests = pipeline(jina=lambda req: httpx.Response(503))
    with pytest.raises(Skip) as exc:
        await headlines.fetch_data(None)
    assert exc.value.kind == "blocked"
    assert len(jina_requests) == 5 * 3  # tenacity: 3 attempts per entry
    assert llm_requests == []


async def test_get_jina_reader_content_retries_until_success(
    llm_env, mock_async_http, no_retry_sleep
):
    no_retry_sleep(headlines.get_jina_reader_content)
    calls = iter([500, 502, 200])

    seen = mock_async_http(lambda req: httpx.Response(next(calls), text="ok body"))
    assert await headlines.get_jina_reader_content("https://x.test/a") == "ok body"
    assert len(seen) == 3


async def test_get_jina_reader_content_gives_up_after_three_attempts(
    llm_env, mock_async_http, no_retry_sleep
):
    no_retry_sleep(headlines.get_jina_reader_content)
    seen = mock_async_http(lambda req: httpx.Response(500))
    with pytest.raises(RetryError) as exc:
        await headlines.get_jina_reader_content("https://x.test/a")
    assert len(seen) == 3
    assert isinstance(exc.value.last_attempt.exception(), httpx.HTTPStatusError)


async def test_get_jina_reader_content_retries_transport_errors(
    llm_env, mock_async_http, no_retry_sleep
):
    no_retry_sleep(headlines.get_jina_reader_content)

    def boom(request):
        raise httpx.ConnectTimeout("slow", request=request)

    seen = mock_async_http(boom)
    with pytest.raises(RetryError) as exc:
        await headlines.get_jina_reader_content("https://x.test/a")
    assert len(seen) == 3
    assert isinstance(exc.value.last_attempt.exception(), httpx.ConnectTimeout)


async def test_get_jina_reader_content_without_token_makes_no_request(mock_async_http):
    seen = mock_async_http(lambda req: httpx.Response(200, text="never"))
    assert await headlines.get_jina_reader_content("https://x.test/a") == ""
    assert seen == []


async def test_fetch_article_text_truncates_and_sends_user_agent(mock_async_http):
    seen = mock_async_http(lambda req: httpx.Response(200, text="x" * 9000))
    text = await headlines._fetch_article_text("https://pub.test/article")
    assert len(text) == 8000
    assert "ZeekerBot" in seen[0].headers["user-agent"]


async def test_fetch_article_text_returns_empty_on_error(mock_async_http):
    mock_async_http(lambda req: httpx.Response(404))
    assert await headlines._fetch_article_text("https://pub.test/missing") == ""


async def test_backfill_fills_empty_summaries(
    llm_env, mock_async_http, stub_openai, chat_completion
):
    from sqlite_utils import Database

    mock_async_http(lambda req: httpx.Response(200, text="article body"))
    llm_requests, _ = stub_openai(
        lambda req: httpx.Response(200, json=chat_completion("Backfilled."))
    )
    db = Database(memory=True)
    db["headlines"].insert_all(
        [
            {"id": "a", "title": "A", "source_link": "https://pub.test/a", "summary": ""},
            {"id": "b", "title": "B", "source_link": "https://pub.test/b", "summary": "kept"},
        ],
        pk="id",
    )
    await headlines._backfill_empty_summaries(db["headlines"])
    summaries = {r["id"]: r["summary"] for r in db["headlines"].rows}
    assert summaries == {"a": "Backfilled.", "b": "kept"}
    assert len(llm_requests) == 1


def test_feed_date_parsing_and_advertisement_prefixes():
    assert headlines.convert_date_to_iso("08 May 2025 00:01:00") == "2025-05-08T00:01:00"
    assert headlines.convert_date_to_iso("08 Sep 2025 10:00:00") == "2025-09-08T10:00:00"
    # fullwidth-colon advertisement prefix used by the feed
    skip, reason = headlines._should_skip_entry(
        {"title": "ADV： promo", "published": "08 May 2025 00:01:00"},
        headlines.datetime(2025, 5, 9),
        None,
        set(),
    )
    assert (skip, reason) == (True, "advertisement")
