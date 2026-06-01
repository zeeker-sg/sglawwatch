"""
Headlines resource for fetching Singapore Law Watch Headlines RSS feed.
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Dict, Optional

TOKEN_LOG_PATH = os.environ.get(
    "ZEEKER_TOKEN_LOG", "/workspace/agent/token_usage.jsonl"
)

import click
import feedparser
import httpx
from sqlite_utils.db import Table
from tenacity import retry, stop_after_attempt, wait_exponential

HEADLINES_URL = "https://www.singaporelawwatch.sg/Portals/0/RSS/Headlines.xml"

# Limit concurrent LLM calls — local Ollama handles one at a time and will
# queue requests. Without this, all 70+ headlines fire simultaneously and
# most time-out waiting in the queue.
# Lazy-init so the semaphore binds to whichever event loop runs first,
# not the import-time loop (which may differ from the asyncio.run() loop).
_LLM_SEMAPHORE: Optional[asyncio.Semaphore] = None


def _get_llm_semaphore() -> asyncio.Semaphore:
    global _LLM_SEMAPHORE
    if _LLM_SEMAPHORE is None:
        _LLM_SEMAPHORE = asyncio.Semaphore(3)
    return _LLM_SEMAPHORE


def _log_token_usage(*, endpoint: str, model: str, prompt_tokens: int | None, completion_tokens: int | None, call_type: str = "summary") -> None:
    """Write token usage to shared JSONL log."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": "sglawwatch-zeeker",
        "endpoint": endpoint,
        "model": model,
        "prompt_tokens": prompt_tokens or 0,
        "completion_tokens": completion_tokens or 0,
        "call_type": call_type,
    }
    try:
        os.makedirs(os.path.dirname(TOKEN_LOG_PATH), exist_ok=True)
        with open(TOKEN_LOG_PATH, "a") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass


SYSTEM_PROMPT_TEXT = """
As an expert in legal affairs, your task is to provide summaries of legal news articles for time-constrained attorneys in an engaging, conversational style. These summaries should highlight the critical legal aspects, relevant precedents, and implications of the issues discussed in the articles. The summary should be in 1 narrative paragraph and should not be longer than 100 words, but ensure they efficiently deliver the key legal insights, making them beneficial for quick comprehension. The end goal is to help the lawyers understand the crux of the articles without having to read them in their entirety.
"""


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=1, max=10))
async def get_jina_reader_content(link: str) -> str:
    """Fetch content from the Jina reader link."""
    jina_token = os.environ.get("JINA_API_TOKEN")
    if not jina_token:
        click.echo("JINA_API_TOKEN environment variable not set", err=True)
        return ""
    jina_link = f"https://r.jina.ai/{link}"
    headers = {
        "Authorization": f"Bearer {jina_token}",
        "X-Retain-Images": "none",
        "X-Target-Selector": "article",
    }
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            r = await client.get(jina_link, headers=headers)
            r.raise_for_status()  # Raises httpx.HTTPStatusError for 4xx/5xx responses
        return r.text
    except (httpx.RequestError, httpx.HTTPStatusError) as e:
        click.echo(f"Error fetching content from Jina reader: {e}", err=True)
        raise


async def get_summary(text: str) -> str:
    """Generate a summary of the article text using any OpenAI-compatible LLM server.

    Supports local Ollama instances on Tailscale via TAILSCALE_PROXY (socks5h://...).
    """
    base_url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
    api_key = os.environ.get("LLM_API_KEY", "")
    model = os.environ.get("LLM_MODEL", "gpt-4.1-mini")
    tailscale_proxy = os.environ.get("TAILSCALE_PROXY", "")

    if not base_url:
        click.echo("LLM_BASE_URL not set — skipping summary", err=True)
        return ""

    from openai import AsyncOpenAI

    # Route through Tailscale SOCKS5 proxy if set — needed to reach local Ollama
    # instances on the Tailscale network (e.g. houfus-macbook-pro:11434)
    http_client = None
    if tailscale_proxy:
        try:
            proxy = httpx.Proxy(tailscale_proxy)
            http_client = httpx.AsyncClient(proxy=proxy, timeout=120)
            click.echo(f"  → Using Tailscale proxy for LLM: {tailscale_proxy}", err=True)
        except Exception as e:
            click.echo(f"  → Tailscale proxy setup failed: {e} — falling back to direct", err=True)

    client = AsyncOpenAI(
        base_url=base_url,
        api_key=api_key or "ollama",
        max_retries=0,
        timeout=300,
        http_client=http_client,
    )
    try:
        async with _get_llm_semaphore():
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT_TEXT},
                    {"role": "user", "content": f"Here is an article to summarise:\n {text[:4000]}"},
                ],
            )
        try:
            _log_token_usage(
                endpoint=base_url,
                model=model,
                prompt_tokens=getattr(response.usage, "prompt_tokens", None),
                completion_tokens=getattr(response.usage, "completion_tokens", None),
                call_type="summary",
            )
        except Exception:
            pass
        content = response.choices[0].message.content
        if not content:
            finish_reason = response.choices[0].finish_reason
            raise ValueError(f"LLM returned empty content (finish_reason={finish_reason})")
        return content
    except Exception as e:
        click.echo(f"Error generating summary from LLM: {e}", err=True)
        raise
    finally:
        if http_client:
            await http_client.aclose()


def get_hash_id(elements: list[str], delimiter: str = "|") -> str:
    """Generate a hash ID from a list of strings.

    Args:
        elements: List of strings to be hashed.
        delimiter: String used to join elements (default: "|").

    Returns:
        A hexadecimal MD5 hash of the joined elements.

    Examples:
        >>> get_hash_id(["2025-05-16", "Meeting Notes"])
        '1a2b3c4d5e6f7g8h9i0j'

        >>> get_hash_id(["user123", "login", "192.168.1.1"], delimiter=":")
        '7h8i9j0k1l2m3n4o5p6q'
    """
    import hashlib

    if not elements:
        raise ValueError("At least one element is required")

    joined_string = delimiter.join(str(element) for element in elements)
    return hashlib.md5(joined_string.encode()).hexdigest()


def convert_date_to_iso(date_str: str) -> str:
    """Convert date string like '08 May 2025 00:01:00' to ISO format."""
    try:
        parsed_date = datetime.strptime(date_str, "%d %B %Y %H:%M:%S")
        return parsed_date.isoformat()  # Returns '2025-05-08T00:01:00'
    except ValueError:
        # Handle potential parsing errors
        try:
            # Try alternative format with abbreviated month name
            parsed_date = datetime.strptime(date_str, "%d %b %Y %H:%M:%S")
            return parsed_date.isoformat()
        except ValueError:
            # If all parsing attempts fail, return original or a default
            return datetime.now().isoformat()


async def process_entry(entry: Dict) -> Optional[Dict]:
    """Process an entry from the RSS feed to extract necessary data.

    Returns a dict with entry data. Internal flags ``_jina_failed`` and
    ``_openai_failed`` track failures so the caller can abort on high error rates.
    """
    try:
        # Convert ISO date string to datetime object
        entry_date = datetime.fromisoformat(convert_date_to_iso(entry["published"]))

        # Always use deterministic hash ID (RSS feed IDs are often empty or inconsistent)
        article_id = get_hash_id([entry_date.isoformat(), entry["title"]])
        # Prepare entry data dictionary
        entry_data = {
            "id": article_id,
            "category": entry.get("category", ""),
            "title": entry.get("title", ""),
            "source_link": entry.get("link", ""),
            "author": entry.get("author", ""),
            "date": entry_date.isoformat(),
            "imported_on": datetime.now().isoformat(),
        }

        # Fetch content from Jina reader with graceful fallback
        click.echo(f"Processing: {entry_data['title']} from {entry_data['date']}")

        # Check if URL is problematic (some URLs cause 422 errors with Jina Reader)
        source_url = entry_data["source_link"]
        skip_jina = any(pattern in source_url for pattern in [
            'store.lawnet.com',  # Known to cause 422 errors
            'utm_source=',       # URLs with tracking parameters sometimes fail
        ])

        jina_failed = False
        try:
            if skip_jina:
                click.echo(f"  → Skipping Jina Reader for problematic URL pattern")
                raise Exception("URL pattern known to cause issues")
            entry_data["text"] = await get_jina_reader_content(source_url)
        except Exception as jina_error:
            jina_failed = True
            click.echo(f"  → Jina Reader failed: {jina_error}", err=True)
            # Use fallback: title as content for summary generation
            entry_data["text"] = f"Article: {entry_data['title']}\nSource: {source_url}\n\nContent could not be retrieved from source."
            click.echo(f"  → Using fallback content for summary generation")

        # Generate summary using LLM
        click.echo(f"  → Generating summary for: {entry_data['title']}")
        llm_failed = False
        try:
            entry_data["summary"] = await get_summary(entry_data["text"])
        except Exception as summary_error:
            llm_failed = True
            click.echo(f"  → Summary generation failed: {summary_error}", err=True)
            # Fallback: use truncated title as summary
            entry_data["summary"] = f"Legal news article: {entry_data['title'][:100]}{'...' if len(entry_data['title']) > 100 else ''}"
            click.echo(f"  → Using fallback summary")

        entry_data["_jina_failed"] = jina_failed
        entry_data["_llm_failed"] = llm_failed
        return entry_data
    except Exception as e:
        click.echo(f"Error processing entry '{entry.get('title', 'Unknown')}': {e}", err=True)
        return None


def _get_existing_data(existing_table: Optional[Table]) -> tuple[set, Optional[datetime]]:
    """Extract existing IDs and last article date from table.

    Uses MAX(date) from actual article data rather than the zeeker build timestamp.
    This avoids the build-time vs article-time mismatch: zeeker sets last_updated to
    datetime.now() even when 0 new articles were processed, which would filter
    midnight-published articles on subsequent same-day builds.
    """
    existing_ids = set()
    last_article_date = None

    if not existing_table:
        return existing_ids, last_article_date

    existing_ids = {row["id"] for row in existing_table.rows}

    # Use the most recent article date from actual data, not the build timestamp
    try:
        row = next(existing_table.db.execute(
            f"SELECT MAX(date) as max_date FROM [{existing_table.name}]"
        ))
        if row and row[0]:
            last_article_date = datetime.fromisoformat(row[0])
            click.echo(f"  → Last article date in DB: {last_article_date.isoformat()}")
    except Exception as e:
        click.echo(f"Could not get max article date from table: {e}", err=True)

    return existing_ids, last_article_date


def _should_skip_entry(
    entry: Dict,
    current_date: datetime,
    last_updated: Optional[datetime],
    existing_ids: set,
    max_day_limit: int = 60,
) -> tuple[bool, str]:
    """Check if entry should be skipped and return skip reason."""
    title = entry.get("title", "")

    # Skip advertisements - various formats (including fullwidth colon U+FF1A)
    if title.startswith("ADV:") or title.startswith("ADV\uff1a") or title.startswith("ADV "):
        return True, "advertisement"

    try:
        entry_date = datetime.fromisoformat(convert_date_to_iso(entry.get("published", "")))
    except ValueError:
        click.echo(f"Error parsing date for entry: {title}", err=True)
        return True, "date_error"

    days_old = (current_date - entry_date).days
    if days_old > max_day_limit:
        return True, "too_old"

    if last_updated and entry_date <= last_updated:
        return True, "already_processed_by_time"

    entry_id = get_hash_id([entry_date.isoformat(), str(title)])
    if existing_ids and entry_id in existing_ids:
        return True, "already_processed_by_id"

    return False, ""


def _log_skip_counts(
    skipped_adv_count: int,
    skipped_old_count: int,
    skipped_processed_time_count: int,
    skipped_processed_id_count: int,
    max_day_limit: int,
):
    """Log summary of skipped entries."""
    if skipped_adv_count > 0:
        click.echo(f"Skipped {skipped_adv_count} advertisements")
    if skipped_old_count > 0:
        click.echo(f"Skipped {skipped_old_count} headlines older than {max_day_limit} days")
    if skipped_processed_time_count > 0:
        click.echo(
            f"Skipped {skipped_processed_time_count} headlines older than last update timestamp"
        )
    if skipped_processed_id_count > 0:
        click.echo(f"Skipped {skipped_processed_id_count} headlines with duplicate IDs in database")


async def _fetch_article_text(url: str) -> str:
    """Fetch plain text from an article URL (HTTP fallback, no Jina required)."""
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "ZeekerBot/1.0 (+https://data.zeeker.sg)"})
            r.raise_for_status()
            return r.text[:8000]
    except Exception as e:
        click.echo(f"  → HTTP fetch failed for {url}: {e}", err=True)
        return ""


async def _backfill_empty_summaries(existing_table: Optional[Table]) -> None:
    """Retroactively generate summaries for rows that have empty or null summaries."""
    if not existing_table:
        return

    try:
        rows = list(existing_table.db.execute(
            f"SELECT id, title, source_link FROM [{existing_table.name}] "
            "WHERE summary IS NULL OR summary = '' OR summary = 'None'"
        ))
    except Exception as e:
        click.echo(f"Backfill: could not query empty summaries: {e}", err=True)
        return

    if not rows:
        click.echo("Backfill: no empty summaries found")
        return

    click.echo(f"Backfill: found {len(rows)} articles with empty summaries — regenerating")

    async def _fix_one(row_id: str, title: str, source_link: str) -> None:
        text = await _fetch_article_text(source_link)
        if not text:
            text = f"Article: {title}\nSource: {source_link}\n\nContent could not be retrieved."
        try:
            async with _get_llm_semaphore():
                summary = await get_summary(text)
            existing_table.db.execute(
                f"UPDATE [{existing_table.name}] SET summary = ? WHERE id = ?",
                [summary, row_id]
            )
            click.echo(f"  → Backfilled summary for: {title[:60]}")
        except Exception as e:
            click.echo(f"  → Backfill failed for {title[:60]}: {e}", err=True)

    tasks = [asyncio.create_task(_fix_one(r[0], r[1], r[2])) for r in rows]
    await asyncio.gather(*tasks)
    click.echo(f"Backfill: done ({len(rows)} articles processed)")


async def fetch_data(existing_table: Optional[Table]):
    """
    Fetch data for the headlines table.

    Args:
        existing_table: sqlite-utils Table object if table exists, None for new table
                       Use this to check for existing data and avoid duplicates

    Returns:
        List[Dict[str, Any]]: List of records to insert into database

    """
    await _backfill_empty_summaries(existing_table)
    click.echo(f"Fetching headlines from {HEADLINES_URL}")
    feed = feedparser.parse(HEADLINES_URL)
    max_day_limit = 60
    current_date = datetime.now()

    existing_ids, last_updated = _get_existing_data(existing_table)

    tasks = []
    new_entries_count = 0
    skipped_adv_count = 0
    skipped_old_count = 0
    skipped_processed_time_count = 0
    skipped_processed_id_count = 0

    for entry in feed.entries:
        should_skip, skip_reason = _should_skip_entry(
            entry, current_date, last_updated, existing_ids, max_day_limit
        )

        if should_skip:
            title = entry.get("title", "")
            if skip_reason == "advertisement":
                skipped_adv_count += 1
                click.echo(f"Skipping advertisement: {title}")
            elif skip_reason == "too_old":
                skipped_old_count += 1
                days_old = (
                    current_date
                    - datetime.fromisoformat(convert_date_to_iso(entry.get("published", "")))
                ).days
                click.echo(f"Skipping old headline ({days_old} days): {title}")
            elif skip_reason == "already_processed_by_time":
                skipped_processed_time_count += 1
                entry_date_str = entry.get("published", "")
                click.echo(
                    f"  → Skipping (published {entry_date_str}, before last_updated {last_updated.strftime('%Y-%m-%d %H:%M:%S') if last_updated else 'None'}): {title}"
                )
            elif skip_reason == "already_processed_by_id":
                skipped_processed_id_count += 1
                click.echo(f"  → Skipping (duplicate ID in database): {title}")
            continue

        new_entries_count += 1
        task = asyncio.create_task(process_entry(entry))
        tasks.append(task)

    results = await asyncio.gather(*tasks)

    # Check failure rates — if most entries failed, something is wrong
    # (e.g. expired API token, service outage)
    valid_results = [r for r in results if r is not None]
    jina_failures = [r for r in valid_results if r.get("_jina_failed")]
    llm_failures = [r for r in valid_results if r.get("_llm_failed")]

    if valid_results and len(jina_failures) > len(valid_results) * 0.5:
        raise RuntimeError(
            f"Jina Reader failed for {len(jina_failures)}/{len(valid_results)} entries. "
            f"Check JINA_API_TOKEN or Jina service status. "
            f"Aborting to avoid storing garbage data."
        )
    if valid_results and len(llm_failures) > len(valid_results) * 0.5:
        raise RuntimeError(
            f"LLM failed for {len(llm_failures)}/{len(valid_results)} entries. "
            f"Check LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL or service status. "
            f"Aborting to avoid storing garbage data."
        )

    # Strip internal flags before returning
    for r in valid_results:
        r.pop("_jina_failed", None)
        r.pop("_llm_failed", None)

    click.echo(f"Added {new_entries_count} new headlines")
    if jina_failures:
        click.echo(
            f"⚠️  Jina Reader failed for {len(jina_failures)}/{len(valid_results)} entries",
            err=True,
        )
    if llm_failures:
        click.echo(
            f"⚠️  LLM failed for {len(llm_failures)}/{len(valid_results)} entries",
            err=True,
        )
    _log_skip_counts(
        skipped_adv_count,
        skipped_old_count,
        skipped_processed_time_count,
        skipped_processed_id_count,
        max_day_limit,
    )
    return results


