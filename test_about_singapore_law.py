"""
Tests for the about_singapore_law resource's fetch_data skip semantics.

- Every section fetch fails (proxy/network down) -> Skip(kind="blocked")
- Clean crawl with nothing new -> [] (up_to_date)
"""

from unittest.mock import patch

import httpx
import pytest
from zeeker import Skip

from resources.about_singapore_law import fetch_data


class TestFetchDataSkip:
    @pytest.fixture(autouse=True)
    def _no_sleep(self):
        with patch("resources.about_singapore_law.time.sleep"):
            yield

    def test_all_sections_failing_raises_blocked_skip(self):
        """When every section fetch fails, fetch_data raises a blocked Skip."""
        with patch(
            "resources.about_singapore_law.discover_chapter_links",
            side_effect=httpx.ConnectError("proxy unreachable"),
        ):
            with pytest.raises(Skip) as exc_info:
                fetch_data(None)

        assert exc_info.value.kind == "blocked"
        assert "discovery failed (proxy?)" in exc_info.value.reason
        assert "ConnectError" in exc_info.value.reason
        assert "proxy unreachable" in exc_info.value.reason

    def test_nothing_new_returns_empty_list(self):
        """A clean crawl that finds no new chapters returns [] (up_to_date)."""
        with patch(
            "resources.about_singapore_law.discover_chapter_links",
            return_value=[],
        ):
            result = fetch_data(None)

        assert result == []

    def test_partial_section_failure_still_returns_items(self):
        """One failing section does not block the build if others succeed."""
        chapter = {
            "id": "abc123def456",
            "item_url": "https://www.singaporelawwatch.sg/About-Singapore-Law/Overview/ch-01",
            "title": "Ch. 01 The Singapore Legal System",
            "section": "Overview",
            "home_page": "Overview",
            "last_scraped": "2026-07-16 00:00:00",
            "content_length": 0,
        }

        calls = iter(
            [
                [chapter],
                httpx.ConnectError("proxy flaked"),
                [],
            ]
        )

        def _side_effect(*args, **kwargs):
            value = next(calls)
            if isinstance(value, Exception):
                raise value
            return value

        with patch(
            "resources.about_singapore_law.discover_chapter_links",
            side_effect=_side_effect,
        ):
            result = fetch_data(None)

        assert result == [chapter]

    def test_existing_chapters_filtered_not_skipped(self):
        """Already-known chapter URLs are filtered out; result is [] without Skip."""
        chapter = {
            "id": "abc123def456",
            "item_url": "https://www.singaporelawwatch.sg/About-Singapore-Law/Overview/ch-01",
            "title": "Ch. 01 The Singapore Legal System",
            "section": "Overview",
            "home_page": "Overview",
            "last_scraped": "2026-07-16 00:00:00",
            "content_length": 0,
        }

        class FakeTable:
            rows = [{"item_url": chapter["item_url"]}]

        with patch(
            "resources.about_singapore_law.discover_chapter_links",
            return_value=[chapter],
        ):
            result = fetch_data(FakeTable())

        assert result == []
