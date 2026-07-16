"""
About Singapore Law resource with fragments support.

Workflow: Multiple home pages → Direct chapter links → Content fragments

Tables created:
- about_singapore_law: One record per legal chapter
- about_singapore_law_fragments: Content chunks from each chapter
"""

import hashlib
import time
from typing import Any, Dict, List, Optional

import click
import httpx
from bs4 import BeautifulSoup
from sqlite_utils.db import Table
from zeeker import Skip

# zeeker >= 0.9.0 runs fetch_data ONCE per build and threads its output to
# fetch_fragments_data as main_data_context — no module reload, no second
# call. The old PID-sentinel guard against double invocation is gone.


def fetch_data(existing_table: Optional[Table]) -> List[Dict[str, Any]]:
    """Discover all legal chapters from multiple Singapore Law Watch sections."""

    existing_urls = set()
    if existing_table:
        existing_urls = {row["item_url"] for row in existing_table.rows}

    all_items = []
    sections = get_home_page_urls()
    failed_sections = 0
    last_error: Optional[Exception] = None

    # Process each home page
    for home_url, home_name in sections:
        try:
            # Each "home page" is actually a section with direct chapter links
            chapter_links = discover_chapter_links(home_url, home_name)

            # Filter out existing chapters
            new_chapters = [
                chapter for chapter in chapter_links if chapter["item_url"] not in existing_urls
            ]

            all_items.extend(new_chapters)

            time.sleep(1)  # Be respectful

        except Exception as e:
            failed_sections += 1
            last_error = e
            click.echo(
                f"about_singapore_law: failed to process section {home_url}: "
                f"{type(e).__name__}: {e}",
                err=True,
            )
            continue

    # Every section fetch failed — the source was never actually checked
    # (proxy down, network outage). Raise a blocked Skip so the build status
    # says so and the _zeeker_updates freshness marker does NOT advance.
    # A clean crawl that simply found nothing new still returns [].
    if failed_sections == len(sections) and not all_items and last_error is not None:
        raise Skip(
            f"discovery failed (proxy?): {type(last_error).__name__}: {last_error}",
            kind="blocked",
        )

    return all_items


def fetch_fragments_data(
    existing_fragments_table: Optional[Table],
    main_data_context: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Create content fragments from each chapter."""

    if not main_data_context:
        return []

    existing_fragment_ids = set()
    if existing_fragments_table:
        existing_fragment_ids = {row["id"] for row in existing_fragments_table.rows}

    all_fragments = []
    scraped_count = 0
    failed_count = 0
    skipped_count = 0

    for chapter in main_data_context:
        chapter_url = chapter.get("item_url", "")
        try:
            # Scrape chapter content — raises on fetch/parse failure
            paragraphs = scrape_chapter_content(chapter_url)
        except Exception as e:
            failed_count += 1
            click.echo(
                f"about_singapore_law: failed to scrape chapter {chapter_url}: "
                f"{type(e).__name__}: {e}",
                err=True,
            )
            time.sleep(1)  # Be respectful
            continue

        if not paragraphs:
            # No usable content — skip the chapter (do NOT emit an empty record)
            skipped_count += 1
            click.echo(
                f"about_singapore_law: no content extracted from {chapter_url} — skipping chapter",
                err=True,
            )
            time.sleep(1)  # Be respectful
            continue

        # Create fragments
        fragments = create_content_fragments(paragraphs, chapter["id"])

        # Filter existing fragments
        new_fragments = [f for f in fragments if f["id"] not in existing_fragment_ids]

        all_fragments.extend(new_fragments)
        scraped_count += 1
        click.echo(
            f"about_singapore_law: created {len(new_fragments)} fragments from {chapter_url}"
        )

        time.sleep(1)  # Be respectful

    click.echo(
        f"about_singapore_law: done — {scraped_count} chapters scraped, "
        f"{failed_count} failed, {skipped_count} skipped"
    )

    return all_fragments


def transform_data(raw_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Optional data transformation before database insertion."""
    return raw_data


def transform_fragments_data(raw_fragments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Optional fragment transformation before database insertion."""
    return raw_fragments


# =============================================================================
# CONFIGURATION - CUSTOMIZE FOR YOUR SITES
# =============================================================================


def get_home_page_urls() -> List[tuple[str, str]]:
    """Return list of (home_url, home_name) tuples to scrape."""
    return [
        ("https://www.singaporelawwatch.sg/About-Singapore-Law/Overview", "Overview"),
        ("https://www.singaporelawwatch.sg/About-Singapore-Law/Commercial-Law", "Commercial Law"),
        (
            "https://www.singaporelawwatch.sg/About-Singapore-Law/Singapore-Legal-System",
            "Singapore Legal System",
        ),
    ]


def discover_chapter_links(section_url: str, section_name: str) -> List[Dict[str, Any]]:
    """Find all chapter links within a section page.

    Raises on fetch/parse failure — fetch_data counts section failures so it
    can tell "every section unreachable" (blocked Skip) from "nothing new".
    """

    response = httpx.get(section_url, timeout=30.0)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")

    # Find all chapter links using the main wrapper selector
    main_wrapper = soup.select(".edn_mainWrapper")
    chapter_links = []

    if main_wrapper:
        links = main_wrapper[0].select("a")  # Use first main wrapper
        for link in links:
            href = link.get("href")
            title = link.get_text(strip=True)

            # Only include links that go deeper into About-Singapore-Law and have meaningful text
            if (
                href
                and "About-Singapore-Law" in href
                and href != section_url  # Not the same page
                and len(title) > 5
            ):  # Has meaningful title
                url_hash = hashlib.md5(href.encode()).hexdigest()[:12]
                chapter_links.append(
                    {
                        "id": url_hash,
                        "item_url": href,
                        "title": title,
                        "section": section_name,
                        "home_page": section_name,
                        "last_scraped": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "content_length": 0,
                    }
                )

    return chapter_links


def scrape_chapter_content(chapter_url: str) -> list[dict]:
    """Extract main content from a chapter page, processing all content tags in order.

    Raises on fetch/parse failure — callers decide how to report and skip.
    Returns an empty list when the page yields no usable content.
    """

    response = httpx.get(chapter_url, timeout=30.0)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")

    article = soup.select(".edn_article")[0]

    # Get all content elements in order (paragraphs, tables, lists, etc.)
    content_elements = article.find_all(
        ["p", "table", "ul", "ol", "div", "h1", "h2", "h3", "h4", "h5", "h6"]
    )

    # Remove elements that are nested inside tables or lists to avoid duplicates
    filtered_elements = []
    for element in content_elements:
        # Skip if this element is inside a table (we already capture table content)
        if element.find_parent("table"):
            continue
        # Skip if this element is inside a ul/ol list (we already capture list content)
        if element.find_parent(["ul", "ol"]):
            continue
        filtered_elements.append(element)

    content_elements = filtered_elements

    content_parts = []
    for element in content_elements:
        if element.name == "table":
            # Extract table content as structured text
            table_text = extract_table_text(element)
            if table_text.strip():
                content_parts.append(
                    {
                        "text": table_text,
                        "type": "table",
                        "original_text": element.get_text(strip=True),
                    }
                )
        elif element.name in ["ul", "ol"]:
            # Extract list content
            list_text = extract_list_text(element)
            if list_text.strip():
                content_parts.append(
                    {
                        "text": list_text,
                        "type": "list",
                        "original_text": element.get_text(strip=True),
                    }
                )
        elif element.name in ["h1", "h2", "h3", "h4", "h5", "h6"]:
            # Extract heading text
            heading_text = element.get_text(strip=True)
            if heading_text:
                content_parts.append(
                    {"text": heading_text, "type": "heading", "original_text": heading_text}
                )
        elif element.name in ["p", "div"]:
            # Extract paragraph/div text - preserve original spacing for indentation check
            original_text = str(element)
            text = element.get_text(strip=True)
            if text:
                content_parts.append(
                    {"text": text, "type": "paragraph", "original_text": original_text}
                )

    # Post-process to group consecutive indented paragraphs that should be list items
    content_parts = group_pseudo_list_items(content_parts)

    # Filter out footer content - stop processing when we hit footer markers
    content_parts = filter_footer_content(content_parts)

    return content_parts


def extract_table_text(table_element) -> str:
    """Extract text content from a table element in a readable format."""
    rows = []
    for tr in table_element.find_all("tr"):
        cells = []
        for cell in tr.find_all(["td", "th"]):
            cell_text = cell.get_text(strip=True)
            cells.append(cell_text)
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def extract_list_text(list_element) -> str:
    """Extract text content from list elements (ul/ol) in a readable format."""
    items = []
    for li in list_element.find_all("li", recursive=False):  # Only direct children
        item_text = li.get_text(strip=True)
        if item_text:
            prefix = "- " if list_element.name == "ul" else "• "
            items.append(f"{prefix}{item_text}")
    return "\n".join(items)


def create_content_fragments(
    content_parts: List[Dict[str, Any]], chapter_id: str
) -> List[Dict[str, Any]]:
    """Split chapter content into searchable fragments.

    Grouping rules:
    - Headings: Join to the NEXT numbered paragraph
    - Tables/Lists: Join to the PREVIOUS numbered paragraph/fragment
    - Indented paragraphs: Join to the PREVIOUS fragment
    - Each fragment starts with a numbered paragraph (like 1.1.1)
    """

    if not content_parts:
        return []

    import re

    fragments = []
    current_headers = []  # Collect headers until we hit a numbered paragraph
    fragment_index = 0
    last_content_type = None  # Track the type of the previous content element

    # Regex to match numbered paragraphs like "1.1.1", "1.2.15", etc.
    numbered_para_pattern = r"^\d+\.\d+\.\d+"

    for i, content_part in enumerate(content_parts):
        content_text = content_part["text"].strip()
        content_type = content_part["type"]
        original_text = content_part["original_text"]

        if len(content_text) < 5:  # Skip very short content
            continue

        # Check if this is a numbered paragraph
        if content_type == "paragraph" and re.match(numbered_para_pattern, content_text):
            # Start new fragment with any collected headers + this numbered paragraph
            fragment_content_parts = current_headers + [content_text]
            fragment_content = "\n\n".join(fragment_content_parts)

            # Extract the section number for the fragment ID
            section_match = re.match(r"^(\d+\.\d+\.\d+)", content_text)
            section_num = section_match.group(1) if section_match else f"f{fragment_index:03d}"
            fragment_id = f"{chapter_id}_{section_num}"

            fragments.append(
                {
                    "id": fragment_id,
                    "item_id": chapter_id,
                    "fragment_order": fragment_index,
                    "content_text": fragment_content,
                    "char_count": len(fragment_content),
                }
            )

            # Reset for next fragment
            current_headers = []
            fragment_index += 1

        elif content_type == "heading":
            # Headings get collected for the NEXT numbered paragraph
            current_headers.append(content_text)

        elif content_type in ["table", "list"]:
            # Tables and lists attach to the PREVIOUS fragment (if exists)
            if fragments:
                last_fragment = fragments[-1]
                last_fragment["content_text"] += "\n\n" + content_text
                last_fragment["char_count"] = len(last_fragment["content_text"])
            else:
                # No previous fragment - collect as header for next numbered paragraph
                current_headers.append(content_text)

        elif content_type == "paragraph":
            # Check if this paragraph is indented content (using original HTML)
            is_indented = check_paragraph_indentation(original_text)

            if is_indented and fragments:
                # Attach to the previous (most recent) fragment
                last_fragment = fragments[-1]
                last_fragment["content_text"] += "\n\n" + content_text
                last_fragment["char_count"] = len(last_fragment["content_text"])
            elif not is_indented and last_content_type in ["table", "list"] and fragments:
                # After a table or list, non-indented paragraphs attach to the previous fragment
                last_fragment = fragments[-1]
                last_fragment["content_text"] += "\n\n" + content_text
                last_fragment["char_count"] = len(last_fragment["content_text"])
            else:
                # This is a regular paragraph - collect it as header for the next numbered paragraph
                current_headers.append(content_text)

        # Track the content type for the next iteration
        last_content_type = content_type

    # Handle any remaining headers at the end (attach to last fragment if exists)
    if current_headers and fragments:
        last_fragment = fragments[-1]
        additional_content = "\n\n".join(current_headers)
        last_fragment["content_text"] += "\n\n" + additional_content
        last_fragment["char_count"] = len(last_fragment["content_text"])

    return fragments


def check_paragraph_indentation(original_html: str) -> bool:
    """Check if a paragraph has exactly 4 spaces of indentation in the original HTML."""
    # Look for patterns indicating indentation in the original HTML
    # This is a simplified check - you might need to adjust based on actual HTML structure
    if "style=" in original_html and (
        "margin-left" in original_html or "padding-left" in original_html
    ):
        return True
    # Check for text that starts with 4 spaces when extracted
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(original_html, "html.parser")
    text_content = soup.get_text()
    leading_spaces = len(text_content) - len(text_content.lstrip())
    return leading_spaces == 4


def is_likely_list_item(text: str) -> bool:
    """Check if a paragraph text looks like it should be a list item."""
    # Check for common list item patterns
    stripped_text = text.strip()

    # Starts with "the " and is likely a continuation of a list
    if stripped_text.lower().startswith("the ") and len(stripped_text) > 20:
        # Look for patterns common in legal list items
        legal_patterns = [
            "veto against",
            "appointment of",
            "concurrence with",
            "withholding of",
            "exercise of",
            "approval of",
            "consent to",
            "power to",
            "authority to",
            "right to",
            "duty to",
            "responsibility for",
        ]
        return any(pattern in stripped_text.lower() for pattern in legal_patterns)

    return False


def group_pseudo_list_items(content_parts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group consecutive paragraphs that should be list items into a single list."""
    if not content_parts:
        return content_parts

    result = []
    i = 0

    while i < len(content_parts):
        current = content_parts[i]

        if current["type"] == "paragraph":
            # Look ahead to see if we have consecutive list-like items
            list_items = []
            j = i

            # Collect consecutive list-like paragraphs
            while (
                j < len(content_parts)
                and content_parts[j]["type"] == "paragraph"
                and is_likely_list_item(content_parts[j]["text"])
            ):
                list_items.append(content_parts[j]["text"])
                j += 1

            if len(list_items) >= 2:  # At least 2 consecutive list items
                # Create a combined list
                list_text = "\n".join(f"• {item}" for item in list_items)
                result.append(
                    {
                        "text": list_text,
                        "type": "list",
                        "original_text": " ".join(
                            item["original_text"] for item in content_parts[i:j]
                        ),
                    }
                )
                i = j  # Skip the items we just processed
            else:
                # Regular paragraph, keep as is
                result.append(current)
                i += 1
        else:
            # Non-paragraph, keep as is
            result.append(current)
            i += 1

    return result


def filter_footer_content(content_parts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Stop processing when we hit footer markers to avoid capturing navigation/metadata."""
    if not content_parts:
        return content_parts

    filtered_parts = []
    footer_markers = [
        "updated as at",
        "by:",
        "disclaimer:",
        "@singaporelawwatch.sg",
        "email protected",
        "the writers wish to acknowledge",
    ]

    for i, part in enumerate(content_parts):
        text_lower = part["text"].lower().strip()

        # Check if this content part contains footer markers
        is_footer = False
        for marker in footer_markers:
            if marker in text_lower:
                is_footer = True
                break

        # More specific checks for different footer patterns
        if not is_footer:
            # Skip the first element if it's a chapter title (legitimate content)
            if i == 0 and part["type"] == "heading" and text_lower.startswith("ch. "):
                is_footer = False  # Keep chapter titles
            # Check for navigation links that appear later (not at the start)
            elif i > 10 and "ch. " in text_lower and len(text_lower) < 100:
                # Navigation pattern like "Ch. 01 The Singapore Legal SystemCh. 03 Mediation"
                if text_lower.count("ch. ") >= 2:
                    is_footer = True
            # Check for standalone "print" or "tags:" that appear later
            elif i > 10 and (text_lower == "print" or text_lower.startswith("tags:")):
                is_footer = True
            # Check for standalone numbers that might be page counts/IDs (but not section numbers)
            elif i > 10 and text_lower.isdigit() and len(text_lower) > 3:
                is_footer = True
            # Check for references section (usually at the end)
            elif i > 10 and text_lower.startswith("references"):
                is_footer = True

        # If we hit footer content, stop processing here
        if is_footer:
            break

        filtered_parts.append(part)

    return filtered_parts
