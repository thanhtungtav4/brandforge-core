"""Fail-first tests for the WordPress rewrite path (specs/
wp-rewrite-feature.md, build step one): list + import + audit +
verdict + linked-draft export for posts already published on a
brand's WordPress site.

Hard invariants under test (spec §4/§8):
- the importer issues GET requests ONLY — the mock records every
  request, and no mutating request ever targets the original post
  id across the whole lifecycle;
- the imported draft's `original` block is byte-verbatim
  content.rendered and survives the whole flow untouched;
- export of an imported draft goes through the EXISTING exporter
  (F1 gate + F2 idempotency), creates a NEW post with the
  `[Viết lại] ` title prefix and a rewrite_of record, and is
  refused unless the verdict is `rewrite`;
- a superseded import is locked out of export.

The mock is a real local HTTP server seeded with full WP post
objects (the exporter mock in test_wordpress.py stores only what
creation receives). Requests without an Authorization header are
the public view: published posts only.
"""
from __future__ import annotations

import base64
import copy
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
from content_core import loaders  # noqa: E402
from content_core.loaders import save_brand_draft  # noqa: E402

BRAND = "moc-brew"
APP_PASSWORD = "abcd efgh ijkl mnop"
APP_PASSWORD_STRIPPED = "abcdefghijklmnop"
USERNAME = "editor1"

# ---------------------------------------------------------------------------
# Seeded posts (WP REST shape)
# ---------------------------------------------------------------------------

CLEAN_HTML = (
    '<p>Mộc Brew rang cà phê vào thứ Ba hàng tuần cho bạn. '
    'Một gói 250g có giá 185.000đ trước khi nói tới hương vị.</p>\n'
    '<h2>Hạt và nguồn gốc</h2>\n'
    '<p>Hạt Arabica Cầu Đất cho vị chua nhẹ và hậu ngọt rõ. '
    'Hạt Robusta Đắk Lắk cho body dày và vị đậm.</p>\n'
    '<ul><li>Rang vào thứ Ba hàng tuần</li>'
    '<li>Đóng gói ngay sau khi rang</li></ul>\n'
    '<p>Xem thêm <a href="https://mocbrew.example/bang-gia">'
    'bảng giá chi tiết</a> của tiệm.</p>\n'
    '<h2>Cách pha</h2>\n'
    '<figure><img class="wp-image-556" '
    'src="https://wp.test/wp-content/uploads/pha-phin.jpg" '
    'alt="Phin cà phê đang nhỏ giọt" />'
    '<figcaption>Phin pha chậm cho vị rõ.</figcaption></figure>\n'
    '<p>Mình ghi rõ ngày rang trên từng gói gửi tới tay bạn. '
    'Bạn pha phin hoặc V60 đều cho vị rõ và sạch.</p>'
)

STALE_HTML = (
    '<p>Mộc Brew là cà phê tốt nhất Việt Nam theo lời tiệm. '
    'Một gói 250g chỉ còn 150.000đ trong tuần này.</p>\n'
    '<h2>Giá bán</h2>\n'
    '<p>Giá cũ 150.000đ áp dụng cho mọi đơn trong tuần này.</p>'
)

RAW_HTML = (
    '<p>Bài có bảng giá và shortcode không chuyển được.</p>\n'
    '<table><tr><td>Gói 250g</td><td>185.000đ</td></tr>'
    '<tr><td>Gói 500g</td><td>359.000đ</td></tr></table>\n'
    '<p>[gallery ids="1,2,3"]</p>\n'
    '<p>Phần chữ thường vẫn vào section như mọi bài.</p>'
)


def _post(post_id, title, content_html, status="publish",
          featured_media=0, categories=None, protected=False):
    return {
        "id": post_id, "status": status, "slug": f"bai-{post_id}",
        "date": "2026-11-15T08:00:00", "modified": "2026-11-16T09:30:00",
        "link": f"https://wp.test/?p={post_id}",
        "title": {"rendered": title},
        "content": {"rendered": content_html, "protected": protected},
        "excerpt": {"rendered": ""},
        "featured_media": featured_media,
        "categories": categories or [], "tags": [9],
    }


MEDIA_556 = {"id": 556,
             "source_url": "https://wp.test/wp-content/uploads/pha-phin.jpg",
             "alt_text": "Phin cà phê đang nhỏ giọt"}

SEED_POSTS = {
    9001: _post(9001, "Cách làm khô cá lóc chuẩn nhà làm", CLEAN_HTML,
                featured_media=556, categories=[4]),
    9002: _post(9002, "Bài giá cũ", STALE_HTML),
    9003: _post(9003, "Bài có bảng và shortcode", RAW_HTML),
    9004: _post(9004, "Bài nháp trên WordPress",
                "<p>Nội dung nháp chưa đăng.</p>", status="draft"),
    9005: _post(9005, "Bài đặt mật khẩu", "", protected=True),
    9006: _post(9006, "Bài chỉ có ảnh",
                '<p><img class="wp-image-557" '
                'src="https://wp.test/wp-content/uploads/chi-anh.jpg" '
                'alt="Ảnh minh họa" /></p>'),
    # Most of the text lives in a <button> — a construct the
    # converter drops — so coverage lands far under the floor.
    9007: _post(9007, "Bài chữ nằm trong nút bấm",
                '<p>Mở bài rất ngắn.</p>\n'
                '<button>Đặt mua gói cà phê rang thứ Ba — gói 250g '
                'giá 185.000đ, gói 500g giá 359.000đ, giao trong '
                'hai tới ba ngày trên toàn quốc, rang mới mỗi '
                'tuần</button>'),
}


# ---------------------------------------------------------------------------
# Mock WP REST server
# ---------------------------------------------------------------------------

class _MockWP:
    def __init__(self):
        self.requests: list[dict] = []
        self.posts = copy.deepcopy(SEED_POSTS)
        self.media = {556: dict(MEDIA_556)}
        self.next_post_id = 9900
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _record(self, body: bytes):
                outer.requests.append({
                    "method": self.command, "path": self.path,
                    "headers": dict(self.headers), "body": body})

            def _send_json(self, code: int, obj):
                raw = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _authed(self) -> bool:
                auth = self.headers.get("Authorization", "")
                if not auth.startswith("Basic "):
                    return False
                try:
                    decoded = base64.b64decode(
                        auth[len("Basic "):]).decode("utf-8")
                except Exception:
                    return False
                return decoded == f"{USERNAME}:{APP_PASSWORD_STRIPPED}"

            def do_GET(self):
                self._record(b"")
                parsed = urllib.parse.urlparse(self.path)
                authed = self._authed()
                if parsed.path == "/wp-json/wp/v2/posts":
                    qs = urllib.parse.parse_qs(parsed.query)
                    slug = (qs.get("slug") or [""])[0]
                    status = (qs.get("status") or [""])[0]
                    out = []
                    for post in outer.posts.values():
                        if slug and post.get("slug") != slug:
                            continue
                        if not authed and post["status"] != "publish":
                            continue
                        if authed and status and \
                                post["status"] not in status.split(","):
                            continue
                        out.append(post)
                    self._send_json(200, out)
                    return
                prefix = "/wp-json/wp/v2/posts/"
                if parsed.path.startswith(prefix):
                    try:
                        post_id = int(parsed.path[len(prefix):])
                    except ValueError:
                        post_id = -1
                    post = outer.posts.get(post_id)
                    if post is None or (not authed
                                        and post["status"] != "publish"):
                        self._send_json(404, {
                            "code": "rest_post_invalid_id",
                            "message": "Invalid post ID."})
                        return
                    self._send_json(200, post)
                    return
                mprefix = "/wp-json/wp/v2/media/"
                if parsed.path.startswith(mprefix):
                    try:
                        media_id = int(parsed.path[len(mprefix):])
                    except ValueError:
                        media_id = -1
                    media = outer.media.get(media_id)
                    if media is None:
                        self._send_json(404, {
                            "code": "rest_post_invalid_id",
                            "message": "Invalid post ID."})
                        return
                    self._send_json(200, media)
                    return
                self._send_json(404, {"code": "rest_no_route",
                                      "message": "unknown path"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                self._record(body)
                if not self._authed():
                    self._send_json(401, {
                        "code": "rest_not_logged_in",
                        "message": "bad credentials"})
                    return
                if self.path == "/wp-json/wp/v2/posts":
                    payload = json.loads(body or b"{}")
                    post_id = outer.next_post_id
                    outer.next_post_id += 1
                    outer.posts[post_id] = {
                        "id": post_id, "status": payload.get("status"),
                        "slug": payload.get("slug", ""),
                        "title": {"rendered": payload.get("title", "")},
                        "content": {"rendered": payload.get("content", ""),
                                    "protected": False},
                        "link": f"https://wp.test/?p={post_id}"}
                    self._send_json(201, {
                        "id": post_id, "status": payload.get("status"),
                        "slug": payload.get("slug", ""),
                        "link": f"https://wp.test/?p={post_id}"})
                    return
                if self.path == "/wp-json/wp/v2/media":
                    self._send_json(201, {
                        "id": 881,
                        "source_url": "https://wp.test/wp-content/"
                                      "uploads/new.jpg"})
                    return
                if self.path.startswith("/wp-json/wp/v2/media/"):
                    payload = json.loads(body or b"{}")
                    self._send_json(200, {"id": int(
                        self.path.rsplit("/", 1)[-1]), **payload})
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

    def writes(self) -> list[dict]:
        return [r for r in self.requests
                if r["method"] in ("POST", "PUT", "PATCH", "DELETE")]

    def writes_to(self, post_id: int) -> list[dict]:
        return [r for r in self.writes()
                if f"/posts/{post_id}" in r["path"]]

    def created_posts(self) -> list[dict]:
        return [json.loads(r["body"]) for r in self.requests
                if r["method"] == "POST"
                and r["path"] == "/wp-json/wp/v2/posts"]


@pytest.fixture()
def mock_wp():
    mock = _MockWP()
    yield mock
    mock.stop()


@pytest.fixture(autouse=True)
def _isolated_stores(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_CORE_DRAFTS_DIR", str(tmp_path / "drafts"))
    monkeypatch.setenv("CONTENT_CORE_WP_TARGETS_DIR",
                       str(tmp_path / "wp_targets"))
    monkeypatch.setenv("CONTENT_CORE_WP_SECRETS_FILE",
                       str(tmp_path / "wordpress_secrets.json"))
    monkeypatch.setenv("CONTENT_CORE_BRAND_DRAFTS_DIR",
                       str(tmp_path / "brand_drafts"))
    monkeypatch.setenv("CONTENT_CORE_FACTS_PROFILES_DIR",
                       str(tmp_path / "facts"))
    monkeypatch.delenv("BRANDFORGE_WP_PASSWORD_MOC_BREW", raising=False)
    monkeypatch.delenv("BRANDFORGE_WP_PASSWORD_BRAND_R", raising=False)
    return tmp_path


def _configure(mock_wp, tmp_path, brand=BRAND, with_secrets=True):
    srv.set_wordpress_target(brand, mock_wp.base_url, USERNAME)
    if with_secrets:
        (tmp_path / "wordpress_secrets.json").write_text(
            json.dumps({brand: APP_PASSWORD}), encoding="utf-8")


def _load(draft_id: str) -> dict:
    return srv.get_draft(draft_id)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def test_list_posts_returns_published_with_fields(mock_wp, tmp_path):
    _configure(mock_wp, tmp_path)
    out = srv.list_wordpress_posts(BRAND)
    assert "error" not in out, out
    ids = {p["wp_post_id"] for p in out["posts"]}
    assert ids == {9001, 9002, 9003, 9005, 9006, 9007}
    post = next(p for p in out["posts"] if p["wp_post_id"] == 9001)
    assert post["title"] == "Cách làm khô cá lóc chuẩn nhà làm"
    assert post["status"] == "publish"
    assert post["date"] and post["modified"] and post["link"]
    assert all(r["method"] == "GET" for r in mock_wp.requests)


def test_list_posts_with_draft_status(mock_wp, tmp_path):
    _configure(mock_wp, tmp_path)
    out = srv.list_wordpress_posts(BRAND, status="draft")
    assert [p["wp_post_id"] for p in out["posts"]] == [9004]


def test_list_posts_without_credentials_sees_published_only(
        mock_wp, tmp_path):
    _configure(mock_wp, tmp_path, with_secrets=False)
    out = srv.list_wordpress_posts(BRAND, status="any")
    assert "error" not in out, out
    assert {p["status"] for p in out["posts"]} == {"publish"}
    assert all("Authorization" not in r["headers"]
               for r in mock_wp.requests)


def test_list_posts_no_target_is_structured_error(mock_wp, tmp_path):
    out = srv.list_wordpress_posts("brand-z")
    assert "error" in out and out["brand_id"] == "brand-z"


# ---------------------------------------------------------------------------
# Import + snapshot (US-1)
# ---------------------------------------------------------------------------

def test_import_creates_draft_with_verbatim_original(mock_wp, tmp_path):
    _configure(mock_wp, tmp_path)
    out = srv.import_wordpress_post(BRAND, 9001)
    assert "error" not in out, out
    draft = _load(out["draft_id"])
    assert draft["source"]["kind"] == "wp_import"
    assert draft["source"]["wp_post_id"] == 9001
    assert draft["source"]["import_round"] == 1
    # Byte-verbatim snapshot — the whole point of the feature.
    assert draft["original"]["content_html"] == CLEAN_HTML
    assert draft["original"]["title"] == \
        "Cách làm khô cá lóc chuẩn nhà làm"
    assert draft["original"]["featured_media"] == 556
    assert draft["original"]["categories"] == [4]
    assert draft["verdict"] is None
    assert draft["gate_history"] == []
    # Conversion: intro + two h2 sections; image extracted, not in
    # text; the link's anchor stays in text and the pair is kept.
    sections = draft["sections"]
    assert [s["heading"] for s in sections] == \
        ["", "Hạt và nguồn gốc", "Cách pha"]
    joined = "\n".join(s["text"] for s in sections)
    assert "bảng giá chi tiết" in joined
    assert "pha-phin.jpg" not in joined
    media = draft["original"]["media"]
    assert media and media[0]["wp_media_id"] == 556
    assert media[0]["alt"] == "Phin cà phê đang nhỏ giọt"
    links = [lnk for s in sections for lnk in s.get("links", [])]
    assert links == [{"anchor": "bảng giá chi tiết",
                      "href": "https://mocbrew.example/bang-gia"}]
    # Report: coverage at the spec's bar for a clean post.
    assert out["coverage"] >= 0.97, out
    assert out["sections"] == 3
    # Audit ran by default and did not touch the gate history.
    audit = draft["original_audit"]
    assert audit["fact_issues"] == []
    assert audit["brand_score"] > 0 and audit["craft_score"] > 0
    # Featured image enters the images state as existing_wp.
    manifest = srv.get_image_manifest(out["draft_id"])["manifest"]
    featured = [m for m in manifest if m["role"] == "featured"]
    assert featured and featured[0]["wp_media_id"] == 556
    # Importer wrote NOTHING to WordPress.
    assert mock_wp.writes() == []


def test_import_missing_post_is_error_and_creates_nothing(
        mock_wp, tmp_path):
    _configure(mock_wp, tmp_path)
    out = srv.import_wordpress_post(BRAND, 424242)
    assert "error" in out
    assert srv.list_drafts()["count"] == 0


def test_import_protected_post_refused(mock_wp, tmp_path):
    _configure(mock_wp, tmp_path)
    out = srv.import_wordpress_post(BRAND, 9005)
    assert "error" in out
    assert "protected" in out["error"] or "mật khẩu" in out["error"]
    assert srv.list_drafts()["count"] == 0


def test_import_no_text_post_flagged(mock_wp, tmp_path):
    _configure(mock_wp, tmp_path)
    out = srv.import_wordpress_post(BRAND, 9006)
    assert "error" not in out, out
    assert out["no_text"] is True
    draft = _load(out["draft_id"])
    assert all(not s["text"].strip() for s in draft["sections"])


def test_import_raw_blocks_kept_and_counted(mock_wp, tmp_path):
    _configure(mock_wp, tmp_path)
    out = srv.import_wordpress_post(BRAND, 9003)
    assert "error" not in out, out
    draft = _load(out["draft_id"])
    raw = draft["original"]["raw_blocks"]
    kinds = {r["note"] for r in raw}
    assert "table" in kinds and "shortcode" in kinds
    assert out["raw_blocks"] == len(raw) >= 2
    assert out["coverage"] >= 0.97, out
    joined = "\n".join(s["text"] for s in draft["sections"])
    assert "Phần chữ thường" in joined
    assert "Gói 500g" not in joined  # the table text is raw, not prose


# ---------------------------------------------------------------------------
# Duplicate rule (US-5)
# ---------------------------------------------------------------------------

def test_reimport_refused_then_force_supersedes(mock_wp, tmp_path):
    _configure(mock_wp, tmp_path)
    first = srv.import_wordpress_post(BRAND, 9001)
    again = srv.import_wordpress_post(BRAND, 9001)
    assert "error" in again
    assert again["draft_id"] == first["draft_id"]

    forced = srv.import_wordpress_post(BRAND, 9001, force=True)
    assert "error" not in forced, forced
    assert forced["draft_id"] != first["draft_id"]
    new = _load(forced["draft_id"])
    assert new["source"]["import_round"] == 2
    assert new["source"]["supersedes"] == first["draft_id"]
    old = _load(first["draft_id"])
    assert old["status"] == "superseded"
    assert old["superseded_by"] == forced["draft_id"]
    # The superseded draft is locked out of export.
    srv.set_rewrite_verdict(first["draft_id"], "rewrite")
    out = srv.export_draft_to_wordpress(BRAND, first["draft_id"])
    assert "error" in out
    assert mock_wp.created_posts() == []


# ---------------------------------------------------------------------------
# Audit (US-2)
# ---------------------------------------------------------------------------

def test_audit_flags_stale_price_and_prohibited_claim(mock_wp, tmp_path):
    _configure(mock_wp, tmp_path)
    out = srv.import_wordpress_post(BRAND, 9002)
    assert "error" not in out, out
    draft = _load(out["draft_id"])
    audit = draft["original_audit"]
    types = {i["type"] for i in audit["fact_issues"]}
    assert "wrong_price" in types
    assert any("150.000" in i["detail"] for i in audit["fact_issues"])
    # The claim's detail is the match-normalised phrase (the fact
    # checker's established output shape).
    assert any("tốt nhất" in c["detail"]
               for c in audit["prohibited_claims"])
    # The audit never dirties the rewrite's gate history.
    assert draft["gate_history"] == []


BRAND_R_DRAFT = {
    "brand": {"id": "brand-r", "name": "Brand R Test", "simulated": True,
              "version": 0, "review_status": "draft",
              "source_type": "own_site", "source_urls": []},
    "voice": {
        "traits": [], "pov": "first-person", "reader_address": "bạn",
        "sentence_rules": {"median_words_default": [11, 14],
                           "median_words_by_format": {},
                           "max_sentence_words": 25},
        "formatting_rules": [], "banned_terms": [],
        "prohibited_claims": [],
    },
    "vocabulary": {"use": [], "exact": [], "banned": [], "glossary": []},
    "examples": [{"format": "blog", "text": "Ví dụ mẫu của Brand R.",
                  "why_it_works": ""}],
    "anti_examples": [],
    "claims_and_guardrails": {"requires_evidence": [],
                              "prohibited_claims": [],
                              "unsupported_claim_marker": "[NEEDS SOURCE]"},
    "brand_kit": {}, "qualitative": {}, "review_notes": [],
}


def test_reaudit_follows_facts_updates(mock_wp, tmp_path):
    save_brand_draft(BRAND_R_DRAFT)
    srv.save_facts("brand-r", {
        "facts": {"price_vnd_per_250g": 50000, "pack_weight_g": 250,
                  "origins": ["Bến Tre"]},
        "entries": []})
    _configure(mock_wp, tmp_path, brand="brand-r")
    # Post 9002's text quotes 150.000đ for a 250g pack — against
    # brand-r's 50.000đ that is a wrong price; reprice the facts to
    # 150.000đ and the re-audit must clear it.
    out = srv.import_wordpress_post("brand-r", 9002)
    assert "error" not in out, out
    draft = _load(out["draft_id"])
    assert any(i["type"] == "wrong_price"
               for i in draft["original_audit"]["fact_issues"])
    srv.save_facts("brand-r", {
        "facts": {"price_vnd_per_250g": 150000, "pack_weight_g": 250,
                  "origins": ["Bến Tre"]},
        "entries": []})
    again = srv.audit_imported_post(out["draft_id"])
    assert "error" not in again, again
    draft = _load(out["draft_id"])
    assert not any(i["type"] == "wrong_price"
                   for i in draft["original_audit"]["fact_issues"])
    assert draft["gate_history"] == []


def test_audit_tool_refuses_non_imported_draft(mock_wp, tmp_path):
    _configure(mock_wp, tmp_path)
    draft_id = srv.start_draft({"id": "plain", "format": "blog",
                                "topic": "cà phê"})["draft_id"]
    out = srv.audit_imported_post(draft_id)
    assert "error" in out


# ---------------------------------------------------------------------------
# Verdicts (US-4)
# ---------------------------------------------------------------------------

def test_verdict_drop_blocks_export_without_any_wp_write(
        mock_wp, tmp_path):
    _configure(mock_wp, tmp_path)
    out = srv.import_wordpress_post(BRAND, 9002)
    draft_id = out["draft_id"]
    res = srv.set_rewrite_verdict(draft_id, "drop",
                                  reason="Giá cũ sai hết, bỏ bài.")
    assert "error" not in res, res
    draft = _load(draft_id)
    assert draft["verdict"]["verdict"] == "drop"
    assert draft["verdict"]["reason"] == "Giá cũ sai hết, bỏ bài."
    exp = srv.export_draft_to_wordpress(BRAND, draft_id)
    assert "error" in exp and "drop" in exp["error"]
    assert mock_wp.writes() == []


def test_verdict_validation(mock_wp, tmp_path):
    _configure(mock_wp, tmp_path)
    out = srv.import_wordpress_post(BRAND, 9001)
    bad = srv.set_rewrite_verdict(out["draft_id"], "maybe")
    assert "error" in bad
    plain = srv.start_draft({"id": "plain2", "format": "blog",
                             "topic": "cà phê"})["draft_id"]
    not_imported = srv.set_rewrite_verdict(plain, "rewrite")
    assert "error" in not_imported


def test_export_without_verdict_refused(mock_wp, tmp_path):
    _configure(mock_wp, tmp_path)
    out = srv.import_wordpress_post(BRAND, 9001)
    exp = srv.export_draft_to_wordpress(BRAND, out["draft_id"])
    assert "error" in exp
    assert mock_wp.writes() == []


# ---------------------------------------------------------------------------
# Full lifecycle: rewrite -> gate -> linked draft (US-3)
# ---------------------------------------------------------------------------

REWRITE_SECTIONS = [
    ("s1", "", "Mộc Brew rang cà phê vào thứ Ba hàng tuần cho bạn. "
               "Một gói 250g có giá 185.000đ."),
    ("s2", "Hạt và nguồn gốc",
     "Hạt Arabica Cầu Đất cho vị chua nhẹ và hậu ngọt rõ. "
     "Mình ghi rõ ngày rang trên từng gói."),
    ("s3", "Cách pha",
     "Bạn pha phin hoặc V60 đều cho vị rõ và sạch. "
     "Mình ghi rõ ngày rang trên từng gói gửi tới tay bạn."),
]


def test_rewrite_exports_linked_draft_and_original_untouched(
        mock_wp, tmp_path):
    _configure(mock_wp, tmp_path)
    out = srv.import_wordpress_post(BRAND, 9001)
    draft_id = out["draft_id"]
    snapshot = copy.deepcopy(_load(draft_id)["original"])
    srv.set_rewrite_verdict(draft_id, "rewrite")
    for sid, heading, text in REWRITE_SECTIONS:
        res = srv.save_section(draft_id, sid, heading, text)
        assert "error" not in res, res
    gate = srv.final_gate(draft_id)
    assert gate["passed"], gate

    exp = srv.export_draft_to_wordpress(BRAND, draft_id)
    assert "error" not in exp, exp
    created = mock_wp.created_posts()
    assert len(created) == 1
    payload = created[0]
    assert payload["status"] == "draft"
    assert payload["title"].startswith("[Viết lại] ")
    # The original featured media id is reused — nothing uploaded.
    assert payload["featured_media"] == 556
    assert not [r for r in mock_wp.requests
                if r["method"] == "POST"
                and r["path"] == "/wp-json/wp/v2/media"]
    # Categories inherited from the original (§12b decision 2);
    # tags are NOT inherited.
    assert payload["categories"] == [4]
    assert "tags" not in payload
    # The link back to the original is recorded.
    draft = _load(draft_id)
    record = draft["wordpress_export"]
    assert record["rewrite_of"]["wp_post_id"] == 9001
    assert record["rewrite_of"]["site_url"] == mock_wp.base_url
    assert exp["rewrite_of"]["wp_post_id"] == 9001
    # HARD INVARIANT: no mutating request ever targeted post 9001,
    # and the snapshot is deep-identical after the whole flow.
    assert mock_wp.writes_to(9001) == []
    assert draft["original"] == snapshot


def test_rewrite_export_reports_unrecovered_raw_blocks(
        mock_wp, tmp_path):
    _configure(mock_wp, tmp_path)
    out = srv.import_wordpress_post(BRAND, 9003)
    draft_id = out["draft_id"]
    srv.set_rewrite_verdict(draft_id, "rewrite")
    for sid, heading, text in REWRITE_SECTIONS:
        srv.save_section(draft_id, sid, heading, text)
    gate = srv.final_gate(draft_id)
    assert gate["passed"], gate
    exp = srv.export_draft_to_wordpress(BRAND, draft_id)
    assert "error" not in exp, exp
    notes = {r["note"] for r in exp["unrecovered_raw_blocks"]}
    assert notes == {"table", "shortcode"}


# ---------------------------------------------------------------------------
# Persisted conversion report: coverage / low_coverage / warnings
# live ON the draft (in `original`, next to the converter's other
# output), not only in the import call's return value — the admin
# panel reads drafts, not past call results. The stored
# `original_audit` carries coverage too, refreshed on every audit
# run from the stored snapshot.
# ---------------------------------------------------------------------------

def _stored(draft_id: str) -> dict:
    path = srv.drafts_dir() / f"{draft_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _rewrite_stored(draft_id: str, data: dict):
    path = srv.drafts_dir() / f"{draft_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def test_import_persists_coverage_fields_on_draft(mock_wp, tmp_path):
    _configure(mock_wp, tmp_path)
    out = srv.import_wordpress_post(BRAND, 9001)
    assert "error" not in out, out
    draft = _load(out["draft_id"])
    original = draft["original"]
    assert original["coverage"] == out["coverage"]
    assert original["low_coverage"] is False
    assert original["warnings"] == out["warnings"] == []
    audit = draft["original_audit"]
    assert audit["coverage"] == out["coverage"]
    assert audit["low_coverage"] is False


def test_import_persists_low_coverage_and_warnings(
        mock_wp, tmp_path):
    _configure(mock_wp, tmp_path)
    out = srv.import_wordpress_post(BRAND, 9007)
    assert "error" not in out, out
    assert out["low_coverage"] is True
    assert out["suggested_verdict"] == "audit_only"
    draft = _load(out["draft_id"])
    original = draft["original"]
    assert original["coverage"] == out["coverage"] < 0.97
    assert original["low_coverage"] is True
    assert original["warnings"] == out["warnings"]
    assert any("coverage" in w for w in original["warnings"])
    audit = draft["original_audit"]
    assert audit["coverage"] == out["coverage"]
    assert audit["low_coverage"] is True


def test_import_persists_no_text_warning(mock_wp, tmp_path):
    _configure(mock_wp, tmp_path)
    out = srv.import_wordpress_post(BRAND, 9006)
    assert "error" not in out, out
    draft = _load(out["draft_id"])
    assert draft["original"]["warnings"] == out["warnings"]
    assert any("no_text" in w for w in draft["original"]["warnings"])


def test_audit_imported_post_stores_coverage(mock_wp, tmp_path):
    _configure(mock_wp, tmp_path)
    out = srv.import_wordpress_post(BRAND, 9002)
    draft_id = out["draft_id"]
    # Simulate an audit stored before coverage was recorded in it.
    stored = _stored(draft_id)
    stored["original_audit"].pop("coverage", None)
    stored["original_audit"].pop("low_coverage", None)
    _rewrite_stored(draft_id, stored)
    again = srv.audit_imported_post(draft_id)
    assert "error" not in again, again
    assert again["original_audit"]["coverage"] == out["coverage"]
    assert again["original_audit"]["low_coverage"] is False
    draft = _load(draft_id)
    assert draft["original_audit"]["coverage"] == out["coverage"]


def test_pre_change_import_reads_back_without_fabricated_fields(
        mock_wp, tmp_path):
    _configure(mock_wp, tmp_path)
    out = srv.import_wordpress_post(BRAND, 9001)
    draft_id = out["draft_id"]
    # Simulate a draft imported before the fields were persisted:
    # strip them from the stored file.
    stored = _stored(draft_id)
    for key in ("coverage", "low_coverage", "warnings"):
        stored["original"].pop(key, None)
    _rewrite_stored(draft_id, stored)
    draft = _load(draft_id)
    # Reads are honest: absent stays absent, nothing is invented.
    assert "coverage" not in draft["original"]
    assert "low_coverage" not in draft["original"]
    assert "warnings" not in draft["original"]
    # A re-audit measures coverage from the stored snapshot and
    # records it in the audit — but never writes into the immutable
    # `original` block (only the import tool writes there).
    again = srv.audit_imported_post(draft_id)
    assert "error" not in again, again
    draft = _load(draft_id)
    assert draft["original_audit"]["coverage"] == out["coverage"]
    assert "coverage" not in draft["original"]
