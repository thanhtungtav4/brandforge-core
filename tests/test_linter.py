import pytest

from content_core.linter import lint_section, sentence_lengths, split_sentences
from content_core.loaders import load_brand
from content_core.writer import BAD_TEXT, POOL

BRAND = load_brand()
GOOD = POOL[0] + " " + POOL[2] + " " + POOL[4]


def test_pool_sentences_inside_brand_length_target():
    for s in POOL:
        assert 11 <= len(s.split()) <= 14, s


def test_good_text_passes_clean():
    violations, score = lint_section(GOOD, "blog", BRAND, "s1")
    assert violations == []
    assert score == 100


def test_banned_term_caught():
    violations, score = lint_section(GOOD + " This will elevate your morning.", "blog", BRAND, "s1")
    assert any(v["type"] == "banned_term" and v["detail"] == "elevate" for v in violations)
    # 1 banned term = -15 -> 85. The final gate still blocks it via the separate
    # "0 banned terms" rule; two banned terms also drop the score below 85.
    assert score == 85
    _, score2 = lint_section(GOOD + " Elevate and delve deeper.", "blog", BRAND, "s1")
    assert score2 < 85


def test_vietnamese_banned_phrase_caught():
    violations, _ = lint_section("Mộc Brew là cà phê tốt nhất Việt Nam hiện nay.", "blog", BRAND, "s1")
    assert any(v["detail"] == "tốt nhất việt nam" for v in violations)


def test_exclamation_flagged_in_blog_but_not_social():
    v_blog, _ = lint_section(GOOD + " Ngon quá!", "blog", BRAND, "s1")
    v_social, _ = lint_section(GOOD + " Ngon quá!", "social", BRAND, "s1")
    assert any(v["type"] == "exclamation_in_blog" for v in v_blog)
    assert not any(v["type"] == "exclamation_in_blog" for v in v_social)


def test_median_sentence_length_rule():
    long_text = " ".join(["một hai ba bốn năm sáu bảy tám chín mười"] * 3) + "."
    violations, _ = lint_section(long_text, "blog", BRAND, "s1")
    assert any(v["type"] == "median_sentence_length" for v in violations)


def test_splitter_does_not_tear_vietnamese_prices():
    # Regression (P1a): "185.000đ" was split into "…giá 185" + "000đ…" fragments,
    # corrupting the median-sentence-length metric on any text with a price.
    text = ("Một gói 250g có giá 185.000đ trước khi nói tới hương vị. "
            "Mình ghi rõ ngày rang trên từng gói gửi tới tay bạn.")
    sentences = split_sentences(text)
    assert len(sentences) == 2
    assert "185.000đ" in sentences[0]  # dots restored, price intact
    assert sentence_lengths(text) == [12, 12]


def test_splitter_handles_multi_group_numbers_and_decimals():
    text = "Doanh thu 1.234.567đ là con số lớn. Tỉ lệ 2.5 phần trăm thì nhỏ."
    sentences = split_sentences(text)
    assert len(sentences) == 2
    assert "1.234.567đ" in sentences[0] and "2.5" in sentences[1]


def test_median_with_price_sentence_is_the_true_median():
    # Four-sentence section whose true lengths are [11, 12, 12, 12] -> median 12.
    text = ("Mộc Brew rang cà phê vào thứ Ba hàng tuần cho bạn. "
            "Một gói 250g có giá 185.000đ trước khi nói tới hương vị. "
            "Hạt Arabica Cầu Đất cho vị chua nhẹ và hậu ngọt rõ. "
            "Mình ghi rõ ngày rang trên từng gói gửi tới tay bạn.")
    violations, score = lint_section(text, "blog", BRAND, "s1")
    assert violations == [] and score == 100


def test_bad_sample_is_caught_hard():
    violations, score = lint_section(BAD_TEXT, "blog", BRAND, "s1")
    types = {v["type"] for v in violations}
    assert "banned_term" in types and "exclamation_in_blog" in types
    assert score < 85
    assert sentence_lengths(BAD_TEXT)  # sanity
