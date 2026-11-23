"""Fail-first tests for SEO five-source Round 3 (specs/
seo-five-source-feature.md sections 6.5-6.7 + plan Round 3):
review records bound to the approved outline version + the
current content fingerprint, ONE core export-readiness decision,
its enforcement inside export_draft_to_wordpress, A6 invalidation
extended to `reviewing`, and the outcome-unknown no-duplicate
guarantee on the SEO path.

Criteria covered (spec section 7):
- A8: an SEO draft exports ONLY when the core confirms every
  condition — each missing condition is refused with a NAMED
  reason (no approved outline / edited after outline approval /
  no current review / review stale against outline or content /
  open mandatory items / expert review pending / gate reasons);
  a lost create response never duplicates the post on retry.
- A6 (extension): reopening from `reviewing` invalidates the
  review as well as direction + outline, history preserved.
- Non-SEO drafts keep the F1/F2 export behaviour byte-identically
  (pinned here by the exact success key set; the full differential
  lives in tests/test_wordpress.py).

All stores point at tmp dirs and WordPress is a local mock — no
live store and no real site is touched.
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import mcp_server as srv  # noqa: E402
from content_core import wordpress as wp  # noqa: E402

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

BRAND = "moc-brew"
APP_PASSWORD = "abcd efgh ijkl mnop"
USERNAME = "editor1"

CONTEXT = {"market": "VN", "language": "vi", "location": "Hồ Chí Minh",
           "device": "mobile", "searched_at": "2026-11-23T08:30:00+07:00"}

BRIEF = {"id": "seo-r3", "format": "blog", "brand_id": BRAND,
         "topic": "cà phê Mộc Brew cho văn phòng", "keyword": "cà phê",
         "title": "Bài SEO vòng 3"}

# The gate-passing moc-brew sections from the exporter tests
# (brand 90 / craft 90, measured there).
SECTIONS = [
    {"id": "s1", "heading": "Mở đầu", "intent": "intro",
     "text": "Mộc Brew rang cà phê vào thứ Ba hàng tuần cho bạn. "
             "Một gói 250g có giá 185.000đ."},
    {"id": "s2", "heading": "Hương vị", "intent": "body",
     "text": "Hạt Arabica Cầu Đất cho vị chua nhẹ và hậu ngọt rõ. "
             "Mình ghi rõ ngày rang trên từng gói."},
]

OK_ITEMS = [
    {"key": "intent_match", "label": "Bài đáp ứng ý định đã duyệt",
     "status": "ok", "mandatory": True},
    {"key": "cta_present", "label": "Có CTA về trang đích của brand",
     "status": "ok", "mandatory": True},
    {"key": "tone", "label": "Giọng văn đúng brand",
     "status": "ok", "mandatory": False},
]


# ---------------------------------------------------------------------------
# Minimal mock WP (posts only — the SEO test drafts are text-only)
# ---------------------------------------------------------------------------

class _MockWP:
    def __init__(self):
        self.requests: list[dict] = []
        self.posts: dict[int, dict] = {}
        self.next_post_id = 4242
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send_json(self, code, obj):
                raw = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                outer.requests.append({"method": "POST",
                                       "path": self.path, "body": body})
                if self.path == "/wp-json/wp/v2/posts":
                    payload = json.loads(body or b"{}")
                    post_id = outer.next_post_id
                    outer.next_post_id += 1
                    outer.posts[post_id] = {
                        "id": post_id, "status": payload.get("status"),
                        "slug": payload.get("slug", ""),
                        "link": f"http://wp.test/?p={post_id}"}
                    self._send_json(201, outer.posts[post_id])
                    return
                self._send_json(404, {"code": "rest_no_route",
                                      "message": "unknown path"})

            def do_GET(self):
                outer.requests.append({"method": "GET",
                                       "path": self.path, "body": b""})
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/wp-json/wp/v2/posts":
                    qs = urllib.parse.parse_qs(parsed.query)
                    slug = (qs.get("slug") or [""])[0]
                    matches = [p for p in outer.posts.values()
                               if not slug or p.get("slug") == slug]
                    self._send_json(200, matches)
                    return
                self._send_json(404, {"code": "rest_no_route",
                                      "message": "unknown path"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def posts_payloads(self) -> list[dict]:
        return [json.loads(r["body"]) for r in self.requests
                if r["method"] == "POST"
                and r["path"] == "/wp-json/wp/v2/posts"]


@pytest.fixture()
def mock_wp():
    mock = _MockWP()
    yield mock
    mock.stop()


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    for var, sub in STORE_ENVS:
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.setenv("CONTENT_CORE_WP_SECRETS_FILE",
                       str(tmp_path / "wordpress_secrets.json"))
    pdir = tmp_path / "personas"
    pdir.mkdir(parents=True, exist_ok=True)
    for name in ("persona_linh.json", "persona_an.json"):
        (pdir / name).write_text(
            (PROJECT_DIR / "data" / name).read_text(encoding="utf-8"),
            encoding="utf-8")
    return tmp_path


def _setup_wp(stores, mock_wp):
    srv.set_wordpress_target(BRAND, mock_wp.base_url, USERNAME)
    (stores / "wordpress_secrets.json").write_text(
        json.dumps({BRAND: APP_PASSWORD}), encoding="utf-8")


def _to_writing(stores):
    """Drive a fresh SEO profile to `writing` with the gate-passing
    content written BEFORE the outline approval — so the approval's
    content fingerprint describes the final text (the supported
    order documented in the round-3 notes). Returns draft_id."""
    draft_id = srv.start_draft(dict(BRIEF))["draft_id"]
    out = srv.create_seo_profile(draft_id, "cà phê", dict(CONTEXT))
    assert "error" not in out, out
    sources = []
    for rank in range(1, 6):
        src = srv.add_seo_source(
            draft_id, f"https://example-{rank}.vn/bai-{rank}",
            title=f"Bài {rank}", observed_rank=rank)
        assert "error" not in src, src
        sources.append(src)
    for src in sources:
        out = srv.record_source_read(draft_id, src["source_id"], "read")
        assert "error" not in out, out
    out = srv.save_seo_analysis(draft_id, [
        {"text": "Bài 1 mở bằng giá", "kind": "sourced",
         "source_ids": [sources[0]["source_id"]]}])
    assert "error" not in out, out
    srv.save_seo_direction(draft_id, "mua hàng")
    srv.record_direction_decision(draft_id, "approved")
    out = srv.propose_seo_outline(
        draft_id, [{"heading": s["heading"], "intent": s["intent"]}
                   for s in SECTIONS])
    assert "error" not in out, out
    for sec in SECTIONS:
        out = srv.save_section(draft_id, sec["id"], sec["heading"],
                               sec["text"], intent=sec["intent"],
                               target_words=120)
        assert "error" not in out, out
    out = srv.approve_seo_outline(draft_id, 1)
    assert "error" not in out, out
    return draft_id


def _to_ready(stores, mock_wp, items=None, verdict="approved",
              expert_review=None):
    """_to_writing + a passing final_gate + a clean current review.
    Returns draft_id in state `reviewing`, fully export-ready."""
    draft_id = _to_writing(stores)
    gate = srv.final_gate(draft_id)
    assert gate["passed"], gate
    out = srv.record_seo_review(draft_id, items or list(OK_ITEMS),
                                verdict, expert_review=expert_review)
    assert "error" not in out, out
    ready = srv.get_seo_export_readiness(draft_id)
    assert ready["ready"] is True, ready
    return draft_id


def _reason_codes(readiness: dict) -> list[str]:
    return [r["code"] for r in readiness["reasons"]]


# ---------------------------------------------------------------------------
# Review records — binding, currency, validation
# ---------------------------------------------------------------------------

def test_review_binds_to_outline_version_and_content(stores):
    draft_id = _to_writing(stores)
    gate = srv.final_gate(draft_id)
    assert gate["passed"], gate
    out = srv.record_seo_review(draft_id, list(OK_ITEMS), "approved")
    assert "error" not in out, out
    assert out["state"] == "reviewing"  # first review moves the state
    review = out["review"]
    assert review["outline_version"] == 1
    assert review["verdict"] == "approved"
    assert review["recorded_at"]
    assert len(review["content_sha"]) == 64
    status = srv.get_seo_profile(draft_id)["review_status"]
    assert status["exists"] is True and status["current"] is True
    assert status["open_mandatory_items"] == []


def test_review_validation(stores):
    draft_id = _to_writing(stores)
    out = srv.record_seo_review(draft_id, [], "approved")
    assert "error" in out  # no items, no review
    out = srv.record_seo_review(
        draft_id, [{"key": "x", "label": "X", "status": "maybe",
                    "mandatory": True}], "approved")
    assert "error" in out and "status" in out["error"]
    out = srv.record_seo_review(draft_id, list(OK_ITEMS), "ok-ish")
    assert "error" in out and "verdict" in out["error"]
    out = srv.record_seo_review(
        draft_id,
        [{"key": "x", "label": "X", "status": "ok", "mandatory": True},
         {"key": "x", "label": "Trùng khóa", "status": "ok",
          "mandatory": False}], "approved")
    assert "error" in out  # duplicate item keys refused
    got = srv.get_seo_profile(draft_id)
    assert got["state"] == "writing" and got["reviews"] == []


def test_review_refused_before_writing(stores):
    draft_id = srv.start_draft(dict(BRIEF))["draft_id"]
    srv.create_seo_profile(draft_id, "cà phê", dict(CONTEXT))
    out = srv.record_seo_review(draft_id, list(OK_ITEMS), "approved")
    assert "error" in out and "collecting" in out["error"]


def test_expert_review_rules(stores):
    draft_id = _to_writing(stores)
    # done without naming the reviewer is refused (spec: who + when).
    out = srv.record_seo_review(
        draft_id, list(OK_ITEMS), "approved",
        expert_review={"required": True, "status": "done"})
    assert "error" in out and "reviewer" in out["error"]
    # required + not_required is a contradiction.
    out = srv.record_seo_review(
        draft_id, list(OK_ITEMS), "approved",
        expert_review={"required": True, "status": "not_required"})
    assert "error" in out
    out = srv.record_seo_review(
        draft_id, list(OK_ITEMS), "approved",
        expert_review={"required": True, "status": "done",
                       "reviewer": "BS. Nguyễn A"})
    assert "error" not in out, out
    expert = out["review"]["expert_review"]
    assert expert["status"] == "done"
    assert expert["reviewer"] == "BS. Nguyễn A"
    assert expert["reviewed_at"]


# ---------------------------------------------------------------------------
# Readiness — every missing condition named (A8)
# ---------------------------------------------------------------------------

def test_readiness_names_everything_missing_early(stores):
    draft_id = srv.start_draft(dict(BRIEF))["draft_id"]
    srv.create_seo_profile(draft_id, "cà phê", dict(CONTEXT))
    ready = srv.get_seo_export_readiness(draft_id)
    assert ready["ready"] is False
    codes = _reason_codes(ready)
    assert "state_not_exportable" in codes
    assert "no_approved_outline" in codes
    assert "no_review_recorded" in codes
    assert "never_ran_gate" in codes  # the F1 reason, reused verbatim


def test_readiness_without_profile_is_a_structured_error(stores):
    draft_id = srv.start_draft(dict(BRIEF))["draft_id"]
    out = srv.get_seo_export_readiness(draft_id)
    assert "error" in out and "seo" in out["error"]


def test_readiness_blocked_by_missing_review_then_ready(stores):
    draft_id = _to_writing(stores)
    gate = srv.final_gate(draft_id)
    assert gate["passed"], gate
    ready = srv.get_seo_export_readiness(draft_id)
    assert ready["ready"] is False
    assert _reason_codes(ready) == ["no_review_recorded"], ready
    srv.record_seo_review(draft_id, list(OK_ITEMS), "approved")
    ready = srv.get_seo_export_readiness(draft_id)
    assert ready == {**ready, "ready": True, "reasons": []}
    assert ready["ready"] is True and ready["reasons"] == []


def test_readiness_blocked_by_gate_reasons(stores):
    draft_id = _to_writing(stores)
    srv.record_seo_review(draft_id, list(OK_ITEMS), "approved")
    ready = srv.get_seo_export_readiness(draft_id)
    assert ready["ready"] is False
    assert _reason_codes(ready) == ["never_ran_gate"], ready


def test_readiness_open_mandatory_item_blocks_by_name(stores):
    draft_id = _to_writing(stores)
    srv.final_gate(draft_id)
    items = [dict(OK_ITEMS[0]),
             {"key": "cta_present", "label": "Có CTA",
              "status": "issue", "mandatory": True,
              "note": "Chưa có CTA nào trong bài"},
             dict(OK_ITEMS[2])]
    srv.record_seo_review(draft_id, items, "approved")
    ready = srv.get_seo_export_readiness(draft_id)
    assert ready["ready"] is False
    hits = [r for r in ready["reasons"]
            if r["code"] == "open_mandatory_items"]
    assert hits and hits[0]["items"] == ["cta_present"], ready
    # A NON-mandatory issue does not block.
    draft2 = _to_writing(stores)
    srv.final_gate(draft2)
    items2 = [dict(OK_ITEMS[0]), dict(OK_ITEMS[1]),
              {"key": "tone", "label": "Giọng", "status": "issue",
               "mandatory": False}]
    srv.record_seo_review(draft2, items2, "approved")
    assert srv.get_seo_export_readiness(draft2)["ready"] is True


def test_readiness_expert_pending_blocks(stores):
    draft_id = _to_writing(stores)
    srv.final_gate(draft_id)
    srv.record_seo_review(
        draft_id, list(OK_ITEMS), "approved",
        expert_review={"required": True, "status": "pending"})
    ready = srv.get_seo_export_readiness(draft_id)
    assert ready["ready"] is False
    assert "expert_review_pending" in _reason_codes(ready)
    # A later review with the expert sign-off clears it.
    srv.record_seo_review(
        draft_id, list(OK_ITEMS), "approved",
        expert_review={"required": True, "status": "done",
                       "reviewer": "BS. Nguyễn A"})
    assert srv.get_seo_export_readiness(draft_id)["ready"] is True


def test_readiness_changes_requested_blocks(stores):
    draft_id = _to_writing(stores)
    srv.final_gate(draft_id)
    srv.record_seo_review(draft_id, list(OK_ITEMS), "changes_requested")
    ready = srv.get_seo_export_readiness(draft_id)
    assert ready["ready"] is False
    assert "review_changes_requested" in _reason_codes(ready)


def test_readiness_review_stale_against_new_outline(stores):
    draft_id = _to_writing(stores)
    srv.final_gate(draft_id)
    srv.record_seo_review(draft_id, list(OK_ITEMS), "approved")
    assert srv.get_seo_export_readiness(draft_id)["ready"] is True
    # A new outline version is proposed + approved: the review was
    # bound to version 1 and is no longer current.
    out = srv.propose_seo_outline(
        draft_id, [{"heading": s["heading"], "intent": s["intent"]}
                   for s in SECTIONS])
    assert "error" not in out, out
    srv.approve_seo_outline(draft_id, out["version"])
    status = srv.get_seo_profile(draft_id)["review_status"]
    assert status["current"] is False
    assert "review_stale_outline" in status["stale_reasons"]
    ready = srv.get_seo_export_readiness(draft_id)
    assert ready["ready"] is False
    assert "review_stale_outline" in _reason_codes(ready)
    # A fresh review against version 2 restores readiness.
    srv.record_seo_review(draft_id, list(OK_ITEMS), "approved")
    assert srv.get_seo_export_readiness(draft_id)["ready"] is True


def test_readiness_post_edit_fingerprint_reasons(stores):
    draft_id = _to_writing(stores)
    srv.final_gate(draft_id)
    srv.record_seo_review(draft_id, list(OK_ITEMS), "approved")
    assert srv.get_seo_export_readiness(draft_id)["ready"] is True
    # Edit the content AFTER review + gate: every fingerprint-bound
    # condition must fall, each with its own name.
    srv.save_section(draft_id, "s2", "Hương vị",
                     SECTIONS[1]["text"] + " Thêm một câu mới.",
                     intent="body", target_words=120)
    ready = srv.get_seo_export_readiness(draft_id)
    assert ready["ready"] is False
    codes = _reason_codes(ready)
    assert "edited_after_outline_approval" in codes, ready
    assert "review_stale_content" in codes, ready
    assert "edited_after_pass" in codes, ready  # F1 reason, reused


# ---------------------------------------------------------------------------
# Export enforcement (A8) — refusal before any WP write; success path
# ---------------------------------------------------------------------------

def test_export_refused_when_seo_not_ready_no_wp_write(stores, mock_wp):
    _setup_wp(stores, mock_wp)
    draft_id = _to_writing(stores)
    srv.final_gate(draft_id)  # gate passes; the REVIEW is missing
    out = srv.export_draft_to_wordpress(BRAND, draft_id)
    assert "error" in out, out
    assert out["reason"] == "seo_not_ready", out
    assert [r["code"] for r in out["reasons"]] == ["no_review_recorded"]
    assert mock_wp.posts_payloads() == []  # nothing created
    assert mock_wp.requests == []          # nothing even attempted


def test_export_succeeds_when_ready_and_marks_profile(stores, mock_wp):
    _setup_wp(stores, mock_wp)
    draft_id = _to_ready(stores, mock_wp)
    out = srv.export_draft_to_wordpress(BRAND, draft_id)
    assert "error" not in out, out
    assert out["wp_post_id"] == 4242
    assert out["status"] == "draft"
    assert mock_wp.posts_payloads()[0]["status"] == "draft"
    profile = srv.get_seo_profile(draft_id)
    assert profile["state"] == "exported"
    assert profile["export"]["wp_post_id"] == 4242
    assert profile["export"]["exported_at"]


def test_export_outcome_unknown_adopts_without_duplicate(stores, mock_wp,
                                                         monkeypatch):
    """The create lands server-side but the response is lost: the
    retry must ADOPT the post (F2), never create a second one —
    and the profile is marked exported only once, on adoption."""
    _setup_wp(stores, mock_wp)
    draft_id = _to_ready(stores, mock_wp)
    real_create = wp.create_post

    def lost_create(target, password, payload):
        real_create(target, password, payload)  # post IS created
        raise wp.WPError("simulated: response lost after create")

    monkeypatch.setattr(wp, "create_post", lost_create)
    first = srv.export_draft_to_wordpress(BRAND, draft_id)
    assert "error" in first, first
    assert len(mock_wp.posts) == 1
    profile = srv.get_seo_profile(draft_id)
    assert profile["state"] == "reviewing"  # NOT marked exported
    assert profile.get("export") is None

    monkeypatch.setattr(wp, "create_post", real_create)
    second = srv.export_draft_to_wordpress(BRAND, draft_id)
    assert "error" not in second, second
    assert second["adopted_existing_post"] is True
    assert second["wp_post_id"] == 4242
    assert len(mock_wp.posts) == 1            # still exactly one post
    assert len(mock_wp.posts_payloads()) == 1  # no second create call
    profile = srv.get_seo_profile(draft_id)
    assert profile["state"] == "exported"
    assert profile["export"]["wp_post_id"] == 4242


def test_non_seo_export_success_shape_unchanged(stores, mock_wp):
    """F1/F2 differential pin: a draft WITHOUT an SEO profile keeps
    the exact pre-Round-3 success shape (no SEO keys leak in)."""
    _setup_wp(stores, mock_wp)
    draft_id = srv.start_draft(dict(BRIEF))["draft_id"]
    for sec in SECTIONS:
        srv.save_section(draft_id, sec["id"], sec["heading"], sec["text"],
                         intent=sec["intent"], target_words=120)
    gate = srv.final_gate(draft_id)
    assert gate["passed"], gate
    out = srv.export_draft_to_wordpress(BRAND, draft_id)
    assert "error" not in out, out
    assert set(out) == {"brand_id", "draft_id", "wp_post_id",
                        "edit_link", "post_link", "status", "site_url",
                        "media_uploaded", "media", "warnings"}, out


# ---------------------------------------------------------------------------
# A6 extension — reopen from `reviewing` invalidates the review too
# ---------------------------------------------------------------------------

def test_a6_reopen_from_reviewing_invalidates_review(stores):
    draft_id = _to_writing(stores)
    srv.final_gate(draft_id)
    srv.record_seo_review(draft_id, list(OK_ITEMS), "approved")
    assert srv.get_seo_profile(draft_id)["state"] == "reviewing"
    out = srv.reopen_seo_analysis(draft_id, "nguồn chính đổi nội dung")
    assert "error" not in out, out
    assert out["state"] == "analyzing"
    assert out["invalidated"]["reviews"], out
    got = srv.get_seo_profile(draft_id)
    assert got["reviews"][0]["invalidated_at"]
    assert got["reviews"][0]["invalidation_reason"].startswith("nguồn")
    assert got["reopen_log"][-1]["invalidated_reviews"] == \
        out["invalidated"]["reviews"]
    assert got["review_status"]["current"] is False
    # History is preserved whole: items + verdict still readable.
    assert got["reviews"][0]["verdict"] == "approved"
    assert got["reviews"][0]["items"][0]["key"] == "intent_match"


def test_a6_reopen_from_exported_refused(stores, mock_wp):
    _setup_wp(stores, mock_wp)
    draft_id = _to_ready(stores, mock_wp)
    srv.export_draft_to_wordpress(BRAND, draft_id)
    out = srv.reopen_seo_analysis(draft_id, "muốn làm lại")
    assert "error" in out and "exported" in out["error"]
