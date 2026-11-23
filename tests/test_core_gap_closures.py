"""Gap closures surfaced by App Round 8 (23 Nov 2026):

1. update_brand_draft — a partial patch over the EXISTING stored brand
   draft (save_brand_draft only builds a NEW draft from fetched pages;
   the app's structured brand-voice editor needs to edit in place
   without risking the human edits prepare_brand_setup preserves, S2).
2. get_persona(brand_id) — the per-brand persona read: get_persona used
   to return the Linh default unconditionally, while a brand's drafts
   resolve the persona named in their briefs (mambagiaokhoe → An).
3. list_quality_profiles — loaders.list_quality_profiles existed but no
   MCP tool exposed it, so the app could only look profiles up by id.

All stores are isolated to tmp dirs; the shipped persona cards are
seeded into the isolated personas dir so default resolution works.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import mcp_server as srv  # noqa: E402
from content_core import loaders  # noqa: E402
from content_core.quality import PRESETS  # noqa: E402

STORE_ENVS = (
    ("CONTENT_CORE_DRAFTS_DIR", "drafts"),
    ("CONTENT_CORE_BRAND_DRAFTS_DIR", "brand_drafts"),
    ("CONTENT_CORE_QUALITY_PROFILES_DIR", "quality_profiles"),
    ("CONTENT_CORE_PERSONAS_DIR", "personas"),
)


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    for var, sub in STORE_ENVS:
        monkeypatch.setenv(var, str(tmp_path / sub))
    pdir = tmp_path / "personas"
    pdir.mkdir(parents=True, exist_ok=True)
    for name in ("persona_linh.json", "persona_an.json"):
        (pdir / name).write_text(
            (PROJECT_DIR / "data" / name).read_text(encoding="utf-8"),
            encoding="utf-8")
    return tmp_path


def _seed_brand_draft(brand_id: str = "gap-brand") -> dict:
    draft = {
        "brand": {"id": brand_id, "name": "Gap Brand",
                  "review_status": "draft", "source_type": "own_site",
                  "source_urls": ["https://example.com"]},
        "voice": {"traits": [{"dimension": "directness",
                              "position": "nói thẳng"}],
                  "banned_terms": ["delve"],
                  "sentence_rules": {"max_sentence_words": 30}},
        "vocabulary": {"preferred": ["rang mộc"]},
        "examples": [{"text": "Ví dụ mẫu một.", "source_url": "https://example.com"}],
        "anti_examples": [],
        "qualitative": {"positioning": "HUMAN EDIT — giữ nguyên câu này"},
        "review_notes": ["note của người duyệt"],
        "inferred_fields": [{"field": "visual.palette",
                             "provenance": "setup_assist",
                             "value": ["#3e6b48"]}],
        "visual": {"palette": ["#3e6b48"], "medium": "photography"},
    }
    loaders.save_brand_draft(copy.deepcopy(draft))
    return draft


# -- GAP 1: update_brand_draft ----------------------------------------------

def test_update_brand_draft_patches_one_field_only(stores):
    seeded = _seed_brand_draft()
    out = srv.update_brand_draft(
        "gap-brand", {"voice": {"banned_terms": ["delve", "curated"]}})
    assert "error" not in out
    assert out["updated_fields"] == ["voice"]
    saved = loaders.load_brand_draft("gap-brand")
    # Shallow merge inside the patched section: the patched key is
    # replaced, its sibling keys survive.
    assert saved["voice"]["banned_terms"] == ["delve", "curated"]
    assert saved["voice"]["traits"] == seeded["voice"]["traits"]
    assert saved["voice"]["sentence_rules"] == seeded["voice"]["sentence_rules"]
    # Every unpatched top-level section is identical to the seed.
    for key in seeded:
        if key != "voice":
            assert saved[key] == seeded[key], key
    assert out["draft"] == saved


def test_update_brand_draft_preserves_human_edits_outside_patch(stores):
    seeded = _seed_brand_draft()
    out = srv.update_brand_draft("gap-brand",
                                 {"brand": {"name": "Gap Brand Mới"}})
    assert "error" not in out
    saved = loaders.load_brand_draft("gap-brand")
    assert saved["brand"]["name"] == "Gap Brand Mới"
    # The human's own sections are byte-for-byte what was seeded.
    assert saved["qualitative"] == seeded["qualitative"]
    assert saved["review_notes"] == seeded["review_notes"]
    assert saved["examples"] == seeded["examples"]


def test_update_brand_draft_list_replaced_only_when_provided(stores):
    seeded = _seed_brand_draft()
    srv.update_brand_draft("gap-brand", {"vocabulary": {"preferred": ["mộc"]}})
    saved = loaders.load_brand_draft("gap-brand")
    assert saved["examples"] == seeded["examples"]  # not in patch → kept
    out = srv.update_brand_draft("gap-brand", {"examples": []})
    assert "error" not in out
    assert loaders.load_brand_draft("gap-brand")["examples"] == []


def test_update_brand_draft_invalid_patch_refused_and_nothing_written(stores):
    _seed_brand_draft()
    path = loaders.brand_drafts_dir() / "gap-brand.json"
    before = path.read_text(encoding="utf-8")
    out = srv.update_brand_draft("gap-brand", {"visual": {"palette": [123]}})
    assert "error" in out  # validate_visual: palette items must be strings
    out = srv.update_brand_draft("gap-brand", {"examples": "not-a-list"})
    assert "error" in out  # validate_brand_draft: examples must be objects
    assert path.read_text(encoding="utf-8") == before


def test_update_brand_draft_unknown_brand_refused(stores):
    out = srv.update_brand_draft("nope-brand", {"brand": {"name": "X"}})
    assert "error" in out
    assert "nope-brand" in out["error"]
    assert loaders.load_brand_draft("nope-brand") is None


def test_update_brand_draft_stamps_preserved_unless_patched(stores):
    seeded = _seed_brand_draft()
    srv.update_brand_draft("gap-brand", {"vocabulary": {"preferred": ["x"]}})
    saved = loaders.load_brand_draft("gap-brand")
    # Not in the patch → the setup-assist stamps and the review state
    # survive exactly.
    assert saved["inferred_fields"] == seeded["inferred_fields"]
    assert saved["brand"]["review_status"] == "draft"
    # Explicitly patched → the patch wins.
    out = srv.update_brand_draft(
        "gap-brand", {"brand": {"review_status": "approved"}})
    assert "error" not in out
    saved = loaders.load_brand_draft("gap-brand")
    assert saved["brand"]["review_status"] == "approved"
    assert saved["brand"]["id"] == "gap-brand"


def test_update_brand_draft_brand_id_mismatch_refused(stores):
    _seed_brand_draft()
    out = srv.update_brand_draft(
        "gap-brand", {"brand": {"id": "other-brand", "name": "Hijack"}})
    assert "error" in out
    saved = loaders.load_brand_draft("gap-brand")
    assert saved["brand"]["name"] == "Gap Brand"
    assert loaders.load_brand_draft("other-brand") is None


def test_update_brand_draft_non_dict_patch_refused(stores):
    _seed_brand_draft()
    out = srv.update_brand_draft("gap-brand", ["voice"])
    assert "error" in out


# -- GAP 2: get_persona(brand_id) --------------------------------------------

def _start(brief: dict) -> str:
    return srv.start_draft(brief)["draft_id"]


def test_get_persona_no_brand_id_is_legacy_byte_identical(stores):
    out = srv.get_persona()
    assert out == loaders.load_persona()
    assert "persona_source" not in out
    assert out["data"]["name"] == "Linh"


def test_get_persona_brand_resolves_its_drafts_persona(stores):
    _seed_brand_draft("gap-brand")
    _start({"brand_id": "gap-brand", "persona_id": "An", "format": "blog"})
    _start({"brand_id": "gap-brand", "persona_id": "An", "format": "blog"})
    _start({"brand_id": "gap-brand", "format": "blog"})  # names none
    out = srv.get_persona("gap-brand")
    assert out["data"]["name"] == "An"
    assert out["persona_source"] == "brand"


def test_get_persona_brand_without_persona_gets_marked_default(stores):
    _seed_brand_draft("plain-brand")
    _start({"brand_id": "plain-brand", "format": "blog"})
    out = srv.get_persona("plain-brand")
    assert out["data"]["name"] == "Linh"
    assert out["persona_source"] == "server_default"


def test_get_persona_server_brand_is_marked_default(stores):
    out = srv.get_persona("moc-brew")
    assert out["data"]["name"] == "Linh"
    assert out["persona_source"] == "server_default"


def test_get_persona_unknown_brand_refused(stores):
    out = srv.get_persona("nope-brand")
    assert "error" in out
    assert "nope-brand" in out["error"]


def test_get_persona_conflicting_draft_personas_refused(stores):
    _seed_brand_draft("split-brand")
    _start({"brand_id": "split-brand", "persona_id": "An", "format": "blog"})
    _start({"brand_id": "split-brand", "persona_id": "Linh", "format": "blog"})
    out = srv.get_persona("split-brand")
    assert "error" in out
    assert "An" in out["error"] and "Linh" in out["error"]


# -- GAP 3: list_quality_profiles ---------------------------------------------

def test_list_quality_profiles_empty(stores):
    out = srv.list_quality_profiles()
    assert out == {"profiles": [], "count": 0}


def test_list_quality_profiles_returns_saved_with_weights(stores):
    weights = dict(PRESETS["blog"])
    weights["facts"], weights["seo"] = weights["seo"], weights["facts"]
    saved = srv.save_quality_profile("blog", weights)
    assert "error" not in saved
    out = srv.list_quality_profiles()
    assert out["count"] == 1
    entry = out["profiles"][0]
    assert entry["id"] == saved["id"]
    assert entry["format"] == "blog"
    assert entry["weights"] == saved["weights"]
    assert entry["pass_total"] == saved["pass_total"]
    assert entry["corrupt"] is False


def test_list_quality_profiles_marks_corrupt(stores):
    d = loaders.quality_profiles_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "broken.json").write_text("{not json", encoding="utf-8")
    out = srv.list_quality_profiles()
    assert out["count"] == 1
    assert out["profiles"][0]["id"] == "broken"
    assert out["profiles"][0]["corrupt"] is True
