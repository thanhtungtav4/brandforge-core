#!/usr/bin/env python3
"""Fetch a whole SITE for the Brand Builder — a CLI helper, NOT part of the
MCP server (the server stays offline and deterministic by design).

Usage:
    .venv/bin/python fetch_site.py <root_url> [--max-pages 8]

Fetches the homepage, discovers same-domain internal links (paths matching
the priority list below are fetched first), and prints ONE JSON object:
{root, pages: [{url, title, text, word_count, js_shell_suspected,
truncated, headings, html_meta}], js_shell_suspected,
sitemap_fallback_used, sitemap_truncated}.
Sitemap URLs (robots.txt / sitemap.xml, indexes included) are ALWAYS
merged with link discovery — a JS-app shell homepage exposes no links
at all (calibration F1), and on healthy sites the sitemap still lists
orphan pages no page links to (evaluation R6-S3).

`text` is the same clean plain text fetch_sample.py produces (its
strip_html is reused). `html_meta` carries the visual brand-kit evidence:
og_site_name, og_image, theme_color, logo_url (best guess), fonts and
colors mined from <style> blocks, inline style attributes and Google
Fonts links. Only the stdlib is used; file:// URLs work, which is how the
tests exercise this without a network.
"""
from __future__ import annotations

import gzip
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from html.parser import HTMLParser

from fetch_sample import _UA, decode_body, detect_js_shell, strip_html

#: Internal paths containing one of these are fetched first (brand-relevant
#: pages before generic ones). Order = priority.
PRIORITY_PATH_KEYWORDS = ["gioi-thieu", "about", "san-pham", "product",
                          "dich-vu", "service", "blog", "tin-tuc",
                          "lien-he", "contact"]

_SKIP_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".svg", ".ico",
                    ".css", ".js", ".pdf", ".zip", ".webp", ".mp4")


class _SiteParser(HTMLParser):
    """One pass over a page: meta/kit evidence, headings, anchor hrefs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.links: list[str] = []          # <a href> in document order
        self.font_links: list[str] = []     # Google Fonts hrefs
        self.headings: list[str] = []
        self.logo_src: str | None = None        # strong logo signal
        self.logo_weak_src: str | None = None   # header-context alt signal
        self.style_chunks: list[str] = []
        self.inline_styles: list[str] = []
        self._in_style = False
        self._header_depth = 0
        self._heading_tag: str | None = None
        self._heading_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        a = dict(attrs)
        if tag == "meta":
            key = a.get("property") or a.get("name")
            if key and a.get("content"):
                self.meta[key.strip().lower()] = a["content"].strip()
        elif tag == "link":
            href = a.get("href", "")
            if "fonts.googleapis.com" in href:
                self.font_links.append(href)
        elif tag == "a":
            if a.get("href"):
                self.links.append(a["href"])
        elif tag == "header":
            self._header_depth += 1
        elif tag == "img":
            self._note_img(a)
        elif tag == "style":
            self._in_style = True
        elif tag in ("h1", "h2", "h3"):
            self._heading_tag = tag
            self._heading_parts = []
        if a.get("style"):
            self.inline_styles.append(a["style"])

    def _note_img(self, a: dict) -> None:
        """Logo evidence (calibration F4). STRONG signal: 'logo' in the
        image's own src path or in its class/id — the asset identifies
        itself. WEAK signal: 'logo' in the alt text while the image sits
        inside a <header> (brand context). Alt text ALONE is not a
        signal: the calibration site's winning 'logo' was a blog-card
        thumbnail whose alt *described* a logo ('Logo Cao Nguyên Mộc
        với dòng chữ...'). When unsure, no logo is recorded (null)."""
        src = a.get("src", "")
        if not src:
            return
        strong = ("logo" in src.lower()
                  or "logo" in a.get("class", "").lower()
                  or "logo" in a.get("id", "").lower())
        if strong:
            if self.logo_src is None:
                self.logo_src = src
        elif (self._header_depth > 0 and "logo" in a.get("alt", "").lower()
              and self.logo_weak_src is None):
            self.logo_weak_src = src

    @property
    def best_logo_src(self) -> str | None:
        return self.logo_src or self.logo_weak_src

    def handle_endtag(self, tag: str) -> None:
        if tag == "style":
            self._in_style = False
        elif tag == "header" and self._header_depth:
            self._header_depth -= 1
        elif tag == self._heading_tag and tag in ("h1", "h2", "h3"):
            text = " ".join("".join(self._heading_parts).split())
            if text:
                self.headings.append(text)
            self._heading_tag = None

    def handle_data(self, data: str) -> None:
        if self._in_style:
            self.style_chunks.append(data)
        elif self._heading_tag:
            self._heading_parts.append(data)


_HEX_RE = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
_FONT_FAMILY_RE = re.compile(r"font-family\s*:\s*([^;}]+)", re.IGNORECASE)


def _normalize_hex(raw: str) -> str:
    h = raw.lower()
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return f"#{h}"


#: Generic families / CSS-wide keywords: valid font-family values that
#: carry no brand signal (calibration F5 also filters var(--…) below).
_GENERIC_FAMILIES = {"inherit", "initial", "unset", "revert", "sans-serif",
                     "serif", "monospace", "cursive", "fantasy", "system-ui",
                     "ui-sans-serif", "ui-serif", "ui-monospace",
                     "ui-rounded", "-apple-system", "blinkmacsystemfont",
                     "emoji", "math", "fangsong"}


def _is_brand_family(name: str) -> bool:
    """A font-family value counts as brand evidence only when it names an
    actual family: CSS variable references (var(--display) — the
    calibration kit's #1 'font') and generic families are not fonts."""
    low = name.strip().lower()
    return bool(low) and "var(" not in low and low not in _GENERIC_FAMILIES


def _font_families(style_text: str, font_links: list[str]) -> list[dict]:
    """font-family first choices from style text + Google Fonts families,
    as [{family, count}] sorted by count desc, then name."""
    counts: Counter = Counter()
    for m in _FONT_FAMILY_RE.finditer(style_text):
        first = m.group(1).split(",")[0].strip().strip("'\"")
        if _is_brand_family(first):
            counts[first] += 1
        elif first:
            counts[first] += 0  # generic/var declarations carry no brand signal
    for href in font_links:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        for fam in query.get("family", []):
            for part in fam.split("|"):
                name = part.split(":")[0].replace("+", " ").strip()
                if name:
                    counts[name] += 1
    return [{"family": name, "count": n}
            for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])) if n > 0]


def extract_html_meta(url: str, parser: _SiteParser) -> dict:
    """Build the html_meta dict for one parsed page."""
    style_text = "\n".join(parser.style_chunks + parser.inline_styles)
    colors = Counter(_normalize_hex(m.group(1)) for m in _HEX_RE.finditer(style_text))
    return {
        "og_site_name": parser.meta.get("og:site_name") or None,
        "og_image": (urllib.parse.urljoin(url, parser.meta["og:image"])
                     if parser.meta.get("og:image") else None),
        "theme_color": (parser.meta.get("theme-color") or "").lower() or None,
        "logo_url": (urllib.parse.urljoin(url, parser.best_logo_src)
                     if parser.best_logo_src else None),
        "fonts": _font_families(style_text, parser.font_links),
        "colors": [{"hex": h, "count": n}
                   for h, n in sorted(colors.items(), key=lambda kv: (-kv[1], kv[0]))],
    }


def parse_page(url: str, html: str, truncated: bool = False) -> dict:
    """Parse one fetched page (HTML string) into the Brand Builder page dict:
    {url, title, text, headings, html_meta, truncated}. Text/title come from
    fetch_sample.strip_html — the same stripping the style flow uses.
    `truncated` records that the source HTML hit the byte cap, so the
    page's tail was never seen (evaluation R6-S4: a 5.6MB page used to
    lose its tail silently; fetch_sample.fetch_url has always flagged
    this — now fetch_site does too)."""
    title, text = strip_html(html)
    parser = _SiteParser()
    parser.feed(html)
    return {"url": url, "title": title, "text": text,
            "word_count": len(text.split()),
            "js_shell_suspected": detect_js_shell(html, text),
            "truncated": truncated,
            "headings": parser.headings,
            "html_meta": extract_html_meta(url, parser)}


def _link_priority(u: str) -> int:
    path = urllib.parse.urlparse(u).path.lower()
    for i, kw in enumerate(PRIORITY_PATH_KEYWORDS):
        if kw in path:
            return i
    return len(PRIORITY_PATH_KEYWORDS)


_UNRESERVED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                  "0123456789-._~")


def _normalize_pct_escapes(path: str) -> str:
    """Normalise percent-escapes in a path (review L8): decode escapes
    of UNRESERVED characters ('%7E' -> '~'), uppercase the hex of the
    rest ('%2f' -> '%2F') — reserved escapes are never decoded, so the
    path structure cannot change."""
    def repl(m: re.Match) -> str:
        ch = chr(int(m.group(1), 16))
        return ch if ch in _UNRESERVED else "%" + m.group(1).upper()
    return re.sub(r"%([0-9A-Fa-f]{2})", repl, path)


def _canonical_url(url: str) -> str:
    """Canonical form for dedupe (evaluation R6-S6): scheme/host
    lowercased, default ports (:80 http, :443 https) dropped, a trailing
    /index.html or /index.php folded to its directory, and a trailing
    slash ignored (except the root '/'). '/a/index.html', '/a/' and
    '/a' are the same page and must never be fetched as three.

    Review L8 completions: IPv6 hosts keep their brackets, userinfo
    survives, ';params' are preserved, the query is normalised
    (pairs sorted, re-encoded consistently) and path percent-escapes
    are normalised — two spellings of one URL dedupe to one fetch."""
    p = urllib.parse.urlparse(url)
    scheme = p.scheme.lower()
    host = (p.hostname or "").lower()
    if ":" in host:
        host = f"[{host}]"  # IPv6 literal: urlunparse needs the brackets
    try:
        port = p.port
    except ValueError:
        port = None
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = None
    userinfo = ""
    if p.username:
        userinfo = p.username
        if p.password:
            userinfo += ":" + p.password
        userinfo += "@"
    netloc = userinfo + host if port is None else f"{userinfo}{host}:{port}"
    path = _normalize_pct_escapes(p.path or "/")
    if path.lower().endswith(("/index.html", "/index.php")):
        path = path.rsplit("/", 1)[0] + "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    query = urllib.parse.urlencode(
        sorted(urllib.parse.parse_qsl(p.query, keep_blank_values=True)),
        quote_via=urllib.parse.quote)
    return urllib.parse.urlunparse((scheme, netloc, path, p.params, query, ""))


def _clean_candidate(root_url: str, href: str) -> str | None:
    """Resolve `href` against the root and keep it only when it is a
    crawlable same-site page URL (shared by <a> discovery and the
    sitemap channel). "Same domain" = same scheme + netloc; for
    file:// URLs the candidate must additionally live in the root
    page's directory (a local fixture tree is not the whole filesystem).
    The returned URL is canonicalised (see _canonical_url), and the
    root page itself — in any of its canonical forms — is excluded."""
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
        return None
    root = urllib.parse.urlparse(root_url)
    absolute = urllib.parse.urljoin(root_url, href)
    parsed = urllib.parse.urlparse(absolute)
    if parsed.scheme not in ("http", "https", "file"):
        return None
    if (parsed.scheme, parsed.netloc) != (root.scheme, root.netloc):
        return None
    if root.scheme == "file":
        root_dir = root.path.rsplit("/", 1)[0] + "/"
        if not parsed.path.startswith(root_dir):
            return None
    if parsed.path.lower().endswith(_SKIP_EXTENSIONS):
        return None
    clean = _canonical_url(urllib.parse.urlunparse(parsed._replace(fragment="")))
    return clean if clean != _canonical_url(root_url) else None


def discover_links(root_url: str, html: str) -> list[str]:
    """Same-domain internal links from a page, priority paths first."""
    parser = _SiteParser()
    parser.feed(html)
    seen: list[str] = []
    for href in parser.links:
        clean = _clean_candidate(root_url, href)
        if clean and clean not in seen:
            seen.append(clean)
    return sorted(seen, key=_link_priority)  # stable: document order within a tier


_LOC_RE = re.compile(r"<loc>\s*([^<]+?)\s*</loc>", re.IGNORECASE)
_ROBOTS_SITEMAP_RE = re.compile(r"(?im)^\s*sitemap\s*:\s*(\S+)\s*$")

#: A sitemap crawl reads at most this many sitemap documents (the root
#: sitemap plus children of a sitemap index) — bounded like everything
#: else in this fetcher. Raised 3 -> 10 (evaluation R6-S2): real
#: sitemap indexes commonly have 4+ children, and the old cap silently
#: dropped every page past the second child.
_MAX_SITEMAP_DOCS = 10


def _parse_sitemap_locs(body: bytes) -> tuple[list[str], bool] | None:
    """Parse a sitemap document with ElementTree (review M14):
    (locs, is_index), or None when the document is not well-formed
    XML. Namespaces are stripped by localname, so default/prefixed
    sitemap namespaces all work; entities and CDATA are decoded by
    the parser — the old <loc> regex silently DROPPED every URL
    containing '&' (entity-escaped as &amp;) and every CDATA loc."""
    try:
        root_el = ET.fromstring(body)
    except Exception:
        return None

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1].lower()

    kind = local(root_el.tag)
    if kind not in ("urlset", "sitemapindex"):
        return None
    locs: list[str] = []
    for child in root_el:
        if local(child.tag) in ("url", "sitemap"):
            for sub in child:
                if local(sub.tag) == "loc" and sub.text and sub.text.strip():
                    locs.append(sub.text.strip())
    return locs, kind == "sitemapindex"


def _fetch_sitemap_locs(sitemap_url: str, timeout: int) -> tuple[list[str], bool]:
    """(locs, is_index) for one sitemap document: '.xml.gz' children
    are decompressed (review M14 — gzipped sitemaps are common on
    real hosts and used to parse as binary noise); a malformed
    document falls back to the <loc> regex on the decoded text."""
    body, _final, _ctype, _trunc = _fetch_raw(sitemap_url, timeout)
    if sitemap_url.lower().split("?")[0].endswith(".gz") or body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    parsed = _parse_sitemap_locs(body)
    if parsed is not None:
        return parsed
    text, _enc = decode_body(body)
    return _LOC_RE.findall(text), False


def _discover_sitemap(root_url: str, timeout: int = 20) -> tuple[list[str], bool]:
    """(urls, truncated) from the site's sitemap(s). `truncated` is
    True when the document cap stopped the crawl with sitemap
    documents still unread — the caller surfaces it instead of letting
    the missing pages vanish silently (evaluation R6-S2)."""
    root = urllib.parse.urlparse(root_url)
    candidates: list[str] = []
    if root.scheme in ("http", "https"):
        origin = f"{root.scheme}://{root.netloc}"
        robots_url = origin + "/robots.txt"
        default_sitemap = origin + "/sitemap.xml"
    else:
        robots_url = urllib.parse.urljoin(root_url, "robots.txt")
        default_sitemap = urllib.parse.urljoin(root_url, "sitemap.xml")
    try:
        robots, _final, _trunc, _enc = _fetch_html(robots_url, timeout)
        for m in _ROBOTS_SITEMAP_RE.finditer(robots):
            candidates.append(urllib.parse.urljoin(robots_url, m.group(1)))
    except Exception:
        pass
    candidates.append(default_sitemap)

    urls: list[str] = []
    docs_read = 0
    truncated = False
    queue = list(dict.fromkeys(candidates))
    while queue and docs_read < _MAX_SITEMAP_DOCS:
        sitemap_url = queue.pop(0)
        try:
            locs, is_index = _fetch_sitemap_locs(sitemap_url, timeout)
        except Exception:
            continue
        docs_read += 1
        for loc in locs:
            resolved = urllib.parse.urljoin(sitemap_url, loc.strip())
            path = urllib.parse.urlparse(resolved).path.lower()
            if is_index or path.endswith((".xml", ".xml.gz")):
                if resolved not in queue and resolved not in candidates:
                    if docs_read + len(queue) < _MAX_SITEMAP_DOCS:
                        queue.append(resolved)  # sitemap index child, one level
                    else:
                        truncated = True  # cap reached: child never read
                continue
            clean = _clean_candidate(root_url, resolved)
            if clean and clean not in urls:
                urls.append(clean)
    if queue:
        truncated = True
    return urls, truncated


def discover_sitemap_urls(root_url: str, timeout: int = 20) -> list[str]:
    """URLs from the site's sitemap(s) — a discovery channel alongside
    <a> links (calibration F1: a JS-shell homepage exposes no <a> links
    at all, but its blog was fully listed in /sitemap.xml).

    Candidates, in order: `Sitemap:` lines from robots.txt, then the
    conventional /sitemap.xml (site root for http(s), the root page's
    directory for file://). A sitemap whose <loc> entries end in .xml
    is a sitemap INDEX: its children are read too, one level deep.
    <loc> values may be relative — they resolve against the sitemap's
    own URL. Every returned URL passes the same same-site/page filter
    as <a>-discovered links. Fetch failures are silent."""
    urls, _truncated = _discover_sitemap(root_url, timeout)
    return urls


def _fetch_raw(url: str, timeout: int = 20,
                max_bytes: int = 5_000_000) -> tuple[bytes, str, str, bool]:
    """Fetch one URL's raw bytes, capped: (body, final_url,
    content_type, truncated)."""
    request = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(max_bytes + 1)
        try:
            content_type = response.headers.get("Content-Type", "") or ""
        except Exception:
            content_type = ""
        return raw[:max_bytes], response.geturl(), content_type, len(raw) > max_bytes


def _fetch_html(url: str, timeout: int = 20,
                max_bytes: int = 5_000_000,
                expect_html: bool = False) -> tuple[str, str, bool, str]:
    """Fetch one page's HTML, capped at max_bytes (audit round 2: bounded
    reads — together with max_pages this bounds a whole site fetch).

    Returns (html, final_url, truncated, encoding): `final_url` is the
    URL after any redirects (evaluation R6-S7: pages used to be recorded
    under their pre-redirect URL, so the same content could be fetched
    twice and cited wrongly); `truncated` says the body hit the byte
    cap; the body is decoded with its DECLARED charset (header, then
    <meta> sniff — review M12, fetch_sample.decode_body) and `encoding`
    says which encoding was used, so a legacy-encoded page is flagged
    instead of silently analysed as mojibake.

    With expect_html=True (page fetches), a response whose declared
    Content-Type is present and not HTML raises ValueError (review
    L9): a PDF or image linked from the homepage must land in
    `errors`, never be stripped into garbage 'text'."""
    raw, final_url, content_type, truncated = _fetch_raw(url, timeout, max_bytes)
    if expect_html:
        main_type = content_type.split(";")[0].strip().lower()
        if main_type and main_type not in ("text/html",
                                           "application/xhtml+xml"):
            raise ValueError(f"non-HTML content-type: {main_type}")
    html, encoding = decode_body(raw, content_type)
    return html, final_url, truncated, encoding


def fetch_site(root_url: str, max_pages: int = 8, timeout: int = 20,
               max_bytes: int = 5_000_000) -> dict:
    """Fetch root + prioritized internal pages. A page that fails to load is
    recorded under `errors`, never fatal (except the root itself).

    Discovery merges BOTH channels (evaluation R6-S3): <a> links from
    the homepage AND the site's sitemap(s) (robots.txt `Sitemap:` lines,
    then /sitemap.xml, sitemap indexes included) — the sitemap used to
    be consulted only when the homepage looked like a JS shell, so an
    orphan page listed only in the sitemap was missed on healthy sites
    too. The result carries `js_shell_suspected` when any fetched page
    looks like a JS shell, `sitemap_fallback_used` when the sitemap
    contributed URLs the links did not, and `sitemap_truncated` when
    the sitemap document cap stopped the sitemap crawl early. Pages are
    recorded under their FINAL (post-redirect) URL, deduplicated by
    canonical URL, and carry a per-page `truncated` flag."""
    try:
        root_html, root_final, root_truncated, root_enc = _fetch_html(
            root_url, timeout, max_bytes, expect_html=True)
    except Exception as exc:
        return {"root": root_url, "pages": [], "errors": [f"{root_url}: {exc}"]}
    root_page = parse_page(root_final, root_html, truncated=root_truncated)
    if root_enc != "utf-8":
        root_page["encoding"] = root_enc  # legacy charset: flagged, not hidden
    pages = [root_page]
    seen = {_canonical_url(root_final)}
    errors: list[str] = []
    # Discovery resolves against the FINAL root URL (review M13): a
    # root that redirects (http->https, www->apex, /old -> /new) used
    # to have its robots/sitemap candidates and same-site checks
    # computed from the PRE-redirect URL — on a cross-host redirect
    # every candidate was filtered out as 'another site'.
    links = discover_links(root_final, root_html)
    sitemap_urls, sitemap_truncated = _discover_sitemap(root_final, timeout)
    extra = [u for u in sitemap_urls if u not in links]
    sitemap_fallback_used = bool(extra)
    if extra:
        links = links + sorted(extra, key=_link_priority)
    for link in links:
        if len(pages) >= max_pages:
            break
        if _canonical_url(link) in seen:
            continue
        try:
            html, final_url, truncated, enc = _fetch_html(
                link, timeout, max_bytes, expect_html=True)
        except Exception as exc:
            errors.append(f"{link}: {exc}")
            continue
        if _canonical_url(final_url) in seen:
            continue  # redirect landed on an already-fetched page
        seen.add(_canonical_url(final_url))
        page = parse_page(final_url, html, truncated=truncated)
        if enc != "utf-8":
            page["encoding"] = enc
        if final_url != link:
            page["requested_url"] = link
        pages.append(page)
    result: dict = {"root": root_url, "pages": pages,
                    "js_shell_suspected": any(p["js_shell_suspected"] for p in pages),
                    "sitemap_fallback_used": sitemap_fallback_used,
                    "sitemap_truncated": sitemap_truncated}
    if errors:
        result["errors"] = errors
    return result


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    max_pages = 8
    for i, a in enumerate(argv):
        if a.startswith("--max-pages"):
            value = a.split("=", 1)[1] if "=" in a else (
                argv[i + 1] if i + 1 < len(argv) else "")
            try:
                max_pages = int(value)
            except (TypeError, ValueError):
                print("usage: fetch_site.py <root_url> [--max-pages 8]",
                      file=sys.stderr)
                return 2
            if "=" not in a and value:
                args = [x for x in args if x != value]
    if not args:
        print("usage: fetch_site.py <root_url> [--max-pages 8]", file=sys.stderr)
        return 2
    print(json.dumps(fetch_site(args[0], max_pages=max_pages),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
