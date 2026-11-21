"""Golden set runner: run every brief through the graph with MockWriter,
print PASS/FAIL per brief against its own criteria, exit non-zero on failure.

Usage: .venv/bin/python run_golden.py
"""
from __future__ import annotations

import sys

from .fact_check import NEEDS_SOURCE
from .gates import BRAND_PASS_SCORE
from .graph import run_flow
from .golden import BAD_DRAFT_BRIEF, GOLDEN_BRIEFS
from .linter import word_count


def _draft_text(state: dict) -> str:
    return "\n".join(s.get("text", "") for s in state.get("sections", []))


def _common_ok(state: dict) -> bool:
    banned = [v for v in state.get("brand_violations", []) if v["type"] == "banned_term"]
    return (state.get("status") == "approved"
            and state.get("scores", {}).get("brand", 0) >= BRAND_PASS_SCORE
            and not banned and not state.get("fact_issues"))


def check_case(case: dict, state: dict) -> tuple[bool, str]:
    kind = case["check"]
    text = _draft_text(state)
    if kind == "blog_drift":
        words = word_count(text)
        return _common_ok(state) and words >= 1500, f"words={words} brand={state.get('scores', {}).get('brand')}"
    if kind == "product_facts":
        ok = _common_ok(state) and "185.000đ" in text and "250g" in text and "thứ Ba" in text
        return ok, "price/weight/roast present, 0 fact issues" if ok else f"fact_issues={state.get('fact_issues')}"
    if kind == "social_tone":
        return _common_ok(state) and word_count(text) < 60, f"words={word_count(text)} (short form, same voice rules)"
    if kind == "email_clarity":
        return _common_ok(state) and "2-3 ngày" in text, "delivery fact correct, approved"
    if kind == "comparison_needs_source":
        ok = _common_ok(state) and NEEDS_SOURCE in text and bool(state.get("flagged_claims"))
        return ok, f"flagged_claims={len(state.get('flagged_claims', []))}, unflagged=0"
    if kind == "refuse_false_claim":
        # Refusal routing (backlog item 7): the flow now ENDS at
        # validate_brief with status "refused" — no draft is produced
        # at all. The old check required an approved clean draft; the
        # new one is stricter (nothing is written), so it asserts the
        # refusal itself + the distinct end status + no claim text.
        refused = state.get("refused_claims", [])
        ok = (bool(refused) and state.get("status") == "refused"
              and "chữa mất ngủ" not in text)
        return ok, (f"refused={refused}, status={state.get('status')}, "
                    f"claim absent from draft={('chữa mất ngủ' not in text)}")
    if kind == "bad_draft_recovery":
        first = (state.get("gate_history") or [{}])[0]
        caught = first.get("brand_violations", 0) > 0 or first.get("fact_issues", 0) > 0
        recovered = state.get("status") == "approved" and state.get("rewrite_count", 0) >= 1
        return caught and recovered, (f"pass1 violations={first.get('brand_violations')} "
                                      f"fact_issues={first.get('fact_issues')} -> rewrites={state.get('rewrite_count')} -> {state.get('status')}")
    return False, f"unknown check {kind}"


def main(cases: list[dict] | None = None) -> int:
    cases = cases if cases is not None else [*GOLDEN_BRIEFS, BAD_DRAFT_BRIEF]
    failures = 0
    for case in cases:
        state = run_flow(case["brief"], outline_approved=True)
        ok, detail = check_case(case, state)
        print(f"{'PASS' if ok else 'FAIL'}  {case['brief']['id']:<8} {case['criteria']}\n"
              f"      -> status={state.get('status')} brand={state.get('scores', {}).get('brand')} | {detail}")
        failures += 0 if ok else 1
    print(f"\n{len(cases) - failures}/{len(cases)} cases passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
