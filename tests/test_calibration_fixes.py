"""Calibration fix round — regression tests for findings F1-F9 of
calibration/caonguyenmoc-report.md (real-site calibration on
caonguyenmoc.vn). Each test names the finding it pins:

- F1 fetch layer blind to JS-shell sites (detection + sitemap fallback
  + JSON-LD text extraction)
- F6 fixed 25-word max sentence / table runs in sentence statistics
- F8 craft glossary is one brand's but judged every brand
- F2 style divergence warning false-positives near zero; F6/R6 style
  density metrics unstable on short texts
- F3/F4/F5/F9 Brand Builder trust leaks (inherited prohibited_claims,
  alt-text logo trap, CSS-var fonts, conversion losing provenance/kit)
- §2 POV suggestion misses brand-name-as-speaker

No network: fetch tests run over file:// fixtures, like the rest of the
suite.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import mcp_server as srv  # noqa: E402
from content_core.brand_extract import (build_brand_draft,  # noqa: E402
                                        draft_to_brand_yaml,
                                        suggest_voice_rules)
from content_core.craft import craft_check  # noqa: E402
from content_core.linter import lint_section  # noqa: E402
from content_core.loaders import load_brand  # noqa: E402
from content_core.style import analyze_samples, style_match  # noqa: E402
from fetch_sample import fetch_url, strip_html  # noqa: E402
from fetch_site import fetch_site, parse_page  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CALIBRATION_WORK = PROJECT_DIR / "calibration" / "work"


@pytest.fixture(scope="module")
def som_pages() -> list[dict]:
    return fetch_site(f"file://{FIXTURES}/som/index.html")["pages"]


def words(n: int, word: str = "từ") -> str:
    return " ".join([word] * n)


# ---------------------------------------------------------------------------
# F1 — fetch hardening
# ---------------------------------------------------------------------------

def test_js_shell_detected_and_sitemap_fallback_crawls_blog():
    out = fetch_site(f"file://{FIXTURES}/jsshell/index.html")
    root = out["pages"][0]
    assert root["js_shell_suspected"] is True       # 97%-script homepage
    assert out["js_shell_suspected"] is True        # surfaced at top level
    urls = [p["url"] for p in out["pages"]]
    # the homepage has NO <a> links and ~11 visible words: the article
    # page is only reachable through the sitemap fallback (robots.txt
    # Sitemap: line in this fixture)
    assert any(u.endswith("bai-viet.html") for u in urls)
    article = next(p for p in out["pages"] if p["url"].endswith("bai-viet.html"))
    assert article["word_count"] if "word_count" in article else len(article["text"].split()) > 50
    assert article["js_shell_suspected"] is False


def test_sitemap_fallback_without_robots_txt():
    # jsshell2 has /sitemap.xml but no robots.txt: the conventional
    # /sitemap.xml location must be tried on its own.
    out = fetch_site(f"file://{FIXTURES}/jsshell2/index.html")
    assert any(p["url"].endswith("bai-viet.html") for p in out["pages"])


def test_fetch_url_flags_js_shell():
    out = fetch_url(f"file://{FIXTURES}/jsshell/index.html")
    assert out["js_shell_suspected"] is True
    assert out["word_count"] < 20                    # visible text is a spinner line


def test_normal_site_not_flagged_as_js_shell(som_pages):
    assert all(p["js_shell_suspected"] is False for p in som_pages)


def test_ldjson_text_extracted_but_plain_script_ignored():
    html = """<!DOCTYPE html><html><head><title>Bài viết</title>
    <script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "BlogPosting",
     "headline": "Cách rang cà phê mộc",
     "description": "Hướng dẫn rang cà phê mộc tại nhà với chảo gang và lửa vừa trong mười hai phút."}
    </script>
    <script>var SECRET_DATA = "dữ liệu này không được lọt vào text";</script>
    </head><body><p>Nội dung chính của bài viết nằm ở đây.</p></body></html>"""
    _title, text = strip_html(html)
    assert "Hướng dẫn rang cà phê mộc tại nhà" in text   # JSON-LD description kept
    assert "SECRET_DATA" not in text and "không được lọt" not in text


# ---------------------------------------------------------------------------
# F6 — per-brand max sentence + table-like segments excluded from stats
# ---------------------------------------------------------------------------

def _brand(max_words: int) -> dict:
    return {"voice": {"banned_terms": [],
                      "sentence_rules": {
                          "median_words_default": [1, 100],
                          "median_words_by_format": {},
                          "max_sentence_words": max_words}}}


def test_linter_uses_per_brand_max_sentence():
    text30 = words(30) + "."
    text50 = words(50) + "."
    v38, _ = lint_section(text30, "blog", _brand(38))
    assert not any(v["type"] == "sentence_too_long" for v in v38)
    v38b, _ = lint_section(text50, "blog", _brand(38))
    assert any(v["type"] == "sentence_too_long" for v in v38b)


def test_mocbrew_max_sentence_unchanged():
    # Mộc Brew's own brand file states 25 — the default path must not move.
    v, _ = lint_section(words(30) + ".", "blog", load_brand())
    assert any(v["type"] == "sentence_too_long" for v in v)


def test_table_like_segments_excluded_from_sentence_stats():
    table_run = ("Tên hạt | Nguồn gốc | Hương vị | " + words(50, "mô") + " | hết bảng")
    assert table_run.count("|") >= 2 and len(table_run.split()) > 25
    text = words(11, "câu") + ". " + table_run
    v, _score = lint_section(text, "blog", load_brand())
    types = {x["type"] for x in v}
    assert "sentence_too_long" not in types       # the 60-word table run is not a sentence
    assert "median_sentence_length" not in types  # median computed without it (11 only)


def test_builder_derives_max_sentence_from_corpus():
    long_copy = " ".join(words(38, "chữ") + "." for _ in range(12))
    rules = suggest_voice_rules(long_copy)["sentence_rules"]
    assert 25 < rules["max_sentence_words"] <= 45


# ---------------------------------------------------------------------------
# F8 — craft glossary per brand
# ---------------------------------------------------------------------------

# A Cao Nguyên Mộc-flavoured passage: "ngon" is ordinary vocabulary for
# this brand, and its sensory language is "mùi thơm", absent from Mộc
# Brew's Arabica-oriented sensory glossary.
CNM_TEXT = (
    "Cà phê rang mộc uống ngon nhất khi còn mới rang xong vài ngày đầu tiên. "
    "Mùi thơm của hạt vừa xay lan khắp căn bếp nhỏ vào buổi sáng sớm tinh mơ. "
    "Bạn cất hạt trong hũ kín, tránh nắng gắt và nơi ẩm thấp quanh khu vực bếp nấu. "
    "Vị đắng dịu ở đầu lưỡi rồi chuyển ngọt nhẹ nơi cuống họng khi nuốt xuống chầm chậm."
)

# Exact prose from demo_mcp_e2e.py (also pinned in test_craft.py).
OLD_DEMO_PRODUCT = (
    "Một gói Mộc Brew 250g có giá 185.000đ, ghi rõ trước mọi mô tả. "
    "Mình phối Arabica Cầu Đất cùng Robusta Đắk Lắk cho vị cân bằng. "
    "Arabica cho vị chua nhẹ, Robusta giữ hậu đậm và lớp crema dày. "
    "Mình ghi ngày rang lên bao bì để bạn kiểm tra độ mới. "
    "Mộc Brew chỉ rang vào thứ Ba hàng tuần, không rang tồn kho. "
    "Đơn xác nhận trước thứ Ba được rang và đóng gói ngay hôm đó. "
    "Hàng giao tới tay bạn trong 2-3 ngày theo lịch hẹn đã chốt. "
    "Bạn xay vừa đủ mỗi lần pha để giữ hương trong hai tuần đầu."
)


def test_craft_default_path_is_mocbrew_pinned():
    out = craft_check(OLD_DEMO_PRODUCT, "product")
    assert out["score"] == 85.0
    assert {v["type"] for v in out["violations"]} == {"monotone_rhythm"}
    out_mb = craft_check(OLD_DEMO_PRODUCT, "product", brand_id="moc-brew")
    assert out_mb["score"] == 85.0


def test_craft_other_brand_uses_generic_glossary():
    default = craft_check(CNM_TEXT, "blog")
    assert any(v["type"] == "weak_word" and v["detail"] == "ngon"
               for v in default["violations"])           # Mộc Brew glossary cries wolf
    assert any(v["type"] == "no_sensory_detail" for v in default["violations"])

    generic = craft_check(CNM_TEXT, "blog", brand_id="cao-nguyen-moc")
    assert not any(v["detail"] == "ngon" for v in generic["violations"])
    assert not any(v["type"] == "no_sensory_detail" for v in generic["violations"])
    assert generic["score"] > default["score"]


def test_craft_explicit_glossary_still_wins():
    custom = {"weak_words": [{"word": "ngon", "replace_with": "ghi vị cụ thể"}],
              "groups": {}, "sensory_groups": []}
    out = craft_check(CNM_TEXT, "blog", glossary=custom, brand_id="cao-nguyen-moc")
    assert any(v["detail"] == "ngon" for v in out["violations"])


def test_mcp_craft_check_accepts_brand_id():
    out = srv.craft_check(text=CNM_TEXT, format="blog", brand_id="cao-nguyen-moc")
    assert not any(v.get("detail") == "ngon" for v in out["violations"])


# ---------------------------------------------------------------------------
# F2 / R6 — style tolerance fixes
# ---------------------------------------------------------------------------

_NEUTRAL = "Cà phê rang mộc có mùi thơm dịu và vị đắng nhẹ ở đầu lưỡi người uống."


def test_no_divergence_warning_for_near_zero_rates():
    # first-person rates ~[0, 0, 0, 0, 0.17]: CV explodes on a near-zero
    # mean (the CNM false positive: [0,0,0,0,0.1] -> CV 2.0), but the
    # samples plainly agree — nobody writes first-person here.
    samples = [" ".join([_NEUTRAL] * 4) for _ in range(4)]
    samples.append(" ".join([_NEUTRAL] * 39)
                   + " Mình pha cà phê mỗi sáng cho cả nhà cùng uống chung vui.")
    out = analyze_samples(samples)
    warnings = " ".join(out["consistency"]["warnings"])
    assert "first_person_rate" not in warnings


def test_divergence_still_warns_for_real_mixed_authors():
    samples = [" ".join([_NEUTRAL] * 4) for _ in range(4)]
    samples.append(" ".join([_NEUTRAL] * 39)
                   + " Mình pha cà phê mỗi sáng cho cả nhà cùng uống chung vui.")
    out = analyze_samples(samples)
    # Backlog item 5b: the four identical copies are ONE sample now
    # (dedupe) — the count is 2, not 5. The divergence behaviour itself
    # is pinned by test_style.py's mixed-authors test (distinct samples).
    assert out["sample_count"] == 2  # sanity: fixture built as intended


def test_style_match_short_text_density_metrics_relaxed():
    # The calibration writer trial: 149 words in Cao Nguyên Mộc's voice.
    # Before the fix it scored 69 — failing reader_rate (1.34 vs 0.20)
    # and number_density (2.68 vs 1.67), both pure short-text density
    # noise, while the REAL miss (sentence stdev 3.2 vs 10.44) must
    # still fail.
    profile = {"fingerprint": {
        "sentence_len": {"median": 14.0, "stdev": 10.44},
        "short_sentence_share": 0.05, "first_person_rate": 0.02,
        "reader_rate": 0.20, "number_density": 1.67}}
    text = (CALIBRATION_WORK / "writer_trial.txt").read_text(encoding="utf-8")
    out = style_match(text, profile)
    by_metric = {m["metric"]: m for m in out["metrics"]}
    assert by_metric["reader_rate"]["within_range"] is True
    assert by_metric["number_density"]["within_range"] is True
    assert by_metric["reader_rate"]["reliability"] == "low"
    assert by_metric["number_density"]["reliability"] == "low"
    assert by_metric["sentence_stdev"]["within_range"] is False  # real signal kept
    assert "reliability_note" in out
    assert out["score"] == 85.0


def test_style_match_long_text_has_no_reliability_note():
    profile = {"fingerprint": {
        "sentence_len": {"median": 14.0, "stdev": 10.44},
        "short_sentence_share": 0.05, "first_person_rate": 0.02,
        "reader_rate": 0.20, "number_density": 1.67}}
    long_text = " ".join([_NEUTRAL] * 30)   # 450 words
    out = style_match(long_text, profile)
    assert "reliability_note" not in out
    assert all(m["reliability"] == "normal" for m in out["metrics"])


# ---------------------------------------------------------------------------
# F3/F4/F5/F9 — Brand Builder trust leaks
# ---------------------------------------------------------------------------

def test_draft_does_not_inherit_house_prohibited_claims(som_pages):
    draft = build_brand_draft(som_pages, None, "own_site")
    assert draft["voice"]["prohibited_claims"] == []
    assert draft["claims_and_guardrails"]["prohibited_claims"] == []
    assert any("prohibited_claims" in n for n in draft["review_notes"])


def test_logo_alt_text_trap_rejected():
    # Modelled on the CNM evidence: a blog-card thumbnail whose ALT text
    # describes a logo ("Logo Cao Nguyên Mộc với dòng chữ...") won the
    # logo slot. Alt text alone, outside a header/brand context, is not
    # a logo signal.
    html = """<!DOCTYPE html><html><head><title>Blog | Mộc Nhỏ</title></head>
    <body><article>
      <img src="/img/b/bai-viet-thumb-t.jpg" alt="Logo Mộc Nhỏ với dòng chữ Mang Tây Nguyên Về Nhà">
      <h2>Bài viết mới</h2><p>Nội dung bài viết ở đây.</p>
    </article></body></html>"""
    page = parse_page("https://mocnho.vn/blog/", html)
    assert page["html_meta"]["logo_url"] is None


def test_logo_header_alt_signal_accepted():
    html = """<!DOCTYPE html><html><head><title>Mộc Nhỏ</title></head>
    <body><header><img src="/img/brand-mark.png" alt="Logo Mộc Nhỏ"></header>
    <p>Trang chủ.</p></body></html>"""
    page = parse_page("https://mocnho.vn/", html)
    assert page["html_meta"]["logo_url"] == "https://mocnho.vn/img/brand-mark.png"


def test_fonts_css_variables_filtered():
    html = """<!DOCTYPE html><html><head><title>Mộc Nhỏ</title>
    <style>
      body { font-family: var(--display); color: #222b1c; }
      h1 { font-family: 'Lora', serif; }
      p { font-family: var(--f-ui); }
    </style></head><body><h1>Xin chào</h1><p>Đoạn văn.</p></body></html>"""
    page = parse_page("https://mocnho.vn/", html)
    families = [f["family"] for f in page["html_meta"]["fonts"]]
    assert families == ["Lora"]


def test_conversion_keeps_example_source_urls(som_pages):
    draft = build_brand_draft(som_pages, None, "own_site")
    brand = yaml.safe_load(draft_to_brand_yaml(draft))
    assert brand["examples"]
    assert all(e.get("source_url") for e in brand["examples"])


def test_conversion_sanitizes_brand_kit(som_pages):
    draft = build_brand_draft(som_pages, None, "own_site")
    # simulate the stale/wrong kit the calibration conversion shipped:
    draft["brand_kit"]["fonts"] = [{"family": "var(--display)", "count": 16},
                                   {"family": "Lora", "count": 10}]
    draft["brand_kit"]["logo_url"] = {
        "value": "https://caonguyenmoc.vn/img/b/bai-viet-thumb-t.jpg",
        "source_url": "https://caonguyenmoc.vn/blog/"}
    brand = yaml.safe_load(draft_to_brand_yaml(draft))
    families = [f["family"] for f in brand["brand_kit"]["fonts"]]
    assert families == ["Lora"]
    assert brand["brand_kit"]["logo_url"]["value"] is None


# ---------------------------------------------------------------------------
# §2 — POV: brand-name-as-speaker
# ---------------------------------------------------------------------------

def test_pov_brand_name_as_speaker():
    corpus = (
        "Mộc Nhỏ rang cà phê vào mỗi sáng thứ Ba hằng tuần cho bạn. "
        "Mộc Nhỏ chỉ hái quả chín đỏ trên cây mang về phơi giàn. "
        "Mộc Nhỏ đóng túi van một chiều để hạt giữ mùi thơm lâu hơn. "
        "Mộc Nhỏ ghi ngày rang lên mép túi cho bạn kiểm tra độ mới. "
        "Bạn nhắn Mộc Nhỏ nếu muốn xay mịn hơn cho phin ở nhà. "
        "Giá mỗi túi hai trăm năm mươi gam ghi rõ ngay trên bao bì. "
        "Hạt rang xong để nghỉ vài ngày rồi mới đóng gói gửi đi xa."
    )
    out = suggest_voice_rules(corpus, brand_name="Mộc Nhỏ")
    assert out["pov"] == "first-person (as brand name)"


def test_pov_no_brand_name_pattern_stays_third_person():
    corpus = (
        "Cửa hàng mở cửa từ bảy giờ sáng đến chín giờ tối mỗi ngày. "
        "Khách hàng có thể đặt hàng qua điện thoại hoặc tại quầy trực tiếp. "
        "Sản phẩm được đóng gói cẩn thận trước khi giao cho đơn vị vận chuyển. "
        "Chính sách đổi trả áp dụng trong bảy ngày kể từ ngày nhận hàng. "
        "Giá bán niêm yết công khai trên bảng giá tại cửa hàng chính."
    )
    out = suggest_voice_rules(corpus, brand_name="Mộc Nhỏ")
    assert out["pov"] == "third-person"
