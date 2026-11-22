"""Layer 4 — Craft & Domain Library: prose-QUALITY machinery (deterministic).

Kept separate from the brand linter on purpose:
- linter.py scores BRAND compliance (banned terms, sentence rules) — blocking.
- craft.py scores WRITING QUALITY (vague words, rhythm, sensory detail) —
  blocking for blog/product via the shared gate in gates.py, advisory
  for the other formats.

Data (all in data/, distilled — principles paraphrased, examples original):
- craft_library.json    technique cards, lorebook-style triggered retrieval
- exemplars.json        few-shot style anchors, one per format
- domain_glossary.json  precise domain terms + weak/vague words to replace

SCORE SEMANTICS (evaluation R3-S1, stated plainly): the craft score
certifies the ABSENCE of the measured surface defects (weak words,
filler, monotone rhythm, repeated openings, missing sensory terms) —
it does NOT certify coherence, logic or genuine prose quality, and no
deterministic signal in this module can: a sensory-stuffed word salad
scores at the ceiling because it commits none of the five measured
defects. A unique-token-ratio signal was evaluated as a salad
detector in the core-completion round and REJECTED with measurements:
the salad's TTR is 0.95 at 62 words, but the project's own social
exemplar sits at 0.91 at 64 words and golden email sections reach
0.92-0.95 at 20-49 words — no threshold separates the salad from
legitimate short copy. Coherence judgement belongs to the writing
model and the human reviewer; this layer is a defect index.
"""
from __future__ import annotations

import re
import statistics
from functools import lru_cache as _lru_cache

from .linter import fuzzy_phrase_spans, normalize_for_match, split_sentences
from .loaders import load_craft_library, load_domain_glossary, load_exemplars


def _sentences(text: str) -> list[str]:
    """Sentence splitting lives in linter.split_sentences, which protects
    Vietnamese thousand separators ('185.000đ' is one token, not a sentence
    boundary) — craft reuses it so rhythm metrics see true sentences."""
    return split_sentences(text)


def _sentence_lengths(text: str) -> list[int]:
    return [len(s.split()) for s in _sentences(text)]

MAX_CARDS_PER_CALL = 3
MAX_CORE_CARDS = 1  # always-on budget: at most 1 core card per retrieval

# Gate policy (P2b): craft is a BLOCKING check for long-form formats — a
# blog/product draft scoring below CRAFT_PASS_SCORE fails the gate and
# must be rewritten for craft. For short/transactional formats (social,
# email, comparison) craft stays advisory (reported, never blocking).
# The rule itself lives in gates.py (evaluate_gate), which BOTH the MCP
# final_gate and the LangGraph graph call; MockWriter's golden output
# measures craft >= 70 for blog/product, so the graph applies the same
# blocking rule (full unification — see gates.py).
CRAFT_PASS_SCORE = 70
CRAFT_BLOCKING_FORMATS = {"blog", "product"}

# Minimum text length craft_check will judge at all. Below this many
# words there is no rhythm, no opening pattern and no room for sensory
# detail — yet the penalty-based scoring used to return ~90 for an empty
# string (nothing to penalise), i.e. "no text" scored as GOOD prose.
# 10 words is roughly one full Vietnamese sentence (the brand's own
# sentence target is 11-14 words); anything shorter is a fragment, not
# prose. Legitimate short formats are far above it (the golden social
# post is ~35 words, care-email sections ~20+). Shorter input returns
# score 0 with an explicit too_short_to_judge violation instead.
MIN_WORDS_TO_JUDGE = 10

# Stock filler phrases (vi): removing them never changes the meaning.
FILLER_PHRASES = [
    "có thể nói rằng", "không thể phủ nhận", "trong thế giới hiện đại",
    "đáng chú ý là", "như chúng ta đã biết", "và hơn thế nữa",
    "thật sự là", "thực sự là", "nói chung là", "ngày nay",
]

_SENSORY_FORMATS = {"product", "blog"}

# ---------------------------------------------------------------------------
# Generic glossary — the brand-neutral fallback (calibration F8)
# ---------------------------------------------------------------------------
# The shipped data/domain_glossary.json is MỘC BREW's glossary (its own
# file says brand_id "moc-brew"): its weak-word list bans ordinary words
# like "ngon" and its sensory terms are Arabica-coffee vocabulary. Using
# it to judge OTHER brands made the craft layer cry wolf (11 false
# "ngon" hits on one real brand's posts; a storage guide failed the
# sensory check for lacking coffee-tasting terms). So:
# - the DEFAULT path (no glossary, no brand_id) is unchanged: the
#   shipped glossary — this is the Mộc Brew path the golden sets pin;
# - an explicit `glossary` argument always wins;
# - a `brand_id` that is NOT the shipped glossary's brand resolves to
#   GENERIC_GLOSSARY below: a small, documented, brand-neutral
#   Vietnamese set — cross-domain vague praise words (no ordinary
#   vocabulary like "ngon") and broad sensory words any food/drink/
#   product copy can use. Per-brand glossaries remain the long-term
#   answer; the generic set is the honest fallback, not a new judge.

GENERIC_WEAK_WORDS = [
    "tuyệt vời", "hoàn hảo", "xuất sắc", "đẳng cấp", "chất lượng cao",
    "hảo hạng", "cao cấp", "sang trọng", "tinh tế", "độc đáo",
    "đặc biệt", "không thể cưỡng lại", "mê hoặc", "giá rẻ bất ngờ",
]

GENERIC_SENSORY_TERMS = [
    "mùi thơm", "hương thơm", "thơm", "vị chua", "vị ngọt", "vị đắng",
    "béo", "mềm", "giòn", "mịn", "mượt", "đậm đà", "dịu", "nồng",
]

_GENERIC_REPLACEMENT = "thay bằng chi tiết cụ thể, đo được thay cho tính từ chung chung"

GENERIC_GLOSSARY: dict = {
    "brand_id": None,
    "generic": True,
    "name": "Generic Vietnamese craft glossary (brand-neutral fallback)",
    "weak_words": [{"word": w, "replace_with": _GENERIC_REPLACEMENT}
                   for w in GENERIC_WEAK_WORDS],
    "groups": {"sensory": {"terms": [{"term": t} for t in GENERIC_SENSORY_TERMS]}},
    "sensory_groups": ["sensory"],
}


def resolve_glossary(glossary: dict | None = None,
                     brand_id: str | None = None) -> dict:
    """Pick the glossary a craft check judges with (see F8 note above):
    explicit glossary > per-brand rule (the shipped glossary only for
    its own brand; GENERIC_GLOSSARY for any other brand) > shipped
    glossary (the default Mộc Brew path)."""
    if glossary is not None:
        return glossary
    if brand_id:
        shipped = load_domain_glossary()
        if brand_id != shipped.get("brand_id"):
            return GENERIC_GLOSSARY
        return shipped
    return load_domain_glossary()


# --------------------------------------------------------------------------
# Loaders (thin wrappers so MCP tools never duplicate logic)
# --------------------------------------------------------------------------

# Public format -> stored exemplar format. The blog anchor is stored
# as "blog_intro" (it anchors an introduction), which used to make
# get_exemplars("blog") silently return [] for every caller except
# assemble_context, which carried its own private copy of this mapping
# (evaluation R3 §5.1). ONE mapping now lives here; every caller —
# including the MCP tool and assemble_context — resolves through it.
EXEMPLAR_FORMAT_ALIASES = {"blog": "blog_intro"}


def resolve_exemplar_format(fmt: str) -> str:
    """The stored-format key a public format name resolves to."""
    return EXEMPLAR_FORMAT_ALIASES.get(fmt, fmt)


def get_exemplars(fmt: str, exemplars: dict | None = None) -> list[dict]:
    """Return the exemplar passage(s) for one format ([] if none).
    Accepts the public format name ("blog") and the stored key
    ("blog_intro") alike — see EXEMPLAR_FORMAT_ALIASES."""
    data = exemplars if exemplars is not None else load_exemplars()
    want = resolve_exemplar_format(fmt)
    return [e for e in data.get("exemplars", [])
            if e.get("format") in (fmt, want)]


def glossary_group(group: str | None = None, glossary: dict | None = None) -> dict:
    """Return one glossary group, or all groups + weak words when group=None."""
    data = glossary if glossary is not None else load_domain_glossary()
    if group is None:
        return data
    groups = data.get("groups", {})
    if group not in groups:
        return {"error": f"unknown glossary group: {group}",
                "available_groups": sorted(groups)}
    return {"group": group, **groups[group]}


def weak_word_entries(glossary: dict | None = None) -> list[dict]:
    data = glossary if glossary is not None else load_domain_glossary()
    return data.get("weak_words", [])


def sensory_terms(glossary: dict | None = None) -> list[str]:
    """Terms from the sensory groups (aroma/acidity/body/aftertaste), longest
    first so multi-word terms win over their parts when counting."""
    data = glossary if glossary is not None else load_domain_glossary()
    groups = data.get("groups", {})
    terms: list[str] = []
    for name in data.get("sensory_groups", ["aroma", "acidity", "body", "aftertaste"]):
        for entry in groups.get(name, {}).get("terms", []):
            terms.append(str(entry["term"]).lower())
    return sorted(set(terms), key=len, reverse=True)


# --------------------------------------------------------------------------
# Technique-card retrieval (lorebook-style: dormant until triggered)
# --------------------------------------------------------------------------

# Keyword triggers match on NORMALIZED TOKEN BOUNDARIES (evaluation
# R3): the old raw-substring match fired the "vị" (taste) trigger inside
# "vị trí" (position) and "giá" inside "đánh giá" (to evaluate). Text is
# split into syllable tokens and a keyword fires only when its own token
# sequence appears contiguously. A small documented list of COMPOUNDS
# whose first syllable is a keyword syllable with a different meaning
# ("vị trí" = position, "đánh giá" = evaluate, "giá trị" = value,
# "vị thế" = standing) is masked out of the token stream first, so the
# syllable inside them cannot fire a taste/price trigger either.
_COMPOUND_MASKS = ["vị trí", "đánh giá", "giá trị", "vị thế"]
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_OPENING_CLEAN_RE = re.compile(r"[^\wÀ-ỹà-ỹ]")


def _match_tokens(text: str) -> list[str]:
    tokens = _TOKEN_RE.findall((text or "").lower())
    compounds = [c.split() for c in _COMPOUND_MASKS]
    masked: list[str] = []
    i = 0
    while i < len(tokens):
        hit = next((c for c in compounds if tokens[i:i + len(c)] == c), None)
        if hit:
            masked.append("\x00compound")
            i += len(hit)
        else:
            masked.append(tokens[i])
            i += 1
    return masked


def _keyword_hits(keywords: list[str], tokens: list[str]) -> list[tuple[str, float]]:
    """[(keyword, centre position)] for each keyword whose token
    sequence appears contiguously in the (compound-masked) token stream."""
    hits: list[tuple[str, float]] = []
    for kw in keywords:
        kt = _TOKEN_RE.findall(kw.lower())
        if not kt:
            continue
        for j in range(len(tokens) - len(kt) + 1):
            if tokens[j:j + len(kt)] == kt:
                hits.append((kw, j + (len(kt) - 1) / 2))
                break
    return hits


def retrieve_cards(fmt: str = "", intent: str = "", text: str = "",
                   library: dict | None = None) -> list[dict]:
    """Return at most MAX_CARDS_PER_CALL technique cards for a writing task.

    - The single core card (concrete-specifics) is always-on.
    - Every other card stays DORMANT unless it triggers: its formats must
      include `fmt` (or be empty), AND — when it declares intents/keywords —
      the intent must match or a keyword must appear in `text`.
    - An EMPTY `fmt` ("") means NO format filtering: a card is not
      excluded for format reasons at all. Dormancy still applies — a
      card declaring intents/keywords still needs one of them to fire.
      (Callers that want format filtering must pass a real format.)
      A card declaring neither intents nor keywords triggers on format alone.

    Ranking (evaluation R3): triggered cards are ranked by TRIGGER
    STRENGTH = 2 per intent match + 1 per DISTINCT keyword hit (the old
    boolean keyword hit let three equally-"hit" cards tie and library
    order silently decided — the domain-vocabulary card lost its own
    canonical product call that way). Ties break by the earliest average
    position of the matched keywords in the query (the query's leading
    topic wins), then library order. The core card keeps its slot, but
    the most relevant triggered card can no longer be crowded out by a
    weaker one.
    """
    data = library if library is not None else load_craft_library()
    cards = data.get("cards", [])
    tokens = _match_tokens(text or "")
    low_intent = (intent or "").lower()

    core = [c for c in cards if c.get("core")][:MAX_CORE_CARDS]
    scored: list[tuple[float, float, int, dict]] = []
    for idx, card in enumerate(cards):
        if card.get("core"):
            continue
        trig = card.get("triggers", {})
        formats = trig.get("formats", [])
        if formats and fmt and fmt not in formats:
            continue
        intents = [str(i).lower() for i in trig.get("intents", [])]
        keywords = [str(k).lower() for k in trig.get("keywords", [])]
        intent_hit = bool(low_intent and low_intent in intents)
        hits = _keyword_hits(keywords, tokens) if keywords else []
        if (intents or keywords) and not (intent_hit or hits):
            continue  # dormant: declared triggers but none fired
        strength = (2 if intent_hit else 0) + len(hits) \
            + (1 if not (intents or keywords) else 0)
        centre = (sum(pos for _kw, pos in hits) / len(hits)) if hits else float("inf")
        scored.append((-strength, centre, idx, card))
    scored.sort(key=lambda t: (t[0], t[1], t[2]))
    triggered = [c for _, _, _, c in scored]
    return (core + triggered)[:MAX_CARDS_PER_CALL]


# --------------------------------------------------------------------------
# craft_check — deterministic prose-quality score (separate from brand score)
# --------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Prepared phrase tables + mask-array matching (perf P1)
# ---------------------------------------------------------------------------
# _find_phrase_hits used to re-normalize and re-sort the whole phrase
# list on EVERY call and rebuild the masked haystack string once per
# phrase — O(phrases × text) character work per call, from call sites
# that invoke it per section and per sentence. The vocabulary is now
# prepared ONCE per distinct phrase list (normalized + length-sorted +
# compiled, cached) and matching runs a single scan per phrase over the
# immutable normalized string, accepting only spans that are still
# unmasked — the same longest-first masking semantics, proven
# equivalent: a candidate is accepted iff (a) the token-boundary guards
# hold on the EFFECTIVE text (masked chars read as spaces) and (b) no
# masked position inside the span carries a non-space character (at a
# masked position the effective char is a space, so the phrase can
# only match there where it has a space itself — and a span containing
# a masked space always also contains a masked letter, since a matched
# phrase's spaces sit between its tokens). One conservative exception:
# if the vocabulary contains a SINGLE-CHARACTER phrase, the legacy
# rebuild-per-phrase implementation runs instead — a one-char phrase
# can mask exactly the position where a longer phrase has a space,
# the one shape the mask-array scan cannot reproduce.

_ALNUM_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789")


@_lru_cache(maxsize=128)
def _phrase_table(phrases: tuple[str, ...]) -> tuple[tuple[str, str, re.Pattern], ...]:
    """(display, normalized, compiled plain pattern) triples for one
    vocabulary, longest normalized form first — the exact order the
    legacy matcher rebuilt per call (stable sort, so equal lengths
    keep list order). Duplicate normalized forms keep only their first
    entry: in the legacy scan the later copy ran against a haystack
    where the first copy had already masked every span it could take,
    so it never produced a hit."""
    pairs = sorted(((p, normalize_for_match(p)) for p in phrases if p),
                   key=lambda pair: len(pair[1]), reverse=True)
    seen: set[str] = set()
    table: list[tuple[str, str, re.Pattern]] = []
    for display, match in pairs:
        if not match or match in seen:
            continue
        seen.add(match)
        table.append((display, match, re.compile(re.escape(match))))
    return tuple(table)


def _span_accepted(norm: str, masked: bytearray, s: int, e: int) -> bool:
    """True when the span (s, e) of `norm` is a match on the EFFECTIVE
    text (masked positions read as spaces): boundary guards hold and
    no masked letter sits inside the span (see the P1 note above)."""
    if s > 0 and not masked[s - 1] and norm[s - 1] in _ALNUM_CHARS:
        return False
    if e < len(norm) and not masked[e] and norm[e] in _ALNUM_CHARS:
        return False
    for i in range(s, e):
        if masked[i] and norm[i] != " ":
            return False
    return True


def _find_phrase_hits_legacy(text: str, phrases: list[str]) -> list[dict]:
    """The original rebuild-per-phrase matcher, kept for vocabularies
    containing a single-character phrase (see the P1 note above)."""
    norm = normalize_for_match(text)
    pairs = sorted(((p, normalize_for_match(p)) for p in phrases if p),
                   key=lambda pair: len(pair[1]), reverse=True)
    masked = list(norm)
    hits: list[dict] = []
    for display, match in pairs:
        if not match:
            continue
        haystack = "".join(masked)
        for m in re.finditer(r"(?<![a-z0-9])" + re.escape(match)
                             + r"(?![a-z0-9])", haystack):
            hits.append({"term": display, "index": m.start()})
            for i in range(m.start(), m.end()):
                masked[i] = " "
    hits.sort(key=lambda h: h["index"])
    return hits


def _find_phrase_hits(text: str, phrases: list[str]) -> list[dict]:
    """Count phrase occurrences, longest phrases first, masking matched
    spans so a long phrase ('thơm ngon') is not double-counted by its
    parts ('ngon').

    Matching runs on the NORMALIZED text (review M3): the linter and
    the fact checker were hardened in audit R2 precisely because
    unaccented Vietnamese is common, but craft kept matching raw
    substrings of text.lower() — so the SAME copy scored 67 accented
    and 85 unaccented, and craft is a BLOCKING layer for blog/product.
    Weak/filler/sensory matching now goes through the shared
    normalize_for_match, with token-boundary guards on the normalized
    form; hits still report the phrase's original (accented) form."""
    table = _phrase_table(tuple(phrases))
    if any(len(match) == 1 for _display, match, _rx in table):
        return _find_phrase_hits_legacy(text, phrases)
    norm = normalize_for_match(text)
    masked = bytearray(len(norm))
    hits: list[dict] = []
    for display, _match, rx in table:
        pos = 0
        while True:
            m = rx.search(norm, pos)
            if m is None:
                break
            s, e = m.start(), m.end()
            if _span_accepted(norm, masked, s, e):
                hits.append({"term": display, "index": s})
                for i in range(s, e):
                    masked[i] = 1
                pos = e
            else:
                pos = s + 1
    hits.sort(key=lambda h: h["index"])
    return hits


# A sensory mention inside a negated clause is an ABSENCE, not detail
# (evaluation R3): "không có mùi cacao" used to COUNT as sensory
# richness. Bounded rule: an occurrence is negated when the text right
# before it in its sentence ends with không/chẳng/chưa (+ có/hề/thấy)
# (+ mùi/hương/vị) — "không có mùi cacao", "chẳng có hương hoa".
# The pattern is in NORMALIZED form (review M3): it is applied to the
# normalized sentence prefix, so unaccented denials count too.
_SENSORY_NEGATION_RE = re.compile(
    r"(khong|chang|chua)\s+(co|he|thay)?\s*(mui|huong|vi)?\s*$")


def _sensory_terms_used(text: str, terms: list[str]) -> list[str]:
    """Distinct sensory terms ACTUALLY used: longest-match counting (a
    "hậu cacao" mention counts once, as "hậu cacao" — the old substring
    set also counted its part "cacao" separately, inflating the metric),
    and negated mentions excluded (see _SENSORY_NEGATION_RE)."""
    used: set[str] = set()
    for sentence in split_sentences(text):
        s_norm = normalize_for_match(sentence)
        for hit in _find_phrase_hits(sentence, terms):
            if _SENSORY_NEGATION_RE.search(s_norm[:hit["index"]]):
                continue
            used.add(hit["term"])
    return sorted(used)


def craft_check(text: str, fmt: str = "blog", glossary: dict | None = None,
                brand_id: str | None = None,
                max_sentence_words: int | None = None,
                banned_terms: list[str] | None = None) -> dict:
    """Score prose QUALITY 0-100 (not brand compliance — see linter.py).

    Scoring (documented, deterministic):
      start 100
      weak/vague word  -6 per occurrence, capped at -30
      filler phrase    -4 per occurrence, capped at -12
      monotone rhythm  -15  (>=4 sentences and stdev of sentence lengths < 2.0)
      repeated opening -10  (same first TWO tokens open >=3 sentences)
      no sensory term  -10  (product/blog only: 0 glossary sensory terms used)

    `banned_terms` (optional, supplied by the gate paths from the
    active brand) names the brand's banned terms so sentences carrying
    one can be excluded from the rhythm statistic — see the rhythm
    note in the body. It never creates violations here; banning is
    the linter's job.

    The weak-word and sensory vocabularies come from the ACTIVE
    glossary (resolve_glossary): an explicit `glossary` wins; a
    `brand_id` other than the shipped glossary's own brand judges with
    the generic brand-neutral set; the default (neither given) is the
    shipped Mộc Brew glossary — the path the golden sets pin.

    Too-short guard: text under MIN_WORDS_TO_JUDGE words is not judged
    at all — it returns score 0 with a too_short_to_judge violation.
    An empty/fragment text must never read as good prose.
    """
    words = (text or "").split()
    if len(words) < MIN_WORDS_TO_JUDGE:
        lengths = _sentence_lengths(text or "")
        return {
            "score": 0.0,
            "violations": [{"type": "too_short_to_judge",
                            "detail": f"{len(words)} words < {MIN_WORDS_TO_JUDGE} minimum to judge prose"}],
            "metrics": {
                "word_count": len(words),
                "sentence_count": len(lengths),
                "rhythm_stdev": round(statistics.stdev(lengths), 2) if len(lengths) >= 2 else 0.0,
                "weak_words_found": [],
                "filler_found": [],
                "sensory_terms_used": [],
            },
        }
    data = resolve_glossary(glossary, brand_id)
    violations: list[dict] = []
    score = 100.0

    # Weak / vague words (empty adjectives) — with suggested replacements.
    replacements = {str(w["word"]).lower(): w.get("replace_with", "")
                    for w in weak_word_entries(data)}
    weak_hits = _find_phrase_hits(text, list(replacements))
    for hit in weak_hits:
        violations.append({"type": "weak_word", "detail": hit["term"],
                           "replace_with": replacements[hit["term"]]})
    score -= min(30.0, 6.0 * len(weak_hits))

    # Filler phrases.
    filler_hits = _find_phrase_hits(text, FILLER_PHRASES)
    for hit in filler_hits:
        violations.append({"type": "filler_phrase", "detail": hit["term"]})
    score -= min(12.0, 4.0 * len(filler_hits))

    # Rhythm: sentence-length variation. When the caller supplies the
    # brand's max sentence length, over-long sentences are EXCLUDED
    # from the stdev (monotonicity fix, evaluation R5 — the same rule
    # the linter's median now follows): an appended over-long sentence
    # is already fined by the linter and must not also "fix" a monotone
    # rhythm by inflating the spread.
    # Core-completion extension (R5 residual): a sentence carrying a
    # violation of its OWN — a weak word or filler phrase, or a banned
    # term when the caller supplies the brand's list — must not count
    # as rhythm evidence either: the sweep's residual inversions all
    # came from one appended violating sentence moving the length
    # spread across the 2.0 line and erasing the monotone fine (in the
    # quality total, outweighing its own fine). EXCLUDING such
    # sentences was tried first and failed measurably (23 -> 24
    # inversions): dropping a near-mean sentence raises the subset's
    # stdev just as unpredictably, and can drop the pool under the
    # four-sentence minimum. The robust-statistics remedy is MEDIAN
    # IMPUTATION: the violating sentence's length is replaced by the
    # pool median — it neither helps nor hurts the rhythm verdict and
    # is fined where it stands. (If EVERY sentence violates, there is
    # no clean anchor and the raw lengths are used.)
    sentences = _sentences(text)
    lengths = [len(s.split()) for s in sentences]

    # Memoized per craft_check call (perf P1): the rhythm block below
    # evaluates _carries_violation up to twice per sentence (the any()
    # probe, then the median-imputation comprehension), and each
    # evaluation is two phrase scans. The verdict depends only on the
    # sentence (replacements / FILLER_PHRASES / banned_terms are fixed
    # for the call), so caching it per sentence is exact.
    _violation_cache: dict[str, bool] = {}

    def _carries_violation(sentence: str) -> bool:
        cached = _violation_cache.get(sentence)
        if cached is not None:
            return cached
        result = _carries_violation_uncached(sentence)
        _violation_cache[sentence] = result
        return result

    def _carries_violation_uncached(sentence: str) -> bool:
        if _find_phrase_hits(sentence, list(replacements)):
            return True
        if _find_phrase_hits(sentence, FILLER_PHRASES):
            return True
        if banned_terms:
            norm = normalize_for_match(sentence)
            return any(fuzzy_phrase_spans(norm, t) for t in banned_terms)
        return False

    pool = [(s, ln) for s, ln in zip(sentences, lengths)
            if max_sentence_words is None or ln <= max_sentence_words]
    rhythm_lengths = [ln for _, ln in pool]
    if pool and any(not _carries_violation(s) for s, _ in pool):
        med = statistics.median(rhythm_lengths)
        rhythm_lengths = [med if _carries_violation(s) else ln
                          for s, ln in pool]
    stdev = statistics.stdev(rhythm_lengths) if len(rhythm_lengths) >= 2 else 0.0
    if len(rhythm_lengths) >= 4 and stdev < 2.0:
        violations.append({"type": "monotone_rhythm",
                           "detail": f"stdev {round(stdev, 2)} < 2.0 over {len(rhythm_lengths)} sentences"})
        score -= 15

    # Repeated sentence openings — keyed by the first TWO tokens
    # (core-completion round, evaluation R3 §3.4): keying by the first
    # syllable-token merged every Vietnamese word sharing an opening
    # syllable ("Cà phê…", "Cà chua…", "Cà tím…" all counted as 'cà'),
    # fined brand-name subjects whose verbs differ ("Mộc rang…",
    # "Mộc ghi…") and fined enumerated steps ("Bước một…", "Bước
    # hai…"). Two-token keys separate all of those; a genuinely
    # repeated opening phrase ("Pha phin cần…" x3) still fires.
    openings: dict[str, int] = {}
    for sentence in sentences:
        key_tokens = []
        for w in sentence.split()[:2]:
            cleaned = _OPENING_CLEAN_RE.sub("", w.lower())
            if cleaned:
                key_tokens.append(cleaned)
        if key_tokens:
            key = " ".join(key_tokens)
            openings[key] = openings.get(key, 0) + 1
    repeated = {w: n for w, n in openings.items() if n >= 3}
    if repeated:
        violations.append({"type": "repeated_openings",
                           "detail": ", ".join(f"'{w}' x{n}" for w, n in sorted(repeated.items()))})
        score -= 10

    # Sensory detail (product/blog only): longest-match counting,
    # negated mentions excluded (see _sensory_terms_used).
    terms = sensory_terms(data)
    sensory_found = _sensory_terms_used(text, terms)
    if fmt in _SENSORY_FORMATS and not sensory_found:
        violations.append({"type": "no_sensory_detail",
                           "detail": f"no glossary sensory term used in a {fmt} text"})
        score -= 10

    return {
        "score": max(0.0, score),
        "violations": violations,
        "metrics": {
            "word_count": len(words),
            "sentence_count": len(lengths),
            "rhythm_stdev": round(stdev, 2),
            "weak_words_found": [h["term"] for h in weak_hits],
            "filler_found": [h["term"] for h in filler_hits],
            "sensory_terms_used": sensory_found,
        },
    }


def craft_check_sections(sections: list[dict], fmt: str,
                         glossary: dict | None = None,
                         brand_id: str | None = None,
                         max_sentence_words: int | None = None,
                         banned_terms: list[str] | None = None) -> dict:
    """craft_check over a whole draft: worst section score wins (same rule as
    the brand linter), violations carry their section_id. `glossary` /
    `brand_id` select the judging vocabulary exactly as in craft_check.

    `failing_sections` is SCORE-based (evaluation R8): a section fails
    only when its own score is below CRAFT_PASS_SCORE — the old rule
    (any violation = failing) put every section of a draft on the
    rewrite list whenever one section dragged the worst score under
    the bar, because even good sections carry a violation or two."""
    if not sections:
        result = craft_check("", fmt, glossary, brand_id,
                             max_sentence_words=max_sentence_words,
                             banned_terms=banned_terms)
        return {**result, "failing_sections": []}
    # Resolve the glossary ONCE for the whole draft (perf P4a): every
    # section judges with the same vocabulary, but craft_check used to
    # re-resolve it per section — a disk read + JSON parse of the
    # domain glossary for EVERY section of every gate run. Passing the
    # resolved dict down is exact: resolve_glossary returns an explicit
    # glossary unchanged, and the brand rule depends only on brand_id,
    # which is fixed for the call.
    data = resolve_glossary(glossary, brand_id)
    all_violations: list[dict] = []
    worst = 100.0
    failing: list[str] = []
    # Same metric KEYS as craft_check (review, Info: this used to
    # return a reduced dict — word_count/filler/rhythm were missing,
    # so a consumer reading sections-metrics got a different shape
    # than a consumer reading text-metrics). word_count sums;
    # weak/filler/sensory aggregate; rhythm_stdev is the WORST
    # section's (the section that set the score).
    total_metrics: dict = {"word_count": 0, "sentence_count": 0,
                           "rhythm_stdev": 0.0, "weak_words_found": [],
                           "filler_found": [], "sensory_terms_used": []}
    for sec in sections:
        r = craft_check(sec.get("text", ""), fmt, data,
                        max_sentence_words=max_sentence_words,
                        banned_terms=banned_terms)
        for v in r["violations"]:
            all_violations.append({**v, "section_id": sec.get("id", "")})
        if r["score"] <= worst:
            total_metrics["rhythm_stdev"] = r["metrics"]["rhythm_stdev"]
        worst = min(worst, r["score"])
        if r["score"] < CRAFT_PASS_SCORE:
            failing.append(sec.get("id", ""))
        total_metrics["word_count"] += r["metrics"]["word_count"]
        total_metrics["sentence_count"] += r["metrics"]["sentence_count"]
        total_metrics["weak_words_found"].extend(r["metrics"]["weak_words_found"])
        total_metrics["filler_found"].extend(r["metrics"]["filler_found"])
        total_metrics["sensory_terms_used"].extend(r["metrics"]["sensory_terms_used"])
    return {"score": worst, "violations": all_violations,
            "metrics": total_metrics, "failing_sections": failing}
