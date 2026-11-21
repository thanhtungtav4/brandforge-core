"""Core-completion round (21 Nov 2026) — regression tests for the weaknesses
left observed-but-unfixed by audit round 2. Every test here was written
BEFORE its fix and failed against the pre-fix code (except the two boundary
pins, which document a deliberate limitation and passed both ways)."""
from __future__ import annotations

import os
import statistics
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import mcp_server as srv  # noqa: E402
from content_core import loaders  # noqa: E402
from content_core.craft import craft_check, craft_check_sections, retrieve_cards  # noqa: E402
from content_core.fact_check import check_section  # noqa: E402
from content_core.linter import sentence_lengths, split_sentences  # noqa: E402
from content_core.loaders import load_brand, load_facts  # noqa: E402

BRAND = load_brand()
FACTS = load_facts()


def _types(issues):
    return {i["type"] for i in issues}


# --- 1. craft_check must not score empty / fragment text as good prose ------

@pytest.mark.parametrize("text", ["", "   \n  ", "Cà phê ngon", "Một hai ba"])
def test_craft_check_too_short_scores_zero(text):
    out = craft_check(text, "blog")
    assert out["score"] == 0.0
    assert any(v["type"] == "too_short_to_judge" for v in out["violations"])
    assert out["metrics"]["word_count"] == len(text.split())


def test_craft_check_exactly_min_words_is_judged_normally():
    out = craft_check("Một hai ba bốn năm sáu bảy tám chín mười", "social")
    assert not any(v["type"] == "too_short_to_judge" for v in out["violations"])
    assert out["score"] > 0


def test_craft_check_sections_short_section_scores_zero():
    out = craft_check_sections([{"id": "s1", "text": "Quá ngắn"}], "social")
    assert out["score"] == 0.0
    assert "s1" in out["failing_sections"]


# --- 2. origin pairing is negation-aware -------------------------------------

def test_negated_wrong_origin_sourcing_passes():
    issues, _ = check_section(
        "Robusta không lấy từ Cầu Đất, vị vẫn đậm và có crema dày.", FACTS, BRAND)
    assert "wrong_origin" not in _types(issues)


def test_negated_wrong_origin_khong_phai_passes():
    issues, _ = check_section(
        "Không phải Robusta Cầu Đất đâu, bạn đừng nhầm nguồn hạt.", FACTS, BRAND)
    assert "wrong_origin" not in _types(issues)


def test_asserted_wrong_origin_still_flagged():
    issues, _ = check_section("Hạt Robusta Cầu Đất cho vị đậm và crema dày.", FACTS, BRAND)
    assert "wrong_origin" in _types(issues)


def test_khong_chi_wrong_origin_still_flagged():
    # "không chỉ ... mà còn" AFFIRMS the sourcing — it is not a denial.
    issues, _ = check_section(
        "Robusta không chỉ lấy từ Cầu Đất mà còn được rang rất đậm vị.", FACTS, BRAND)
    assert "wrong_origin" in _types(issues)


# --- 3. Vietnamese abbreviations do not tear sentences ------------------------

ABBR_TEXT = ("Văn phòng nằm tại TP. Hồ Chí Minh và do ThS. Nguyễn phụ trách. "
             "Mình rang cà phê vào thứ Ba hàng tuần cho bạn. "
             "Một gói 250g có giá 185.000đ trước khi nói tới hương vị.")


def test_abbreviations_keep_sentence_count_and_median():
    sentences = split_sentences(ABBR_TEXT)
    assert len(sentences) == 3
    assert "TP. Hồ Chí Minh" in sentences[0] and "ThS. Nguyễn" in sentences[0]
    assert sentence_lengths(ABBR_TEXT) == [14, 11, 12]
    assert statistics.median(sentence_lengths(ABBR_TEXT)) == 12


def test_district_abbreviation_q1():
    text = "Quán nằm ở Q. 1 và mở cửa từ sớm. Mình rang cà phê vào thứ Ba hàng tuần."
    assert split_sentences(text) == [
        "Quán nằm ở Q. 1 và mở cửa từ sớm",
        "Mình rang cà phê vào thứ Ba hàng tuần",
    ]
    assert sentence_lengths(text) == [10, 9]


def test_stacked_academic_titles_do_not_split():
    text = "Bài viết do PGS. Nguyễn và TS. Trần hiệu đính. Mình rang cà phê vào thứ Ba."
    assert len(split_sentences(text)) == 2


def test_normal_sentence_final_periods_still_split():
    assert split_sentences("Mình rang cà phê. Bạn pha phin ngay.") == [
        "Mình rang cà phê", "Bạn pha phin ngay"]


# --- 4. single-value delivery is checked against the fact range --------------

def test_single_delivery_inside_range_passes():
    issues, _ = check_section(
        "Đơn hàng được giao trong 3 ngày sau khi bạn xác nhận.", FACTS, BRAND)
    assert issues == []


def test_single_delivery_outside_range_flagged():
    issues, _ = check_section(
        "Đơn hàng được giao trong 5 ngày sau khi bạn xác nhận.", FACTS, BRAND)
    assert "wrong_delivery" in _types(issues)


def test_same_day_delivery_claim_flagged():
    issues, _ = check_section("Mộc Brew giao ngay trong ngày cho mọi đơn hàng.", FACTS, BRAND)
    assert "wrong_delivery" in _types(issues)


# --- 5. stored writes are atomic (temp file + os.replace) ---------------------

def _crash_replace(src, dst):
    raise OSError("simulated crash during replace")


def test_style_profile_save_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_CORE_STYLE_PROFILES_DIR", str(tmp_path))
    loaders.save_style_profile({"id": "atomic-voice", "name": "v1"})
    assert [p.name for p in tmp_path.iterdir()] == ["atomic-voice.json"]
    monkeypatch.setattr(os, "replace", _crash_replace)
    with pytest.raises(OSError):
        loaders.save_style_profile({"id": "atomic-voice", "name": "v2"})
    assert loaders.load_style_profile("atomic-voice")["name"] == "v1"


def test_draft_store_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_CORE_DRAFTS_DIR", str(tmp_path))
    srv._store_draft({"draft_id": "atomic-draft", "sections": [], "note": "v1"})
    monkeypatch.setattr(os, "replace", _crash_replace)
    with pytest.raises(OSError):
        srv._store_draft({"draft_id": "atomic-draft", "sections": [], "note": "v2"})
    assert srv._load_draft("atomic-draft")["note"] == "v1"


# --- 6. stored filenames are injective -----------------------------------------

def test_colliding_ids_round_trip_to_different_files(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_CORE_STYLE_PROFILES_DIR", str(tmp_path))
    loaders.save_style_profile({"id": "a/b", "name": "slash"})
    loaders.save_style_profile({"id": "ab", "name": "plain"})
    assert loaders._style_profile_path("a/b") != loaders._style_profile_path("ab")
    assert loaders.load_style_profile("a/b")["name"] == "slash"
    assert loaders.load_style_profile("ab")["name"] == "plain"


def test_clean_ids_keep_their_legacy_filename():
    assert loaders._style_profile_path("mocbrew-blog-voice").name == "mocbrew-blog-voice.json"
    assert loaders._quality_profile_path("preset-blog").name == "preset-blog.json"


# --- 7. retrieve_cards: empty format means NO format filtering -----------------

def test_retrieve_cards_empty_format_disables_format_filter():
    # show-dont-tell-sensory is blog/product only; with fmt="" there is no
    # format filter, so an intent/keyword hit still triggers it.
    cards = retrieve_cards("", "sensory", "vị hương hạt pha")
    assert "show-dont-tell-sensory" in [c["id"] for c in cards]
    cards = retrieve_cards("", "comparison", "so sánh đối thủ")
    assert "logical-transitions" in [c["id"] for c in cards]


def test_retrieve_cards_empty_format_still_keeps_dormancy():
    # No format filter does not wake dormant cards: intent/keyword
    # triggers still apply.
    cards = retrieve_cards("", "zzz-unrelated", "thời tiết biển hôm nay")
    assert [c["id"] for c in cards] == ["concrete-specifics"]


# --- 8. statistic-shaped claims written as Vietnamese words ---------------------

def test_word_number_statistic_flagged():
    issues, _ = check_section(
        "Đã có năm trăm khách hàng tin dùng gói cà phê này mỗi tháng.", FACTS, BRAND)
    assert "unsupported_number" in _types(issues)


def test_vague_word_quantifier_statistic_flagged():
    issues, _ = check_section(
        "Hàng nghìn người dùng đã chọn gói cà phê này cho văn phòng.", FACTS, BRAND)
    assert "unsupported_number" in _types(issues)


def test_word_number_statistic_marked_is_flagged_not_blocking():
    issues, flagged = check_section(
        "Đã có năm trăm khách hàng tin dùng gói này [NEEDS SOURCE].", FACTS, BRAND)
    assert "unsupported_number" not in _types(issues)
    assert any(f["type"] == "flagged_claim" for f in flagged)


def test_money_written_out_in_words_is_a_documented_boundary():
    # BOUNDARY PIN (passed before and after): money fully spelled out in
    # Vietnamese words is NOT parsed by the price checker — see README
    # limitations. This test documents the boundary, it is not a fix.
    issues, _ = check_section(
        "Một gói có giá một trăm tám mươi lăm nghìn đồng.", FACTS, BRAND)
    assert "wrong_price" not in _types(issues)
