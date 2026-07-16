"""
Tests for the headlines resource.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from resources.headlines import (
    convert_date_to_iso,
    fetch_data,
    get_hash_id,
    get_jina_reader_content,
    get_summary,
    process_entry,
)


class TestUtilityFunctions:
    """Test utility functions in the headlines module."""

    def test_get_hash_id_basic(self):
        """Test basic hash ID generation."""
        elements = ["2025-05-16", "Meeting Notes"]
        result = get_hash_id(elements)
        assert isinstance(result, str)
        assert len(result) == 32  # MD5 hash length

        # Same input should produce same hash
        result2 = get_hash_id(elements)
        assert result == result2

    def test_get_hash_id_custom_delimiter(self):
        """Test hash ID generation with custom delimiter."""
        elements = ["user123", "login", "192.168.1.1"]
        result1 = get_hash_id(elements, delimiter=":")
        result2 = get_hash_id(elements, delimiter="|")
        assert result1 != result2

    def test_get_hash_id_empty_list_raises_error(self):
        """Test that empty list raises ValueError."""
        with pytest.raises(ValueError, match="At least one element is required"):
            get_hash_id([])

    def test_convert_date_to_iso_standard_format(self):
        """Test date conversion with standard format."""
        date_str = "08 May 2025 00:01:00"
        result = convert_date_to_iso(date_str)
        assert result == "2025-05-08T00:01:00"

    def test_convert_date_to_iso_abbreviated_format(self):
        """Test date conversion with abbreviated month format."""
        date_str = "08 May 2025 00:01:00"
        result = convert_date_to_iso(date_str)
        assert result == "2025-05-08T00:01:00"

    def test_convert_date_to_iso_invalid_format_returns_current(self):
        """Test that invalid date format returns current datetime."""
        with patch("resources.headlines.datetime") as mock_datetime:
            mock_now = MagicMock()
            mock_now.isoformat.return_value = "2025-08-11T12:00:00"
            mock_datetime.now.return_value = mock_now
            mock_datetime.strptime.side_effect = ValueError("Invalid format")

            result = convert_date_to_iso("invalid date")
            assert result == "2025-08-11T12:00:00"

    def test_should_skip_advertisements(self):
        """Test that advertisements are properly filtered out."""
        from datetime import datetime

        from resources.headlines import _should_skip_entry

        current_date = datetime.now()

        # Test ADV: format
        adv_entry_1 = {
            "title": "ADV: Some advertisement content",
            "published": "04 Sep 2025 00:01:00",
        }

        # Test ADV JLP: format (space after ADV)
        adv_entry_2 = {
            "title": "ADV JLP: Starting an Action (Disputes)",
            "published": "04 Sep 2025 00:01:00",
        }

        # Test normal article (should not be skipped for advertisement)
        normal_entry = {
            "title": "Singapore, India to launch roadmap on cooperation",
            "published": "04 Sep 2025 00:01:00",
        }

        # Test advertisement filtering
        should_skip_1, reason_1 = _should_skip_entry(adv_entry_1, current_date, None, set())
        assert should_skip_1 is True
        assert reason_1 == "advertisement"

        should_skip_2, reason_2 = _should_skip_entry(adv_entry_2, current_date, None, set())
        assert should_skip_2 is True
        assert reason_2 == "advertisement"

        # Normal entry should not be skipped for advertisement
        should_skip_normal, reason_normal = _should_skip_entry(
            normal_entry, current_date, None, set()
        )
        # It might be skipped for other reasons, but not for advertisement
        if should_skip_normal:
            assert reason_normal != "advertisement"


class TestAsyncFunctions:
    """Test async functions in the headlines module."""

    @pytest.mark.asyncio
    async def test_get_jina_reader_content_missing_token(self):
        """Test Jina reader with missing API token."""
        with patch.dict("os.environ", {}, clear=True):
            result = await get_jina_reader_content("https://example.com")
            assert result == ""

    @pytest.mark.asyncio
    async def test_get_jina_reader_content_success(self):
        """Test successful Jina reader content fetch."""
        mock_response = MagicMock()
        mock_response.text = "Article content here"

        with patch.dict("os.environ", {"JINA_API_TOKEN": "test-token"}):
            with patch("httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                    return_value=mock_response
                )

                result = await get_jina_reader_content("https://example.com")
                assert result == "Article content here"

    @pytest.mark.asyncio
    async def test_get_summary_missing_base_url(self):
        """Test summary generation skips when LLM_BASE_URL is explicitly empty."""
        with patch.dict("os.environ", {"LLM_BASE_URL": ""}, clear=True):
            result = await get_summary("Some article text")
            assert result == ""

    @pytest.mark.asyncio
    async def test_get_summary_success(self):
        """Test successful summary generation."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "This is a summary"

        with patch.dict(
            "os.environ",
            {"LLM_BASE_URL": "http://localhost:11434/v1", "LLM_API_KEY": "test-key"},
            clear=True,
        ):
            with patch("openai.AsyncOpenAI") as mock_openai:
                mock_client = MagicMock()
                mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
                mock_openai.return_value = mock_client

                result = await get_summary("Article text to summarize")
                assert result == "This is a summary"

    @pytest.mark.asyncio
    async def test_process_entry_success(self):
        """Test successful entry processing."""
        entry = {
            "published": "08 May 2025 00:01:00",
            "title": "Test Article",
            "link": "https://example.com",
            "author": "Test Author",
            "category": "Legal News",
        }

        with patch(
            "resources.headlines.get_jina_reader_content", new_callable=AsyncMock
        ) as mock_jina:
            with patch("resources.headlines.get_summary", new_callable=AsyncMock) as mock_summary:
                mock_jina.return_value = "Article content"
                mock_summary.return_value = "Article summary"

                result = await process_entry(entry)

                assert result is not None
                assert result["title"] == "Test Article"
                assert result["author"] == "Test Author"
                assert result["category"] == "Legal News"
                assert result["source_link"] == "https://example.com"
                assert result["text"] == "Article content"
                assert result["summary"] == "Article summary"
                assert "id" in result
                assert "date" in result
                assert "imported_on" in result

    @pytest.mark.asyncio
    async def test_process_entry_exception_handling(self):
        """Test entry processing with exception."""
        entry = {"published": "invalid date", "title": "Test Article"}

        with patch(
            "resources.headlines.get_jina_reader_content", new_callable=AsyncMock
        ) as mock_jina:
            with patch("resources.headlines.get_summary", new_callable=AsyncMock) as mock_summary:
                mock_jina.return_value = "Article content"
                mock_summary.return_value = "Article summary"

                result = await process_entry(entry)

        # The function handles invalid dates gracefully and returns data with fallback values
        assert result is not None
        assert result["title"] == "Test Article"
        # The date should be converted to current time as fallback
        assert "date" in result
        assert result["date"] is not None

    @pytest.mark.asyncio
    async def test_fetch_data_basic(self):
        """Test basic fetch_data functionality."""
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%d %B %Y %H:%M:%S")
        two_days_ago = (datetime.now() - timedelta(days=2)).strftime("%d %B %Y %H:%M:%S")
        mock_feed = MagicMock()
        mock_feed.entries = [
            {
                "published": yesterday,
                "title": "Test Article 1",
                "link": "https://example1.com",
                "author": "Author 1",
                "category": "Legal News",
            },
            {
                "published": two_days_ago,
                "title": "ADV: Advertisement",
                "link": "https://example2.com",
            },
        ]

        with patch("feedparser.parse", return_value=mock_feed):
            with patch("resources.headlines.process_entry", new_callable=AsyncMock) as mock_process:
                mock_process.return_value = {
                    "id": "test123",
                    "title": "Test Article 1",
                    "text": "Content",
                    "summary": "Summary",
                }

                result = await fetch_data(None)

                # Should skip ADV entries, so only 1 call to process_entry
                assert mock_process.call_count == 1
                assert len(result) == 1

    @pytest.mark.asyncio
    async def test_fetch_data_with_existing_table(self):
        """Test fetch_data with existing table and metadata."""
        mock_table = MagicMock()
        mock_table.name = "headlines"
        mock_table.rows = []

        mock_db = MagicMock()
        mock_db.table_names.return_value = ["_zeeker_updates", "headlines"]
        mock_table.db = mock_db

        two_days_ago = (datetime.now() - timedelta(days=2)).isoformat()
        # _get_existing_data reads MAX(date) from the table; _backfill_empty_summaries
        # queries for empty summaries first — return no rows for that call.
        mock_db.execute.side_effect = [iter([]), iter([(two_days_ago,)])]

        yesterday = (datetime.now() - timedelta(days=1)).strftime("%d %B %Y %H:%M:%S")
        three_days_ago = (datetime.now() - timedelta(days=3)).strftime("%d %B %Y %H:%M:%S")
        mock_feed = MagicMock()
        mock_feed.entries = [
            {
                "published": yesterday,  # After last_updated
                "title": "New Article",
                "link": "https://example1.com",
                "author": "Author 1",
                "category": "Legal News",
            },
            {
                "published": three_days_ago,  # Before last_updated
                "title": "Old Article",
                "link": "https://example2.com",
            },
        ]

        with patch("feedparser.parse", return_value=mock_feed):
            with patch("resources.headlines.process_entry", new_callable=AsyncMock) as mock_process:
                mock_process.return_value = {"id": "test123", "title": "New Article"}

                await fetch_data(mock_table)

                # Should only process the new article
                assert mock_process.call_count == 1

    @pytest.mark.asyncio
    async def test_process_entry_problematic_url_flagged_for_drop(self):
        """Problematic URLs are flagged _jina_failed so the entry is dropped, not stored."""
        from resources.headlines import process_entry

        # Entry with a problematic URL (store.lawnet.com)
        test_entry = {
            "id": "test_lawnet",
            "category": "Legal",
            "title": "Test LawNet Article",
            "link": "https://store.lawnet.com/jlp-starting-an-action.html?utm_source=slw_edm",
            "author": "Legal Team",
            "published": "04 Sep 2025 00:01:00",
        }

        with (
            patch(
                "resources.headlines.get_jina_reader_content", new_callable=AsyncMock
            ) as mock_jina,
            patch("resources.headlines.get_summary", new_callable=AsyncMock) as mock_summary,
        ):
            result = await process_entry(test_entry)

            # Should not call Jina Reader for problematic URLs
            assert mock_jina.call_count == 0
            # Should not attempt a summary for a dropped entry
            assert mock_summary.call_count == 0

            # Entry is flagged for exclusion — no placeholder content is produced
            assert result is not None
            assert result["_jina_failed"] is True
            assert "text" not in result
            assert "summary" not in result
            assert "Content could not be retrieved" not in str(result)

    @pytest.mark.asyncio
    async def test_process_entry_jina_exception_flagged_for_drop(self):
        """A Jina Reader exception flags the entry _jina_failed with the failure reason."""
        entry = {
            "published": "04 Sep 2025 00:01:00",
            "title": "Jina Down Article",
            "link": "https://example.com/article",
        }

        with (
            patch(
                "resources.headlines.get_jina_reader_content", new_callable=AsyncMock
            ) as mock_jina,
            patch("resources.headlines.get_summary", new_callable=AsyncMock) as mock_summary,
        ):
            mock_jina.side_effect = ConnectionError("boom")

            result = await process_entry(entry)

            assert result is not None
            assert result["_jina_failed"] is True
            assert result["_llm_failed"] is False
            assert "ConnectionError" in result["_failure_reason"]
            assert "boom" in result["_failure_reason"]
            # No placeholder text/summary is generated for a dropped entry
            assert "text" not in result
            assert "summary" not in result
            assert mock_summary.call_count == 0

    @pytest.mark.asyncio
    async def test_process_entry_llm_exception_flagged_for_drop(self):
        """An LLM exception flags the entry _llm_failed with the failure reason."""
        entry = {
            "published": "04 Sep 2025 00:01:00",
            "title": "LLM Down Article",
            "link": "https://example.com/article",
        }

        with (
            patch(
                "resources.headlines.get_jina_reader_content", new_callable=AsyncMock
            ) as mock_jina,
            patch("resources.headlines.get_summary", new_callable=AsyncMock) as mock_summary,
        ):
            mock_jina.return_value = "Article content"
            mock_summary.side_effect = TimeoutError("llm timed out")

            result = await process_entry(entry)

            assert result is not None
            assert result["_jina_failed"] is False
            assert result["_llm_failed"] is True
            assert "TimeoutError" in result["_failure_reason"]
            # No placeholder summary is stored
            assert "summary" not in result


class TestDegradedBatchHandling:
    """Tests for the degrade-instead-of-abort behavior in fetch_data."""

    @staticmethod
    def _make_feed(titles):
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%d %B %Y %H:%M:%S")
        mock_feed = MagicMock()
        mock_feed.entries = [
            {
                "published": yesterday,
                "title": title,
                "link": f"https://example.com/{i}",
                "author": "Author",
                "category": "Legal News",
            }
            for i, title in enumerate(titles)
        ]
        return mock_feed

    @staticmethod
    def _entry(entry_id, *, jina_failed=False, llm_failed=False, reason=""):
        data = {
            "id": entry_id,
            "title": f"Article {entry_id}",
            "source_link": f"https://example.com/{entry_id}",
            "_jina_failed": jina_failed,
            "_llm_failed": llm_failed,
            "_failure_reason": reason,
        }
        if not jina_failed:
            data["text"] = "Content"
        if not (jina_failed or llm_failed):
            data["summary"] = "Summary"
        return data

    @pytest.mark.asyncio
    async def test_jina_failed_entries_excluded_from_results(self):
        """Entries flagged _jina_failed are excluded so they retry next build."""
        mock_feed = self._make_feed(["Good Article", "Bad Article"])

        with patch("feedparser.parse", return_value=mock_feed):
            with patch("resources.headlines.process_entry", new_callable=AsyncMock) as mock_process:
                mock_process.side_effect = [
                    self._entry("good1"),
                    self._entry(
                        "bad1", jina_failed=True, reason="HTTPStatusError: 422 Unprocessable"
                    ),
                ]

                result = await fetch_data(None)

        assert len(result) == 1
        assert result[0]["id"] == "good1"

    @pytest.mark.asyncio
    async def test_llm_failed_entries_excluded_from_results(self):
        """Entries flagged _llm_failed are excluded so they retry next build."""
        mock_feed = self._make_feed(["Good Article", "Bad Article"])

        with patch("feedparser.parse", return_value=mock_feed):
            with patch("resources.headlines.process_entry", new_callable=AsyncMock) as mock_process:
                mock_process.side_effect = [
                    self._entry("good1"),
                    self._entry("bad1", llm_failed=True, reason="TimeoutError: llm timed out"),
                ]

                result = await fetch_data(None)

        assert len(result) == 1
        assert result[0]["id"] == "good1"

    @pytest.mark.asyncio
    async def test_total_failure_raises_blocked_skip(self):
        """100% failure raises Skip(kind='blocked') — outage, not 'nothing new'."""
        from zeeker import Skip

        import resources.headlines as headlines_module

        mock_feed = self._make_feed(["Article A", "Article B", "Article C"])

        with patch("feedparser.parse", return_value=mock_feed):
            with patch("resources.headlines.process_entry", new_callable=AsyncMock) as mock_process:
                mock_process.side_effect = [
                    self._entry("a", jina_failed=True, reason="ConnectError: no route"),
                    self._entry("b", jina_failed=True, reason="ConnectError: no route"),
                    self._entry("c", llm_failed=True, reason="APIError: 500"),
                ]

                with pytest.raises(Skip) as exc_info:
                    await fetch_data(None)

        assert exc_info.value.kind == "blocked"
        assert "all 3 new entries failed (Jina/LLM)" in exc_info.value.reason
        # The degradation counters are still surfaced via __zeeker_report__
        # (zeeker consumes it on every exit path, including a raised Skip).
        report = getattr(headlines_module, "__zeeker_report__", None)
        assert report is not None
        assert report["dropped_jina"] == 2
        assert report["dropped_llm"] == 1
        # Clean up the module-level report so other tests see fresh state
        delattr(headlines_module, "__zeeker_report__")

    @pytest.mark.asyncio
    async def test_no_new_entries_returns_empty_not_skip(self):
        """A feed with nothing new returns [] (up_to_date), never raises Skip."""
        mock_feed = MagicMock()
        mock_feed.entries = []

        with patch("feedparser.parse", return_value=mock_feed):
            result = await fetch_data(None)

        assert result == []

    @pytest.mark.asyncio
    async def test_partial_failure_sets_zeeker_report(self):
        """Partial degradation returns kept rows AND sets __zeeker_report__ counters."""
        import resources.headlines as headlines_module

        mock_feed = self._make_feed(["Good Article", "Bad Article"])

        with patch("feedparser.parse", return_value=mock_feed):
            with patch("resources.headlines.process_entry", new_callable=AsyncMock) as mock_process:
                mock_process.side_effect = [
                    self._entry("good1"),
                    self._entry("bad1", llm_failed=True, reason="TimeoutError: llm timed out"),
                ]

                result = await fetch_data(None)

        assert len(result) == 1
        assert result[0]["id"] == "good1"
        report = getattr(headlines_module, "__zeeker_report__", None)
        assert report is not None
        assert report["dropped_jina"] == 0
        assert report["dropped_llm"] == 1
        assert "notes" in report
        delattr(headlines_module, "__zeeker_report__")

    @pytest.mark.asyncio
    async def test_full_success_leaves_no_zeeker_report(self):
        """No drops → __zeeker_report__ is not set (no noise on the status line)."""
        import resources.headlines as headlines_module

        # Ensure no stale report from a previous test
        if hasattr(headlines_module, "__zeeker_report__"):
            delattr(headlines_module, "__zeeker_report__")

        mock_feed = self._make_feed(["Good Article"])

        with patch("feedparser.parse", return_value=mock_feed):
            with patch("resources.headlines.process_entry", new_callable=AsyncMock) as mock_process:
                mock_process.return_value = self._entry("good1")

                result = await fetch_data(None)

        assert len(result) == 1
        assert not hasattr(headlines_module, "__zeeker_report__")

    @pytest.mark.asyncio
    async def test_internal_flags_stripped_from_stored_rows(self):
        """Rows returned for storage carry no internal bookkeeping flags."""
        mock_feed = self._make_feed(["Good Article"])

        with patch("feedparser.parse", return_value=mock_feed):
            with patch("resources.headlines.process_entry", new_callable=AsyncMock) as mock_process:
                mock_process.return_value = self._entry("good1")

                result = await fetch_data(None)

        assert len(result) == 1
        row = result[0]
        assert "_jina_failed" not in row
        assert "_llm_failed" not in row
        assert "_failure_reason" not in row
