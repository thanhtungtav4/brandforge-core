"""Audit round 2 (21 Nov 2026) — regression tests for the defects found in
the full codebase review. Every test here FAILED against the pre-fix code;
each names the hole it pins (H1..H15)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from content_core.fact_check import check_section, find_refused_claims  # noqa: E402
from content_core.gates import evaluate_gate  # noqa: E402
from content_core.linter import lint_section  # noqa: E402
from content_core.loaders import load_brand, load_facts  # noqa: E402
from content_core.seo import seo_pass  # noqa: E402
from content_core.style import build_profile  # noqa: E402

BRAND = load_brand()
FACTS = load_facts()


def _types(issues):
    return {i["type"] for i in issues}


# --- H1: the gate approved EMPTY drafts ------------------------------------

def test_gate_rejects_empty_section_list():
    verdict = evaluate_gate([], "blog", BRAND, FACTS)
    assert verdict["passed"] is False
    assert verdict["hard"]["empty_sections"] == ["<no sections>"]


def test_gate_rejects_whitespace_only_section():
    verdict = evaluate_gate([{"id": "s1", "text": "   \n  "}], "blog", BRAND, FACTS)
    assert verdict["passed"] is False
    assert verdict["hard"]["empty_sections"] == ["s1"]
    assert "s1" in verdict["failing_sections"]


def test_gate_still_passes_a_real_draft():
    text = ("Mộc Brew rang cà phê vào thứ Ba hàng tuần cho bạn. "
            "Một gói 250g có giá 185.000đ trước khi nói tới hương vị. "
            "Hạt Arabica Cầu Đất cho vị chua nhẹ và hậu ngọt rõ. "
            "Mình ghi rõ ngày rang trên từng gói gửi tới tay bạn.")
    verdict = evaluate_gate([{"id": "s1", "text": text}], "product", BRAND, FACTS)
    assert verdict["hard"]["empty_sections"] == []


# --- H2/H11: money formats the checker could not see ------------------------

def test_comma_grouped_wrong_price_is_flagged():
    issues, _ = check_section("Giá chỉ 200,000đ cho một gói hôm nay.", FACTS, BRAND)
    assert "wrong_price" in _types(issues)


def test_comma_grouped_correct_price_is_clean():
    issues, _ = check_section("Một gói có giá 185,000đ như đã niêm yết.", FACTS, BRAND)
    assert "wrong_price" not in _types(issues)


def test_dong_sign_wrong_price_is_flagged():
    issues, _ = check_section("Giá chỉ 200.000₫ cho một gói hôm nay.", FACTS, BRAND)
    assert "wrong_price" in _types(issues)


# --- H3: delivery ranges with typographic dashes / spaces -------------------

@pytest.mark.parametrize("text", [
    "Giao hàng trong 5–7 ngày tới nơi.",      # en dash
    "Giao hàng trong 5—7 ngày tới nơi.",      # em dash
    "Giao hàng trong 5 - 7 ngày tới nơi.",    # spaced hyphen
])
def test_delivery_range_variants_are_checked(text):
    issues, _ = check_section(text, FACTS, BRAND)
    assert "wrong_delivery" in _types(issues)


def test_delivery_fact_range_variants_are_clean():
    for text in ["Giao trong 2–3 ngày là tới nơi.", "Giao trong 2 - 3 ngày là tới nơi."]:
        issues, _ = check_section(text, FACTS, BRAND)
        assert "wrong_delivery" not in _types(issues)


# --- H12: weight written as 'gam' -------------------------------------------

def test_weight_in_gam_is_flagged():
    issues, _ = check_section("Một gói 300 gam cho bạn dùng thử cả tuần.", FACTS, BRAND)
    assert "unknown_weight" in _types(issues)


def test_weight_in_gam_correct_is_clean():
    issues, _ = check_section("Một gói 250 gam rang mẻ nhỏ mỗi tuần.", FACTS, BRAND)
    assert "unknown_weight" not in _types(issues)


# --- H4: 'số 1' false positive on ordinary numbers ---------------------------

def test_so_12_is_not_a_superlative_claim():
    issues, _ = check_section("Căn phòng số 12 rộng rãi và thoáng mát.", FACTS, BRAND)
    assert "unsupported_claim" not in _types(issues)
    assert "prohibited_claim" not in _types(issues)


def test_real_so_1_claim_still_flagged():
    issues, _ = check_section("Đây là cà phê số 1 cho dân văn phòng.", FACTS, BRAND)
    assert "unsupported_claim" in _types(issues)


# --- H5: negated claims are not assertions -----------------------------------

def test_negated_superlative_is_not_flagged():
    issues, flagged = check_section(
        "Mộc Brew không phải là tốt nhất, mình chỉ rang mẻ nhỏ thôi.", FACTS, BRAND)
    assert not issues and not flagged


def test_negated_prohibited_claim_is_not_flagged():
    issues, flagged = check_section(
        "Mình không nhận là chữa mất ngủ, cà phê chỉ giúp tỉnh táo.", FACTS, BRAND)
    assert not issues and not flagged


def test_affirming_khong_chi_still_flagged():
    issues, _ = check_section(
        "Cà phê này không chỉ tốt nhất mà còn rất đậm vị.", FACTS, BRAND)
    assert "unsupported_claim" in _types(issues)


# --- H13: negated roast day ---------------------------------------------------

def test_negated_roast_day_is_not_flagged():
    issues, _ = check_section(
        "Mộc Brew không rang vào thứ Tư, chỉ rang đúng lịch quen thuộc.", FACTS, BRAND)
    assert "wrong_roast_day" not in _types(issues)


def test_wrong_roast_day_still_flagged():
    issues, _ = check_section("Mộc Brew rang vào thứ Tư hàng tuần.", FACTS, BRAND)
    assert "wrong_roast_day" in _types(issues)


# --- H6: diacritics-free / punctuation-variant evasions -----------------------

def test_banned_terms_without_diacritics_are_caught():
    violations, _score = lint_section(
        "Ca phe dang cap the gioi cho ban, cam ket 100% chua mat ngu.", "blog", BRAND)
    assert any(v["type"] == "banned_term" for v in violations)


def test_banned_term_hyphen_space_variant_is_caught():
    violations, _ = lint_section(
        "This will game changing your morning routine at home.", "blog", BRAND)
    assert any(v["type"] == "banned_term" and v["detail"] == "game-changing"
               for v in violations)


def test_prohibited_claim_without_diacritics_is_caught():
    issues, _ = check_section("Hat nay chua mat ngu rat tot cho ban.", FACTS, BRAND)
    assert "prohibited_claim" in _types(issues)


def test_refused_claim_without_diacritics_is_caught():
    assert find_refused_claims("viết bài nhấn mạnh 100% chua mat ngu", BRAND)


def test_origin_pairing_without_diacritics_is_caught():
    issues, _ = check_section("Hat Arabica Dak Lak duoc nhieu nguoi thich.", FACTS, BRAND)
    assert "wrong_origin" in _types(issues)


def test_normal_diacritics_text_unchanged():
    # the shipped brand example must stay violation-free under the new matcher
    text = BRAND["examples"][0]["text"]
    violations, score = lint_section(text, "blog", BRAND)
    assert violations == [] and score == 100.0


# --- H7: fullwidth punctuation -------------------------------------------------

def test_fullwidth_exclamation_counts_in_blog():
    violations, _ = lint_section("Hạt mới rang sáng nay bạn nhé！", "blog", BRAND)
    assert any(v["type"] == "exclamation_in_blog" for v in violations)


# --- H9: seo_pass robustness ----------------------------------------------------

def test_seo_pass_tolerates_section_without_text():
    result = seo_pass({"topic": "cà phê"}, [{"id": "s1", "heading": "Mở đầu"}])
    assert result["meta_ok"] is False


# --- H10: zero-sample style profile ----------------------------------------------

def test_build_profile_rejects_zero_samples():
    with pytest.raises(ValueError):
        build_profile("Empty Voice", [], {})
    with pytest.raises(ValueError):
        build_profile("Blank Voice", ["   ", ""], {})


# --- H14: corrupt stored JSON raises a clear ValueError ---------------------------

def test_corrupt_style_profile_raises_value_error_with_context(tmp_path, monkeypatch):
    from content_core import loaders
    monkeypatch.setenv("CONTENT_CORE_STYLE_PROFILES_DIR", str(tmp_path))
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt"):
        loaders.load_style_profile("broken")


# --- H15: bool is not a valid judged grade / floor ---------------------------------

def test_quality_rejects_bool_judged_grade():
    from content_core.quality import evaluate_quality, preset_profile
    sections = [{"id": "s1", "text": "Mộc Brew rang thứ Ba. Giá 185.000đ cho gói 250g."}]
    with pytest.raises(ValueError):
        evaluate_quality(sections, "blog", preset_profile("blog"),
                         {"coherence": True}, BRAND, FACTS)


def test_quality_rejects_bool_floor():
    from content_core.quality import validate_profile, PRESETS
    with pytest.raises(ValueError):
        validate_profile({"format": "blog", "weights": dict(PRESETS["blog"]),
                          "floors": {"facts": True}})


# --- fetch helpers: bounded reads + CLI robustness ----------------------------------

def test_fetch_url_caps_bytes(tmp_path):
    import fetch_sample
    big = tmp_path / "big.html"
    big.write_text("<html><head><title>T</title></head><body><p>"
                   + ("cà phê ngon " * 5000) + "</p></body></html>", encoding="utf-8")
    result = fetch_sample.fetch_url(big.as_uri(), max_bytes=2000)
    assert result["truncated"] is True
    assert len(result["text"]) < 3000


def test_fetch_site_cli_bad_max_pages_exits_2():
    import fetch_site
    assert fetch_site.main(["--max-pages"]) == 2
    assert fetch_site.main(["--max-pages=abc", "file:///nonexistent"]) == 2
