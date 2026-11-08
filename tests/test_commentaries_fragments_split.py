"""commentaries.fetch_fragments_data: 1,200-char windows with 150-char overlap."""

import hashlib
import string

import pytest

from resources.commentaries import FRAGMENT_OVERLAP, FRAGMENT_SIZE, fetch_fragments_data

STEP = FRAGMENT_SIZE - FRAGMENT_OVERLAP


def _text(n: int) -> str:
    """Deterministic, non-repeating-at-the-seams text of length n (no whitespace edges)."""
    alphabet = string.ascii_letters + string.digits
    return "".join(alphabet[(i * 7 + i // 62) % len(alphabet)] for i in range(n))


def _record(text, rid="r1", description="desc"):
    return {"id": rid, "full_text": text, "description": description}


def _frag_id(rid, idx):
    return hashlib.md5(f"{rid}|{idx}".encode()).hexdigest()


def test_constants_match_zeeker_toml_documentation():
    assert (FRAGMENT_SIZE, FRAGMENT_OVERLAP) == (1200, 150)


@pytest.mark.parametrize(
    "length, expected",
    [
        (1, 1),
        (1200, 2),  # tail window (from 1050) is pure overlap: fully contained in window 0
        (1050, 1),
        (1051, 2),
        (2250, 3),
        (3000, 3),
        (3150, 3),
        (3151, 4),
    ],
)
def test_fragment_count(length, expected):
    frags = fetch_fragments_data(None, [_record(_text(length))])
    assert len(frags) == expected
    assert [f["fragment_index"] for f in frags] == list(range(expected))


def test_windows_overlap_and_cover_whole_text():
    text = _text(3000)
    frags = fetch_fragments_data(None, [_record(text)])
    assert [f["char_count"] for f in frags] == [len(f["text"]) for f in frags]
    assert frags[0]["text"] == text[:1200]
    for a, b in zip(frags, frags[1:]):
        assert a["text"][-FRAGMENT_OVERLAP:] == b["text"][:FRAGMENT_OVERLAP]
    rebuilt = frags[0]["text"] + "".join(f["text"][FRAGMENT_OVERLAP:] for f in frags[1:])
    assert rebuilt == text


def test_ids_are_stable_and_order_preserving():
    records = [_record(_text(2500), "a"), _record(_text(1300), "b")]
    first = fetch_fragments_data(None, records)
    second = fetch_fragments_data(None, records)
    assert first == second
    assert [(f["commentary_id"], f["fragment_index"]) for f in first] == [
        ("a", 0),
        ("a", 1),
        ("a", 2),
        ("b", 0),
        ("b", 1),
    ]
    assert all(f["id"] == _frag_id(f["commentary_id"], f["fragment_index"]) for f in first)
    assert len({f["id"] for f in first}) == len(first)


def test_row_keys_follow_the_documented_columns(zeeker_toml):
    frags = fetch_fragments_data(None, [_record(_text(100))])
    assert set(frags[0]) == set(zeeker_toml["resource"]["commentaries_fragments"]["columns"])


def test_whitespace_is_trimmed():
    frags = fetch_fragments_data(None, [_record("   " + _text(50) + "   ")])
    assert frags[0]["text"] == _text(50)


def test_empty_text_falls_back_to_description_single_fragment():
    frags = fetch_fragments_data(None, [_record("  ", description=" From RSS ")])
    assert len(frags) == 1
    assert frags[0]["text"] == "From RSS" and frags[0]["fragment_index"] == 0
    assert frags[0]["id"] == _frag_id("r1", 0)


def test_no_text_and_no_description_gives_nothing():
    assert fetch_fragments_data(None, [_record("", description="")]) == []


def test_no_context_gives_nothing():
    assert fetch_fragments_data(None, None) == []
    assert fetch_fragments_data(None, []) == []


def test_existing_fragments_are_not_reemitted():
    records = [_record(_text(2500))]
    all_frags = fetch_fragments_data(None, records)

    class FakeTable:
        rows = [{"id": all_frags[0]["id"]}, {"id": all_frags[1]["id"]}]

    remaining = fetch_fragments_data(FakeTable(), records)
    assert [f["fragment_index"] for f in remaining] == [2]
