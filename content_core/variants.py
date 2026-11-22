"""Best-of-N section variants (quality uplift WS2).

The writer drafts several candidate texts for the sections where a
single attempt is most often mediocre — headline, opening, CTA,
closing — and this module ranks them with the SAME checkers the gate
uses (linter / fact_check / craft / style_match), each variant scored
in isolation as a one-section draft.

Ranking follows the gate's philosophy exactly:

- HARD criteria stay OUTSIDE the weighting. A variant carrying a
  banned term or an unflagged fact issue (wrong price, unknown
  weight…) is INELIGIBLE: it ranks after every eligible variant,
  however fluent it is, and its hard violations are listed.
- The composite is the plain mean of the measured component scores
  that could be computed (brand, craft, style). The fact layer is
  pass/fail (the hard layer above), not a weighted ingredient.

Honesty about scope: some checkers judge a whole draft, not a
fragment — SEO needs the title/headings/meta around the text, and
coherence is a cross-section property. Those axes are reported in
`not_scored` with their reasons; no score is ever faked for them.
Craft likewise refuses very short texts (its too_short_to_judge
guard): the variant's craft score is then None with a note, and the
composite is the mean of what remains.
"""
from __future__ import annotations

from .craft import craft_check
from .fact_check import check_section
from .linter import lint_section
from .loaders import brand_check_context
from .style import style_match

SECTION_ROLES = ("headline", "opening", "cta", "closing")
MIN_VARIANTS, MAX_VARIANTS = 2, 5

#: Axes a single variant cannot be scored on — they are properties
#: of the whole draft. Reported, never faked.
NOT_SCORED = [
    {"axis": "seo",
     "reason": "needs the full draft (title, headings, meta)"},
    {"axis": "coherence",
     "reason": "a cross-section property; a variant is scored in "
               "isolation"},
]


def score_variants(variants: list, role: str, fmt: str, brand: dict,
                   facts: dict, style_profile: dict | None = None) -> dict:
    """Rank 2–5 variant texts for one section role.

    Pure function of its inputs (the MCP tool resolves the brand /
    facts / style profile). Returns the ranked list — eligible
    variants first by composite score, ineligible ones last — with
    per-variant component scores, violations and notes."""
    if role not in SECTION_ROLES:
        raise ValueError(
            f"section_role must be one of {SECTION_ROLES}, got {role!r}")
    if not isinstance(variants, list) \
            or not MIN_VARIANTS <= len(variants) <= MAX_VARIANTS:
        raise ValueError(
            f"variants must be a list of {MIN_VARIANTS}–{MAX_VARIANTS} "
            f"texts, got {len(variants) if isinstance(variants, list) else type(variants).__name__}")
    texts: list[str] = []
    for i, variant in enumerate(variants):
        text = str(variant or "").strip()
        if not text:
            raise ValueError(f"variant {i} is empty")
        texts.append(text)

    brand_id, max_sentence_words, banned_terms = brand_check_context(brand)
    scored: list[dict] = []
    for i, text in enumerate(texts):
        violations, brand_score = lint_section(text, fmt, brand, "variant")
        fact_issues, flagged = check_section(text, facts, brand, "variant")
        craft = craft_check(text, fmt, brand_id=brand_id,
                            max_sentence_words=max_sentence_words,
                            banned_terms=banned_terms)
        notes: list[str] = []
        if any(v["type"] == "too_short_to_judge"
               for v in craft["violations"]):
            craft_score = None
            notes.append(
                "craft not scored: the text is under the craft "
                "checker's minimum length to judge prose")
        else:
            craft_score = craft["score"]
        style_score = None
        if style_profile is not None:
            match = style_match(text, style_profile)
            style_score = match["score"]
            if match.get("reliability_note"):
                notes.append(f"style: {match['reliability_note']}")
        hard = [v for v in violations if v["type"] == "banned_term"] \
            + list(fact_issues)
        present = [s for s in (brand_score, craft_score, style_score)
                   if s is not None]
        composite = round(sum(present) / len(present), 2) if present else 0.0
        scored.append({
            "index": i, "text": text,
            "eligible": not hard,
            "hard_violations": hard,
            "scores": {"brand": brand_score, "craft": craft_score,
                       "style": style_score, "composite": composite},
            "violations": violations,
            "fact_issues": fact_issues,
            "flagged_claims": flagged,
            "craft_violations": craft["violations"]
            if craft_score is not None else [],
            "notes": notes,
        })
    eligible = sorted((r for r in scored if r["eligible"]),
                      key=lambda r: (-r["scores"]["composite"], r["index"]))
    ineligible = sorted((r for r in scored if not r["eligible"]),
                        key=lambda r: (-r["scores"]["composite"], r["index"]))
    ranked = eligible + ineligible
    for rank, entry in enumerate(ranked, 1):
        entry["rank"] = rank
    return {
        "role": role, "format": fmt,
        "brand_id": brand.get("brand", {}).get("id", ""),
        "style_profile_id": style_profile.get("id")
        if style_profile else None,
        "ranked": ranked,
        "best_index": ranked[0]["index"] if ranked and ranked[0]["eligible"]
        else None,
        "scored_in_isolation": ["brand", "fact", "craft", "style"],
        "not_scored": NOT_SCORED,
    }
