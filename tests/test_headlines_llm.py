"""headlines.get_summary against the real openai SDK with a stubbed HTTP transport.

Everything runs with OPENAI_API_KEY / LLM_* unset (see the root conftest).
"""

import json
import os

import httpx
import openai
import pytest

from resources import headlines


@pytest.fixture(autouse=True)
def _token_log(monkeypatch, tmp_path):
    path = tmp_path / "tokens.jsonl"
    monkeypatch.setattr(headlines, "TOKEN_LOG_PATH", str(path))
    return path


def test_environment_has_no_openai_key():
    assert "OPENAI_API_KEY" not in os.environ


async def test_summary_success_request_and_response_shape(
    monkeypatch, stub_openai, chat_completion, _token_log
):
    monkeypatch.setenv("LLM_BASE_URL", "http://llm.test/v1")
    monkeypatch.setenv("LLM_MODEL", "stub-model")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    seen, ctor = stub_openai(
        lambda req: httpx.Response(200, json=chat_completion("Short summary."))
    )

    out = await headlines.get_summary("A" * 10_000)

    assert out == "Short summary."
    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == "http://llm.test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-test"
    body = json.loads(request.content)
    assert body["model"] == "stub-model"
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["messages"][0]["content"] == headlines.SYSTEM_PROMPT_TEXT
    # only the first 4,000 characters of the article are sent
    assert body["messages"][1]["content"] == "Here is an article to summarise:\n " + "A" * 4000
    # SDK retries are disabled so tenacity-free failures surface immediately
    assert ctor[0]["max_retries"] == 0

    record = json.loads(_token_log.read_text().splitlines()[-1])
    assert record["prompt_tokens"] == 11 and record["completion_tokens"] == 7
    assert record["model"] == "stub-model" and record["call_type"] == "summary"


async def test_summary_defaults_without_any_env(stub_openai, chat_completion):
    seen, _ = stub_openai(lambda req: httpx.Response(200, json=chat_completion("ok")))
    assert await headlines.get_summary("text") == "ok"
    assert str(seen[0].url) == "https://api.openai.com/v1/chat/completions"
    assert json.loads(seen[0].content)["model"] == "gpt-4.1-mini"
    assert seen[0].headers["authorization"] == "Bearer ollama"


@pytest.mark.parametrize("content", [None, ""])
async def test_summary_empty_content_raises(monkeypatch, stub_openai, chat_completion, content):
    monkeypatch.setenv("LLM_BASE_URL", "http://llm.test/v1")
    stub_openai(lambda req: httpx.Response(200, json=chat_completion(content, "length")))
    with pytest.raises(ValueError, match="finish_reason=length"):
        await headlines.get_summary("text")


async def test_summary_http_error_surfaces_as_openai_error(monkeypatch, stub_openai):
    monkeypatch.setenv("LLM_BASE_URL", "http://llm.test/v1")
    seen, _ = stub_openai(
        lambda req: httpx.Response(500, json={"error": {"message": "boom", "type": "server_error"}})
    )
    with pytest.raises(openai.InternalServerError):
        await headlines.get_summary("text")
    assert len(seen) == 1  # max_retries=0


async def test_summary_auth_error_type(monkeypatch, stub_openai):
    monkeypatch.setenv("LLM_BASE_URL", "http://llm.test/v1")
    stub_openai(lambda req: httpx.Response(401, json={"error": {"message": "bad key"}}))
    with pytest.raises(openai.AuthenticationError):
        await headlines.get_summary("text")


async def test_summary_connection_error_type(monkeypatch, stub_openai):
    monkeypatch.setenv("LLM_BASE_URL", "http://llm.test/v1")

    def boom(request):
        raise httpx.ConnectError("refused", request=request)

    stub_openai(boom)
    with pytest.raises(openai.APIConnectionError):
        await headlines.get_summary("text")


async def test_blank_base_url_skips_the_llm(monkeypatch, stub_openai):
    monkeypatch.setenv("LLM_BASE_URL", "")
    seen, ctor = stub_openai(lambda req: httpx.Response(500))
    assert await headlines.get_summary("text") == ""
    assert seen == [] and ctor == []


async def test_tailscale_proxy_builds_a_socks_http_client(
    monkeypatch, stub_openai, chat_completion
):
    """Non-localhost target + TAILSCALE_PROXY -> a SOCKS-proxied httpx client (needs httpx[socks])."""
    monkeypatch.setenv("LLM_BASE_URL", "http://100.64.0.9:11434/v1")
    monkeypatch.setenv("TAILSCALE_PROXY", "socks5h://127.0.0.1:1080")

    clients = []
    real_client = httpx.AsyncClient

    class _Spy(real_client):
        def __init__(self, *args, **kwargs):
            clients.append(kwargs.copy())
            # keep the SOCKS proxy config validated by httpx, but never connect
            kwargs["transport"] = httpx.MockTransport(
                lambda req: httpx.Response(200, json=chat_completion("via proxy"))
            )
            kwargs.pop("proxy", None)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _Spy)
    _seen, ctor = stub_openai(lambda req: httpx.Response(500))

    assert await headlines.get_summary("text") == "via proxy"
    assert str(clients[0]["proxy"].url) == "socks5h://127.0.0.1:1080"
    assert ctor[0]["http_client"] is not None


async def test_localhost_target_never_uses_the_proxy(monkeypatch, stub_openai, chat_completion):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("TAILSCALE_PROXY", "socks5h://127.0.0.1:1080")
    seen, ctor = stub_openai(lambda req: httpx.Response(200, json=chat_completion("direct")))
    assert await headlines.get_summary("text") == "direct"
    assert ctor[0]["http_client"] is None


def test_token_log_failure_is_swallowed(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(headlines, "TOKEN_LOG_PATH", str(blocker / "sub" / "log.jsonl"))
    headlines._log_token_usage(endpoint="e", model="m", prompt_tokens=1, completion_tokens=2)


async def test_process_entry_end_to_end_with_stub_llm(
    monkeypatch, mock_async_http, stub_openai, chat_completion
):
    monkeypatch.setenv("LLM_BASE_URL", "http://llm.test/v1")
    monkeypatch.setenv("JINA_API_TOKEN", "t")
    mock_async_http(lambda req: httpx.Response(200, text="body"))
    stub_openai(lambda req: httpx.Response(200, json=chat_completion("Sum.")))
    row = await headlines.process_entry(
        {
            "published": "08 May 2025 00:01:00",
            "title": "T",
            "link": "https://pub.test/x",
            "author": "ST: A",
            "category": "Straits Times",
        }
    )
    assert row["summary"] == "Sum." and row["text"] == "body"
    assert row["_jina_failed"] is False and row["_llm_failed"] is False
