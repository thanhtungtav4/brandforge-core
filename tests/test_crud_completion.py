"""Fail-first tests for the CRUD-completion round (23 Nov 2026):
save_persona + the delete tools the surface audit found missing.

Discipline pinned here:

- save_persona validates through the persona loader rules (Card V2:
  an object whose `data` carries a non-empty name and correctly
  typed fields) BEFORE anything is written — an invalid card writes
  nothing; creating a new id and updating an existing one both work,
  and the result says which happened.
- EVERY delete tool requires confirm=True for the destructive step:
  without it the tool returns a preview of what WOULD be deleted
  (or blocked) and deletes nothing.
- Guarded deletes refuse even WITH confirm, naming the reference:
  a style profile a registered corpus points at, or a brand's taste
  profile; a corpus a brand has chosen; an SEO profile in state
  `exported`; the built-in quality presets; the server-default
  brand; a brand with dependent stores (listed as blockers — no
  cascade deletes, ever).
- remove_library_image MOVES a core-managed image file (under the
  image library / inbox dirs) into a `_removed/` subfolder instead
  of unlinking it; URL and external refs just drop the record.
- delete_backup takes a bare .zip filename only — the same
  traversal guards as restore_core_data.
- delete_wordpress_target never touches the secrets file.

All tests run against temp store roots (every CONTENT_CORE_*_DIR
pointed at tmp_path), like the rest of the suite.
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

STORE_ENVS = (
    ("CONTENT_CORE_DRAFTS_DIR", "drafts"),
    ("CONTENT_CORE_BRAND_DRAFTS_DIR", "brand_drafts"),
    ("CONTENT_CORE_FACTS_PROFILES_DIR", "facts"),
    ("CONTENT_CORE_STYLE_PROFILES_DIR", "style_profiles"),
    ("CONTENT_CORE_QUALITY_PROFILES_DIR", "quality_profiles"),
    ("CONTENT_CORE_IMAGE_LIBRARY_DIR", "image_library"),
    ("CONTENT_CORE_IMAGE_LIBRARY_INBOX_DIR", "image_library_inbox"),
    ("CONTENT_CORE_PERSONAS_DIR", "personas"),
    ("CONTENT_CORE_RESEARCH_PACKS_DIR", "research_packs"),
    ("CONTENT_CORE_CORPUS_REGISTRY_DIR", "corpus_registry"),
    ("CONTENT_CORE_CORPUS_CHOICES_DIR", "corpus_choices"),
    ("CONTENT_CORE_TASTE_DIR", "taste"),
    ("CONTENT_CORE_WP_TARGETS_DIR", "wordpress_targets"),
    ("CONTENT_CORE_BRAND_SETUPS_DIR", "brand_setups"),
    ("CONTENT_CORE_BACKUPS_DIR", "backups"),
    ("CONTENT_CORE_SEO_PROFILES_DIR", "seo_profiles"),
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


ARTICLE = ("Bếp nhà Châu Đốc buổi sớm thơm mùi mắm chưng. " * 6
           + "Nồi đất đặt trên bếp củi, hơi bốc lên nghi ngút. " * 6)
SAMPLES = [ARTICLE, ARTICLE.replace("mắm", "khô cá"),
           ARTICLE.replace("Châu Đốc", "miệt vườn")]


def _persona(name="Bé Ba", **data_overrides):
    data = {"name": name,
            "description": "Người kể chuyện miệt vườn.",
            "personality": "Ấm áp, ít chữ mà thấm.",
            "scenario": "Quán nhỏ ven sông.",
            "first_mes": "Chào anh chị, em là Bé Ba.",
            "mes_example": "", "system_prompt": "",
            "post_history_instructions": "",
            "alternate_greetings": [], "tags": ["mien-tay"],
            "character_version": "1.0", "creator": "test",
            "creator_notes": "", "extensions": {}}
    data.update(data_overrides)
    return {"spec": "chara_card_v2", "spec_version": "2.0", "data": data}


# ---------------------------------------------------------------- save_persona

def test_save_persona_creates_new(stores):
    result = srv.save_persona(_persona())
    assert "error" not in result, result
    assert result["created"] is True
    assert result["persona_id"] == "Bé Ba"
    card = loaders.load_persona_by_id("Bé Ba")
    assert card["data"]["description"] == "Người kể chuyện miệt vườn."
    assert (stores / "personas" / "persona_be-ba.json").exists() or \
        any(p.name.startswith("persona_b") for p in
            (stores / "personas").glob("persona_*.json"))


def test_save_persona_updates_existing(stores):
    card = _persona("An", description="Bản mô tả mới.")
    result = srv.save_persona(card)
    assert "error" not in result, result
    assert result["created"] is False
    stored = loaders.load_persona_by_id("An")
    assert stored["data"]["description"] == "Bản mô tả mới."
    # No duplicate file for the same persona.
    names = [p.name for p in (stores / "personas").glob("persona_*.json")]
    assert names.count("persona_an.json") == 1
    assert len(names) == 2


@pytest.mark.parametrize("bad", [
    {"data": {"description": "thiếu tên"}},
    {"data": {"name": "  "}},
    {"spec": "chara_card_v2", "spec_version": "2.0"},
    _persona("X", description=42),
    _persona("X", tags="khong-phai-list"),
    _persona("X", alternate_greetings=[1, 2]),
    {"spec": "other_spec", "spec_version": "9.9",
     "data": {"name": "X"}},
    ["not", "an", "object"],
])
def test_save_persona_refuses_invalid(stores, bad):
    before = sorted(p.name for p in (stores / "personas").glob("*.json"))
    result = srv.save_persona(bad)
    assert "error" in result, result
    after = sorted(p.name for p in (stores / "personas").glob("*.json"))
    assert before == after  # nothing written


# ------------------------------------------------------- remove_library_image

def _seed_library_image(stores, ref, brand="brand-x"):
    return srv.add_library_image(brand, ref, "Ảnh thử nghiệm cho vòng CRUD",
                                  tags=["test"], source="client")


def test_remove_library_image_moves_core_managed_file(stores):
    img_dir = stores / "image_library" / "brand-x"
    img_dir.mkdir(parents=True)
    photo = img_dir / "photo.jpg"
    photo.write_bytes(b"\xff\xd8fake-jpeg")
    added = _seed_library_image(stores, str(photo))
    image_id = added["id"]

    preview = srv.remove_library_image("brand-x", image_id)
    assert preview.get("needs_confirm") is True, preview
    assert loaders.load_image_library("brand-x")["images"], "preview deleted!"

    result = srv.remove_library_image("brand-x", image_id, confirm=True)
    assert "error" not in result, result
    assert result["file_action"] == "moved"
    assert loaders.load_image_library("brand-x")["images"] == []
    assert not photo.exists()
    moved = Path(result["file_moved_to"])
    assert moved.exists() and moved.read_bytes() == b"\xff\xd8fake-jpeg"
    assert moved.parent.name == "_removed"


def test_remove_library_image_url_ref_drops_record_only(stores):
    added = _seed_library_image(stores, "https://example.com/a.jpg")
    result = srv.remove_library_image("brand-x", added["id"], confirm=True)
    assert "error" not in result, result
    assert result["file_action"] == "external"
    assert loaders.load_image_library("brand-x")["images"] == []


def test_remove_library_image_unknown_id(stores):
    _seed_library_image(stores, "https://example.com/a.jpg")
    result = srv.remove_library_image("brand-x", "lib-khong-co", confirm=True)
    assert "error" in result, result
    assert len(loaders.load_image_library("brand-x")["images"]) == 1


# -------------------------------------------------------- delete_style_profile

def test_delete_style_profile_roundtrip(stores):
    profile = srv.save_style_profile("Giọng Xoá Thử", SAMPLES)
    pid = profile["id"]
    preview = srv.delete_style_profile(pid)
    assert preview.get("needs_confirm") is True, preview
    assert loaders.load_style_profile(pid) is not None
    result = srv.delete_style_profile(pid, confirm=True)
    assert "error" not in result, result
    assert loaders.load_style_profile(pid) is None


def test_delete_style_profile_refused_for_corpus_profile(stores):
    profile = srv.save_style_profile("Giọng Kho Văn Phong", SAMPLES)
    pid = profile["id"]
    reg = srv.register_corpus("kho-thu", 1, "Kho thử", "genre",
                              pid, "test basis", 1000)
    assert "error" not in reg, reg
    result = srv.delete_style_profile(pid, confirm=True)
    assert "error" in result and "kho-thu" in result["error"], result
    assert loaders.load_style_profile(pid) is not None


def test_delete_style_profile_refused_for_taste_profile(stores):
    loaders.save_style_profile({"id": "brand-x-taste-voice"})
    loaders.save_taste_doc({
        "brand_id": "brand-x", "entries": [],
        "taste_profile": {"profile_id": "brand-x-taste-voice",
                          "built_from": 3,
                          "built_at": "2026-11-23T00:00:00+00:00"}})
    result = srv.delete_style_profile("brand-x-taste-voice", confirm=True)
    assert "error" in result and "brand-x" in result["error"], result
    assert loaders.load_style_profile("brand-x-taste-voice") is not None


def test_delete_style_profile_unknown(stores):
    result = srv.delete_style_profile("khong-co-profile-nay", confirm=True)
    assert "error" in result, result


# ------------------------------------------------------ delete_quality_profile

WEIGHTS = {"facts": 18, "voice": 14, "clarity": 14, "coherence": 14,
           "seo": 22, "persuasion": 8, "depth": 10}


def test_delete_quality_profile_custom_only(stores):
    saved = srv.save_quality_profile("blog", WEIGHTS,
                                     profile_id="custom-xoa-thu")
    assert "error" not in saved, saved
    preview = srv.delete_quality_profile("custom-xoa-thu")
    assert preview.get("needs_confirm") is True, preview
    assert loaders.load_quality_profile("custom-xoa-thu") is not None
    result = srv.delete_quality_profile("custom-xoa-thu", confirm=True)
    assert "error" not in result, result
    assert loaders.load_quality_profile("custom-xoa-thu") is None


@pytest.mark.parametrize("preset_id", ["blog", "preset-blog", "product"])
def test_delete_quality_profile_refuses_presets(stores, preset_id):
    result = srv.delete_quality_profile(preset_id, confirm=True)
    assert "error" in result, result


def test_delete_quality_profile_unknown(stores):
    result = srv.delete_quality_profile("custom-khong-co", confirm=True)
    assert "error" in result, result


# ------------------------------------------------------------ unregister_corpus

def test_unregister_corpus_roundtrip_keeps_style_profile(stores):
    profile = srv.save_style_profile("Giọng Kho B", SAMPLES)
    pid = profile["id"]
    srv.register_corpus("kho-b", 1, "Kho B", "genre", pid,
                        "test basis", 1000)
    preview = srv.unregister_corpus("kho-b")
    assert preview.get("needs_confirm") is True, preview
    assert loaders.load_corpus_entry("kho-b") is not None
    result = srv.unregister_corpus("kho-b", confirm=True)
    assert "error" not in result, result
    assert result["style_profile_deleted"] is False
    assert loaders.load_corpus_entry("kho-b") is None
    assert loaders.load_style_profile(pid) is not None


def test_unregister_corpus_refused_while_chosen(stores):
    profile = srv.save_style_profile("Giọng Kho C", SAMPLES)
    srv.register_corpus("kho-c", 1, "Kho C", "genre", profile["id"],
                        "test basis", 1000)
    choice = srv.set_corpus_choice("brand-x", "kho-c")
    assert "error" not in choice, choice
    result = srv.unregister_corpus("kho-c", confirm=True)
    assert "error" in result and "brand-x" in result["error"], result
    assert loaders.load_corpus_entry("kho-c") is not None


def test_unregister_corpus_unknown(stores):
    result = srv.unregister_corpus("kho-khong-co", confirm=True)
    assert "error" in result, result


# ------------------------------------------------------------ delete_seo_profile

SEO_CONTEXT = {"market": "VN", "language": "vi", "location": "Hồ Chí Minh",
               "device": "desktop", "searched_at": "2026-11-23"}


def _seed_seo_profile(stores, brand="brand-x"):
    draft = srv.start_draft({"brand_id": brand, "title": "Bài SEO thử",
                             "keyword": "khô cá lóc", "format": "blog"})
    assert "error" not in draft, draft
    created = srv.create_seo_profile(draft["draft_id"], "khô cá lóc",
                                     SEO_CONTEXT)
    assert "error" not in created, created
    return draft["draft_id"]


def test_delete_seo_profile_roundtrip(stores):
    draft_id = _seed_seo_profile(stores)
    preview = srv.delete_seo_profile(draft_id)
    assert preview.get("needs_confirm") is True, preview
    assert loaders.load_seo_profile(draft_id) is not None
    result = srv.delete_seo_profile(draft_id, confirm=True)
    assert "error" not in result, result
    assert loaders.load_seo_profile(draft_id) is None


def test_delete_seo_profile_refused_when_exported(stores):
    draft_id = _seed_seo_profile(stores)
    profile = loaders.load_seo_profile(draft_id)
    profile["state"] = "exported"
    loaders.save_seo_profile(profile)
    result = srv.delete_seo_profile(draft_id, confirm=True)
    assert "error" in result and "exported" in result["error"], result
    assert loaders.load_seo_profile(draft_id) is not None


def test_delete_seo_profile_missing(stores):
    result = srv.delete_seo_profile("d-khongco123", confirm=True)
    assert "error" in result, result


# ---------------------------------------------------------------- delete_backup

def test_delete_backup_roundtrip(stores):
    made = srv.export_core_data()
    assert "error" not in made, made
    name = Path(made["archive"]).name if "archive" in made else \
        made.get("name") or Path(made["path"]).name
    preview = srv.delete_backup(name)
    assert preview.get("needs_confirm") is True, preview
    assert (stores / "backups" / name).exists()
    result = srv.delete_backup(name, confirm=True)
    assert "error" not in result, result
    assert not (stores / "backups" / name).exists()


@pytest.mark.parametrize("bad_name", ["../secret.zip", "sub/dir.zip",
                                      "/abs/path.zip", "not-a-zip.txt",
                                      "..\\win.zip"])
def test_delete_backup_traversal_refused(stores, bad_name):
    result = srv.delete_backup(bad_name, confirm=True)
    assert "error" in result, result


def test_delete_backup_missing(stores):
    result = srv.delete_backup("core-data-19990101-000000.zip", confirm=True)
    assert "error" in result, result


# ------------------------------------------------------- delete_wordpress_target

def test_delete_wordpress_target_roundtrip(stores):
    srv.set_wordpress_target("brand-x", "https://shop.example.com",
                             "admin")
    preview = srv.delete_wordpress_target("brand-x")
    assert preview.get("needs_confirm") is True, preview
    assert "error" not in srv.get_wordpress_target("brand-x")
    result = srv.delete_wordpress_target("brand-x", confirm=True)
    assert "error" not in result, result
    assert result["secrets_touched"] is False
    assert "error" in srv.get_wordpress_target("brand-x")


def test_delete_wordpress_target_missing(stores):
    result = srv.delete_wordpress_target("brand-khong-co", confirm=True)
    assert "error" in result, result


# ------------------------------------------------------------ create_brand_draft

def _brand_doc(brand_id="brand-moi"):
    return {"brand": {"id": brand_id, "name": "Brand Mới",
                      "source_type": "own_site",
                      "review_status": "draft"},
            "voice": {"banned_terms": ["tốt nhất"]},
            "examples": [], "anti_examples": []}


def test_create_brand_draft_stores_document(stores):
    out = srv.create_brand_draft(_brand_doc())
    assert "error" not in out
    loaded = loaders.load_brand_draft("brand-moi")
    assert loaded["brand"]["name"] == "Brand Mới"
    assert loaded["voice"]["banned_terms"] == ["tốt nhất"]


def test_create_brand_draft_refuses_existing(stores):
    srv.create_brand_draft(_brand_doc())
    out = srv.create_brand_draft(_brand_doc())
    assert "error" in out and "already exists" in out["error"]
    assert loaders.load_brand_draft("brand-moi")["brand"]["name"] \
        == "Brand Mới"


def test_create_brand_draft_invalid_shapes_write_nothing(stores):
    # (An id like "Bad ID!" is NOT invalid here: stored_file_stem
    # sanitises injectively by design. The app's manual-creation
    # form imposes the stricter slug rule because the id becomes
    # a URL segment there — an app-layer rule, not a core one.)
    for bad in ("not-a-dict", {}, {"brand": {}}, {"brand": {"id": ""}},
                {"brand": {"id": "x"}, "examples": "not-a-list"}):
        out = srv.create_brand_draft(bad)
        assert "error" in out, bad
    assert loaders.list_brand_drafts() == []


def test_create_brand_draft_competitor_examples_restamped(stores):
    doc = _brand_doc("doi-thu")
    doc["brand"]["source_type"] = "competitor"
    doc["examples"] = [{"text": "copy của đối thủ"}]
    out = srv.create_brand_draft(doc)
    assert "error" not in out
    loaded = loaders.load_brand_draft("doi-thu")
    assert loaded["examples"][0]["reference_only"] is True


# ----------------------------------------------------------------- delete_brand

def _seed_brand_doc(brand_id="brand-x"):
    return loaders.save_brand_draft(
        {"brand": {"id": brand_id, "name": "Brand X",
                   "source_type": "own_site", "review_status": "draft"},
         "voice": {}, "examples": [], "anti_examples": []})


def test_delete_brand_refuses_server_default(stores):
    result = srv.delete_brand("moc-brew", confirm=True)
    assert "error" in result, result


def test_delete_brand_blockers_listed_no_cascade(stores):
    _seed_brand_doc()
    srv.save_facts("brand-x", {"facts": {
        "price_vnd_per_250g": 50000, "pack_weight_g": 250,
        "origins": ["Bến Tre"]}})
    preview = srv.delete_brand("brand-x")
    assert preview.get("needs_confirm") is True, preview
    blockers = preview.get("blockers") or []
    assert any("facts" in b for b in blockers), preview
    result = srv.delete_brand("brand-x", confirm=True)
    assert "error" in result, result
    assert result.get("blockers"), result
    # Nothing cascaded: brand doc AND facts both still there.
    assert loaders.load_brand_draft("brand-x") is not None
    assert loaders.load_facts_profile("brand-x") is not None


def test_delete_brand_clean_roundtrip(stores):
    _seed_brand_doc("brand-sach")
    preview = srv.delete_brand("brand-sach")
    assert preview.get("needs_confirm") is True, preview
    assert preview.get("blockers") == [], preview
    result = srv.delete_brand("brand-sach", confirm=True)
    assert "error" not in result, result
    assert loaders.load_brand_draft("brand-sach") is None


def test_delete_brand_blocked_by_article_draft(stores):
    _seed_brand_doc("brand-co-bai")
    draft = srv.start_draft({"brand_id": "brand-co-bai",
                             "title": "Bài của brand", "format": "blog"})
    assert "error" not in draft, draft
    result = srv.delete_brand("brand-co-bai", confirm=True)
    assert "error" in result, result
    assert any("draft" in b for b in result.get("blockers", [])), result


def test_delete_brand_unknown(stores):
    result = srv.delete_brand("brand-khong-ton-tai", confirm=True)
    assert "error" in result, result
