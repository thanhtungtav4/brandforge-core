"""Model Golden Set — regression for REAL model-written prose (MCP mode).

The original golden set (golden.py) runs MockWriter fixtures through the
LangGraph graph: it tests the GATES, not the writer. In MCP mode the writer
is a real model, and its failure modes are different — plain-but-correct
prose, craft drift in later sections, banned terms and wrong prices sneaking
into section 3. This set pins down MODEL-WRITTEN drafts (data/model_golden.json)
with their expected scores, evaluated with the exact check functions and the
exact gate rule the MCP final_gate uses (lint_draft + check_draft +
craft_check_sections, craft blocking for blog/product at CRAFT_PASS_SCORE).

Run after every prompt/craft/core change: .venv/bin/python run_model_golden.py
"""
from __future__ import annotations

import json
from pathlib import Path

from .craft import craft_check, craft_check_sections
from .gates import BRAND_PASS_SCORE, evaluate_gate
from .loaders import DATA_DIR, load_brand, load_facts


def load_model_golden(path: Path | None = None) -> dict:
    return json.loads((path or DATA_DIR / "model_golden.json").read_text(encoding="utf-8"))


def evaluate_entry(entry: dict, brand: dict | None = None,
                   facts: dict | None = None) -> tuple[bool, dict]:
    """Evaluate one model-golden entry against its `expected` block.

    Returns (all_checks_passed, details) where details carries the measured
    brand/craft scores, banned count, fact issues, gate verdict and the
    per-expectation check results."""
    brand = brand if brand is not None else load_brand()
    facts = facts if facts is not None else load_facts()
    sections = entry["sections"]
    fmt = entry["format"]
    exp = entry.get("expected", {})

    # THE gate rule — gates.evaluate_gate, the same function the MCP
    # final_gate tool and the LangGraph graph both call (legacy path: no
    # quality profile / judged scores supplied, so the quality layer is
    # inactive and the verdict is the hard+craft rule, as before).
    verdict = evaluate_gate(sections, fmt, brand, facts)
    brand_score = verdict["hard"]["brand_score"]
    violations = verdict["hard"]["brand_violations"]
    issues = verdict["hard"]["fact_issues"]
    banned = verdict["hard"]["banned_terms_found"]
    craft = verdict["craft"]["report"]
    craft_failed = verdict["craft"]["failed"]
    gate_pass = verdict["passed"]
    failing = set(verdict["failing_sections"])

    checks: dict[str, bool] = {}
    checks["brand_min"] = brand_score >= exp.get("brand_min", 0)
    if "brand_max" in exp:
        checks["brand_max"] = brand_score <= exp["brand_max"]
    if "craft_min" in exp:
        checks["craft_min"] = craft["score"] >= exp["craft_min"]
    if "craft_max" in exp:
        checks["craft_max"] = craft["score"] <= exp["craft_max"]
    if "craft_whole_text" in exp:
        # Whole-text craft (all sections joined) — the number the demo v2
        # comparison quoted; gate semantics use the worst-section score.
        whole = craft_check("\n".join(s.get("text", "") for s in sections), fmt)
        checks["craft_whole_text"] = whole["score"] == exp["craft_whole_text"]
    checks["banned"] = len(banned) == exp.get("banned", 0)
    checks["fact_ok"] = (not issues) == exp.get("fact_ok", True)
    checks["gate_pass"] = gate_pass == exp.get("gate_pass", True)
    if exp.get("gate_fail_reason") == "craft":
        checks["gate_fail_reason_craft"] = (
            not gate_pass and brand_score >= BRAND_PASS_SCORE and not banned
            and not issues and craft_failed)
    for sid in exp.get("failing_includes", []):
        checks[f"failing_includes_{sid}"] = sid in failing
    for sid in exp.get("failing_excludes", []):
        checks[f"failing_excludes_{sid}"] = sid not in failing

    details = {"brand_score": brand_score, "craft_score": craft["score"],
               "banned": len(banned), "fact_issues": issues,
               "gate_pass": gate_pass, "failing_sections": sorted(failing),
               "checks": checks}
    return all(checks.values()), details
