"""Shared helpers for the offline resource tests."""

import re
import tomllib
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@pytest.fixture(scope="session")
def zeeker_toml() -> dict:
    return tomllib.loads((ROOT / "zeeker.toml").read_text())


@pytest.fixture
def fixture_bytes():
    def _read(name: str) -> bytes:
        return (FIXTURES / name).read_bytes()

    return _read


@pytest.fixture
def fresh_feed():
    """Return fixture RSS bytes with every pubDate rewritten to a few days ago.

    headlines drops entries older than 60 days relative to ``datetime.now()``,
    so the saved feed's dates are rebased to keep the tests date-independent.
    """

    def _make(name: str) -> bytes:
        text = (FIXTURES / name).read_text(encoding="utf-8")
        counter = iter(range(1, 1000))

        def _sub(_match):
            day = datetime.now() - timedelta(days=next(counter))
            return f"<pubDate>{day.day:02d} {_MONTHS[day.month - 1]} {day.year} 00:01:00</pubDate>"

        return re.sub(r"<pubDate>.*?</pubDate>", _sub, text).encode("utf-8")

    return _make


@pytest.fixture
def patch_feedparser(monkeypatch):
    """Make ``feedparser.parse(<url>)`` parse the given bytes instead of fetching."""
    import feedparser

    real_parse = feedparser.parse

    def _install(payload: bytes):
        monkeypatch.setattr(feedparser, "parse", lambda *_a, **_k: real_parse(payload))

    return _install


@pytest.fixture
def mock_async_http(monkeypatch):
    """Route every ``httpx.AsyncClient`` created by resources through a MockTransport.

    Returns ``install(handler) -> list[httpx.Request]`` (the recorded requests).
    """
    real_client = httpx.AsyncClient

    def _install(handler):
        seen: list[httpx.Request] = []

        def _record(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler(request)

        class _MockedClient(real_client):
            def __init__(self, *args, **kwargs):
                kwargs.pop("proxy", None)
                kwargs.setdefault("transport", httpx.MockTransport(_record))
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", _MockedClient)
        return seen

    return _install


@pytest.fixture
def mock_sync_http(monkeypatch):
    """Route module-level ``httpx.get`` (used by about_singapore_law) through a MockTransport."""

    def _install(handler):
        seen: list[httpx.Request] = []

        def _record(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler(request)

        def _get(url, **kwargs):
            with httpx.Client(transport=httpx.MockTransport(_record)) as client:
                return client.get(url, **kwargs)

        monkeypatch.setattr(httpx, "get", _get)
        return seen

    return _install


@pytest.fixture
def no_retry_sleep(monkeypatch):
    """Make tenacity backoff instantaneous for the given ``@retry``-decorated coroutines."""

    async def _instant(_seconds):
        return None

    def _apply(*funcs):
        for func in funcs:
            monkeypatch.setattr(func.retry, "sleep", _instant)

    return _apply


@pytest.fixture
def chat_completion():
    """Build an OpenAI-style chat.completions JSON body."""

    def _make(content, finish_reason="stop"):
        return {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1700000000,
            "model": "stub-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        }

    return _make


@pytest.fixture
def stub_openai(monkeypatch):
    """Patch ``openai.AsyncOpenAI`` so the *real* SDK talks to a MockTransport.

    ``install(handler)`` returns ``(requests, constructor_kwargs)``. If the code under
    test passes its own ``http_client`` (the Tailscale proxy path) that client is kept.
    """
    import openai

    real = openai.AsyncOpenAI

    def _install(handler):
        seen: list[httpx.Request] = []
        ctor: list[dict] = []

        def _record(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler(request)

        def _factory(**kwargs):
            ctor.append(dict(kwargs))
            if kwargs.get("http_client") is None:
                kwargs["http_client"] = httpx.AsyncClient(transport=httpx.MockTransport(_record))
            return real(**kwargs)

        monkeypatch.setattr(openai, "AsyncOpenAI", _factory)
        return seen, ctor

    return _install
