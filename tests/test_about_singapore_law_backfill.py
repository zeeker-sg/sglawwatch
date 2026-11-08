"""about_singapore_law: lettered numbering, unnumbered pages, alias chapters and the backfill pass."""

import httpx
import pytest
import sqlite_utils

from resources import about_singapore_law as asl

BASE = "https://www.singaporelawwatch.sg/About-Singapore-Law/"
LETTERED = BASE + "Commercial-Law/ch-08a-remedies-for-breach-of-contract"
PROSE = BASE + "Singapore-Legal-System/ministry-of-law"
ALIAS = BASE + "Commercial-Law/ch-25-shipping-law-9"
ALIAS_TARGET = BASE + "Commercial-Law/Ch-17-Corporate-Finance-and-Securities-Regulation"

LETTERED_HTML = b"""<article class="edn_article">
<h1>Ch. 08A Remedies for Breach of Contract</h1>
<p>SECTION 1 INTRODUCTION</p>
<p>8A.1.1 This report addresses the general positions under Singapore contract law.</p>
<p>8A.1.2 Singapore contract law and the contract law in England are similar.</p>
</article>"""

PROSE_HTML = b"""<article class="edn_article">
<h1>Ministry of Law</h1>
<p>The Ministry of Law's mission is to ensure a sound legal infrastructure.</p>
<p>Please visit the <a href="https://mlaw.gov.sg">Ministry of Law</a> website for details.</p>
<p><a href="/a">Attorney-General's Chambers</a><a href="/b">The Law Society of Singapore</a></p>
<p>Print</p>
<p>14169</p>
</article>"""

TARGET_HTML = b"""<article class="edn_article"><h1>Ch. 17</h1>
<p>17.1.1 Corporate finance text that already lives under the canonical chapter.</p></article>"""


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(asl.time, "sleep", lambda *_: None)


@pytest.fixture
def site(mock_sync_http):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == LETTERED:
            return httpx.Response(200, content=LETTERED_HTML)
        if url == PROSE:
            return httpx.Response(200, content=PROSE_HTML)
        if url == ALIAS:
            return httpx.Response(301, headers={"location": ALIAS_TARGET})
        if url == ALIAS_TARGET:
            return httpx.Response(200, content=TARGET_HTML)
        return httpx.Response(404)

    return mock_sync_http(handler)


def test_lettered_paragraph_numbers_anchor_fragments(site):
    frags = asl.create_content_fragments(asl.scrape_chapter_content(LETTERED), "c8a")
    assert [f["id"] for f in frags] == ["c8a_8A.1.1", "c8a_8A.1.2"]
    # the chapter title and section heading join forward onto the first numbered paragraph
    assert frags[0]["content_text"].startswith("Ch. 08A Remedies")


def test_unnumbered_page_falls_back_to_paragraph_anchors_without_page_chrome(site):
    frags = asl.create_content_fragments(asl.scrape_chapter_content(PROSE), "mol")
    assert [f["id"] for f in frags] == ["mol_p000", "mol_p001"]
    assert frags[0]["content_text"].startswith("Ministry of Law\n\nThe Ministry of Law's mission")
    joined = "\n".join(f["content_text"] for f in frags)
    assert "Print" not in joined and "14169" not in joined
    assert "Attorney-General's Chambers" not in joined  # prev/next navigation links
    assert "Please visit the" in joined  # a paragraph that merely contains a link is kept


def test_redirect_to_another_chapter_is_an_alias(site):
    with pytest.raises(asl.AliasChapter):
        asl.scrape_chapter_content(ALIAS)


def _db_with(chapters, fragments):
    db = sqlite_utils.Database(memory=True)
    db["about_singapore_law"].insert_all(chapters, pk="id")
    db["about_singapore_law_fragments"].insert_all(
        fragments
        or [
            {
                "id": "x",
                "item_id": "x",
                "fragment_order": 0,
                "content_text": "",
                "char_count": 0,
            }
        ],
        pk="id",
    )
    return db


def test_backfill_retries_stored_chapters_without_fragments(site):
    db = _db_with(
        [
            {"id": "done", "item_url": BASE + "Overview/ch-01"},
            {"id": "c8a", "item_url": LETTERED},
            {"id": "mol", "item_url": PROSE},
            {"id": "alias", "item_url": ALIAS},
            {"id": "orphan", "item_url": LETTERED.replace("https://", "http://")},
        ],
        [
            {
                "id": "done_1.1.1",
                "item_id": "done",
                "fragment_order": 0,
                "content_text": "t",
                "char_count": 1,
            }
        ],
    )
    frags = asl.fetch_fragments_data(db["about_singapore_law_fragments"], [])

    assert sorted({f["item_id"] for f in frags}) == ["c8a", "mol"]
    requested = {str(r.url) for r in site}
    assert BASE + "Overview/ch-01" not in requested  # already has fragments
    assert not any(u.startswith("http://") for u in requested)  # http orphans are not retried
    assert asl.__zeeker_report__ == {
        "backfill_candidates": 3,
        "backfilled": 2,
        "skipped_alias": 1,
    }


def test_backfill_does_not_double_queue_chapters_discovered_this_build(site):
    db = _db_with([{"id": "c8a", "item_url": LETTERED}], [])
    frags = asl.fetch_fragments_data(
        db["about_singapore_law_fragments"], [{"id": "c8a", "item_url": LETTERED}]
    )
    assert [f["id"] for f in frags] == ["c8a_8A.1.1", "c8a_8A.1.2"]
    assert len(site) == 1


def test_up_to_date_build_with_nothing_to_heal_does_no_requests(site):
    db = _db_with(
        [{"id": "done", "item_url": LETTERED}],
        [
            {
                "id": "done_8A.1.1",
                "item_id": "done",
                "fragment_order": 0,
                "content_text": "t",
                "char_count": 1,
            }
        ],
    )
    assert asl.fetch_fragments_data(db["about_singapore_law_fragments"], []) == []
    assert site == []
