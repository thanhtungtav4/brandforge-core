"""Fail-first tests for `list_taste_entries` (App Round 9 follow-up):
get_taste_status returns verdict COUNTS only, so the app cannot
re-list a brand's pending taste articles after a page reload. The
listing tool returns one summary per entry — id, ref, verdict,
imported/marked stamps, word count — in the taste store's house
shape. A corrupt ENTRY is listed as corrupt (the house list
discipline: never silently skipped, never a crash); a corrupt
document file is the structured error every taste tool returns.

All stores point at tmp dirs — no live store is touched.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import mcp_server as srv  # noqa: E402
from content_core import loaders  # noqa: E402


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_CORE_TASTE_DIR", str(tmp_path / "taste"))
    monkeypatch.setenv("CONTENT_CORE_DRAFTS_DIR", str(tmp_path / "drafts"))
    monkeypatch.setenv("CONTENT_CORE_STYLE_PROFILES_DIR",
                       str(tmp_path / "style_profiles"))
    return tmp_path


def _article(tmp_path: Path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


TEXT_A = "Bài một về mắm cá lóc Châu Đốc ngon lắm đó nghe."
TEXT_B = "Bài hai kể chuyện mùa nước nổi ở quê em năm nay."
TEXT_C = "Bài ba hướng dẫn chọn khô cá sặc bổi cho đúng hàng ngon."


def test_empty_brand_lists_nothing(stores):
    out = srv.list_taste_entries("brand-x")
    assert out == {"brand_id": "brand-x", "entries": [], "count": 0}


def test_entries_listed_with_mixed_verdicts(stores, tmp_path):
    fa = _article(tmp_path, "a.md", TEXT_A)
    fb = _article(tmp_path, "b.md", TEXT_B)
    fc = _article(tmp_path, "c.md", TEXT_C)
    srv.import_taste_articles("brand-x", [fa, fb, fc])
    srv.mark_article_verdict("brand-x", "approved_unchanged",
                             file_ref=fa)
    srv.mark_article_verdict("brand-x", "edited", file_ref=fb,
                             edited_text=TEXT_B + " Đã sửa.")

    out = srv.list_taste_entries("brand-x")
    assert out["brand_id"] == "brand-x"
    assert out["count"] == 3
    by_ref = {e["ref"]: e for e in out["entries"]}
    assert set(by_ref) == {fa, fb, fc}
    # The still-pending one: the app needs exactly this row back
    # after a reload — id, verdict, import stamp, word count.
    pending = by_ref[fc]
    assert pending["verdict"] == "pending"
    assert pending["id"].startswith("taste-")
    assert pending["imported_at"]
    assert pending["marked_at"] is None
    assert pending["word_count"] == len(TEXT_C.split())
    assert pending["corrupt"] is False
    approved = by_ref[fa]
    assert approved["verdict"] == "approved_unchanged"
    assert approved["marked_at"]
    assert approved["word_count"] == len(TEXT_A.split())
    edited = by_ref[fb]
    assert edited["verdict"] == "edited"
    assert edited["marked_at"]


def test_corrupt_entry_is_listed_as_corrupt(stores, tmp_path):
    fa = _article(tmp_path, "a.md", TEXT_A)
    srv.import_taste_articles("brand-x", [fa])
    # Hand-corrupt the stored doc: one entry loses its verdict,
    # another is not even an object. The good entry must still
    # list; the broken ones surface as corrupt, never vanish.
    path = loaders.taste_dir() / "brand-x.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["entries"].append({"id": "taste-broken", "ref": "gone.md",
                           "text": "văn bản không có phán quyết"})
    doc["entries"].append("not-an-object")
    path.write_text(json.dumps(doc, ensure_ascii=False),
                    encoding="utf-8")

    out = srv.list_taste_entries("brand-x")
    assert "error" not in out
    assert out["count"] == 3
    good = [e for e in out["entries"] if not e["corrupt"]]
    bad = [e for e in out["entries"] if e["corrupt"]]
    assert len(good) == 1 and good[0]["ref"] == fa
    assert len(bad) == 2
    assert all("index" in e for e in bad)


def test_corrupt_doc_file_is_a_structured_error(stores):
    path = loaders.taste_dir()
    path.mkdir(parents=True, exist_ok=True)
    (path / "brand-x.json").write_text("{not json", encoding="utf-8")
    out = srv.list_taste_entries("brand-x")
    assert "error" in out
    assert out["brand_id"] == "brand-x"
