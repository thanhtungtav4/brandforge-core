"""Pins for the performance fix round (PERF_REVIEW_2026-11-22.md).

Every refactor in the round must be behaviour-preserving; these tests
pin the new internal surface (prepared phrase tables, the bounded
normalizer cache, the linter's lazy preparation) against the legacy
behaviour it replaced.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from content_core import craft, linter                      # noqa: E402
from content_core.loaders import load_brand, load_brand_draft  # noqa: E402

TEXTS = [
    # weak/filler/sensory-dense copy with overlaps and masking interplay
    "Mộc Brew mang đến trải nghiệm cà phê đẳng cấp thế giới. "
    "Hạt thơm ngon, vị thơm rõ, hậu cacao đậm. Có thể nói rằng đây là "
    "sản phẩm tuyệt vời, hoàn hảo và chất lượng cao.",
    "Không thơm. Không có mùi cacao trong gói này. Mùi thơm của hạt "
    "rang thứ Ba vẫn rõ, thơm ngon và đậm đà.",
    "ngon ngon ngon. thơm thơm. đẳng cấp đẳng cấp thế giới thế giới.",
    "Có thể nói rằng có thể nói rằng ngày nay ngày nay thật sự là "
    "thực sự là nói chung là đáng chú ý là như chúng ta đã biết.",
    "a b a b a b. x1y2 z. số 1 số 12 số 185.",
    "",
    "một câu ngắn",
]

PHRASE_LISTS = [
    ["thơm ngon", "ngon", "thơm", "đẳng cấp thế giới", "đẳng cấp",
     "tuyệt vời", "hoàn hảo", "chất lượng cao"],
    craft.FILLER_PHRASES,
    ["mùi thơm", "thơm", "mùi", "hậu cacao", "cacao", "đậm đà", "đậm"],
    ["ngon", "ngon", "Thơm Ngon", "thơm ngon"],  # duplicates / same norm
    ["số 1", "số", "1"],  # digit-boundary guards; '1' forces legacy path
    ["a", "a b", "b"],  # single-char phrase forces the legacy path
]


def test_find_phrase_hits_matches_legacy_matcher():
    # The mask-array matcher must reproduce the legacy
    # rebuild-per-phrase matcher EXACTLY (terms, order, indices) on
    # every text × vocabulary combination, including the lists whose
    # single-character entries route to the legacy path itself.
    for phrases in PHRASE_LISTS:
        for text in TEXTS:
            assert craft._find_phrase_hits(text, phrases) == \
                craft._find_phrase_hits_legacy(text, phrases), (phrases, text)


def test_phrase_table_dedupes_and_orders_longest_first():
    table = craft._phrase_table(("ngon", "thơm ngon", "Thơm Ngon", "ngon"))
    matches = [m for _d, m, _rx in table]
    assert matches == ["thom ngon", "ngon"]  # longest first, deduped
    # The surviving display for a duplicated normalized form is the
    # first in legacy order (stable sort by length, then list order).
    assert table[0][0] == "thơm ngon"


def test_normalize_cache_only_holds_short_inputs():
    linter._normalize_for_match_cached.cache_clear()
    short = "Đẳng cấp thế giới"
    assert linter.normalize_for_match(short) == "dang cap the gioi"
    info = linter._normalize_for_match_cached.cache_info()
    assert info.currsize == 1
    linter.normalize_for_match(short)  # second call: a hit, not a re-add
    info = linter._normalize_for_match_cached.cache_info()
    assert info.currsize == 1 and info.hits >= 1
    long_text = "cà phê " * 100  # 700 chars > the 256 cap: bypasses
    assert linter.normalize_for_match(long_text).startswith("ca phe")
    info = linter._normalize_for_match_cached.cache_info()
    assert info.currsize == 1  # unchanged — long inputs never enter


def test_lint_zero_banned_brand_still_scores_other_rules():
    # Perf P5: a brand with NO banned terms skips the banned-term
    # preparation entirely — the other lint rules must be unaffected.
    brand = load_brand_draft("mambagiaokhoe")
    assert linter.banned_terms(brand) == []
    text = ("Một câu ngắn. Hai câu ngắn nữa đây. Ba câu cũng ngắn thôi. "
            "Bốn câu vẫn ngắn như vậy luôn nhé bạn ơi!")
    violations, score = linter.lint_section(text, "blog", brand, "s1")
    types = {v["type"] for v in violations}
    assert "exclamation_in_blog" in types  # punctuation rule still runs
    assert not any(t.startswith("banned_term") for t in types)
    assert score < 100


def test_lint_with_banned_terms_unchanged_for_server_brand():
    # The lazy whole-text preparation must not lose the letter-dotted
    # evasion catch (the whole-text pass exists for exactly this).
    brand = load_brand()
    assert linter.banned_terms(brand)  # Mộc Brew has banned terms
    violations, _score = linter.lint_section(
        "Trải nghiệm d.e.l.v.e vào từng ngụm cà phê.", "blog", brand, "s1")
    assert any(v["type"] == "banned_term" and v["detail"] == "delve"
               for v in violations)


# -- P2c: the per-character-table normalizer --------------------------------
# The ORIGINAL whole-string implementation, kept here as the reference
# the shipped per-character version must equal on every input.
import re as _re
import unicodedata as _unicodedata


def _reference_normalize(text: str) -> str:
    lowered = (text or "").lower()
    for ch in linter._FORMAT_CHARS:
        lowered = lowered.replace(ch, "")
    lowered = lowered.replace("đ", "d")
    folded = _unicodedata.normalize("NFKC", lowered)
    decomposed = _unicodedata.normalize("NFD", folded)
    stripped = "".join(c for c in decomposed if not _unicodedata.combining(c))
    mapped = "".join(linter._CONFUSABLES.get(c, c) for c in stripped)
    return _re.sub(r"[^a-z0-9]+", " ", mapped).strip()


def test_normalizer_matches_reference_implementation():
    import json
    samples: list[str] = []
    for name in ("craft_library.json", "exemplars.json",
                 "domain_glossary.json", "style_samples_mocbrew.json"):
        samples.append((PROJECT_DIR / "data" / name).read_text(encoding="utf-8"))
    samples += [s["text"] for s in
                json.loads((PROJECT_DIR / "data" / "style_samples_mocbrew.json")
                           .read_text(encoding="utf-8"))["samples"]]
    samples += TEXTS
    # Exhaustive over the ranges that carry Vietnamese + Latin +
    # combining behaviour, plus every combining mark paired with the
    # common Vietnamese bases (the cross-boundary composition shapes).
    samples += [chr(cp) for cp in range(0x0000, 0x3000)]
    samples += [chr(cp) for cp in range(0x1E00, 0x1F00)]
    samples += [chr(cp) for cp in range(0x2000, 0x2100)]
    bases = "aăâeêioôơuưyđAĂÂEÊIOÔƠUƯYĐ"
    samples += [b + chr(cp) for b in bases
                for cp in range(0x0300, 0x0370)]
    samples += [b + chr(cp) + chr(cp2) for b in "aâăơư"
                for cp in (0x0300, 0x0301, 0x0302, 0x0323)
                for cp2 in (0x0300, 0x0323)]
    for s in samples:
        assert linter.normalize_for_match(s) == _reference_normalize(s), repr(s)
        assert linter._normalize_for_match_uncached(s) == \
            _reference_normalize(s), repr(s)
