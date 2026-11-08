"""Run the real ``zeeker`` DatabaseBuilder over this project, fully offline.

All I/O (RSS parsing, Jina, docling, OpenAI-compatible LLM, site scraping) is stubbed at
the library boundary (feedparser / httpx / openai), so resource modules are loaded from
disk exactly as ``zeeker build`` does. This is the test most likely to catch a zeeker
upgrade that changes the resource contract.
"""

import sqlite3
from pathlib import Path

import httpx
import pytest
from zeeker.core.database.builder import DatabaseBuilder
from zeeker.core.types import ZeekerProject

from resources import about_singapore_law as asl

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def built_db(
    tmp_path,
    monkeypatch,
    fixture_bytes,
    fresh_feed,
    mock_async_http,
    mock_sync_http,
    stub_openai,
    chat_completion,
):
    import feedparser

    monkeypatch.setenv("LLM_BASE_URL", "http://llm.test/v1")
    monkeypatch.setenv("JINA_API_TOKEN", "t")
    monkeypatch.setenv("ZEEKER_TOKEN_LOG", str(tmp_path / "tokens.jsonl"))
    monkeypatch.setattr("time.sleep", lambda *_: None)

    real_parse = feedparser.parse

    def parse(url, *a, **k):
        if "Headlines" in url:
            return real_parse(fresh_feed("headlines.xml"))
        if "Commentaries" in url:
            return real_parse(fixture_bytes("commentaries.xml"))
        raise AssertionError(f"unexpected feed {url}")

    monkeypatch.setattr(feedparser, "parse", parse)

    def async_handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "r.jina.ai":
            return httpx.Response(200, text="Extracted text. " * 120)  # ~1.9k chars -> 2 fragments
        if request.url.path.endswith(".pdf"):
            return httpx.Response(200, content=b"%PDF")
        if request.url.path == "/v1/convert/file":
            return httpx.Response(200, json={"document": {"md_content": "PDF text. " * 20}})
        return httpx.Response(404)

    mock_async_http(async_handler)
    stub_openai(lambda req: httpx.Response(200, json=chat_completion("A summary.")))

    def sync_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://www.singaporelawwatch.sg/About-Singapore-Law/Overview":
            return httpx.Response(200, content=fixture_bytes("section_overview.html"))
        if "/ch-" in url:
            return httpx.Response(200, content=fixture_bytes("chapter.html"))
        return httpx.Response(200, content=b'<div class="edn_mainWrapper"></div>')

    mock_sync_http(sync_handler)

    project = ZeekerProject.from_toml(ROOT / "zeeker.toml")
    db_path = tmp_path / "build.db"
    project.database = str(db_path)  # absolute path -> never touches the repo
    result = DatabaseBuilder(ROOT, project).build_database(setup_fts=True)
    return result, sqlite3.connect(db_path)


def _count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]


def test_build_succeeds_with_no_errors(built_db):
    result, _ = built_db
    assert result.is_valid, result.errors
    assert not result.errors


def test_all_declared_tables_exist_with_expected_rows(built_db, zeeker_toml):
    _, conn = built_db
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(zeeker_toml["resource"]) <= tables
    assert _count(conn, "headlines") == 5  # 6 in feed, 1 ADV skipped
    assert _count(conn, "commentaries") == 4
    assert _count(conn, "about_singapore_law") == 4
    assert _count(conn, "about_singapore_law_fragments") == 4 * 5  # 5 numbered paras per chapter
    assert _count(conn, "commentaries_fragments") >= 4


def test_table_columns_match_zeeker_toml(built_db, zeeker_toml):
    _, conn = built_db
    for name, cfg in zeeker_toml["resource"].items():
        columns = {r[1] for r in conn.execute(f"PRAGMA table_info([{name}])")}
        assert columns == set(cfg["columns"]), name


def test_fragment_rows_link_back_to_their_parents(built_db):
    _, conn = built_db
    orphans = conn.execute(
        "SELECT COUNT(*) FROM about_singapore_law_fragments f "
        "LEFT JOIN about_singapore_law c ON c.id = f.item_id WHERE c.id IS NULL"
    ).fetchone()[0]
    assert orphans == 0
    orphans = conn.execute(
        "SELECT COUNT(*) FROM commentaries_fragments f "
        "LEFT JOIN commentaries c ON c.id = f.commentary_id WHERE c.id IS NULL"
    ).fetchone()[0]
    assert orphans == 0


def test_row_values_and_types(built_db):
    _, conn = built_db
    row = conn.execute("SELECT id, date, summary, text FROM headlines LIMIT 1").fetchone()
    assert len(row[0]) == 32 and "T" in row[1]
    assert row[2] == "A summary." and row[3].startswith("Extracted text.")
    assert conn.execute(
        "SELECT typeof(fragment_order), typeof(char_count) FROM about_singapore_law_fragments LIMIT 1"
    ).fetchone() == ("integer", "integer")


def test_fts_indexes_are_searchable(built_db):
    _, conn = built_db
    hits = conn.execute(
        "SELECT COUNT(*) FROM headlines_fts WHERE headlines_fts MATCH 'summary'"
    ).fetchone()[0]
    assert hits == 5
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM about_singapore_law_fragments_fts "
            "WHERE about_singapore_law_fragments_fts MATCH 'tapestry'"
        ).fetchone()[0]
        == 4
    )


def test_build_did_not_touch_the_repo(built_db):
    assert not (ROOT / "build.db").exists()
    assert not list(ROOT.glob("*.db"))
    assert asl.get_home_page_urls()  # module still importable after zeeker's reload
