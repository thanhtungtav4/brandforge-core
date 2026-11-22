"""WordPress draft exporter — the client half (P3, Part C).

Delivery decision (pmdecisions.md, 2026-11-22): finished articles
are delivered by creating a **draft** on the client's own WordPress
site. Never published — the client reviews and publishes. That rule
is structural here, not a setting:

- `build_post_payload` hard-codes status "draft" and ignores any
  status a caller passes;
- the target store (loaders.validate_wordpress_target) keeps only
  known non-secret fields, so a hand-edited target file cannot
  smuggle a status (or a credential) into an export;
- there is no publish code path in this module at all.

Credentials discipline: the Application Password is read ONLY from
(a) the environment variable BRANDFORGE_WP_PASSWORD_<BRAND>
(brand id upper-cased, non-alphanumerics folded to "_"), or
(b) a local secrets file — JSON {brand_id: "app password"} at
data/wordpress_secrets.json (override: CONTENT_CORE_WP_SECRETS_FILE;
the file is gitignored and matches the backup exporter's
credential-pattern exclusion). The password is never logged, never
stored, and every error this module raises is passed through
`redact` with the password (and its whitespace-stripped form, the
form actually sent on the wire) scrubbed out — a WP server that
echoes the Authorization header in an error body cannot leak it
through our messages.

The HTTP layer is stdlib urllib (the core stays dependency-light,
like fetch_sample.py). Requests time out at 30s. Every request
carries a mainstream browser User-Agent (`USER_AGENT`): urllib's
own "Python-urllib/x.y" signature is banned outright by some
Cloudflare configurations (Error 1010 "browser_signature_banned",
HTTP 403 — the first live export to kho.nttung.dev failed exactly
this way on 2026-11-22 while the same credentials via curl
succeeded).
"""
from __future__ import annotations

import base64
import html
import json
import mimetypes
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from . import loaders

_TIMEOUT_S = 30

# Sent on EVERY request (see the module docstring): a current
# mainstream Chrome on Windows string. Named + module-level so the
# tests can pin that what arrives at the server is exactly this.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/138.0.0.0 Safari/537.36")


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def secrets_file_path() -> Path:
    override = os.environ.get("CONTENT_CORE_WP_SECRETS_FILE")
    return Path(override) if override else loaders.DATA_DIR / \
        "wordpress_secrets.json"


def _env_var_name(brand_id: str) -> str:
    key = re.sub(r"[^A-Za-z0-9]", "_", str(brand_id)).upper()
    return f"BRANDFORGE_WP_PASSWORD_{key}"


def load_app_password(brand_id: str) -> str | None:
    """The brand's WP Application Password, or None when no
    credential is configured. Environment variable wins over the
    secrets file. The value is returned exactly as configured;
    whitespace is stripped at the wire (WP displays app passwords
    in spaced groups; WP itself ignores the spaces)."""
    from_env = os.environ.get(_env_var_name(brand_id))
    if from_env and from_env.strip():
        return from_env.strip()
    path = secrets_file_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if isinstance(data, dict):
            value = data.get(brand_id)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def credentials_configured(brand_id: str) -> bool:
    """Whether an Application Password is available for the brand —
    a boolean only; the value itself never leaves this module
    except onto the wire."""
    return load_app_password(brand_id) is not None


def not_configured_message(brand_id: str) -> str:
    return (
        f"WordPress credentials not configured for brand {brand_id!r}: "
        f"set the environment variable {_env_var_name(brand_id)} or add "
        f"the brand's Application Password to the local secrets file "
        f"{secrets_file_path().name} (see content_core/wordpress.py)")


def redact(text: str, *secrets: str | None) -> str:
    """Scrub every secret (and its whitespace-stripped twin) out of
    a message. Applied to EVERY error this module raises. Variants
    are replaced longest-first so a composite secret (e.g. a full
    "Basic <token>" header value) is scrubbed as a unit."""
    out = str(text)
    variants: set[str] = set()
    for secret in secrets:
        if not secret:
            continue
        variants.add(secret)
        variants.add("".join(secret.split()))
    for variant in sorted(variants, key=len, reverse=True):
        if variant:
            out = out.replace(variant, "***")
    return out


# ---------------------------------------------------------------------------
# REST client
# ---------------------------------------------------------------------------

class WPError(ValueError):
    """A WordPress export failure. The message is already redacted."""


def _auth_token(username: str, app_password: str) -> str:
    wire_password = "".join(app_password.split())
    return base64.b64encode(
        f"{username}:{wire_password}".encode("utf-8")).decode("ascii")


def _auth_header(username: str, app_password: str) -> str:
    return f"Basic {_auth_token(username, app_password)}"


def _request(target: dict, app_password: str, method: str, route: str,
             json_body: dict | None = None, raw_body: bytes | None = None,
             extra_headers: dict | None = None) -> dict:
    """One authenticated WP REST call. Returns the decoded JSON
    body. Any failure raises WPError with a redacted message naming
    the HTTP status and WP's own error message when it sent one."""
    url = f"{target['site_url']}/wp-json/wp/v2/{route}"
    auth = _auth_header(target["username"], app_password)
    # Everything that could carry the credential into an error body:
    # the password as configured, its wire (stripped) form, the
    # base64 token and the full header value a careless server
    # might echo back.
    secrets = (app_password, auth, _auth_token(target["username"],
                                               app_password))
    headers = {"Authorization": auth,
               "Accept": "application/json",
               "User-Agent": USER_AGENT}
    data = raw_body
    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if extra_headers:
        headers.update(extra_headers)
    request = urllib.request.Request(url, data=data, headers=headers,
                                     method=method)
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        detail = body
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict) and parsed.get("message"):
                detail = str(parsed["message"])
        except json.JSONDecodeError:
            pass
        raise WPError(redact(
            f"WordPress {method} {route} failed with HTTP "
            f"{exc.code}: {detail}", *secrets)) from None
    except urllib.error.URLError as exc:
        raise WPError(redact(
            f"WordPress {method} {route} could not be reached at "
            f"{target['site_url']}: {exc.reason}", *secrets)) from None
    try:
        parsed = json.loads(body) if body else {}
    except json.JSONDecodeError:
        raise WPError(redact(
            f"WordPress {method} {route} returned a non-JSON response",
            *secrets)) from None
    if not isinstance(parsed, dict):
        raise WPError(redact(
            f"WordPress {method} {route} returned an unexpected "
            f"response shape", *secrets))
    return parsed


def upload_media(target: dict, app_password: str, filename: str,
                 data: bytes) -> dict:
    """Upload one media file. Returns the WP media object
    ({id, source_url, ...})."""
    content_type = mimetypes.guess_type(filename)[0] \
        or "application/octet-stream"
    safe_name = filename.replace('"', "")
    return _request(
        target, app_password, "POST", "media", raw_body=data,
        extra_headers={
            "Content-Type": content_type,
            "Content-Disposition": f'attachment; filename="{safe_name}"'})


def update_media(target: dict, app_password: str, media_id: int,
                 fields: dict) -> dict:
    """Set metadata (alt_text / caption / description) on an
    uploaded media item."""
    return _request(target, app_password, "POST", f"media/{media_id}",
                    json_body=fields)


def create_post(target: dict, app_password: str, payload: dict) -> dict:
    """Create the post. The payload comes from build_post_payload,
    which forces status "draft" — this function adds nothing."""
    return _request(target, app_password, "POST",
                    target.get("post_type") or "posts",
                    json_body=payload)


# ---------------------------------------------------------------------------
# Payload + content builders (pure)
# ---------------------------------------------------------------------------

def build_post_payload(title: str, content_html: str,
                       status: str | None = None,
                       featured_media: int | None = None,
                       category_id: int | None = None) -> dict:
    """The WP post payload. `status` is accepted so callers can
    pass whatever they hold — and IGNORED: the payload always
    carries "draft" (the delivery decision is not a parameter)."""
    payload: dict = {"title": title, "content": content_html,
                     "status": "draft"}
    if featured_media:
        payload["featured_media"] = featured_media
    if category_id:
        payload["categories"] = [category_id]
    return payload


def _paragraphs(text: str) -> str:
    out = []
    for block in re.split(r"\n\s*\n", str(text or "")):
        block = block.strip()
        if block:
            out.append(f"<p>{html.escape(block)}</p>")
    return "\n".join(out)


def build_content_html(sections: list[dict],
                       media_by_section: dict[str, dict],
                       featured: dict | None = None) -> str:
    """Assemble the post body from draft sections + uploaded media.

    media_by_section maps section_id -> the manifest entry enriched
    with its WP source_url. An in-content image is placed at the
    START of its section (before the heading), as a <figure> when
    it has a caption, else a bare <img>. The featured image is NOT
    in the body — WordPress renders it from featured_media."""
    parts: list[str] = []
    for section in sections:
        section_id = section.get("id") or section.get("section_id")
        media = media_by_section.get(str(section_id))
        if media:
            img = (f'<img src="{html.escape(media["source_url"], quote=True)}" '
                   f'alt="{html.escape(media.get("alt_text", ""), quote=True)}" />')
            caption = str(media.get("caption") or "").strip()
            if caption:
                parts.append(
                    f"<figure>{img}<figcaption>"
                    f"{html.escape(caption)}</figcaption></figure>")
            else:
                parts.append(img)
        heading = str(section.get("heading") or "").strip()
        if heading:
            parts.append(f"<h2>{html.escape(heading)}</h2>")
        parts.append(_paragraphs(section.get("text", "")))
    return "\n".join(p for p in parts if p)


def edit_link(site_url: str, post_id: int) -> str:
    """The wp-admin edit URL for a post — the link the client (and
    Smith) actually clicks to review the draft."""
    return f"{site_url}/wp-admin/post.php?post={post_id}&action=edit"
