"""Fail-first tests for SEO five-source Round 2 (specs/
seo-five-source-feature.md, plan Round 2): direction + user
decision, the sufficiency-gated versioned outline linked to the
draft, and predictable invalidation (A6).

Criteria covered (spec section 7):
- A5: a mixed/unclear intent is put to the USER — the direction
  must name ONE primary intent (the user's choice), the decision
  (approved / rejected, with timestamp) is recorded, and the
  approved outline is a readable versioned record.
- A6: after direction/outline approval, changing sources /
  analysis is refused in the later states; the ONLY way back is
  reopen_seo_analysis, which invalidates the dependent approvals
  and keeps them in history — nothing is silently mutated.
- Outline gate: proposing an outline requires
  sufficiency.proceed_ready (5 counted reads, or a recorded
  shortfall exception). The partial-counts rule runs through the
  PARTIAL_READS_COUNT_TOWARD_REQUIRED policy constant (Smith's
  open call — flipping it is one line).
- A7 re-check: the full Round 2 flow still writes NOTHING into
  facts / brand / corpus / taste stores; the only draft change
  is the outline field (the sanctioned link to make_outline).

All stores point at tmp dirs — no live store is touched.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import mcp_server as srv  # noqa: E402
from content_core import loaders, seo_profile  # noqa: E402
from content_core.gates import content_sha  # noqa: E402

STORE_ENVS = (
    ("CONTENT_CORE_DRAFTS_DIR", "drafts"),
    ("CONTENT_CORE_SEO_PROFILES_DIR", "seo_profiles"),
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
    ("CONTENT_CORE_WP_TARGETS_DIR", "wordpress_targets"),
    ("CONTENT_CORE_BRAND_SETUPS_DIR", "brand_setups"),
    ("CONTENT_CORE_BACKUPS_DIR", "backups"),
)

CONTEXT = {"market": "VN", "language": "vi", "location": "Hồ Chí Minh",
           "device": "mobile", "searched_at": "2026-11-23T08:30:00+07:00"}

SECTIONS = [{"heading": "Mở đầu: chọn cà phê cho văn phòng",
             "intent": "hook", "target_words": 150},
            {"heading": "Giá và các gói", "intent": "facts"}]


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


def _to_analyzing(stores, reads: int = 5, partial_last: bool = False):
    """Drive a fresh profile to `analyzing`: 5 sources, `reads` of
    them read, analysis saved. Returns (draft_id, sources,
    claim_ids)."""
    brief = {"id": "seo-r2", "format": "blog", "topic": "cà phê",
             "keyword": "cà phê nguyên chất", "brand_id": "brand-x"}
    draft_id = srv.start_draft(brief)["draft_id"]
    out = srv.create_seo_profile(draft_id, "cà phê nguyên chất",
                                 dict(CONTEXT))
    assert "error" not in out, out
    sources = []
    for rank in range(1, 6):
        src = srv.add_seo_source(
            draft_id, f"https://example-{rank}.vn/bai-{rank}",
            title=f"Bài {rank}", observed_rank=rank)
        assert "error" not in src, src
        sources.append(src)
    for src in sources[:reads]:
        status = "read"
        if partial_last and src is sources[reads - 1]:
            status = "partial"
        out = srv.record_source_read(draft_id, src["source_id"], status)
        assert "error" not in out, out
    out = srv.save_seo_analysis(draft_id, [
        {"text": "Bài 1 mở bằng giá", "kind": "sourced",
         "source_ids": [sources[0]["source_id"]]},
        {"text": "Người đọc có vẻ muốn so giá", "kind": "inference",
         "source_ids": []}])
    assert "error" not in out, out
    claim_ids = [c["id"] for c in out["claims"]]
    return draft_id, sources, claim_ids


def _to_outline_review(stores, **kw):
    draft_id, sources, claim_ids = _to_analyzing(stores, **kw)
    out = srv.save_seo_direction(draft_id, "mua hàng",
                                 rationale_claim_ids=claim_ids[:1])
    assert "error" not in out, out
    out = srv.record_direction_decision(draft_id, "approved")
    assert "error" not in out, out
    return draft_id, sources, claim_ids


def _to_writing(stores, **kw):
    draft_id, sources, claim_ids = _to_outline_review(stores, **kw)
    out = srv.propose_seo_outline(draft_id, list(SECTIONS))
    assert "error" not in out, out
    out = srv.approve_seo_outline(draft_id, out["version"])
    assert "error" not in out, out
    return draft_id, sources, claim_ids


# ---------------------------------------------------------------------------
# A5 — direction: one primary intent, the USER decides
# ---------------------------------------------------------------------------

def test_a5_direction_requires_primary_intent(stores):
    draft_id, _, _ = _to_analyzing(stores)
    for bad in ("", "   "):
        out = srv.save_seo_direction(draft_id, bad)
        assert "error" in out and "primary" in out["error"], out
    got = srv.get_seo_profile(draft_id)
    assert got["state"] == "analyzing"      # nothing moved
    assert got["direction"] is None


def test_a5_direction_needs_analysis_first(stores):
    brief = {"id": "seo-r2", "format": "blog", "topic": "cà phê",
             "keyword": "kw", "brand_id": "brand-x"}
    draft_id = srv.start_draft(brief)["draft_id"]
    srv.create_seo_profile(draft_id, "kw", dict(CONTEXT))
    out = srv.save_seo_direction(draft_id, "mua hàng")
    assert "error" in out and "analyzing" in out["error"]


def test_a5_direction_proposal_and_user_approval(stores):
    draft_id, _, claim_ids = _to_analyzing(stores)
    out = srv.save_seo_direction(
        draft_id, "mua hàng", secondary_intents=["so sánh giá"],
        rationale_claim_ids=claim_ids[:1])
    assert "error" not in out, out
    assert out["state"] == "direction_review"
    direction = out["direction"]
    assert direction["primary_intent"] == "mua hàng"
    assert direction["secondary_intents"] == ["so sánh giá"]
    assert direction["rationale_claim_ids"] == claim_ids[:1]
    assert direction["status"] == "proposed"
    assert direction["decision"] is None
    out = srv.record_direction_decision(draft_id, "approved",
                                        note="đúng ý định mua")
    assert "error" not in out, out
    assert out["state"] == "outline_review"
    decision = out["direction"]["decision"]
    assert decision["decision"] == "approved"
    assert decision["note"] == "đúng ý định mua"
    assert decision["decided_at"]
    assert out["direction"]["status"] == "approved"
    # Readable back (A5): a reopen shows the recorded decision.
    got = srv.get_seo_profile(draft_id)
    assert got["direction"]["decision"]["decision"] == "approved"


def test_a5_direction_cites_only_known_claims(stores):
    draft_id, _, _ = _to_analyzing(stores)
    out = srv.save_seo_direction(draft_id, "mua hàng",
                                 rationale_claim_ids=["claim-khong-co"])
    assert "error" in out and "claim" in out["error"]


def test_a5_rejection_returns_to_analyzing_with_history(stores):
    draft_id, _, _ = _to_analyzing(stores)
    srv.save_seo_direction(draft_id, "mua hàng")
    out = srv.record_direction_decision(draft_id, "rejected",
                                        note="ý định chính phải là học cách pha")
    assert "error" not in out, out
    assert out["state"] == "analyzing"
    got = srv.get_seo_profile(draft_id)
    assert got["direction"] is None
    assert len(got["direction_history"]) == 1
    rejected = got["direction_history"][0]
    assert rejected["status"] == "rejected"
    assert rejected["decision"]["decision"] == "rejected"
    assert rejected["decision"]["decided_at"]
    # A fresh direction can be proposed after a rejection.
    out = srv.save_seo_direction(draft_id, "học cách pha")
    assert "error" not in out, out
    assert out["state"] == "direction_review"


def test_a5_decision_rules(stores):
    draft_id, _, _ = _to_analyzing(stores)
    out = srv.record_direction_decision(draft_id, "approved")
    assert "error" in out  # no direction proposed yet
    srv.save_seo_direction(draft_id, "mua hàng")
    out = srv.record_direction_decision(draft_id, "maybe")
    assert "error" in out and "approved" in out["error"]


# ---------------------------------------------------------------------------
# Outline gate — sufficiency.proceed_ready, via the policy constant
# ---------------------------------------------------------------------------

def test_gate_propose_refused_on_shortfall_without_exception(stores):
    draft_id, _, _ = _to_outline_review(stores, reads=3)
    suf = srv.get_seo_profile(draft_id)["sufficiency"]
    assert suf["proceed_ready"] is False
    out = srv.propose_seo_outline(draft_id, list(SECTIONS))
    assert "error" in out
    assert "proceed" in out["error"] or "shortfall" in out["error"] \
        or "exception" in out["error"], out
    got = srv.get_seo_profile(draft_id)
    assert got["state"] == "outline_review"
    assert got["outlines"] == []


def test_gate_propose_allowed_with_recorded_exception(stores):
    draft_id, _, claim_ids = _to_analyzing(stores, reads=3)
    out = srv.record_shortfall_exception(
        draft_id, "2 nguồn còn lại không đọc được, đã ghi lý do")
    assert "error" not in out, out
    srv.save_seo_direction(draft_id, "mua hàng")
    srv.record_direction_decision(draft_id, "approved")
    out = srv.propose_seo_outline(draft_id, list(SECTIONS))
    assert "error" not in out, out
    assert out["version"] == 1


def test_gate_partial_counting_runs_through_the_constant(stores, monkeypatch):
    # Round 1 behaviour (constant True): 4 read + 1 partial is ready.
    draft_id, _, _ = _to_outline_review(stores, reads=5, partial_last=True)
    suf = srv.get_seo_profile(draft_id)["sufficiency"]
    assert suf["read_count"] == 5 and suf["proceed_ready"] is True
    out = srv.propose_seo_outline(draft_id, list(SECTIONS))
    assert "error" not in out, out
    # Smith's stricter option (constant False): partial no longer
    # counts, the same profile is short, and the gate refuses.
    draft2, _, _ = _to_outline_review(stores, reads=5, partial_last=True)
    monkeypatch.setattr(seo_profile,
                        "PARTIAL_READS_COUNT_TOWARD_REQUIRED", False)
    suf = srv.get_seo_profile(draft2)["sufficiency"]
    assert suf["read_count"] == 4
    assert suf["full_read_count"] == 4
    assert suf["proceed_ready"] is False
    out = srv.propose_seo_outline(draft2, list(SECTIONS))
    assert "error" in out


# ---------------------------------------------------------------------------
# Outline versions — linked to the draft, one approved-current
# ---------------------------------------------------------------------------

def test_outline_proposal_links_into_the_draft(stores):
    draft_id, _, _ = _to_outline_review(stores)
    out = srv.propose_seo_outline(draft_id, list(SECTIONS))
    assert "error" not in out, out
    assert out["version"] == 1
    assert out["outline"]["status"] == "proposed"
    headings = [s["heading"] for s in out["outline"]["sections"]]
    assert headings == [s["heading"] for s in SECTIONS]
    # The draft remains the writing artefact: its outline field now
    # holds the same sections (the make_outline link, by reference
    # of content — not a second competing store).
    draft = srv.get_draft(draft_id)
    assert [s["heading"] for s in draft["outline"]] == headings
    assert [s["id"] for s in draft["outline"]] == ["s1", "s2"]


def test_outline_section_validation(stores):
    draft_id, _, _ = _to_outline_review(stores)
    out = srv.propose_seo_outline(draft_id, [])
    assert "error" in out
    out = srv.propose_seo_outline(draft_id, [{"intent": "x"}])
    assert "error" in out and "heading" in out["error"]
    out = srv.propose_seo_outline(
        draft_id, [{"heading": "Mục", "target_words": True}])
    assert "error" in out


def test_outline_approval_records_version_time_and_content_sha(stores):
    draft_id, _, _ = _to_outline_review(stores)
    srv.propose_seo_outline(draft_id, list(SECTIONS))
    out = srv.approve_seo_outline(draft_id, 1)
    assert "error" not in out, out
    assert out["state"] == "writing"
    record = out["outline"]
    assert record["status"] == "approved"
    assert record["approved_at"]
    # The content fingerprint at approval time (Round 3 compares
    # against it): the draft still has no written sections.
    assert record["content_sha_at_approval"] == content_sha([], "Untitled")
    got = srv.get_seo_profile(draft_id)
    approved = [o for o in got["outlines"] if o["status"] == "approved"]
    assert len(approved) == 1 and approved[0]["version"] == 1


def test_outline_reproposal_supersedes_pending_proposal(stores):
    draft_id, _, _ = _to_outline_review(stores)
    srv.propose_seo_outline(draft_id, list(SECTIONS))
    out = srv.propose_seo_outline(
        draft_id, [{"heading": "Bản dàn ý khác", "intent": "mới"}])
    assert out["version"] == 2
    got = srv.get_seo_profile(draft_id)
    by_version = {o["version"]: o for o in got["outlines"]}
    assert by_version[1]["status"] == "superseded"
    assert by_version[2]["status"] == "proposed"


def test_outline_new_approval_supersedes_previous_approved(stores):
    draft_id, _, _ = _to_writing(stores)
    out = srv.propose_seo_outline(
        draft_id, [{"heading": "Dàn ý v2", "intent": "sửa"}])
    assert "error" not in out, out
    assert out["version"] == 2
    # v1 stays the approved-current until v2 is approved.
    got = srv.get_seo_profile(draft_id)
    approved = [o for o in got["outlines"] if o["status"] == "approved"]
    assert [o["version"] for o in approved] == [1]
    out = srv.approve_seo_outline(draft_id, 2)
    assert "error" not in out, out
    got = srv.get_seo_profile(draft_id)
    by_version = {o["version"]: o for o in got["outlines"]}
    assert by_version[1]["status"] == "superseded"
    assert by_version[2]["status"] == "approved"
    approved = [o for o in got["outlines"] if o["status"] == "approved"]
    assert len(approved) == 1  # exactly ONE approved-current, always


def test_outline_approve_rules(stores):
    draft_id, _, _ = _to_outline_review(stores)
    out = srv.approve_seo_outline(draft_id, 99)
    assert "error" in out  # unknown version
    srv.propose_seo_outline(draft_id, list(SECTIONS))
    srv.propose_seo_outline(draft_id, [{"heading": "Khác"}])
    out = srv.approve_seo_outline(draft_id, 1)
    assert "error" in out  # v1 was superseded by the re-proposal
    out = srv.approve_seo_outline(draft_id, 2)
    assert "error" not in out, out


def test_outline_propose_wrong_state_refused(stores):
    draft_id, _, _ = _to_analyzing(stores)
    out = srv.propose_seo_outline(draft_id, list(SECTIONS))
    assert "error" in out and "analyzing" in out["error"]


# ---------------------------------------------------------------------------
# A6 — invalidation: fenced mutations + the controlled reopen path
# ---------------------------------------------------------------------------

def test_a6_mutations_refused_in_later_states(stores):
    draft_id, sources, _ = _to_writing(stores)
    for out in (
            srv.add_seo_source(draft_id, "https://x.vn/1", observed_rank=1),
            srv.record_source_read(draft_id, sources[0]["source_id"], "read"),
            srv.save_seo_analysis(draft_id,
                                  [{"text": "X", "kind": "inference",
                                    "source_ids": []}]),
            srv.record_shortfall_exception(draft_id, "lý do")):
        assert "error" in out and "writing" in out["error"], out


def test_a6_reopen_invalidates_approvals_and_keeps_history(stores):
    draft_id, _, _ = _to_writing(stores)
    out = srv.reopen_seo_analysis(draft_id, "   ")
    assert "error" in out  # a reason is required
    out = srv.reopen_seo_analysis(
        draft_id, "nguồn 2 đổi nội dung, phải phân tích lại")
    assert "error" not in out, out
    assert out["state"] == "analyzing"
    assert out["invalidated"]["outlines"] == [1]
    got = srv.get_seo_profile(draft_id)
    assert got["direction"] is None
    history = got["direction_history"]
    assert len(history) == 1
    assert history[0]["status"] == "invalidated"
    assert history[0]["invalidation_reason"].startswith("nguồn 2")
    assert history[0]["invalidated_at"]
    # The approval itself is preserved in the history record.
    assert history[0]["decision"]["decision"] == "approved"
    by_version = {o["version"]: o for o in got["outlines"]}
    assert by_version[1]["status"] == "invalidated"
    assert by_version[1]["invalidation_reason"].startswith("nguồn 2")
    assert got["reopen_log"][0]["from_state"] == "writing"
    assert got["reopen_log"][0]["reason"].startswith("nguồn 2")
    # The invalidated outline version can never be approved.
    out = srv.approve_seo_outline(draft_id, 1)
    assert "error" in out
    # Inputs are mutable again, and the whole flow can re-run.
    out = srv.add_seo_source(draft_id, "https://moi.vn/6", observed_rank=5)
    assert "error" in out  # rank 5 is taken — normal rules apply again
    out = srv.save_seo_analysis(
        draft_id, [{"text": "Phân tích lại", "kind": "inference",
                    "source_ids": []}])
    assert "error" not in out, out
    srv.save_seo_direction(draft_id, "mua hàng")
    srv.record_direction_decision(draft_id, "approved")
    out = srv.propose_seo_outline(draft_id, list(SECTIONS))
    assert "error" not in out and out["version"] == 2, out
    out = srv.approve_seo_outline(draft_id, 2)
    assert "error" not in out, out
    got = srv.get_seo_profile(draft_id)
    approved = [o for o in got["outlines"] if o["status"] == "approved"]
    assert [o["version"] for o in approved] == [2]


def test_a6_reopen_from_direction_review(stores):
    draft_id, _, _ = _to_analyzing(stores)
    srv.save_seo_direction(draft_id, "mua hàng")
    out = srv.reopen_seo_analysis(draft_id, "đổi hướng")
    assert "error" not in out, out
    assert out["state"] == "analyzing"
    got = srv.get_seo_profile(draft_id)
    assert got["direction_history"][0]["status"] == "invalidated"


def test_a6_reopen_refused_when_nothing_to_invalidate(stores):
    draft_id, _, _ = _to_analyzing(stores)
    out = srv.reopen_seo_analysis(draft_id, "thử")
    assert "error" in out and "analyzing" in out["error"]


# ---------------------------------------------------------------------------
# page_type — assigned at read time (Round 1 notes, ambiguity #3)
# ---------------------------------------------------------------------------

def test_page_type_rules(stores):
    brief = {"id": "seo-r2", "format": "blog", "topic": "cà phê",
             "keyword": "kw", "brand_id": "brand-x"}
    draft_id = srv.start_draft(brief)["draft_id"]
    srv.create_seo_profile(draft_id, "kw", dict(CONTEXT))
    src = srv.add_seo_source(draft_id, "https://a.vn/1", observed_rank=1)
    assert src["page_type"] is None
    out = srv.record_source_read(draft_id, src["source_id"], "read",
                                 page_type="blog")
    assert "error" in out and "page_type" in out["error"]
    out = srv.record_source_read(draft_id, src["source_id"], "read",
                                 page_type="article")
    assert "error" not in out, out
    assert out["page_type"] == "article"
    # A later read event without page_type leaves the value alone.
    out = srv.record_source_read(draft_id, src["source_id"], "partial")
    assert out["page_type"] == "article"
    got = srv.get_seo_profile(draft_id)
    assert got["sources"][0]["page_type"] == "article"


# ---------------------------------------------------------------------------
# A7 re-check — Round 2 flow still touches no live config stores
# ---------------------------------------------------------------------------

def _snapshot(dirs: list[Path]) -> dict[str, bytes]:
    snap = {}
    for d in dirs:
        if not d.exists():
            continue
        for p in sorted(d.rglob("*")):
            if p.is_file():
                snap[str(p)] = p.read_bytes()
    return snap


def test_a7_round2_flow_touches_no_live_config_stores(stores):
    loaders.save_facts({"brand_id": "brand-x", "facts": {
        "price_vnd_per_250g": 50000, "pack_weight_g": 250,
        "origins": ["Bến Tre"]}})
    loaders.save_brand_draft({"brand": {"id": "brand-x",
                                        "name": "Brand X"}})
    loaders.save_corpus_entry({
        "id": "van-hoc", "version": 1, "title": "Văn học",
        "kind": "literature", "profile_id": "p-1",
        "legitimacy_basis": "public domain", "word_count": 1000})
    loaders.save_corpus_choice("brand-x", {
        "corpus_id": "van-hoc", "version": 1, "profile_id": "p-1"})
    loaders.save_taste_doc({"brand_id": "brand-x", "entries": [
        {"verdict": "pending", "text": "Bài mẫu của chủ."}]})
    guarded = [stores / sub for sub in (
        "facts", "brand_drafts", "corpus_registry", "corpus_choices",
        "taste", "style_profiles", "research_packs")]
    draft_id, _, _ = _to_writing(stores)
    draft_before = srv.get_draft(draft_id)
    before = _snapshot(guarded)
    assert before
    srv.reopen_seo_analysis(draft_id, "kiểm tra bất biến A7")
    srv.save_seo_direction(draft_id, "mua hàng")
    srv.record_direction_decision(draft_id, "approved")
    srv.propose_seo_outline(draft_id, list(SECTIONS))
    srv.approve_seo_outline(draft_id, 2)
    assert _snapshot(guarded) == before
    # The draft itself changed ONLY in its outline (the link).
    draft_after = srv.get_draft(draft_id)
    for key in ("brief", "sections", "gate_history", "status"):
        assert draft_after[key] == draft_before[key], key
