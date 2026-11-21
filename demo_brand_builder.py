#!/usr/bin/env python3
"""Demo: Brand Builder end-to-end on the two fixture sites (no network —
fetch_site runs over file:// URLs into tests/fixtures/).

Flow shown, exactly the product workflow:
  fetch_site -> analyze (kit / voice suggestions / claims)
  -> the "model" (this script) fills the qualitative schema
  -> save draft (own site) -> convert to brand YAML
  -> competitor draft: reference_only examples + converter REFUSAL
  -> compare mode: my site vs the competitor, gaps from the numbers.

Usage: .venv/bin/python demo_brand_builder.py   (exit 0 on success)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR))

from content_core.brand_extract import (build_brand_draft,  # noqa: E402
                                        compare_brand_voice,
                                        draft_to_brand_yaml,
                                        extract_brand_kit,
                                        find_site_claims,
                                        suggest_voice_rules, site_corpus)
from fetch_site import fetch_site  # noqa: E402

FIXTURES = PROJECT_DIR / "tests" / "fixtures"

# The qualitative schema, filled by the "model" after reading the pages.
QUAL_SOM = {"tone_traits": [
                {"dimension": "directness", "position": "nói thẳng, nêu fact trước",
                 "do": "ghi giá và ngày rang rõ ràng", "dont": "hype, hứa hẹn lớn"},
                {"dimension": "formality", "position": "thân thiện như người quen",
                 "do": "xưng mình, gọi bạn", "dont": "văn phong công văn, 'quý khách'"}],
            "audience_guess": "dân văn phòng 25-35 uống cà phê mỗi sáng",
            "positioning": "cà phê rang mẻ nhỏ, minh bạch ngày rang, bán trực tiếp",
            "differentiators": ["ghi ngày rang lên túi", "chỉ rang khi có đơn"]}

QUAL_DINHCAO = {"tone_traits": [
                    {"dimension": "confidence", "position": "tự xưng số 1",
                     "do": "dùng superlative liên tục", "dont": "nói giảm, nói tránh"}],
                "audience_guess": "khách mua theo khuyến mãi, nhạy giá",
                "positioning": "thương hiệu lớn, giá rẻ nhất, đẳng cấp",
                "differentiators": []}


def main() -> int:
    som = fetch_site(f"file://{FIXTURES}/som/index.html")
    dinhcao = fetch_site(f"file://{FIXTURES}/dinhcao/index.html")
    som_pages, dinhcao_pages = som["pages"], dinhcao["pages"]
    print(f"fetched: Sớm {len(som_pages)} pages, Đỉnh Cao {len(dinhcao_pages)} pages\n")

    # --- own site: draft + conversion -------------------------------------
    draft = build_brand_draft(som_pages, QUAL_SOM, "own_site")
    kit = draft["brand_kit"]
    print("=== DRAFT BRAND — Sớm Coffee (own_site) ===")
    print(f"brand: {draft['brand']['name']} (id={draft['brand']['id']}, "
          f"status={draft['brand']['review_status']})")
    print(f"kit: theme={kit['theme_color']['value']} "
          f"colors={[c['hex'] for c in kit['colors']]} "
          f"fonts={[f['family'] for f in kit['fonts']]} "
          f"logo={kit['logo_url']['value'].split('/')[-1]}")
    print(f"voice: pov={draft['voice']['pov']} reader={draft['voice']['reader_address']} "
          f"median={draft['voice']['sentence_rules']['median_words_default']} "
          f"banned_terms={draft['voice']['banned_terms']} (user must add)")
    print(f"traits (model-proposed): "
          f"{[t['dimension'] + ': ' + t['position'] for t in draft['voice']['traits']]}")
    print(f"vocabulary.use: {[u['term'] for u in draft['vocabulary']['use']]}")
    print(f"vocabulary.exact: {draft['vocabulary']['exact']}")
    print(f"examples: {len(draft['examples'])} passage(s) from the site itself")
    print(f"review_notes: {len(draft['review_notes'])} note(s) for the user")

    brand_yaml = draft_to_brand_yaml(draft)
    brand = yaml.safe_load(brand_yaml)
    assert brand["brand"]["name"] == "Sớm Coffee" and "review_notes" not in brand
    print("draft_to_brand_yaml: OK — converts to a loadable brand profile\n")

    # --- competitor: analysis only, conversion refused --------------------
    comp = build_brand_draft(dinhcao_pages, QUAL_DINHCAO, "competitor")
    print("=== DRAFT — Đỉnh Cao Coffee (competitor) ===")
    print(f"examples reference_only: "
          f"{[e.get('reference_only') for e in comp['examples']]}")
    print(f"site claims to review: "
          f"{len(comp['claims_and_guardrails']['site_claims_to_review'])}")
    try:
        draft_to_brand_yaml(comp)
        print("ERROR: competitor draft converted — guard failed")
        return 1
    except ValueError as exc:
        print(f"draft_to_brand_yaml: REFUSED as designed ({str(exc)[:70]}...)\n")

    # --- compare ------------------------------------------------------------
    out = compare_brand_voice(som_pages, dinhcao_pages)
    print(f"=== COMPARE — {out['labels']['a']} (A) vs {out['labels']['b']} (B) ===")
    for gap in out["gaps"]:
        print(f" - {gap}")
    print(f"\nclaims: A={out['claims']['a']} B={out['claims']['b']} | "
          f"corpus words: A={out['corpus']['a']['words']} B={out['corpus']['b']['words']}")
    print(f"kit colors: A={[c['hex'] for c in out['brand_kit']['a']['colors'][:3]]} "
          f"B={[c['hex'] for c in out['brand_kit']['b']['colors'][:3]]}")
    assert out["gaps"]
    print("\nBrand Builder demo OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
