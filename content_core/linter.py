"""Deterministic brand linter + scorer. Pure code — no LLM judgement.

Rules come from data/brand_mocbrew.yaml (checkable rules, not adjectives):
banned terms, no exclamation marks in blog, median sentence length per format,
max sentence length.
"""
from __future__ import annotations

import re
import statistics
import unicodedata

from .loaders import banned_terms, median_range

SENTENCE_SPLIT = re.compile(r"[.!?…！？]+")

# Placeholder for '.' inside numbers (Vietnamese thousand separators and
# decimals): "185.000đ" is ONE token, not a sentence boundary. Without this
# protection the splitter tears prices in half and corrupts every
# sentence-length metric downstream (median, rhythm).
_NUM_DOT = "§"


def _protect_numbers(text: str) -> str:
    protected = text
    while True:  # loop for multi-group numbers like 1.234.567
        new = re.sub(r"(\d)\.(\d)", rf"\1{_NUM_DOT}\2", protected)
        if new == protected:
            return protected
        protected = new


# Vietnamese abbreviations whose trailing '.' is part of the abbreviation,
# NOT a sentence boundary: "TP. Hồ Chí Minh", "ThS. Nguyễn", "Q. 1" used to
# be torn into fragments, corrupting sentence counts and every median /
# rhythm metric built on them. The set is deliberately small and
# documented: place/district (TP, Q) and academic/professional titles
# (ThS, TS, PGS, GS, BS, KS). Matching is case-sensitive (the canonical
# forms); the dot is swapped for the same placeholder as number dots and
# restored in the returned sentences. Trade-off: an abbreviation at the
# very end of a sentence ("...ở Q.") no longer ends that sentence — in
# practice these abbreviations are virtually always followed by the name
# or number they abbreviate, so protection is the safer default.
_ABBREVIATIONS = ["PGS", "ThS", "TP", "GS", "TS", "BS", "KS", "Q"]
_ABBR_DOT_RE = re.compile(r"\b(" + "|".join(_ABBREVIATIONS) + r")\.")


def _protect_abbreviations(text: str) -> str:
    return _ABBR_DOT_RE.sub(lambda m: m.group(1) + _NUM_DOT, text)


def split_sentences(text: str) -> list[str]:
    """Split on sentence punctuation WITHOUT breaking numbers like 185.000đ
    or documented abbreviations like 'TP. Hồ Chí Minh'. Protected dots are
    restored in the returned sentences, so callers see the original text,
    just correctly segmented."""
    protected = _protect_abbreviations(_protect_numbers(text))
    return [s.strip().replace(_NUM_DOT, ".")
            for s in SENTENCE_SPLIT.split(protected) if s.strip()]


# ---------------------------------------------------------------------------
# Normalized phrase matching (audit round 2)
# ---------------------------------------------------------------------------
# Banned terms and claim phrases used to be matched as raw substrings of the
# lowercased text. That let three evasion classes slip through every gate:
#   * diacritics-free Vietnamese ("dang cap the gioi", "chua mat ngu") —
#     a very common way to write Vietnamese copy;
#   * punctuation variants ("game changing" for "game-changing",
#     "100 % ..." for "100% ...");
#   * fullwidth/CJK punctuation pasted from other editors.
# Matching now happens on a normalized form: lowercase, diacritics stripped
# (đ -> d), every run of non-alphanumerics collapsed to one space. A phrase
# boundary guard ((?<!\d) ... (?!\d)) stops digit-ending phrases like
# "số 1" from matching inside ordinary numbers ("số 12", "số 185").

def normalize_for_match(text: str) -> str:
    """Lowercase, strip Vietnamese diacritics, collapse punctuation to
    single spaces. Digits are kept. 'Đẳng cấp!' -> 'dang cap'."""
    lowered = (text or "").lower().replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", lowered)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", stripped).strip()


def _phrase_regex(phrase: str) -> re.Pattern | None:
    norm = normalize_for_match(phrase)
    if not norm:
        return None
    return re.compile(r"(?<!\d)" + re.escape(norm) + r"(?!\d)")


def phrase_spans(text: str, phrase: str) -> list[tuple[int, int]]:
    """Match spans of `phrase` in the NORMALIZED form of `text`
    (diacritics/punctuation-insensitive, digit-boundary guarded)."""
    rx = _phrase_regex(phrase)
    if rx is None:
        return []
    return [(m.start(), m.end()) for m in rx.finditer(normalize_for_match(text))]


def count_phrase(text: str, phrase: str) -> int:
    return len(phrase_spans(text, phrase))


def phrase_in(text: str, phrase: str) -> bool:
    return bool(phrase_spans(text, phrase))


def word_count(text: str) -> int:
    return len(text.split())


def sentence_lengths(text: str) -> list[int]:
    return [len(s.split()) for s in split_sentences(text)]


def lint_section(text: str, fmt: str, brand: dict, section_id: str = "") -> tuple[list[dict], float]:
    """Return (violations, score 0-100) for one section."""
    violations: list[dict] = []
    score = 100.0

    # Banned terms: normalized matching (diacritics/punctuation-insensitive)
    # so "dang cap the gioi" or "game changing" cannot evade the ban; the
    # reported detail stays the brand's own term string.
    for term in banned_terms(brand):
        for _ in range(count_phrase(text, term)):
            violations.append({"section_id": section_id, "type": "banned_term", "detail": term})
            score -= 15

    if fmt == "blog":
        n = text.count("!") + text.count("！")  # ASCII + fullwidth
        if n:
            violations.append({"section_id": section_id, "type": "exclamation_in_blog", "detail": f"{n} exclamation mark(s)"})
            score -= 5 * n

    lengths = sentence_lengths(text)
    if lengths:
        median = statistics.median(lengths)
        lo, hi = median_range(brand, fmt)
        if not (lo <= median <= hi):
            violations.append({"section_id": section_id, "type": "median_sentence_length",
                               "detail": f"median {median} words, target {lo}-{hi}"})
            score -= 10
        max_words = int(brand["voice"]["sentence_rules"]["max_sentence_words"])
        for ln in lengths:
            if ln > max_words:
                violations.append({"section_id": section_id, "type": "sentence_too_long",
                                    "detail": f"{ln} words > {max_words}"})
                score -= 2
    return violations, max(0.0, score)


def lint_draft(sections: list[dict], fmt: str, brand: dict) -> tuple[list[dict], float, list[str]]:
    """Lint all sections. Overall score = worst section score (a chain is its weakest section)."""
    all_violations: list[dict] = []
    failing: list[str] = []
    worst = 100.0
    for sec in sections:
        v, s = lint_section(sec.get("text", ""), fmt, brand, sec.get("id", ""))
        all_violations.extend(v)
        worst = min(worst, s)
        if v or s < 85:
            failing.append(sec.get("id", ""))
    return all_violations, worst, failing
