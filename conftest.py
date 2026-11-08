"""Repo-wide pytest guards: the suite must pass offline and with no secrets.

Applied to every test (root-level and ``tests/``):

* secrets / endpoints that the resources read from the environment are removed,
* ``ZEEKER_TOKEN_LOG`` is pointed at a throw-away file so nothing writes to
  ``/workspace``,
* DNS resolution and socket connects raise, so an accidental real HTTP call
  fails loudly instead of silently hitting the network.

Tests that genuinely need the network or credentials must be marked
``@pytest.mark.live``; those are excluded by default (see ``pyproject.toml``).
"""

import os
import socket
import tempfile

import pytest

os.environ.setdefault(
    "ZEEKER_TOKEN_LOG", os.path.join(tempfile.gettempdir(), "sglawwatch-test-token-usage.jsonl")
)

_SCRUBBED_ENV = (
    "OPENAI_API_KEY",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "TAILSCALE_PROXY",
    "JINA_API_TOKEN",
    "DOCLING_SERVE_URL",
    "DOCLING_SERVE_API_KEY",
    "DOCLING_CONCURRENCY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


@pytest.fixture(autouse=True)
def _offline_and_secret_free(monkeypatch, request):
    for name in _SCRUBBED_ENV:
        monkeypatch.delenv(name, raising=False)

    if request.node.get_closest_marker("live"):
        yield
        return

    def _blocked(*args, **kwargs):
        raise RuntimeError("network access is blocked in tests (mark with @pytest.mark.live)")

    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    yield
