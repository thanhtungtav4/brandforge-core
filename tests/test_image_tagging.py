"""Tests for AI image tagging of library entries (2026-11-22).

Smith's direction: "nên có tính năng dùng AI gắn tag ảnh để dễ truy
xuất hơn" — the host model LOOKS at the image and supplies structured
tags; the core validates, stores and scores them, exactly the
record_vision_check split (no vision model inside the core, no
network). The weakness this attacks: library ranking is token overlap
between the slot's query tokens and the entry's tokens, so an entry
with a thin human description was nearly invisible to fulfilment.

Pinned here:
- the fixed tag discipline (subjects/objects are short noun phrases,
  capped counts; colours capped; scene capped; an empty tag set is
  refused) — validation lives in loaders and is shared by the tag
  tool and the add-with-tags path;
- re-tagging REPLACES the record and bumps a tag count (no full
  history is kept);
- ai_tags tokens (subjects + objects + scene + colours) join the
  entry token set library_score ranks on, at equal weight with the
  existing tags + description tokens — an entry whose ONLY match to
  a slot comes through its AI tags is picked, and untagged scoring
  is byte-for-byte the old behaviour;
- LIBRARY_MATCH_THRESHOLD is untouched (0.35, a design constant).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import mcp_server as srv  # noqa: E402
from content_core import images as img  # noqa: E402
from content_core import loaders  # noqa: E402

BRIEF = {"id": "tag-test", "format": "blog", "topic": "cà phê văn phòng",
         "keyword": "cà phê", "title": "Cà phê văn phòng"}

SECTIONS = [
    {"id": "s1", "heading": "Nguồn gốc hạt Arabica Cầu Đất",
     "intent": "origins", "target_words": 120, "text": "x"},
    {"id": "s2", "heading": "Pha tại văn phòng", "intent": "office-brewing",
     "target_words": 120, "text": "y"},
]

GOOD_TAGS = {"subjects": ["vườn arabica cầu đất"],
             "objects": ["hạt cà phê"],
             "scene": "nguồn gốc hạt arabica",
             "colours": ["nâu", "xanh lá"],
             "text_in_image": False}


@pytest.fixture(autouse=True)
def _isolated_stores(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_CORE_DRAFTS_DIR", str(tmp_path / "drafts"))
    monkeypatch.setenv("CONTENT_CORE_IMAGE_LIBRARY_DIR",
                       str(tmp_path / "image_library"))
    monkeypatch.setenv("CONTENT_CORE_BRAND_DRAFTS_DIR",
                       str(tmp_path / "brand_drafts"))


# -- the tag discipline (validation) -------------------------------------------

def test_validate_ai_tags_accepts_and_cleans():
    cleaned = loaders.validate_ai_tags(
        {"subjects": ["  vườn cà phê  "], "objects": [],
         "scene": "vườn buổi sáng", "colours": ["nâu"],
         "text_in_image": True})
    assert cleaned == {"subjects": ["vườn cà phê"], "objects": [],
                       "scene": "vườn buổi sáng", "colours": ["nâu"],
                       "text_in_image": True}
    # missing optional fields default cleanly
    cleaned2 = loaders.validate_ai_tags({"subjects": ["phin cà phê"]})
    assert cleaned2["objects"] == [] and cleaned2["scene"] == ""
    assert cleaned2["colours"] == [] and cleaned2["text_in_image"] is False


@pytest.mark.parametrize("bad", [
    {"subjects": ["một hai ba bốn năm"]},                # phrase > 4 words
    {"subjects": ["a"] * 9},                             # > 8 subjects
    {"objects": ["a"] * 11},                             # > 10 objects
    {"subjects": ["phin"], "colours": ["a"] * 7},        # > 6 colours
    {"subjects": ["phin"], "scene": " ".join(["từ"] * 13)},  # scene > 12 words
    {"subjects": ["phin"], "colours": ["một hai ba bốn"]},   # colour too long
    {},                                                  # empty tag set
    {"subjects": [], "objects": [], "scene": "", "colours": []},
    {"subjects": ["   "]},                               # blank-only phrase
    {"subjects": [42]},                                  # non-string item
    {"subjects": ["phin"], "text_in_image": "yes"},      # non-bool flag
    {"subjects": ["phin"], "bogus_key": 1},              # unknown key
    {"subjects": "phin cà phê"},                         # not a list
    "not-a-dict",
])
def test_validate_ai_tags_rejects(bad):
    with pytest.raises(ValueError):
        loaders.validate_ai_tags(bad)


@pytest.mark.parametrize("bad", [
    {"width": 0, "height": 100}, {"width": -5, "height": 100},
    {"width": True, "height": 100}, {"width": 100.5, "height": 100},
    {"width": "100", "height": 100}, {"width": 100},
    {"width": 100, "height": 100, "depth": 3}, "not-a-dict",
])
def test_validate_dimensions_rejects(bad):
    with pytest.raises(ValueError):
        loaders.validate_image_dimensions(bad)


def test_validate_dimensions_accepts():
    assert loaders.validate_image_dimensions(
        {"width": 1600, "height": 900}) == {"width": 1600, "height": 900}


# -- the tag tool ---------------------------------------------------------------

def _add_untagged():
    return srv.add_library_image(
        "moc-brew", "photos/vuon.jpg", "Ảnh chụp trên đồi cao buổi sớm",
        ["đồi"])


def test_tag_roundtrip_stamps_and_lists():
    entry = _add_untagged()
    assert "ai_tags" not in entry and "dimensions" not in entry
    out = srv.tag_library_image("moc-brew", "photos/vuon.jpg", GOOD_TAGS,
                                {"width": 1600, "height": 1200})
    assert "error" not in out, out
    tags = out["ai_tags"]
    assert tags["subjects"] == ["vườn arabica cầu đất"]
    assert tags["tagged_by"] == "host_model" and tags["tagged_at"]
    assert tags["tag_count"] == 1
    assert out["dimensions"] == {"width": 1600, "height": 1200}
    # the search surface (list) carries the additive fields
    listed = srv.list_library_images("moc-brew")["images"]
    assert listed[0]["ai_tags"]["subjects"] == ["vườn arabica cầu đất"]
    assert listed[0]["dimensions"] == {"width": 1600, "height": 1200}


def test_retag_replaces_and_counts():
    _add_untagged()
    srv.tag_library_image("moc-brew", "photos/vuon.jpg", GOOD_TAGS)
    out = srv.tag_library_image(
        "moc-brew", "photos/vuon.jpg",
        {"subjects": ["rẫy cà phê"], "objects": [], "scene": "",
         "colours": [], "text_in_image": False})
    assert out["ai_tags"]["subjects"] == ["rẫy cà phê"]
    assert out["ai_tags"]["tag_count"] == 2
    listed = srv.list_library_images("moc-brew")["images"]
    assert len(listed) == 1  # still one entry — replaced, not duplicated
    assert listed[0]["ai_tags"]["subjects"] == ["rẫy cà phê"]


def test_tag_unknown_ref_and_wrong_brand_fail_cleanly():
    _add_untagged()
    assert "error" in srv.tag_library_image(
        "moc-brew", "photos/khong-co.jpg", GOOD_TAGS)
    # the ref exists — under ANOTHER brand; tagging via this brand fails
    assert "error" in srv.tag_library_image(
        "cao-nguyen-moc", "photos/vuon.jpg", GOOD_TAGS)
    # and nothing was written anywhere
    assert "ai_tags" not in srv.list_library_images("moc-brew")["images"][0]


def test_tag_duplicate_ref_fails_cleanly():
    srv.add_library_image("moc-brew", "photos/trung.jpg",
                          "Mô tả thứ nhất cho ảnh trùng", [])
    srv.add_library_image("moc-brew", "photos/trung.jpg",
                          "Mô tả thứ hai khác hẳn cho ảnh trùng", [])
    assert len(srv.list_library_images("moc-brew")["images"]) == 2
    out = srv.tag_library_image("moc-brew", "photos/trung.jpg", GOOD_TAGS)
    assert "error" in out
    for e in srv.list_library_images("moc-brew")["images"]:
        assert "ai_tags" not in e  # ambiguous -> nothing tagged


def test_tag_invalid_payload_writes_nothing():
    _add_untagged()
    out = srv.tag_library_image("moc-brew", "photos/vuon.jpg",
                                {"subjects": ["một hai ba bốn năm"]})
    assert "error" in out
    out2 = srv.tag_library_image("moc-brew", "photos/vuon.jpg", GOOD_TAGS,
                                 {"width": 0, "height": 10})
    assert "error" in out2
    assert "ai_tags" not in srv.list_library_images("moc-brew")["images"][0]


# -- tags at add time (one-call import path) -------------------------------------

def test_add_with_tags_and_dimensions_one_call():
    entry = srv.add_library_image(
        "moc-brew", "photos/phin.jpg", "Phin cà phê trên bàn gỗ", ["phin"],
        ai_tags={"subjects": ["phin cà phê"], "objects": ["bàn gỗ"],
                 "scene": "", "colours": ["bạc"], "text_in_image": False},
        dimensions={"width": 1200, "height": 900})
    assert "error" not in entry, entry
    assert entry["ai_tags"]["subjects"] == ["phin cà phê"]
    assert entry["ai_tags"]["tag_count"] == 1
    assert entry["dimensions"] == {"width": 1200, "height": 900}


def test_add_with_invalid_tags_persists_nothing():
    out = srv.add_library_image(
        "moc-brew", "photos/x.jpg", "Mô tả hợp lệ", [],
        ai_tags={"subjects": []})
    assert "error" in out
    assert srv.list_library_images("moc-brew")["images"] == []


def test_readd_without_tags_preserves_existing_tags():
    srv.add_library_image("moc-brew", "photos/vuon.jpg",
                          "Ảnh chụp trên đồi cao buổi sớm", ["đồi"],
                          ai_tags=GOOD_TAGS)
    again = srv.add_library_image("moc-brew", "photos/vuon.jpg",
                                  "Ảnh chụp trên đồi cao buổi sớm", ["đồi"])
    assert again["ai_tags"]["subjects"] == ["vườn arabica cầu đất"]
    assert len(srv.list_library_images("moc-brew")["images"]) == 1


# -- retrieval: tags enter the ranking token set ----------------------------------

def _slot_s1():
    plan = img.plan_images(SECTIONS, BRIEF, srv._brand())
    return plan[1], BRIEF  # img-s1, the origins section


def _thin_entry():
    # description + tags share NO token with the s1 slot query
    return {"id": "lib-thin", "ref": "photos/vuon.jpg", "source": "client",
            "description": "Ảnh chụp trên đồi cao buổi sớm",
            "tags": ["đồi"]}


def test_untagged_scoring_is_the_old_formula():
    entry = _thin_entry()
    item, brief = _slot_s1()
    query = img.slot_query_tokens(item, brief)
    score, hits = img.library_score(entry, query)
    old_tokens = img._token_set(
        " ".join(entry["tags"]) + " " + entry["description"])
    assert hits == len(query & old_tokens)
    assert score == hits / len(query)


def test_tags_alone_can_carry_a_match():
    entry = _thin_entry()
    item, brief = _slot_s1()
    query = img.slot_query_tokens(item, brief)
    score0, hits0 = img.library_score(entry, query)
    assert hits0 == 0 and score0 < img.LIBRARY_MATCH_THRESHOLD
    assert img.best_library_match(item, brief, [entry], set()) is None
    tagged = dict(entry, ai_tags=dict(GOOD_TAGS, tagged_by="host_model",
                                      tagged_at="2026-11-22T00:00:00+00:00",
                                      tag_count=1))
    score1, hits1 = img.library_score(tagged, query)
    assert hits1 >= 3 and score1 >= img.LIBRARY_MATCH_THRESHOLD
    match = img.best_library_match(item, brief, [tagged], set())
    assert match is not None and match[0]["id"] == "lib-thin"


def test_fulfil_picks_entry_only_after_tagging():
    # a featured-perfect entry (fills img-featured untagged) + the thin
    # entry, which fits the origins slot ONLY through its AI tags
    srv.add_library_image("moc-brew", "photos/bia.jpg",
                          "Cà phê văn phòng", [])
    srv.add_library_image("moc-brew", "photos/vuon.jpg",
                          "Ảnh chụp trên đồi cao buổi sớm", ["đồi"])
    draft_id = srv.start_draft(BRIEF)["draft_id"]
    for sec in SECTIONS:
        srv.save_section(draft_id, sec["id"], sec["heading"], sec["text"],
                         intent=sec["intent"], target_words=120)
    srv.plan_images(draft_id)
    out1 = srv.fulfil_images_from_library(draft_id)
    gen1 = {g["image_id"] for g in out1["needs_generation"]}
    assert "img-s1" in gen1  # thin entry invisible to ranking untagged
    srv.tag_library_image("moc-brew", "photos/vuon.jpg", GOOD_TAGS)
    out2 = srv.fulfil_images_from_library(draft_id)
    by_slot = {f["image_id"]: f for f in out2["fulfilled"]}
    assert by_slot["img-s1"]["ref"] == "photos/vuon.jpg"
    assert by_slot["img-s1"]["score"] >= img.LIBRARY_MATCH_THRESHOLD


def test_threshold_constant_untouched():
    assert img.LIBRARY_MATCH_THRESHOLD == 0.35
