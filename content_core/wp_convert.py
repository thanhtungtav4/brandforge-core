"""HTML -> draft sections conversion for imported WordPress posts
(specs/wp-rewrite-feature.md §3.2).

The rule table is FIXED — the import report's coverage number is
only meaningful if every post is converted by the same rules:

| HTML                  | Result                                             |
|-----------------------|----------------------------------------------------|
| h2 (and h1)           | new section boundary; its text is the heading      |
| content before 1st h2 | the intro section, heading ""                      |
| h3-h6                 | demoted to a text paragraph in the current         |
|                       | section; counted in the report                     |
| p                     | a paragraph; paragraphs join with a blank line     |
| ul / ol               | one text line per li                               |
| img / figure          | extracted to the section's source_media (never     |
|                       | into the text): wp_media_id from the               |
|                       | wp-image-<id> class, source_url, alt, caption      |
| a                     | anchor text stays in the text; the (anchor, href)  |
|                       | pair is recorded in the section's links            |
| blockquote            | plain paragraphs; counted in the report            |
| table / iframe /      | raw_blocks of the current section, HTML preserved  |
| embed / object / form | verbatim, with the tag as the note                 |
| a paragraph that is   | a raw_block with note "shortcode"                  |
| only a shortcode      |                                                    |

Text inside constructs the table does not route (svg, video,
audio, button, select, textarea, noscript, template) is DROPPED
from the sections — and the coverage metric counts it: coverage =
(chars of section text + heading text + raw-block text + media
captions) / (chars of ALL text in the source, script/style
excluded). A post under COVERAGE_FLOOR still imports, flagged —
the shortfall is reported, never hidden (spec §3.2).
"""
from __future__ import annotations

import html as _html
import re
from html.parser import HTMLParser

# The linked-draft title prefix (§12b decision 1: KEPT — the
# fastest way to spot an original/rewrite pair in wp-admin).
REWRITE_TITLE_PREFIX = "[Viết lại] "

COVERAGE_FLOOR = 0.97
LONG_POST_WORDS = 20000

_RAW_TAGS = {"table", "iframe", "embed", "object", "form"}
_DROP_TAGS = {"svg", "video", "audio", "button", "select",
              "textarea", "noscript", "template"}
_SKIP_TAGS = {"script", "style"}
_HEADING_TAGS = {"h1", "h2"}
_DEMOTE_TAGS = {"h3", "h4", "h5", "h6"}
_SHORTCODE_RE = re.compile(r"\[[^\[\]]+\](\s*\[/[^\[\]]+\])?")
_WP_IMAGE_RE = re.compile(r"wp-image-(\d+)")


def _norm(text: str) -> str:
    """The one whitespace normalisation every side of the coverage
    ratio shares: entities are already decoded by the parser;
    every whitespace run collapses to one space, ends stripped."""
    return " ".join(str(text or "").split())


def plain_text(rendered: str) -> str:
    """A WP `*.rendered` field (title, excerpt) as plain text:
    tags stripped, entities decoded, whitespace normalised."""
    text = re.sub(r"<[^>]+>", " ", str(rendered or ""))
    return _norm(_html.unescape(text))


class _Converter(HTMLParser):
    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.source = source
        self._line_starts = [0] + [m.end()
                                   for m in re.finditer("\n", source)]
        # Outputs
        self.sections: list[dict] = []
        self.raw_blocks: list[dict] = []
        self.media: list[dict] = []
        self.demoted_headings = 0
        self.blockquotes = 0
        self.total_chars = 0
        self.kept_chars = 0
        # State
        self._cur: dict | None = None
        self._para: list[str] | None = None
        self._para_start: int | None = None
        self._heading: list[str] | None = None
        self._demote: list[str] | None = None
        self._list: list[str] | None = None
        self._li: list[str] | None = None
        self._figure: dict | None = None
        self._caption: list[str] | None = None
        self._raw: dict | None = None
        self._skip_depth = 0
        self._drop_depth = 0
        self._anchor: dict | None = None

    # -- positions ---------------------------------------------------
    def _offset(self) -> int:
        line, col = self.getpos()
        return self._line_starts[line - 1] + col

    def _tag_end(self) -> int:
        return self.source.index(">", self._offset()) + 1

    # -- section / buffer plumbing ------------------------------------
    def _ensure_section(self) -> dict:
        if self._cur is None:
            self._cur = {"id": f"s{len(self.sections) + 1}",
                         "heading": "", "intent": "", "target_words": 0,
                         "parts": [], "links": [], "source_media": []}
            self.sections.append(self._cur)
        return self._cur

    def _count_kept(self, text: str):
        self.kept_chars += len(text)

    def _flush_para(self, at_p_end: bool = False):
        if self._para is None:
            return
        text = _norm("".join(self._para))
        start, self._para, self._para_start = \
            self._para_start, None, None
        if not text:
            return
        if at_p_end and start is not None \
                and _SHORTCODE_RE.fullmatch(text):
            end = self._tag_end()
            self.raw_blocks.append({
                "section_id": self._ensure_section()["id"],
                "html": self.source[start:end], "note": "shortcode"})
            self._count_kept(text)
            return
        self._ensure_section()["parts"].append(text)
        self._count_kept(text)

    def _flush_list(self):
        if self._list is None:
            return
        items, self._list = self._list, None
        if items:
            self._ensure_section()["parts"].append("\n".join(items))

    def _flush_li(self):
        if self._li is None:
            return
        text = _norm("".join(self._li))
        self._li = None
        if text:
            if self._list is None:
                self._list = []
            self._list.append(text)
            self._count_kept(text)

    def _flush_demote(self):
        if self._demote is None:
            return
        text = _norm("".join(self._demote))
        self._demote = None
        if text:
            self._ensure_section()["parts"].append(text)
            self._count_kept(text)

    def _add_media(self, entry: dict):
        section = self._ensure_section()
        entry = {**entry, "section_id": section["id"]}
        section["source_media"].append(entry)
        self.media.append(entry)

    # -- parser events -------------------------------------------------
    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if self._raw is not None:
            if tag == self._raw["tag"]:
                self._raw["depth"] += 1
            return
        if tag in _DROP_TAGS:
            self._drop_depth += 1
            return
        if self._drop_depth:
            return
        attrs = dict(attrs)
        if tag in _RAW_TAGS:
            self._flush_para()
            self._flush_list()
            self._raw = {"tag": tag, "depth": 1,
                         "start": self._offset(), "text": []}
            return
        if tag in _HEADING_TAGS:
            self._flush_para()
            self._flush_list()
            self._heading = []
            return
        if tag in _DEMOTE_TAGS:
            self._flush_para()
            self._demote = []
            self.demoted_headings += 1
            return
        if tag == "p":
            self._flush_para()
            self._para = []
            self._para_start = self._offset()
            return
        if tag in ("ul", "ol"):
            self._flush_para()
            self._flush_list()
            self._list = []
            return
        if tag == "li":
            self._flush_li()
            self._li = []
            return
        if tag == "blockquote":
            self.blockquotes += 1
            return
        if tag == "figure":
            self._figure = {"media": None, "caption": ""}
            return
        if tag == "figcaption":
            if self._figure is not None:
                self._caption = []
            return
        if tag == "img":
            entry = self._media_entry(attrs)
            if self._figure is not None:
                self._figure["media"] = entry
            else:
                self._add_media(entry)
            return
        if tag == "a":
            self._anchor = {"href": attrs.get("href", ""), "buf": []}
            return
        if tag == "br":
            self._append_text(" ")
            return
        # Transparent containers (div, span, strong, …): nothing.

    def handle_startendtag(self, tag, attrs):
        # Void elements written XHTML-style (<img ... />): route
        # through the same handlers; raw/drop bookkeeping balances
        # itself because handle_endtag ignores them.
        if tag == "img":
            self.handle_starttag(tag, attrs)
        elif tag in _RAW_TAGS:
            self.handle_starttag(tag, attrs)
            if self._raw is not None and self._raw["tag"] == tag:
                self._close_raw(self._tag_end())

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if self._raw is not None:
            if tag == self._raw["tag"]:
                self._raw["depth"] -= 1
                if self._raw["depth"] <= 0:
                    self._close_raw(self._tag_end())
            return
        if tag in _DROP_TAGS:
            if self._drop_depth:
                self._drop_depth -= 1
            return
        if self._drop_depth:
            return
        if tag in _HEADING_TAGS:
            if self._heading is not None:
                text = _norm("".join(self._heading))
                self._heading = None
                self._flush_para()
                self._flush_list()
                self._cur = None
                section = self._ensure_section()
                section["heading"] = text
                self._count_kept(text)
            return
        if tag in _DEMOTE_TAGS:
            self._flush_demote()
            return
        if tag == "p":
            self._flush_para(at_p_end=True)
            return
        if tag in ("ul", "ol"):
            self._flush_li()
            self._flush_list()
            return
        if tag == "li":
            self._flush_li()
            return
        if tag == "figcaption":
            if self._caption is not None and self._figure is not None:
                self._figure["caption"] = _norm("".join(self._caption))
            self._caption = None
            return
        if tag == "figure":
            if self._figure is not None:
                entry = self._figure["media"]
                if entry is not None:
                    entry["caption"] = self._figure["caption"]
                    self._add_media(entry)
                    self._count_kept(self._figure["caption"])
                self._figure = None
            return
        if tag == "a":
            if self._anchor is not None:
                anchor = _norm("".join(self._anchor["buf"]))
                href = self._anchor["href"]
                self._anchor = None
                if anchor and href:
                    self._ensure_section()["links"].append(
                        {"anchor": anchor, "href": href})
            return

    def handle_data(self, data):
        if self._skip_depth:
            return
        norm = _norm(data)
        if norm:
            self.total_chars += len(norm)
        if self._raw is not None:
            self._raw["text"].append(data)
            return
        if self._drop_depth:
            return  # counted in the total, never routed: the loss
            # is exactly what the coverage metric exists to show
        self._append_text(data)

    def _append_text(self, data):
        if self._caption is not None:
            self._caption.append(data)
        elif self._heading is not None:
            self._heading.append(data)
        elif self._demote is not None:
            self._demote.append(data)
        elif self._li is not None:
            self._li.append(data)
        elif self._para is not None:
            self._para.append(data)
        elif data.strip():
            # Stray text (bare in a div, a blockquote's own words):
            # an implicit paragraph in the current section.
            self._para = [data]
            self._para_start = None
        if self._anchor is not None:
            self._anchor["buf"].append(data)

    def _close_raw(self, end: int):
        raw, self._raw = self._raw, None
        self.raw_blocks.append({
            "section_id": self._ensure_section()["id"],
            "html": self.source[raw["start"]:end], "note": raw["tag"]})
        self._count_kept(_norm(" ".join(raw["text"])))

    def _media_entry(self, attrs: dict) -> dict:
        match = _WP_IMAGE_RE.search(attrs.get("class", ""))
        return {"wp_media_id": int(match.group(1)) if match else None,
                "source_url": attrs.get("src", ""),
                "alt": attrs.get("alt", ""), "caption": ""}

    def close(self):
        super().close()
        self._flush_para()
        self._flush_li()
        self._flush_list()
        self._flush_demote()
        if self._raw is not None:
            self._close_raw(len(self.source))


def convert_post_html(content_html: str) -> dict:
    """Convert one post's content.rendered per the fixed rule table
    in the module docstring. Returns {sections, media, raw_blocks,
    coverage, demoted_headings, blockquotes, links_extracted,
    word_count, no_text}. Sections carry the draft shape ({id,
    heading, intent, target_words, text}) plus the provenance the
    writer needs: links[] and source_media[] per section."""
    parser = _Converter(str(content_html or ""))
    parser.feed(str(content_html or ""))
    parser.close()
    sections = []
    for raw in parser.sections:
        sections.append({
            "id": raw["id"], "heading": raw["heading"], "intent": "",
            "target_words": 0,
            "text": "\n\n".join(p for p in raw["parts"] if p),
            "links": raw["links"], "source_media": raw["source_media"]})
    if not sections:
        sections = [{"id": "s1", "heading": "", "intent": "",
                     "target_words": 0, "text": "", "links": [],
                     "source_media": []}]
    coverage = 1.0
    if parser.total_chars:
        coverage = min(1.0, parser.kept_chars / parser.total_chars)
    word_count = sum(len(s["text"].split()) for s in sections)
    return {
        "sections": sections,
        "media": parser.media,
        "raw_blocks": parser.raw_blocks,
        "coverage": round(coverage, 4),
        "demoted_headings": parser.demoted_headings,
        "blockquotes": parser.blockquotes,
        "links_extracted": sum(len(s["links"]) for s in sections),
        "word_count": word_count,
        "no_text": not any(s["text"].strip() for s in sections),
    }
