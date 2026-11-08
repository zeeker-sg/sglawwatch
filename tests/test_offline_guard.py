"""The root conftest must make accidental network use fail loudly."""

import httpx
import pytest


def test_real_http_call_is_blocked():
    with pytest.raises(Exception, match="network access is blocked"):
        httpx.get("https://www.singaporelawwatch.sg/")


def test_secrets_are_scrubbed(monkeypatch):
    import os

    for name in ("OPENAI_API_KEY", "JINA_API_TOKEN", "LLM_API_KEY", "DOCLING_SERVE_API_KEY"):
        assert name not in os.environ
