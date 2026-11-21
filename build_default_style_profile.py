#!/usr/bin/env python3
"""Build the shipped default Style Profile: data/style_profiles/mocbrew-blog-voice.json

Reads the 3 ORIGINAL Mộc Brew sample texts + the model-filled qualitative
schema from data/style_samples_mocbrew.json, runs style.build_profile over
them and persists the result through loaders.save_style_profile (the one
place style-profile file IO lives). store_excerpts=True because the samples
are our own simulated brand's texts.

Usage: .venv/bin/python build_default_style_profile.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR))

from content_core.loaders import DATA_DIR, DEFAULT_STYLE_PROFILE_ID, save_style_profile  # noqa: E402
from content_core.style import build_profile  # noqa: E402


def main() -> int:
    data = json.loads((DATA_DIR / "style_samples_mocbrew.json").read_text(encoding="utf-8"))
    samples = [s["text"] for s in data["samples"]]
    profile = build_profile(
        "Mộc Brew Blog Voice", samples,
        qualitative=data.get("qualitative"),
        store_excerpts=True,
        brand_id=data.get("brand_id", "moc-brew"),
        profile_id=DEFAULT_STYLE_PROFILE_ID)
    save_style_profile(profile)
    fp = profile["fingerprint"]
    print(f"saved profile {profile['id']} "
          f"(samples={profile['created_from']['sample_count']}, "
          f"words={profile['created_from']['total_words']})")
    print(f"fingerprint: sentence median={fp['sentence_len']['median']} "
          f"stdev={fp['sentence_len']['stdev']} "
          f"short_share={fp['short_sentence_share']} "
          f"first_person={fp['first_person_rate']}/100w "
          f"reader={fp['reader_rate']}/100w "
          f"numbers={fp['number_density']}/100w")
    print(f"warnings: {profile['consistency_warnings']}")
    print(f"excerpts stored: {len(profile['exemplars'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
