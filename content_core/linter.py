"""Deterministic brand linter + scorer. Pure code — no LLM judgement.

Rules come from data/brand_mocbrew.yaml (checkable rules, not adjectives):
banned terms, no exclamation marks in blog, median sentence length per format,
max sentence length.
"""
from __future__ import annotations

import re
import statistics

from .loaders import banned_terms, median_range

SENTENCE_SPLIT = re.compile(r"[.!?…]+")

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


def split_sentences(text: str) -> list[str]:
    """Split on sentence punctuation WITHOUT breaking numbers like 185.000đ.
    The '.' inside digit groups is restored in the returned sentences, so
    callers see the original text, just correctly segmented."""
    protected = _protect_numbers(text)
    return [s.strip().replace(_NUM_DOT, ".")
            for s in SENTENCE_SPLIT.split(protected) if s.strip()]


def word_count(text: str) -> int:
    return len(text.split())


def sentence_lengths(text: str) -> list[int]:
    return [len(s.split()) for s in split_sentences(text)]


def lint_section(text: str, fmt: str, brand: dict, section_id: str = "") -> tuple[list[dict], float]:
    """Return (violations, score 0-100) for one section."""
    violations: list[dict] = []
    score = 100.0
    low = text.lower()

    for term in banned_terms(brand):
        for _ in re.finditer(re.escape(term), low):
            violations.append({"section_id": section_id, "type": "banned_term", "detail": term})
            score -= 15

    if fmt == "blog" and "!" in text:
        n = text.count("!")
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
