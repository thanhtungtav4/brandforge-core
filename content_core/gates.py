"""THE final gate — one rule, two call sites.

Both gate paths (the MCP `final_gate` tool and the LangGraph graph's
`final_gate` node) call `evaluate_gate` here. There is exactly ONE rule
table in the codebase, and it lives in this module:

1. HARD layer (never traded away, never weighted):
   brand score >= BRAND_PASS_SCORE (85) AND 0 banned terms AND
   0 unflagged fact issues AND the draft is not vacuous (at least one
   section, and every section has words — an empty draft used to pass
   every checker trivially; audit round 2). (Prohibited-claim refusal
   happens earlier, in validate_brief, and is not part of the gate.)
2. CRAFT layer: for formats in CRAFT_BLOCKING_FORMATS (blog, product) a
   craft score below CRAFT_PASS_SCORE (70) fails the gate and the
   craft-failing sections join the rewrite list. For other formats craft
   is reported but advisory.
3. QUALITY layer (quality.py): active only when a profile and/or judged
   scores are supplied. quality_pass requires the weighted total to
   clear the profile's pass_total AND every axis to clear its floor.

UNIFICATION NOTE (measured 21 Nov 2026): the graph previously skipped
the craft layer because MockWriter is a structural fixture. Measured
with craft_check_sections over the golden set, MockWriter output scores
craft 75 (worst section) for blog/product good drafts and 84 on the
intentionally bad draft's first pass (its badness is brand/fact, which
craft does not measure) — all >= 70. Craft blocking is therefore enabled
in BOTH paths: full unification, no behavioural difference remains.

`craft_mode` ("blocking" | "advisory") stays as an explicit parameter as
the single escape hatch: a future fixture-writer whose prose is not
meant to be judged could run the graph in advisory mode without forking
the rule. Both production call sites use the default, "blocking".
"""
from __future__ import annotations

import hashlib
import json

from .craft import CRAFT_BLOCKING_FORMATS, CRAFT_PASS_SCORE, craft_check_sections
from .fact_check import check_draft
from .linter import lint_draft, word_count
from .loaders import _utc_now
from .loaders import brand_check_context as _brand_check_context
from .quality import evaluate_quality

# The gate constants live HERE now (graph.py and mcp_server.py import
# them from this module; craft.py keeps the craft-policy originals,
# re-exported below so there is one import surface for gate policy).
BRAND_PASS_SCORE = 85
MAX_REWRITES = 2

__all__ = ["BRAND_PASS_SCORE", "MAX_REWRITES", "CRAFT_PASS_SCORE",
           "CRAFT_BLOCKING_FORMATS", "evaluate_gate", "content_sha",
           "stamp_gate_entry"]


def stamp_gate_entry(entry: dict) -> dict:
    """Record WHEN a gate run happened on its gate_history entry.

    Both gate paths build their entry dict locally (the field sets are
    aligned by hand — see the N2 note in graph.py), so the timestamp
    itself is stamped HERE, in the one shared place, in the codebase's
    standard format (loaders._utc_now: UTC ISO-8601, seconds). Only NEW
    entries are stamped: entries recorded before this field existed
    stay without `ran_at` — no backfill, no invented times — and
    consumers (the app's quality-over-time chart) treat a missing
    `ran_at` as "sequence order only". Mutates and returns `entry`.
    """
    entry["ran_at"] = _utc_now()
    return entry


def content_sha(sections: list[dict], title: str = "") -> str:
    """Fingerprint of a draft's EXPORTABLE content: the post title
    plus each section's id / heading / text, in order — exactly what
    the WordPress exporter turns into a post. Recorded on every
    gate_history entry (fix round F1) so the exporter can prove a
    recorded PASS still describes the CURRENT text: any edit after
    the pass changes this hash. Deterministic (canonical JSON,
    sorted keys); intent/target_words are planning metadata, not
    content, and are excluded."""
    canonical = {
        "title": str(title or ""),
        "sections": [{"id": str(s.get("id", "")),
                      "heading": str(s.get("heading", "")),
                      "text": str(s.get("text", ""))}
                     for s in sections or []],
    }
    raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def evaluate_gate(sections: list[dict], fmt: str, brand: dict, facts: dict,
                  *, craft_mode: str = "blocking",
                  quality_profile: dict | None = None,
                  judged: dict | None = None,
                  style_profile: dict | None = None,
                  brief: dict | None = None) -> dict:
    """Evaluate the full gate for a draft's sections. Pure function.

    Returns one structured verdict:
    {passed, hard: {...}, craft: {...}, quality: {...} | None,
     failing_sections: [...]}.

    failing_sections is the rewrite list: sections named by the brand
    linter / fact checker, plus craft-failing sections when the craft
    layer blocks. The quality layer is whole-draft (axes, not sections),
    so a quality failure adds no sections — the quality report itself
    says which axis/floor failed."""
    if craft_mode not in ("blocking", "advisory"):
        raise ValueError(f"craft_mode must be 'blocking' or 'advisory', got {craft_mode!r}")

    # --- checkers (the same functions every path has always used) ---
    violations, brand_score, _lint_failing = lint_draft(sections, fmt, brand)
    issues, flagged, _fact_failing = check_draft(sections, facts, brand)
    # Craft judges with the ACTIVE brand's glossary (evaluation R3-S4:
    # the blocking paths used to omit brand_id, so every brand was
    # judged with Mộc Brew's coffee vocabulary — the calibration F8
    # false positives, inside the gate itself). The brand id and the
    # brand's own max sentence length (for the rhythm statistic) come
    # from the brand dict this gate already receives; the MCP
    # final_gate inherits both automatically.
    # The brand context (id, sentence cap, banned terms) comes from
    # loaders.brand_check_context — ONE implementation shared with
    # quality.py (review L2). The brand's banned terms go to craft so
    # a sentence carrying one is excluded from the rhythm statistic
    # (monotonicity, R5 residual) — the gate is the path where that
    # gaming mattered.
    brand_id, max_sentence_words, gate_banned_terms = \
        _brand_check_context(brand)
    craft = craft_check_sections(sections, fmt, brand_id=brand_id,
                                 max_sentence_words=max_sentence_words,
                                 banned_terms=gate_banned_terms)

    # --- 1. HARD layer ---
    # Vacuity guard (audit round 2): an empty draft — no sections at all,
    # or a section with zero words — used to sail through every checker
    # (no text = no violations = brand 100) and get APPROVED. An empty
    # section is a hard failure and joins the rewrite list.
    if sections:
        empty_sections = [sec.get("id", "") for sec in sections
                          if word_count(sec.get("text", "")) == 0]
    else:
        empty_sections = ["<no sections>"]
    banned = [v for v in violations if v["type"] == "banned_term"]
    hard_passed = (brand_score >= BRAND_PASS_SCORE and not banned and not issues
                   and not empty_sections)
    hard = {"passed": hard_passed,
            "brand_score": brand_score,
            "brand_pass_score": BRAND_PASS_SCORE,
            "brand_violations": violations,
            "banned_terms_found": banned,
            "fact_issues": issues,
            "flagged_claims": flagged,
            "empty_sections": empty_sections}

    # --- 2. CRAFT layer ---
    craft_blocking = fmt in CRAFT_BLOCKING_FORMATS and craft_mode == "blocking"
    craft_failed = craft_blocking and craft["score"] < CRAFT_PASS_SCORE
    craft_layer = {"score": craft["score"],
                   "pass_score": CRAFT_PASS_SCORE,
                   "blocking": craft_blocking,
                   "advisory": not craft_blocking,
                   "mode": craft_mode,
                   "failed": craft_failed,
                   "report": craft}

    # --- 3. QUALITY layer (only when a profile/judged scores are given) ---
    quality = None
    if quality_profile is not None or judged is not None:
        profile = quality_profile
        if profile is None:
            from .quality import preset_profile
            profile = preset_profile(fmt)
        # The quality layer REUSES this gate's own checker results
        # (review M4): lint_draft/check_draft were just run on these
        # exact sections — re-running them inside evaluate_quality
        # doubled the gate's cost on long drafts for identical numbers.
        quality = evaluate_quality(
            sections, fmt, profile, judged, brand, facts,
            style_profile=style_profile, brief=brief,
            _precomputed={"fact_issues": issues, "fact_flagged": flagged,
                          "brand_violations": violations,
                          "brand_score": brand_score})

    passed = (hard_passed and not craft_failed
              and (quality is None or quality["quality_pass"]))

    failing = sorted({v["section_id"] for v in violations if not v.get("warning")}
                     | {i["section_id"] for i in issues})
    if empty_sections and sections:
        failing = sorted(set(failing) | set(empty_sections))
    if craft_failed:
        failing = sorted(set(failing) | set(craft["failing_sections"]))
    if passed:
        failing = []

    return {"passed": passed,
            "hard": hard,
            "craft": craft_layer,
            "quality": quality,
            "failing_sections": failing}
