#!/usr/bin/env python3
"""Model Golden Set runner: evaluate every MODEL-WRITTEN draft in
data/model_golden.json against its expected scores, using the exact check
functions and gate rule of the MCP final_gate (craft blocking for
blog/product at 70). Exit code non-zero on any mismatch.

This is the regression set for MCP mode (real model prose). The original
run_golden.py covers the LangGraph graph with MockWriter fixtures — both
must stay green.

Usage: .venv/bin/python run_model_golden.py
"""
from __future__ import annotations

import sys

from content_core.model_golden import evaluate_entry, load_model_golden


def main() -> int:
    entries = load_model_golden()["entries"]
    failures = 0
    for entry in entries:
        ok, d = evaluate_entry(entry)
        print(f"{'PASS' if ok else 'FAIL'}  {entry['id']:<4} {entry['format']:<8} "
              f"brand={d['brand_score']} craft={d['craft_score']} banned={d['banned']} "
              f"fact_issues={len(d['fact_issues'])} gate_pass={d['gate_pass']} "
              f"failing={d['failing_sections']}")
        if not ok:
            failures += 1
            bad = [k for k, v in d["checks"].items() if not v]
            print(f"      -> failed expectations: {bad} | fact_issues={d['fact_issues']}")
    print(f"\n{len(entries) - failures}/{len(entries)} model golden entries passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
