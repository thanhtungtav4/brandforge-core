from content_core.fact_check import check_section, find_refused_claims
from content_core.loaders import load_brand, load_facts

BRAND = load_brand()
FACTS = load_facts()


def test_correct_price_passes():
    issues, _ = check_section("Một gói 250g có giá 185.000đ trước khi nói tới hương vị.", FACTS, BRAND, "s1")
    assert issues == []


def test_wrong_price_fails():
    issues, _ = check_section("Giá chỉ 200.000đ cho một gói nhỏ xinh hôm nay.", FACTS, BRAND, "s1")
    assert any(i["type"] == "wrong_price" for i in issues)


def test_correct_delivery_passes_and_wrong_fails():
    ok, _ = check_section("Đơn hàng được giao trong 2-3 ngày sau khi bạn xác nhận.", FACTS, BRAND, "s1")
    bad, _ = check_section("Đơn hàng được giao trong 5-7 ngày sau khi bạn xác nhận.", FACTS, BRAND, "s1")
    assert ok == []
    assert any(i["type"] == "wrong_delivery" for i in bad)


def test_health_claim_in_draft_is_an_issue():
    issues, _ = check_section("Cà phê này 100% chữa mất ngủ cho mọi người dùng.", FACTS, BRAND, "s1")
    assert any(i["type"] == "prohibited_claim" for i in issues)


def test_unsupported_claim_unmarked_blocks_but_marked_is_flagged():
    unmarked, _ = check_section("Nhiều người nói hạt nhập khẩu luôn thơm hơn hạt Việt.", FACTS, BRAND, "s1")
    _, flagged = check_section("Nhiều người nói hạt nhập khẩu luôn thơm hơn hạt Việt [NEEDS SOURCE].", FACTS, BRAND, "s1")
    assert any(i["type"] == "unsupported_claim" for i in unmarked)
    assert any(f["type"] == "flagged_claim" for f in flagged)


# --- P1b: money normalization ---------------------------------------------

import pytest

CORRECT_PRICE_FORMS = ["185.000đ", "185.000 đ", "185.000đ/gói", "185.000 VND",
                       "185.000 vnđ", "185000đ", "185k", "185 nghìn", "185 ngàn"]
WRONG_PRICE_FORMS = ["200.000đ", "200.000 VND", "190000đ", "190k", "200 nghìn", "200 ngàn"]


@pytest.mark.parametrize("form", CORRECT_PRICE_FORMS)
def test_correct_price_in_any_format_passes(form):
    issues, _ = check_section(f"Một gói 250g có giá {form} hôm nay bạn nhé.", FACTS, BRAND, "s1")
    assert not any(i["type"] == "wrong_price" for i in issues), (form, issues)


@pytest.mark.parametrize("form", WRONG_PRICE_FORMS)
def test_wrong_price_in_any_format_fails(form):
    issues, _ = check_section(f"Một gói 250g có giá {form} hôm nay bạn nhé.", FACTS, BRAND, "s1")
    wrong = [i for i in issues if i["type"] == "wrong_price"]
    assert len(wrong) == 1, (form, issues)  # exactly one: no double-reporting


def test_money_amount_reported_once_not_twice():
    issues, _ = check_section("Giá là 200.000đ cho một gói bạn nhé.", FACTS, BRAND, "s1")
    assert len([i for i in issues if i["type"] == "wrong_price"]) == 1


# --- P1b: roast weekday -----------------------------------------------------

def test_correct_roast_day_passes():
    issues, _ = check_section("Mộc Brew rang cà phê vào thứ Ba hàng tuần cho bạn.", FACTS, BRAND, "s1")
    assert issues == []


def test_wrong_roast_day_fails():
    issues, _ = check_section("Mộc Brew rang cà phê vào thứ Tư hàng tuần cho bạn.", FACTS, BRAND, "s1")
    assert any(i["type"] == "wrong_roast_day" for i in issues)


def test_weekday_in_non_roast_sentence_is_ignored():
    issues, _ = check_section("Bạn nhận hàng vào thứ Tư tuần này nhé bạn.", FACTS, BRAND, "s1")
    assert not any(i["type"] == "wrong_roast_day" for i in issues)


# --- P1b: origins -------------------------------------------------------------

def test_correct_origin_pairings_pass():
    ok1, _ = check_section("Hạt Arabica Cầu Đất cho vị chua nhẹ và hậu ngọt rõ.", FACTS, BRAND, "s1")
    ok2, _ = check_section("Hạt Robusta Đắk Lắk cho vị đậm và lớp crema dày hơn.", FACTS, BRAND, "s1")
    assert ok1 == [] and ok2 == []


def test_swapped_origin_pairing_fails():
    bad1, _ = check_section("Hạt Arabica Đắk Lắk cho vị chua nhẹ của vùng cao.", FACTS, BRAND, "s1")
    bad2, _ = check_section("Hạt Robusta Cầu Đất cho vị đậm và crema dày.", FACTS, BRAND, "s1")
    assert any(i["type"] == "wrong_origin" for i in bad1)
    assert any(i["type"] == "wrong_origin" for i in bad2)


def test_both_correct_pairings_in_one_sentence_pass():
    issues, _ = check_section(
        "Arabica Cầu Đất cho vị chua sáng, Robusta Đắk Lắk giữ hậu cacao đậm ở cuối lưỡi.",
        FACTS, BRAND, "s1")
    assert issues == []


def test_other_region_claimed_as_source_fails_but_plain_mention_passes():
    claimed, _ = check_section("Hạt của mình được trồng tại Gia Lai và thu hoạch tại đó.", FACTS, BRAND, "s1")
    assert any(i["type"] == "unknown_origin" for i in claimed)
    mention, _ = check_section("Nhiều quán ở Buôn Ma Thuột rang hạt Robusta bán tại chỗ mỗi sáng.", FACTS, BRAND, "s1")
    assert not any(i["type"] == "unknown_origin" for i in mention)


# --- P1b: unsupported statistic-shaped numbers --------------------------------

def test_invented_customer_count_fails_unless_marked():
    issues, _ = check_section("Đã có 500 khách hàng tin dùng gói cà phê này mỗi tháng.", FACTS, BRAND, "s1")
    assert any(i["type"] == "unsupported_number" for i in issues)
    _, flagged = check_section("Đã có 500 khách hàng tin dùng gói cà phê này [NEEDS SOURCE].", FACTS, BRAND, "s1")
    assert any(f["type"] == "flagged_claim" for f in flagged)


def test_fact_numbers_do_not_trigger_unsupported_number():
    issues, _ = check_section(
        "Một gói 250g có giá 185.000đ trước khi nói tới hương vị. "
        "Đơn hàng được giao trong 2-3 ngày sau khi bạn xác nhận.", FACTS, BRAND, "s1")
    assert issues == []


def test_brief_with_false_health_claim_is_refused():
    refused = find_refused_claims("{'topic': 'ngủ', 'requested_claims': ['100% chữa mất ngủ']}", BRAND)
    assert "100% chữa mất ngủ" in refused
