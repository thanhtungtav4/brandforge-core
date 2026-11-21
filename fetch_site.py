#!/usr/bin/env python3
"""Fetch a whole SITE for the Brand Builder — a CLI helper, NOT part of the
MCP server (the server stays offline and deterministic by design).

Usage:
    .venv/bin/python fetch_site.py <root_url> [--max-pages 8]

Fetches the homepage, discovers same-domain internal links (paths matching
the priority list below are fetched first), and prints ONE JSON object:
{root, pages: [{url, title, text, headings, html_meta}]}.

`text` is the same clean plain text fetch_sample.py produces (its
strip_html is reused). `html_meta` carries the visual brand-kit evidence:
og_site_name, og_image, theme_color, logo_url (best guess), fonts and
colors mined from <style> blocks, inline style attributes and Google
Fonts links. Only the stdlib is used; file:// URLs work, which is how the
tests exercise this without a network.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
from collections import Counter
from html.parser import HTMLParser

from fetch_sample import _UA, strip_html

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
        self.logo_src: str | None = None
        self.style_chunks: list[str] = []
        self.inline_styles: list[str] = []
        self._in_style = False
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
        elif tag == "img":
            if self.logo_src is None:
                haystack = " ".join([a.get("src", ""), a.get("alt", ""),
                                     a.get("class", ""), a.get("id", "")]).lower()
                if "logo" in haystack and a.get("src"):
                    self.logo_src = a["src"]
        elif tag == "style":
            self._in_style = True
        elif tag in ("h1", "h2", "h3"):
            self._heading_tag = tag
            self._heading_parts = []
        if a.get("style"):
            self.inline_styles.append(a["style"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "style":
            self._in_style = False
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


def _font_families(style_text: str, font_links: list[str]) -> list[dict]:
    """font-family first choices from style text + Google Fonts families,
    as [{family, count}] sorted by count desc, then name."""
    counts: Counter = Counter()
    for m in _FONT_FAMILY_RE.finditer(style_text):
        first = m.group(1).split(",")[0].strip().strip("'\"")
        if first and first.lower() not in ("inherit", "initial", "sans-serif",
                                           "serif", "monospace", "cursive"):
            counts[first] += 1
        elif first:
            counts[first] += 0  # generic-only declarations carry no brand signal
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
        "logo_url": (urllib.parse.urljoin(url, parser.logo_src)
                     if parser.logo_src else None),
        "fonts": _font_families(style_text, parser.font_links),
        "colors": [{"hex": h, "count": n}
                   for h, n in sorted(colors.items(), key=lambda kv: (-kv[1], kv[0]))],
    }


def parse_page(url: str, html: str) -> dict:
    """Parse one fetched page (HTML string) into the Brand Builder page dict:
    {url, title, text, headings, html_meta}. Text/title come from
    fetch_sample.strip_html — the same stripping the style flow uses."""
    title, text = strip_html(html)
    parser = _SiteParser()
    parser.feed(html)
    return {"url": url, "title": title, "text": text,
            "headings": parser.headings,
            "html_meta": extract_html_meta(url, parser)}


def discover_links(root_url: str, html: str) -> list[str]:
    """Same-domain internal links from a page, priority paths first.

    "Same domain" = same scheme + netloc; for file:// URLs the candidate
    must additionally live in the root page's directory (a local fixture
    tree is not the whole filesystem)."""
    parser = _SiteParser()
    parser.feed(html)
    root = urllib.parse.urlparse(root_url)
    seen: list[str] = []
    for href in parser.links:
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urllib.parse.urljoin(root_url, href)
        parsed = urllib.parse.urlparse(absolute)
        if parsed.scheme not in ("http", "https", "file"):
            continue
        if (parsed.scheme, parsed.netloc) != (root.scheme, root.netloc):
            continue
        if root.scheme == "file":
            root_dir = root.path.rsplit("/", 1)[0] + "/"
            if not parsed.path.startswith(root_dir):
                continue
        if parsed.path.lower().endswith(_SKIP_EXTENSIONS):
            continue
        clean = urllib.parse.urlunparse(parsed._replace(fragment=""))
        if clean != root_url and clean not in seen:
            seen.append(clean)

    def priority(u: str) -> int:
        path = urllib.parse.urlparse(u).path.lower()
        for i, kw in enumerate(PRIORITY_PATH_KEYWORDS):
            if kw in path:
                return i
        return len(PRIORITY_PATH_KEYWORDS)

    return sorted(seen, key=priority)  # stable: document order within a tier


def _fetch_html(url: str, timeout: int = 20) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_site(root_url: str, max_pages: int = 8, timeout: int = 20) -> dict:
    """Fetch root + prioritized internal pages. A page that fails to load is
    recorded under `errors`, never fatal (except the root itself)."""
    try:
        root_html = _fetch_html(root_url, timeout)
    except Exception as exc:
        return {"root": root_url, "pages": [], "errors": [f"{root_url}: {exc}"]}
    pages = [parse_page(root_url, root_html)]
    errors: list[str] = []
    for link in discover_links(root_url, root_html):
        if len(pages) >= max_pages:
            break
        try:
            pages.append(parse_page(link, _fetch_html(link, timeout)))
        except Exception as exc:
            errors.append(f"{link}: {exc}")
    result: dict = {"root": root_url, "pages": pages}
    if errors:
        result["errors"] = errors
    return result


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    max_pages = 8
    for a in argv:
        if a.startswith("--max-pages"):
            value = a.split("=", 1)[1] if "=" in a else argv[argv.index(a) + 1]
            max_pages = int(value)
    if not args:
        print("usage: fetch_site.py <root_url> [--max-pages 8]", file=sys.stderr)
        return 2
    print(json.dumps(fetch_site(args[0], max_pages=max_pages),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
