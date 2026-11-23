"""Brand Builder — extract a DRAFT Brand Voice profile from a website.

Smith's request: build a Brand FAST by analyzing a target site (the user's
own client site) or a competitor site. The output is a **draft** in the
exact structure of data/brand_mocbrew.yaml (plus clearly-marked draft-only
fields) so the user edits/approves it quickly instead of writing a Brand
Voice profile from scratch.

Division of labour, same as Style Extraction:
- CODE (this module) does everything deterministic: corpus stats, the
  style fingerprint of the site's copy, the visual brand kit (with the
  source_url each value came from), voice-rule SUGGESTIONS derived from
  measured numbers, vocabulary candidates, and the site's own claims
  flagged for evidence review.
- The MODEL reads the pages and fills the small qualitative schema
  (tone traits, audience guess, positioning, differentiators) — code only
  validates and stores it.
- The HUMAN approves. Nothing here silently becomes an approved brand:
  every draft carries review_status="draft" and review_notes listing what
  was inferred and what the user must decide. Banned terms in particular
  can NOT be inferred from a site (a site never says what it forbids
  itself), so drafts start with banned_terms=[] and a note saying so.

COMPETITOR RULE: a competitor's copy is analysis material only. Drafts
built with source_type="competitor" mark every example passage
reference_only=True (the marking is re-stamped at save time in
loaders.save_brand_draft, so it cannot be stripped on the way in),
and draft_to_brand_yaml REFUSES to convert while any example is still
reference-only — for a competitor draft, a present-but-falsy flag
(0, "", None) does not clear the passage; the user clears it by
replacing the text and removing the flag (or setting it to an
explicit boolean False).
Honest scope (review M10): source_type is the user's own declaration
— the guard makes conversion impossible BY ACCIDENT for a draft
declared as a competitor's; it cannot detect a competitor site that
was mislabelled "own_site" at build time.

Fetching is NOT here: fetch_site.py (CLI helper, stdlib) turns a URL into
pages = [{url, title, text, headings, html_meta}]; the MCP server stays
offline and every function below takes that pages shape.
"""
from __future__ import annotations

import math
import re
import unicodedata
import urllib.parse
from collections import Counter

import yaml

from .fact_check import CLAIM_PHRASES, find_claim_detail
from .fact_check import _QUESTION_MARKERS_NORM
from .linter import WORD_RE as _WORD_RE
from .linter import normalize_for_match
from .linter import split_sentences
from .linter import word_tokens as _tokens
from .loaders import (load_brand, prohibited_claims, validate_image_sources,
                      validate_visual)
from .loaders import slugify as _slugify
from .style import STOPWORDS, fingerprint

# ---------------------------------------------------------------------------
# Constants (documented heuristics — tune here, nowhere else)
# ---------------------------------------------------------------------------

#: The qualitative schema the MODEL fills after reading the site's pages.
#: Unknown keys are IGNORED on build (same documented choice as style.py);
#: missing keys default to empty.
QUAL_FIELDS = ["tone_traits", "audience_guess", "positioning", "differentiators"]

#: Generic navigation/boilerplate syllables: frequent on every Vietnamese
#: site, so they carry no domain signal for the vocabulary heuristic.
_GENERIC_WEB_WORDS = {
    "trang", "chủ", "giới", "thiệu", "sản", "phẩm", "dịch", "vụ",
    "liên", "hệ", "tin", "tức", "mua", "ngay", "xem", "thêm", "tìm",
    "kiếm", "đăng", "nhập", "giỏ", "hàng", "thanh", "toán", "hotline",
    "email", "website", "copyright", "bản", "quyền",
    # pronouns are voice signal (measured by the fingerprint), not domain
    # vocabulary:
    "mình", "bạn", "tôi", "ta", "họ", "chúng",
}

#: Title/heading words that are page furniture, not brand/product names —
#: trimmed from the edges of capitalized-name candidates.
_GENERIC_NAME_WORDS = {
    "Trang", "Chủ", "Giới", "Thiệu", "Sản", "Phẩm", "Dịch", "Vụ",
    "Liên", "Hệ", "Tin", "Tức", "Về", "Blog", "Home",
}

_CAP_TOKEN_RE = re.compile(r"[A-ZÀ-Ỹ][\wà-ỹ]*")

#: Superlative/claim patterns scanned on the site itself: the fact_checker's
#: evidence-requiring phrases + the house prohibited-claim patterns (from
#: the shipped brand). A site's own claims are REVIEW material, never
#: approved brand claims.
_DEFAULT_REQUIRES_EVIDENCE = ["mọi số liệu", "mọi khẳng định về sức khỏe",
                              "mọi so sánh với đối thủ"]


def _claim_patterns() -> list[str]:
    patterns = list(CLAIM_PHRASES)
    try:
        patterns += prohibited_claims(load_brand())
    except Exception:
        pass
    out: list[str] = []
    for p in patterns:
        if p not in out:
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------

def site_corpus(pages: list[dict]) -> dict:
    """Join the pages into one corpus with per-page stats."""
    pages = [p for p in pages or [] if p and p.get("text")]
    per_page = []
    for p in pages:
        text = p.get("text", "")
        per_page.append({"url": p.get("url", ""), "title": p.get("title", ""),
                         "word_count": len(text.split()),
                         "sentence_count": len(split_sentences(text))})
    all_text = "\n\n".join(p.get("text", "") for p in pages)
    return {"all_text": all_text,
            "total_words": len(all_text.split()),
            "page_count": len(pages),
            "per_page": per_page}


# ---------------------------------------------------------------------------
# Visual brand kit — every value carries its source_url evidence
# ---------------------------------------------------------------------------

def _title_brand_segment(pages: list[dict]) -> str | None:
    """The title segment repeated across pages ("Trang Chủ | Sớm Coffee"
    and "Giới Thiệu | Sớm Coffee" share "Sớm Coffee") — the brand name when
    og:site_name is absent."""
    counts: Counter = Counter()
    source: dict[str, str] = {}
    for p in pages:
        segments = re.split(r"\s*[|—–·:]\s*|\s+-\s+", p.get("title", ""))
        if len(segments) < 2:
            continue  # no separator -> the whole title is a page title, not a brand segment
        for seg in segments:
            seg = seg.strip()
            if seg:
                counts[seg] += 1
                source.setdefault(seg, p.get("url", ""))
    for seg, n in counts.most_common():
        if n >= 2 or len(pages) == 1:
            return seg
    return None


def extract_brand_kit(pages: list[dict]) -> dict:
    """Merge the pages' html_meta into one visual brand kit.

    Name precedence: og:site_name > repeated title segment > domain label.
    Colors/fonts are merged frequency tables (top 6 / top 3). Scalar fields
    are {value, source_url} — the first page that provides the value is the
    evidence."""
    pages = [p for p in pages or [] if p]
    metas = [(p.get("url", ""), p.get("html_meta") or {}) for p in pages]

    def first(key: str):
        for url, meta in metas:
            if meta.get(key):
                return {"value": meta[key], "source_url": url}
        return {"value": None, "source_url": ""}

    name = first("og_site_name")
    if not name["value"]:
        segment = _title_brand_segment(pages)
        if segment:
            src = next((p.get("url", "") for p in pages if segment in p.get("title", "")), "")
            name = {"value": segment, "source_url": src}
    if not name["value"] and pages:
        host = urllib.parse.urlparse(pages[0].get("url", "")).netloc or "site"
        label = host.removeprefix("www.").split(".")[0]
        name = {"value": label.capitalize() or "Brand", "source_url": pages[0].get("url", "")}

    color_counts: Counter = Counter()
    font_counts: Counter = Counter()
    for _url, meta in metas:
        for c in meta.get("colors", []):
            color_counts[c["hex"]] += int(c.get("count", 1))
        for f in meta.get("fonts", []):
            # Defensive (F5): pages can come from fetchers other than
            # fetch_site.py — a CSS variable is never a font family.
            if "var(" in str(f.get("family", "")).lower():
                continue
            font_counts[f["family"]] += int(f.get("count", 1))

    return {
        "brand_name": name,
        "logo_url": first("logo_url"),
        "theme_color": first("theme_color"),
        "og_image": first("og_image"),
        "colors": [{"hex": h, "count": n} for h, n in
                   sorted(color_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:6]],
        "fonts": [{"family": f, "count": n} for f, n in
                  sorted(font_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]],
    }


# ---------------------------------------------------------------------------
# Corpus cleaning — boilerplate filtering for voice statistics
# ---------------------------------------------------------------------------
# Fetched site text is block-structured (blank-line separated blocks).
# The blocks that repeat verbatim across a site — nav, cart, hotline,
# sidebar listings, footers — are CHROME, not the brand's voice; they
# also carry no sentence punctuation, so measured raw they glue onto
# real prose and poison every sentence statistic (trial bug 2: on
# mambagiaokhoe.com 845 of 919 blocks were repeats, the raw sentence
# median came out 33 against real prose ≈ 27, and the suggested band
# [28, 30] was unusable without a manual operator edit). The
# calibration round filtered table-like segments out of the linter's
# sentence statistics; this is the same move one level up, applied to
# the corpus the builder measures. Voice statistics (suggest_voice_
# rules, the analyze fingerprint, compare mode) are computed on the
# CLEANED text; the raw corpus stays available for fetch diagnostics
# (site_corpus total_words / per-page counts are unchanged).

#: Markers of legal/footer lines — boilerplate even when the block
#: appears only once (a homepage-only legal footer is still chrome).
_LEGAL_BOILERPLATE_MARKERS = ("©", "copyright", "bản quyền",
                              "all rights reserved", "ghi rõ nguồn")

#: A short block whose tokens are mostly generic web vocabulary is a
#: nav/chrome label, not prose ("Trang chủ", "Giỏ hàng Giỏ hàng 0").
#: The word bound keeps the rule away from real sentences: prose of
#: <= 14 tokens that is >= 60% navigation vocabulary does not occur
#: on the sites measured so far.
_BOILERPLATE_MAX_WORDS = 14
_BOILERPLATE_GENERIC_SHARE = 0.6


def is_boilerplate_segment(segment: str) -> bool:
    """True when a fetched-text block is page furniture, not prose:
    it carries a legal/footer marker, or it is short (<= 14 tokens)
    and mostly generic web vocabulary (see _GENERIC_WEB_WORDS).
    Conservative by design — a block of real prose sentences is long
    and content-dense, so it trips neither rule."""
    low = (segment or "").lower()
    if any(marker in low for marker in _LEGAL_BOILERPLATE_MARKERS):
        return True
    toks = _tokens(segment or "")
    if 0 < len(toks) <= _BOILERPLATE_MAX_WORDS:
        generic = sum(1 for t in toks if t in _GENERIC_WEB_WORDS)
        return generic / len(toks) >= _BOILERPLATE_GENERIC_SHARE
    return False


def _segment_key(segment: str) -> str:
    """Normalised identity of a block for repetition counting:
    case- and whitespace-insensitive (fetchers vary both)."""
    return " ".join((segment or "").lower().split())


def clean_corpus_text(text: str) -> str:
    """The PROSE-weighted view of a fetched corpus, for voice
    statistics: boilerplate-like segments are removed entirely, and
    every other verbatim-repeated block is kept ONCE (its first
    occurrence). Nav/sidebar chrome repeats on every page by
    construction, so counting it per page lets page furniture
    outvote the brand's prose in every median and rate; genuine
    prose blocks (post excerpts repeated on listing pages) stay
    represented exactly once, so the sentence-length tail they carry
    is not thinned either. Block order is preserved. A corpus with
    no repeated/boilerplate blocks is returned block-for-block."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text or "") if b.strip()]
    seen: set[str] = set()
    kept: list[str] = []
    for block in blocks:
        if is_boilerplate_segment(block):
            continue
        key = _segment_key(block)
        if key in seen:
            continue
        seen.add(key)
        kept.append(block)
    return "\n\n".join(kept)


# ---------------------------------------------------------------------------
# Voice-rule suggestions — derived from the measured fingerprint
# ---------------------------------------------------------------------------

#: Fallback max-sentence cap when a corpus has no measurable sentences,
#: and the floor of the derived range (a derived cap never goes below the
#: long-standing default — brands that write short keep the strict cap).
MAX_SENTENCE_FLOOR = 25
#: Ceiling of the derived range: beyond ~45 words a Vietnamese sentence
#: is a run-on for any brand measured so far.
MAX_SENTENCE_CEILING = 45


def _derived_max_sentence(corpus_text: str) -> int:
    """Derive the max-sentence cap from the site's OWN sentence lengths
    (calibration R3/F6): the 90th percentile of observed lengths,
    clamped to [MAX_SENTENCE_FLOOR, MAX_SENTENCE_CEILING].

    Why p90 and not the observed max or p95: the top decile of a scraped
    corpus is dominated by extraction artefacts (page furniture and
    flattened tables became 49-69-word 'sentences' in calibration),
    while genuine prose tails off below it — Cao Nguyên Mộc's real
    sentences run to ~39 words (p90 = 39) against a fixed cap of 25 that
    flagged 28/28 of all violations on its clean published posts. A
    corpus of short sentences (p90 <= 25) keeps the default 25."""
    lengths = sorted(len(s.split()) for s in split_sentences(corpus_text or ""))
    if not lengths:
        return MAX_SENTENCE_FLOOR
    p90 = lengths[math.ceil(0.90 * len(lengths)) - 1]
    return max(MAX_SENTENCE_FLOOR, min(MAX_SENTENCE_CEILING, p90))


def _brand_name_subject_hits(corpus_text: str, brand_name: str) -> int:
    """Sentences that OPEN with the brand name in subject position.
    Pronoun counting cannot see name-as-pronoun (calibration §2: the
    brand narrates as a person — 'Mộc bán…', 'Mộc muốn…').

    Opening forms tried (a sentence counts ONCE if any matches): the
    full name; its last word (the short self-reference form, 'Mộc' for
    'Cao Nguyên Mộc'); and its PREFIXES — first token and first two
    tokens (evaluation R6-S1: 'Ong Rừng U Minh' writes as 'Ong Rừng…';
    the last-token-only rule looked for 'Minh' and found nothing, so a
    first-person-by-name brand was misread as third-person)."""
    name_tokens = [t.strip(",.;:()").lower()
                   for t in (brand_name or "").split() if t.strip(",.;:()")]
    if not name_tokens:
        return 0
    forms = [name_tokens, name_tokens[:2], name_tokens[:1], name_tokens[-1:]]
    forms = [f for f in dict.fromkeys(tuple(f) for f in forms if f)]
    hits = 0
    for sentence in split_sentences(corpus_text or ""):
        tokens = [t.strip(",.;:()").lower() for t in sentence.split()]
        if not tokens:
            continue
        if any(tokens[:len(f)] == list(f) for f in forms):
            hits += 1
    return hits


def suggest_voice_rules(corpus_text: str, brand_name: str | None = None) -> dict:
    """Deterministic voice suggestions from style.fingerprint().

    - sentence median range: [floor(median*0.85), ceil(median*1.15)],
      clamped to [3, 30] and at least 2 wide — a STARTING range around how
      the site actually writes, for the user to tighten.
    - max_sentence_words: DERIVED from the corpus (see
      _derived_max_sentence: p90 of observed lengths, clamped [25, 45])
      — no longer a fixed 25 for every brand.
    - pov: first_person_rate >= 1.5/100 words → first-person observed;
      else, when `brand_name` is given and the brand name repeatedly
      opens sentences as the subject (>= 3 hits) while the reader is
      addressed as "bạn" (reader_rate >= 0.8) → "first-person (as brand
      name)"; otherwise the copy speaks in third person / brand name.
    - reader_address: "bạn" when reader_rate >= 0.8/100 words, else None.
    - exclamation: a high rate is reported as a review NOTE, never copied
      into formatting_rules — hype punctuation must be a deliberate choice.

    The corpus is measured AFTER boilerplate filtering
    (clean_corpus_text): repeated chrome blocks are deduped and
    boilerplate-like segments excluded BEFORE the fingerprint and
    POV statistics are computed, so fetched page furniture can no
    longer set a brand's sentence band (trial bug 2). When filtering
    removed anything, a formatting note says so. ONE statistic is
    deliberately still derived from the RAW text: max_sentence_words
    (see _derived_max_sentence — its p90 was designed for raw scraped
    corpora in the calibration round, where the artefacts live in the
    top decile the percentile discounts; deduping first would thin
    the genuine excerpt tail and under-cap brands whose own posts
    run long — CNM's derived 39 is the pinned evidence).
    """
    raw_text = corpus_text or ""
    corpus_text = clean_corpus_text(raw_text)
    fp = fingerprint(corpus_text)
    median = fp["sentence_len"]["median"]
    lo = max(3, math.floor(median * 0.85)) if median else 11
    hi = min(30, math.ceil(median * 1.15)) if median else 14
    if hi <= lo:
        hi = lo + 2
    max_sentence = _derived_max_sentence(raw_text)

    fp_rate = fp["first_person_rate"]
    subject_hits = (_brand_name_subject_hits(corpus_text, brand_name)
                    if brand_name else 0)
    if fp_rate >= 1.5:
        pov = "first-person"
        pov_note = (f"site copy speaks in first person ({fp_rate}/100 words "
                    f"of mình/tôi) — confirm WHICH pronoun the brand uses "
                    f"before approving")
    elif subject_hits >= 3 and fp["reader_rate"] >= 0.8:
        pov = "first-person (as brand name)"
        pov_note = (f"the brand name '{brand_name}' opens {subject_hits} "
                    f"sentences as the speaking subject while readers are "
                    f"addressed as 'bạn' ({fp['reader_rate']}/100 words) — "
                    f"the brand speaks first-person BY NAME, not in third "
                    f"person; confirm the exact self-reference form")
    else:
        pov = "third-person"
        pov_note = (f"first person is rare in the site copy "
                    f"({fp_rate}/100 words) — the brand speaks about itself "
                    f"in third person / by name")

    notes: list[str] = []
    if corpus_text != raw_text:
        notes.append(
            "boilerplate filtering: repeated blocks were deduped to "
            "one copy and boilerplate segments removed before "
            "measuring — fetched chrome (nav, cart, sidebars, "
            "footers) is not the brand's voice and must not set its "
            "sentence band")
    excl = fp["exclamation_rate"]
    if excl >= 10:
        notes.append(
            f"site copy uses exclamation marks frequently ({excl} per 100 "
            f"sentences) — decide deliberately whether to keep this; it is "
            f"NOT copied into formatting_rules automatically")
    if max_sentence > MAX_SENTENCE_FLOOR:
        notes.append(
            f"max_sentence_words {max_sentence} was DERIVED from the "
            f"site's own sentence lengths (90th percentile, clamped to "
            f"[{MAX_SENTENCE_FLOOR}, {MAX_SENTENCE_CEILING}]) — the fixed "
            f"default of 25 does not fit how this site actually writes")
    if fp["word_count"] < 800:
        notes.append(
            f"corpus is small ({fp['word_count']} words) — these suggestions "
            f"are a rough starting point, not a measurement to trust blindly")
    notes.append(pov_note)

    return {
        "sentence_rules": {"median_words_default": [lo, hi],
                           "max_sentence_words": max_sentence},
        "pov": pov,
        "pov_note": pov_note,
        "reader_address": "bạn" if fp["reader_rate"] >= 0.8 else None,
        "formatting_notes": notes,
        "fingerprint_summary": {
            "sentence_median": median, "sentence_stdev": fp["sentence_len"]["stdev"],
            "first_person_rate": fp_rate, "reader_rate": fp["reader_rate"],
            "exclamation_rate": excl, "question_rate": fp["question_rate"],
            "number_density": fp["number_density"],
            "word_count": fp["word_count"]},
    }


# ---------------------------------------------------------------------------
# Vocabulary candidates
# ---------------------------------------------------------------------------

def _capitalized_names(pages: list[dict]) -> list[str]:
    """Capitalized multiword names from page titles + headings, exact
    casing preserved ("Đỉnh Cao Gold Blend"). Generic furniture words are
    trimmed from the edges; sequences made only of them are dropped."""
    found: list[str] = []
    for p in pages or []:
        texts = [p.get("title", "")] + list(p.get("headings", []) or [])
        for text in texts:
            tokens = text.replace("|", " ").split()
            run: list[str] = []

            def flush(run=run):
                trimmed = list(run)
                while trimmed and trimmed[0] in _GENERIC_NAME_WORDS:
                    trimmed.pop(0)
                while trimmed and trimmed[-1] in _GENERIC_NAME_WORDS:
                    trimmed.pop()
                # Model-code runs are not names (review L13): a
                # heading listing grinder models ("W180 W240 W320")
                # produced a 'brand name' made of pure letter+digit
                # codes. A candidate whose EVERY token is a code is
                # equipment, not vocabulary.
                if len(trimmed) >= 2 and not all(
                        re.fullmatch(r"[A-ZÀ-Ỹ]?\d+", t) for t in trimmed):
                    name = " ".join(trimmed)
                    if name not in found:
                        found.append(name)

            for tok in tokens:
                clean = tok.strip(",.;:()")
                if clean and _CAP_TOKEN_RE.match(clean) and clean[0].isupper():
                    run.append(clean)
                else:
                    flush(run)
                    run = []
            flush(run)
    return found


def extract_vocabulary(pages: list[dict]) -> dict:
    """Vocabulary candidates from the corpus.

    Heuristic (documented, deliberately simple): domain-looking terms are
    the frequent bigrams of the corpus whose syllables are all content
    words (not stopwords, not generic web/boilerplate words, no digits) —
    in Vietnamese most domain terms are two-syllable compounds ("cà phê",
    "rang xay", "hậu vị"). `use` takes the top bigrams; the remaining
    frequent content words become glossary candidates for the user to
    define. `exact` is the brand name plus capitalized product-like names
    from titles/headings, casing preserved."""
    corpus = site_corpus(pages)
    text = corpus["all_text"]
    tokens = _tokens(text)

    def content(tok: str) -> bool:
        return (tok not in STOPWORDS and tok not in _GENERIC_WEB_WORDS
                and not tok.isdigit() and len(tok) >= 2)

    bigrams: Counter = Counter()
    for a, b in zip(tokens, tokens[1:]):
        if content(a) and content(b):
            bigrams[f"{a} {b}"] += 1
    use = [{"term": t, "count": n, "instead_of": ""}
           for t, n in bigrams.most_common(8) if n >= 2]

    used = {u["term"] for u in use}
    singles: Counter = Counter(t for t in tokens if content(t))
    glossary = [{"term": t, "count": n} for t, n in singles.most_common(20)
                if t not in " ".join(used).split()][:10]

    kit = extract_brand_kit(pages)
    exact: list[str] = []
    if kit["brand_name"]["value"]:
        exact.append(kit["brand_name"]["value"])
    for name in _capitalized_names(pages):
        if name not in exact:
            exact.append(name)
    return {"use": use, "exact": exact[:8], "glossary_candidates": glossary}


# ---------------------------------------------------------------------------
# The site's own claims — review material, never approved claims
# ---------------------------------------------------------------------------

def _claim_excerpt(sentence: str, phrase: str) -> str:
    """The stored provenance text for a claim hit (review M15): a
    plain sentence is stored as-is (<= 200 chars), but a FLATTENED
    TABLE sentence used to be stored as mangled table prose truncated
    mid-cell — unreadable as review evidence. For pipe-flattened
    sentences, store the CELL containing the claim; for other
    over-long sentences (tables flattened without separators), store
    a window centred on the claim phrase."""
    text = sentence.strip()
    if "|" in text:
        phrase_norm = normalize_for_match(phrase)
        cells = [c.strip() for c in text.split("|") if c.strip()]
        for cell in cells:
            if phrase_norm and phrase_norm in normalize_for_match(cell):
                return cell[:200]
        return text[:200]
    if len(text) > 200:
        idx = text.lower().find(phrase.lower())
        if idx >= 0:
            start = max(0, idx - 60)
            return ("…" if start else "") + text[start:start + 200]
    return text[:200]


def find_site_claims(pages: list[dict]) -> list[dict]:
    """Sentences on the site containing superlative/claim phrases (the
    fact_check CLAIM_PHRASES + the house prohibited-claim patterns). Each
    hit: {claim (the sentence), phrase (what matched), source_url,
    risk: "needs_evidence"}. These go to the draft's review list — a site
    asserting something does not make it an approved brand claim."""
    patterns = _claim_patterns()
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for p in pages or []:
        for sentence in split_sentences(p.get("text", "")):
            # Same matcher as fact_check: normalized (diacritics-free copy
            # still counts), digit-boundary guarded ("số 12" is not a
            # claim), negation-aware (a denied claim is not a claim).
            detail = find_claim_detail(sentence, patterns)
            if not detail:
                continue
            hit, start, norm = detail
            # A claim inside a QUESTION the site is asking ("Cà phê
            # rang xong bao lâu thì uống ngon nhất" — a heading, not
            # an assertion) is not a site claim (review M15).
            if any(marker in norm[:start][-48:]
                   for marker in _QUESTION_MARKERS_NORM):
                continue
            claim = _claim_excerpt(sentence, hit)
            key = (claim, p.get("url", ""))
            if key not in seen:
                seen.add(key)
                out.append({"claim": claim, "phrase": hit,
                            "source_url": p.get("url", ""),
                            "risk": "needs_evidence"})
    return out


# ---------------------------------------------------------------------------
# Draft building — code validates the qualitative schema like style.py
# ---------------------------------------------------------------------------

def _validate_qualitative(qualitative: dict | None) -> dict:
    q = qualitative or {}
    out: dict = {}
    for field in QUAL_FIELDS:
        value = q.get(field)
        if field == "tone_traits":
            traits = []
            for item in value or []:
                if isinstance(item, dict):
                    traits.append({"dimension": str(item.get("dimension", "")),
                                   "position": str(item.get("position", "")),
                                   "do": str(item.get("do", "")),
                                   "dont": str(item.get("dont", ""))})
                elif item:
                    traits.append({"dimension": str(item), "position": "",
                                   "do": "", "dont": ""})
            out[field] = traits
        elif field in ("audience_guess", "positioning"):
            out[field] = str(value or "")
        else:  # differentiators
            out[field] = [str(v) for v in (value or [])]
    return out


def _example_passages(pages: list[dict], limit: int = 3) -> list[dict]:
    """Candidate example passages (<=120 words) from the site's own pages:
    paragraphs (or sentence groups) of 30-120 words, first good ones first,
    each with its source_url."""
    out: list[dict] = []
    for p in pages or []:
        text = p.get("text", "")
        paragraphs = [x.strip() for x in re.split(r"\n\s*\n", text) if x.strip()]
        if len(paragraphs) < 2:
            sentences = split_sentences(text)
            paragraphs = [" ".join(sentences[i:i + 4])
                          for i in range(0, len(sentences), 4)]
        for para in paragraphs:
            words = para.split()
            if 30 <= len(words) <= 120:
                out.append({"text": para, "source_url": p.get("url", "")})
                break  # one passage per page keeps the set varied
        if len(out) >= limit:
            break
    return out


def build_brand_draft(pages: list[dict], qualitative: dict | None = None,
                      source_type: str = "own_site",
                      brand_name: str | None = None) -> dict:
    """Build the DRAFT brand profile from fetched pages.

    The returned dict follows data/brand_mocbrew.yaml's structure (brand /
    voice / examples / anti_examples / claims_and_guardrails) plus the
    draft extensions: vocabulary, brand_kit, qualitative, review_notes —
    and, for competitors, a top-level competitor_warning. It is a draft:
    brand.review_status == "draft" until a human approves it."""
    if source_type not in ("own_site", "competitor"):
        raise ValueError(f"source_type must be 'own_site' or 'competitor', got {source_type!r}")
    pages = [p for p in pages or [] if p]
    corpus = site_corpus(pages)
    kit = extract_brand_kit(pages)
    name = brand_name or kit["brand_name"]["value"] or "Brand"
    suggestions = suggest_voice_rules(corpus["all_text"], brand_name=name)
    vocab = extract_vocabulary(pages)
    claims = find_site_claims(pages)
    qual = _validate_qualitative(qualitative)

    # prohibited_claims starts EMPTY (calibration F3): a brand's
    # prohibitions are its OWN legal/editorial decisions. Drafts used to
    # silently inherit the house brand's (Mộc Brew's) list — a new brand
    # must never be governed by another brand's bans. The generic
    # requires_evidence guardrails ("mọi số liệu", ...) are not claims
    # and stay as the default review checklist.
    try:
        default_requires = list(load_brand().get("claims_and_guardrails", {})
                                .get("requires_evidence", _DEFAULT_REQUIRES_EVIDENCE))
    except Exception:
        default_requires = _DEFAULT_REQUIRES_EVIDENCE

    is_competitor = source_type == "competitor"
    examples = []
    for ex in _example_passages(pages):
        entry = {"format": "site_copy", "text": ex["text"],
                 "source_url": ex["source_url"],
                 "why_it_works": "đoạn mẫu lấy từ chính site — user duyệt trước khi dùng"}
        if is_competitor:
            entry["reference_only"] = True
        examples.append(entry)

    review_notes = [
        "Đây là bản DRAFT trích xuất tự động — chưa phải brand chính thức. "
        "User sửa và duyệt trước khi dùng.",
        "banned_terms đang TRỐNG: từ cấm không suy ra được từ site (site "
        "không bao giờ tự nói mình cấm gì) — user phải tự thêm.",
        "prohibited_claims đang TRỐNG: claim cấm là quyết định của TỪNG "
        "brand — draft KHÔNG kế thừa danh sách cấm của bất kỳ brand nào "
        "khác; user tự đặt theo rủi ro pháp lý/nội dung của brand mình "
        "(các claim site đang dùng nằm ở site_claims_to_review để duyệt).",
        "voice.traits do model đề xuất (qualitative) — cần user duyệt từng "
        "trait, không mặc định đúng.",
        f"pov '{suggestions['pov']}' và sentence_rules được SUY từ số đo "
        f"văn phong của site ({suggestions['pov_note']}) — xác nhận lại "
        f"trước khi chốt.",
    ]
    review_notes += suggestions["formatting_notes"]
    if claims:
        review_notes.append(
            f"site có {len(claims)} claim cần kiểm chứng bằng chứng — xem "
            f"claims_and_guardrails.site_claims_to_review; KHÔNG claim nào "
            f"được duyệt sẵn.")
    if corpus["total_words"] < 800:
        review_notes.append(
            f"corpus chỉ có {corpus['total_words']} từ — nên fetch thêm "
            f"trang (blog/sản phẩm) để hồ sơ tin cậy hơn.")
    competitor_warning = None
    if is_competitor:
        competitor_warning = (
            "COMPETITOR SITE: hồ sơ này chỉ để PHÂN TÍCH đối thủ (traits, "
            "định vị, khoảng trống). Mọi example đều là reference_only — "
            "phải thay/diễn đạt lại bằng lời của brand mình trước khi "
            "duyệt; draft_to_brand_yaml sẽ TỪ CHỐI chuyển đổi khi còn "
            "example reference_only.")
        review_notes.append(competitor_warning)

    draft = {
        "brand": {
            "id": _slugify(name),
            "name": name,
            "simulated": False,
            "version": 0,
            "review_status": "draft",
            "source_type": source_type,
            "source_urls": [p.get("url", "") for p in pages],
        },
        "voice": {
            "traits": qual["tone_traits"],
            "pov": suggestions["pov"],
            "reader_address": suggestions["reader_address"],
            "sentence_rules": {
                "median_words_default": suggestions["sentence_rules"]["median_words_default"],
                "median_words_by_format": {},
                "max_sentence_words": suggestions["sentence_rules"]["max_sentence_words"],
            },
            "formatting_rules": [],
            "banned_terms": [],
            "prohibited_claims": [],
        },
        "vocabulary": {
            "use": vocab["use"],
            "exact": vocab["exact"],
            "banned": [],
            "glossary": vocab["glossary_candidates"],
        },
        "examples": examples,
        "anti_examples": [],
        "claims_and_guardrails": {
            "requires_evidence": default_requires,
            "prohibited_claims": [],
            "unsupported_claim_marker": "[NEEDS SOURCE]",
            "site_claims_to_review": claims,
        },
        "brand_kit": kit,
        "qualitative": qual,
        "review_notes": review_notes,
    }
    if competitor_warning:
        draft["competitor_warning"] = competitor_warning
    return draft


# ---------------------------------------------------------------------------
# Conversion — draft -> approved brand YAML (with the competitor guard)
# ---------------------------------------------------------------------------

_DRAFT_ONLY_BRAND_KEYS = ("review_status", "source_type", "source_urls")


#: Generic font families / CSS-wide keywords — mirrors the fetch
#: layer's _GENERIC_FAMILIES (fetch_site.py): valid font-family values
#: that name no actual family and carry no brand signal.
_GENERIC_FONT_FAMILIES = {"inherit", "initial", "unset", "revert",
                          "sans-serif", "serif", "monospace", "cursive",
                          "fantasy", "system-ui", "ui-sans-serif",
                          "ui-serif", "ui-monospace", "ui-rounded",
                          "-apple-system", "blinkmacsystemfont",
                          "emoji", "math", "fangsong"}


def _is_brand_family(name: str) -> bool:
    """Mirror of fetch_site._is_brand_family (review L10): the
    sanitizer claimed to 're-apply the extraction rules' but only
    dropped var(--…) — generic families like 'sans-serif'/'inherit'
    passed conversion although extraction would have rejected them."""
    low = str(name or "").strip().lower()
    return bool(low) and "var(" not in low and low not in _GENERIC_FONT_FAMILIES


def _logo_filename_ok(value: str) -> bool:
    """The approved-logo rule (review L10): 'logo' must be a token of
    the FILENAME — the old rule ('logo' anywhere in the path) kept a
    banner living under a 'logo-ish/' directory as the brand logo."""
    filename = urllib.parse.urlparse(str(value)).path.rsplit("/", 1)[-1].lower()
    return bool(re.search(r"(^|[-_.])logo", filename))


def _sanitize_brand_kit(kit: dict) -> dict:
    """The kit that lands in an APPROVED brand file (calibration F9):
    conversion used to pass the draft's kit through untouched, baking
    extraction artefacts (a blog-thumbnail 'logo', a var(--…) 'font')
    into the approved brand. Conversion now re-applies the extraction
    rules to whatever kit the (reviewed) draft carries:
    - fonts: CSS variable references AND generic families are dropped
      (the extraction rule, fetch_site._is_brand_family — review L10);
    - logo_url: kept only when the URL's FILENAME carries 'logo' as a
      token (review L10) — otherwise nulled, because an approved brand
      must not point at an article thumbnail or a directory name.
    Everything else (name, colors, theme, og_image, source_urls) passes
    through exactly as reviewed."""
    kit = dict(kit or {})
    fonts = [f for f in kit.get("fonts", [])
             if _is_brand_family(f.get("family", ""))]
    if fonts != kit.get("fonts"):
        kit["fonts"] = fonts
    logo = dict(kit.get("logo_url") or {})
    value = logo.get("value")
    if value and not _logo_filename_ok(str(value)):
        logo["value"] = None
        kit["logo_url"] = logo
    return kit


def _blocks_conversion(entry: dict, competitor_draft: bool) -> bool:
    """True when an example/anti-example entry still carries
    competitor copy into conversion (review M10). Two ways to block:
    - the entry is marked reference_only with ANY truthy value
      (True, "yes", 1 — the old check's truthiness is kept for marks);
    - the draft is a COMPETITOR draft and the entry carries a
      reference_only key whose value is not an explicit boolean
      False: a PRESENT-but-falsy spelling (0, "", None) is an
      ambiguous marking, not a clearance, and still blocks. The two
      clearances are the documented ones: remove the key after
      replacing the passage (the original flow), or set it to False
      explicitly."""
    flag = entry.get("reference_only")
    if flag:
        return True
    return (competitor_draft and "reference_only" in entry
            and flag is not False)


def _reference_only_entries(draft: dict) -> list[dict]:
    """Every entry the conversion guard inspects: examples AND
    anti-examples (review M10: the guard used to look only at
    `examples` — competitor text parked in anti_examples[].bad
    converted untouched), including dicts nested in an anti-example's
    `bad` list."""
    competitor = (draft.get("brand", {}).get("source_type") == "competitor"
                  or "competitor_warning" in draft)
    entries = list(draft.get("examples", []))
    for anti in draft.get("anti_examples", []):
        if isinstance(anti, dict):
            entries.append(anti)
            bad = anti.get("bad")
            if isinstance(bad, list):
                entries.extend(e for e in bad if isinstance(e, dict))
    return [e for e in entries if _blocks_conversion(e, competitor)]


def draft_to_brand_yaml(draft: dict) -> str:
    """Convert an EDITED draft into brand YAML in brand_mocbrew.yaml's
    structure, minus draft-only fields (brand.review_status/source_type/
    source_urls, review_notes, claims_and_guardrails.site_claims_to_review,
    qualitative working notes).

    REFUSES (ValueError) while any example/anti-example still carries
    competitor copy (see _blocks_conversion): competitor text must be
    replaced/paraphrased by the user first, and the passage's flag
    then removed (or set to an explicit false).

    The draft's `visual` section (the article-images style lock) is
    carried through, validated via loaders.validate_visual (trial
    bug 3: conversion used to drop it silently, so a brand approved
    via YAML lost the style lock its draft carried). An invalid
    visual raises the validator's ValueError NAMING the field —
    failing loudly beats approving a brand whose images quietly lost
    their style."""
    ref_only = _reference_only_entries(draft)
    if ref_only:
        raise ValueError(
            f"draft still contains {len(ref_only)} reference_only example(s) "
            f"from a competitor site — replace or paraphrase them with the "
            f"brand's own words and remove the reference_only flag before "
            f"converting to an approved brand")

    brand_in = draft.get("brand", {})
    brand = {"id": brand_in.get("id", "brand"), "name": brand_in.get("name", ""),
             "simulated": False, "version": 1}
    voice_in = draft.get("voice", {})
    guard_in = draft.get("claims_and_guardrails", {})
    rules_in = voice_in.get("sentence_rules", {})
    # prohibited_claims lives in TWO places in a draft (voice + the
    # guardrails block); conversion takes the UNION (review M11): a
    # claim the user added only to claims_and_guardrails.prohibited_
    # claims used to vanish from the approved brand silently.
    prohibited: list = []
    for claim in list(voice_in.get("prohibited_claims", [])) \
            + list(guard_in.get("prohibited_claims", [])):
        if claim not in prohibited:
            prohibited.append(claim)
    voice = {
        "traits": voice_in.get("traits", []),
        "pov": voice_in.get("pov", ""),
        "reader_address": voice_in.get("reader_address"),
        "sentence_rules": {
            "median_words_default": rules_in.get("median_words_default", [11, 14]),
            "median_words_by_format": rules_in.get("median_words_by_format", {}),
            "max_sentence_words": rules_in.get("max_sentence_words", 25),
        },
        "formatting_rules": voice_in.get("formatting_rules", []),
        "banned_terms": voice_in.get("banned_terms", []),
        "prohibited_claims": prohibited,
    }
    examples = []
    for e in draft.get("examples", []):
        # Provenance survives conversion (F9): an approved brand keeps
        # the source_url each approved example came from.
        entry = {"format": e.get("format", "site_copy"), "text": e.get("text", ""),
                 "why_it_works": e.get("why_it_works", "")}
        if e.get("source_url"):
            entry["source_url"] = e["source_url"]
        examples.append(entry)
    clean = {
        "brand": brand,
        "voice": voice,
        "vocabulary": draft.get("vocabulary",
                                {"use": [], "exact": [], "banned": [], "glossary": []}),
        "examples": examples,
        "anti_examples": draft.get("anti_examples", []),
        "claims_and_guardrails": {
            "requires_evidence": guard_in.get("requires_evidence",
                                               _DEFAULT_REQUIRES_EVIDENCE),
            "unsupported_claim_marker": guard_in.get("unsupported_claim_marker",
                                                     "[NEEDS SOURCE]"),
        },
        "brand_kit": _sanitize_brand_kit(draft.get("brand_kit", {})),
    }
    if draft.get("visual") is not None:
        clean["visual"] = validate_visual(draft["visual"])
    if draft.get("image_sources") is not None:
        # The searched-image allowlist rides along with visual (same
        # silent-drop hazard as trial bug 3): an approved brand must
        # not lose the allowlist its draft carried.
        clean["image_sources"] = validate_image_sources(
            draft["image_sources"])
    return yaml.safe_dump(clean, allow_unicode=True, sort_keys=False,
                          default_flow_style=False)


# ---------------------------------------------------------------------------
# Compare mode — my site vs a competitor, from the numbers
# ---------------------------------------------------------------------------

def _fp_metrics(fp: dict) -> dict:
    return {"sentence_median": fp["sentence_len"]["median"],
            "sentence_stdev": fp["sentence_len"]["stdev"],
            "short_sentence_share": fp["short_sentence_share"],
            "first_person_rate": fp["first_person_rate"],
            "reader_rate": fp["reader_rate"],
            "number_density": fp["number_density"],
            "exclamation_rate": fp["exclamation_rate"],
            "question_rate": fp["question_rate"]}


def _gaps(m: dict, claims_a: int, claims_b: int,
          label_a: str, label_b: str, vocab: dict) -> list[str]:
    """Observations computed from actual deltas (thresholds documented):
    first-person ratio >= 2x AND abs diff >= 0.5/100w; claims abs diff >= 3;
    sentence median abs diff >= 3 words; burstiness ratio >= 1.5x;
    exclamation abs diff >= 5/100 sentences; non-empty vocabulary split."""
    gaps: list[str] = []

    def side(name: str) -> str:
        return label_a if name == "a" else label_b

    fa, fb = m["first_person_rate"]["a"], m["first_person_rate"]["b"]
    hi, lo = max(fa, fb), min(fa, fb)
    if lo > 0 and hi / lo >= 2 and hi - lo >= 0.5:
        gaps.append(f"{side('a' if fa > fb else 'b')} uses first-person "
                    f"{hi / lo:.1f}x more than "
                    f"{side('b' if fa > fb else 'a')} "
                    f"({hi} vs {lo} per 100 words) — a warmer, more personal voice")
    elif hi - lo >= 0.5:
        gaps.append(f"first-person usage differs: {label_a} {fa} vs "
                    f"{label_b} {fb} per 100 words")

    if abs(claims_a - claims_b) >= 3:
        more, fewer = (("a", "b") if claims_a > claims_b else ("b", "a"))
        gaps.append(f"{side(more)} has {max(claims_a, claims_b)} superlative/"
                    f"evidence-requiring claims, {side(fewer)} has "
                    f"{min(claims_a, claims_b)} — {side(fewer)} is more restrained")

    med_a, med_b = m["sentence_median"]["a"], m["sentence_median"]["b"]
    if abs(med_a - med_b) >= 3:
        gaps.append(f"sentence length differs: {label_a} median {med_a} words vs "
                    f"{label_b} median {med_b} words — "
                    f"{label_a if med_a < med_b else label_b} reads punchier")

    sa, sb = m["sentence_stdev"]["a"], m["sentence_stdev"]["b"]
    if min(sa, sb) > 0 and max(sa, sb) / min(sa, sb) >= 1.5:
        gaps.append(f"rhythm burstiness differs: {label_a} stdev {sa} vs "
                    f"{label_b} stdev {sb} — "
                    f"{label_a if sa > sb else label_b} varies sentence length more")

    ea, eb = m["exclamation_rate"]["a"], m["exclamation_rate"]["b"]
    if abs(ea - eb) >= 5:
        gaps.append(f"exclamation usage differs sharply: {label_a} {ea} vs "
                    f"{label_b} {eb} per 100 sentences — "
                    f"{label_a if ea > eb else label_b} shouts, "
                    f"{label_b if ea > eb else label_a} stays calm")

    if vocab["a_only"] and vocab["b_only"]:
        gaps.append(f"vocabulary differs: {label_a} leans on "
                    f"{[w['word'] for w in vocab['a_only'][:5]]}, "
                    f"{label_b} leans on {[w['word'] for w in vocab['b_only'][:5]]}")
    return gaps


def compare_brand_voice(pages_a: list[dict], pages_b: list[dict]) -> dict:
    """Contrast two sites' voices (typically: mine vs a competitor).

    Side-by-side fingerprints for the 6 style_match metrics (+ sentence
    median/stdev and punctuation rates), vocabulary symmetric difference,
    claim counts, brand kits, and `gaps` — observations generated in code
    from the measured deltas, never hardcoded prose."""
    corpus_a, corpus_b = site_corpus(pages_a), site_corpus(pages_b)
    # Voice is compared on the PROSE of each site (trial bug 2):
    # chrome-inflated sentence statistics would compare the two
    # sites' page furniture, not their voices.
    fp_a = fingerprint(clean_corpus_text(corpus_a["all_text"]))
    fp_b = fingerprint(clean_corpus_text(corpus_b["all_text"]))
    ma, mb = _fp_metrics(fp_a), _fp_metrics(fp_b)
    metrics = {k: {"a": ma[k], "b": mb[k], "delta": round(mb[k] - ma[k], 2)}
               for k in ma}

    words_a = {w["word"] for w in fp_a["top_words"]}
    words_b = {w["word"] for w in fp_b["top_words"]}
    vocab = {
        "a_only": [{"word": w["word"], "count": w["count"]}
                   for w in fp_a["top_words"] if w["word"] not in words_b][:10],
        "b_only": [{"word": w["word"], "count": w["count"]}
                   for w in fp_b["top_words"] if w["word"] not in words_a][:10],
    }

    kit_a, kit_b = extract_brand_kit(pages_a), extract_brand_kit(pages_b)
    label_a = kit_a["brand_name"]["value"] or "Site A"
    label_b = kit_b["brand_name"]["value"] or "Site B"
    claims_a = find_site_claims(pages_a)
    claims_b = find_site_claims(pages_b)

    return {
        "labels": {"a": label_a, "b": label_b},
        "corpus": {"a": {"pages": corpus_a["page_count"], "words": corpus_a["total_words"]},
                   "b": {"pages": corpus_b["page_count"], "words": corpus_b["total_words"]}},
        "fingerprint": {"a": fp_a, "b": fp_b},
        "metrics": metrics,
        "sentence": {"median": {"a": ma["sentence_median"], "b": mb["sentence_median"]},
                     "stdev": {"a": ma["sentence_stdev"], "b": mb["sentence_stdev"]}},
        "vocabulary": vocab,
        "claims": {"a": len(claims_a), "b": len(claims_b),
                   "a_items": claims_a, "b_items": claims_b},
        "brand_kit": {"a": kit_a, "b": kit_b},
        "gaps": _gaps(metrics, len(claims_a), len(claims_b), label_a, label_b, vocab),
    }
