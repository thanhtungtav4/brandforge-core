"""Tests for the GEO layer (2026-11-27, specs/geo-feature.md).

Layers under test:
- content_core/geo.py — the pure deterministic checker (8 checks,
  renormalisation over applicable checks, per-section fixes) and the
  judgement validator;
- mcp_server GEO tools — check_geo (stores the report on the draft),
  record_geo_judgement (the host model's judged layer, mirroring
  record_vision_check);
- the ADVISORY boundary: GEO never changes final_gate's verdict or
  check_draft_all's existing fields — the only difference a stored
  GEO report makes is the additive `geo` summary (differential test);
- assemble_context's opt-in `geo_guidance` part (absent by default,
  so existing contexts stay byte-identical);
- the Craft Library GEO technique card (dormant until triggered).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import mcp_server as srv  # noqa: E402
from content_core import geo  # noqa: E402
from content_core.craft import retrieve_cards  # noqa: E402

BRIEF = {"id": "geo-test", "format": "blog",
         "topic": "Mắm cá lóc Bà Giáo Khỏe",
         "keyword": "mắm cá lóc",
         "title": "Mắm cá lóc Bà Giáo Khỏe: vị mắm Châu Đốc"}

# An opening that answers first: entity + definition in sentence 1,
# a priced fact in sentence 2, an attributed statistic in sentence 3.
ANSWER_FIRST = (
    "Mắm cá lóc là loại mắm làm từ cá lóc đồng ủ muối và thính gạo, "
    "đặc sản của Châu Đốc, An Giang. Một hủ mắm cá lóc 500g có giá "
    "100.000đ tại tiệm Bà Giáo Khỏe. Theo Sở Nông nghiệp An Giang, "
    "làng mắm Châu Đốc đã có hơn 100 năm làm nghề này."
)

# An opening that buries the answer: scenery first, no entity, no
# number, no definition in the first two sentences.
BURIED = (
    "Trời còn tối đất, bến cá đã rộn lên như mọi buổi sớm mùa nước. "
    "Tiếng máy ghe nổ giòn trên mặt rạch, tiếng người gọi nhau í ới. "
    "Cá theo con nước nổi về đầy ghe, thứ nào cũng tươi rói."
)


@pytest.fixture(autouse=True)
def _isolated_stores(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_CORE_DRAFTS_DIR", str(tmp_path / "drafts"))
    monkeypatch.setenv("CONTENT_CORE_BRAND_DRAFTS_DIR",
                       str(tmp_path / "brand_drafts"))


def _checks(report: dict) -> dict:
    return {c["key"]: c for c in report["checks"]}


def _draft(sections, brief=None) -> str:
    draft_id = srv.start_draft(brief or BRIEF)["draft_id"]
    for sec in sections:
        srv.save_section(draft_id, sec["id"], sec.get("heading", ""),
                         sec["text"])
    return draft_id


# ---------------------------------------------------------------------------
# answer_first
# ---------------------------------------------------------------------------

def test_answer_first_passes_on_direct_opening():
    report = geo.check_geo([{"id": "s1", "heading": "Mở đầu",
                             "text": ANSWER_FIRST}], BRIEF)
    check = _checks(report)["answer_first"]
    assert check["status"] == "pass" and check["score"] == 100
    assert check["evidence"]["entity_early"] is True
    assert check["evidence"]["direct_answer"] is True


def test_answer_first_fails_on_buried_opening():
    report = geo.check_geo([{"id": "s1", "heading": "Mở đầu",
                             "text": BURIED}], BRIEF)
    check = _checks(report)["answer_first"]
    assert check["status"] == "fail" and check["score"] == 0
    # …and the fix list tells the writer exactly what to do about it.
    assert any(f["section_id"] == "s1" and f["check"] == "answer_first"
               for f in report["fixes"])


def test_answer_first_partial_credit_for_entity_without_answer():
    text = ("Mắm cá lóc gắn với bữa cơm của người Châu Đốc từ lâu đời. "
            "Mỗi mùa nước nổi, cá về đầy ghe và nhà nào cũng ủ vài hủ.")
    report = geo.check_geo([{"id": "s1", "heading": "", "text": text}], BRIEF)
    check = _checks(report)["answer_first"]
    assert check["evidence"]["entity_early"] is True
    assert check["evidence"]["direct_answer"] is False
    assert 0 < check["score"] < 70


# ---------------------------------------------------------------------------
# question_headings
# ---------------------------------------------------------------------------

def test_question_headings_share_scored():
    sections = [
        {"id": "s1", "heading": "Mắm cá lóc là gì?", "text": ANSWER_FIRST},
        {"id": "s2", "heading": "Giá mắm cá lóc bao nhiêu?",
         "text": "Một hủ 500g giá 100.000đ."},
        {"id": "s3", "heading": "Cách chọn mắm ngon",
         "text": "Chọn hủ có màu đỏ au tự nhiên."},
        {"id": "s4", "heading": "Bảo quản mắm như thế nào?",
         "text": "Để nơi thoáng mát, tránh nắng."},
    ]
    check = _checks(geo.check_geo(sections, BRIEF))["question_headings"]
    assert check["evidence"]["question_headings"] == 3
    assert check["evidence"]["headed_sections"] == 4
    assert check["score"] == 100  # 3/4 = 0.75 >= the 0.40 full-marks share


def test_question_headings_fail_when_none_are_questions():
    sections = [
        {"id": "s1", "heading": "Giới thiệu", "text": ANSWER_FIRST},
        {"id": "s2", "heading": "Giá bán", "text": "Một hủ 500g giá 100.000đ."},
    ]
    check = _checks(geo.check_geo(sections, BRIEF))["question_headings"]
    assert check["score"] == 0 and check["status"] == "fail"


def test_question_headings_excluded_with_too_few_headings():
    sections = [{"id": "s1", "heading": "Duy nhất", "text": ANSWER_FIRST}]
    report = geo.check_geo(sections, BRIEF)
    check = _checks(report)["question_headings"]
    assert check["status"] == "not_applicable" and check["score"] is None
    assert "question_headings" in report["excluded_checks"]


# ---------------------------------------------------------------------------
# fact_density / attribution
# ---------------------------------------------------------------------------

def _padded(numeric_sentences: int, total_sentences: int,
            attributed: int = 0) -> str:
    """A text of `total_sentences` plain sentences, `numeric_sentences`
    of them carrying a number, `attributed` of those carrying a source
    cue. ~10 words per sentence, so 100 sentences ≈ 1,000 words."""
    plain = "Người làm mắm ở Châu Đốc giữ nghề theo cách cha ông truyền lại."
    numeric = "Một hủ mắm cá lóc 500g có giá 100.000đ tại tiệm."
    cited = "Theo Sở Nông nghiệp An Giang, làng mắm có hơn 100 năm tuổi nghề."
    out = []
    for i in range(total_sentences):
        if i < attributed:
            out.append(cited)
        elif i < numeric_sentences:
            out.append(numeric)
        else:
            out.append(plain)
    return " ".join(out)


def test_fact_density_full_marks_at_target():
    # 100 sentences ≈ 1,370 words; 10 numeric → density ≈ 7.3/1000 >= 6.
    text = _padded(numeric_sentences=10, total_sentences=100)
    check = _checks(geo.check_geo(
        [{"id": "s1", "heading": "", "text": text}], BRIEF))["fact_density"]
    assert check["score"] == 100
    assert check["evidence"]["numeric_sentences"] == 10


def test_fact_density_zero_without_numbers():
    text = _padded(numeric_sentences=0, total_sentences=60)
    check = _checks(geo.check_geo(
        [{"id": "s1", "heading": "", "text": text}], BRIEF))["fact_density"]
    assert check["score"] == 0 and check["status"] == "fail"


def test_fact_density_excluded_for_short_text():
    check = _checks(geo.check_geo(
        [{"id": "s1", "heading": "", "text": "Mắm ngon lắm bạn ơi."}],
        BRIEF))["fact_density"]
    assert check["status"] == "not_applicable"


def test_attribution_share_scored():
    # 4 numeric sentences, 2 attributed → share 0.5 → full marks.
    text = _padded(numeric_sentences=4, total_sentences=60, attributed=2)
    check = _checks(geo.check_geo(
        [{"id": "s1", "heading": "", "text": text}], BRIEF))["attribution"]
    assert check["evidence"]["numeric_sentences"] == 4
    assert check["evidence"]["attributed_sentences"] == 2
    assert check["score"] == 100


def test_attribution_zero_without_source_cues():
    text = _padded(numeric_sentences=4, total_sentences=60)
    check = _checks(geo.check_geo(
        [{"id": "s1", "heading": "", "text": text}], BRIEF))["attribution"]
    assert check["score"] == 0


def test_attribution_excluded_without_numeric_claims():
    text = _padded(numeric_sentences=0, total_sentences=60)
    check = _checks(geo.check_geo(
        [{"id": "s1", "heading": "", "text": text}], BRIEF))["attribution"]
    assert check["status"] == "not_applicable"


# ---------------------------------------------------------------------------
# quotable_blocks / definition_present
# ---------------------------------------------------------------------------

def test_quotable_blocks_counted():
    text = (
        "Mắm cá lóc nguyên con có giá 100.000đ cho một hủ 500g. "
        "Mắm cá lóc cắt khúc có giá 110.000đ cho một hủ 500g. "
        "Mắm cá lóc xay sẵn có giá 100.000đ cho một hủ 500g. "
        "Mắm cá lóc filet có giá 120.000đ cho một hủ 500g. "
        "Nó ngon lắm, ai ăn rồi cũng nhớ hoài không quên được đâu."
    )
    check = _checks(geo.check_geo(
        [{"id": "s1", "heading": "", "text": text}], BRIEF))["quotable_blocks"]
    # The four priced statements are quotable; the last sentence opens
    # with a pronoun reference and carries no fact — not quotable.
    assert check["evidence"]["quotable_blocks"] == 4
    assert check["score"] == 100


def test_definition_present_and_missing():
    found = _checks(geo.check_geo(
        [{"id": "s1", "heading": "", "text": ANSWER_FIRST}],
        BRIEF))["definition_present"]
    assert found["score"] == 100 and found["status"] == "pass"
    assert "Mắm cá lóc là" in found["evidence"]["definition"]

    missing = _checks(geo.check_geo(
        [{"id": "s1", "heading": "", "text": BURIED}], BRIEF)
        )["definition_present"]
    assert missing["score"] == 0 and missing["status"] == "fail"


def test_definition_excluded_without_topic():
    report = geo.check_geo([{"id": "s1", "heading": "", "text": ANSWER_FIRST}],
                           {"format": "blog"})
    check = _checks(report)["definition_present"]
    assert check["status"] == "not_applicable"


# ---------------------------------------------------------------------------
# structure / faq_present
# ---------------------------------------------------------------------------

def test_structure_rewards_lists_and_extractable_paragraphs():
    text = ("Đoạn một nói về nguồn gốc của món mắm này ở vùng Châu Đốc "
            "vào mùa nước nổi hằng năm.\n\n"
            "Các loại mắm ở tiệm gồm:\n"
            "- Mắm nguyên con ủ đủ 3 tháng\n"
            "- Mắm cắt khúc tiện nấu canh chua\n"
            "- Mắm xay sẵn cho người bận rộn\n\n"
            "Đoạn cuối dặn cách giữ hủ mắm ngon sau khi mở nắp ra dùng.")
    sections = [{"id": "s1", "heading": "Các loại mắm", "text": text},
                {"id": "s2", "heading": "Bảo quản", "text": "Để nơi mát."}]
    check = _checks(geo.check_geo(sections, BRIEF))["structure"]
    assert check["evidence"]["has_list"] is True
    assert check["score"] >= 70


def test_structure_fails_on_one_giant_unheaded_block():
    giant = " ".join(["Câu chuyện về làng nghề ven sông Hậu kéo dài "
                      "qua nhiều thế hệ cư dân."] * 20)  # ~300 words, 1 para
    sections = [{"id": "s1", "heading": "", "text": giant}]
    check = _checks(geo.check_geo(sections, BRIEF))["structure"]
    assert check["evidence"]["has_list"] is False
    assert check["score"] < 40


def test_faq_present_via_heading_and_via_qa_pairs():
    by_heading = _checks(geo.check_geo(
        [{"id": "s1", "heading": "Nội dung", "text": ANSWER_FIRST},
         {"id": "s2", "heading": "Hỏi đáp về mắm cá lóc",
          "text": "Mắm để được bao lâu? Mắm để được 12 tháng."}],
        BRIEF))["faq_present"]
    assert by_heading["score"] == 100

    qa_text = ("Mắm cá lóc để được bao lâu? Hủ mắm chưa mở nắp để được "
               "12 tháng ở nơi thoáng mát. Mắm có gửi đi tỉnh khác "
               "không? Tiệm đóng hủ kín và gửi đi các tỉnh trong 3 ngày.")
    by_pairs = _checks(geo.check_geo(
        [{"id": "s1", "heading": "Thông tin", "text": qa_text}],
        BRIEF))["faq_present"]
    assert by_pairs["score"] == 100
    assert by_pairs["evidence"]["qa_pairs"] == 2


def test_faq_missing_and_excluded_for_social():
    missing = _checks(geo.check_geo(
        [{"id": "s1", "heading": "Nội dung", "text": ANSWER_FIRST}],
        BRIEF))["faq_present"]
    assert missing["score"] == 0

    social = _checks(geo.check_geo(
        [{"id": "s1", "heading": "", "text": ANSWER_FIRST}],
        {**BRIEF, "format": "social"}))["faq_present"]
    assert social["status"] == "not_applicable"


# ---------------------------------------------------------------------------
# Renormalisation + score bounds
# ---------------------------------------------------------------------------

def test_renormalisation_over_applicable_checks_only():
    # Social format, one unheaded section, ~110 words with numbers:
    # faq + question_headings are excluded; the score must be the
    # weighted mean over the REMAINING checks with their weights
    # renormalised — not dragged down by the excluded ones.
    text = _padded(numeric_sentences=3, total_sentences=12, attributed=1)
    report = geo.check_geo([{"id": "s1", "heading": "", "text": text}],
                           {**BRIEF, "format": "social"})
    assert "faq_present" in report["excluded_checks"]
    assert "question_headings" in report["excluded_checks"]
    applicable = [c for c in report["checks"]
                  if c["status"] != "not_applicable"]
    total_weight = sum(c["weight"] for c in applicable)
    expected = round(sum(c["score"] * c["weight"] for c in applicable)
                     / total_weight, 2)
    assert report["geo_score"] == expected
    assert 0 <= report["geo_score"] <= 100


def test_empty_draft_scores_none_not_zero():
    report = geo.check_geo([], BRIEF)
    assert report["geo_score"] is None
    assert report["applicable_checks"] == []
    report2 = geo.check_geo([{"id": "s1", "heading": "", "text": ""}], BRIEF)
    assert report2["geo_score"] is None


def test_all_scores_within_bounds_on_mixed_fixture():
    sections = [
        {"id": "s1", "heading": "Mắm cá lóc là gì?", "text": ANSWER_FIRST},
        {"id": "s2", "heading": "Cách làm", "text": BURIED},
    ]
    report = geo.check_geo(sections, BRIEF)
    for check in report["checks"]:
        if check["score"] is not None:
            assert 0 <= check["score"] <= 100
    assert report["weights_status"] == "provisional_v1"
    assert sum(geo.GEO_WEIGHTS.values()) == 100


# ---------------------------------------------------------------------------
# MCP tools: check_geo storage + judgement layer
# ---------------------------------------------------------------------------

def test_check_geo_tool_stores_report_on_draft():
    draft_id = _draft([{"id": "s1", "heading": "Mắm cá lóc là gì?",
                        "text": ANSWER_FIRST}])
    out = srv.check_geo(draft_id)
    assert "error" not in out, out
    assert out["draft_id"] == draft_id
    assert out["geo_score"] is not None
    assert out["checked_at"]  # stamped by the tool, not the pure checker
    stored = srv._load_draft(draft_id)
    assert stored["geo"]["report"]["geo_score"] == out["geo_score"]
    assert stored["geo"]["report"]["checked_at"] == out["checked_at"]


def test_check_geo_tool_unknown_draft_is_structured_error():
    out = srv.check_geo("d-doesnotexist")
    assert "error" in out and out["draft_id"] == "d-doesnotexist"


def test_record_geo_judgement_roundtrip_and_hard_flag():
    draft_id = _draft([{"id": "s1", "heading": "", "text": ANSWER_FIRST}])
    srv.check_geo(draft_id)
    out = srv.record_geo_judgement(
        draft_id,
        {"citability": 2, "answer_clarity": 0, "self_contained": 1},
        ["buried_answer"])
    assert "error" not in out, out
    judgement = out["judgement"]
    assert judgement["hard_flags"] == ["answer_clarity"]
    # GRADE_SCORES {0:20, 1:60, 2:95} — mean of (95, 20, 60).
    assert judgement["judgement_score"] == round((95 + 20 + 60) / 3, 2)
    assert judgement["judged_at"]
    # The judgement survives a re-run of the deterministic check.
    srv.check_geo(draft_id)
    stored = srv._load_draft(draft_id)
    assert stored["geo"]["judgement"]["grades"]["citability"] == 2


def test_record_geo_judgement_before_check_geo_is_error():
    draft_id = _draft([{"id": "s1", "heading": "", "text": ANSWER_FIRST}])
    out = srv.record_geo_judgement(
        draft_id, {"citability": 2, "answer_clarity": 2,
                   "self_contained": 2}, [])
    assert "error" in out


@pytest.mark.parametrize("grades", [
    {"citability": 3, "answer_clarity": 2, "self_contained": 2},   # range
    {"citability": True, "answer_clarity": 2, "self_contained": 2},  # bool
    {"citability": 2.0, "answer_clarity": 2, "self_contained": 2},  # float
    {"citability": 2, "answer_clarity": 2},                        # missing
    {"citability": 2, "answer_clarity": 2, "self_contained": 2,
     "extra": 1},                                                  # unknown dim
])
def test_record_geo_judgement_rejects_bad_grades(grades):
    draft_id = _draft([{"id": "s1", "heading": "", "text": ANSWER_FIRST}])
    srv.check_geo(draft_id)
    out = srv.record_geo_judgement(draft_id, grades, [])
    assert "error" in out


def test_record_geo_judgement_rejects_unknown_issue():
    draft_id = _draft([{"id": "s1", "heading": "", "text": ANSWER_FIRST}])
    srv.check_geo(draft_id)
    out = srv.record_geo_judgement(
        draft_id, {"citability": 2, "answer_clarity": 2,
                   "self_contained": 2}, ["not_a_real_issue"])
    assert "error" in out


# ---------------------------------------------------------------------------
# Advisory boundary — the differential
# ---------------------------------------------------------------------------

_CLEAN_SECTIONS = [
    {"id": "s1", "heading": "Cà phê cho văn phòng",
     "text": "Mộc Brew rang cà phê vào thứ Ba hàng tuần cho bạn. "
             "Một gói 250g có giá 185.000đ trước khi nói tới hương vị. "
             "Hạt Arabica Cầu Đất cho vị chua nhẹ và hậu ngọt rõ. "
             "Hạt Robusta Đắk Lắk cho vị đậm và lớp crema dày hơn. "
             "Bạn pha phin chậm rãi vào buổi sáng trước giờ làm việc."},
    {"id": "s2", "heading": "Giao hàng và bảo quản",
     "text": "Đơn hàng được giao trong 2-3 ngày sau khi bạn xác nhận. "
             "Mình ghi rõ ngày rang trên từng gói gửi tới tay bạn. "
             "Bạn bảo quản hạt trong hộp kín và tránh ánh nắng trực tiếp. "
             "Mỗi mẻ rang đều được nếm thử trước khi giao cho khách hàng."},
]
_CLEAN_BRIEF = {"id": "geo-diff", "format": "blog",
                "topic": "cà phê văn phòng giá", "keyword": "cà phê",
                "title": "Cà phê văn phòng"}


def test_geo_is_additive_only_for_gate_and_combined_check():
    draft_id = _draft(_CLEAN_SECTIONS, _CLEAN_BRIEF)
    gate_before = srv.final_gate(draft_id)
    combined_before = srv.check_draft_all(draft_id)
    assert "geo" not in combined_before

    srv.check_geo(draft_id)
    srv.record_geo_judgement(
        draft_id, {"citability": 1, "answer_clarity": 1,
                   "self_contained": 1}, ["vague_claims"])

    gate_after = srv.final_gate(draft_id)
    combined_after = srv.check_draft_all(draft_id)

    # The gate verdict and every number in it are untouched by GEO.
    for key in ("passed", "brand_score", "craft_score",
                "failing_sections", "quality"):
        assert gate_after.get(key) == gate_before.get(key), key
    # check_draft_all: identical except the additive `geo` summary.
    assert set(combined_after) == set(combined_before) | {"geo"}
    for key in combined_before:
        assert combined_after[key] == combined_before[key], key
    summary = combined_after["geo"]
    assert summary["geo_score"] is not None
    assert summary["judgement_score"] is not None
    assert summary["hard_flags"] == []


# ---------------------------------------------------------------------------
# assemble_context geo_guidance part (opt-in)
# ---------------------------------------------------------------------------

def test_geo_guidance_absent_by_default():
    out = srv.assemble_context({"format": "blog", "topic": "cà phê văn phòng",
                                "keyword": "cà phê", "title": "Cà phê văn phòng"})
    parts = [o["part"] for o in out["ordered_context"]]
    assert parts == ["persona", "brand_rules", "craft_cards",
                     "facts_triggered", "exemplar", "task",
                     "post_history_reminder"]
    assert "geo_guidance" not in out


def test_geo_guidance_present_when_opted_in():
    brief = {"format": "blog", "topic": "cà phê văn phòng",
             "keyword": "cà phê", "title": "Cà phê văn phòng"}
    out = srv.assemble_context(brief, geo_guidance=True)
    parts = [o["part"] for o in out["ordered_context"]]
    assert "geo_guidance" in parts
    assert parts.index("exemplar") < parts.index("geo_guidance")
    assert parts.index("geo_guidance") < parts.index("task")
    assert out["geo_guidance"]["enabled"] is True
    # The guidance names the concrete moves (answer-first, quotable
    # facts, question headings, cited statistics, one definition).
    text = out["context_text"].lower()
    assert "answer" in text or "trả lời" in text
    # A brief-level opt-in works too.
    out2 = srv.assemble_context({**brief, "geo": True})
    assert "geo_guidance" in [o["part"] for o in out2["ordered_context"]]


# ---------------------------------------------------------------------------
# Craft Library card
# ---------------------------------------------------------------------------

def test_geo_craft_card_registered_and_triggered():
    cards = retrieve_cards("blog", "geo", "tối ưu geo cho ai overview")
    ids = [c["id"] for c in cards]
    assert "answer-first-geo" in ids
    card = next(c for c in cards if c["id"] == "answer-first-geo")
    assert card["source_note"]  # paraphrased-technique provenance, as always
    # Dormant for an unrelated writing task (no geo intent/keywords).
    plain = retrieve_cards("blog", "drafting", "viết đoạn mở đầu cho bài")
    assert "answer-first-geo" not in [c["id"] for c in plain]


# ---------------------------------------------------------------------------
# Round 2 (daily proposals 2026-11-28, Smith-approved):
# 1) the card teaches an explicit answer+definition LEAD-IN layer that
#    stands BEFORE the persona's literary opening (voice unchanged);
# 2) the geo_guidance part gains a question-headings + closing-FAQ
#    pattern block, scoped to shopping/recipe briefs only.
# ---------------------------------------------------------------------------

def test_geo_card_teaches_leadin_layer_before_literary_opening():
    cards = retrieve_cards("blog", "geo", "tối ưu geo cho ai overview")
    card = next(c for c in cards if c["id"] == "answer-first-geo")
    instruction = card["instruction"]
    low = instruction.lower()
    # The lead-in is named as its own layer, FIRST in the article…
    lead = low.find("lớp mở đầu trả lời")
    assert lead >= 0, instruction
    # …carrying the definition pattern…
    assert "là …" in instruction or "là ..." in instruction
    # …and only AFTER it comes the persona's own opening, voice intact.
    assert low.find("giọng", lead) > lead
    assert "không thay giọng" in low
    # The good example demonstrates the sequence on a narrative piece:
    # definition lead-in first, then the scene opening.
    example = card["good_example"]
    assert "Mắm cá lóc là" in example
    assert example.find("Mắm cá lóc là") < example.find("bến cá")


def test_geo_card_scopes_question_faq_patterns_to_shopping_recipe():
    cards = retrieve_cards("blog", "geo", "tối ưu geo cho ai overview")
    card = next(c for c in cards if c["id"] == "answer-first-geo")
    low = card["instruction"].lower()
    # Question headings + closing FAQ are prescribed for shopping /
    # recipe pieces and explicitly NOT forced on narrative essays.
    assert "mua sắm/công thức" in low
    assert "hỏi đáp" in low
    assert "tản văn" in low


def _part_names(out: dict) -> list[str]:
    return [o["part"] for o in out["ordered_context"]]


def test_geo_guidance_shopping_patterns_for_product_format():
    out = srv.assemble_context({**BRIEF, "format": "product", "geo": True})
    assert "geo_guidance" in _part_names(out)
    # Distinctive marker of the shopping-pattern block: its header
    # line (the base guidance never names the pattern block).
    assert "Pattern riêng cho bài mua sắm/công thức" in out["context_text"]
    assert out["geo_guidance"]["patterns"] == ["question_headings",
                                               "faq_closing"]


def test_geo_guidance_shopping_patterns_for_comparison_format():
    out = srv.assemble_context({**BRIEF, "format": "comparison",
                                "geo": True})
    assert out["geo_guidance"]["patterns"] == ["question_headings",
                                               "faq_closing"]


def test_geo_guidance_shopping_patterns_for_recipe_intent_on_blog():
    # A recipe published as a blog post still gets the patterns: the
    # brief's intent is the signal (the same signal craft cards use).
    out = srv.assemble_context({**BRIEF, "intent": "recipe", "geo": True})
    assert "Pattern riêng cho bài mua sắm/công thức" in out["context_text"]
    assert out["geo_guidance"]["patterns"] == ["question_headings",
                                               "faq_closing"]


def test_geo_guidance_no_shopping_patterns_for_blog_essay():
    # The mùa nước nổi shape: blog format, narrative piece, GEO on —
    # base guidance only, no question-heading/FAQ prescription.
    out = srv.assemble_context({**BRIEF, "geo": True})
    assert "geo_guidance" in _part_names(out)
    assert "Pattern riêng cho bài mua sắm/công thức" not in out["context_text"]
    assert out["geo_guidance"]["patterns"] == []


def test_geo_guidance_patterns_absent_when_geo_not_opted_in():
    # Even a shopping-format brief gets NO geo part at all without
    # the opt-in — the byte-identical default is untouched.
    out = srv.assemble_context({**BRIEF, "format": "product"})
    assert "geo_guidance" not in _part_names(out)
    assert "geo_guidance" not in out
