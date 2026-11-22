"""Fail-first tests for the three trial-surfaced core bugs (22 Nov 2026,
production trial `trials/mam-ca-loc-run.md`):

BUG 1 — the WRITER-FACING tools ignored brief.brand_id: validate_brief,
assemble_context and brand_check resolved the server brand (Mộc Brew /
persona Linh) even when the brief named another brand, while the gate
path (fact_check / final_gate / quality_score) resolved the brief's
brand correctly. A writer following assemble_context blindly would
have written as Linh for a coffee brand. These tools now resolve brand
(+ persona, when the brief carries persona_id) from the brief exactly
like the gate path; the server brand is only the default when the
brief names no brand.

BUG 2 — the Brand Builder's sentence-band suggestion was corruptible
by fetched boilerplate: on mambagiaokhoe.com the corpus's repeated
nav/cart/sidebar blocks (no sentence punctuation) glued into giant
pseudo-sentences, so the suggested median band came out [28, 30] and
needed a manual operator edit. Corpus statistics are now computed on
the prose that survives boilerplate filtering (repeated segments
deduped, boilerplate-like segments excluded) — the calibration
round's table-like filtering, one level up.

BUG 3 — draft_to_brand_yaml silently dropped the draft's `visual`
section, so a brand approved via YAML lost its image style lock.
Conversion now carries `visual` through (validated), and an invalid
visual fails loudly naming the field instead of vanishing.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
import yaml

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import mcp_server as srv  # noqa: E402
from content_core.brand_extract import (draft_to_brand_yaml,  # noqa: E402
                                        site_corpus, suggest_voice_rules)
from content_core.loaders import save_brand_draft  # noqa: E402
from content_core.style import fingerprint  # noqa: E402
from fetch_site import fetch_site  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# ---------------------------------------------------------------------------
# BUG 1 fixtures — a second brand ("brand-b") + second persona ("Bình")
# in fully isolated stores. brand-b's rules are chosen to DISAGREE with
# Mộc Brew's on every axis the tools consult: median band [20, 26] vs
# [11, 14], max sentence 40 vs 25, a banned term and a prohibited claim
# Mộc Brew does not have, a different price and a distinctive lorebook
# entry — so a check that silently used the server brand is visible.
# ---------------------------------------------------------------------------

BRAND_B_DRAFT = {
    "brand": {"id": "brand-b", "name": "Brand B Test", "simulated": True,
              "version": 0, "review_status": "draft",
              "source_type": "own_site", "source_urls": []},
    "voice": {
        "traits": [],
        "pov": "first-person",
        "reader_address": "bạn",
        "sentence_rules": {"median_words_default": [20, 26],
                           "median_words_by_format": {},
                           "max_sentence_words": 40},
        "formatting_rules": [],
        "banned_terms": ["mambamia"],
        "prohibited_claims": ["giảm cân thần tốc"],
    },
    "vocabulary": {"use": [], "exact": [], "banned": [], "glossary": []},
    "examples": [{"format": "blog", "text": "Ví dụ mẫu của Brand B.",
                  "why_it_works": ""}],
    "anti_examples": [],
    "claims_and_guardrails": {"requires_evidence": [],
                              "prohibited_claims": [],
                              "unsupported_claim_marker": "[NEEDS SOURCE]"},
    "brand_kit": {},
    "qualitative": {},
    "review_notes": [],
}

BRAND_B_FACTS = {
    "facts": {"price_vnd_per_250g": 50000, "pack_weight_g": 250,
              "origins": ["Bến Tre"]},
    "entries": [{"id": "brandb-entry", "keys": ["mắm"],
                 "content": "ZXQ Brand B fact: hủ 250g giá 50.000đ.",
                 "constant": False}],
}

PERSONA_BINH = {
    "spec": "chara_card_v2", "spec_version": "2.0",
    "data": {"name": "Bình",
             "system_prompt": "PERSONA-BINH-MARKER: bạn là Bình.",
             "description": "Người viết của Brand B.",
             "personality": "điềm đạm, nói có chứng cứ",
             "scenario": "Viết bài cho Brand B.",
             "mes_example": "<START> Bình: chào bạn.",
             "first_mes": "Chào bạn.",
             "post_history_instructions": "BINH-REMINDER: giữ giọng Bình.",
             "character_book": {"entries": []}},
}


@pytest.fixture()
def brand_b(tmp_path, monkeypatch):
    for var, sub in (("CONTENT_CORE_DRAFTS_DIR", "drafts"),
                     ("CONTENT_CORE_BRAND_DRAFTS_DIR", "brand_drafts"),
                     ("CONTENT_CORE_FACTS_PROFILES_DIR", "facts"),
                     ("CONTENT_CORE_PERSONAS_DIR", "personas")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    pdir = tmp_path / "personas"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "persona_binh.json").write_text(
        json.dumps(PERSONA_BINH, ensure_ascii=False), encoding="utf-8")
    # The store is the store: seed it with the shipped default card
    # too, exactly as a real data dir carries persona_linh.json.
    linh = (PROJECT_DIR / "data" / "persona_linh.json").read_text(
        encoding="utf-8")
    (pdir / "persona_linh.json").write_text(linh, encoding="utf-8")
    save_brand_draft(BRAND_B_DRAFT)
    saved = srv.save_facts("brand-b", BRAND_B_FACTS)
    assert "error" not in saved
    return tmp_path


def _words(n: int, token: str = "chữ") -> str:
    return " ".join([token] * n)


def _part(context_text: str, name: str) -> str:
    """One [N. NAME] segment of an assemble_context context_text."""
    parts = re.split(r"\[\d+\. [A-Z_]+\]\n", context_text)
    headers = re.findall(r"\[(\d+)\. ([A-Z_]+)\]", context_text)
    for (_num, part_name), body in zip(headers, parts[1:]):
        if part_name == name:
            return body
    raise AssertionError(f"part {name} not found in {headers}")


# -- BUG 1: validate_brief ---------------------------------------------------

def test_validate_brief_resolves_brief_brand_and_persona(brand_b):
    out = srv.validate_brief({"format": "blog", "topic": "mắm Bến Tre",
                              "brand_id": "brand-b", "persona_id": "Bình"})
    assert out["valid"] is True
    assert out["brand_id"] == "brand-b"
    assert out["persona_id"] == "Bình"


def test_validate_brief_refusal_uses_brief_brand_claims(brand_b):
    # "giảm cân thần tốc" is prohibited by brand-b ONLY — the server
    # brand does not prohibit it, so a refusal here can only come from
    # resolving the brief's brand.
    out = srv.validate_brief({"format": "blog", "topic": "mắm giảm cân",
                              "brand_id": "brand-b",
                              "requested_claims": ["giảm cân thần tốc"]})
    assert out["valid"] is False
    assert out["status"] == "refused"
    assert out["refused_claims"]
    # Same claim, no brand named -> the server brand's rules apply and
    # the brief is clean: the refusal above was brand-b's, not a fluke.
    default = srv.validate_brief({"format": "blog", "topic": "mắm giảm cân",
                                  "requested_claims": ["giảm cân thần tốc"]})
    assert default["valid"] is True
    assert default["refused_claims"] == []


def test_validate_brief_unknown_brand_is_structured_error(brand_b):
    out = srv.validate_brief({"format": "blog", "topic": "mắm",
                              "brand_id": "khong-co-brand-nay"})
    assert "error" in out


def test_validate_brief_default_brand_unchanged(brand_b):
    out = srv.validate_brief({"format": "blog", "topic": "cà phê văn phòng"})
    assert out["valid"] is True
    assert out["brand_id"] == "moc-brew"
    assert out["persona_id"] == "Linh"


# -- BUG 1: assemble_context ---------------------------------------------------

def test_assemble_context_resolves_brief_brand_persona_facts(brand_b):
    brief = {"format": "blog", "topic": "mắm cá lóc Bến Tre",
             "keyword": "mắm", "title": "Mắm Brand B",
             "brand_id": "brand-b", "persona_id": "Bình"}
    out = srv.assemble_context(brief)
    assert "error" not in out
    assert out["brand_id"] == "brand-b"
    assert out["persona_id"] == "Bình"
    text = out["context_text"]
    # Persona part: Bình's card, not Linh's.
    persona_part = _part(text, "PERSONA")
    assert "PERSONA-BINH-MARKER" in persona_part
    assert "Linh" not in persona_part
    # Brand part: brand-b's name and ITS banned terms.
    brand_part = _part(text, "BRAND_RULES")
    assert "Brand B Test" in brand_part
    assert "mambamia" in brand_part
    # Facts part: brand-b's triggered lorebook entry, not Mộc Brew's
    # price entry — the trial's failure was coffee facts (185.000đ)
    # served for a mắm brand.
    facts_part = _part(text, "FACTS_TRIGGERED")
    assert "ZXQ" in facts_part
    assert "185.000đ" not in facts_part


def test_assemble_context_unknown_brand_is_structured_error(brand_b):
    out = srv.assemble_context({"format": "blog", "topic": "mắm",
                                "brand_id": "khong-co-brand-nay"})
    assert "error" in out


def test_assemble_context_default_brand_unchanged(brand_b):
    out = srv.assemble_context({"format": "blog", "topic": "cà phê văn phòng giá",
                                "keyword": "cà phê", "title": "Cà phê văn phòng"})
    assert out["brand_id"] == "moc-brew"
    assert out["persona_id"] == "Linh"
    assert "185.000đ" in _part(out["context_text"], "FACTS_TRIGGERED")


# -- BUG 1: brand_check --------------------------------------------------------

# 23-word sentences: median 23 sits inside brand-b's band [20, 26] and
# under its 40-word cap, but OUTSIDE Mộc Brew's band [11, 14] — so the
# same text passes under brand-b and fails the median rule under the
# server brand. Clean otherwise (no banned terms, no exclamations).
TEXT_23 = " ".join(_words(22, f"w{i}") + " hết." for i in range(5))


def test_brand_check_draft_uses_brief_brand(brand_b):
    draft = srv.start_draft({"format": "blog", "topic": "mắm",
                             "brand_id": "brand-b"})
    srv.save_section(draft["draft_id"], "s1", "Mục một", TEXT_23)
    out = srv.brand_check(draft_id=draft["draft_id"])
    assert "error" not in out
    assert out["brand_id"] == "brand-b"
    assert out["score"] == 100
    assert out["failing_sections"] == []
    # The same text under the server brand (no draft) fails the median
    # rule — proving the draft path above did not use Mộc Brew's rules.
    default = srv.brand_check(text=TEXT_23, format="blog")
    assert default["brand_id"] == "moc-brew"
    assert any(v["type"] == "median_sentence_length"
               for v in default["violations"])
    assert default["score"] < 100


def test_brand_check_explicit_brand_id_and_banned_terms(brand_b):
    text = "Mambamia là từ bị cấm của riêng Brand B trong câu này thôi."
    out = srv.brand_check(text=text, format="blog", brand_id="brand-b")
    assert out["brand_id"] == "brand-b"
    assert "mambamia" in out["banned_found"]
    default = srv.brand_check(text=text, format="blog")
    assert "mambamia" not in default["banned_found"]


def test_brand_check_unknown_brand_is_structured_error(brand_b):
    out = srv.brand_check(text="câu ngắn.", format="blog",
                          brand_id="khong-co-brand-nay")
    assert "error" in out
    draft = srv.start_draft({"format": "blog", "topic": "mắm",
                             "brand_id": "khong-co-brand-nay"})
    srv.save_section(draft["draft_id"], "s1", "Mục một", TEXT_23)
    out2 = srv.brand_check(draft_id=draft["draft_id"])
    assert "error" in out2


# ---------------------------------------------------------------------------
# BUG 2 — boilerplate-robust corpus statistics
# ---------------------------------------------------------------------------

def _bagiaokhoe_pages() -> list[dict]:
    doc = json.loads((FIXTURES / "bagiaokhoe" / "pages.json")
                     .read_text(encoding="utf-8"))
    return doc["pages"]


def test_builder_band_robust_on_polluted_real_corpus():
    pages = _bagiaokhoe_pages()
    corpus = site_corpus(pages)
    # The fixture really is polluted: measured RAW, the fingerprint
    # median is chrome-inflated (the trial measured band [28, 30]).
    raw_median = fingerprint(corpus["all_text"])["sentence_len"]["median"]
    assert raw_median >= 30
    sug = suggest_voice_rules(corpus["all_text"],
                              brand_name="Mắm Bà Giáo Khỏe")
    median = sug["fingerprint_summary"]["sentence_median"]
    lo, hi = sug["sentence_rules"]["median_words_default"]
    # The site's real prose (giới thiệu paragraphs) medians ≈ 27; the
    # suggestion must be derived from THAT, with no manual edit.
    assert 22 <= median <= 30
    assert lo <= 23 and hi <= 30 and lo < hi
    assert [lo, hi] != [28, 30]  # the corrupt trial output
    assert sug["sentence_rules"]["max_sentence_words"] <= 45


def test_builder_band_robust_on_synthetic_chrome():
    chrome = [
        "Trang chủ Giới thiệu Sản phẩm Tin tức Liên hệ",
        "Giỏ hàng Giỏ hàng 0",
        "Gọi ngay 0903 999 117",
        "Bản quyền thuộc về công ty ví dụ mọi hành vi sao chép đều bị cấm",
    ]
    prose = [
        "Người dân quê tôi làm mắm từ cá đồng tươi mỗi mùa nước nổi về.",
        "Hủ mắm ủ đủ ngày thì thịt cá đỏ au và thơm dịu dễ chịu.",
        "Bữa cơm quê chỉ cần thêm dĩa rau đồng là đã thấy ngon miệng rồi.",
        "Khách xa về thăm quê hay mua vài hủ mắm mang đi làm quà biếu.",
        "Người làm mắm chọn cá tươi và muối hạt to để ướp cho đều vị.",
        "Tiệm nhỏ nằm ngay đầu chợ nên khách ghé mua mắm rất thuận tiện luôn.",
    ]
    assert all(len(s.split()) == 15 for s in prose)  # prose median = 15
    pages = []
    for i in range(3):
        blocks = chrome[:2] + [prose[2 * i]] + chrome[2:] + [prose[2 * i + 1]]
        pages.append({"url": f"https://vi-du.vn/p{i}", "title": "Ví dụ",
                      "text": "\n\n".join(blocks)})
    corpus = site_corpus(pages)
    raw_median = fingerprint(corpus["all_text"])["sentence_len"]["median"]
    sug = suggest_voice_rules(corpus["all_text"], brand_name="Ví Dụ")
    # Raw, the unpunctuated chrome glues onto the prose and inflates
    # the median far above the prose's own 15.
    assert raw_median >= 25
    assert sug["fingerprint_summary"]["sentence_median"] == 15
    assert sug["sentence_rules"]["median_words_default"] == [12, 18]


def test_fixture_site_suggestions_do_not_move():
    # Sớm Coffee's fetched text carries no repeated/boilerplate blocks,
    # so filtering is the identity there and its suggestions must be
    # EXACTLY the pre-fix values.
    som = fetch_site(f"file://{FIXTURES}/som/index.html")["pages"]
    sug = suggest_voice_rules(site_corpus(som)["all_text"])
    assert sug["sentence_rules"]["median_words_default"] == [15, 21]
    assert sug["sentence_rules"]["max_sentence_words"] == 25
    assert sug["pov"] == "first-person"
    # Đỉnh Cao: filtering may only nudge the median by a word.
    din = fetch_site(f"file://{FIXTURES}/dinhcao/index.html")["pages"]
    sug_d = suggest_voice_rules(site_corpus(din)["all_text"])
    assert sug_d["pov"] == "third-person"
    assert abs(sug_d["fingerprint_summary"]["sentence_median"] - 16.0) <= 1.0


# ---------------------------------------------------------------------------
# BUG 3 — conversion carries the visual section
# ---------------------------------------------------------------------------

def _minimal_draft(**overrides) -> dict:
    draft = {
        "brand": {"id": "brand-b", "name": "Brand B"},
        "voice": {"traits": [], "pov": "first-person",
                  "reader_address": "bạn",
                  "sentence_rules": {"median_words_default": [20, 26],
                                     "median_words_by_format": {},
                                     "max_sentence_words": 40},
                  "formatting_rules": [], "banned_terms": [],
                  "prohibited_claims": []},
        "examples": [], "anti_examples": [],
        "claims_and_guardrails": {},
        "brand_kit": {},
    }
    draft.update(overrides)
    return draft


VISUAL = {"style_summary": "warm natural-light delta food photography",
          "palette": ["#A0522D", "cream"], "medium": "photo",
          "mood": "warm, rustic", "composition": "close-up",
          "avoid": ["text overlays", "glossy studio look"]}


def test_conversion_carries_visual():
    brand = yaml.safe_load(draft_to_brand_yaml(_minimal_draft(visual=VISUAL)))
    assert brand["visual"] == VISUAL


def test_conversion_visual_string_palette_normalised():
    visual = dict(VISUAL, palette="brown, cream")
    brand = yaml.safe_load(draft_to_brand_yaml(_minimal_draft(visual=visual)))
    assert brand["visual"]["palette"] == ["brown", "cream"]


def test_conversion_invalid_visual_fails_loudly():
    visual = dict(VISUAL, palette=123)
    with pytest.raises(ValueError, match="palette"):
        draft_to_brand_yaml(_minimal_draft(visual=visual))


def test_conversion_without_visual_has_no_visual_key():
    brand = yaml.safe_load(draft_to_brand_yaml(_minimal_draft()))
    assert "visual" not in brand
