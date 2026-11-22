"""Tests for the WordPress draft exporter (P3, Part C).

House discipline: fail-first — these tests were written before
content_core/wordpress.py and the three MCP tools existed.

The mock is a REAL local HTTP server (http.server on an ephemeral
port) implementing the slice of the WP REST API the exporter uses:
POST /wp-json/wp/v2/media, POST /wp-json/wp/v2/media/<id>,
POST /wp-json/wp/v2/posts. It records every request so tests can
assert on the auth header shape, the uploaded bytes, the alt-text
update and the post payload — and it can be told to fail (401 with
the credential echoed in the body, to prove error redaction; 404 on
media) the way a real misconfigured site fails.

Standing rule under test (pmdecisions 2026-11-22): the created post
is ALWAYS a draft. No code path — not a hand-edited target file, not
a payload argument — may send "publish".
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
from content_core import wordpress as wp  # noqa: E402

BRAND = "moc-brew"
APP_PASSWORD = "abcd efgh ijkl mnop"          # as WP shows it (spaced)
APP_PASSWORD_STRIPPED = "abcdefghijklmnop"    # as it goes on the wire
USERNAME = "editor1"

BRIEF = {"id": "wp-export-test", "format": "blog",
         "topic": "cà phê Mộc Brew cho văn phòng",
         "keyword": "cà phê",
         "title": "Bài kiểm tra xuất bản nháp"}
SECTIONS = [
    {"id": "s1", "heading": "Mở đầu", "intent": "intro",
     "text": "Mộc Brew rang cà phê vào thứ Ba hàng tuần cho bạn. "
             "Một gói 250g có giá 185.000đ."},
    {"id": "s2", "heading": "Hương vị", "intent": "body",
     "text": "Hạt Arabica Cầu Đất cho vị chua nhẹ và hậu ngọt rõ. "
             "Mình ghi rõ ngày rang trên từng gói."},
]


# ---------------------------------------------------------------------------
# Mock WP REST server
# ---------------------------------------------------------------------------

class _MockWP:
    def __init__(self):
        self.requests: list[dict] = []
        self.fail_auth = False
        self.media_404 = False
        self.posts_404 = False
        self.next_media_id = 101
        self.next_post_id = 4242
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output clean
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
                if outer.fail_auth:
                    return False
                auth = self.headers.get("Authorization", "")
                if not auth.startswith("Basic "):
                    return False
                decoded = base64.b64decode(
                    auth[len("Basic "):]).decode("utf-8")
                return decoded == f"{USERNAME}:{APP_PASSWORD_STRIPPED}"

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                self._record(body)
                if not self._auth_ok():
                    # A careless real server: echoes the credential
                    # back in the error body. The exporter must never
                    # let it reach its own error message.
                    self._send_json(401, {
                        "code": "rest_not_logged_in",
                        "message": f"bad credentials: "
                                   f"{self.headers.get('Authorization')}"})
                    return
                if self.path == "/wp-json/wp/v2/media":
                    if outer.media_404:
                        self._send_json(404, {
                            "code": "rest_no_route",
                            "message": "No route was found matching "
                                       "the URL and request method."})
                        return
                    media_id = outer.next_media_id
                    outer.next_media_id += 1
                    disp = self.headers.get("Content-Disposition", "")
                    filename = disp.split("filename=")[-1].strip('"') \
                        if "filename=" in disp else "upload"
                    self._send_json(201, {
                        "id": media_id,
                        "source_url": f"http://wp.test/wp-content/"
                                      f"uploads/{filename}",
                        "alt_text": ""})
                    return
                if self.path.startswith("/wp-json/wp/v2/media/"):
                    payload = json.loads(body or b"{}")
                    self._send_json(200, {"id": int(
                        self.path.rsplit("/", 1)[-1]), **payload})
                    return
                if self.path == "/wp-json/wp/v2/posts":
                    if outer.posts_404:
                        self._send_json(404, {
                            "code": "rest_no_route",
                            "message": "No route was found matching "
                                       "the URL and request method."})
                        return
                    payload = json.loads(body or b"{}")
                    post_id = outer.next_post_id
                    outer.next_post_id += 1
                    self._send_json(201, {
                        "id": post_id,
                        "status": payload.get("status"),
                        "link": f"http://wp.test/?p={post_id}"})
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
                if r["path"] == "/wp-json/wp/v2/posts"]

    def media_uploads(self) -> list[dict]:
        return [r for r in self.requests
                if r["path"] == "/wp-json/wp/v2/media"]

    def media_updates(self) -> list[dict]:
        return [json.loads(r["body"]) for r in self.requests
                if r["path"].startswith("/wp-json/wp/v2/media/")]


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
    monkeypatch.delenv("BRANDFORGE_WP_PASSWORD_MOC_BREW", raising=False)
    return tmp_path


def _write_secrets(tmp_path, password: str = APP_PASSWORD):
    (tmp_path / "wordpress_secrets.json").write_text(
        json.dumps({BRAND: password}), encoding="utf-8")


def _draft_with_images(tmp_path) -> tuple[str, dict]:
    """A stored draft with a fulfilled featured + one in-content
    image whose refs are REAL files with known bytes."""
    draft_id = srv.start_draft(BRIEF)["draft_id"]
    for sec in SECTIONS:
        srv.save_section(draft_id, sec["id"], sec["heading"], sec["text"],
                         intent=sec["intent"], target_words=120)
    srv.plan_images(draft_id)
    featured = tmp_path / "featured-src.webp"
    featured.write_bytes(b"FEATURED-BYTES")
    incontent = tmp_path / "incontent-src.webp"
    incontent.write_bytes(b"INCONTENT-BYTES")
    srv.set_image(draft_id, "img-featured", "client", str(featured),
                  alt="Gói cà phê Mộc Brew 250g trên bàn gỗ",
                  caption="Gói 250g cạnh phin pha",
                  description="Ảnh bìa gói cà phê Mộc Brew")
    srv.set_image(draft_id, "img-s1", "client", str(incontent),
                  alt="Hạt cà phê Arabica Cầu Đất vừa rang",
                  caption="", description="Hạt Arabica sau khi rang")
    manifest = srv.get_image_manifest(draft_id)
    # The plan has a third slot (img-s2) deliberately left
    # unfulfilled: the manifest carries the 2 fulfilled images and
    # the exporter must warn about the missing slot, not drop it.
    assert len(manifest["manifest"]) == 2, manifest
    return draft_id, manifest


# ---------------------------------------------------------------------------
# Target store: set / get / validation / no secrets in the store
# ---------------------------------------------------------------------------

def test_set_and_get_target_normalises_and_stores_no_secret(
        _isolated_stores, mock_wp):
    tmp_path = _isolated_stores
    out = srv.set_wordpress_target(BRAND, mock_wp.base_url + "/",
                                   USERNAME)
    assert "error" not in out, out
    assert out["site_url"] == mock_wp.base_url  # trailing slash gone
    assert out["username"] == USERNAME
    assert out["credentials_configured"] is False

    got = srv.get_wordpress_target(BRAND)
    assert got["site_url"] == mock_wp.base_url
    assert got["post_type"] == "posts"

    stored = json.loads(
        (tmp_path / "wp_targets" / "moc-brew.json").read_text(
            encoding="utf-8"))
    assert not any("password" in k or "secret" in k or "token" in k
                   for k in stored), stored


def test_get_target_unknown_brand_errors():
    out = srv.get_wordpress_target("no-such-brand")
    assert "error" in out


@pytest.mark.parametrize("bad", ["notaurl", "ftp://example.com",
                                 "https://", "example.com/blog"])
def test_set_target_rejects_malformed_urls(_isolated_stores, bad):
    out = srv.set_wordpress_target(BRAND, bad, USERNAME)
    assert "error" in out, out
    assert srv.get_wordpress_target(BRAND).get("error")


def test_credentials_configured_flag_flips_with_secrets_file(
        _isolated_stores, mock_wp):
    tmp_path = _isolated_stores
    srv.set_wordpress_target(BRAND, mock_wp.base_url, USERNAME)
    assert srv.get_wordpress_target(
        BRAND)["credentials_configured"] is False
    _write_secrets(tmp_path)
    got = srv.get_wordpress_target(BRAND)
    assert got["credentials_configured"] is True
    assert APP_PASSWORD not in json.dumps(got)  # never leaks the value


# ---------------------------------------------------------------------------
# Export: configuration + credential handling
# ---------------------------------------------------------------------------

def test_export_without_credentials_is_a_clear_not_configured_error(
        _isolated_stores, mock_wp):
    tmp_path = _isolated_stores
    draft_id, _ = _draft_with_images(tmp_path)
    srv.set_wordpress_target(BRAND, mock_wp.base_url, USERNAME)
    out = srv.export_draft_to_wordpress(BRAND, draft_id)
    assert "error" in out and "not configured" in out["error"], out
    assert mock_wp.requests == []  # nothing was even attempted


def test_export_without_target_errors(_isolated_stores):
    tmp_path = _isolated_stores
    draft_id, _ = _draft_with_images(tmp_path)
    out = srv.export_draft_to_wordpress(BRAND, draft_id)
    assert "error" in out


def test_export_brand_mismatch_errors(_isolated_stores, mock_wp):
    tmp_path = _isolated_stores
    draft_id, _ = _draft_with_images(tmp_path)  # a moc-brew draft
    srv.set_wordpress_target("cao-nguyen-moc", mock_wp.base_url, USERNAME)
    _write_secrets(tmp_path)
    out = srv.export_draft_to_wordpress("cao-nguyen-moc", draft_id)
    assert "error" in out
    assert mock_wp.requests == []


def test_env_var_credential_works(_isolated_stores, mock_wp, monkeypatch):
    tmp_path = _isolated_stores
    draft_id, _ = _draft_with_images(tmp_path)
    srv.set_wordpress_target(BRAND, mock_wp.base_url, USERNAME)
    monkeypatch.setenv("BRANDFORGE_WP_PASSWORD_MOC_BREW", APP_PASSWORD)
    out = srv.export_draft_to_wordpress(BRAND, draft_id)
    assert "error" not in out, out
    assert out["wp_post_id"] == 4242


# ---------------------------------------------------------------------------
# Export: the happy path against the mock WP server
# ---------------------------------------------------------------------------

def test_export_happy_path_uploads_media_and_creates_draft(
        _isolated_stores, mock_wp):
    tmp_path = _isolated_stores
    draft_id, manifest = _draft_with_images(tmp_path)
    srv.set_wordpress_target(BRAND, mock_wp.base_url, USERNAME)
    _write_secrets(tmp_path)

    out = srv.export_draft_to_wordpress(BRAND, draft_id)
    assert "error" not in out, out
    assert out["wp_post_id"] == 4242
    assert "post.php?post=4242&action=edit" in out["edit_link"]
    assert out["status"] == "draft"

    # --- media uploads: bytes + filename + auth shape
    uploads = mock_wp.media_uploads()
    assert len(uploads) == 2
    bodies = sorted(r["body"] for r in uploads)
    assert bodies == [b"FEATURED-BYTES", b"INCONTENT-BYTES"]
    for r in uploads:
        expected = "Basic " + base64.b64encode(
            f"{USERNAME}:{APP_PASSWORD_STRIPPED}".encode()).decode()
        assert r["headers"]["Authorization"] == expected
        assert "filename=" in r["headers"]["Content-Disposition"]

    # --- alt text (and caption/description) set on the media items
    updates = mock_wp.media_updates()
    alts = {u["alt_text"] for u in updates}
    assert alts == {"Gói cà phê Mộc Brew 250g trên bàn gỗ",
                    "Hạt cà phê Arabica Cầu Đất vừa rang"}

    # --- the post: ALWAYS a draft, featured media wired, image in body
    payloads = mock_wp.posts_payloads()
    assert len(payloads) == 1
    post = payloads[0]
    assert post["status"] == "draft"
    assert post["title"] == BRIEF["title"]
    # featured media id = the id the mock gave the featured upload:
    # uploads happen in manifest order (featured first)
    assert post["featured_media"] == 101
    assert "wp-content/uploads/" in post["content"]
    assert "Hương vị" in post["content"]

    # --- the export is recorded on the draft
    draft = srv.get_draft(draft_id)
    record = draft["wordpress_export"]
    assert record["wp_post_id"] == 4242
    assert record["site_url"] == mock_wp.base_url
    assert len(record["media"]) == 2


def test_export_duplicate_refused_unless_forced(
        _isolated_stores, mock_wp):
    tmp_path = _isolated_stores
    draft_id, _ = _draft_with_images(tmp_path)
    srv.set_wordpress_target(BRAND, mock_wp.base_url, USERNAME)
    _write_secrets(tmp_path)

    first = srv.export_draft_to_wordpress(BRAND, draft_id)
    assert "error" not in first, first
    second = srv.export_draft_to_wordpress(BRAND, draft_id)
    assert "error" in second and "already exported" in second["error"]
    assert len(mock_wp.posts_payloads()) == 1

    forced = srv.export_draft_to_wordpress(BRAND, draft_id, force=True)
    assert "error" not in forced, forced
    assert forced["wp_post_id"] == 4243
    assert srv.get_draft(draft_id)["wordpress_export"]["wp_post_id"] == 4243


# ---------------------------------------------------------------------------
# Draft-only pin: nothing can make the exporter publish
# ---------------------------------------------------------------------------

def test_payload_builder_forces_draft():
    payload = wp.build_post_payload(
        title="T", content_html="<p>x</p>", status="publish",
        featured_media=7)
    assert payload["status"] == "draft"


def test_hand_edited_target_cannot_make_it_publish(
        _isolated_stores, mock_wp):
    tmp_path = _isolated_stores
    draft_id, _ = _draft_with_images(tmp_path)
    srv.set_wordpress_target(BRAND, mock_wp.base_url, USERNAME)
    # An operator hand-edits the stored target and plants a status:
    target_path = tmp_path / "wp_targets" / "moc-brew.json"
    stored = json.loads(target_path.read_text(encoding="utf-8"))
    stored["status"] = "publish"
    stored["default_status"] = "publish"
    target_path.write_text(json.dumps(stored), encoding="utf-8")
    _write_secrets(tmp_path)

    out = srv.export_draft_to_wordpress(BRAND, draft_id)
    assert "error" not in out, out
    assert mock_wp.posts_payloads()[0]["status"] == "draft"


# ---------------------------------------------------------------------------
# Error handling: redaction, WP failures, missing files
# ---------------------------------------------------------------------------

def test_auth_failure_error_never_contains_the_password(
        _isolated_stores, mock_wp):
    tmp_path = _isolated_stores
    draft_id, _ = _draft_with_images(tmp_path)
    srv.set_wordpress_target(BRAND, mock_wp.base_url, USERNAME)
    _write_secrets(tmp_path)
    mock_wp.fail_auth = True  # 401, body echoes the Authorization header

    out = srv.export_draft_to_wordpress(BRAND, draft_id)
    assert "error" in out, out
    assert "401" in out["error"]
    assert APP_PASSWORD not in out["error"]
    assert APP_PASSWORD_STRIPPED not in out["error"]
    assert "Basic" not in out["error"]


def test_media_404_surfaces_status_and_creates_no_post(
        _isolated_stores, mock_wp):
    tmp_path = _isolated_stores
    draft_id, _ = _draft_with_images(tmp_path)
    srv.set_wordpress_target(BRAND, mock_wp.base_url, USERNAME)
    _write_secrets(tmp_path)
    mock_wp.media_404 = True

    out = srv.export_draft_to_wordpress(BRAND, draft_id)
    assert "error" in out and "404" in out["error"], out
    assert mock_wp.posts_payloads() == []
    assert "wordpress_export" not in srv.get_draft(draft_id)


def test_missing_image_file_is_a_clear_error(_isolated_stores, mock_wp):
    tmp_path = _isolated_stores
    draft_id, _ = _draft_with_images(tmp_path)
    (tmp_path / "featured-src.webp").unlink()
    srv.set_wordpress_target(BRAND, mock_wp.base_url, USERNAME)
    _write_secrets(tmp_path)

    out = srv.export_draft_to_wordpress(BRAND, draft_id)
    assert "error" in out and "featured-src.webp" in out["error"], out
    assert mock_wp.requests == []


def test_export_text_only_draft_without_image_plan(
        _isolated_stores, mock_wp):
    tmp_path = _isolated_stores
    draft_id = srv.start_draft(BRIEF)["draft_id"]
    for sec in SECTIONS:
        srv.save_section(draft_id, sec["id"], sec["heading"], sec["text"],
                         intent=sec["intent"], target_words=120)
    srv.set_wordpress_target(BRAND, mock_wp.base_url, USERNAME)
    _write_secrets(tmp_path)

    out = srv.export_draft_to_wordpress(BRAND, draft_id)
    assert "error" not in out, out
    assert out["media_uploaded"] == 0
    assert mock_wp.posts_payloads()[0]["status"] == "draft"
