"""
Commentaries resource for Singapore Law Watch.

Fetches law firm commentaries from the SLW RSS feed.
- PDFs are converted to markdown via docling (OCR-capable, handles complex layouts)
- Web pages are extracted via Jina Reader
- Full text is chunked into searchable fragments

Tables created:
- commentaries: One record per commentary (title, author, pub_date, link, full_text, ...)
- commentaries_fragments: Text chunks from each commentary
"""

import asyncio
import hashlib
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import click
import feedparser
import httpx
from sqlite_utils.db import Table
from tenacity import retry, stop_after_attempt, wait_exponential

COMMENTARIES_RSS = "https://www.singaporelawwatch.sg/Portals/0/RSS/Commentaries.xml"
# host.docker.internal only resolves inside Docker containers with the
# host-gateway add-host flag. The build runs directly on the Linux host
# (zeeker-build script), where it fails DNS lookup. Default to localhost,
# matching the zeeker-source-creator skill convention.
DOCLING_URL = os.environ.get("DOCLING_SERVE_URL", "http://localhost:5001")
DOCLING_API_KEY = os.environ.get("DOCLING_SERVE_API_KEY")
# docling-serve runs a single worker by default; firing all RSS PDFs at it
# concurrently makes later ones exceed the httpx timeout (504 Gateway Timeout).
# Cap in-flight conversions; increase only if docling-serve is scaled up.
DOCLING_CONCURRENCY = int(os.environ.get("DOCLING_CONCURRENCY", "2"))
_docling_semaphore: Optional["asyncio.Semaphore"] = None
FRAGMENT_SIZE = 1200  # characters per chunk
FRAGMENT_OVERLAP = 150  # overlap between chunks


def _get_docling_semaphore() -> "asyncio.Semaphore":
    """Lazy-init so the semaphore binds to whichever event loop runs first."""
    global _docling_semaphore
    if _docling_semaphore is None:
        _docling_semaphore = asyncio.Semaphore(DOCLING_CONCURRENCY)
    return _docling_semaphore


def get_hash_id(elements: list[str]) -> str:
    return hashlib.md5("|".join(str(e) for e in elements).encode()).hexdigest()


def parse_pub_date(entry: Dict) -> str:
    """Parse feedparser entry date to ISO string."""
    if hasattr(entry, "published_parsed") and entry.get("published_parsed"):
        try:
            return datetime(*entry.published_parsed[:6]).isoformat()
        except Exception:
            pass
    published = entry.get("published", "")
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%d %b %Y"):
        try:
            return datetime.strptime(published, fmt).isoformat()
        except ValueError:
            pass
    return datetime.now().isoformat()


def is_pdf(url: str) -> bool:
    return url.lower().split("?")[0].endswith(".pdf")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=1, max=15))
async def extract_via_docling(url: str) -> str:
    """Download a PDF and convert to markdown via docling-serve."""
    async with httpx.AsyncClient(timeout=120) as client:
        pdf_resp = await client.get(url, follow_redirects=True)
        pdf_resp.raise_for_status()
        pdf_bytes = pdf_resp.content

    headers = {}
    if DOCLING_API_KEY:
        headers["Authorization"] = f"Bearer {DOCLING_API_KEY}"

    # /v1/convert/file is the multipart byte-upload endpoint. /v1/convert/source
    # takes JSON with a URL — different shape entirely. Field name is "files"
    # (plural array), format param is "to_formats". image_export_mode=placeholder
    # keeps the markdown free of base64-embedded image data (otherwise typical
    # commentary PDFs balloon to 600KB+ of mostly image dumps).
    async with _get_docling_semaphore():
        async with httpx.AsyncClient(timeout=300) as client:
            r = await client.post(
                f"{DOCLING_URL}/v1/convert/file",
                files=[("files", ("document.pdf", pdf_bytes, "application/pdf"))],
                data={"to_formats": "md", "image_export_mode": "placeholder"},
                headers=headers,
            )
            r.raise_for_status()
            result = r.json()

    # Handle docling-serve response formats
    md = (
        result.get("document", {}).get("md_content")
        or result.get("output", {}).get("content")
        or result.get("content")
        or ""
    )
    return md


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=1, max=10))
async def extract_via_jina(url: str) -> str:
    """Extract web page content via Jina Reader."""
    jina_token = os.environ.get("JINA_API_TOKEN")
    headers = {"X-Retain-Images": "none"}
    if jina_token:
        headers["Authorization"] = f"Bearer {jina_token}"

    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.get(f"https://r.jina.ai/{url}", headers=headers)
        r.raise_for_status()
        return r.text


async def extract_content(url: str) -> tuple[str, str]:
    """Extract full text from URL. Returns (content_type, full_text)."""
    if is_pdf(url):
        try:
            text = await extract_via_docling(url)
            return "pdf", text
        except Exception as e:
            click.echo(f"commentaries:   → docling failed: {e}", err=True)
            return "pdf", ""
    else:
        try:
            text = await extract_via_jina(url)
            return "web", text
        except Exception as e:
            click.echo(f"commentaries:   → Jina failed: {e}", err=True)
            return "web", ""


async def process_entry(entry: Dict) -> Optional[Dict]:
    """Process one RSS entry into a commentary record."""
    try:
        url = entry.get("link", "")
        title = entry.get("title", "").strip()
        author = entry.get("author", entry.get("category", "")).strip()
        description = entry.get("summary", "").strip()
        pub_date = parse_pub_date(entry)
        record_id = get_hash_id([url])

        click.echo(f"commentaries: processing: {title}")
        content_type, full_text = await extract_content(url)
        click.echo(f"commentaries:   → {content_type}, {len(full_text)} chars extracted")

        return {
            "id": record_id,
            "title": title,
            "author": author,
            "pub_date": pub_date,
            "link": url,
            "content_type": content_type,
            "description": description,
            "full_text": full_text,
            "imported_on": datetime.now().isoformat(),
        }
    except Exception as e:
        click.echo(
            f"commentaries: error processing '{entry.get('title', 'Unknown')}': {e}", err=True
        )
        return None


async def fetch_data(existing_table: Optional[Table]) -> List[Dict[str, Any]]:
    """Fetch commentaries from the SLW RSS feed."""
    click.echo(f"commentaries: fetching commentaries from {COMMENTARIES_RSS}")
    feed = feedparser.parse(COMMENTARIES_RSS)

    existing_ids = set()
    if existing_table:
        existing_ids = {row["id"] for row in existing_table.rows}

    tasks = []
    skipped = 0

    for entry in feed.entries:
        url = entry.get("link", "")
        if not url:
            continue
        record_id = get_hash_id([url])
        if record_id in existing_ids:
            skipped += 1
            continue
        tasks.append(asyncio.create_task(process_entry(entry)))

    if skipped:
        click.echo(f"commentaries: skipped {skipped} already-imported entries")

    results = await asyncio.gather(*tasks)
    valid = [r for r in results if r is not None]

    empty_text = [r for r in valid if not r.get("full_text")]
    if valid and len(empty_text) > len(valid) * 0.5:
        click.echo(
            f"commentaries: WARNING — text extraction failed for {len(empty_text)}/{len(valid)} "
            "entries; check docling health and JINA_API_TOKEN",
            err=True,
        )

    click.echo(f"commentaries: added {len(valid)} new commentaries")
    return valid


def fetch_fragments_data(
    existing_fragments_table: Optional[Table],
    main_data_context: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Chunk commentary full_text into searchable fragments."""
    if not main_data_context:
        return []

    existing_ids = set()
    if existing_fragments_table:
        existing_ids = {row["id"] for row in existing_fragments_table.rows}

    fragments = []

    for record in main_data_context:
        full_text = record.get("full_text", "").strip()

        if not full_text:
            # Fall back to description as the sole fragment
            desc = record.get("description", "").strip()
            if desc:
                frag_id = get_hash_id([record["id"], "0"])
                if frag_id not in existing_ids:
                    fragments.append(
                        {
                            "id": frag_id,
                            "commentary_id": record["id"],
                            "fragment_index": 0,
                            "text": desc,
                            "char_count": len(desc),
                        }
                    )
            continue

        i = 0
        idx = 0
        while i < len(full_text):
            chunk = full_text[i : i + FRAGMENT_SIZE].strip()
            if chunk:
                frag_id = get_hash_id([record["id"], str(idx)])
                if frag_id not in existing_ids:
                    fragments.append(
                        {
                            "id": frag_id,
                            "commentary_id": record["id"],
                            "fragment_index": idx,
                            "text": chunk,
                            "char_count": len(chunk),
                        }
                    )
                idx += 1
            i += FRAGMENT_SIZE - FRAGMENT_OVERLAP

    click.echo(
        f"commentaries: created {len(fragments)} fragments "
        f"from {len(main_data_context)} commentaries"
    )
    return fragments
