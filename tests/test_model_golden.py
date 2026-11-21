"""Model Golden Set (P2a): regression for REAL model-written prose.

Every entry in data/model_golden.json is a model-written draft with expected
brand/craft scores and gate verdict, evaluated with the MCP final_gate's
exact functions and rule. If a prompt/craft/core change alters how real
prose is judged, this set catches it — the MockWriter golden set cannot.
"""
from __future__ import annotations

import copy

from content_core.model_golden import evaluate_entry, load_model_golden

ENTRIES = load_model_golden()["entries"]
BY_ID = {e["id"]: e for e in ENTRIES}


def test_model_golden_set_has_all_six_entries():
    assert [e["id"] for e in ENTRIES] == ["mg1", "mg2", "mg3", "mg4", "mg5", "mg6"]


def test_all_model_golden_entries_meet_expectations():
    for entry in ENTRIES:
        ok, details = evaluate_entry(entry)
        assert ok, f"{entry['id']}: {details}"


def test_mg4_fails_on_craft_only():
    ok, d = evaluate_entry(BY_ID["mg4"])
    assert ok  # expectations themselves hold
    assert d["gate_pass"] is False and d["craft_score"] < 70
    assert d["brand_score"] >= 85 and d["fact_issues"] == []


def test_mg5_drift_is_pinned_to_section_3():
    ok, d = evaluate_entry(BY_ID["mg5"])
    assert ok
    assert d["gate_pass"] is False
    assert d["failing_sections"] == ["s3"]


def test_expectations_are_not_vacuous_tampered_price_fails():
    # Mutating mg1's price to 190k must break its expectations (wrong_price
    # via the P1b money normalization) — proves the set actually checks.
    tampered = copy.deepcopy(BY_ID["mg1"])
    tampered["sections"][0]["text"] = tampered["sections"][0]["text"].replace("185.000đ", "190k")
    ok, d = evaluate_entry(tampered)
    assert ok is False
    assert any(i["type"] == "wrong_price" for i in d["fact_issues"])


def test_model_golden_runner_exit_code_zero():
    import runpy
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    ns = runpy.run_path(str(root / "run_model_golden.py"), run_name="not_main")
    assert ns["main"]() == 0
