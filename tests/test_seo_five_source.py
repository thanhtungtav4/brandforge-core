"""Fail-first tests for SEO five-source Round 1 (specs/
seo-five-source-feature.md, plan Round 1): the per-draft SEO profile
store + source management + analysis with provenance.

Criteria covered (spec section 7):
- A1: create pins the draft's brand, persists keyword + search
  context + state; reopening returns them all.
- A2: the 5 original results keep rank/time; a failed read and a
  replacement keep full history and reasons; the original record is
  never rewritten.
- A3: shortfall is reported; proceeding with <5 needs a recorded
  exception with a reason and at least 1 read source; 0 reads can
  never proceed.
- A4: every claim is sourced or labelled inference; a claim citing
  an unread source is flagged, never silently treated as analysed.
- A7: the whole flow writes NOTHING into facts / brand / corpus /
  taste stores (competitor data never becomes live config).
- A9: the SEO store rides backup export/restore round-trip.

All stores point at tmp dirs — no live store is touched.
"""
from __future__ import annotations

import hashlib
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


def _draft(brand_id: str | None = "brand-x") -> str:
    brief = {"id": "seo-trial", "format": "blog", "topic": "cà phê",
             "keyword": "cà phê nguyên chất"}
    if brand_id is not None:
        brief["brand_id"] = brand_id
    return srv.start_draft(brief)["draft_id"]


def _profile(stores, brand_id: str | None = "brand-x") -> str:
    draft_id = _draft(brand_id)
    out = srv.create_seo_profile(draft_id, "cà phê nguyên chất",
                                 dict(CONTEXT))
    assert "error" not in out, out
    return draft_id


def _add_five(stores, draft_id: str) -> list[dict]:
    out = []
    for rank in range(1, 6):
        src = srv.add_seo_source(
            draft_id, f"https://example-{rank}.vn/bai-{rank}",
            title=f"Bài {rank}", observed_rank=rank,
            observed_at=f"2026-11-23T08:3{rank}:00+07:00")
        assert "error" not in src, src
        out.append(src)
    return out


def _read_all(stores, draft_id: str, sources: list[dict],
              status: str = "read") -> None:
    for src in sources:
        out = srv.record_source_read(draft_id, src["source_id"], status)
        assert "error" not in out, out


# ---------------------------------------------------------------------------
# A1 — create / reopen
# ---------------------------------------------------------------------------

def test_a1_create_persists_and_reopens(stores):
    draft_id = _profile(stores)
    got = srv.get_seo_profile(draft_id)
    assert "error" not in got, got
    assert got["draft_id"] == draft_id
    assert got["brand_id"] == "brand-x"          # pinned from the brief
    assert got["keyword"] == "cà phê nguyên chất"
    assert got["search_context"] == CONTEXT      # khu vực / ngữ cảnh đủ
    assert got["state"] == "collecting"
    assert got["schema_version"] == 1
    assert got["sources"] == []
    assert got["analysis"] is None
    assert got["shortfall_exception"] is None
    assert got["sufficiency"]["required_sources"] == 5
    assert got["sufficiency"]["read_count"] == 0
    assert got["sufficiency"]["shortfall"] is True


def test_a1_create_rejects_brand_mismatch(stores):
    draft_id = _draft("brand-x")
    out = srv.create_seo_profile(draft_id, "kw", dict(CONTEXT),
                                 brand_id="brand-y")
    assert "error" in out and "brand" in out["error"]


def test_a1_create_accepts_matching_brand(stores):
    draft_id = _draft("brand-x")
    out = srv.create_seo_profile(draft_id, "kw", dict(CONTEXT),
                                 brand_id="brand-x")
    assert "error" not in out, out


def test_a1_create_requires_full_search_context(stores):
    draft_id = _draft()
    for missing in CONTEXT:
        ctx = {k: v for k, v in CONTEXT.items() if k != missing}
        out = srv.create_seo_profile(draft_id, "kw", ctx)
        assert "error" in out and missing in out["error"], (missing, out)
    ctx = dict(CONTEXT, market="  ")
    out = srv.create_seo_profile(draft_id, "kw", ctx)
    assert "error" in out


def test_a1_create_unknown_draft_and_missing_profile(stores):
    out = srv.create_seo_profile("d-nope", "kw", dict(CONTEXT))
    assert "error" in out
    draft_id = _draft()
    out = srv.get_seo_profile(draft_id)
    assert "error" in out and "not found" in out["error"]


def test_a1_create_duplicate_refused(stores):
    draft_id = _profile(stores)
    out = srv.create_seo_profile(draft_id, "kw khác", dict(CONTEXT))
    assert "error" in out and "already exists" in out["error"]


def test_a1_draft_without_brand_refused(stores):
    draft_id = _draft(None)
    out = srv.create_seo_profile(draft_id, "kw", dict(CONTEXT))
    assert "error" in out and "brand" in out["error"]


# ---------------------------------------------------------------------------
# A2 — sources: ranks, reads, replacements, history
# ---------------------------------------------------------------------------

def test_a2_five_originals_keep_rank_and_time(stores):
    draft_id = _profile(stores)
    _add_five(stores, draft_id)
    got = srv.get_seo_profile(draft_id)
    originals = [s for s in got["sources"]
                 if s["replaces_source_id"] is None]
    assert [s["observed_rank"] for s in originals] == [1, 2, 3, 4, 5]
    assert originals[0]["observed_at"] == "2026-11-23T08:31:00+07:00"
    assert all(s["entry_source"] == "manual" for s in originals)
    # Read status is NEVER inferred from having a URL (spec 4).
    assert all(s["read_status"] == "pending" for s in originals)


def test_a2_rank_rules(stores):
    draft_id = _profile(stores)
    srv.add_seo_source(draft_id, "https://a.vn/1", observed_rank=1)
    dup = srv.add_seo_source(draft_id, "https://a.vn/2", observed_rank=1)
    assert "error" in dup and "rank" in dup["error"]
    for bad in (0, 6, -1):
        out = srv.add_seo_source(draft_id, f"https://a.vn/x{bad}",
                                 observed_rank=bad)
        assert "error" in out, bad
    dup_url = srv.add_seo_source(draft_id, "https://a.vn/1",
                                 observed_rank=2)
    assert "error" in dup_url


def test_a2_failed_read_and_replacement_keep_history(stores):
    draft_id = _profile(stores)
    src = srv.add_seo_source(draft_id, "https://a.vn/1", observed_rank=1)
    read = srv.record_source_read(draft_id, src["source_id"], "failed",
                                  reason="HTTP 403 khi đọc")
    assert read["read_status"] == "failed"
    assert read["read_reason"] == "HTTP 403 khi đọc"
    assert read["read_at"]
    assert read["read_events"][0]["status"] == "failed"
    repl = srv.add_replacement_source(
        draft_id, src["source_id"], "https://b.vn/thay-the",
        observed_rank=7, reason="nguồn gốc không đọc được")
    assert "error" not in repl, repl
    assert repl["replaces_source_id"] == src["source_id"]
    got = srv.get_seo_profile(draft_id)
    by_id = {s["source_id"]: s for s in got["sources"]}
    original = by_id[src["source_id"]]
    # The original record is NEVER rewritten by a replacement.
    assert original["read_status"] == "failed"
    assert original["read_reason"] == "HTTP 403 khi đọc"
    assert original["url"] == "https://a.vn/1"
    assert len(got["sources"]) == 2


def test_a2_read_recording_rules(stores):
    draft_id = _profile(stores)
    src = srv.add_seo_source(draft_id, "https://a.vn/1", observed_rank=1)
    out = srv.record_source_read(draft_id, "src-nope", "read")
    assert "error" in out
    out = srv.record_source_read(draft_id, src["source_id"], "done")
    assert "error" in out
    out = srv.record_source_read(draft_id, src["source_id"], "pending")
    assert "error" in out  # pending is the initial state, not a read event
    part = srv.record_source_read(draft_id, src["source_id"], "partial",
                                  reason="trang cắt giữa chừng",
                                  content_truncated=True)
    assert part["read_status"] == "partial"
    assert part["content_truncated"] is True
    again = srv.record_source_read(draft_id, src["source_id"], "read")
    assert again["read_status"] == "read"
    assert [e["status"] for e in again["read_events"]] == ["partial", "read"]


# ---------------------------------------------------------------------------
# A3 — shortfall + exception
# ---------------------------------------------------------------------------

def test_a3_shortfall_reported(stores):
    draft_id = _profile(stores)
    sources = _add_five(stores, draft_id)
    _read_all(stores, draft_id, sources[:3])
    suf = srv.get_seo_profile(draft_id)["sufficiency"]
    assert suf["read_count"] == 3
    assert suf["required_sources"] == 5
    assert suf["shortfall"] is True
    assert suf["exception_recorded"] is False
    assert suf["proceed_ready"] is False


def test_a3_five_reads_sufficient_partial_counts(stores):
    draft_id = _profile(stores)
    sources = _add_five(stores, draft_id)
    _read_all(stores, draft_id, sources[:4])
    srv.record_source_read(draft_id, sources[4]["source_id"], "partial",
                           content_truncated=True)
    suf = srv.get_seo_profile(draft_id)["sufficiency"]
    assert suf["read_count"] == 5        # read + partial
    assert suf["full_read_count"] == 4   # partial stays visible
    assert suf["shortfall"] is False
    assert suf["proceed_ready"] is True


def test_a3_exception_rules(stores):
    draft_id = _profile(stores)
    sources = _add_five(stores, draft_id)
    # 0 reads: an exception can NEVER authorise proceeding.
    out = srv.record_shortfall_exception(draft_id, "khách cần bài gấp")
    assert "error" in out
    _read_all(stores, draft_id, sources[:2])
    out = srv.record_shortfall_exception(draft_id, "   ")
    assert "error" in out  # a reason is required
    out = srv.record_shortfall_exception(
        draft_id, "2 nguồn còn lại là quảng cáo, không phải bài organic")
    assert "error" not in out, out
    exc = out["shortfall_exception"]
    assert exc["read_count_at_confirmation"] == 2
    assert exc["confirmed_at"]
    got = srv.get_seo_profile(draft_id)
    assert got["shortfall_exception"]["reason"].startswith("2 nguồn")
    suf = got["sufficiency"]
    assert suf["exception_recorded"] is True
    assert suf["shortfall"] is True       # the exception does not fake 5
    assert suf["proceed_ready"] is True   # ...but proceeding is authorised


# ---------------------------------------------------------------------------
# A4 — analysis provenance
# ---------------------------------------------------------------------------

def _claims(*specs):
    return [{"text": t, "kind": k, "source_ids": ids}
            for t, k, ids in specs]


def test_a4_sourced_claim_needs_sources(stores):
    draft_id = _profile(stores)
    _add_five(stores, draft_id)
    out = srv.save_seo_analysis(
        draft_id, _claims(("Ý định chính là mua hàng", "sourced", [])))
    assert "error" in out
    out = srv.save_seo_analysis(
        draft_id, _claims(("Ý định chính là mua hàng", "guess", [])))
    assert "error" in out  # kind must be sourced | inference


def test_a4_inference_claim_needs_no_source(stores):
    draft_id = _profile(stores)
    out = srv.save_seo_analysis(
        draft_id, _claims(("Có vẻ người đọc muốn so giá", "inference", [])))
    assert "error" not in out, out
    assert out["flagged_claim_ids"] == []


def test_a4_claim_citing_unread_source_is_flagged(stores):
    draft_id = _profile(stores)
    sources = _add_five(stores, draft_id)
    _read_all(stores, draft_id, sources[:1])
    claims = _claims(
        ("Bài 1 mở bằng giá", "sourced", [sources[0]["source_id"]]),
        ("Bài 3 nói về nguồn gốc", "sourced", [sources[2]["source_id"]]),
    )
    out = srv.save_seo_analysis(draft_id, claims)
    assert "error" not in out, out
    assert len(out["flagged_claim_ids"]) == 1
    stored = srv.get_seo_profile(draft_id)["analysis"]["claims"]
    flagged = [c for c in stored if c["flags"]]
    assert len(flagged) == 1
    assert flagged[0]["text"] == "Bài 3 nói về nguồn gốc"
    assert sources[2]["source_id"] in flagged[0]["flags"][0] or \
        sources[2]["source_id"] in str(flagged[0]["flags"])


def test_a4_claim_citing_unknown_source_refused(stores):
    draft_id = _profile(stores)
    out = srv.save_seo_analysis(
        draft_id, _claims(("X", "sourced", ["src-khong-co"])))
    assert "error" in out


def test_a4_analysis_state_and_versioning(stores):
    draft_id = _profile(stores)
    sources = _add_five(stores, draft_id)
    _read_all(stores, draft_id, sources)
    out = srv.save_seo_analysis(
        draft_id, _claims(("Bài 1 mở bằng giá", "sourced",
                            [sources[0]["source_id"]])))
    assert out["state"] == "analyzing"
    assert out["analysis_version"] == 1
    out2 = srv.save_seo_analysis(
        draft_id, _claims(("Nhận định mới", "inference", []),
                          ("Bài 2 nói về rang", "sourced",
                           [sources[1]["source_id"]])))
    assert out2["analysis_version"] == 2
    got = srv.get_seo_profile(draft_id)
    assert got["state"] == "analyzing"
    assert len(got["analysis"]["claims"]) == 2
    assert len(got["analysis_history"]) == 1
    assert got["analysis_history"][0]["version"] == 1


# ---------------------------------------------------------------------------
# State guard — Round 1 owns collecting/analyzing only
# ---------------------------------------------------------------------------

def test_mutations_refused_beyond_analyzing(stores):
    draft_id = _profile(stores)
    profile = loaders.load_seo_profile(draft_id)
    profile["state"] = "writing"  # a state only later rounds may reach
    loaders.save_seo_profile(profile)
    out = srv.add_seo_source(draft_id, "https://a.vn/9", observed_rank=1)
    assert "error" in out and "writing" in out["error"]
    out = srv.save_seo_analysis(
        draft_id, _claims(("X", "inference", [])))
    assert "error" in out


# ---------------------------------------------------------------------------
# A7 — hard invariant: competitor data never touches live config stores
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


def test_a7_flow_touches_no_live_config_stores(stores):
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
        "taste", "drafts", "style_profiles", "research_packs")]
    draft_id = _draft()  # setup, before the snapshot — not the flow
    before = _snapshot(guarded)
    assert before  # the seeds really landed

    out = srv.create_seo_profile(draft_id, "cà phê nguyên chất",
                                 dict(CONTEXT))
    assert "error" not in out, out
    sources = _add_five(stores, draft_id)
    srv.record_source_read(draft_id, sources[0]["source_id"], "failed",
                           reason="403")
    srv.add_replacement_source(draft_id, sources[0]["source_id"],
                               "https://b.vn/thay", observed_rank=6,
                               reason="thay nguồn lỗi")
    _read_all(stores, draft_id, sources[1:])
    srv.save_seo_analysis(draft_id, _claims(
        ("Bài 2 mở bằng giá", "sourced", [sources[1]["source_id"]]),
        ("Suy luận riêng", "inference", [])))
    srv.record_shortfall_exception(draft_id, "1 nguồn chết, đã thay")
    assert _snapshot(guarded) == before


# ---------------------------------------------------------------------------
# A9 — backup / restore round-trip
# ---------------------------------------------------------------------------

def test_a9_backup_round_trip_includes_seo_store(stores):
    draft_id = _profile(stores)
    _add_five(stores, draft_id)
    seo_file = next((stores / "seo_profiles").glob("*.json"))
    before = seo_file.read_bytes()
    out = srv.export_core_data()
    assert "error" not in out, out
    assert out["stores"]["seo_profiles"]["files"] >= 1
    seo_file.unlink()
    report = srv.restore_core_data(out["archive"])
    assert "error" not in report, report
    assert f"seo_profiles/{seo_file.name}" in report["restored"]
    assert seo_file.read_bytes() == before
    got = srv.get_seo_profile(draft_id)
    assert got["keyword"] == "cà phê nguyên chất"  # old records still read


# ---------------------------------------------------------------------------
# Corrupt records — structured error, listed as corrupt
# ---------------------------------------------------------------------------

def test_corrupt_profile_surfaces_structured_error(stores):
    draft_id = _profile(stores)
    path = next((stores / "seo_profiles").glob("*.json"))
    path.write_text("{not json", encoding="utf-8")
    out = srv.get_seo_profile(draft_id)
    assert "error" in out and "corrupt" in out["error"]
    listed = {e["draft_id"]: e for e in loaders.list_seo_profiles()}
    assert listed[draft_id]["corrupt"] is True
