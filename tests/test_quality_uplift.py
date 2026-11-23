"""Fail-first tests for the QUALITY UPLIFT round (22 Nov 2026):

WS1 — Research → depth: a brand's APPLIED research-pack items feed
assemble_context as a clearly-labelled depth-material section (source
material for the writer — never silently promoted into the fact
base), and the quality result surfaces `depth_context`
(research_pack / brand_facts_only / none) as an additive field. No
score formula changes; a brand without applied items gets a context
byte-identical to before.

WS2 — Best-of-N section variants: score_section_variants ranks 2–5
candidate texts for a section role (headline / opening / cta /
closing) with the gate's own checkers. Hard violations (banned term,
unflagged fact issue) make a variant INELIGIBLE — it ranks last
regardless of fluency, mirroring the gate's rule that hard criteria
stay outside the weighting. Checkers that need full-draft context
(seo, coherence) are reported as not scored, never faked.

WS3 — WordPress edit feedback: sync_wordpress_edits is READ-ONLY
against WordPress (GET only). When Smith edits an exported draft
post on the site, the (original, edited) pair lands in the taste
layer as an "edited" verdict entry keyed on the WP post; re-syncs
are idempotent; a further edit supersedes the entry (the taste
layer's re-mark rule: same ref replaces).
"""
from __future__ import annotations

import base64
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import mcp_server as srv  # noqa: E402
from content_core import loaders, research, taste, wordpress  # noqa: E402

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
    ("CONTENT_CORE_WP_TARGETS_DIR", "wp_targets"),
    ("CONTENT_CORE_WP_SECRETS_FILE", "wordpress_secrets.json"),
)


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    for var, sub in STORE_ENVS:
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.delenv("BRANDFORGE_WP_PASSWORD_MOC_BREW", raising=False)
    pdir = tmp_path / "personas"
    pdir.mkdir(parents=True, exist_ok=True)
    for name in ("persona_linh.json", "persona_an.json"):
        (pdir / name).write_text(
            (PROJECT_DIR / "data" / name).read_text(encoding="utf-8"),
            encoding="utf-8")
    return tmp_path


def _brand_setup(brand_id: str, facts: dict | None = None):
    """A resolvable non-default brand: stored brand draft + facts
    profile (the _brand_b_setup pattern from core-completion 2)."""
    draft = {
        "brand": {"id": brand_id, "name": f"Brand {brand_id}",
                  "simulated": True, "version": 0,
                  "review_status": "draft", "source_type": "own_site",
                  "source_urls": []},
        "voice": {"traits": [], "pov": "first-person",
                  "reader_address": "bạn",
                  "sentence_rules": {"median_words_default": [11, 14],
                                     "median_words_by_format": {},
                                     "max_sentence_words": 25},
                  "formatting_rules": [], "banned_terms": [],
                  "prohibited_claims": []},
        "vocabulary": {"use": [], "exact": [], "banned": [],
                       "glossary": []},
        "examples": [], "anti_examples": [],
        "claims_and_guardrails": {
            "requires_evidence": [], "prohibited_claims": [],
            "unsupported_claim_marker": "[NEEDS SOURCE]"},
        "brand_kit": {}, "qualitative": {}, "review_notes": [],
    }
    loaders.save_brand_draft(draft)
    srv.save_facts(brand_id, {"facts": facts or {
        "price_vnd_per_250g": 100000, "pack_weight_g": 500,
        "origins": ["Châu Đốc An Giang"]}})


def _fact_item(brand, field, value, url, note=""):
    return srv.propose_pack_item(
        brand, "fact", {"field": field, "value": value},
        {"source_url": url, "note": note})


def _draft_for(brand_id: str, text: str) -> str:
    brief = {"format": "blog", "brand_id": brand_id,
             "topic": "chủ đề kiểm tra", "keyword": "kiểm tra",
             "title": "Bài kiểm tra"}
    draft_id = srv.start_draft(brief)["draft_id"]
    srv.save_section(draft_id, "s1", "Mở đầu", text,
                     intent="intro", target_words=120)
    return draft_id


CLEAN_MAM = ("Mắm cá lóc Châu Đốc làm từ cá đồng mùa nước nổi. "
             "Một hũ 500g có giá 100.000đ theo bảng giá của tiệm. "
             "Cá ướp muối hột, ủ trong lu đất đủ ngày mới ra mắm.")


# ---------------------------------------------------------------------------
# WS1 — research → depth material in assemble_context
# ---------------------------------------------------------------------------

def _pack_with_applied(stores, brand="brand-x"):
    _brand_setup(brand)
    srv.create_research_pack(brand)
    srv.add_pack_source(
        brand, "https://brand-x.example/ve-tiem", "factual",
        legitimacy_basis="trang chính thức của tiệm",
        quality_verdict="accept", quality_reason="nguồn sơ cấp",
        pages=[{"url": "https://brand-x.example/ve-tiem",
                "title": "Về tiệm",
                "text": "Tiệm ủ mắm trong lu đất sành suốt 90 ngày. "
                        "Muối hột Châu Đốc được rang khô trước khi ướp cá. "
                        "Mỗi mẻ mắm đều ghi ngày xuống lu trên nắp."}])
    entry = _fact_item(
        brand, "entry",
        {"id": "quy-trinh-u", "keys": ["ủ", "lu đất", "90 ngày"],
         "content": "Mắm ủ trong lu đất 90 ngày, muối hột rang khô "
                    "trước khi ướp."},
        "https://brand-x.example/ve-tiem", "quy trình ủ")
    price = _fact_item(brand, "known_price_vnd", 110000,
                       "https://brand-x.example/bang-gia", "giá cắt khúc")
    for item in (entry, price):
        srv.decide_pack_item(brand, item["id"], "approve")
    report = srv.apply_pack(brand)
    assert report["applied"] == [entry["id"], price["id"]], report
    return entry, price


def test_ws1_applied_items_become_depth_material(stores):
    _pack_with_applied(stores)
    out = srv.assemble_context({"brand_id": "brand-x", "format": "blog",
                                "topic": "mắm ủ lu đất", "keyword": "mắm",
                                "title": "Mắm ủ lu đất"})
    assert "error" not in out, out
    parts = [p["part"] for p in out["ordered_context"]]
    assert "depth_material" in parts
    # After the facts, before the exemplar/task: depth material is
    # writer material, not a rule.
    assert parts.index("facts_triggered") < parts.index("depth_material")
    assert parts.index("depth_material") < parts.index("task")
    text = out["context_text"]
    # The applied fact lands with its provenance, clearly labelled
    # as source material (NOT merged into the facts part).
    assert "Mắm ủ trong lu đất 90 ngày" in text
    assert "https://brand-x.example/ve-tiem" in text
    assert "110000" in text
    assert "SOURCE MATERIAL" in text or "TƯ LIỆU" in text
    # The accepted source's excerpt rides along for local colour.
    assert "Muối hột Châu Đốc được rang khô" in text
    assert out["depth_material"]["applied_items"] == 2
    assert out["depth_material"]["source_excerpts"] == 1


def test_ws1_unapproved_and_blocked_items_never_appear(stores):
    _pack_with_applied(stores)
    brand = "brand-x"
    # A merely-proposed fact with a distinctive marker.
    _fact_item(brand, "claim", "DẤU VẾT CHƯA DUYỆT 777",
               "https://brand-x.example/khac")
    # A contradiction pair (competing prices) — locked, unresolvable
    # here, so neither value may leak into the writer's context.
    _fact_item(brand, "price_vnd_per_250g", 111111,
               "https://brand-x.example/a")
    _fact_item(brand, "price_vnd_per_250g", 222222,
               "https://brand-x.example/b")
    pack = srv.get_pack(brand)
    assert pack["counts"]["by_status"].get("proposed", 0) >= 3
    assert len(pack["open_contradictions"]) == 1
    out = srv.assemble_context({"brand_id": brand, "format": "blog",
                                "topic": "mắm", "keyword": "mắm",
                                "title": "Mắm"})
    text = out["context_text"]
    assert "DẤU VẾT CHƯA DUYỆT 777" not in text
    assert "111111" not in text and "222222" not in text


def test_ws1_brand_without_applied_pack_has_no_depth_part(stores):
    _brand_setup("brand-y")
    out = srv.assemble_context({"brand_id": "brand-y", "format": "blog",
                                "topic": "chủ đề", "keyword": "k",
                                "title": "T"})
    assert "error" not in out, out
    assert "depth_material" not in [p["part"] for p in
                                    out["ordered_context"]]
    assert "depth_material" not in out
    # The server-default brand (no pack at all) is likewise untouched.
    plain = srv.assemble_context({"format": "blog", "topic": "cà phê",
                                  "keyword": "cà phê", "title": "Cà phê"})
    assert "depth_material" not in [p["part"] for p in
                                    plain["ordered_context"]]
    assert "depth_material" not in plain


def test_ws1_depth_context_field_on_quality_results(stores):
    _pack_with_applied(stores)
    with_pack = _draft_for("brand-x", CLEAN_MAM)
    report = srv.quality_score(with_pack,
                               judged_scores={"depth": 2})
    assert report["depth_context"] == "research_pack", report
    combined = srv.check_draft_all(with_pack,
                                   judged_scores={"depth": 2})
    assert combined["quality"]["depth_context"] == "research_pack"

    _brand_setup("brand-y")
    facts_only = _draft_for("brand-y", CLEAN_MAM)
    report2 = srv.quality_score(facts_only, judged_scores={"depth": 1})
    assert report2["depth_context"] == "brand_facts_only", report2

    # The bare helper: no pack + an empty fact base = "none"; an
    # applied pack wins even over an empty fact base.
    assert research.depth_context(
        "brand-z", {"facts": {}, "entries": []}) == "none"
    assert research.depth_context(
        "brand-x", {"facts": {}, "entries": []}) == "research_pack"
    assert research.depth_context(
        "brand-y", {"facts": {}, "entries": []}) == "none"


# ---------------------------------------------------------------------------
# N3 (review round 3) — _depth_excerpts prefix citation must stop at a
# path boundary: a source at https://x/blog must not claim a citation
# of https://x/blogger (same string prefix, different URL).
# ---------------------------------------------------------------------------

def _pack_citing(source_url, cited_url, page_url=None):
    """One accepted source + one applied fact citing `cited_url`."""
    return {
        "brand_id": "brand-n3",
        "sources": [{
            "url": source_url,
            "quality": {"verdict": "accept"},
            "pages": [{"url": page_url or source_url, "title": "Blog",
                       "text": "Nội dung trích từ nguồn blog để lấy "
                               "màu sắc địa phương cho bài viết."}],
        }],
        "items": [{
            "id": "i1", "kind": "fact", "status": "applied",
            "payload": {"field": "claim",
                        "value": "một khẳng định đã duyệt"},
            "provenance": {"source_url": cited_url},
        }],
    }


def test_n3_sibling_prefix_url_is_not_a_citation():
    # The review's repro: source https://x/blog, cited
    # https://x/blogger — pre-fix the source wrongly contributes
    # its excerpt; post-fix it contributes nothing.
    pack = _pack_citing("https://x.example/blog",
                        "https://x.example/blogger")
    out = research.depth_material(pack)
    assert out is not None
    assert out["source_excerpts"] == 0, out
    assert "Nội dung trích từ nguồn blog" not in out["text"]


@pytest.mark.parametrize("cited_url", [
    "https://x.example/blog",            # the source's own URL
    "https://x.example/blog/bai-viet",   # a page beneath the source
    "https://x.example/blog/",           # trailing slash on the cited URL
])
def test_n3_own_and_subpath_urls_are_citations(cited_url):
    pack = _pack_citing("https://x.example/blog", cited_url)
    out = research.depth_material(pack)
    assert out is not None
    assert out["source_excerpts"] == 1, out
    assert "Nội dung trích từ nguồn blog" in out["text"]


def test_n3_trailing_slash_source_still_matches_subpath():
    pack = _pack_citing("https://x.example/blog/",
                        "https://x.example/blog/bai-viet")
    out = research.depth_material(pack)
    assert out is not None
    assert out["source_excerpts"] == 1, out


def test_n3_query_string_after_prefix_is_not_a_citation():
    # The boundary rule is "/" or end-of-string; "?" is neither, so
    # a tracked link to the source's own path does not count as a
    # citation of the source either — the safe direction.
    pack = _pack_citing("https://x.example/blog",
                        "https://x.example/blog?ref=nha")
    out = research.depth_material(pack)
    assert out is not None
    assert out["source_excerpts"] == 0, out


# ---------------------------------------------------------------------------
# WS2 — best-of-N section variants
# ---------------------------------------------------------------------------

GOOD_OPENING = (
    "Mộc Brew rang cà phê vào thứ Ba hàng tuần cho bạn. "
    "Một gói 250g có giá 185.000đ trước khi nói tới hương vị. "
    "Hạt Arabica Cầu Đất cho vị chua nhẹ và hậu ngọt rõ. "
    "Mình ghi rõ ngày rang trên từng gói gửi tới tay bạn.")
WEAK_OPENING = (
    "Có thể nói rằng đây là một sản phẩm cà phê rất là tuyệt vời và "
    "hoàn hảo dành cho buổi sáng của bạn nói chung là đáng chú ý. "
    "Nó thực sự là một trải nghiệm đáng chú ý cho mọi người.")
BANNED_OPENING = (
    "Mộc Brew mang đến trải nghiệm cà phê đẳng cấp thế giới cho mọi "
    "khách hàng yêu cà phê. Sản phẩm được curated kỹ lưỡng để "
    "elevate buổi sáng và leverage năng lượng của bạn.")
WRONG_PRICE_OPENING = (
    "Một gói 250g của Mộc Brew có giá 999.000đ cho bạn hôm nay. "
    "Mình ghi rõ ngày rang trên từng gói gửi tới tay bạn mỗi tuần.")


def _mocbrew_draft(stores, style=False):
    brief = {"format": "blog", "topic": "cà phê văn phòng",
             "keyword": "cà phê", "title": "Cà phê Mộc Brew"}
    if style:
        made = srv.save_style_profile(
            "Giọng Kiểm Tra", [GOOD_OPENING, WEAK_OPENING,
                               BANNED_OPENING])
        assert "error" not in made, made
        brief["style_profile_id"] = made["id"]
    draft_id = srv.start_draft(brief)["draft_id"]
    srv.save_section(draft_id, "s1", "Mở đầu", GOOD_OPENING,
                     intent="intro", target_words=120)
    return draft_id


def test_ws2_ranking_and_hard_violation_ineligibility(stores):
    draft_id = _mocbrew_draft(stores)
    out = srv.score_section_variants(
        "opening",
        [WEAK_OPENING, GOOD_OPENING, BANNED_OPENING,
         WRONG_PRICE_OPENING],
        draft_id=draft_id)
    assert "error" not in out, out
    ranked = out["ranked"]
    assert len(ranked) == 4
    # The clean, fact-correct variant wins; the weak one is second.
    assert ranked[0]["index"] == 1 and ranked[0]["eligible"] is True
    assert ranked[1]["index"] == 0 and ranked[1]["eligible"] is True
    # Hard violations rank last regardless of fluency, and say why.
    tail = ranked[2:]
    assert {r["index"] for r in tail} == {2, 3}
    assert all(r["eligible"] is False for r in tail)
    banned = next(r for r in tail if r["index"] == 2)
    assert any(v["type"] == "banned_term" for v in
               banned["hard_violations"])
    wrong = next(r for r in tail if r["index"] == 3)
    assert any(i["type"] == "wrong_price" for i in
               wrong["hard_violations"])
    assert out["best_index"] == 1
    # Full-draft-context checkers are honestly reported, not faked.
    not_scored = {n["axis"] for n in out["not_scored"]}
    assert {"seo", "coherence"} <= not_scored


def test_ws2_component_scores_present_and_style_optional(stores):
    draft_id = _mocbrew_draft(stores, style=True)
    out = srv.score_section_variants(
        "opening", [GOOD_OPENING, WEAK_OPENING], draft_id=draft_id)
    assert "error" not in out, out
    for entry in out["ranked"]:
        scores = entry["scores"]
        assert scores["brand"] is not None
        assert scores["craft"] is not None
        assert scores["style"] is not None  # brief carries a profile
        assert scores["composite"] is not None
    assert out["style_profile_id"]


def test_ws2_headline_too_short_for_craft_is_not_scored(stores):
    out = srv.score_section_variants(
        "headline",
        ["Cà phê rang mới mỗi tuần",
         "Cà phê Mộc Brew cho văn phòng"],
        brand_id="moc-brew")
    assert "error" not in out, out
    for entry in out["ranked"]:
        assert entry["scores"]["craft"] is None
        assert any("craft" in n for n in entry["notes"])
        assert entry["scores"]["composite"] is not None


def test_ws2_input_validation(stores):
    draft_id = _mocbrew_draft(stores)
    one = srv.score_section_variants("opening", [GOOD_OPENING],
                                     draft_id=draft_id)
    assert "error" in one
    six = srv.score_section_variants(
        "opening", [GOOD_OPENING] * 6, draft_id=draft_id)
    assert "error" in six
    role = srv.score_section_variants(
        "middle", [GOOD_OPENING, WEAK_OPENING], draft_id=draft_id)
    assert "error" in role
    missing = srv.score_section_variants(
        "opening", [GOOD_OPENING, WEAK_OPENING],
        draft_id="d-doesnotexist")
    assert "error" in missing
    neither = srv.score_section_variants(
        "opening", [GOOD_OPENING, WEAK_OPENING])
    assert "error" in neither
    empty = srv.score_section_variants(
        "opening", [GOOD_OPENING, "  "], draft_id=draft_id)
    assert "error" in empty


def test_ws2_stale_brief_style_profile_is_an_error(stores):
    """Round-3 S3: a draft whose brief names a style profile that no
    longer resolves must NOT score variants as if no profile were
    intended — the stale id is an error, mirroring assemble_context's
    style_profile_error. (Fail-first: before the fix the call returns
    a ranking with style_profile_id None.)"""
    brief = {"format": "blog", "topic": "cà phê văn phòng",
             "keyword": "cà phê", "title": "Cà phê Mộc Brew",
             "style_profile_id": "sp-doesnotexist"}
    draft_id = srv.start_draft(brief)["draft_id"]
    out = srv.score_section_variants(
        "opening", [GOOD_OPENING, WEAK_OPENING], draft_id=draft_id)
    assert "error" in out, out
    assert "sp-doesnotexist" in out["error"]
    assert "ranked" not in out


def test_ws2_stale_explicit_style_profile_is_an_error(stores):
    """Same stale-id rule for the explicit argument — already an error
    before this round; pinned so the fix cannot regress it."""
    draft_id = _mocbrew_draft(stores)
    out = srv.score_section_variants(
        "opening", [GOOD_OPENING, WEAK_OPENING], draft_id=draft_id,
        style_profile_id="sp-doesnotexist")
    assert "error" in out, out
    assert "sp-doesnotexist" in out["error"]


def test_ws2_stale_corpus_choice_default_stays_silent(stores):
    """A stale corpus-choice DEFAULT is not an error: like
    assemble_context, only a profile the brief (or the caller)
    explicitly named is reported — a default that fails to resolve
    just means no style part."""
    loaders.save_corpus_choice("moc-brew", {
        "corpus_id": "c-doesnotexist", "version": 1,
        "profile_id": "sp-doesnotexist", "source": "direct"})
    out = srv.score_section_variants(
        "opening", [GOOD_OPENING, WEAK_OPENING], brand_id="moc-brew")
    assert "error" not in out, out
    assert out["style_profile_id"] is None


# ---------------------------------------------------------------------------
# Approved item A (Smith 22/11/2026): the S3 stale-profile rule applied
# to quality_score and check_draft_all
# ---------------------------------------------------------------------------

def _stale_profile_draft_id():
    """A draft whose brief names a style profile that no longer
    resolves — the same stale-id shape as the S3 test above."""
    brief = {"format": "blog", "topic": "cà phê văn phòng",
             "keyword": "cà phê", "title": "Cà phê Mộc Brew",
             "style_profile_id": "sp-doesnotexist"}
    draft_id = srv.start_draft(brief)["draft_id"]
    srv.save_section(draft_id, "s1", "Mở đầu", GOOD_OPENING,
                     intent="intro", target_words=120)
    return draft_id


def test_qs_stale_brief_style_profile_is_an_error(stores):
    """Approved item A: quality_score must NOT score a draft whose
    brief names a style profile that no longer resolves as if no
    profile were intended — the stale id is a structured error,
    mirroring score_section_variants after S3. (Fail-first: before
    the fix the call returns a full quality report with the voice
    axis scored without any style profile.)"""
    draft_id = _stale_profile_draft_id()
    out = srv.quality_score(draft_id)
    assert "error" in out, out
    assert "sp-doesnotexist" in out["error"]
    assert "weighted_total" not in out


def test_check_all_stale_brief_style_profile_is_an_error(stores):
    """Approved item A, same rule for check_draft_all: a stale brief
    style profile is an error naming the id — no combined report
    with style None and a style-less quality section. (Fail-first:
    before the fix the call returns the full combined report.)"""
    draft_id = _stale_profile_draft_id()
    out = srv.check_draft_all(draft_id)
    assert "error" in out, out
    assert "sp-doesnotexist" in out["error"]
    assert "brand" not in out and "quality" not in out


def test_qs_corrupt_stored_style_profile_is_a_structured_error(stores):
    """Pin (approved item A, corrupt-profile clause): a stored profile
    file that fails to load is a structured error, not a crash —
    already the behaviour via the ValueError guard around the
    resolve; pinned so the stale-id change cannot regress it."""
    (Path(stores) / "style_profiles").mkdir(parents=True, exist_ok=True)
    (Path(stores) / "style_profiles" / "sp-broken.json").write_text(
        "{not json", encoding="utf-8")
    brief = {"format": "blog", "topic": "cà phê văn phòng",
             "keyword": "cà phê", "title": "Cà phê Mộc Brew",
             "style_profile_id": "sp-broken"}
    draft_id = srv.start_draft(brief)["draft_id"]
    srv.save_section(draft_id, "s1", "Mở đầu", GOOD_OPENING,
                     intent="intro", target_words=120)
    out = srv.quality_score(draft_id)
    assert "error" in out, out
    assert "sp-broken" in out["error"]
    assert "weighted_total" not in out


def test_qs_and_check_all_valid_brief_style_profile_still_scores(stores):
    """Pin: a brief naming a profile that DOES resolve still scores
    with it — quality_score's voice axis carries the profile id and
    check_draft_all returns a style report for it. The fix must not
    turn every profiled draft into an error."""
    draft_id = _mocbrew_draft(stores, style=True)
    qs = srv.quality_score(draft_id)
    assert "error" not in qs, qs
    voice = next(a for a in qs["axes"] if a["axis"] == "voice")
    assert voice["detail"].get("style_profile_id")
    combined = srv.check_draft_all(draft_id)
    assert "error" not in combined, combined
    assert combined["style"] is not None
    assert combined["style"]["profile_id"] == \
        voice["detail"]["style_profile_id"]
    assert combined["quality"]["weighted_total"] == qs["weighted_total"]


# ---------------------------------------------------------------------------
# Fix round (Smith review, 23 Nov 2026) F4: final_gate S3 parity —
# the stale/corrupt brief style-profile rule quality_score and
# check_draft_all already follow (approved item A) applies to
# final_gate too (Smith approved via the review report).
# ---------------------------------------------------------------------------

def test_fg_stale_brief_style_profile_is_an_error(stores):
    """F4: final_gate must NOT gate a draft whose brief names a
    style profile that no longer resolves as if no profile were
    intended — the stale id is a structured error naming it, and NO
    gate_history entry is recorded (an error is not a verdict).
    (Fail-first: before the fix the call returns a full verdict
    with no style report.)"""
    draft_id = _stale_profile_draft_id()
    out = srv.final_gate(draft_id)
    assert "error" in out, out
    assert "sp-doesnotexist" in out["error"]
    assert "passed" not in out
    draft = srv.get_draft(draft_id)
    assert draft["gate_history"] == []
    assert draft["status"] == "drafting"


def test_fg_corrupt_stored_style_profile_is_a_structured_error(stores):
    """F4 pin: a stored profile file that fails to load makes
    final_gate return a structured error, not a crash."""
    (Path(stores) / "style_profiles").mkdir(parents=True, exist_ok=True)
    (Path(stores) / "style_profiles" / "sp-broken.json").write_text(
        "{not json", encoding="utf-8")
    brief = {"format": "blog", "topic": "cà phê văn phòng",
             "keyword": "cà phê", "title": "Cà phê Mộc Brew",
             "style_profile_id": "sp-broken"}
    draft_id = srv.start_draft(brief)["draft_id"]
    srv.save_section(draft_id, "s1", "Mở đầu", GOOD_OPENING,
                     intent="intro", target_words=120)
    out = srv.final_gate(draft_id)
    assert "error" in out, out
    assert "passed" not in out
    assert srv.get_draft(draft_id)["gate_history"] == []


def test_fg_stale_corpus_default_in_brief_stays_silent(stores):
    """F4: a brief whose style_profile_id came from the brand's
    corpus choice (style_profile_source == "brand_corpus_choice")
    treats a stale id the way assemble_context treats a stale
    default: silently no style part — the gate still runs."""
    brief = {"format": "blog", "topic": "cà phê văn phòng",
             "keyword": "cà phê", "title": "Cà phê Mộc Brew",
             "style_profile_id": "sp-doesnotexist",
             "style_profile_source": "brand_corpus_choice"}
    draft_id = srv.start_draft(brief)["draft_id"]
    srv.save_section(draft_id, "s1", "Mở đầu", GOOD_OPENING,
                     intent="intro", target_words=120)
    out = srv.final_gate(draft_id)
    assert "error" not in out, out
    assert "passed" in out
    assert "style_match" not in out


def test_fg_valid_brief_style_profile_still_gates(stores):
    """F4 pin: a brief naming a profile that DOES resolve still
    gates with the advisory style report recorded — the fix must
    not turn every profiled draft into an error."""
    draft_id = _mocbrew_draft(stores, style=True)
    out = srv.final_gate(draft_id)
    assert "error" not in out, out
    assert "style_match" in out
    assert out["gate_history"][-1]["style_profile_id"]


# ---------------------------------------------------------------------------
# WS3 — WordPress edit feedback sync
# ---------------------------------------------------------------------------

WP_BRAND = "moc-brew"
WP_USER = "editor1"
WP_PASSWORD = "abcd efgh ijkl mnop"
WP_PASSWORD_STRIPPED = "abcdefghijklmnop"
WP_SECTIONS = [
    {"id": "s1", "heading": "Mở đầu", "intent": "intro",
     "text": "Mộc Brew rang cà phê vào thứ Ba hàng tuần cho bạn. "
             "Một gói 250g có giá 185.000đ."},
    {"id": "s2", "heading": "Hương vị", "intent": "body",
     "text": "Hạt Arabica Cầu Đất cho vị chua nhẹ và hậu ngọt rõ. "
             "Mình ghi rõ ngày rang trên từng gói."},
]


class _MockWPGet:
    """The exporter's mock (test_wordpress.py) plus the read side:
    POST creates a stored post; GET /posts/<id> serves it back; the
    test edits the stored content the way Smith edits in wp-admin."""

    def __init__(self):
        self.requests: list[dict] = []
        self.posts: dict[int, dict] = {}
        self.next_post_id = 4242
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _record(self, body: bytes):
                outer.requests.append({
                    "method": self.command, "path": self.path,
                    "headers": dict(self.headers), "body": body})

            def _send_json(self, code: int, obj: dict):
                raw = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _auth_ok(self) -> bool:
                auth = self.headers.get("Authorization", "")
                if not auth.startswith("Basic "):
                    return False
                decoded = base64.b64decode(
                    auth[len("Basic "):]).decode("utf-8")
                return decoded == f"{WP_USER}:{WP_PASSWORD_STRIPPED}"

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                self._record(body)
                if not self._auth_ok():
                    self._send_json(401, {"code": "rest_not_logged_in",
                                          "message": "bad credentials"})
                    return
                if self.path == "/wp-json/wp/v2/posts":
                    payload = json.loads(body or b"{}")
                    post_id = outer.next_post_id
                    outer.next_post_id += 1
                    outer.posts[post_id] = {
                        "id": post_id, "status": payload.get("status"),
                        "title": {"rendered": payload.get("title", "")},
                        "content": {
                            "rendered": payload.get("content", "")},
                        "link": f"http://wp.test/?p={post_id}"}
                    self._send_json(201, {
                        "id": post_id, "status": payload.get("status"),
                        "link": f"http://wp.test/?p={post_id}"})
                    return
                self._send_json(404, {"code": "rest_no_route",
                                      "message": "unknown path"})

            def do_GET(self):
                self._record(b"")
                if not self._auth_ok():
                    self._send_json(401, {"code": "rest_not_logged_in",
                                          "message": "bad credentials"})
                    return
                prefix = "/wp-json/wp/v2/posts/"
                if self.path.startswith(prefix):
                    try:
                        post_id = int(self.path[len(prefix):])
                    except ValueError:
                        post_id = -1
                    post = outer.posts.get(post_id)
                    if post is None:
                        self._send_json(404, {
                            "code": "rest_post_invalid_id",
                            "message": "Invalid post ID."})
                        return
                    self._send_json(200, post)
                    return
                self._send_json(404, {"code": "rest_no_route",
                                      "message": "unknown path"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def edit_post(self, post_id: int, content_html: str):
        """Simulate Smith editing the draft in wp-admin."""
        self.posts[post_id]["content"]["rendered"] = content_html

    def methods_since(self, mark: int) -> list[str]:
        return [r["method"] for r in self.requests[mark:]]


@pytest.fixture()
def mock_wp_get():
    mock = _MockWPGet()
    yield mock
    mock.stop()


def _exported_draft(stores, mock) -> str:
    brief = {"id": "wp-sync-test", "format": "blog",
             "topic": "cà phê Mộc Brew cho văn phòng",
             "keyword": "cà phê", "title": "Bài kiểm tra đồng bộ"}
    draft_id = srv.start_draft(brief)["draft_id"]
    for sec in WP_SECTIONS:
        srv.save_section(draft_id, sec["id"], sec["heading"],
                         sec["text"], intent=sec["intent"],
                         target_words=120)
    # Fix round F1: the exporter only ships a draft carrying a
    # current final_gate pass — gate first (this content passes).
    gate = srv.final_gate(draft_id)
    assert gate["passed"], gate
    srv.set_wordpress_target(WP_BRAND, mock.base_url, WP_USER)
    (stores / "wordpress_secrets.json").write_text(
        json.dumps({WP_BRAND: WP_PASSWORD}), encoding="utf-8")
    out = srv.export_draft_to_wordpress(WP_BRAND, draft_id)
    assert "error" not in out, out
    assert out["wp_post_id"] == 4242
    return draft_id


def _taste_entries(brand=WP_BRAND):
    doc = loaders.load_taste_doc(brand)
    return [] if doc is None else doc["entries"]


def test_ws3_unchanged_post_syncs_nothing(stores, mock_wp_get):
    _exported_draft(stores, mock_wp_get)
    mark = len(mock_wp_get.requests)
    out = srv.sync_wordpress_edits(brand_id=WP_BRAND)
    assert "error" not in out, out
    assert len(out["results"]) == 1
    result = out["results"][0]
    assert result["wp_post_id"] == 4242
    assert result["changed"] is False
    assert _taste_entries() == []
    # READ-ONLY against WordPress: the sync issued GETs and only GETs.
    assert mock_wp_get.methods_since(mark) == ["GET"]


def test_ws3_edited_post_lands_in_taste_layer(stores, mock_wp_get):
    draft_id = _exported_draft(stores, mock_wp_get)
    edited_html = (
        "<h2>Mở đầu</h2>\n"
        "<p>Mộc Brew rang cà phê vào sáng thứ Ba hàng tuần cho bạn. "
        "Một gói 250g có giá 185.000đ. Gói được đóng ngay trong ngày "
        "rang để giữ mùi thơm.</p>\n"
        "<h2>Hương vị</h2>\n"
        "<p>Hạt Arabica Cầu Đất cho vị chua nhẹ và hậu ngọt rõ. "
        "Mình ghi rõ ngày rang trên từng gói.</p>")
    mock_wp_get.edit_post(4242, edited_html)
    mark = len(mock_wp_get.requests)
    out = srv.sync_wordpress_edits(draft_id=draft_id)
    assert "error" not in out, out
    result = out["results"][0]
    assert result["changed"] is True
    assert result["diff"]["words_added"] > 0
    assert mock_wp_get.methods_since(mark) == ["GET"]

    entries = _taste_entries()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["verdict"] == "edited"
    assert entry["source"] == "wordpress"
    # Both halves of the diff are kept: what the system wrote, and
    # what Smith changed it to.
    assert "vào thứ Ba hàng tuần" in entry["text"]
    assert "đóng ngay trong ngày rang" in entry["edited_text"]
    assert entry["wp_sync"]["wp_post_id"] == 4242
    assert entry["wp_sync"]["site_url"] == mock_wp_get.base_url
    assert entry["wp_sync"]["content_hash"]

    # Re-sync without further edits: idempotent, still one entry.
    again = srv.sync_wordpress_edits(draft_id=draft_id)
    assert again["results"][0]["changed"] is False
    assert len(_taste_entries()) == 1


def test_ws3_second_edit_supersedes_the_entry(stores, mock_wp_get):
    draft_id = _exported_draft(stores, mock_wp_get)
    first = ("<h2>Mở đầu</h2>\n"
             "<p>Mộc Brew rang cà phê vào sáng thứ Ba hàng tuần cho "
             "bạn. Một gói 250g có giá 185.000đ.</p>\n"
             "<h2>Hương vị</h2>\n"
             "<p>Hạt Arabica Cầu Đất cho vị chua nhẹ và hậu ngọt rõ. "
             "Mình ghi rõ ngày rang trên từng gói.</p>")
    mock_wp_get.edit_post(4242, first)
    srv.sync_wordpress_edits(draft_id=draft_id)
    second = ("<h2>Mở đầu</h2>\n"
              "<p>Mộc Brew rang cà phê vào sáng thứ Ba hàng tuần cho "
              "bạn. Một gói 250g có giá 185.000đ.</p>\n"
              "<h2>Hương vị</h2>\n"
              "<p>Hạt Arabica Cầu Đất cho vị chua dịu và hậu ngọt dài. "
              "Mình ghi rõ ngày rang trên từng gói.</p>")
    mock_wp_get.edit_post(4242, second)
    out = srv.sync_wordpress_edits(draft_id=draft_id)
    assert out["results"][0]["changed"] is True
    entries = _taste_entries()
    # The taste layer's re-mark rule: same ref replaces — the entry
    # now carries the NEWEST edited text, the original stays the
    # exported draft.
    assert len(entries) == 1
    assert "hậu ngọt dài" in entries[0]["edited_text"]
    assert "hậu ngọt rõ" in entries[0]["text"]


def test_ws3_sync_skips_unexported_and_errors_cleanly(
        stores, mock_wp_get):
    draft_id = _exported_draft(stores, mock_wp_get)
    brief = {"format": "blog", "topic": "chưa xuất", "keyword": "k",
             "title": "Chưa xuất"}
    other = srv.start_draft(brief)["draft_id"]
    srv.save_section(other, "s1", "Mở đầu", GOOD_OPENING,
                     intent="intro", target_words=60)
    # Brand sync covers only the exported draft.
    out = srv.sync_wordpress_edits(brand_id=WP_BRAND)
    assert [r["draft_id"] for r in out["results"]] == [draft_id]
    # Draft-scoped sync on an unexported draft is a clean error.
    single = srv.sync_wordpress_edits(draft_id=other)
    assert "error" in single
    # Neither argument is a clean error too.
    assert "error" in srv.sync_wordpress_edits()


def test_ws3_malformed_export_record_is_a_per_draft_error(
        stores, mock_wp_get):
    """S1 (review round 3): a stored draft whose wordpress_export
    wp_post_id is not an integer ("not-a-number" — a corrupt or
    hand-edited record) must NOT sink the brand-wide sync. The
    per-draft contract: that draft gets a structured error and the
    remaining drafts still sync."""
    good_id = _exported_draft(stores, mock_wp_get)
    bad = {
        "draft_id": "d-malformed-export",
        "brief": {"brand_id": WP_BRAND, "format": "blog",
                  "topic": "bản ghi xuất lỗi", "keyword": "k",
                  "title": "Bản ghi lỗi"},
        "sections": [],
        "wordpress_export": {
            "brand_id": WP_BRAND, "wp_post_id": "not-a-number",
            "site_url": mock_wp_get.base_url,
            "post_link": f"{mock_wp_get.base_url}/?p=bad"},
    }
    (stores / "drafts" / "d-malformed-export.json").write_text(
        json.dumps(bad, ensure_ascii=False), encoding="utf-8")

    out = srv.sync_wordpress_edits(brand_id=WP_BRAND)
    assert "error" not in out, out
    by_id = {r["draft_id"]: r for r in out["results"]}
    assert set(by_id) == {good_id, "d-malformed-export"}
    # The good draft synced normally…
    assert by_id[good_id]["changed"] is False
    # …and the malformed one is a structured per-draft error that
    # names the bad value — no exception escaped the sync.
    bad_result = by_id["d-malformed-export"]
    assert "error" in bad_result
    assert "not-a-number" in bad_result["error"]


def test_ws3_html_blocks_roundtrip_matches_draft_blocks():
    sections = [dict(s) for s in WP_SECTIONS]
    html = wordpress.build_content_html(sections, {})
    blocks = wordpress.html_to_blocks(html)
    assert blocks == wordpress.sections_to_blocks(sections)
    diff = wordpress.diff_blocks(blocks, blocks)
    assert diff["blocks_changed"] == 0
    assert diff["words_added"] == 0 and diff["words_removed"] == 0
