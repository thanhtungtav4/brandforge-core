"""Fail-first tests for the Tier 1 + Tier 2 evaluation backlog round.

Each test is built from a repro case in evaluation/round-1..8-*.md and
pins the behaviour the fix must produce. Design values are NOT changed:
craft bar 70, brand pass 85, quality presets/floors, exclamation ban.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import mcp_server as srv  # noqa: E402
from content_core import gates, quality  # noqa: E402
from content_core.craft import (craft_check, craft_check_sections,  # noqa: E402
                                retrieve_cards)
from content_core.fact_check import check_section, find_refused_claims  # noqa: E402
from content_core.graph import run_flow  # noqa: E402
from content_core.linter import lint_section  # noqa: E402
from content_core.loaders import load_brand, load_facts, load_style_profile  # noqa: E402
from content_core.style import analyze_samples, build_profile, style_match  # noqa: E402
from fetch_sample import strip_html  # noqa: E402
from fetch_site import fetch_site  # noqa: E402

FACTS = load_facts()
BRAND = load_brand()
CNM_BRAND = load_brand(PROJECT_DIR / "calibration" / "caonguyenmoc-brand.yaml")


@pytest.fixture(autouse=True)
def _isolated_drafts(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_CORE_DRAFTS_DIR", str(tmp_path / "drafts"))


def _types(text: str) -> set[str]:
    issues, _ = check_section(text, FACTS, BRAND, "s1")
    return {i["type"] for i in issues}


# ---------------------------------------------------------------------------
# Item 1a — money roles (R1 §1)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Phí ship cố định 30.000đ cho mọi đơn hàng trong tuần này.",
    "Phí vận chuyển là 30.000đ cho mỗi đơn hàng của bạn.",
    "Hai gói có tổng 370.000đ, ghi rõ trước khi bạn thanh toán.",
    "Đơn này giảm 20.000đ cho khách hàng đặt lần đầu tiên.",
    "Bạn tiết kiệm 20.000đ khi mua gói này trong tuần đầu tiên.",
    "Giá cũ 220.000đ đã được hạ xuống cho tuần lễ ra mắt.",
    "Một cân hạt có giá 740.000đ cho khách mua số lượng lớn.",
    "Gói lớn 1kg có giá 740.000đ cho quán nhỏ dùng thử.",
])
def test_money_roles_do_not_raise_wrong_price(text):
    assert "wrong_price" not in _types(text)


@pytest.mark.parametrize("text", [
    "Một gói 250g có giá 200.000đ cho bạn dùng thử.",
    "Giá một gói là 200.000đ, rang mới mỗi tuần cho bạn.",
])
def test_bare_mismatched_price_still_flags(text):
    assert "wrong_price" in _types(text)


def test_strikethrough_old_price_does_not_raise():
    assert "wrong_price" not in _types(
        "Giá niêm yết ~~220.000đ~~ nay chỉ còn 185.000đ cho gói 250g.")


# ---------------------------------------------------------------------------
# Item 1e — ₫-prefix amounts; marked percent claims land in flagged_claims
# ---------------------------------------------------------------------------

def test_dong_prefix_amount_checked():
    assert "wrong_price" in _types("Một gói 250g có giá ₫200.000 hôm nay cho bạn.")
    assert "wrong_price" not in _types("₫185.000 cho một gói 250g rang mới mỗi tuần.")


def test_marked_percent_claim_appears_in_flagged():
    _issues, flagged = check_section(
        "Tăng 20% doanh số trong quý này [NEEDS SOURCE].", FACTS, BRAND, "s1")
    assert any("20%" in f["detail"] for f in flagged)


# ---------------------------------------------------------------------------
# Item 1b — delivery phrasings (R1 §3)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Hàng tới nơi trong vòng 5 ngày.",
    "Hàng được giao trong vòng 5 ngày sau khi bạn đặt.",
    "Giao hàng sau 5 ngày làm việc kể từ ngày đặt.",
    "Thời gian giao là 5 đến 7 ngày cho mọi đơn hàng.",
    "Giao hỏa tốc trong 24 giờ cho đơn nội thành hôm nay.",
    "Ship trong ngày là tới cho đơn đặt buổi sáng.",
])
def test_delivery_phrasings_flag(text):
    assert "wrong_delivery" in _types(text)


@pytest.mark.parametrize("text", [
    "Hạt sau khi mở túi nên dùng trong 5-7 ngày đầu tiên.",
    "Mộc Brew không giao trong 5 ngày; thời gian giao luôn là 2-3 ngày.",
])
def test_delivery_non_delivery_ranges_and_negation_safe(text):
    assert "wrong_delivery" not in _types(text)


# ---------------------------------------------------------------------------
# Item 1c — statistics (R1 §2/§4)
# ---------------------------------------------------------------------------

def test_stat_fact_token_exemption_removed():
    assert "unsupported_number" in _types(
        "Đã có 250 khách hàng thân thiết quay lại mua mỗi tháng.")


def test_stat_hyphen_range_checked():
    assert "unsupported_number" in _types(
        "Có 5-7 khách hàng mới ghé quán mỗi buổi sáng sớm.")


def test_stat_ordinary_prose_safe():
    assert "unsupported_number" not in _types(
        "Pha cho 2 người uống là vừa một phin nhỏ này.")
    assert "unsupported_number" not in _types(
        "Một gói 1kg rang mới mỗi tuần cho quán nhỏ dùng thử.")


def test_composition_percent_not_flagged():
    types = _types("Gói này là 100% hạt Arabica nguyên chất từ Cầu Đất.")
    assert "unsupported_statistic" not in types
    assert "unsupported_number" not in types


def test_weight_decimal_and_gr_forms():
    assert "unknown_weight" in _types("Một gói 300gr để bạn dùng thử cả tuần.")
    issues, _ = check_section("Một gói 250,5g rang mới mỗi tuần.", FACTS, BRAND, "s1")
    detail = next(i["detail"] for i in issues if i["type"] == "unknown_weight")
    assert "250,5g" in detail


# ---------------------------------------------------------------------------
# Item 1d — origin swaps and region-before-bean (R1 §5)
# ---------------------------------------------------------------------------

def test_origin_full_swap_flagged():
    assert "wrong_origin" in _types(
        "Arabica Đắk Lắk, Robusta Cầu Đất là hai hạt chính của quán.")


@pytest.mark.parametrize("text", [
    "Đắk Lắk là quê hương của hạt Arabica trong vùng này.",
    "Cầu Đất là vùng trồng Robusta nổi tiếng của tỉnh.",
])
def test_origin_region_before_bean_flagged(text):
    assert "wrong_origin" in _types(text)


def test_origin_correct_and_negated_safe():
    assert "wrong_origin" not in _types(
        "Hạt Arabica Cầu Đất cho vị chua nhẹ và hậu ngọt rõ.")
    assert "wrong_origin" not in _types(
        "Robusta không lấy từ Cầu Đất mà lấy từ Đắk Lắk quê nhà.")


# ---------------------------------------------------------------------------
# Item 2 — brand_id wired into gates + quality (R3-S4)
# ---------------------------------------------------------------------------

CNM_TEXT = (
    "Cà phê rang mộc uống ngon nhất khi còn mới rang xong vài ngày đầu tiên. "
    "Mùi thơm của hạt vừa xay lan khắp căn bếp nhỏ vào buổi sáng sớm tinh mơ. "
    "Bạn cất hạt trong hũ kín, tránh nắng gắt và nơi ẩm thấp quanh khu vực bếp nấu. "
    "Vị đắng dịu ở đầu lưỡi rồi chuyển ngọt nhẹ nơi cuống họng khi nuốt xuống chầm chậm."
)


def test_gates_craft_uses_active_brand_glossary():
    sections = [{"id": "s1", "text": CNM_TEXT}]
    expected = craft_check(CNM_TEXT, "blog", brand_id="cao-nguyen-moc")["score"]
    verdict = gates.evaluate_gate(sections, "blog", CNM_BRAND, FACTS)
    assert verdict["craft"]["score"] == expected
    default = craft_check(CNM_TEXT, "blog")["score"]
    assert verdict["craft"]["score"] > default


def test_quality_clarity_uses_active_brand_glossary():
    sections = [{"id": "s1", "text": CNM_TEXT}]
    expected = craft_check(CNM_TEXT, "blog", brand_id="cao-nguyen-moc")["score"]
    report = quality.evaluate_quality(sections, "blog",
                                      quality.preset_profile("blog"), None,
                                      CNM_BRAND, FACTS)
    clarity = next(a for a in report["axes"] if a["axis"] == "clarity")
    assert clarity["score"] == expected


# ---------------------------------------------------------------------------
# Item 3 — craft failing sections by score (R8 F8-1)
# ---------------------------------------------------------------------------

def test_craft_failing_sections_by_score_not_violations():
    good_with_violations = (
        "Một gói Mộc Brew 250g có giá 185.000đ, ghi rõ trước mọi mô tả. "
        "Mình phối Arabica Cầu Đất cùng Robusta Đắk Lắk cho vị cân bằng. "
        "Arabica cho vị chua nhẹ, Robusta giữ hậu đậm và lớp crema dày. "
        "Mình ghi ngày rang lên bao bì để bạn kiểm tra độ mới. "
        "Mộc Brew chỉ rang vào thứ Ba hàng tuần, không rang tồn kho. "
        "Đơn xác nhận trước thứ Ba được rang và đóng gói ngay hôm đó. "
        "Hàng giao tới tay bạn trong 2-3 ngày theo lịch hẹn đã chốt. "
        "Bạn xay vừa đủ mỗi lần pha để giữ hương trong hai tuần đầu."
    )  # craft 85: monotone violation only — must NOT be "failing"
    bad = (
        "Sản phẩm ngon và tuyệt vời lắm. Mùi vị ngon, chất lượng ngon, "
        "ai uống cũng thấy ngon và rất ngon. Ngon thật sự luôn đó bạn. "
        "Nói chung là ngon, mua đi vì nó ngon hơn mọi thứ khác nhiều."
    )
    out = craft_check_sections(
        [{"id": "s1", "text": good_with_violations}, {"id": "s2", "text": bad}],
        "product")
    assert craft_check(bad, "product")["score"] < 70
    assert out["score"] < 70  # worst section wins
    assert out["failing_sections"] == ["s2"]


# ---------------------------------------------------------------------------
# Item 4 — linter normalisation hardening (R2)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("term", [
    "ele\u200bvate",        # zero-width space
    "ele\u00advate",        # soft hyphen
    "ele\ufeffvate",        # BOM / zero-width no-break space
    "ele\u200dvate",        # zero-width joiner
    "elev\u0430te",         # Cyrillic а
    "ｅｌｅｖａｔｅ",   # fullwidth
    "elevate7",             # digit-guard abuse (trailing)
    "7elevate",             # digit-guard abuse (leading)
])
def test_banned_term_evasions_caught(term):
    text = f"Bài viết này dùng từ {term} để nói về buổi sáng của bạn hôm nay."
    violations, _score = lint_section(text, "blog", BRAND)
    assert any(v["type"] == "banned_term" for v in violations), term


def test_pipe_sentence_is_measured_not_exempt():
    text = ("Hạt Robusta cho vị đậm và body dày cùng lớp crema vàng óng | "
            "hạt Arabica cho vị chua sáng và hương hoa nhẹ nhàng | "
            "cả hai loại đều được rang mẻ nhỏ mỗi tuần tại xưởng rang của mình cho bạn")
    violations, _score = lint_section(text, "blog", BRAND)
    assert any(v["type"] == "sentence_too_long" for v in violations)


def test_genuine_flattened_table_still_excluded():
    cells = " | ".join(["Arabica Cầu Đất", "185.000đ", "250g", "chua sáng",
                         "Robusta Đắk Lắk", "185.000đ", "250g", "đậm hậu",
                         "rang thứ Ba", "giao 2-3 ngày", "phin V60", "mẻ nhỏ",
                         "hậu cacao", "body dày", "ít đắng", "ngọt nhẹ",
                         "xay vừa", "nước 92 độ", "nghỉ 3 ngày", "túi zip"])
    violations, _score = lint_section(cells, "blog", BRAND)
    assert not any(v["type"] == "sentence_too_long" for v in violations)


# ---------------------------------------------------------------------------
# Item 6 — monotonicity: over-max sentences leave the median (R5)
# ---------------------------------------------------------------------------

_SENT_10 = "Mộc Brew rang cà phê vào thứ Ba cho bạn!"
_SENT_11 = "Mình ghi rõ ngày rang trên từng gói gửi tới bạn!"
_LONG = ("Bạn dùng nước vừa sôi và chờ một phút trước khi rót lên bề mặt "
         "bột cà phê trong phin để chiết xuất đều và giữ hương thơm lâu hơn.")


def test_appending_overlong_sentence_cannot_raise_brand_score():
    base = f"{_SENT_10} {_SENT_11}"
    _v1, score_a = lint_section(base, "blog", BRAND)
    _v2, score_b = lint_section(f"{base} {_LONG}", "blog", BRAND)
    assert score_b <= score_a


def test_gate_verdict_cannot_flip_fail_to_pass_by_appending():
    base = f"{_SENT_10} {_SENT_11}"
    v_a = gates.evaluate_gate([{"id": "s1", "text": base}], "blog", BRAND, FACTS)
    v_b = gates.evaluate_gate([{"id": "s1", "text": f"{base} {_LONG}"}],
                              "blog", BRAND, FACTS)
    assert v_a["passed"] is False
    assert v_b["passed"] is False


# ---------------------------------------------------------------------------
# Item 5 — style matcher completeness (R4)
# ---------------------------------------------------------------------------

def _dinhcao_text() -> str:
    pages = []
    for path in sorted((PROJECT_DIR / "tests" / "fixtures" / "dinhcao").glob("*.html")):
        pages.append(strip_html(path.read_text(encoding="utf-8"))[1])
    return "\n\n".join(pages)


def test_style_match_scores_exclamation_swap():
    profile = load_style_profile("mocbrew-blog-voice")
    base = ("Mình mở túi hạt mới rang ngay khi vừa tới văn phòng sáng nay. "
            "Mùi caramel ấm chạm vào mũi trước tiên, rồi mùi cacao theo sau. "
            "Bạn thử đoán xem mình làm gì tiếp theo? Đun nước. Rồi chờ. "
            "Mình rót nước thật chậm lên mặt bột, nghe tiếng bọt nở lách tách. "
            "Ngụm đầu tiên luôn là ngụm đáng chờ nhất trong buổi sáng. "
            "Một gói như vậy mình ghi giá ngay ở dòng đầu cho bạn dễ tính.")
    swapped = base.replace(".", "!")
    plain = style_match(base, profile)["score"]
    loud = style_match(swapped, profile)["score"]
    assert loud <= plain - 8, (plain, loud)


def test_style_match_hype_fixture_no_longer_saturates():
    profile = load_style_profile("mocbrew-blog-voice")
    out = style_match(_dinhcao_text(), profile)
    assert out["score"] < 100
    assert out["score"] <= 88


def test_style_match_short_trial_keeps_calibration_score():
    # Regression guard (found in this round's re-measurement): the CNM
    # writer-trial section (149 words, 8 sentences, 0 questions) scored
    # 85.0 at 8215b8c. The new question_rate metric (profile rate 22.92)
    # initially dropped it to 77 — a rate from 8 sentences moves in
    # steps of 12.5, so _within now includes a sampling-granularity
    # term (200 / sentence count). The calibration score must hold.
    profile = load_style_profile("cao-nguyen-moc-blog-voice-dd5678")
    trial = (PROJECT_DIR / "calibration" / "work" / "writer_trial.txt"
             ).read_text(encoding="utf-8")
    assert style_match(trial, profile)["score"] == 85.0


def test_analyze_samples_dedupes_and_warns():
    t1 = "Mình pha phin mỗi sáng trước giờ làm cho cả văn phòng cùng uống."
    t2 = "Bạn ghi ngày rang lên túi để nhớ mẻ nào mới nhất trong tuần này."
    out = analyze_samples([t1, t1, "  " + t1.replace(" ", "  ") + " ", t2])
    assert out["sample_count"] == 2
    assert any("duplicate" in w for w in out["consistency"]["warnings"])


def test_cnm_posts_self_match_stays_high():
    import json as _json
    profile = load_style_profile("cao-nguyen-moc-blog-voice-dd5678")
    samples = _json.loads((PROJECT_DIR / "calibration" / "work"
                           / "samples.json").read_text(encoding="utf-8"))
    for sample in samples:
        out = style_match(sample["text"], profile)
        assert out["score"] >= 90, out["score"]


# ---------------------------------------------------------------------------
# Item 7 — refusal routing (R8 F8-7, R7 F7)
# ---------------------------------------------------------------------------

REFUSED_BRIEF = {"id": "t-refused", "format": "blog", "topic": "cà phê và giấc ngủ",
                 "keyword": "cà phê", "title": "Cà phê và giấc ngủ",
                 "requested_claims": ["100% chữa mất ngủ"]}

DISCLAIMER_BRIEF = {"id": "t-disclaimer", "format": "blog",
                    "topic": "cà phê và giấc ngủ", "keyword": "cà phê",
                    "title": "Cà phê và giấc ngủ của dân văn phòng",
                    "notes": "Bài viết cam kết không chữa mất ngủ, chỉ nói về "
                             "thói quen uống cà phê buổi sáng."}


def test_flow_refused_brief_ends_with_refused_status():
    state = run_flow(dict(REFUSED_BRIEF), outline_approved=True)
    assert state["status"] == "refused"
    assert state["refused_claims"]
    assert not state.get("sections")


def test_flow_disclaimer_brief_is_not_refused():
    state = run_flow(dict(DISCLAIMER_BRIEF), outline_approved=True)
    assert state["status"] != "refused"
    assert state["refused_claims"] == []


def test_find_refused_claims_honours_negation():
    assert find_refused_claims("cam kết không chữa mất ngủ", BRAND) == []
    assert find_refused_claims("nhấn mạnh 100% chữa mất ngủ", BRAND)


def test_mcp_validate_brief_refused_is_invalid():
    out = srv.validate_brief({"format": "blog", "topic": "cà phê và giấc ngủ",
                              "requested_claims": ["100% chữa mất ngủ"]})
    assert out["valid"] is False
    assert out["refused_claims"]
    ok = srv.validate_brief({"format": "blog", "topic": "cà phê văn phòng",
                             "notes": "cam kết không chữa mất ngủ"})
    assert ok["valid"] is True
    assert ok["refused_claims"] == []


# ---------------------------------------------------------------------------
# Item 8 — assemble_context: single representation + budget (R8)
# ---------------------------------------------------------------------------

def test_assemble_context_no_duplicated_payloads():
    out = srv.assemble_context({"format": "blog", "topic": "cà phê văn phòng giá",
                                "keyword": "cà phê"})
    parts = [o["part"] for o in out["ordered_context"]]
    assert parts == ["persona", "brand_rules", "craft_cards", "facts_triggered",
                     "exemplar", "task", "post_history_reminder"]
    assert "185.000đ" in out["context_text"]
    for entry in out["ordered_context"]:
        assert "content" not in entry  # metadata only, no second copy
    assert out["counts"]["sources"] >= 1
    assert out["counts"]["craft_cards"] >= 1


def test_assemble_context_part_cap_truncates_with_marker():
    from mcp_server import _cap_part
    truncated: list[str] = []
    text = "x" * 5000
    capped = _cap_part(text, 1000, "facts_triggered", truncated)
    assert len(capped) <= 1000 + 80
    assert "truncated" in capped
    assert truncated == ["facts_triggered"]
    short = _cap_part("ngắn", 1000, "task", truncated)
    assert short == "ngắn" and truncated == ["facts_triggered"]


# ---------------------------------------------------------------------------
# Item 9 — fetch completeness (R6)
# ---------------------------------------------------------------------------

def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _page(body: str, links: str = "") -> str:
    return (f"<html><head><title>T</title></head><body>{links}"
            f"<main><p>{body}</p></main></body></html>")


_LONG = ("Cà phê rang tại xưởng nhỏ của gia đình, hạt chọn tay từng mẻ, "
         "rang vừa tới để giữ mùi thơm tự nhiên cho người uống mỗi sáng. " * 3)


def test_fetch_merges_sitemap_with_link_discovery(tmp_path):
    site = tmp_path / "site"
    links = "".join(f'<a href="p{i}.html">p{i}</a>' for i in (1, 2, 3))
    _write(site / "index.html", _page(_LONG, links))
    for i in (1, 2, 3):
        _write(site / f"p{i}.html", _page(_LONG))
    _write(site / "orphan.html", _page(_LONG))
    _write(site / "sitemap.xml",
           '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           "<url><loc>orphan.html</loc></url></urlset>")
    out = fetch_site(f"file://{site}/index.html")
    urls = [p["url"] for p in out["pages"]]
    assert any(u.endswith("orphan.html") for u in urls), urls
    assert out["sitemap_fallback_used"] is True


def test_fetch_sitemap_index_cap_raised_and_truncation_warned(tmp_path):
    site = tmp_path / "site"
    _write(site / "index.html", _page(_LONG))
    children = []
    for i in range(12):
        _write(site / f"p{i}.html", _page(_LONG))
        _write(site / f"sm{i}.xml",
               '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
               f"<url><loc>p{i}.html</loc></url></urlset>")
        children.append(f"<sitemap><loc>sm{i}.xml</loc></sitemap>")
    _write(site / "sitemap.xml",
           '<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           + "".join(children) + "</sitemapindex>")
    out = fetch_site(f"file://{site}/index.html", max_pages=20)
    assert out["sitemap_truncated"] is True
    urls = [p["url"] for p in out["pages"]]
    # cap is 10 sitemap docs (index + 9 children), not 3 (index + 2)
    assert sum(1 for u in urls if "/p" in u) >= 9


def test_fetch_page_truncated_flag(tmp_path):
    site = tmp_path / "site"
    _write(site / "index.html", _page(_LONG * 30))
    out = fetch_site(f"file://{site}/index.html", max_bytes=1500)
    assert out["pages"][0]["truncated"] is True


def test_canonical_url_dedupes_variants():
    from fetch_site import _canonical_url
    a = _canonical_url("http://Example.com:80/a/index.html")
    b = _canonical_url("http://example.com/a/")
    c = _canonical_url("http://example.com/a")
    assert a == b == c


def test_fetch_records_final_url_after_redirect():
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence
            pass

        def do_GET(self):
            if self.path == "/old":
                self.send_response(301)
                self.send_header("Location", "/new")
                self.end_headers()
            elif self.path == "/new":
                body = _page(_LONG).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        out = fetch_site(f"http://127.0.0.1:{port}/old")
    finally:
        server.shutdown()
    assert out["pages"], out
    assert out["pages"][0]["url"].endswith("/new")


# ---------------------------------------------------------------------------
# Item 10 — POV prefix brands (R6-S1)
# ---------------------------------------------------------------------------

def test_pov_prefix_brand_name_counts_as_speaker():
    from content_core.brand_extract import suggest_voice_rules
    corpus = (
        "Ong Rừng thu hoạch mật ong rừng vào mùa khô cho bạn. "
        "Ong Rừng đóng hũ thủ công và gửi tới tay bạn trong tuần. "
        "Ong Rừng chỉ lấy mật ở bìa rừng, không phá tổ của ong. "
        "Ong Rừng ghi ngày thu hoạch lên nắp hũ cho bạn kiểm tra. "
        "Bạn cất hũ mật ở nơi khô ráo, tránh nắng gắt và nơi ẩm thấp. "
        "Ong Rừng trả lời tin nhắn của bạn trong ngày làm việc."
    )
    rules = suggest_voice_rules(corpus, "Ong Rừng U Minh")
    assert rules["pov"].startswith("first-person")


# ---------------------------------------------------------------------------
# Item 11 — MCP hardening (R7)
# ---------------------------------------------------------------------------

def test_store_excerpts_string_is_structured_error(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_CORE_STYLE_PROFILES_DIR",
                       str(tmp_path / "profiles"))
    out = srv.save_style_profile(
        samples=["Mình pha phin mỗi sáng cho bạn trước giờ làm việc."],
        name="Giọng Lỗi", store_excerpts="yes")
    assert "error" in out
    assert not list((tmp_path / "profiles").glob("*.json"))


def test_unknown_draft_returns_structured_error():
    assert "error" in srv.get_draft("khong-ton-tai")
    assert "error" in srv.brand_check(draft_id="khong-ton-tai")
    assert "error" in srv.final_gate("khong-ton-tai")


def test_overlong_id_structured_error_without_path():
    out = srv.get_draft("x" * 500)
    assert "error" in out
    assert "Errno" not in out["error"] and "/" not in out["error"]
    saved = srv.save_section("y" * 500, "s1", "H", "nội dung")
    assert "error" in saved
    assert "Errno" not in saved["error"] and "/" not in saved["error"]


def test_corrupt_draft_shape_returns_named_error(tmp_path):
    import json as _json
    drafts = tmp_path / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    (drafts / "bad-list.json").write_text('["not", "a", "dict"]', encoding="utf-8")
    (drafts / "bad-keys.json").write_text(_json.dumps({"foo": 1}), encoding="utf-8")
    assert "error" in srv.get_draft("bad-list")
    assert "corrupt" in srv.get_draft("bad-list")["error"]
    assert "error" in srv.get_draft("bad-keys")


# ---------------------------------------------------------------------------
# Item 12 — draft lifecycle (R8)
# ---------------------------------------------------------------------------

GOOD_SECTION = (
    "Mộc Brew rang cà phê vào thứ Ba hàng tuần cho bạn. "
    "Một gói 250g có giá 185.000đ trước khi nói tới hương vị. "
    "Hạt Arabica Cầu Đất cho vị chua nhẹ và hậu ngọt rõ. "
    "Mình ghi rõ ngày rang trên từng gói gửi tới tay bạn."
)


def _started_draft() -> str:
    out = srv.start_draft({"id": "life", "format": "blog", "topic": "cà phê văn phòng",
                           "keyword": "cà phê", "title": "Cà phê văn phòng"})
    return out["draft_id"]


def test_save_section_preserves_intent_and_target_when_omitted():
    draft_id = _started_draft()
    srv.save_section(draft_id, "s1", "Mở đầu", GOOD_SECTION,
                     intent="intro", target_words=200)
    srv.save_section(draft_id, "s1", "Mở đầu", GOOD_SECTION + " Thêm một câu nữa cho đủ ý.")
    draft = srv.get_draft(draft_id)
    section = next(s for s in draft["sections"] if s["id"] == "s1")
    assert section["intent"] == "intro"
    assert section["target_words"] == 200


def test_final_gate_empty_draft_asks_for_sections():
    draft_id = _started_draft()
    out = srv.final_gate(draft_id)
    assert out["passed"] is False
    assert "section" in out["instruction"].lower()
    assert "[]" not in out["instruction"]


def test_saving_to_approved_draft_resets_status():
    draft_id = _started_draft()
    srv.save_section(draft_id, "s1", "Mở đầu", GOOD_SECTION)
    gate = srv.final_gate(draft_id)
    assert gate["status"] == "approved", gate
    srv.save_section(draft_id, "s1", "Mở đầu", GOOD_SECTION)
    assert srv.get_draft(draft_id)["status"] == "drafting"


# ---------------------------------------------------------------------------
# Item 13 — linter fairness (R2)
# ---------------------------------------------------------------------------

def test_negated_banned_term_is_warning_not_hard_violation():
    text = "Mộc Brew không nhận là tốt nhất Việt Nam trong mọi bài viết của mình."
    violations, _score = lint_section(text, "blog", BRAND)
    assert not any(v["type"] == "banned_term" for v in violations)
    assert any(v["type"] == "banned_term_context" for v in violations)
    verdict = gates.evaluate_gate([{"id": "s1", "text": text}], "blog", BRAND, FACTS)
    assert verdict["hard"]["banned_terms_found"] == []


def test_quoted_banned_term_is_warning():
    text = "Khách nhắn lại rằng “tốt nhất Việt Nam” khi khen gói hạt mới rang tuần này."
    violations, _score = lint_section(text, "blog", BRAND)
    assert not any(v["type"] == "banned_term" for v in violations)
    assert any(v["type"] == "banned_term_context" for v in violations)


def test_asserted_banned_term_stays_hard():
    text = "Mộc Brew là tốt nhất Việt Nam cho mọi gia đình yêu cà phê thật sự."
    violations, _score = lint_section(text, "blog", BRAND)
    assert any(v["type"] == "banned_term" for v in violations)


def test_exclamation_penalty_capped_per_section():
    sentence = "Mộc Brew rang cà phê vào thứ Ba hàng tuần cho bạn!"
    violations, score = lint_section(" ".join([sentence] * 20), "blog", BRAND)
    assert any(v["type"] == "exclamation_in_blog" for v in violations)
    assert score == 85.0  # 20 marks, penalty capped at -15


def test_sentence_too_long_penalty_scales_with_overage():
    words45 = " ".join(["cà"] * 44) + " phê."
    violations, score = lint_section(words45, "blog", BRAND)
    # 45 words vs cap 25: overage 20 -> too_long penalty 2 + 2 = 4;
    # median (fallback: every sentence is over the cap) out of band -> -10.
    assert any(v["type"] == "sentence_too_long" for v in violations)
    assert score == 86.0


# ---------------------------------------------------------------------------
# Item 14 — craft retrieval quality (R3)
# ---------------------------------------------------------------------------

def test_retrieval_no_substring_misfires():
    cards = retrieve_cards("blog", "drafting",
                           "đánh giá vị trí quán và giá thuê mặt bằng")
    ids = [c["id"] for c in cards]
    assert "strong-verbs" not in ids
    assert "fact-before-claim" not in ids


def test_retrieval_ranking_prefers_most_relevant():
    cards = retrieve_cards("product", "product", "giá vị hương mô tả sản phẩm")
    ids = [c["id"] for c in cards]
    assert "domain-vocabulary" in ids


def test_retrieval_new_intents_and_aliases():
    assert "concrete-opening" in [c["id"] for c in
                                  retrieve_cards("blog", "mở bài", "viết mở bài cho bài blog")]
    assert "concrete-ending" in [c["id"] for c in
                                 retrieve_cards("blog", "kết bài", "phần kết bài cần chốt lại")]
    sales = retrieve_cards("product", "sales", "chốt sale cho gói cà phê này")
    assert "concrete-ending" in [c["id"] for c in sales] or \
           "fact-before-claim" in [c["id"] for c in sales]


# ---------------------------------------------------------------------------
# Item 15 — sensory check honesty (R3-S3)
# ---------------------------------------------------------------------------

def test_negated_sensory_mention_does_not_count():
    text = ("Mở túi ra, hạt rang còn ấm và thơm nức khắp phòng. "
            "Không có mùi cacao nào trong mẻ rang này cả bạn nhé. "
            "Vị chua sáng rõ ở ngụm đầu tiên khi uống thử. "
            "Hậu vị sạch và kéo dài vừa phải sau ngụm cuối cùng.")
    out = craft_check(text, "blog")
    assert "cacao" not in out["metrics"]["sensory_terms_used"]


def test_multiword_sensory_term_counts_once():
    text = ("Mở túi ra, mùi caramel và hạt rang còn ấm chạm vào mũi. "
            "Vị chua sáng rõ, hậu cacao đậm ở cuối lưỡi khi nuốt. "
            "Body dày vừa, vị cân bằng và sạch sau ngụm cuối cùng.")
    out = craft_check(text, "blog")
    used = out["metrics"]["sensory_terms_used"]
    assert "hậu cacao" in used
    assert "cacao" not in used
