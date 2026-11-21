"""Tests for Style Extraction (content_core/style.py + MCP tools + fetch helper).

Discrimination fixtures, measured against the shipped default profile
(mocbrew-blog-voice: median 13.0, stdev 8.72, short share 0.28,
first-person 3.5/100w, reader 1.93/100w, number density 0.36/100w):
- SAME_VOICE: another conversational Mộc Brew text -> style_match 100.
- MONOTONE: uniform ~14-word sentences, no first person, no 'bạn', no
  numbers -> 77 (sentence_stdev + short_sentence_share flagged; at 115
  words its density metrics are low-reliability/widened since the
  calibration fix round — before that it scored 61 on density noise).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import mcp_server as srv  # noqa: E402
from content_core.loaders import load_style_profile  # noqa: E402
from content_core.style import (analyze_samples, build_profile, fingerprint,  # noqa: E402
                                style_match)
from fetch_sample import strip_html  # noqa: E402

# Hand-computed: sentences of 5 and 9 words -> median 7, mean 7,
# population stdev 2.0; 1 of 2 sentences <= 6 words; 1 question in 2 sentences.
EXACT_TEXT = "Một hai ba bốn năm. Sáu bảy tám chín mười mười một mười hai?"

CONV1 = ("Sáng nay mình pha phin sớm hơn thường lệ. Bạn tin không, cả văn phòng tỉnh hẳn. "
         "Mình rót nước, chờ bọt nở, rồi nghe tiếng giọt rơi chậm rãi xuống cốc thủy tinh quen thuộc. "
         "Ngắn thôi. Nhưng đủ. Bạn thử một lần xem, mình cá là bạn sẽ bỏ cà phê hòa tan ngay tuần này.")
CONV2 = ("Mình hay ghi ngày rang lên mép túi cho bạn nhớ. Vì hạt để lâu là mất mùi. "
         "Có hôm mình rang hỏng cả một mẻ chỉ vì mải nghe điện thoại của khách hàng quen thuộc. "
         "Buồn. Nhưng thà bỏ. Bạn mua hạt của mình thì phải nhận đúng mẻ mình dám uống trước.")
LEGAL = ("Căn cứ theo quy định của pháp luật hiện hành về hoạt động thương mại, các bên tham gia "
         "giao dịch có nghĩa vụ thực hiện đầy đủ những điều khoản đã được thống nhất trong văn bản "
         "thỏa thuận chính thức giữa hai bên. "
         "Việc chậm trễ trong quá trình thực hiện nghĩa vụ thanh toán sẽ dẫn đến trách nhiệm bồi "
         "thường thiệt hại phát sinh theo đúng trình tự thủ tục do cơ quan có thẩm quyền ban hành "
         "và hướng dẫn thi hành chi tiết. "
         "Mọi tranh chấp phát sinh từ quan hệ hợp đồng này sẽ được giải quyết thông qua thương "
         "lượng hòa giải trước khi chuyển đến cơ quan tài phán theo quy định chung của hệ thống "
         "pháp luật quốc gia hiện hành.")

SAME_VOICE = (
    "Mình mở túi hạt mới rang ngay khi vừa tới văn phòng sáng nay. "
    "Mùi caramel ấm chạm vào mũi trước tiên, rồi mùi cacao theo sau, "
    "giống hệt mùi ở xưởng rang mỗi sáng thứ Ba khi mình mở cửa bước vào phòng rang nhỏ. "
    "Bạn thử đoán xem mình làm gì tiếp theo? Đun nước. Rồi chờ. "
    "Mình rót nước thật chậm lên mặt bột, nghe tiếng bọt nở lách tách trong phin, "
    "nhìn từng giọt rơi xuống cốc thủy tinh quen thuộc trên bàn làm việc của mình mỗi ngày. "
    "Ngụm đầu tiên luôn là ngụm đáng chờ nhất. "
    "Vị chua sáng của Arabica Cầu Đất đến nhanh trên đầu lưỡi, sáng và gọn, "
    "rồi nhường chỗ cho hậu cacao đậm của Robusta Đắk Lắk ở lại rất lâu sau ngụm cuối. "
    "Một gói như vậy giá 185.000đ, mình ghi ngay ở dòng đầu cho bạn dễ tính. "
    "Bạn pha mỗi sáng một phin nhỏ thì gói này đi được gần hai tuần làm việc. "
    "Mình vẫn ghi ngày rang lên mép túi bằng tay như mọi khi. "
    "Chậm hơn in nhãn, nhưng mình muốn bạn thấy con số đó mỗi sáng."
)
MONOTONE = (
    "Người ta uống cà phê vào buổi sáng trước giờ làm việc ở văn phòng. "
    "Người ta pha cà phê bằng phin nhôm với nước nóng vừa mới đun sôi. "
    "Người ta chờ cà phê nhỏ giọt xuống cốc trong vài phút yên tĩnh. "
    "Người ta thêm đường hoặc sữa tùy theo khẩu vị của từng người uống. "
    "Người ta mang cà phê theo khi di chuyển tới chỗ làm mỗi ngày. "
    "Người ta uống hết cốc cà phê rồi bắt đầu công việc của ngày mới. "
    "Người ta rửa phin và cốc sau khi uống xong bữa sáng nhẹ nhàng. "
    "Người ta lặp lại thói quen này đều đặn mỗi buổi sáng trong tuần."
)

CLEAN_TEXT = (
    "Mộc Brew rang cà phê vào thứ Ba hàng tuần cho bạn. "
    "Một gói 250g có giá 185.000đ trước khi nói tới hương vị. "
    "Hạt Arabica Cầu Đất cho vị chua nhẹ và hậu ngọt rõ. "
    "Mình ghi rõ ngày rang trên từng gói gửi tới tay bạn."
)


@pytest.fixture(autouse=True)
def _isolated_drafts(tmp_path, monkeypatch):
    # Only drafts are isolated for every test. Style profiles are NOT:
    # several tests read the shipped default profile from data/. The
    # save/get roundtrip test monkeypatches the profiles dir itself.
    monkeypatch.setenv("CONTENT_CORE_DRAFTS_DIR", str(tmp_path / "drafts"))


# -- fingerprint ---------------------------------------------------------------

def test_fingerprint_exactness():
    fp = fingerprint(EXACT_TEXT)
    assert fp["word_count"] == 14
    assert fp["sentence_count"] == 2
    assert fp["sentence_len"] == {"median": 7.0, "mean": 7.0, "stdev": 2.0}
    assert fp["short_sentence_share"] == 0.5
    assert fp["long_sentence_share"] == 0.0
    assert fp["question_rate"] == 50.0  # per 100 sentences
    assert fp["exclamation_rate"] == 0.0
    assert fp["first_person_rate"] == 0.0


def test_fingerprint_first_person_ignores_nguoi_ta():
    # "người ta" is generic third person — its "ta" must not be counted.
    assert fingerprint(MONOTONE)["first_person_rate"] == 0.0
    assert fingerprint(CONV1)["first_person_rate"] > 0


# -- analyze_samples -------------------------------------------------------------

def test_analyze_samples_single_sample_warnings():
    out = analyze_samples([CONV1])
    warnings = " ".join(out["consistency"]["warnings"])
    assert "only 1 sample" in warnings
    assert "800" in warnings  # too-few-words warning
    assert out["sample_count"] == 1


def test_analyze_samples_divergence_mixed_authors():
    out = analyze_samples([CONV1, CONV2, LEGAL])
    warnings = " ".join(out["consistency"]["warnings"])
    assert "diverge" in warnings and "mixed authors" in warnings
    assert out["consistency"]["divergence_cv"]["sentence_median"] > 0.35


# -- build_profile -----------------------------------------------------------------

def test_build_profile_no_excerpts_by_default():
    profile = build_profile("Giọng Test", [CONV1, CONV2],
                            qualitative={"stance": "nói thẳng",
                                         "unknown_field_xyz": "should be ignored"})
    assert profile["exemplars"] == []
    assert "not stored" in profile["excerpts_note"]
    assert profile["qualitative"]["stance"] == "nói thẳng"
    assert "unknown_field_xyz" not in profile["qualitative"]  # unknown keys ignored
    assert profile["qualitative"]["never_do"] == []           # missing keys default empty
    assert profile["created_from"]["sample_count"] == 2
    assert profile["id"].startswith("giong-test-")


def test_build_profile_excerpts_when_allowed():
    sample = ("Đoạn một mở đầu bằng một cảnh nhỏ ở văn phòng vào buổi sáng sớm.\n\n"
              "Đoạn hai kể tiếp câu chuyện về cái phin và ấm nước trên bàn làm việc.")
    profile = build_profile("Giọng Test", [sample, CONV1, CONV2], store_excerpts=True)
    assert 1 <= len(profile["exemplars"]) <= 3
    assert all(len(e.split()) <= 120 for e in profile["exemplars"])


def test_default_profile_shipped():
    # Pinned to the shipped build (build_default_style_profile.py over
    # data/style_samples_mocbrew.json) — update these numbers if the
    # samples are ever edited.
    profile = load_style_profile("mocbrew-blog-voice")
    assert profile is not None
    assert profile["created_from"] == {"sample_count": 3, "total_words": 829}
    assert profile["fingerprint"]["sentence_len"]["median"] == 13.0
    assert profile["fingerprint"]["sentence_len"]["stdev"] == 8.72
    assert profile["consistency_warnings"] == []
    assert len(profile["exemplars"]) == 3  # our own texts -> excerpts stored
    assert profile["qualitative"]["never_do"]


# -- style_match ---------------------------------------------------------------------

def test_style_match_same_voice_scores_high():
    profile = load_style_profile("mocbrew-blog-voice")
    out = style_match(SAME_VOICE, profile)
    assert out["score"] >= 80
    assert out["advice"] == []


def test_style_match_monotone_flagged_on_rhythm():
    profile = load_style_profile("mocbrew-blog-voice")
    out = style_match(MONOTONE, profile)
    same = style_match(SAME_VOICE, profile)
    assert out["score"] < same["score"]
    stdev = next(m for m in out["metrics"] if m["metric"] == "sentence_stdev")
    assert stdev["within_range"] is False
    assert any("uniform" in a or "burstiness" in a for a in out["advice"])


# -- MCP layer -------------------------------------------------------------------------

def test_mcp_style_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_CORE_STYLE_PROFILES_DIR", str(tmp_path / "profiles"))
    saved = srv.save_style_profile(
        name="Giọng Test", samples=[CONV1, CONV2],
        qualitative={"stance": "nói thẳng", "never_do": ["không hứa sức khỏe"]})
    pid = saved["id"]
    assert pid and saved["exemplars"] == []

    got = srv.get_style_profile(pid)
    assert got["id"] == pid and got["qualitative"]["stance"] == "nói thẳng"

    listing = srv.get_style_profile("")
    assert pid in [p["id"] for p in listing["profiles"]]

    analysis = srv.analyze_style([CONV1])
    assert analysis["sample_count"] == 1 and analysis["fingerprint"]["word_count"] > 0

    match = srv.style_match(text=CONV1, profile_id=pid)
    assert match["profile_id"] == pid
    # 8 metrics since backlog item 5a (question_rate + exclamation_rate
    # joined the original 6).
    assert 0 <= match["score"] <= 100 and len(match["metrics"]) == 8

    missing = srv.get_style_profile("khong-ton-tai")
    assert "error" in missing


def test_assemble_context_with_style_profile_8_parts():
    brief = {"format": "blog", "topic": "cà phê văn phòng giá",
             "keyword": "cà phê", "title": "Cà phê văn phòng"}
    with_profile = srv.assemble_context(brief, style_profile_id="mocbrew-blog-voice")
    parts = [o["part"] for o in with_profile["ordered_context"]]
    assert parts == ["persona", "brand_rules", "craft_cards", "style_profile",
                     "facts_triggered", "exemplar", "task", "post_history_reminder"]
    assert with_profile["style_profile"]["id"] == "mocbrew-blog-voice"
    style_part = next(o for o in with_profile["ordered_context"] if o["part"] == "style_profile")
    # Backlog item 8: ordered_context carries metadata only (order/part/
    # chars) — the part's content lives in context_text, its single
    # representation.
    assert style_part["chars"] > 0
    assert "NEVER" in with_profile["context_text"] and "median" in with_profile["context_text"]

    without = srv.assemble_context(brief)
    assert [o["part"] for o in without["ordered_context"]] == [
        "persona", "brand_rules", "craft_cards", "facts_triggered",
        "exemplar", "task", "post_history_reminder"]
    assert without["style_profile"] is None


def test_final_gate_style_advisory_only_when_profile_attached():
    brief = {"id": "styled", "format": "blog", "topic": "cà phê văn phòng",
             "keyword": "cà phê", "title": "Cà phê văn phòng",
             "style_profile_id": "mocbrew-blog-voice"}
    draft_id = srv.start_draft(brief)["draft_id"]
    srv.save_section(draft_id, "s1", "Giá và lịch rang", CLEAN_TEXT)
    gate = srv.final_gate(draft_id)
    assert gate["passed"] is True  # style is advisory: it cannot block
    assert gate["style_match"]["profile_id"] == "mocbrew-blog-voice"
    assert gate["gate_history"][-1]["style_score"] == gate["style_match"]["score"]

    plain_id = srv.start_draft({"id": "plain", "format": "blog", "topic": "cà phê",
                                "keyword": "cà phê", "title": "Cà phê"})["draft_id"]
    srv.save_section(plain_id, "s1", "Giá và lịch rang", CLEAN_TEXT)
    gate2 = srv.final_gate(plain_id)
    assert "style_match" not in gate2
    assert "style_score" not in gate2["gate_history"][-1]


# -- fetch_sample HTML stripping (local fixture, no network) ----------------------------

FIXTURE_HTML = """<html><head><title>Bài mẫu thử</title><style>.x{color:red}</style>
<script>var secret = 1;</script></head>
<body><nav>Menu điều hướng</nav>
<article><h1>Tiêu đề bài</h1><p>Đoạn văn đầu tiên của bài mẫu này.</p>
<p>Đoạn thứ hai có thêm vài chữ nữa.</p></article>
<footer>Chân trang liên hệ</footer></body></html>"""


def test_fetch_sample_strip_html_fixture():
    title, text = strip_html(FIXTURE_HTML)
    assert title == "Bài mẫu thử"
    assert "Đoạn văn đầu tiên" in text and "Đoạn thứ hai" in text
    assert "Menu điều hướng" not in text       # nav dropped
    assert "Chân trang" not in text            # footer dropped
    assert "secret" not in text                # script dropped
    assert "color:red" not in text             # style dropped
