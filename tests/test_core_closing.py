"""Fail-first tests for the CORE COMPLETION round (22 Nov 2026).

Each test is built from a defect documented in evaluation/round-*.md and
left open by the Tier 1+2 round (see evaluation/SUMMARY.md post-fix
section): per-brand fact bases (Workstream A), the remaining fact-check
defects D5/D6/D8/D10/D11 (B), the last linter evasions + the sweep
residual (C), craft leftovers (D), style leftovers (E) and MCP draft
concurrency (F).
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from content_core.fact_check import check_section  # noqa: E402
from content_core.linter import lint_section  # noqa: E402
from content_core.loaders import load_brand, load_facts  # noqa: E402

FACTS = load_facts()
BRAND = load_brand()

PAD = ("Mộc Brew rang hạt vào thứ Ba hằng tuần cho khách quen. "
       "Mùi thơm nhẹ lan tỏa trong không gian quán nhỏ yên tĩnh. "
       "Bạn pha phin chậm rãi rồi ngồi ngắm vườn xanh mát. "
       "Giá một gói hai trăm năm mươi gam là 185.000đ rõ ràng. ")


def _types(text: str) -> set[str]:
    issues, _flagged = check_section(text, FACTS, BRAND)
    return {i["type"] for i in issues}


# ---------------------------------------------------------------------------
# Workstream C — the last 5 linter evasions (evaluation R2 LOW)
# ---------------------------------------------------------------------------

def _banned_types(text: str) -> set[str]:
    violations, _score = lint_section(PAD + text, "blog", BRAND, "s1")
    return {v["type"] for v in violations}


def test_linter_letter_spacing_evasion_caught():
    assert "banned_term" in _banned_types(
        "Trải nghiệm uống cà phê thật s e a m l e s s mỗi sáng.")


def test_linter_letter_dotting_evasion_caught():
    assert "banned_term" in _banned_types(
        "Bài này sẽ e.l.e.v.a.t.e trải nghiệm uống cà phê mỗi sáng.")


def test_linter_html_tag_split_evasion_caught():
    assert "banned_term" in _banned_types(
        "Đây là dòng hạt game-<b>changing</b> cho người mới uống.")


def test_linter_html_entity_evasion_caught():
    assert "banned_term" in _banned_types(
        "Đây là dòng hạt game&#45;changing cho người mới uống.")


def test_linter_inserted_word_in_phrase_caught():
    # banned term "tốt nhất Việt Nam" with one inserted word
    assert "banned_term" in _banned_types(
        "Ai cũng khen cà phê tốt nhất ở Việt Nam dạo này.")


def test_linter_space_split_single_term_caught():
    # "seam less" — a single-word term split by one space
    assert "banned_term" in _banned_types(
        "Trải nghiệm uống cà phê thật seam less mỗi sáng.")


def test_linter_fuzzy_matching_keeps_clean_text_clean():
    # The fuzzy passes must not invent hits on ordinary prose: similar
    # but distinct words, and a phrase with TWO inserted words (outside
    # the bounded 1-insertion rule), stay clean.
    assert "banned_term" not in _banned_types(
        "Mình rang hạt cẩn thận, đóng gói gọn gàng mỗi sáng.")
    assert "banned_term" not in _banned_types(
        "Cà phê tốt nhất là ở vùng Việt Nam xa xôi kia.")


# ---------------------------------------------------------------------------
# Workstream B — D5 roast-day clause scoping
# ---------------------------------------------------------------------------

def test_roast_negation_no_longer_bleeds_forward():
    # The affirmed second weekday is the false claim and must flag.
    assert "wrong_roast_day" in _types(
        "Mộc Brew không rang vào thứ Ba mà rang vào thứ Năm.")


def test_roast_other_activity_weekday_not_flagged():
    assert "wrong_roast_day" not in _types(
        "Thứ Sáu là ngày giao hàng; hạt rang vào thứ Ba.")
    assert "wrong_roast_day" not in _types(
        "Bài viết đăng vào thứ Năm, còn hạt rang thứ Ba.")


def test_roast_postposed_negation_not_flagged():
    assert "wrong_roast_day" not in _types(
        "Rang vào thứ Ba, không phải thứ Tư.")
    assert "wrong_roast_day" not in _types(
        "Vào thứ Tư, Mộc Brew không rang; ngày rang là thứ Ba.")


def test_roast_wrong_day_still_flagged():
    assert "wrong_roast_day" in _types("Mộc Brew rang vào thứ Tư hàng tuần.")
    assert "wrong_roast_day" in _types("Ngày rang là thứ Tư, đóng gói thứ Sáu.")


# ---------------------------------------------------------------------------
# Workstream B — D6 claim denial / attribution scope
# ---------------------------------------------------------------------------

def test_claim_bare_negation_is_a_denial():
    assert not ({"prohibited_claim", "unsupported_claim"}
                & _types("Cà phê không tốt nhất đâu, uống thử rồi tự cảm nhận."))
    assert not ({"prohibited_claim", "unsupported_claim"}
                & _types("Bài này không khẳng định hạt ngon nhất, chỉ ghi giá."))
    assert not ({"prohibited_claim", "unsupported_claim"}
                & _types("Mộc Brew chưa từng nhận là số 1."))


def test_claim_competitor_attribution_is_flagged_not_blocking():
    issues, flagged = check_section(
        "Đối thủ tự nhận là tốt nhất Việt Nam, Mộc Brew không bình luận.",
        FACTS, BRAND)
    assert not issues
    assert flagged  # recorded, not silently dropped


def test_claim_reported_question_is_flagged_not_blocking():
    issues, flagged = check_section(
        "Khách hỏi có phải ngon nhất không, mình trả lời là không.",
        FACTS, BRAND)
    assert not issues
    assert flagged


def test_claim_affirmations_still_block():
    assert "prohibited_claim" in _types("Không chỉ tốt nhất Việt Nam đâu.")
    assert "unsupported_claim" in _types(
        "Khách khen đây là hạt ngon nhất họ từng uống.")


# ---------------------------------------------------------------------------
# Workstream B — D11 claim paraphrases / insertions
# ---------------------------------------------------------------------------

def test_claim_inserted_word_caught():
    assert ({"prohibited_claim", "unsupported_claim"}
            & _types("Hạt này chữa được mất ngủ sau vài ngày."))


def test_claim_word_numeral_caught():
    assert ({"prohibited_claim", "unsupported_claim"}
            & _types("Cà phê ngon số một hiện nay."))


def test_claim_superlative_synonyms_caught():
    assert ({"prohibited_claim", "unsupported_claim"}
            & _types("Đứng đầu Việt Nam về cà phê rang mộc."))
    assert ({"prohibited_claim", "unsupported_claim"}
            & _types("Thần dược cho người mất ngủ."))
    assert ({"prohibited_claim", "unsupported_claim"}
            & _types("Cam kết hết mất ngủ sau một tuần."))


# ---------------------------------------------------------------------------
# Workstream B — D10 chained word-number statistics
# ---------------------------------------------------------------------------

def test_wordstat_chained_scales_caught():
    assert "unsupported_number" in _types("Hàng trăm nghìn khách hàng đã mua.")
    assert "unsupported_number" in _types("Hàng chục nghìn người dùng.")
    assert "unsupported_number" in _types("Cả nghìn khách hàng đang dùng.")
    assert "unsupported_number" in _types("Nghìn khách hàng tin tưởng.")


def test_wordstat_ordinary_prose_still_clean():
    assert "unsupported_number" not in _types("Một khách hàng hỏi giá gói 250g.")
    assert "unsupported_number" not in _types("Năm nay mình rang thêm nhiều mẻ mới.")


# ---------------------------------------------------------------------------
# Workstream B — D8 bare money in explicit price context
# ---------------------------------------------------------------------------

def test_bare_money_with_price_context_caught():
    assert "wrong_price" in _types("Một gói giá 200.000.")
    assert "wrong_price" in _types("Một gói giá 200.\n000đ.")


def test_bare_money_correct_price_and_stats_untouched():
    assert "wrong_price" not in _types("Một gói giá 185.000.")
    # "đánh giá" is not a price context, and a stat number is not money
    issues, _f = check_section("Khách đánh giá 200.000 khách hàng đã mua.",
                               FACTS, BRAND)
    assert "wrong_price" not in {i["type"] for i in issues}


# ---------------------------------------------------------------------------
# Workstream B — origin: cross-sentence pairing + non-listed origins
# ---------------------------------------------------------------------------

def test_origin_cross_sentence_pairing_caught():
    assert "wrong_origin" in _types(
        "Hạt Arabica của Mộc Brew. Hạt này được trồng ở Đắk Lắk.")


def test_origin_nonlisted_region_with_import_marker_caught():
    assert "unknown_origin" in _types(
        "Hạt Arabica nhập từ Ethiopia cho dòng giới hạn.")


def test_origin_comparisons_stay_clean():
    assert not ({"wrong_origin", "unknown_origin"} & _types(
        "Khác với hạt Bảo Lộc trên thị trường, Mộc Brew dùng Arabica Cầu Đất."))
    assert not ({"wrong_origin", "unknown_origin"} & _types(
        "Hạt của đối thủ trồng tại Bảo Lộc, còn hạt của mình là Arabica Cầu Đất."))


# ---------------------------------------------------------------------------
# Workstream D — craft leftovers (evaluation R3)
# ---------------------------------------------------------------------------

from content_core.craft import craft_check, get_exemplars  # noqa: E402


def test_get_exemplars_blog_resolves_to_blog_intro():
    blog = get_exemplars("blog")
    assert blog and blog[0]["format"] == "blog_intro"
    assert get_exemplars("blog_intro") == blog
    assert get_exemplars("product")[0]["format"] == "product"
    assert get_exemplars("khong-co") == []


def test_repeated_openings_two_token_key_no_ca_merging():
    # R3 §3.4 probe: three DIFFERENT subjects sharing the first syllable
    # "Cà" (coffee / tomato / aubergine) merged into one fake repetition.
    text = ("Cà phê phin cần nước nóng để bột nở đều trong phin nhôm. "
            "Cà chua chín đỏ nấu canh chua cá là món quen của bữa cơm nhà. "
            "Cà tím nướng mỡ hành ăn kèm cơm nóng cũng rất đưa cơm trưa. "
            "Ba loại cà khác nhau hoàn toàn nhưng đều có mặt trong bếp Việt.")
    result = craft_check(text, "social")
    assert "repeated_openings" not in {v["type"] for v in result["violations"]}


def test_repeated_openings_genuine_repetition_still_fires():
    text = ("Pha phin cần nước nóng vừa đủ trăm độ. "
            "Pha phin cần bột xay mịn đều tay. "
            "Pha phin cần thời gian chờ đủ lâu. "
            "Pha xong bạn rót ra ly và uống chậm rãi.")
    result = craft_check(text, "social")
    assert "repeated_openings" in {v["type"] for v in result["violations"]}


def test_rhythm_not_repaired_by_weak_word_sentence():
    # Sweep residual mechanism (R5): four uniform sentences (monotone)
    # plus one long sentence carrying a weak word. The violating
    # sentence must not "fix" the rhythm statistic it violates.
    base = ("Mùi thơm nhẹ lan tỏa trong không gian quán nhỏ. "
            "Ly nước ấm vị dịu hậu rõ từng ngụm một. "
            "Bàn gỗ cửa sổ mở vườn xanh lá cây mát. "
            "Khách tới ngồi xuống chiếc ghế bên cạnh mình. ")
    injected = base + ("Ngụm bàn gỗ cửa sổ mở vườn xanh lá cây gió mát trời "
                       "khách tới ngồi xuống chiếc ghế bên cạnh mùi hương "
                       "thơm nhẹ lan tỏa tuyệt vời.")
    assert "monotone_rhythm" in {v["type"] for v in craft_check(injected, "blog")["violations"]}


def test_rhythm_not_repaired_by_banned_sentence_when_terms_supplied():
    base = ("Mùi thơm nhẹ lan tỏa trong không gian quán nhỏ. "
            "Ly nước ấm vị dịu hậu rõ từng ngụm một. "
            "Bàn gỗ cửa sổ mở vườn xanh lá cây mát. "
            "Khách tới ngồi xuống chiếc ghế bên cạnh mình. ")
    injected = base + ("Ngụm bàn gỗ cửa sổ mở vườn xanh lá cây gió mát trời "
                       "khách tới ngồi xuống chiếc ghế bên cạnh mùi hương "
                       "thơm nhẹ lan tỏa trong không gian quán.")
    # The appended sentence is long and clean on its own; supplied with
    # a banned term it carries, it is neutralised in the rhythm stats.
    text = injected.replace("không gian quán.", "không gian quán delve.")
    result = craft_check(text, "blog", banned_terms=["delve"])
    assert "monotone_rhythm" in {v["type"] for v in result["violations"]}


# ---------------------------------------------------------------------------
# Workstream E — style leftovers (evaluation R4)
# ---------------------------------------------------------------------------

from content_core.style import (  # noqa: E402
    ALGORITHM_VERSION, build_profile, fingerprint, refresh_profile,
)

SAMPLE_A = (
    "Mình rang hạt vào sáng thứ Ba mỗi tuần cho mẻ mới. "
    "Mình ghi ngày rang lên mép túi để bạn dễ nhớ. "
    "Hạt để nguội hẳn rồi mình mới đóng gói gửi đi. "
    "Bạn pha phin chậm rãi và uống khi còn ấm nhé."
)
SAMPLE_B = (
    "Xưởng rang hạt vào sáng thứ Ba mỗi tuần cho mẻ mới. "
    "Ngày rang được ghi rõ lên mép túi giấy kraft. "
    "Hạt để nguội hẳn rồi mới đóng gói gửi đi xa. "
    "Khách pha phin chậm rãi và uống khi còn ấm."
)


def test_built_profiles_carry_algorithm_stamp_and_detail():
    profile = build_profile("Stamp Test", [SAMPLE_A, SAMPLE_B])
    assert profile["algorithm_version"] == ALGORITHM_VERSION
    detail = profile["consistency_detail"]
    assert len(detail["per_sample"]) == 2
    assert "first_person_rate" in detail["per_sample"][0]


def test_refresh_drops_unverifiable_stale_divergence_warning():
    stale = {
        "id": "x", "name": "Stale",
        "consistency_warnings": [
            "samples diverge on first_person_rate (CV 2.0) — "
            "possibly mixed authors"],
        # no algorithm_version, no consistency_detail: a pre-stamp profile
    }
    refreshed = refresh_profile(stale)
    assert refreshed["algorithm_version"] == ALGORITHM_VERSION
    assert not any(w.startswith("samples diverge on")
                   for w in refreshed["consistency_warnings"])
    assert any("could not be re-verified" in w
               for w in refreshed["consistency_warnings"])


def test_refresh_recomputes_divergence_from_stored_metrics():
    profile = build_profile("Recompute", [SAMPLE_A, SAMPLE_B])
    profile["algorithm_version"] = 1  # pretend an older algorithm
    profile["consistency_warnings"] = [
        "samples diverge on first_person_rate (CV 9.9) — bogus"]
    refreshed = refresh_profile(profile)
    assert not any("CV 9.9" in w for w in refreshed["consistency_warnings"])
    assert refreshed["algorithm_version"] == ALGORITHM_VERSION


def test_fingerprint_ignores_table_rows_in_sentence_stats():
    text = ("Câu đầu tiên có đủ năm từ nhé. "
            "Câu thứ hai cũng đủ năm từ luôn.\n"
            "| 250g | 89.000đ | thêm | nhiều | ô | chữ | vào | bảng |\n"
            "| 500g | 179.000đ | thêm | nhiều | ô | chữ | vào | bảng |")
    fp = fingerprint(text)
    assert fp["sentence_count"] == 2


def test_fingerprint_ttr_uses_capped_window():
    tokens = ["cà"] * 250 + [f"w{i}" for i in range(250)] + [f"u{i}" for i in range(200)]
    sentences = [" ".join(tokens[i:i + 10]) + "." for i in range(0, len(tokens), 10)]
    fp = fingerprint(" ".join(sentences))
    # First 500 tokens only: 251 unique / 500 = 0.5 (uncapped: 451/700).
    assert fp["type_token_ratio"] == 0.5


# ---------------------------------------------------------------------------
# Workstream A — per-brand fact base (the calibration's structural defect)
# ---------------------------------------------------------------------------

import json  # noqa: E402

import pytest  # noqa: E402

from content_core.fact_check import check_draft  # noqa: E402
from content_core.graph import Core  # noqa: E402
from content_core.loaders import (  # noqa: E402
    load_brand, load_facts, save_facts, validate_facts,
)

ROOT = Path(__file__).resolve().parents[1]
CNM_BRAND = load_brand(ROOT / "calibration" / "caonguyenmoc-brand.yaml")
CNM_TRIAL = (ROOT / "calibration" / "work" / "writer_trial.txt").read_text(
    encoding="utf-8")


def test_load_facts_default_is_mocbrew_unchanged():
    facts = load_facts()
    assert facts["brand_id"] == "moc-brew"
    assert facts["facts"]["price_vnd_per_250g"] == 185000


def test_load_facts_unknown_brand_raises_never_falls_back():
    with pytest.raises(ValueError, match="no-such-brand"):
        load_facts("no-such-brand")


def test_shipped_cnm_facts_profile_resolves():
    facts = load_facts("cao-nguyen-moc")
    assert facts["facts"]["price_vnd_per_250g"] == 89000
    assert 359000 in facts["facts"]["known_prices_vnd"]
    # The calibration never verified delivery or roast day for CNM:
    # absent, so those checks skip rather than borrow Mộc Brew's.
    assert "delivery_days" not in facts["facts"]
    assert "roast_weekday" not in facts["facts"]


def test_facts_save_load_roundtrip_and_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_CORE_FACTS_PROFILES_DIR", str(tmp_path))
    doc = {"brand_id": "brand-x", "simulated": True,
           "facts": {"price_vnd_per_250g": 100000, "price_display": "100.000đ",
                     "pack_weight_g": 250, "origins": ["Robusta Test"]},
           "entries": []}
    save_facts(doc)
    assert load_facts("brand-x")["facts"]["price_vnd_per_250g"] == 100000
    for bad in (
            {"brand_id": "b", "facts": {"pack_weight_g": 250, "origins": ["X"]}},
            {"brand_id": "b", "facts": {"price_vnd_per_250g": -5,
                                        "pack_weight_g": 250, "origins": ["X"]}},
            {"brand_id": "b", "facts": {"price_vnd_per_250g": 5,
                                        "pack_weight_g": 250, "origins": []}},
            {"brand_id": "b", "facts": {"price_vnd_per_250g": 5,
                                        "pack_weight_g": 0, "origins": ["X"]}},
            {"facts": {"price_vnd_per_250g": 5, "pack_weight_g": 250,
                       "origins": ["X"]}}):
        with pytest.raises(ValueError):
            validate_facts(bad)


def test_cnm_writer_trial_passes_fact_layer_under_cnm_facts():
    # THE calibration failure case: CNM's true 89.000đ price, written in
    # CNM's voice, failed the gate under Mộc Brew's fact base.
    sections = [{"id": "s1", "text": CNM_TRIAL}]
    issues, _, _ = check_draft(sections, load_facts("cao-nguyen-moc"), CNM_BRAND)
    assert issues == []


def test_cnm_writer_trial_still_fails_under_mocbrew_facts():
    sections = [{"id": "s1", "text": CNM_TRIAL}]
    issues, _, _ = check_draft(sections, load_facts(), CNM_BRAND)
    assert "wrong_price" in {i["type"] for i in issues}


def test_cnm_story_post_flags_less_under_own_facts_but_still_flags():
    # CNM's own story post contradicts its shop on cashew prices (blog
    # 94/169/339k vs shop 99/199/379k — calibration report). Under CNM
    # facts those stale prices legitimately STILL flag; the Mộc Brew
    # fact base flags far more (every true CNM price included).
    pages = json.loads((ROOT / "calibration" / "work" / "pages_setB.json")
                       .read_text(encoding="utf-8"))
    pages = pages["pages"] if isinstance(pages, dict) else pages
    story = next(p["text"] for p in pages if "la-ai" in p.get("url", ""))
    sections = [{"id": "s1", "text": story}]
    own, _, _ = check_draft(sections, load_facts("cao-nguyen-moc"), CNM_BRAND)
    foreign, _, _ = check_draft(sections, load_facts(), CNM_BRAND)
    assert 0 < len(own) < len(foreign)
    assert "wrong_price" in {i["type"] for i in own}


def test_core_resolves_facts_for_its_brand():
    core = Core(brand=CNM_BRAND)
    assert core.facts["brand_id"] == "cao-nguyen-moc"
    assert Core().facts["brand_id"] == "moc-brew"


def test_mcp_facts_tools_and_brand_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_CORE_FACTS_PROFILES_DIR", str(tmp_path / "facts"))
    monkeypatch.setenv("CONTENT_CORE_BRAND_DRAFTS_DIR", str(tmp_path / "brands"))
    monkeypatch.setenv("CONTENT_CORE_DRAFTS_DIR", str(tmp_path / "drafts"))
    import mcp_server as srv

    # Default brand behaviour is unchanged.
    assert srv.get_facts()["brand_id"] == "moc-brew"
    # Unknown brand: structured error everywhere, never a fallback.
    assert "error" in srv.get_facts("no-such-brand")
    assert "error" in srv.fact_check(text="Giá 1.000đ.", brand_id="no-such-brand")
    # Save + resolve a second brand's fact base end to end.
    doc = {"facts": {"price_vnd_per_250g": 89000, "price_display": "89.000đ",
                     "pack_weight_g": 250, "origins": ["Robusta Đức Cơ"]},
           "entries": []}
    assert srv.save_facts("cao-nguyen-moc", doc)["brand_id"] == "cao-nguyen-moc"
    assert srv.get_facts("cao-nguyen-moc")["facts"]["price_vnd_per_250g"] == 89000
    # Facts alone are not enough to CHECK under a brand: the brand
    # rules (its stored Brand Builder draft) are required too — no
    # silent mixing with the server brand's rules.
    assert "error" in srv.fact_check(text="Giá 89.000đ.", brand_id="cao-nguyen-moc")
    # An invalid save is rejected and writes nothing over the good doc.
    assert "error" in srv.save_facts("cao-nguyen-moc", {"facts": {"origins": []}})
    assert srv.get_facts("cao-nguyen-moc")["facts"]["price_vnd_per_250g"] == 89000


# ---------------------------------------------------------------------------
# Workstream F — MCP draft concurrency (evaluation R7 quantified data loss)
# ---------------------------------------------------------------------------

import os  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402

_WORKER = """
import sys
sys.path.insert(0, {root!r})
import mcp_server as srv
draft_id, mode, prefix, count = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
if mode == "sections":
    for i in range(count):
        out = srv.save_section(draft_id, f"{{prefix}}-{{i}}", "H",
                               "Hạt rang mùi thơm hậu ngọt rõ ràng cho bạn.")
        assert "error" not in out, out
else:
    for _ in range(count):
        out = srv.final_gate(draft_id)
        assert "error" not in out, out
"""


def _run_workers(tmp_path, draft_id, jobs):
    env = {**os.environ, "CONTENT_CORE_DRAFTS_DIR": str(tmp_path)}
    procs = [subprocess.Popen(
        [sys.executable, "-c", _WORKER.format(root=str(ROOT)), draft_id, mode, prefix, str(count)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for mode, prefix, count in jobs]
    for p in procs:
        _, err = p.communicate(timeout=180)
        assert p.returncode == 0, err


def test_concurrent_save_section_loses_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_CORE_DRAFTS_DIR", str(tmp_path))
    import mcp_server as srv
    draft_id = srv.start_draft({"topic": "t", "format": "blog"})["draft_id"]
    _run_workers(tmp_path, draft_id,
                 [("sections", "a", 15), ("sections", "b", 15)])
    draft = srv.get_draft(draft_id)
    assert len(draft["sections"]) == 30


def test_concurrent_final_gates_both_record_history(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_CORE_DRAFTS_DIR", str(tmp_path))
    import mcp_server as srv
    draft_id = srv.start_draft({"topic": "t", "format": "blog"})["draft_id"]
    srv.save_section(draft_id, "s1", "H",
                     "Mộc Brew rang hạt vào thứ Ba hằng tuần cho bạn. "
                     "Một gói 250g có giá 185.000đ rõ ràng minh bạch.")
    _run_workers(tmp_path, draft_id, [("gate", "x", 1), ("gate", "y", 1)])
    draft = srv.get_draft(draft_id)
    assert len(draft["gate_history"]) == 2


def test_stale_lock_is_stolen_and_fresh_lock_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_CORE_DRAFTS_DIR", str(tmp_path))
    import mcp_server as srv
    draft_id = srv.start_draft({"topic": "t", "format": "blog"})["draft_id"]
    lock_path = tmp_path / f"{draft_id}.lock"
    # A lock left by a crashed writer (mtime 120s ago) must not wedge
    # the draft: it is stolen and the save proceeds.
    lock_path.write_text("stale", encoding="utf-8")
    old = time.time() - 120
    os.utime(lock_path, (old, old))
    out = srv.save_section(draft_id, "s1", "H", "Hạt rang mùi thơm.")
    assert "error" not in out
    assert not lock_path.exists()  # released after the save
    # A FRESH lock held by a live writer: the save waits, then surfaces
    # a structured error (timeout shrunk for the test).
    monkeypatch.setattr(srv, "_DRAFT_LOCK_TIMEOUT_S", 0.3)
    lock_path.write_text("live", encoding="utf-8")
    out = srv.save_section(draft_id, "s2", "H", "Hạt rang mùi thơm.")
    assert "error" in out and "locked" in out["error"]
    lock_path.unlink()
