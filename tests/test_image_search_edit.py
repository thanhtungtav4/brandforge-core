"""Tests for the image search-edit feature (spec:
specs/image-search-edit-feature.md, §12 resolved 2026-11-23 under
Smith's Brandforge delegation — see pmdecisions.md).

The chain under test, per slot: brand library → searched real photos
from the brand's `image_sources` allowlist (candidate pool, max 5) →
vision vetting (watermark / wrong_subject / grade 0 reject) → AI edit
from the FIXED op vocabulary (product truthfulness: no op may change
the product, and watermark removal is not in the vocabulary at all) →
vision check with the grade-0 fallback chain → generation LAST, only
on the explicit allow_generation flag, marked generated in the
manifest.

Brands WITHOUT image_sources keep the v1 behaviour byte-identically —
that side is pinned by tests/test_images.py, which this file never
touches; this file pins the new behaviour and the boundary.
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

SECTIONS = [
    {"id": "s1", "heading": "Chợ nổi buổi sáng", "intent": "market-scene",
     "target_words": 120, "text": "x"},
    {"id": "s2", "heading": "Hũ mắm của tiệm", "intent": "own-product",
     "target_words": 120, "text": "y"},
]

BRIEF = {"id": "search-test", "format": "blog", "topic": "mắm cá lóc",
         "keyword": "mắm cá lóc", "title": "Mắm cá lóc Châu Đốc",
         "brand_id": "shop-test"}

ALLOWLIST = [
    {"name": "Pexels", "domains": ["pexels.com"],
     "license_types": ["stock_commercial"]},
    {"name": "Wikimedia Commons", "domains": ["wikimedia.org"],
     "license_types": ["cc_attribution", "public_domain"]},
    {"name": "Supplier Zalo group", "license_types": ["user_supplied"]},
]

SHOP_BRAND = {
    "brand": {"id": "shop-test", "name": "Shop Test"},
    "voice": {"traits": ["ấm áp", "gần gũi"], "pov": "first_person",
              "sentence_rules": {"median_words_default": [11, 14],
                                 "max_sentence_words": 25},
              "banned_terms": ["đẳng cấp thế giới", "curated"],
              "prohibited_claims": []},
    "visual": {"style_summary": "warm natural-light food photography",
               "palette": ["brown", "cream"], "medium": "photograph",
               "mood": "warm", "composition": "close-up",
               "avoid": ["studio look"]},
    "image_sources": ALLOWLIST,
}


@pytest.fixture(autouse=True)
def _isolated_stores(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_CORE_DRAFTS_DIR", str(tmp_path / "drafts"))
    monkeypatch.setenv("CONTENT_CORE_IMAGE_LIBRARY_DIR",
                       str(tmp_path / "image_library"))
    monkeypatch.setenv("CONTENT_CORE_BRAND_DRAFTS_DIR",
                       str(tmp_path / "brand_drafts"))
    monkeypatch.setenv("CONTENT_CORE_FACTS_PROFILES_DIR",
                       str(tmp_path / "facts_profiles"))


def _shop_draft(plan=True, product_specific=None) -> str:
    loaders.save_brand_draft(SHOP_BRAND)
    draft_id = srv.start_draft(BRIEF)["draft_id"]
    for sec in SECTIONS:
        srv.save_section(draft_id, sec["id"], sec["heading"], sec["text"],
                         intent=sec["intent"], target_words=120)
    if plan:
        out = srv.plan_images(draft_id, product_specific=product_specific)
        assert "error" not in out, out
    return draft_id


def _candidate(draft_id, slot="img-s1", url="https://www.pexels.com/photo/123",
               license_type="stock_commercial", **kw):
    return srv.add_image_candidate(draft_id, slot, url, license_type, **kw)


def _viable_candidate(draft_id, slot="img-s1",
                      url="https://www.pexels.com/photo/123"):
    out = _candidate(draft_id, slot, url)
    assert "error" not in out, out
    cid = out["candidate"]["id"]
    vet = srv.record_candidate_vetting(draft_id, slot, cid, 2, [])
    assert vet["candidate"]["status"] == "viable", vet
    return cid


# -- loaders: image_sources validation ------------------------------------

def test_validate_image_sources_normalises():
    out = loaders.validate_image_sources(
        [{"name": "Pexels", "domains": ["Pexels.COM", " images.pexels.com "],
          "license_types": ["stock_commercial"]}])
    assert out[0]["name"] == "Pexels"
    assert out[0]["domains"] == ["pexels.com", "images.pexels.com"]
    assert loaders.validate_image_sources([]) == []


@pytest.mark.parametrize("bad", [
    "pexels",                                            # not a list
    [{"domains": ["pexels.com"]}],                       # no name
    [{"name": "  "}],                                    # blank name
    [{"name": "X", "domains": "pexels.com"}],            # domains not a list
    [{"name": "X", "license_types": ["free_for_all"]}],  # license outside set
    [{"name": "X", "license_types": "stock_commercial"}],  # not a list
])
def test_validate_image_sources_rejects(bad):
    with pytest.raises(ValueError):
        loaders.validate_image_sources(bad)


def test_brand_draft_image_sources_round_trip_and_reject():
    loaders.save_brand_draft(SHOP_BRAND)
    loaded = loaders.load_brand_draft("shop-test")
    assert [s["name"] for s in loaded["image_sources"]] == [
        "Pexels", "Wikimedia Commons", "Supplier Zalo group"]
    with pytest.raises(ValueError):
        loaders.save_brand_draft(
            {"brand": {"id": "bad"}, "image_sources": [{"name": "X",
             "license_types": ["whatever"]}]})


def test_draft_to_brand_yaml_carries_image_sources():
    from content_core.brand_extract import draft_to_brand_yaml
    text = draft_to_brand_yaml(SHOP_BRAND)
    assert "image_sources" in text and "Pexels" in text


# -- registration: allowlist + license discipline (US-2) -------------------

def test_candidate_refused_without_allowlist():
    # Mộc Brew (the server brand) has no image_sources: the search
    # step is OFF and no pool is created.
    draft_id = srv.start_draft(
        {"id": "plain", "format": "blog", "topic": "cà phê",
         "keyword": "cà phê", "title": "Cà phê"})["draft_id"]
    srv.save_section(draft_id, "s1", "Pha phin", "x", intent="brewing")
    srv.plan_images(draft_id)
    out = srv.add_image_candidate(
        draft_id, "img-s1", "https://www.pexels.com/photo/1",
        "stock_commercial")
    assert "error" in out and "image_sources" in out["error"]
    draft = srv.get_draft(draft_id)
    assert not draft["images"].get("candidates")


@pytest.mark.parametrize("bad_license", ["", "unknown", "free_to_use",
                                         "creative_commons", None])
def test_candidate_refused_bad_license(bad_license):
    draft_id = _shop_draft()
    out = _candidate(draft_id, license_type=bad_license)
    assert "error" in out
    out2 = srv.fulfil_images_v2(draft_id)
    slot = next(s for s in out2["slots"] if s["image_id"] == "img-s1")
    assert slot["status"] == "awaiting_candidates"
    assert slot.get("pool_size", 0) == 0


def test_candidate_refused_off_allowlist_source():
    draft_id = _shop_draft()
    out = _candidate(draft_id, url="https://random-blog.example/photo.jpg")
    assert "error" in out and "allowlist" in out["error"]


def test_candidate_source_matched_by_name_and_subdomain():
    draft_id = _shop_draft()
    by_name = srv.add_image_candidate(
        draft_id, "img-s1", "https://cdn.example.org/x.jpg",
        "user_supplied", source_name="Supplier Zalo group")
    assert "error" not in by_name, by_name
    assert by_name["candidate"]["source_name"] == "Supplier Zalo group"
    by_host = _candidate(draft_id, url="https://images.pexels.com/photo/9")
    assert "error" not in by_host, by_host
    assert by_host["candidate"]["source_name"] == "Pexels"


def test_candidate_license_must_fit_source_entry():
    draft_id = _shop_draft()
    # The Pexels entry only allows stock_commercial.
    out = _candidate(draft_id, license_type="cc_attribution",
                     attribution_text="Photo by A")
    assert "error" in out


def test_candidate_pool_cap_five():
    draft_id = _shop_draft()
    for i in range(5):
        out = _candidate(draft_id, url=f"https://www.pexels.com/photo/{i}")
        assert "error" not in out, out
    sixth = _candidate(draft_id, url="https://www.pexels.com/photo/99")
    assert "error" in sixth and "5" in sixth["error"]


def test_candidate_duplicate_url_refused():
    draft_id = _shop_draft()
    assert "error" not in _candidate(draft_id)
    assert "error" in _candidate(draft_id)


# -- vetting (US-3) ----------------------------------------------------------

def test_vetting_rejects_watermark_wrong_subject_grade0():
    draft_id = _shop_draft()
    cases = [
        (["watermark"], 2, "watermark"),
        (["wrong_subject"], 2, "wrong_subject"),
        ([], 0, "grade 0"),
    ]
    for i, (issues, grade, needle) in enumerate(cases):
        out = _candidate(draft_id, url=f"https://www.pexels.com/photo/{50+i}")
        cid = out["candidate"]["id"]
        vet = srv.record_candidate_vetting(draft_id, "img-s1", cid,
                                           grade, issues)
        assert vet["candidate"]["status"] == "rejected", vet
        assert needle in vet["candidate"]["rejection_reason"]


def test_vetting_accepts_clean_and_validates_inputs():
    draft_id = _shop_draft()
    cid = _candidate(draft_id)["candidate"]["id"]
    vet = srv.record_candidate_vetting(
        draft_id, "img-s1", cid, 1, ["low_resolution"], notes="hơi mờ")
    assert vet["candidate"]["status"] == "viable"
    assert vet["candidate"]["vetting"]["issues"] == ["low_resolution"]
    assert "error" in srv.record_candidate_vetting(
        draft_id, "img-s1", cid, 2, ["remove_watermark"])
    assert "error" in srv.record_candidate_vetting(
        draft_id, "img-s1", cid, 3, [])
    assert "error" in srv.record_candidate_vetting(
        draft_id, "img-s1", cid, True, [])


def test_rejected_candidate_cannot_fulfil():
    draft_id = _shop_draft()
    cid = _candidate(draft_id)["candidate"]["id"]
    srv.record_candidate_vetting(draft_id, "img-s1", cid, 2, ["watermark"])
    out = srv.set_image(draft_id, "img-s1", "searched", "dl/photo.jpg",
                        candidate_id=cid)
    assert "error" in out and "rejected" in out["error"]


def test_unvetted_candidate_cannot_fulfil():
    draft_id = _shop_draft()
    cid = _candidate(draft_id)["candidate"]["id"]
    out = srv.set_image(draft_id, "img-s1", "searched", "dl/photo.jpg",
                        candidate_id=cid)
    assert "error" in out


# -- edit vocabulary + the watermark-removal ban ------------------------------

def test_edit_ops_vocabulary_enforced_at_set():
    draft_id = _shop_draft()
    cid = _viable_candidate(draft_id)
    for bad_ops in (["remove_watermark"], ["replace_background"],
                    ["change_product_color"], ["add_product"],
                    ["crop", "remove_watermark"]):
        out = srv.set_image(draft_id, "img-s1", "searched", "dl/photo.jpg",
                            candidate_id=cid, edit_ops=bad_ops)
        assert "error" in out, bad_ops
    ok = srv.set_image(draft_id, "img-s1", "searched", "dl/photo.jpg",
                       candidate_id=cid,
                       edit_ops=["crop", "color_grade", "background_cleanup"],
                       alt="Chợ nổi buổi sáng với ghe đầy trái cây",
                       description="Chợ nổi buổi sáng")
    assert "error" not in ok, ok
    assert ok["image"]["origin"]["kind"] == "searched_edited"
    assert ok["image"]["origin"]["edit_ops"] == [
        "crop", "color_grade", "background_cleanup"]


def test_watermark_removal_op_exists_nowhere():
    assert "remove_watermark" not in img.EDIT_OPS
    assert not any("watermark" in op for op in img.EDIT_OPS)


def test_stored_forbidden_edit_op_is_hard_issue():
    # A hand-planted record (bypassing set_image's write-side checks)
    # is re-validated by the checker, like stored vision grades (M4).
    draft_id = _shop_draft()
    cid = _viable_candidate(draft_id)
    srv.set_image(draft_id, "img-s1", "searched", "dl/photo.jpg",
                  candidate_id=cid, edit_ops=["crop"],
                  alt="Chợ nổi buổi sáng với ghe đầy trái cây",
                  description="Chợ nổi")
    draft = srv.get_draft(draft_id)
    draft["images"]["images"]["img-s1"]["origin"]["edit_ops"] = [
        "crop", "remove_watermark"]
    srv._store_draft(draft)
    chk = srv.image_check(draft_id, complete=True)
    assert any(i["image_id"] == "img-s1"
               and i["type"] == "edit_op_not_allowed" for i in chk["issues"])
    assert srv.get_image_manifest(draft_id)["ready"] is False


# -- fulfilment chain (US-1, US-4) ---------------------------------------------

def test_unfilled_slot_awaits_candidates_not_generation():
    draft_id = _shop_draft()
    out = srv.fulfil_images_v2(draft_id)
    assert out["needs_generation"] == []
    slot = next(s for s in out["slots"] if s["image_id"] == "img-s1")
    assert slot["status"] == "awaiting_candidates"


def test_candidates_ready_status_lists_viable():
    draft_id = _shop_draft()
    cid = _viable_candidate(draft_id)
    out = srv.fulfil_images_v2(draft_id)
    slot = next(s for s in out["slots"] if s["image_id"] == "img-s1")
    assert slot["status"] == "candidates_ready"
    assert slot["candidates"] == [cid]


def test_grade0_fallback_chain():
    draft_id = _shop_draft()
    cid1 = _viable_candidate(draft_id, url="https://www.pexels.com/photo/1")
    cid2 = _viable_candidate(draft_id, url="https://www.pexels.com/photo/2")
    # Edited fulfilment of candidate 1, then vision grade 0.
    srv.set_image(draft_id, "img-s1", "searched", "dl/edited.jpg",
                  candidate_id=cid1, edit_ops=["crop"],
                  alt="Chợ nổi buổi sáng với ghe đầy trái cây",
                  description="Chợ nổi")
    srv.record_vision_check(draft_id, "img-s1", 0, 2)
    out = srv.fulfil_images_v2(draft_id)
    slot = next(s for s in out["slots"] if s["image_id"] == "img-s1")
    assert slot["status"] == "reopened"
    assert slot["next_step"] == {"action": "try_unedited",
                                 "candidate_id": cid1}
    # The unedited original of the same candidate, also grade 0.
    srv.set_image(draft_id, "img-s1", "searched", "dl/original.jpg",
                  candidate_id=cid1, edit_ops=[],
                  alt="Chợ nổi buổi sáng với ghe đầy trái cây",
                  description="Chợ nổi")
    srv.record_vision_check(draft_id, "img-s1", 0, 0)
    out = srv.fulfil_images_v2(draft_id)
    slot = next(s for s in out["slots"] if s["image_id"] == "img-s1")
    assert slot["status"] == "reopened"
    assert slot["next_step"] == {"action": "try_candidate",
                                 "candidate_id": cid2}
    # Candidate 2 unedited, grade 0 too: the pool is exhausted.
    srv.set_image(draft_id, "img-s1", "searched", "dl/second.jpg",
                  candidate_id=cid2, edit_ops=[],
                  alt="Chợ nổi buổi sáng với ghe đầy trái cây",
                  description="Chợ nổi")
    srv.record_vision_check(draft_id, "img-s1", 0, 2)
    out = srv.fulfil_images_v2(draft_id)
    slot = next(s for s in out["slots"] if s["image_id"] == "img-s1")
    assert slot["status"] == "search_exhausted"
    assert out["needs_generation"] == []
    out = srv.fulfil_images_v2(draft_id, allow_generation=True)
    assert "img-s1" in [g["image_id"] for g in out["needs_generation"]]


def test_all_candidates_rejected_falls_to_generation_only_with_flag():
    draft_id = _shop_draft()
    cid = _candidate(draft_id)["candidate"]["id"]
    srv.record_candidate_vetting(draft_id, "img-s1", cid, 2,
                                 ["wrong_subject"])
    out = srv.fulfil_images_v2(draft_id)
    slot = next(s for s in out["slots"] if s["image_id"] == "img-s1")
    assert slot["status"] == "search_exhausted"
    assert slot["rejected"][0]["candidate_id"] == cid
    assert "wrong_subject" in slot["rejected"][0]["reason"]
    out = srv.fulfil_images_v2(draft_id, allow_generation=True)
    assert "img-s1" in [g["image_id"] for g in out["needs_generation"]]


def test_library_still_first_in_v2_and_stamps_origin():
    srv.add_library_image(
        "shop-test", "photos/cho-noi.jpg",
        "Chợ nổi buổi sáng với ghe đầy trái cây và rau củ",
        ["chợ nổi", "ghe", "trái cây"])
    draft_id = _shop_draft()
    out = srv.fulfil_images_v2(draft_id)
    assert out["fulfilled"][0]["image_id"] == "img-s1"
    assert out["fulfilled"][0]["origin"] == "library"
    man = srv.get_image_manifest(draft_id)
    entry = next(m for m in man["manifest"] if m["section_id"] == "s1")
    assert entry["origin"] == "library" and entry["generated"] is False


# -- manifest provenance (US-5) --------------------------------------------------

def _full_house_draft() -> str:
    """One draft with all three origins: library (s2 via plan order?
    no — s1 library), searched_edited (s2)... sections are s1/s2 plus
    featured; featured becomes the generated one."""
    srv.add_library_image(
        "shop-test", "photos/cho-noi.jpg",
        "Chợ nổi buổi sáng với ghe đầy trái cây và rau củ",
        ["chợ nổi", "ghe", "trái cây"])
    draft_id = _shop_draft()
    srv.fulfil_images_v2(draft_id)  # library fills img-s1
    cid = _viable_candidate(draft_id, slot="img-s2",
                            url="https://www.pexels.com/photo/77")
    srv.set_image(draft_id, "img-s2", "searched", "dl/hu-mam.jpg",
                  candidate_id=cid, edit_ops=["crop", "exposure"],
                  alt="Hũ mắm cá lóc đặt trên kệ gỗ trong tiệm",
                  description="Hũ mắm cá lóc")
    plan = srv.get_draft(draft_id)["images"]["plan"]
    srv.set_image(draft_id, "img-featured", "ai", "ai/cover.webp",
                  alt="Mắm cá lóc Châu Đốc và các món khô miền Tây",
                  caption="Đặc sản Châu Đốc", description="Ảnh bìa",
                  prompt_used=plan[0]["prompt"], mark_generated=True)
    return draft_id


def test_manifest_provenance_three_origins():
    draft_id = _full_house_draft()
    man = srv.get_image_manifest(draft_id)
    by_role = {m["role"]: m for m in man["manifest"]}
    content = {m["section_id"]: m for m in man["manifest"]
               if m["role"] == "in_content"}
    assert content["s1"]["origin"] == "library"
    assert content["s1"]["generated"] is False
    assert content["s2"]["origin"] == "searched_edited"
    assert content["s2"]["source_url"] == "https://www.pexels.com/photo/77"
    assert content["s2"]["license_type"] == "stock_commercial"
    assert content["s2"]["edit_ops"] == ["crop", "exposure"]
    assert content["s2"]["generated"] is False
    assert by_role["featured"]["origin"] == "generated"
    assert by_role["featured"]["generated"] is True
    assert man["ready"] is True, man["issues"]


def test_searched_unedited_origin_kind():
    draft_id = _shop_draft()
    cid = _viable_candidate(draft_id)
    srv.set_image(draft_id, "img-s1", "searched", "dl/raw.jpg",
                  candidate_id=cid,
                  alt="Chợ nổi buổi sáng với ghe đầy trái cây",
                  description="Chợ nổi")
    man = srv.get_image_manifest(draft_id)
    entry = next(m for m in man["manifest"] if m["section_id"] == "s1")
    assert entry["origin"] == "searched_unedited"
    assert entry["edit_ops"] == []


def test_manifest_license_missing_blocks_ready():
    draft_id = _shop_draft()
    cid = _viable_candidate(draft_id)
    srv.set_image(draft_id, "img-s1", "searched", "dl/photo.jpg",
                  candidate_id=cid, edit_ops=["crop"],
                  alt="Chợ nổi buổi sáng với ghe đầy trái cây",
                  description="Chợ nổi")
    draft = srv.get_draft(draft_id)
    draft["images"]["images"]["img-s1"]["origin"]["source_url"] = ""
    srv._store_draft(draft)
    chk = srv.image_check(draft_id, complete=True)
    assert any(i["type"] == "image_license_missing" for i in chk["issues"])
    assert srv.get_image_manifest(draft_id)["ready"] is False


# -- cc_attribution (§12 decision 4) --------------------------------------------

def test_cc_attribution_flows_to_caption():
    draft_id = _shop_draft()
    out = srv.add_image_candidate(
        draft_id, "img-s1", "https://upload.wikimedia.org/cho.jpg",
        "cc_attribution", attribution_text="Photo by Nguyen Van A, CC BY 4.0")
    cid = out["candidate"]["id"]
    srv.record_candidate_vetting(draft_id, "img-s1", cid, 2, [])
    srv.set_image(draft_id, "img-s1", "searched", "dl/cho.jpg",
                  candidate_id=cid,
                  alt="Chợ nổi buổi sáng với ghe đầy trái cây",
                  caption="Chợ nổi", description="Chợ nổi")
    man = srv.get_image_manifest(draft_id)
    entry = next(m for m in man["manifest"] if m["section_id"] == "s1")
    assert entry["license_type"] == "cc_attribution"
    assert entry["attribution_text"] == "Photo by Nguyen Van A, CC BY 4.0"
    assert "Nguyen Van A" in entry["caption"]
    assert not [i for i in man["issues"]
                if i["image_id"] == "img-s1"
                and i["type"] == "attribution_missing"]


def test_cc_attribution_missing_blocks_ready():
    draft_id = _shop_draft()
    out = srv.add_image_candidate(
        draft_id, "img-s1", "https://upload.wikimedia.org/cho.jpg",
        "cc_attribution")
    cid = out["candidate"]["id"]
    srv.record_candidate_vetting(draft_id, "img-s1", cid, 2, [])
    srv.set_image(draft_id, "img-s1", "searched", "dl/cho.jpg",
                  candidate_id=cid,
                  alt="Chợ nổi buổi sáng với ghe đầy trái cây",
                  description="Chợ nổi")
    chk = srv.image_check(draft_id, complete=True)
    assert any(i["type"] == "attribution_missing" for i in chk["issues"])
    assert srv.get_image_manifest(draft_id)["ready"] is False


# -- product_specific (US-6, §12 decision 2) --------------------------------------

def test_product_specific_slot_never_takes_candidates():
    draft_id = _shop_draft(product_specific=["s2"])
    plan = srv.get_draft(draft_id)["images"]["plan"]
    item = next(p for p in plan if p["id"] == "img-s2")
    assert item["product_specific"] is True
    out = srv.add_image_candidate(
        draft_id, "img-s2", "https://www.pexels.com/photo/5",
        "stock_commercial")
    assert "error" in out and "product_specific" in out["error"]
    # Fulfilment: no library match -> straight to the generation path,
    # never awaiting candidates.
    out = srv.fulfil_images_v2(draft_id)
    slot = next(s for s in out["slots"] if s["image_id"] == "img-s2")
    assert slot["status"] == "awaiting_generation_confirmation"
    out = srv.fulfil_images_v2(draft_id, allow_generation=True)
    assert "img-s2" in [g["image_id"] for g in out["needs_generation"]]


def test_plan_items_have_no_product_specific_key_by_default():
    # Differential shape pin: an unmarked plan is exactly the v1 plan.
    draft_id = _shop_draft()
    plan = srv.get_draft(draft_id)["images"]["plan"]
    assert all("product_specific" not in p for p in plan)


# -- boundary: the new layer stays advisory ---------------------------------------

def test_searched_layer_does_not_change_final_gate():
    srv.save_facts("shop-test", {"facts": {
        "price_vnd_per_250g": 100000, "pack_weight_g": 250,
        "origins": ["Châu Đốc"]}})
    draft_id = _shop_draft()
    before = srv.final_gate(draft_id)
    assert "passed" in before, before
    cid = _viable_candidate(draft_id)
    srv.set_image(draft_id, "img-s1", "searched", "dl/photo.jpg",
                  candidate_id=cid, edit_ops=["crop"],
                  alt="Chợ nổi buổi sáng", description="Chợ nổi")
    after = srv.final_gate(draft_id)
    assert before["passed"] == after["passed"]
    assert "images" not in after
