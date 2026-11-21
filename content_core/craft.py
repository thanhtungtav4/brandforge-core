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
"""
from __future__ import annotations

import re
import statistics

from .linter import split_sentences
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


# --------------------------------------------------------------------------
# Loaders (thin wrappers so MCP tools never duplicate logic)
# --------------------------------------------------------------------------

def get_exemplars(fmt: str, exemplars: dict | None = None) -> list[dict]:
    """Return the exemplar passage(s) for one format ([] if none)."""
    data = exemplars if exemplars is not None else load_exemplars()
    return [e for e in data.get("exemplars", []) if e.get("format") == fmt]


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
    Triggered cards are ranked by match strength (intent+keyword > intent >
    keyword > format-only), ties keep library order.
    """
    data = library if library is not None else load_craft_library()
    cards = data.get("cards", [])
    low_text = (text or "").lower()
    low_intent = (intent or "").lower()

    core = [c for c in cards if c.get("core")][:MAX_CORE_CARDS]
    scored: list[tuple[int, int, dict]] = []
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
        keyword_hit = any(k in low_text for k in keywords) if keywords else False
        if (intents or keywords) and not (intent_hit or keyword_hit):
            continue  # dormant: declared triggers but none fired
        strength = (2 if intent_hit else 0) + (2 if keyword_hit else 0) + (0 if (intents or keywords) else 1)
        scored.append((-strength, idx, card))
    scored.sort(key=lambda t: (t[0], t[1]))
    triggered = [c for _, _, c in scored]
    return (core + triggered)[:MAX_CARDS_PER_CALL]


# --------------------------------------------------------------------------
# craft_check — deterministic prose-quality score (separate from brand score)
# --------------------------------------------------------------------------

def _find_phrase_hits(low_text: str, phrases: list[str]) -> list[dict]:
    """Count phrase occurrences, longest phrases first, masking matched spans
    so a long phrase ('thơm ngon') is not double-counted by its parts ('ngon')."""
    masked = list(low_text)
    hits: list[dict] = []
    for phrase in sorted(phrases, key=len, reverse=True):
        if not phrase:
            continue
        haystack = "".join(masked)
        for m in re.finditer(re.escape(phrase), haystack):
            hits.append({"term": phrase, "index": m.start()})
            for i in range(m.start(), m.end()):
                masked[i] = " "
    hits.sort(key=lambda h: h["index"])
    return hits


def craft_check(text: str, fmt: str = "blog", glossary: dict | None = None) -> dict:
    """Score prose QUALITY 0-100 (not brand compliance — see linter.py).

    Scoring (documented, deterministic):
      start 100
      weak/vague word  -6 per occurrence, capped at -30
      filler phrase    -4 per occurrence, capped at -12
      monotone rhythm  -15  (>=4 sentences and stdev of sentence lengths < 2.0)
      repeated opening -10  (same first word opens >=3 sentences)
      no sensory term  -10  (product/blog only: 0 glossary sensory terms used)

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
    data = glossary if glossary is not None else load_domain_glossary()
    low = text.lower()
    violations: list[dict] = []
    score = 100.0

    # Weak / vague words (empty adjectives) — with suggested replacements.
    replacements = {str(w["word"]).lower(): w.get("replace_with", "")
                    for w in weak_word_entries(data)}
    weak_hits = _find_phrase_hits(low, list(replacements))
    for hit in weak_hits:
        violations.append({"type": "weak_word", "detail": hit["term"],
                           "replace_with": replacements[hit["term"]]})
    score -= min(30.0, 6.0 * len(weak_hits))

    # Filler phrases.
    filler_hits = _find_phrase_hits(low, FILLER_PHRASES)
    for hit in filler_hits:
        violations.append({"type": "filler_phrase", "detail": hit["term"]})
    score -= min(12.0, 4.0 * len(filler_hits))

    # Rhythm: sentence-length variation.
    lengths = _sentence_lengths(text)
    stdev = statistics.stdev(lengths) if len(lengths) >= 2 else 0.0
    if len(lengths) >= 4 and stdev < 2.0:
        violations.append({"type": "monotone_rhythm",
                           "detail": f"stdev {round(stdev, 2)} < 2.0 over {len(lengths)} sentences"})
        score -= 15

    # Repeated sentence openings.
    openings: dict[str, int] = {}
    for sentence in _sentences(text):
        words = sentence.split()
        if words:
            first = re.sub(r"[^\wÀ-ỹà-ỹ]", "", words[0].lower())
            if first:
                openings[first] = openings.get(first, 0) + 1
    repeated = {w: n for w, n in openings.items() if n >= 3}
    if repeated:
        violations.append({"type": "repeated_openings",
                           "detail": ", ".join(f"'{w}' x{n}" for w, n in sorted(repeated.items()))})
        score -= 10

    # Sensory detail (product/blog only).
    terms = sensory_terms(data)
    sensory_found = sorted({t for t in terms if t in low})
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
                         glossary: dict | None = None) -> dict:
    """craft_check over a whole draft: worst section score wins (same rule as
    the brand linter), violations carry their section_id."""
    if not sections:
        result = craft_check("", fmt, glossary)
        return {**result, "failing_sections": []}
    all_violations: list[dict] = []
    worst = 100.0
    failing: list[str] = []
    total_metrics = {"sentence_count": 0, "weak_words_found": [], "sensory_terms_used": []}
    for sec in sections:
        r = craft_check(sec.get("text", ""), fmt, glossary)
        for v in r["violations"]:
            all_violations.append({**v, "section_id": sec.get("id", "")})
        worst = min(worst, r["score"])
        if r["violations"]:
            failing.append(sec.get("id", ""))
        total_metrics["sentence_count"] += r["metrics"]["sentence_count"]
        total_metrics["weak_words_found"].extend(r["metrics"]["weak_words_found"])
        total_metrics["sensory_terms_used"].extend(r["metrics"]["sensory_terms_used"])
    return {"score": worst, "violations": all_violations,
            "metrics": total_metrics, "failing_sections": failing}
