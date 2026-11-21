#!/usr/bin/env python3
"""Post-fix re-check on the calibration corpus (calibration fix round).

Re-runs, on the SAME saved evidence (samples.json / pages_setB.json /
writer_trial.txt), the checks the calibration report ran BEFORE the
fixes, now with the fixed code paths:
- craft_check with the generic glossary (brand_id="cao-nguyen-moc")
- brand lint with the DERIVED max_sentence_words + table exclusion
- style_match (short-text reliability) on posts + the writer trial
- analyze_style divergence warnings on the 5 samples
Before-numbers come from self_consistency.json / writer_trial_results.json
as recorded by the calibration run. Output: recheck_after_fixes.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))

from content_core.brand_extract import suggest_voice_rules  # noqa: E402
from content_core.craft import craft_check  # noqa: E402
from content_core.linter import lint_draft  # noqa: E402
from content_core.loaders import load_style_profile  # noqa: E402
from content_core.style import analyze_samples, style_match  # noqa: E402

WORK = PROJECT / "calibration" / "work"
BRAND_ID = "cao-nguyen-moc"


def slug(url: str) -> str:
    return url.rstrip("/").split("/")[-1]


def main() -> None:
    samples = json.loads((WORK / "samples.json").read_text(encoding="utf-8"))
    before = json.loads((WORK / "self_consistency.json").read_text(encoding="utf-8"))
    trial_before = json.loads((WORK / "writer_trial_results.json").read_text(encoding="utf-8"))
    pages_b = json.loads((WORK / "pages_setB.json").read_text(encoding="utf-8"))
    if isinstance(pages_b, dict):
        pages_b = pages_b.get("pages", [])
    corpus_text = "\n\n".join(p.get("text", "") for p in pages_b)

    brand = yaml.safe_load((PROJECT / "calibration" / "caonguyenmoc-brand.yaml")
                           .read_text(encoding="utf-8"))
    derived = suggest_voice_rules(corpus_text, brand_name="Cao Nguyên Mộc")
    derived_max = derived["sentence_rules"]["max_sentence_words"]
    brand_derived = json.loads(json.dumps(brand))  # deep copy
    brand_derived["voice"]["sentence_rules"]["max_sentence_words"] = derived_max

    profile = load_style_profile("cao-nguyen-moc-blog-voice-dd5678")

    out: dict = {"derived_max_sentence_words": derived_max,
                 "derived_pov": derived["pov"],
                 "posts": {}, "writer_trial": {}}

    for sample in samples:
        key = slug(sample["url"])
        text = sample["text"]
        craft_after = craft_check(text, "blog", brand_id=BRAND_ID)
        _v, brand_score, _f = lint_draft([{"id": "post", "text": text}], "blog", brand_derived)
        lint_after = lint_draft([{"id": "post", "text": text}], "blog", brand_derived)
        match_after = style_match(text, profile)
        b = before.get(key, {})
        out["posts"][key] = {
            "craft_before": b.get("craft_score"),
            "craft_after": craft_after["score"],
            "craft_after_violations": [v["type"] + ":" + v.get("detail", "")
                                       for v in craft_after["violations"]],
            "brand_before": b.get("brand_score"),
            "brand_after": brand_score,
            "brand_violations_before": b.get("brand_violation_count"),
            "brand_violations_after": len(lint_after[0]),
            "brand_violation_types_after": sorted({v["type"] for v in lint_after[0]}),
            "style_match_before": b.get("style_match_score"),
            "style_match_after": match_after["score"],
        }

    trial = (WORK / "writer_trial.txt").read_text(encoding="utf-8")
    trial_craft = craft_check(trial, "blog", brand_id=BRAND_ID)
    trial_lint = lint_draft([{"id": "s1", "text": trial}], "blog", brand_derived)
    trial_match = style_match(trial, profile)
    out["writer_trial"] = {
        "craft_before": trial_before["craft"]["score"],
        "craft_after": trial_craft["score"],
        "craft_after_violations": [v["type"] for v in trial_craft["violations"]],
        "brand_before": trial_before["brand_check"]["score"],
        "brand_after": trial_lint[1],
        "brand_after_violations": [v["type"] for v in trial_lint[0]],
        "style_match_before": trial_before["style_match"]["score"],
        "style_match_after": trial_match["score"],
        "style_match_reliability_note": trial_match.get("reliability_note"),
        "style_match_metrics_after": trial_match["metrics"],
    }

    analysis = analyze_samples([s["text"] for s in samples])
    out["style_analysis_warnings_after"] = analysis["consistency"]["warnings"]
    out["style_analysis_warnings_before"] = [
        "samples diverge on first_person_rate (CV 2.0 > 0.35) — possibly mixed authors"]

    (WORK / "recheck_after_fixes.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
