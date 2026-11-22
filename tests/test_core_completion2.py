"""Fail-first tests for CORE-COMPLETION ROUND 2 (22 Nov 2026):

WS1 — Research pack (specs/research-config-feature.md, the only new
core artefact): pack store + proposal/decision/apply flow with the
spec's hard invariants (US-1..US-6, section 8): nothing unapproved
ever reaches a live store; an item linked to an OPEN contradiction
cannot be decided or applied; apply is idempotent and its report
matches the approved set exactly; style items from a pack are always
saved with store_excerpts=False.

WS2 — System-level corpus registry (Smith's decision, 22 Nov 2026):
corpora registered once; a brand CHOOSES one; assemble_context
defaults the style profile from the brand's choice when the brief
names the brand but no explicit style_profile_id — explicit wins.

WS3 — Taste layer (learning loop, core side): owner verdicts on
articles (approved unchanged / edited, both versions kept), a
per-brand taste profile distilled from >=3 verdicted articles —
the owner's OWN texts, so excerpts may be stored here and only here.

WS4 — Draft list/delete tools (review round 1 leftover Low).

WS5 — Local data backup: export_core_data snapshots every local
store into one timestamped zip with a checksummed manifest; no
credential-pattern file is ever included.
"""
from __future__ import annotations

import json
import sys
import zipfile
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
    ("CONTENT_CORE_PERSONAS_DIR", "personas"),
    ("CONTENT_CORE_RESEARCH_PACKS_DIR", "research_packs"),
    ("CONTENT_CORE_CORPUS_REGISTRY_DIR", "corpus_registry"),
    ("CONTENT_CORE_CORPUS_CHOICES_DIR", "corpus_choices"),
    ("CONTENT_CORE_TASTE_DIR", "taste"),
    ("CONTENT_CORE_BACKUPS_DIR", "backups"),
)


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    for var, sub in STORE_ENVS:
        monkeypatch.setenv(var, str(tmp_path / sub))
    # Personas: seed the shipped cards so default resolution works.
    pdir = tmp_path / "personas"
    pdir.mkdir(parents=True, exist_ok=True)
    for name in ("persona_linh.json", "persona_an.json"):
        (pdir / name).write_text(
            (PROJECT_DIR / "data" / name).read_text(encoding="utf-8"),
            encoding="utf-8")
    return tmp_path


def _words(n: int, token: str = "chữ") -> str:
    return " ".join([token] * n)


ARTICLE_1 = ("Bếp nhà Châu Đốc buổi sớm thơm mùi mắm chưng. " * 6
             + "Nồi đất đặt trên bếp củi, hơi bốc lên nghi ngút. " * 6
             + "EDITEDONLY dấu gu riêng của người viết bài này. " * 4)
ARTICLE_2 = ("Con nước nổi về mang theo cá linh đầy đồng. " * 8
             + "Người xứ này làm mắm để giữ cá lại cho mùa khô. " * 8)
ARTICLE_3 = ("Ghe xuồng chở cá về bến lúc trời còn tối đất. " * 8
             + "Tiếng người gọi nhau í ới trên mặt rạch yên. " * 8)


# ---------------------------------------------------------------------------
# WS1 — research pack
# ---------------------------------------------------------------------------

def _fact(brand, field, value, url, note=""):
    return srv.propose_pack_item(brand, "fact", {"field": field, "value": value},
                                 {"source_url": url, "note": note})


def test_ws1_pack_create_get_counts(stores):
    out = srv.create_research_pack("brand-x")
    assert "error" not in out
    assert out["brand_id"] == "brand-x" and out["version"] == 1
    pack = srv.get_pack("brand-x")
    assert pack["counts"]["items_total"] == 0
    assert pack["open_contradictions"] == []
    assert srv.get_pack("no-such-brand").get("error")


def test_ws1_add_source_quality_rejection_recorded(stores):
    srv.create_research_pack("brand-x")
    bad = srv.add_pack_source("brand-x", "https://spam.example/listicle",
                              "genre", quality_verdict="reject",
                              quality_reason="SEO listicle, no author")
    assert bad["quality"]["verdict"] == "reject"
    pack = srv.get_pack("brand-x")
    assert len(pack["quality_rejections"]) == 1
    assert pack["quality_rejections"][0]["reason"] == "SEO listicle, no author"
    good = srv.add_pack_source("brand-x", "https://brand-x.example/",
                               "own", quality_verdict="accept")
    assert good["quality"]["verdict"] == "accept"
    # A rejected source is not duplicated into quality_rejections twice.
    assert len(srv.get_pack("brand-x")["quality_rejections"]) == 1


def test_ws1_fact_item_requires_source_url(stores):
    srv.create_research_pack("brand-x")
    out = srv.propose_pack_item("brand-x", "fact",
                                {"field": "pack_weight_g", "value": 500},
                                {"note": "no url here"})
    assert "error" in out
    assert srv.get_pack("brand-x")["counts"]["items_total"] == 0


def test_ws1_proposed_items_never_apply(stores):
    """US-5 invariant: apply with everything still proposed changes
    NO live store."""
    srv.create_research_pack("brand-x")
    _fact("brand-x", "price_vnd_per_250g", 100000, "https://x.example/p")
    _fact("brand-x", "pack_weight_g", 500, "https://x.example/p")
    _fact("brand-x", "origin", "Châu Đốc An Giang", "https://x.example/p")
    before = loaders.list_facts_profiles()
    report = srv.apply_pack("brand-x")
    assert report["applied"] == []
    assert loaders.list_facts_profiles() == before
    assert loaders.load_facts_profile("brand-x") is None


def test_ws1_contradiction_blocks_decision_and_apply_then_resolution(stores):
    """US-2, the mambagiaokhoe precedent: listing 100.000đ vs the
    site's own table 90.000đ — one contradiction, both items locked,
    only the resolved value ever lands."""
    srv.create_research_pack("brand-x")
    a = _fact("brand-x", "price_vnd_per_250g", 100000,
              "https://x.example/product", "listing price")
    b = _fact("brand-x", "price_vnd_per_250g", 90000,
              "https://x.example/product#bang-gia", "price table")
    pack = srv.get_pack("brand-x")
    assert len(pack["contradictions"]) == 1
    con = pack["contradictions"][0]
    assert con["status"] == "open"
    assert {a["id"], b["id"]} <= set(con["item_ids"])
    items = {i["id"]: i for i in pack["items"]}
    assert items[a["id"]]["contradiction_id"] == con["id"]
    assert items[b["id"]]["contradiction_id"] == con["id"]
    # Locked: no decision while the contradiction is open.
    assert "error" in srv.decide_pack_item("brand-x", a["id"], "approve")
    # The rest of the fact set can be approved and applied — but with
    # the price unresolved, the merged doc must not be written at all
    # if it cannot validate... seed a valid live doc first instead:
    srv.save_facts("brand-x", {"facts": {
        "price_vnd_per_250g": 50000, "pack_weight_g": 250,
        "origins": ["Nơi Khác"]}})
    w = _fact("brand-x", "known_price_vnd", 110000, "https://x.example/p")
    srv.decide_pack_item("brand-x", w["id"], "approve")
    report = srv.apply_pack("brand-x")
    assert report["applied"] == [w["id"]]
    blocked_ids = {entry["item_id"] for entry in report["blocked"]}
    assert blocked_ids == {a["id"], b["id"]}
    live = loaders.load_facts("brand-x")
    assert live["facts"]["price_vnd_per_250g"] == 50000  # untouched
    assert 110000 in live["facts"]["known_prices_vnd"]
    # Resolve: choose the listing value. Loser is rejected by the
    # resolution itself; winner becomes approved.
    res = srv.resolve_contradiction("brand-x", con["id"], a["id"],
                                    note="listing is primary")
    assert "error" not in res
    pack = srv.get_pack("brand-x")
    items = {i["id"]: i for i in pack["items"]}
    assert items[a["id"]]["status"] == "approved"
    assert items[b["id"]]["status"] == "rejected"
    assert pack["contradictions"][0]["status"] == "resolved"
    report2 = srv.apply_pack("brand-x")
    assert report2["applied"] == [a["id"]]
    live = loaders.load_facts("brand-x")
    assert live["facts"]["price_vnd_per_250g"] == 100000
    assert 90000 not in live["facts"].get("known_prices_vnd", [])


def test_ws1_edited_item_applies_edited_payload_and_keeps_original(stores):
    srv.create_research_pack("brand-x")
    srv.save_facts("brand-x", {"facts": {
        "price_vnd_per_250g": 50000, "pack_weight_g": 250,
        "origins": ["Nơi Khác"]}})
    item = _fact("brand-x", "origin", "Giá Trị Gốc", "https://x.example/p")
    out = srv.decide_pack_item(
        "brand-x", item["id"], "edit",
        edited_payload={"field": "origin", "value": "Giá Trị Đã Sửa"})
    assert out["status"] == "edited"
    assert out["original_payload"]["value"] == "Giá Trị Gốc"
    report = srv.apply_pack("brand-x")
    assert report["applied"] == [item["id"]]
    origins = loaders.load_facts("brand-x")["facts"]["origins"]
    assert "Giá Trị Đã Sửa" in origins and "Giá Trị Gốc" not in origins


def test_ws1_apply_idempotent_and_report_matches_approved_set(stores):
    srv.create_research_pack("brand-x")
    ids = []
    for field, value in (("price_vnd_per_250g", 100000),
                         ("pack_weight_g", 500),
                         ("origin", "Châu Đốc An Giang")):
        item = _fact("brand-x", field, value, "https://x.example/p")
        srv.decide_pack_item("brand-x", item["id"], "approve")
        ids.append(item["id"])
    rejected = _fact("brand-x", "delivery_days", "2-3", "https://x.example/p")
    srv.decide_pack_item("brand-x", rejected["id"], "reject")
    report = srv.apply_pack("brand-x")
    assert sorted(report["applied"]) == sorted(ids)
    report2 = srv.apply_pack("brand-x")
    assert report2["applied"] == []
    assert sorted(report2["already_applied"]) == sorted(ids)
    live = loaders.load_facts("brand-x")
    assert live["facts"]["price_vnd_per_250g"] == 100000
    assert "delivery_days" not in live["facts"]


def test_ws1_reference_brand_item_cannot_apply(stores):
    """US-3: a competitor-sourced brand draft (reference_only
    examples) fails apply exactly the way draft_to_brand_yaml
    refuses it, and no live brand draft is written."""
    srv.create_research_pack("brand-x")
    draft = {
        "brand": {"id": "brand-x", "name": "Brand X", "simulated": True,
                  "version": 0, "review_status": "draft",
                  "source_type": "competitor", "source_urls": []},
        "voice": {"traits": [], "pov": "first-person",
                  "reader_address": "bạn",
                  "sentence_rules": {"median_words_default": [11, 14],
                                     "median_words_by_format": {},
                                     "max_sentence_words": 25},
                  "formatting_rules": [], "banned_terms": [],
                  "prohibited_claims": []},
        "vocabulary": {"use": [], "exact": [], "banned": [], "glossary": []},
        "examples": [{"format": "blog", "text": "Câu văn của đối thủ.",
                      "why_it_works": "", "reference_only": True}],
        "anti_examples": [],
        "claims_and_guardrails": {"requires_evidence": [],
                                  "prohibited_claims": [],
                                  "unsupported_claim_marker": "[NEEDS SOURCE]"},
        "brand_kit": {}, "qualitative": {}, "review_notes": [],
    }
    item = srv.propose_pack_item(
        "brand-x", "brand_rule", draft,
        {"source_url": "https://competitor.example/", "note": "rival site"},
        reference=True)
    srv.decide_pack_item("brand-x", item["id"], "approve")
    report = srv.apply_pack("brand-x")
    assert report["applied"] == []
    assert report["failed"] and "reference" in json.dumps(
        report["failed"], ensure_ascii=False).lower()
    assert loaders.load_brand_draft("brand-x") is None


def test_ws1_unflagged_reference_only_draft_fails_at_conversion(stores):
    """Defence in depth: even when the operator forgot the reference
    flag, a draft whose examples are still reference_only is refused
    by the conversion gate itself — the refusal does not depend on
    the flag being set."""
    srv.create_research_pack("brand-x")
    draft = {
        "brand": {"id": "brand-x", "name": "Brand X", "simulated": True,
                  "version": 0, "review_status": "draft",
                  "source_type": "competitor", "source_urls": []},
        "voice": {"traits": [], "pov": "first-person",
                  "reader_address": "bạn",
                  "sentence_rules": {"median_words_default": [11, 14],
                                     "median_words_by_format": {},
                                     "max_sentence_words": 25},
                  "formatting_rules": [], "banned_terms": [],
                  "prohibited_claims": []},
        "vocabulary": {"use": [], "exact": [], "banned": [], "glossary": []},
        "examples": [{"format": "blog", "text": "Câu văn của đối thủ.",
                      "why_it_works": "", "reference_only": True}],
        "anti_examples": [],
        "claims_and_guardrails": {"requires_evidence": [],
                                  "prohibited_claims": [],
                                  "unsupported_claim_marker": "[NEEDS SOURCE]"},
        "brand_kit": {}, "qualitative": {}, "review_notes": [],
    }
    item = srv.propose_pack_item(
        "brand-x", "brand_rule", draft,
        {"source_url": "https://brand-x.example/", "note": "quên gắn cờ"})
    srv.decide_pack_item("brand-x", item["id"], "approve")
    report = srv.apply_pack("brand-x")
    assert report["applied"] == []
    assert report["failed"] and "reference_only" in report["failed"][0]["error"]
    assert loaders.load_brand_draft("brand-x") is None


def test_ws1_brand_item_applies_via_save_brand_draft(stores):
    srv.create_research_pack("brand-y")
    draft = {
        "brand": {"id": "brand-y", "name": "Brand Y", "simulated": True,
                  "version": 0, "review_status": "draft",
                  "source_type": "own_site", "source_urls": []},
        "voice": {"traits": [], "pov": "first-person",
                  "reader_address": "bạn",
                  "sentence_rules": {"median_words_default": [11, 14],
                                     "median_words_by_format": {},
                                     "max_sentence_words": 25},
                  "formatting_rules": [], "banned_terms": [],
                  "prohibited_claims": []},
        "vocabulary": {"use": [], "exact": [], "banned": [], "glossary": []},
        "examples": [{"format": "blog", "text": "Câu văn của chính brand.",
                      "why_it_works": ""}],
        "anti_examples": [],
        "claims_and_guardrails": {"requires_evidence": [],
                                  "prohibited_claims": [],
                                  "unsupported_claim_marker": "[NEEDS SOURCE]"},
        "brand_kit": {}, "qualitative": {}, "review_notes": [],
    }
    item = srv.propose_pack_item("brand-y", "brand_rule", draft,
                                 {"source_url": "https://brand-y.example/"})
    srv.decide_pack_item("brand-y", item["id"], "approve")
    report = srv.apply_pack("brand-y")
    assert report["applied"] == [item["id"]]
    assert loaders.load_brand_draft("brand-y") is not None


def test_ws1_style_item_forces_store_excerpts_false(stores):
    """US-4 / section 8 hard gate: a style item whose payload ASKS
    for excerpts still saves a profile with store_excerpts=False."""
    srv.create_research_pack("brand-x")
    samples = [ARTICLE_1, ARTICLE_2, ARTICLE_3]
    item = srv.propose_pack_item(
        "brand-x", "style_candidate",
        {"name": "Giọng Thử Nghiệm", "samples": samples,
         "qualitative": None, "store_excerpts": True},
        {"source_url": "https://genre.example/corpus",
         "note": "legitimacy: rights-holder channel"})
    srv.decide_pack_item("brand-x", item["id"], "approve")
    report = srv.apply_pack("brand-x")
    assert report["applied"] == [item["id"]]
    profiles = loaders.list_style_profiles()
    made = [p for p in profiles if p["name"] == "Giọng Thử Nghiệm"]
    assert len(made) == 1
    profile = loaders.load_style_profile(made[0]["id"])
    assert profile["exemplars"] == []
    assert profile["excerpts_note"].startswith("excerpts not stored")


def test_ws1_persona_frame_and_corpus_choice_apply(stores):
    srv.create_research_pack("brand-x")
    reg = srv.register_corpus(
        "corpus-t", version=1, title="Corpus Thử", kind="press",
        profile_id="some-profile", legitimacy_basis="public domain",
        word_count=1000)
    assert "error" not in reg
    persona = srv.propose_pack_item(
        "brand-x", "persona_frame",
        {"data": {"name": "Cô Ba", "description": "Người viết thử.",
                  "personality": "mộc mạc", "scenario": "Viết cho brand-x.",
                  "system_prompt": "Bạn là Cô Ba.",
                  "mes_example": "", "first_mes": "Chào bạn.",
                  "post_history_instructions": ""}},
        {"note": "frame from Smith's brief"})
    choice = srv.propose_pack_item(
        "brand-x", "corpus_choice",
        {"corpus_id": "corpus-t", "version": 1},
        {"note": "Smith chose the press corpus"})
    for item in (persona, choice):
        srv.decide_pack_item("brand-x", item["id"], "approve")
    report = srv.apply_pack("brand-x")
    assert sorted(report["applied"]) == sorted([persona["id"], choice["id"]])
    card = loaders.load_persona_by_id("Cô Ba")
    assert card["data"]["name"] == "Cô Ba"
    got = srv.get_corpus_choice("brand-x")
    assert got["corpus_id"] == "corpus-t" and got["version"] == 1
    assert got["profile_id"] == "some-profile"


def test_ws1_recreate_bumps_version_and_keeps_live_config(stores):
    srv.create_research_pack("brand-x")
    item = _fact("brand-x", "origin", "Châu Đốc An Giang",
                 "https://x.example/p")
    srv.save_facts("brand-x", {"facts": {
        "price_vnd_per_250g": 50000, "pack_weight_g": 250,
        "origins": ["Nơi Khác"]}})
    srv.decide_pack_item("brand-x", item["id"], "approve")
    srv.apply_pack("brand-x")
    out = srv.create_research_pack("brand-x")
    assert out["version"] == 2
    assert srv.get_pack("brand-x")["counts"]["items_total"] == 0
    # Live config from the first pack survives the re-create.
    assert "Châu Đốc An Giang" in loaders.load_facts(
        "brand-x")["facts"]["origins"]


def test_ws1_build_pack_proposals_from_own_pages(stores):
    srv.create_research_pack("brand-x")
    pages = [{"url": "https://brand-x.example/",
              "title": "Brand X",
              "text": ("Brand X bán đặc sản quê nhà cho bạn. "
                       "Mỗi gói được đóng cẩn thận trước khi gửi đi. "
                       "Khách hàng quay lại vì vị quen thuộc của món quê. "
                       "Giá cả ghi rõ ràng trên từng sản phẩm của tiệm."),
              "headings": ["Brand X"], "html_meta": {}}]
    srv.add_pack_source("brand-x", "https://brand-x.example/", "own",
                        pages=pages)
    out = srv.build_pack_proposals("brand-x")
    assert "error" not in out
    kinds = [i["kind"] for i in srv.get_pack("brand-x")["items"]]
    assert "brand_rule" in kinds


# ---------------------------------------------------------------------------
# WS2 — corpus registry + brand choice defaulting
# ---------------------------------------------------------------------------

def test_ws2_registry_register_list_get_and_validation(stores):
    out = srv.register_corpus("c-1", version=2, title="Corpus Một",
                              kind="literature", profile_id="p-1",
                              legitimacy_basis="user-provided",
                              word_count=5000, sample_count=7)
    assert out["id"] == "c-1" and out["version"] == 2
    assert srv.get_corpus("c-1")["profile_id"] == "p-1"
    assert [c["id"] for c in srv.list_corpora()["corpora"]] == ["c-1"]
    assert "error" in srv.get_corpus("missing")
    assert "error" in srv.register_corpus(
        "c-1", version=2, title="Trùng", kind="press", profile_id="p-2",
        legitimacy_basis="public domain", word_count=10)


def test_ws2_shipped_registry_has_the_two_trial_corpora():
    corpora = {c["id"]: c for c in srv.list_corpora()["corpora"]}
    assert corpora["mien-tay-van-hoa"]["profile_id"] == \
        "mien-tay-van-hoa-voice"
    assert corpora["mien-tay-van-hoa"]["word_count"] == 39619
    assert corpora["mien-tay-van-hoc"]["profile_id"] == \
        "mien-tay-van-hoc-voice"
    assert corpora["mien-tay-van-hoc"]["word_count"] == 95507


def test_ws2_corpus_choice_defaults_style_in_assemble_context(stores):
    # A stored style profile the registry entry points at.
    made = srv.save_style_profile("Giọng Mặc Định", [ARTICLE_1, ARTICLE_2,
                                                     ARTICLE_3])
    assert "error" not in made
    srv.register_corpus("corpus-d", version=1, title="Corpus D",
                        kind="press", profile_id=made["id"],
                        legitimacy_basis="public domain", word_count=3000)
    # brand-b style setup: draft + facts so writer-context resolves.
    draft = {
        "brand": {"id": "brand-b", "name": "Brand B", "simulated": True,
                  "version": 0, "review_status": "draft",
                  "source_type": "own_site", "source_urls": []},
        "voice": {"traits": [], "pov": "first-person",
                  "reader_address": "bạn",
                  "sentence_rules": {"median_words_default": [11, 14],
                                     "median_words_by_format": {},
                                     "max_sentence_words": 25},
                  "formatting_rules": [], "banned_terms": [],
                  "prohibited_claims": []},
        "vocabulary": {"use": [], "exact": [], "banned": [], "glossary": []},
        "examples": [], "anti_examples": [],
        "claims_and_guardrails": {"requires_evidence": [],
                                  "prohibited_claims": [],
                                  "unsupported_claim_marker": "[NEEDS SOURCE]"},
        "brand_kit": {}, "qualitative": {}, "review_notes": [],
    }
    loaders.save_brand_draft(draft)
    srv.save_facts("brand-b", {"facts": {
        "price_vnd_per_250g": 50000, "pack_weight_g": 250,
        "origins": ["Bến Tre"]}})
    brief = {"topic": "viết thử", "format": "blog", "brand_id": "brand-b"}
    # No choice yet → no style part.
    plain = srv.assemble_context(brief)
    assert plain["style_profile"] is None
    srv.set_corpus_choice("brand-b", "corpus-d")
    defaulted = srv.assemble_context(brief)
    assert defaulted["style_profile"]["id"] == made["id"]
    assert defaulted["style_profile_source"] == "brand_corpus_choice"
    # Explicit brief choice always wins over the brand default.
    other = srv.save_style_profile("Giọng Khác", [ARTICLE_2, ARTICLE_3,
                                                  ARTICLE_1])
    explicit = srv.assemble_context(brief, style_profile_id=other["id"])
    assert explicit["style_profile"]["id"] == other["id"]
    assert explicit["style_profile_source"] == "brief"


# ---------------------------------------------------------------------------
# WS3 — taste layer
# ---------------------------------------------------------------------------

def _write_articles(tmp_path: Path) -> list[str]:
    files = []
    for i, text in enumerate((ARTICLE_1, ARTICLE_2, ARTICLE_3), 1):
        p = tmp_path / f"article-{i}.md"
        p.write_text(text, encoding="utf-8")
        files.append(str(p))
    return files


def test_ws3_import_seeds_pending_and_build_refuses(stores, tmp_path):
    files = _write_articles(tmp_path)
    out = srv.import_taste_articles("brand-x", files)
    assert out["imported"] == 3
    status = srv.get_taste_status("brand-x")
    assert status["counts"] == {"pending": 3, "approved_unchanged": 0,
                                 "edited": 0}
    assert status["taste_profile"] is None
    # Re-import is idempotent (same refs, no duplicates).
    assert srv.import_taste_articles("brand-x", files)["imported"] == 0
    assert "error" in srv.build_taste_profile("brand-x")


def test_ws3_mark_and_build_taste_profile(stores, tmp_path):
    files = _write_articles(tmp_path)
    srv.import_taste_articles("brand-x", files)
    for f in files:
        out = srv.mark_article_verdict("brand-x", "approved_unchanged",
                                       file_ref=f)
        assert "error" not in out
    built = srv.build_taste_profile("brand-x")
    assert "error" not in built
    assert built["id"] == "brand-x-taste-voice"
    assert built["built_from"] == 3
    profile = loaders.load_style_profile("brand-x-taste-voice")
    assert profile is not None
    # The owner's OWN articles: excerpts MAY be stored here.
    assert profile["exemplars"], "taste profile should carry exemplars"
    status = srv.get_taste_status("brand-x")
    assert status["counts"]["approved_unchanged"] == 3
    assert status["taste_profile"]["profile_id"] == "brand-x-taste-voice"


def test_ws3_edited_entry_keeps_both_versions_and_build_uses_edited(
        stores, tmp_path):
    original = ("ORIGINALONLY bản nháp đầu còn vụng về. " * 10)
    edited = ("EDITEDONLY bản đã sửa tay mượt mà hơn hẳn. " * 10)
    f1 = tmp_path / "file_a.md"
    f1.write_text(original, encoding="utf-8")
    f2 = tmp_path / "file_b.md"
    f2.write_text(ARTICLE_2, encoding="utf-8")
    f3 = tmp_path / "file_c.md"
    f3.write_text(ARTICLE_3, encoding="utf-8")
    out = srv.mark_article_verdict("brand-x", "edited", file_ref=str(f1),
                                   edited_text=edited,
                                   note="Smith sửa tay")
    assert "error" not in out
    srv.mark_article_verdict("brand-x", "approved_unchanged",
                             file_ref=str(f2))
    srv.mark_article_verdict("brand-x", "approved_unchanged",
                             file_ref=str(f3))
    doc = loaders.load_taste_doc("brand-x")
    entry = next(e for e in doc["entries"] if e["verdict"] == "edited")
    assert entry["text"] == original and entry["edited_text"] == edited
    built = srv.build_taste_profile("brand-x")
    assert "error" not in built
    profile = loaders.load_style_profile("brand-x-taste-voice")
    joined = "\n".join(profile["exemplars"])
    assert "EDITEDONLY" in joined and "ORIGINALONLY" not in joined


def test_ws3_real_batch1_seeds_as_pending(stores):
    files = [str(p) for p in sorted(
        (PROJECT_DIR / "trials" / "shop-batch-1").glob("*.md"))
        if p.name not in ("README.md", "run-report.md")]
    assert len(files) == 3
    out = srv.import_taste_articles("mambagiaokhoe", files)
    assert out["imported"] == 3
    status = srv.get_taste_status("mambagiaokhoe")
    assert status["counts"]["pending"] == 3
    assert status["counts"]["approved_unchanged"] == 0


# ---------------------------------------------------------------------------
# WS4 — draft list / delete
# ---------------------------------------------------------------------------

def test_ws4_list_and_delete_drafts(stores):
    d1 = srv.start_draft({"topic": "một", "format": "blog"})
    d2 = srv.start_draft({"topic": "hai", "format": "blog",
                          "brand_id": "brand-b"})
    listed = srv.list_drafts()
    by_id = {d["draft_id"]: d for d in listed["drafts"]}
    assert set(by_id) == {d1["draft_id"], d2["draft_id"]}
    assert by_id[d2["draft_id"]]["brand_id"] == "brand-b"
    assert by_id[d1["draft_id"]]["status"] == "drafting"
    assert by_id[d1["draft_id"]]["updated"]
    out = srv.delete_draft(d1["draft_id"])
    assert out["deleted"] == d1["draft_id"]
    assert "error" in srv.get_draft(d1["draft_id"])
    assert [d["draft_id"] for d in srv.list_drafts()["drafts"]] == \
        [d2["draft_id"]]


def test_ws4_delete_approved_draft_needs_confirm(stores):
    d = srv.start_draft({"topic": "duyệt rồi", "format": "blog"})
    draft = srv.get_draft(d["draft_id"])
    draft["status"] = "approved"
    srv._store_draft(draft)
    assert "error" in srv.delete_draft(d["draft_id"])
    assert srv.get_draft(d["draft_id"])["status"] == "approved"
    out = srv.delete_draft(d["draft_id"], confirm=True)
    assert out["deleted"] == d["draft_id"]


# ---------------------------------------------------------------------------
# WS5 — backup
# ---------------------------------------------------------------------------

def test_ws5_export_manifest_matches_and_excludes_secrets(stores, tmp_path):
    srv.save_style_profile("Giọng Sao Lưu", [ARTICLE_1, ARTICLE_2, ARTICLE_3])
    srv.save_facts("brand-x", {"facts": {
        "price_vnd_per_250g": 50000, "pack_weight_g": 250,
        "origins": ["Bến Tre"]}})
    srv.start_draft({"topic": "sao lưu", "format": "blog"})
    # Plant credential-pattern files inside store dirs: they must be
    # excluded and recorded, never archived.
    drafts = Path(stores / "drafts")
    (drafts / ".env").write_text("TOKEN=x", encoding="utf-8")
    (drafts / "api_token.txt").write_text("x", encoding="utf-8")
    style_dir = Path(stores / "style_profiles")
    (style_dir / "credentials.json").write_text("{}", encoding="utf-8")
    out = srv.export_core_data()
    assert "error" not in out
    archive = Path(stores / "backups") / out["archive"]
    assert archive.exists()
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        manifest = json.loads(zf.read("manifest.json"))
        for name in names:
            low = name.lower()
            assert "credential" not in low and "token" not in low
            assert not Path(name).name.startswith(".")
        assert any(n.startswith("style_profiles/") for n in names)
        assert any(n.startswith("facts_profiles/") for n in names)
        assert any(n.startswith("drafts/") and n.endswith(".json")
                   for n in names)
        # Manifest checksums verify against the archive bytes.
        for entry in manifest["files"]:
            import hashlib
            digest = hashlib.sha256(zf.read(entry["path"])).hexdigest()
            assert digest == entry["sha256"]
        counts = {s: v["files"] for s, v in manifest["stores"].items()}
        assert counts["style_profiles"] >= 1
        assert counts["facts_profiles"] == 1
        excluded_names = {Path(e["path"]).name for e in manifest["excluded"]}
        assert {".env", "api_token.txt", "credentials.json"} <= excluded_names
    listed = srv.list_backups()
    assert [b["name"] for b in listed["backups"]] == [out["archive"]]
    assert listed["backups"][0]["total_files"] == manifest["total_files"]


def test_ws5_list_backups_empty(stores):
    assert srv.list_backups()["backups"] == []
