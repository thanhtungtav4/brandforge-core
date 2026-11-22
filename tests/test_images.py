"""Tests for the article-images feature (2026-11-22).

Layers under test:
- content_core/images.py — pure planning / suggestions / checking /
  library ranking / manifest building (no IO, no generation: image
  generation is the host writer's job, exactly like text writing);
- loaders image-library store — per-brand refs-only store with the
  usual stored-profile discipline (validated ids, atomic writes);
- mcp_server image tools — plan / set / check / manifest over stored
  drafts, library add/list, library-first fulfilment.

Design pinned here: hybrid source (client photos first via the brand's
library, AI generation for the rest), every AI prompt carries the
brand's visual-style block verbatim (style lock), and the image layer
is ADVISORY — it never enters final_gate's verdict.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import mcp_server as srv  # noqa: E402
from content_core import images as img  # noqa: E402

BRIEF = {"id": "img-test", "format": "blog", "topic": "cà phê văn phòng",
         "keyword": "cà phê", "title": "Cà phê văn phòng"}

SECTIONS = [
    {"id": "s1", "heading": "Nguồn gốc hạt Arabica Cầu Đất",
     "intent": "origins", "target_words": 120, "text": "x"},
    {"id": "s2", "heading": "Pha tại văn phòng", "intent": "office-brewing",
     "target_words": 120, "text": "y"},
]

OTHER_BRAND = {
    "brand": {"id": "test-brand", "name": "Test Brand"},
    "visual": {"style_summary": "flat vector illustration, two-tone",
               "palette": ["#0044FF", "white"], "medium": "illustration",
               "mood": "playful, bold",
               "composition": "centered icon-like subject",
               "avoid": ["photorealism", "gradients"]},
}


@pytest.fixture(autouse=True)
def _isolated_stores(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_CORE_DRAFTS_DIR", str(tmp_path / "drafts"))
    monkeypatch.setenv("CONTENT_CORE_IMAGE_LIBRARY_DIR",
                       str(tmp_path / "image_library"))
    monkeypatch.setenv("CONTENT_CORE_BRAND_DRAFTS_DIR",
                       str(tmp_path / "brand_drafts"))


def _moc_brand() -> dict:
    return srv._brand()


def _plan(brand=None):
    return img.plan_images(SECTIONS, BRIEF, brand if brand is not None else _moc_brand())


_VC = {"content_grade": 2, "style_grade": 2, "notes": "",
       "checked_at": "2026-11-22T00:00:00+00:00"}


def _good_images(plan) -> dict:
    """A fully valid fulfilment of the 3-slot Mộc Brew plan."""
    return {
        "img-featured": {
            "source": "client", "ref": "photos/goi-ca-phe.jpg",
            "filename": "ca-phe-van-phong.webp",
            "alt": "Gói cà phê Mộc Brew 250g đặt cạnh phin trên bàn gỗ",
            "caption": "Gói 250g và phin pha tại văn phòng",
            "description": "Ảnh gói cà phê Mộc Brew 250g trên bàn gỗ",
            "prompt_used": "", "from_library": None, "alt_suggested": False,
            "vision_check": dict(_VC)},
        "img-s1": {
            "source": "ai", "ref": "ai/vuon-arabica.webp",
            "filename": "ca-phe-nguon-goc-hat-arabica-cau-dat.webp",
            "alt": "Vườn cà phê Arabica ở Cầu Đất vào buổi sáng sớm",
            "caption": "", "description": "Vườn Arabica Cầu Đất",
            "prompt_used": plan[1]["prompt"], "from_library": None,
            "alt_suggested": False, "vision_check": dict(_VC)},
        "img-s2": {
            "source": "client", "ref": "photos/pha-phin.jpg",
            "filename": "ca-phe-pha-tai-van-phong.webp",
            "alt": "Pha cà phê bằng phin ngay tại bàn làm việc",
            "caption": "", "description": "Phin cà phê trên bàn làm việc",
            "prompt_used": "", "from_library": None, "alt_suggested": False,
            "vision_check": dict(_VC)},
    }


# -- planning ---------------------------------------------------------------

def test_plan_shape_cap_and_budgets():
    plan = _plan()
    assert [p["id"] for p in plan] == ["img-featured", "img-s1", "img-s2"]
    assert plan[0]["role"] == "featured" and plan[0]["section_id"] is None
    assert plan[0]["aspect"] == "16:9" and plan[0]["size_budget_kb"] == 200
    assert plan[1]["role"] == "in_content" and plan[1]["section_id"] == "s1"
    assert plan[1]["aspect"] == "4:3" and plan[1]["size_budget_kb"] == 150
    assert all(p["format"] == "webp" for p in plan)
    assert all(p["source_preference"] == "client_photo_or_ai" for p in plan)
    many = [{"id": f"s{i}", "heading": f"Phần {i}", "intent": "", "text": ""}
            for i in range(1, 10)]
    capped = img.plan_images(many, BRIEF, _moc_brand())
    assert len(capped) == 1 + img.MAX_IN_CONTENT  # featured + cap


def test_plan_prompts_carry_identical_style_block():
    plan = _plan()
    block = img.style_block(_moc_brand()["visual"])
    assert block  # Mộc Brew ships a seeded visual style
    for item in plan:
        assert item["style_block"] == block
        assert item["prompt"].endswith(block)
    scenes = {item["prompt"][: -len(block)] for item in plan}
    assert len(scenes) == len(plan)  # scenes differ, style block identical


def test_plan_prompts_differ_between_brands():
    a = img.plan_images(SECTIONS, BRIEF, _moc_brand())
    b = img.plan_images(SECTIONS, BRIEF, OTHER_BRAND)
    assert a[0]["style_block"] != b[0]["style_block"]
    assert a[0]["prompt"] != b[0]["prompt"]
    assert "illustration" in b[0]["style_block"]


def test_plan_without_visual_has_no_style_block():
    bare = {"brand": {"id": "bare", "name": "Bare"}}
    plan = img.plan_images(SECTIONS, BRIEF, bare)
    assert all(p["style_block"] == "" for p in plan)
    issues, _ = img.check_images(plan, _good_images(plan), BRIEF, bare,
                                 complete=True)
    assert not [i for i in issues if i["type"] == "style_not_locked"]


# -- suggestions --------------------------------------------------------------

def test_suggest_filename_slug_rules():
    plan = _plan()
    assert img.suggest_filename(plan[0], BRIEF) == "ca-phe.webp"
    name = img.suggest_filename(plan[1], BRIEF)
    assert name.startswith("ca-phe-") and name.endswith(".webp")
    assert name == name.lower() and " " not in name


def test_suggest_alt_is_marked_template_output():
    plan = _plan()
    alt = img.suggest_alt(plan[0], BRIEF)
    assert alt and len(alt) <= img.ALT_SOFT_LIMIT
    from_desc = img.suggest_alt(plan[1], BRIEF,
                                description="Vườn cà phê Arabica ở Cầu Đất. "
                                            "Chụp lúc sáng sớm, sương còn đọng.")
    assert from_desc.startswith("Vườn cà phê Arabica ở Cầu Đất")
    assert "sương" not in from_desc  # first sentence only


# -- checker: the original hard issues ----------------------------------------

def test_check_good_images_pass_complete():
    plan = _plan()
    issues, flagged = img.check_images(plan, _good_images(plan), BRIEF,
                                       _moc_brand(), complete=True)
    assert issues == []
    assert flagged == []  # featured has caption+description, all local
    # refs are non-existent paths -> no file checks fire


def test_check_missing_image_only_in_complete_mode():
    plan = _plan()
    partial = {"img-featured": _good_images(plan)["img-featured"]}
    issues, _ = img.check_images(plan, partial, BRIEF, _moc_brand())
    assert not [i for i in issues if i["type"] == "missing_image"]
    issues, _ = img.check_images(plan, partial, BRIEF, _moc_brand(),
                                 complete=True)
    missing = {i["image_id"] for i in issues if i["type"] == "missing_image"}
    assert missing == {"img-s1", "img-s2"}


def test_check_missing_and_duplicate_alt():
    plan = _plan()
    images = _good_images(plan)
    images["img-s1"]["alt"] = ""
    images["img-s2"]["alt"] = images["img-featured"]["alt"]
    issues, _ = img.check_images(plan, images, BRIEF, _moc_brand(),
                                 complete=True)
    types = {(i["image_id"], i["type"]) for i in issues}
    assert ("img-s1", "missing_alt") in types
    assert ("img-s2", "duplicate_alt") in types


def test_check_duplicate_alt_is_diacritics_insensitive():
    plan = _plan()
    images = _good_images(plan)
    images["img-s2"]["alt"] = "Goi ca phe Moc Brew 250g dat canh phin tren ban go"
    issues, _ = img.check_images(plan, images, BRIEF, _moc_brand(),
                                 complete=True)
    assert any(i["type"] == "duplicate_alt" for i in issues)


def test_check_stuffed_alt():
    plan = _plan()
    images = _good_images(plan)
    images["img-s1"]["alt"] = "Cà phê cà phê ngon cho người mê cà phê"
    issues, _ = img.check_images(plan, images, BRIEF, _moc_brand(),
                                 complete=True)
    assert any(i["image_id"] == "img-s1" and i["type"] == "stuffed_alt"
               for i in issues)
    images = _good_images(plan)
    images["img-s1"]["alt"] = "cà phê"  # nothing but the keyword
    issues, _ = img.check_images(plan, images, BRIEF, _moc_brand(),
                                 complete=True)
    assert any(i["type"] == "stuffed_alt" for i in issues)


def test_check_filename_rules():
    plan = _plan()
    images = _good_images(plan)
    images["img-s1"]["filename"] = "Vuon Arabica!!.JPG"
    images["img-featured"]["filename"] = "anh-bia-dep.webp"  # no keyword
    issues, _ = img.check_images(plan, images, BRIEF, _moc_brand(),
                                 complete=True)
    types = {(i["image_id"], i["type"]) for i in issues}
    assert ("img-s1", "bad_filename") in types
    assert ("img-featured", "featured_filename_missing_keyword") in types


def test_check_featured_reused_in_content():
    plan = _plan()
    images = _good_images(plan)
    images["img-s2"]["ref"] = images["img-featured"]["ref"]
    issues, _ = img.check_images(plan, images, BRIEF, _moc_brand(),
                                 complete=True)
    assert any(i["image_id"] == "img-s2" and i["type"] == "featured_reused"
               for i in issues)


def test_check_too_many_images():
    plan = _plan()
    images = _good_images(plan)
    images["img-extra"] = dict(images["img-s2"], ref="photos/khac.jpg",
                               alt="Một ảnh hoàn toàn khác hẳn")
    issues, _ = img.check_images(plan, images, BRIEF, _moc_brand(),
                                 complete=True)
    assert any(i["image_id"] == "img-extra" and i["type"] == "too_many_images"
               for i in issues)


def test_check_alt_length_flag_and_hard_limit():
    plan = _plan()
    images = _good_images(plan)
    images["img-s1"]["alt"] = "Vườn cà phê " + "rất đẹp " * 20  # ~190 chars
    assert 125 < len(images["img-s1"]["alt"]) <= 200
    issues, flagged = img.check_images(plan, images, BRIEF, _moc_brand(),
                                       complete=True)
    assert not [i for i in issues if i["image_id"] == "img-s1"]
    assert any(f["type"] == "alt_long" for f in flagged)
    images["img-s1"]["alt"] = "Vườn cà phê " + "rất đẹp " * 30  # > 200
    issues, _ = img.check_images(plan, images, BRIEF, _moc_brand(),
                                 complete=True)
    assert any(i["type"] == "alt_too_long" for i in issues)


def test_check_flagged_caption_description_and_local_file(tmp_path):
    plan = _plan()
    images = _good_images(plan)
    images["img-featured"]["caption"] = ""
    images["img-featured"]["description"] = ""
    big = tmp_path / "big.bmp"
    big.write_bytes(b"x" * (300 * 1024))
    images["img-s2"]["ref"] = str(big)
    issues, flagged = img.check_images(plan, images, BRIEF, _moc_brand(),
                                       complete=True)
    types = {f["type"] for f in flagged}
    assert "missing_caption" in types and "missing_description" in types
    assert "file_over_budget" in types and "file_format" in types
    assert issues == []


# -- checker: style lock --------------------------------------------------------

def test_style_not_locked_fires_for_direct_ai_without_prompt():
    plan = _plan()
    images = _good_images(plan)
    images["img-s1"]["prompt_used"] = ""
    issues, _ = img.check_images(plan, images, BRIEF, _moc_brand(),
                                 complete=True)
    assert any(i["image_id"] == "img-s1" and i["type"] == "style_not_locked"
               for i in issues)


def test_style_not_locked_fires_for_wrong_block():
    plan = _plan()
    images = _good_images(plan)
    other = img.plan_images(SECTIONS, BRIEF, OTHER_BRAND)
    images["img-s1"]["prompt_used"] = other[1]["prompt"]  # other brand's block
    issues, _ = img.check_images(plan, images, BRIEF, _moc_brand(),
                                 complete=True)
    assert any(i["type"] == "style_not_locked" for i in issues)


def test_style_block_mismatch_between_ai_images():
    plan = _plan()
    images = _good_images(plan)
    images["img-s2"] = dict(images["img-s1"], ref="ai/khac.webp",
                            filename="ca-phe-khac.webp",
                            alt="Một khung cảnh khác hẳn về cà phê")
    other = img.plan_images(SECTIONS, BRIEF, OTHER_BRAND)
    images["img-s2"]["prompt_used"] = other[2]["prompt"]
    issues, _ = img.check_images(plan, images, BRIEF, _moc_brand(),
                                 complete=True)
    assert any(i["type"] == "style_block_mismatch" for i in issues)


def test_library_and_client_images_need_no_prompt():
    plan = _plan()
    images = _good_images(plan)
    images["img-s1"] = dict(images["img-s1"], source="ai",
                            from_library="lib-abc123", prompt_used="")
    issues, _ = img.check_images(plan, images, BRIEF, _moc_brand(),
                                 complete=True)
    assert not [i for i in issues if i["type"] == "style_not_locked"]


# -- library store --------------------------------------------------------------

def test_library_add_list_roundtrip():
    entry = srv.add_library_image(
        "moc-brew", "photos/goi.jpg", "Gói cà phê Mộc Brew 250g trên bàn gỗ",
        ["cà phê", "gói 250g"])
    assert entry["source"] == "client" and entry["id"].startswith("lib-")
    assert entry["tags"] == ["cà phê", "gói 250g"] and entry["added_at"]
    listed = srv.list_library_images("moc-brew")
    assert [e["id"] for e in listed["images"]] == [entry["id"]]
    assert srv.list_library_images("brand-khac")["images"] == []


def test_library_validation_errors_are_structured():
    assert "error" in srv.add_library_image("moc-brew", "", "mô tả", [])
    assert "error" in srv.add_library_image("moc-brew", "a.jpg", "", [])
    assert "error" in srv.add_library_image(
        "moc-brew", "a.jpg", "mô tả", [], source="stock")
    assert "error" in srv.add_library_image("x" * 201, "a.jpg", "mô tả", [])
    assert "error" in srv.list_library_images("x" * 201)
    assert "error" in srv.add_library_image("moc-brew", "a.jpg", "mô tả",
                                            tags="khong-phai-list")


def test_library_store_is_per_brand_and_idempotent_id():
    a = srv.add_library_image("moc-brew", "photos/a.jpg", "Ảnh A", [])
    b = srv.add_library_image("cao-nguyen-moc", "photos/a.jpg", "Ảnh A", [])
    assert a["id"] == b["id"]  # id derives from ref+description
    assert len(srv.list_library_images("moc-brew")["images"]) == 1
    assert len(srv.list_library_images("cao-nguyen-moc")["images"]) == 1
    again = srv.add_library_image("moc-brew", "photos/a.jpg", "Ảnh A", [])
    assert again["id"] == a["id"]
    assert len(srv.list_library_images("moc-brew")["images"]) == 1


# -- MCP draft flow ---------------------------------------------------------------

def _draft_with_sections() -> str:
    draft_id = srv.start_draft(BRIEF)["draft_id"]
    for sec in SECTIONS:
        srv.save_section(draft_id, sec["id"], sec["heading"], sec["text"],
                         intent=sec["intent"], target_words=120)
    return draft_id


def test_mcp_plan_set_check_manifest_flow():
    draft_id = _draft_with_sections()
    out = srv.plan_images(draft_id)
    assert [p["id"] for p in out["plan"]] == ["img-featured", "img-s1", "img-s2"]
    plan = out["plan"]

    r = srv.set_image(draft_id, "img-featured", "client", "photos/goi.jpg",
                      alt="Gói cà phê Mộc Brew 250g đặt cạnh phin trên bàn gỗ",
                      caption="Gói 250g và phin pha tại văn phòng",
                      description="Ảnh gói cà phê Mộc Brew 250g trên bàn gỗ")
    assert r["image"]["filename"] == "ca-phe.webp"  # suggested, keyword-led
    assert r["image"]["alt_suggested"] is False
    srv.set_image(draft_id, "img-s1", "ai", "ai/vuon.webp",
                  alt="Vườn cà phê Arabica ở Cầu Đất vào buổi sáng sớm",
                  description="Vườn Arabica Cầu Đất",
                  prompt_used=plan[1]["prompt"])
    r2 = srv.set_image(draft_id, "img-s2", "client", "photos/pha.jpg")
    assert r2["image"]["alt_suggested"] is True and r2["image"]["alt"]

    chk = srv.image_check(draft_id, complete=True)
    assert chk["ok"] and chk["issues"] == [], chk
    man = srv.get_image_manifest(draft_id)
    assert man["ready"] is True
    assert [m["role"] for m in man["manifest"]] == [
        "featured", "in_content", "in_content"]
    assert man["manifest"][0]["alt_text"].startswith("Gói cà phê")
    assert set(man["manifest"][0]) == {
        "role", "section_id", "ref", "filename", "alt_text", "caption",
        "description"}


def test_mcp_set_image_validation():
    draft_id = _draft_with_sections()
    assert "error" in srv.set_image(draft_id, "img-s1", "ai", "x.webp")
    srv.plan_images(draft_id)  # no plan yet -> the error above; now plan
    assert "error" in srv.set_image(draft_id, "img-nope", "ai", "x.webp")
    assert "error" in srv.set_image(draft_id, "img-s1", "stock", "x.webp")
    assert "error" in srv.set_image(draft_id, "img-s1", "ai", "")
    assert "error" in srv.plan_images("d-khong-ton-tai")
    assert "error" in srv.image_check("d-khong-ton-tai")


def test_mcp_image_check_reports_broken_variants():
    draft_id = _draft_with_sections()
    plan = srv.plan_images(draft_id)["plan"]
    srv.set_image(draft_id, "img-featured", "client", "photos/goi.jpg",
                  alt="Gói cà phê Mộc Brew trên bàn gỗ cạnh phin",
                  caption="c", description="d")
    srv.set_image(draft_id, "img-s1", "client", "photos/goi.jpg",  # reuse!
                  alt="Cà phê cà phê cho người mê cà phê")  # stuffed
    srv.set_image(draft_id, "img-s2", "client", "photos/pha.jpg",
                  alt="Gói cà phê Mộc Brew trên bàn gỗ cạnh phin")  # dup alt
    chk = srv.image_check(draft_id, complete=True)
    types = {i["type"] for i in chk["issues"]}
    assert {"featured_reused", "stuffed_alt", "duplicate_alt"} <= types
    assert chk["ok"] is False
    assert srv.get_image_manifest(draft_id)["ready"] is False
    assert plan  # silence unused warning


# -- library-first fulfilment ------------------------------------------------------

def _stock_library():
    srv.add_library_image(
        "moc-brew", "photos/goi-ca-phe.jpg",
        "Gói cà phê Mộc Brew 250g trên bàn gỗ cạnh chiếc phin",
        ["cà phê", "gói 250g", "phin"])
    srv.add_library_image(
        "moc-brew", "ai/vuon-arabica.webp",
        "Vườn cà phê Arabica ở Cầu Đất vào buổi sáng",
        ["arabica", "cầu đất", "vườn cà phê"], source="ai")
    srv.add_library_image(
        "moc-brew", "photos/meo.jpg", "Con mèo nằm ngủ trên ghế sofa",
        ["mèo", "sofa"])


def test_fulfil_picks_relevant_never_double_uses():
    _stock_library()
    draft_id = _draft_with_sections()
    srv.plan_images(draft_id)
    out = srv.fulfil_images_from_library(draft_id)
    by_slot = {f["image_id"]: f for f in out["fulfilled"]}
    assert by_slot["img-featured"]["ref"] == "photos/goi-ca-phe.jpg"
    assert by_slot["img-s1"]["ref"] == "ai/vuon-arabica.webp"
    refs = [f["ref"] for f in out["fulfilled"]]
    assert len(refs) == len(set(refs))  # one entry never fulfils two slots
    entry = out["images"]["img-featured"]
    assert entry["from_library"] and entry["source"] == "client"
    assert entry["alt_suggested"] is True
    assert "Gói cà phê" in entry["alt"]  # suggested from entry description
    gen = {g["image_id"] for g in out["needs_generation"]}
    assert gen == {"img-s2"}  # office-brewing slot: nothing relevant


def test_fulfil_below_threshold_gets_style_locked_prompt():
    _stock_library()
    draft_id = _draft_with_sections()
    plan = srv.plan_images(draft_id)["plan"]
    out = srv.fulfil_images_from_library(draft_id)
    gen = {g["image_id"]: g for g in out["needs_generation"]}
    block = plan[0]["style_block"]
    assert block and gen["img-s2"]["prompt"].endswith(block)
    # fulfilling again is a no-op for already-filled slots
    out2 = srv.fulfil_images_from_library(draft_id)
    assert out2["fulfilled"] == []


def test_fulfil_requires_plan():
    draft_id = _draft_with_sections()
    assert "error" in srv.fulfil_images_from_library(draft_id)


def test_manifest_shape_unchanged_by_library_flow():
    _stock_library()
    draft_id = _draft_with_sections()
    srv.plan_images(draft_id)
    srv.fulfil_images_from_library(draft_id)
    man = srv.get_image_manifest(draft_id)
    assert man["ready"] is False  # img-s2 still unfulfilled
    assert [m["role"] for m in man["manifest"]] == ["featured", "in_content"]
    for entry in man["manifest"]:
        assert set(entry) == {"role", "section_id", "ref", "filename",
                              "alt_text", "caption", "description"}


# -- vision check (judged layer) -------------------------------------------------

def test_vision_unchecked_is_flagged_not_blocking():
    plan = _plan()
    images = _good_images(plan)
    del images["img-s1"]["vision_check"]
    issues, flagged = img.check_images(plan, images, BRIEF, _moc_brand(),
                                       complete=True)
    assert issues == []
    assert any(f["image_id"] == "img-s1" and f["type"] == "vision_unchecked"
               for f in flagged)
    # non-complete mode does not nag about unchecked images
    _, flagged2 = img.check_images(plan, images, BRIEF, _moc_brand())
    assert not [f for f in flagged2 if f["type"] == "vision_unchecked"]


def test_vision_grade_zero_is_hard_issue():
    plan = _plan()
    images = _good_images(plan)
    images["img-s1"]["vision_check"] = {
        "content_grade": 0, "style_grade": 2, "notes": "ảnh ra vườn chè",
        "checked_at": "2026-11-22T00:00:00+00:00"}
    images["img-s2"]["vision_check"] = {
        "content_grade": 2, "style_grade": 0, "notes": "màu neon, sai hẳn",
        "checked_at": "2026-11-22T00:00:00+00:00"}
    issues, _ = img.check_images(plan, images, BRIEF, _moc_brand(),
                                 complete=True)
    types = {(i["image_id"], i["type"]) for i in issues}
    assert ("img-s1", "vision_content_mismatch") in types
    assert ("img-s2", "vision_style_mismatch") in types


def test_vision_grade_one_passes_with_notes_flagged():
    plan = _plan()
    images = _good_images(plan)
    images["img-s1"]["vision_check"] = {
        "content_grade": 1, "style_grade": 2, "notes": "hơi tối ở góc trái",
        "checked_at": "2026-11-22T00:00:00+00:00"}
    issues, flagged = img.check_images(plan, images, BRIEF, _moc_brand(),
                                       complete=True)
    assert issues == []
    hits = [f for f in flagged if f["type"] == "vision_reservations"]
    assert hits and "hơi tối ở góc trái" in hits[0]["detail"]


def test_vision_applies_to_library_photos_too():
    plan = _plan()
    images = _good_images(plan)
    images["img-s1"] = dict(images["img-s1"], from_library="lib-xyz",
                            prompt_used="",
                            vision_check={"content_grade": 0,
                                          "style_grade": 2,
                                          "notes": "ảnh thư viện chụp sản phẩm khác",
                                          "checked_at": "2026-11-22T00:00:00+00:00"})
    issues, _ = img.check_images(plan, images, BRIEF, _moc_brand(),
                                 complete=True)
    assert any(i["type"] == "vision_content_mismatch" for i in issues)
    assert not [i for i in issues if i["type"] == "style_not_locked"]


def test_record_vision_check_roundtrip_and_validation():
    draft_id = _draft_with_sections()
    srv.plan_images(draft_id)
    srv.set_image(draft_id, "img-s1", "client", "photos/a.jpg",
                  alt="Vườn cà phê Arabica ở Cầu Đất vào buổi sáng sớm")
    out = srv.record_vision_check(draft_id, "img-s1", 2, 1, "hơi tối")
    assert out["vision_check"]["content_grade"] == 2
    assert out["vision_check"]["style_grade"] == 1
    assert out["vision_check"]["checked_at"]
    stored = srv.get_draft(draft_id)["images"]["images"]["img-s1"]
    assert stored["vision_check"]["notes"] == "hơi tối"
    # strict grade validation: out of range, wrong type, bool-as-int
    for bad in (3, -1, "2", True, 1.5, None):
        assert "error" in srv.record_vision_check(draft_id, "img-s1", bad, 2), bad
        assert "error" in srv.record_vision_check(draft_id, "img-s1", 2, bad), bad
    # unfulfilled slot / unknown draft
    assert "error" in srv.record_vision_check(draft_id, "img-s2", 2, 2)
    assert "error" in srv.record_vision_check("d-khong-co", "img-s1", 2, 2)


def test_vision_grade_zero_blocks_manifest_ready():
    draft_id = _draft_with_sections()
    plan = srv.plan_images(draft_id)["plan"]
    srv.set_image(draft_id, "img-featured", "client", "photos/goi.jpg",
                  alt="Gói cà phê Mộc Brew 250g đặt cạnh phin trên bàn gỗ",
                  caption="c", description="d")
    srv.set_image(draft_id, "img-s1", "ai", "ai/vuon.webp",
                  alt="Vườn cà phê Arabica ở Cầu Đất vào buổi sáng sớm",
                  description="Vườn Arabica", prompt_used=plan[1]["prompt"])
    srv.set_image(draft_id, "img-s2", "client", "photos/pha.jpg",
                  alt="Pha cà phê bằng phin ngay tại bàn làm việc",
                  description="Phin trên bàn")
    man = srv.get_image_manifest(draft_id)
    assert man["ready"] is True  # unchecked warns, does not block
    assert any(w["type"] == "vision_unchecked" for w in man["warnings"])
    srv.record_vision_check(draft_id, "img-s1", 0, 2, "sinh nhầm vườn chè")
    man = srv.get_image_manifest(draft_id)
    assert man["ready"] is False
    assert any(i["type"] == "vision_content_mismatch" for i in man["issues"])
    srv.record_vision_check(draft_id, "img-s1", 2, 2)
    assert srv.get_image_manifest(draft_id)["ready"] is True


# -- review round 2 fixes (M1-M4) ---------------------------------------------

def test_check_duplicate_ref_between_content_slots():
    # M1: the uniqueness unit is the REF — two in-content slots filled
    # with the same photo is a hard issue even though neither is the
    # featured image (the old checker only had featured-vs-content).
    plan = _plan()
    images = _good_images(plan)
    images["img-s2"]["ref"] = images["img-s1"]["ref"]
    issues, _ = img.check_images(plan, images, BRIEF, _moc_brand(),
                                 complete=True)
    assert any(i["image_id"] == "img-s2" and i["type"] == "duplicate_ref"
               for i in issues)


def test_check_duplicate_ref_normalises_local_paths():
    # M1: "./ai/x.webp" and "ai/x.webp" are the same file — the ref
    # comparison is normalised, not raw-string.
    plan = _plan()
    images = _good_images(plan)
    images["img-s2"]["ref"] = "./" + images["img-s1"]["ref"]
    issues, _ = img.check_images(plan, images, BRIEF, _moc_brand(),
                                 complete=True)
    assert any(i["image_id"] == "img-s2" and i["type"] == "duplicate_ref"
               for i in issues)


def test_fulfil_never_uses_same_ref_for_two_slots():
    # M1, the review's end-to-end repro: the same photo added to the
    # library twice under different descriptions is two ENTRIES, but
    # its ref may still fill only ONE slot.
    srv.add_library_image(
        "moc-brew", "photos/shared.jpg",
        "Gói cà phê văn phòng Mộc Brew trên bàn gỗ", [])
    srv.add_library_image(
        "moc-brew", "photos/shared.jpg",
        "Vườn cà phê Arabica ở Cầu Đất vào buổi sáng",
        ["arabica", "cầu đất", "vườn cà phê"], source="ai")
    draft_id = _draft_with_sections()
    srv.plan_images(draft_id)
    out = srv.fulfil_images_from_library(draft_id)
    refs = [f["ref"] for f in out["fulfilled"]]
    assert len(refs) == len(set(refs))  # no ref fulfils two slots
    assert refs.count("photos/shared.jpg") == 1
    gen = {g["image_id"] for g in out["needs_generation"]}
    # img-s1's only fitting entry is the second shared.jpg record —
    # its ref is already used by the featured slot, so it must wait
    # for generation instead of duplicating the photo.
    assert "img-s1" in gen
    chk = srv.image_check(draft_id, complete=True)
    assert not [i for i in chk["issues"] if i["type"] == "duplicate_ref"]


def test_duplicate_ref_via_set_image_blocks_manifest():
    # M1 via plain set_image (no library involved).
    draft_id = _draft_with_sections()
    plan = srv.plan_images(draft_id)["plan"]
    srv.set_image(draft_id, "img-featured", "client", "photos/goi.jpg",
                  alt="Gói cà phê Mộc Brew 250g đặt cạnh phin trên bàn gỗ",
                  caption="c", description="d")
    srv.set_image(draft_id, "img-s1", "ai", "photos/shared.jpg",
                  alt="Vườn cà phê Arabica ở Cầu Đất vào buổi sáng sớm",
                  description="Vườn Arabica", prompt_used=plan[1]["prompt"])
    srv.set_image(draft_id, "img-s2", "client", "photos/shared.jpg",
                  alt="Pha cà phê bằng phin ngay tại bàn làm việc",
                  description="Phin trên bàn")
    chk = srv.image_check(draft_id, complete=True)
    assert any(i["image_id"] == "img-s2" and i["type"] == "duplicate_ref"
               for i in chk["issues"])
    assert srv.get_image_manifest(draft_id)["ready"] is False


def test_no_visual_brand_ai_without_prompt_not_style_locked():
    # M2: a brand with NO visual section has no style block, so there
    # is no style lock to break — an AI image without prompt_used must
    # NOT raise the hard style_not_locked issue (module contract).
    bare = {"brand": {"id": "bare", "name": "Bare"}}
    plan = img.plan_images(SECTIONS, BRIEF, bare)
    images = _good_images(plan)
    images["img-s1"]["prompt_used"] = ""
    issues, _ = img.check_images(plan, images, BRIEF, bare, complete=True)
    assert not [i for i in issues if i["type"] == "style_not_locked"]


def test_style_block_string_palette_not_garbled():
    # M3: the report's repro — palette written as a plain string (the
    # natural way in YAML) must render as items, never per-character.
    block = img.style_block({"medium": "photo",
                             "palette": "warm earth tones",
                             "avoid": "neon, clutter"})
    assert "warm earth tones" in block
    assert "w, a, r, m" not in block
    assert "neon" in block and "clutter" in block


def test_validate_visual_normalises_and_rejects():
    # M3: load-time validation/normalisation of the visual section.
    from content_core import loaders
    out = loaders.validate_visual({"palette": "brown, cream",
                                   "avoid": "neon"})
    assert out["palette"] == ["brown", "cream"]  # comma-separated string
    assert out["avoid"] == ["neon"]
    out = loaders.validate_visual({"palette": "warm earth tones"})
    assert out["palette"] == ["warm earth tones"]  # no comma -> one item
    out = loaders.validate_visual({"palette": ["#fff", "black"]})
    assert out["palette"] == ["#fff", "black"]  # proper list untouched
    with pytest.raises(ValueError, match="palette"):
        loaders.validate_visual({"palette": 42})
    with pytest.raises(ValueError, match="palette"):
        loaders.validate_visual({"palette": ["ok", 7]})
    with pytest.raises(ValueError, match="medium"):
        loaders.validate_visual({"medium": ["photo"]})
    with pytest.raises(ValueError, match="visual"):
        loaders.validate_visual("not-a-dict")


def test_load_brand_normalises_string_palette(tmp_path):
    # M3 at the YAML load boundary.
    from content_core import loaders
    path = tmp_path / "brand.yaml"
    path.write_text(
        'brand:\n  id: yaml-brand\n  name: YAML Brand\n'
        'visual:\n  medium: photo\n  palette: "warm earth tones"\n',
        encoding="utf-8")
    brand = loaders.load_brand(path)
    assert brand["visual"]["palette"] == ["warm earth tones"]
    assert "warm earth tones" in img.style_block(brand["visual"])


def test_brand_draft_visual_normalised_at_save_and_load():
    # M3 at the brand-draft persistence boundary.
    from content_core import loaders
    loaders.save_brand_draft(
        {"brand": {"id": "vbrand", "name": "V"},
         "visual": {"palette": "nâu, kem", "avoid": ["neon"]}})
    loaded = loaders.load_brand_draft("vbrand")
    assert loaded["visual"]["palette"] == ["nâu", "kem"]
    with pytest.raises(ValueError, match="palette"):
        loaders.save_brand_draft(
            {"brand": {"id": "vbad"}, "visual": {"palette": 7}})


@pytest.mark.parametrize("bad_grades", [
    {"content_grade": 3, "style_grade": 2},
    {"content_grade": 2, "style_grade": -1},
    {"content_grade": 5, "style_grade": 5},
    {"content_grade": True, "style_grade": True},
    {"content_grade": "2", "style_grade": 2},
])
def test_check_vision_grade_invalid_stored(bad_grades):
    # M4: the checker re-validates STORED vision grades — a record
    # whose grades are not strict ints in 0..2 (hand-edited / legacy
    # draft JSON) is invalid: a hard issue in complete mode, and it
    # never counts as checked in either mode.
    plan = _plan()
    images = _good_images(plan)
    images["img-s1"]["vision_check"] = {
        **bad_grades, "notes": "", "checked_at": "2026-11-22T00:00:00+00:00"}
    issues, flagged = img.check_images(plan, images, BRIEF, _moc_brand(),
                                       complete=True)
    assert any(i["image_id"] == "img-s1"
               and i["type"] == "vision_grade_invalid" for i in issues)
    assert not [f for f in flagged if f["image_id"] == "img-s1"
                and f["type"] in ("vision_reservations", "vision_unchecked")]
    issues2, flagged2 = img.check_images(plan, images, BRIEF, _moc_brand())
    assert not [i for i in issues2 if i["image_id"] == "img-s1"]
    assert not [f for f in flagged2 if f["image_id"] == "img-s1"]


def test_manifest_blocked_by_tampered_vision_grade():
    # M4 end to end: a grade of 3 planted in the stored draft JSON
    # (bypassing record_vision_check's write-side validation) must
    # block manifest readiness.
    draft_id = _draft_with_sections()
    plan = srv.plan_images(draft_id)["plan"]
    srv.set_image(draft_id, "img-featured", "client", "photos/goi.jpg",
                  alt="Gói cà phê Mộc Brew 250g đặt cạnh phin trên bàn gỗ",
                  caption="c", description="d")
    srv.set_image(draft_id, "img-s1", "ai", "ai/vuon.webp",
                  alt="Vườn cà phê Arabica ở Cầu Đất vào buổi sáng sớm",
                  description="Vườn Arabica", prompt_used=plan[1]["prompt"])
    srv.set_image(draft_id, "img-s2", "client", "photos/pha.jpg",
                  alt="Pha cà phê bằng phin ngay tại bàn làm việc",
                  description="Phin trên bàn")
    srv.record_vision_check(draft_id, "img-s1", 2, 2)
    assert srv.get_image_manifest(draft_id)["ready"] is True
    draft = srv.get_draft(draft_id)
    draft["images"]["images"]["img-s1"]["vision_check"]["content_grade"] = 3
    srv._store_draft(draft)
    man = srv.get_image_manifest(draft_id)
    assert man["ready"] is False
    assert any(i["type"] == "vision_grade_invalid" for i in man["issues"])


# -- advisory boundary ---------------------------------------------------------------

def test_images_do_not_change_final_gate(tmp_path, monkeypatch):
    # A draft whose images are a mess still gets the gate verdict its
    # TEXT earns: the image layer is advisory in this round.
    draft_id = _draft_with_sections()
    srv.plan_images(draft_id)
    before = srv.final_gate(draft_id)
    srv.set_image(draft_id, "img-featured", "client", "photos/x.jpg",
                  alt="cà phê cà phê cà phê")
    after = srv.final_gate(draft_id)
    assert before["passed"] == after["passed"]
    assert "images" not in after
