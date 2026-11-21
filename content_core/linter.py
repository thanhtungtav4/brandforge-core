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

# Invisible format characters stripped BEFORE matching (evaluation R2):
# copy pasted from rich editors smuggles these INSIDE words to break the
# literal match — "ele\u200bvate" (zero-width space), soft hyphens, BOMs,
# zero-width joiners. They carry no meaning; removing them is safe.
_FORMAT_CHARS = ["\u200b", "\u200c", "\u200d", "\ufeff", "\u00ad"]

# Latin confusables (evaluation R2): Cyrillic/Greek letters that LOOK
# Latin and get pasted (or typed) in place of them — "elev\u0430te" with
# a Cyrillic а reads identically to "elevate" but never matched. A small,
# documented map folds the common look-alikes back to Latin. Applied
# AFTER diacritics stripping, so Vietnamese text (already folded to
# a-z) is untouched.
_CONFUSABLES = {
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x",
    "у": "y", "к": "k", "м": "m", "н": "h", "т": "t", "ѕ": "s",
    "і": "i", "ј": "j", "ԁ": "d", "ɡ": "g", "ο": "o", "α": "a",
    "ε": "e", "ι": "i", "κ": "k", "μ": "m", "ν": "v", "ρ": "p",
    "τ": "t", "χ": "x", "υ": "u", "β": "b", "һ": "h", "ḅ": "b",
}


def normalize_for_match(text: str) -> str:
    """Lowercase, strip Vietnamese diacritics, collapse punctuation to
    single spaces. Digits are kept. 'Đẳng cấp!' -> 'dang cap'.

    Hardening (evaluation R2): zero-width/format characters are removed
    first, the text is NFKC-folded (fullwidth 'ｅｌｅｖａｔｅ' ->
    'elevate'), and Latin confusables (Cyrillic/Greek look-alikes) are
    folded to Latin — the three cheapest banned-term evasions."""
    lowered = (text or "").lower()
    for ch in _FORMAT_CHARS:
        lowered = lowered.replace(ch, "")
    lowered = lowered.replace("đ", "d")
    folded = unicodedata.normalize("NFKC", lowered)
    decomposed = unicodedata.normalize("NFD", folded)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    mapped = "".join(_CONFUSABLES.get(c, c) for c in stripped)
    return re.sub(r"[^a-z0-9]+", " ", mapped).strip()


def _phrase_regex(phrase: str) -> re.Pattern | None:
    """Regex for a phrase on normalized text. Digit-boundary guards are
    applied ONLY on the side where the phrase itself starts/ends with a
    digit: they exist so digit-ending phrases ('số 1') don't match
    inside ordinary numbers ('số 12'), but applied to every phrase they
    became an evasion — 'elevate7' and '7elevate' slipped the ban
    (evaluation R2). Letter phrases now match with digits glued on."""
    norm = normalize_for_match(phrase)
    if not norm:
        return None
    left = r"(?<!\d)" if norm[0].isdigit() else ""
    right = r"(?!\d)" if norm[-1].isdigit() else ""
    return re.compile(left + re.escape(norm) + right)


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


# A claim/term immediately preceded by one of these negations (optionally
# followed by "la") is a DENIAL, not an assertion: "không phải là tốt
# nhất" must not be treated as claiming "tốt nhất". Narrow on purpose —
# "không chỉ tốt nhất" (which affirms) is not in the list. Defined here
# and shared with fact_check (which imports it) so the two checkers can
# never drift apart on what counts as a negation.
NEGATED_PREFIX_RE = re.compile(
    r"(khong phai|chang phai|dau phai|khong nhan|khong dam nhan)( la)?$")

_QUOTE_OPEN = {"«", "“"}
_QUOTE_CLOSE = {"»", "”"}


def _normalize_indexed(text: str) -> tuple[str, list[bool]]:
    """normalize_for_match WITH a per-character quote map: returns
    (normalized_text, flags) where flags[i] says whether normalized
    character i sat inside quotation marks («…», “…” or "…") in the
    raw text. Used to tell an ASSERTED banned term from a QUOTED one
    (item: linter fairness — a brand quoting a customer's hype is not
    itself hyping). The normalized string is built with the same steps
    as normalize_for_match; callers verify equality and fall back to
    'not quoted' when the two ever disagree."""
    out: list[tuple[str, bool]] = []
    in_quote = False
    pending_space_flag: bool | None = None
    for ch in text or "":
        if ch in _QUOTE_OPEN:
            in_quote = True
            if pending_space_flag is None:
                pending_space_flag = in_quote
            continue
        if ch in _QUOTE_CLOSE:
            if pending_space_flag is None:
                pending_space_flag = in_quote
            in_quote = False
            continue
        if ch == '"':
            if pending_space_flag is None:
                pending_space_flag = in_quote
            in_quote = not in_quote
            continue
        low = ch.lower()
        if low in _FORMAT_CHARS:
            continue
        low = unicodedata.normalize(
            "NFD", unicodedata.normalize("NFKC", low.replace("đ", "d")))
        for c in low:
            if unicodedata.combining(c):
                continue
            c = _CONFUSABLES.get(c, c)
            if c.isalnum() and c.isascii() and (c.isalpha() or c.isdigit()):
                if pending_space_flag is not None and out:
                    out.append((" ", pending_space_flag))
                pending_space_flag = None
                out.append((c, in_quote))
            elif pending_space_flag is None:
                pending_space_flag = in_quote
    normalized = "".join(c for c, _ in out)
    return normalized, [f for _, f in out]


def _banned_hit_is_context_only(norm: str, span: tuple[int, int],
                                quote_flags: list[bool] | None) -> bool:
    """True when a banned-term occurrence inside ONE sentence is a
    MENTION, not a use (linter fairness, evaluation R2):
    - it is immediately preceded by a negation (the shared
      NEGATED_PREFIX_RE: 'không nhận là tốt nhất Việt Nam'), or
    - it sits inside quotation marks in the raw sentence (a quoted
      customer/competitor line: 'khách khen “tốt nhất Việt Nam”').
    Bounded on purpose: attribution without quote marks and negations
    further away than the immediate prefix still count as assertions.
    `norm`/`span` are normalized-text coordinates; `quote_flags` is the
    _normalize_indexed map for the same sentence, or None when the map
    could not be aligned (then only the negation rule applies)."""
    start, end = span
    if NEGATED_PREFIX_RE.search(norm[:start].rstrip()):
        return True
    if quote_flags and all(quote_flags[start:end]):
        return True
    return False


def word_count(text: str) -> int:
    return len(text.split())


def sentence_lengths(text: str) -> list[int]:
    return [len(s.split()) for s in split_sentences(text)]


def is_table_like(segment: str) -> bool:
    """A flattened table row, not prose (calibration F6 — a Robusta/
    Arabica comparison table became one 69-word 'sentence' and poisoned
    the median/max statistics).

    NARROWED (evaluation R2): the old rule — any segment with >= 2 '|'
    — let an ordinary 60-word sentence dodge every length rule just by
    containing two pipes (100% evasion). A segment now counts as
    table-like only when it really looks like a table row:
    - it is a markdown separator row (contains a |---| run), or
    - it has >= 3 separators (4+ cells — real flattened tables have
      many cells; prose sentences with 3+ pipes are vanishingly rare), or
    - it has exactly 2 separators AND its cells are short (median cell
      <= 6 words, longest cell <= 12) — a 3-column row of labels/values,
      not three clauses of a sentence."""
    pipes = segment.count("|")
    if pipes < 2:
        return False
    if re.search(r"\|\s*:?-{2,}|\s*:?-{2,}\s*\|", segment):
        return True
    if pipes >= 3:
        return True
    cells = [c.strip() for c in segment.split("|") if c.strip()]
    if len(cells) < 3:
        return False
    counts = [len(c.split()) for c in cells]
    return statistics.median(counts) <= 6 and max(counts) <= 12


def stat_sentence_lengths(text: str) -> list[int]:
    """Sentence lengths for STATISTICS (median / max checks): table-like
    segments are excluded — they are page furniture, not sentences the
    writer wrote. Banned-term and punctuation checks still scan the
    full text; only the length statistics skip table runs."""
    return [len(s.split()) for s in split_sentences(text)
            if not is_table_like(s)]


def lint_section(text: str, fmt: str, brand: dict, section_id: str = "") -> tuple[list[dict], float]:
    """Return (violations, score 0-100) for one section.

    Penalty table (evaluation R2 rebalance):
    - banned term ASSERTED: -15 per hit (hard gate counts these).
    - banned term MENTIONED (negated clause or inside quotation marks):
      reported as a 'banned_term_context' WARNING — no score penalty,
      not counted by the hard gate. A brand denying or quoting hype is
      not itself hyping; asserted use stays fully hard.
    - exclamation in blog: -5 per mark, CAPPED at -15 per section (one
      bad paragraph used to be able to zero a whole section by itself).
    - median sentence length out of band: -10. The median is computed
      over sentences WITHIN the max length only (monotonicity fix,
      evaluation R5): an over-long sentence is already fined on its own;
      letting it also drag the median let a draft game a FAIL into a
      PASS by appending one huge sentence.
    - sentence too long: -2 base, -1 per full 10 words over the cap,
      max -10 (a 26-word and a 200-word sentence used to cost the same).
    """
    violations: list[dict] = []
    score = 100.0

    # Banned terms: normalized matching (diacritics/punctuation-insensitive)
    # so "dang cap the gioi" or "game changing" cannot evade the ban; the
    # reported detail stays the brand's own term string. Occurrences are
    # classified per sentence: asserted use -> hard violation; negated or
    # quoted mention -> warning (see _banned_hit_is_context_only).
    sentences = split_sentences(text)
    for term in banned_terms(brand):
        rx = _phrase_regex(term)
        if rx is None:
            continue
        for sentence in sentences:
            norm = normalize_for_match(sentence)
            indexed = _normalize_indexed(sentence)
            quote_flags = indexed[1] if indexed[0] == norm else None
            for m in rx.finditer(norm):
                if _banned_hit_is_context_only(norm, (m.start(), m.end()), quote_flags):
                    violations.append({"section_id": section_id,
                                       "type": "banned_term_context",
                                       "detail": term, "warning": True})
                else:
                    violations.append({"section_id": section_id,
                                       "type": "banned_term", "detail": term})
                    score -= 15

    if fmt == "blog":
        n = text.count("!") + text.count("！")  # ASCII + fullwidth
        if n:
            violations.append({"section_id": section_id, "type": "exclamation_in_blog", "detail": f"{n} exclamation mark(s)"})
            score -= min(15, 5 * n)

    lengths = stat_sentence_lengths(text)
    if lengths:
        max_words = int(brand["voice"]["sentence_rules"]["max_sentence_words"])
        within = [ln for ln in lengths if ln <= max_words]
        median = statistics.median(within if within else lengths)
        lo, hi = median_range(brand, fmt)
        if not (lo <= median <= hi):
            violations.append({"section_id": section_id, "type": "median_sentence_length",
                               "detail": f"median {median} words, target {lo}-{hi}"})
            score -= 10
        for ln in lengths:
            if ln > max_words:
                over = ln - max_words
                violations.append({"section_id": section_id, "type": "sentence_too_long",
                                    "detail": f"{ln} words > {max_words}"})
                score -= min(10, 2 + over // 10)
    return violations, max(0.0, score)


def lint_draft(sections: list[dict], fmt: str, brand: dict) -> tuple[list[dict], float, list[str]]:
    """Lint all sections. Overall score = worst section score (a chain is its weakest section).
    A section is 'failing' when it carries a non-warning violation or scores
    below 85 — warnings alone (banned_term_context) never force a rewrite."""
    all_violations: list[dict] = []
    failing: list[str] = []
    worst = 100.0
    for sec in sections:
        v, s = lint_section(sec.get("text", ""), fmt, brand, sec.get("id", ""))
        all_violations.extend(v)
        worst = min(worst, s)
        if any(not x.get("warning") for x in v) or s < 85:
            failing.append(sec.get("id", ""))
    return all_violations, worst, failing
