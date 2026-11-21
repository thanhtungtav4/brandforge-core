#!/usr/bin/env python3
"""Fetch sample articles for Style Extraction — a CLI helper, NOT part of
the MCP server (the server stays offline and deterministic by design).

Usage:
    .venv/bin/python fetch_sample.py <url> [more urls...]

Prints a JSON list, one entry per URL: {url, title, text, word_count}.
HTML is stripped to plain text with the stdlib html.parser (no new deps):
script/style/nav/footer subtrees are dropped, block elements become
paragraph breaks.

The Style Extraction workflow this feeds:
    fetch (this helper, or Muse in-session) -> analyze_style (MCP tool)
    -> the model fills the qualitative schema -> save_style_profile
    -> a human approves the profile -> assemble_context(style_profile_id=...)
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from html.parser import HTMLParser

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_SKIP_TAGS = {"script", "style", "nav", "footer"}
_BLOCK_TAGS = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6",
               "blockquote", "article", "section", "div", "br"}

#: JSON-LD fields whose string values are real page copy (headline,
#: description, body) as opposed to identifiers/URLs. Text under these
#: keys in <script type="application/ld+json"> blocks is extracted and
#: appended to the page text; every other <script> stays skipped —
#: calibration F1: JS-app pages often carry their only machine-readable
#: copy in JSON-LD, while plain script data is code, not prose.
_LD_TEXT_KEYS = {"name", "description", "headline", "articleBody",
                 "text", "caption", "abstract"}


def _ldjson_texts(node, out: list[str]) -> None:
    """Collect string values under _LD_TEXT_KEYS from parsed JSON-LD
    (dicts, lists and @graph nests), in document order."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _LD_TEXT_KEYS and isinstance(value, str) and value.strip():
                out.append(value.strip())
            elif isinstance(value, (dict, list)):
                _ldjson_texts(value, out)
    elif isinstance(node, list):
        for item in node:
            _ldjson_texts(item, out)


class _TextExtractor(HTMLParser):
    """Collect visible text; skipped subtrees contribute nothing — except
    JSON-LD script blocks, whose declared copy fields are collected."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self._in_ldjson = False
        self._ld_parts: list[str] = []
        self.title_parts: list[str] = []
        self.chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            if tag == "script":
                attr = {k.lower(): (v or "") for k, v in attrs}
                if attr.get("type", "").strip().lower() == "application/ld+json":
                    self._in_ldjson = True
                    self._ld_parts = []
        elif tag == "title":
            self._in_title = True
        elif tag in _BLOCK_TAGS and not self._skip_depth:
            self.chunks.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            if tag == "script" and self._in_ldjson:
                self._in_ldjson = False
                self._flush_ldjson()
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK_TAGS and not self._skip_depth:
            self.chunks.append("\n\n")

    def _flush_ldjson(self) -> None:
        raw = "".join(self._ld_parts)
        self._ld_parts = []
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return  # malformed JSON-LD carries no trustworthy copy
        texts: list[str] = []
        _ldjson_texts(data, texts)
        for t in texts:
            self.chunks.append("\n\n")
            self.chunks.append(t)

    def handle_data(self, data: str) -> None:
        if self._in_ldjson:
            self._ld_parts.append(data)
            return
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        else:
            self.chunks.append(data)


def strip_html(html: str) -> tuple[str, str]:
    """Return (title, plain_text) for an HTML document string."""
    parser = _TextExtractor()
    parser.feed(html)
    title = " ".join("".join(parser.title_parts).split())
    raw = "".join(parser.chunks)
    paragraphs = [" ".join(p.split()) for p in raw.split("\n\n")]
    text = "\n\n".join(p for p in paragraphs if p)
    return title, text


#: Hard cap on bytes read per page (audit round 2): a hostile or broken
#: server must not make this helper buffer an unbounded response.
MAX_BYTES = 5_000_000

_SCRIPT_BLOCK_RE = re.compile(r"<script\b[^>]*>(.*?)</script\s*>",
                              re.IGNORECASE | re.DOTALL)


def detect_js_shell(html: str, text: str) -> bool:
    """Heuristic (calibration F1): is this page a JS-app shell whose real
    content is rendered client-side from <script> data?

    The calibration site's homepage was 2.9 MB of HTML with 97% of its
    bytes inside <script> blocks and ZERO extractable words — the old
    fetchers returned it silently as an empty page. Suspected when the
    extracted text is tiny (< 100 words) AND either the page is large
    (>= 20 KB) with no text at all, or >= 50% of the HTML bytes are
    script bodies. Server-rendered pages with ordinary analytics/widget
    scripts stay far below both bars."""
    if not html:
        return False
    words = len((text or "").split())
    if words >= 100:
        return False
    total = len(html)
    if words == 0 and total >= 20_000:
        return True
    script_bytes = sum(len(m.group(1)) for m in _SCRIPT_BLOCK_RE.finditer(html))
    return script_bytes >= 0.5 * total


def fetch_url(url: str, timeout: int = 20, max_bytes: int = MAX_BYTES) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(max_bytes + 1)
    truncated = len(raw) > max_bytes
    html = raw[:max_bytes].decode("utf-8", errors="replace")
    title, text = strip_html(html)
    return {"url": url, "title": title, "text": text,
            "word_count": len(text.split()), "truncated": truncated,
            "js_shell_suspected": detect_js_shell(html, text)}


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: fetch_sample.py <url> [more urls...]", file=sys.stderr)
        return 2
    results = []
    for url in argv:
        try:
            results.append(fetch_url(url))
        except Exception as exc:  # one bad URL must not kill the batch
            results.append({"url": url, "error": str(exc), "title": "",
                            "text": "", "word_count": 0})
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
