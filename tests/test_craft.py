"""Tests for Layer 4 — Craft & Domain Library (content_core/craft.py + MCP tools).

Key comparisons:
- OLD pre-Craft demo product text (from demo_mcp_e2e.py) is correct-but-plain:
  uniform 13-14 word sentences -> monotone rhythm -> craft score 85.
- A sensory-rich rewrite with varied rhythm scores 100.
The fixtures below are the exact demo sections (OLD) and an original
sensory-rich rewrite (RICH) written for this test.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import mcp_server as srv  # noqa: E402
from content_core.craft import (craft_check, get_exemplars, glossary_group,  # noqa: E402
                                retrieve_cards, sensory_terms)
from content_core.loaders import (load_craft_library, load_domain_glossary,  # noqa: E402
                                  load_exemplars)

# Exact prose from demo_mcp_e2e.py (pre-Craft, Muse-written, brand-clean but plain).
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

# Sensory-rich rewrite of the same facts: varied rhythm, glossary terms, no weak words.
RICH_PRODUCT = (
    "185.000đ cho 250g. Mở túi ra, mùi caramel và hạt rang còn ấm chạm vào mũi trước tiên. "
    "Vị chua sáng của Arabica Cầu Đất đi qua nhanh trên đầu lưỡi. "
    "Rồi Robusta Đắk Lắk giữ lại hậu cacao đậm, hơi khói nhẹ, ở lại cuối lưỡi rất lâu sau ngụm cuối. "
    "Body dày vừa, không gắt. Bạn xay, rót nước, nghe tiếng bọt nở lách tách trong phin "
    "vào một buổi sáng thứ Tư còn yên tĩnh."
)

WEAK_TEXT = ("Cà phê này rất ngon và thơm ngon tuyệt vời, hoàn hảo và đặc biệt, "
             "chất lượng cao thật sự là không thể phủ nhận.")
MONOTONE_TEXT = (
    "Mình rang cà phê vào sáng thứ Ba hàng tuần. "
    "Mình đóng gói cà phê sau khi hạt đã nguội. "
    "Mình ghi ngày rang lên trên mép túi giấy. "
    "Mình gửi cà phê tới bạn trong tuần này."
)


# -- data loading -------------------------------------------------------------

def test_craft_data_files_load():
    lib = load_craft_library()
    assert len(lib["cards"]) >= 10
    for card in lib["cards"]:
        assert card["good_example"] and card["bad_example"] and card["source_note"]
        assert "paraphras" in card["source_note"].lower()
    glossary = load_domain_glossary()
    assert set(glossary["groups"]) == {"aroma", "acidity", "body", "aftertaste", "brewing", "roast"}
    assert len(glossary["weak_words"]) >= 10
    bank = load_exemplars()
    assert {e["format"] for e in bank["exemplars"]} == {
        "blog_intro", "product", "social", "email", "comparison"}


def test_exemplar_lengths_and_notes():
    for e in load_exemplars()["exemplars"]:
        words = len(e["text"].split())
        assert 60 <= words <= 120, (e["format"], words)
        assert 2 <= len(e["why_it_works"]) <= 4


# -- card retrieval: triggered vs dormant --------------------------------------

def test_retrieve_cards_triggered_by_intent_and_keyword():
    cards = retrieve_cards("blog", "sensory", "vị hương hạt pha")
    ids = [c["id"] for c in cards]
    assert "show-dont-tell-sensory" in ids
    assert len(cards) <= 3


def test_retrieve_cards_dormant_for_unrelated_task():
    cards = retrieve_cards("social", "zzz-unrelated", "thời tiết biển hôm nay")
    ids = [c["id"] for c in cards]
    # Only the always-on core card survives; triggered cards stay dormant.
    assert ids == ["concrete-specifics"]
    assert "show-dont-tell-sensory" not in ids
    assert "logical-transitions" not in ids


def test_retrieve_cards_format_gate_and_cap():
    # logical-transitions is comparison/blog only -> dormant for email.
    cards = retrieve_cards("email", "comparison", "so sánh đối thủ")
    assert "logical-transitions" not in [c["id"] for c in cards]
    # Exactly one core card, never more, and the cap is 3 total.
    cards = retrieve_cards("product", "product", "giá vị hương mô tả sản phẩm")
    assert sum(1 for c in cards if c.get("core")) == 1
    assert len(cards) == 3


# -- glossary / exemplars via craft.py -----------------------------------------

def test_glossary_and_exemplars():
    assert glossary_group("aroma")["terms"]
    assert glossary_group("khong-ton-tai")["error"]
    full = glossary_group()
    assert full["weak_words"]
    assert get_exemplars("product")[0]["format"] == "product"
    assert get_exemplars("khong-co") == []
    terms = sensory_terms()
    assert "caramel" in terms and "hậu cacao" in terms


# -- craft_check -----------------------------------------------------------------

def test_craft_check_catches_weak_words():
    out = craft_check(WEAK_TEXT, "product")
    found = set(out["metrics"]["weak_words_found"])
    assert {"ngon", "thơm ngon", "tuyệt vời", "hoàn hảo", "đặc biệt", "chất lượng cao"} <= found
    assert out["score"] < 85
    weak_violations = [v for v in out["violations"] if v["type"] == "weak_word"]
    assert all(v["replace_with"] for v in weak_violations)  # every hit suggests a replacement


def test_craft_check_flags_monotone_and_repeated_openings():
    out = craft_check(MONOTONE_TEXT, "social")
    types = {v["type"] for v in out["violations"]}
    assert "monotone_rhythm" in types
    # PIN UPDATED (core-completion round, evaluation R3 §3.4):
    # repeated_openings used to fire here because all four sentences
    # open with the token "Mình" — but their TWO-token openings differ
    # ("Mình rang", "Mình đóng", "Mình ghi", "Mình gửi": one subject,
    # four different verbs), which is exactly the first-syllable
    # artefact the two-token key removes. Genuine two-token repetition
    # is pinned in tests/test_core_closing.py.
    assert "repeated_openings" not in types
    assert out["metrics"]["rhythm_stdev"] < 2.0


def test_craft_check_sensory_rich_scores_high():
    out = craft_check(RICH_PRODUCT, "product")
    assert out["score"] >= 85
    assert len(out["metrics"]["sensory_terms_used"]) >= 5
    assert out["violations"] == []


def test_craft_check_old_demo_scores_lower_than_rich():
    old = craft_check(OLD_DEMO_PRODUCT, "product")
    rich = craft_check(RICH_PRODUCT, "product")
    assert old["score"] < rich["score"], (old, rich)
    # The old text is plain, not bad: its only craft problem is monotone rhythm.
    assert {v["type"] for v in old["violations"]} == {"monotone_rhythm"}
    assert old["score"] == 85.0 and rich["score"] == 100.0


def test_craft_check_no_sensory_penalty_outside_product_blog():
    out = craft_check(MONOTONE_TEXT, "email")
    assert "no_sensory_detail" not in {v["type"] for v in out["violations"]}


# -- MCP layer ---------------------------------------------------------------------

def test_mcp_tool_list_matches_canonical():
    # ONE canonical literal (review M19): tests/test_mcp_server.py's
    # EXPECTED_TOOLS. This file used to carry a second copy plus a
    # stale 'now_29' test name from an older round — duplicates drift.
    from test_mcp_server import EXPECTED_TOOLS
    assert set(srv.TOOL_NAMES) == EXPECTED_TOOLS
    assert len(srv.TOOL_NAMES) == 31
    for name in EXPECTED_TOOLS:
        assert callable(getattr(srv, name)), name


def test_mcp_craft_tools():
    cards = srv.get_craft_cards(format="blog", intent="sensory", query="vị hương hạt")
    assert "show-dont-tell-sensory" in [c["id"] for c in cards["cards"]]
    ex = srv.get_exemplars("social")
    assert ex["count"] == 1 and ex["exemplars"][0]["format"] == "social"
    gloss = srv.get_domain_glossary("body")
    assert gloss["group"] == "body"
    check = srv.craft_check(text=WEAK_TEXT, format="product")
    assert check["score"] < 85 and check["metrics"]["weak_words_found"]


def test_assemble_context_new_order():
    out = srv.assemble_context({"format": "product", "topic": "mô tả gói cà phê giá vị hương",
                                "keyword": "cà phê", "title": "Gói cà phê Mộc Brew 250g"})
    parts = [o["part"] for o in out["ordered_context"]]
    assert parts == ["persona", "brand_rules", "craft_cards", "facts_triggered",
                     "exemplar", "task", "post_history_reminder"]
    # Backlog item 8: full structured payloads were replaced by counts
    # (the rendered context_text is the single representation).
    assert out["counts"]["craft_cards"] >= 1 and out["counts"]["exemplars"] >= 1
    assert "185.000đ" in out["context_text"]
