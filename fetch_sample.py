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
import sys
import urllib.request
from html.parser import HTMLParser

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_SKIP_TAGS = {"script", "style", "nav", "footer"}
_BLOCK_TAGS = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6",
               "blockquote", "article", "section", "div", "br"}


class _TextExtractor(HTMLParser):
    """Collect visible text; skipped subtrees contribute nothing."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self.title_parts: list[str] = []
        self.chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _BLOCK_TAGS and not self._skip_depth:
            self.chunks.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK_TAGS and not self._skip_depth:
            self.chunks.append("\n\n")

    def handle_data(self, data: str) -> None:
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


def fetch_url(url: str, timeout: int = 20) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        html = response.read().decode("utf-8", errors="replace")
    title, text = strip_html(html)
    return {"url": url, "title": title, "text": text,
            "word_count": len(text.split())}


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
