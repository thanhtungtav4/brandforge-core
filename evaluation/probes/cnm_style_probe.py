"""CNM regression + style-discrimination probe.

- Cao Nguyên Mộc's 5 real posts (calibration/work/samples.json): craft
  score with the CNM glossary, brand lint violations vs the CNM brand,
  style_match vs the CNM profile. Post-calibration-fix baseline
  (8215b8c, calibration/work/recheck_after_fixes.json): craft all 90,
  brand violations 10 total, style_match 92-100.
- Đỉnh Cao hype fixture vs the Mộc Brew profile: R4 measured 100
  (saturation); the fix must pull it down materially.
- CNM writer-trial section vs the CNM profile: baseline 85.

    python evaluation/probes/cnm_style_probe.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from content_core.craft import craft_check
from content_core.linter import lint_draft
from content_core.loaders import load_brand, load_style_profile
from content_core.style import style_match
from fetch_sample import strip_html


def main() -> None:
    samples = json.loads((ROOT / "calibration/work/samples.json").read_text(encoding="utf-8"))
    posts = samples if isinstance(samples, list) else samples.get("posts", samples)
    if isinstance(posts, dict):
        posts = list(posts.values())
    cnm_brand = load_brand(ROOT / "calibration/caonguyenmoc-brand.yaml")
    # Mirror calibration/work/recheck_after_fixes.py exactly: the posts
    # are judged with the DERIVED cap (suggest_voice_rules over the
    # pages_setB corpus; p90 = 39), not the YAML's default 25.
    from content_core.brand_extract import suggest_voice_rules
    pages_b = json.loads((ROOT / "calibration/work/pages_setB.json").read_text(encoding="utf-8"))
    if isinstance(pages_b, dict):
        pages_b = pages_b.get("pages", [])
    corpus_text = "\n\n".join(p.get("text", "") for p in pages_b)
    derived = suggest_voice_rules(corpus_text, brand_name="Cao Nguyên Mộc")
    derived_max = derived["sentence_rules"]["max_sentence_words"]
    print(f"derived max_sentence_words for CNM corpus: {derived_max}")
    cnm_brand["voice"]["sentence_rules"]["max_sentence_words"] = derived_max
    cnm_profile = load_style_profile("cao-nguyen-moc-blog-voice-dd5678")

    total_violations = 0
    print(f"{'post':<38} {'craft':>6} {'brand':>6} {'viol':>5} {'style':>6}")
    for post in posts:
        text = post.get("text") or post.get("content") or ""
        slug = (post.get("slug") or post.get("url") or "?")[-38:]
        craft = craft_check(text, "blog", brand_id="cao-nguyen-moc")
        violations, brand_score, _f = lint_draft(
            [{"id": "s1", "text": text}], "blog", cnm_brand)
        total_violations += len(violations)
        style = style_match(text, cnm_profile)
        print(f"{slug:<38} {craft['score']:>6} {brand_score:>6} "
              f"{len(violations):>5} {style['score']:>6}")
    print(f"total brand violations across posts: {total_violations}")

    trial = (ROOT / "calibration/work/writer_trial.txt").read_text(encoding="utf-8")
    print(f"writer trial style_match vs CNM profile: "
          f"{style_match(trial, cnm_profile)['score']}")

    hype_parts = []
    for path in sorted((ROOT / "tests/fixtures/dinhcao").glob("*.html")):
        hype_parts.append(strip_html(path.read_text(encoding="utf-8"))[1])
    hype_text = "\n\n".join(hype_parts)
    moc_profile = load_style_profile("mocbrew-blog-voice")
    out = style_match(hype_text, moc_profile)
    print(f"Đỉnh Cao hype vs mocbrew profile: {out['score']} "
          f"(metrics failed: {[m['metric'] for m in out['metrics'] if not m['within_range']]})")


if __name__ == "__main__":
    main()
