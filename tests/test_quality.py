"""Quality Profile (quality.py) + the unified gate (gates.py).

Covers: preset weights, profile validation, the documented axis-score
mappings, renormalisation when judged axes are missing, the
anti-compensation floor rule, hard-gate dominance over the quality
layer, MCP↔graph gate parity, and the 3 quality MCP tools in-process.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import mcp_server as srv  # noqa: E402
from content_core import quality  # noqa: E402
from content_core.gates import evaluate_gate  # noqa: E402
from content_core.graph import Core  # noqa: E402
from content_core.loaders import load_brand, load_facts  # noqa: E402
from content_core.quality import (AXIS_KEYS, FLOORS, JUDGED_AXES, PRESETS,
                                  evaluate_quality, get_profile, make_profile,
                                  preset_profile, validate_profile)

BRAND = load_brand()
FACTS = load_facts()

CLEAN_TEXT = (
    "Mộc Brew rang cà phê vào thứ Ba hàng tuần cho bạn. "
    "Một gói 250g có giá 185.000đ trước khi nói tới hương vị. "
    "Hạt Arabica Cầu Đất cho vị chua nhẹ và hậu ngọt rõ. "
    "Mình ghi rõ ngày rang trên từng gói gửi tới tay bạn."
)
CLEAN_BRIEF = {"id": "q", "format": "blog", "topic": "cà phê văn phòng",
               "keyword": "cà phê", "title": "Cà phê văn phòng"}
WRONG_PRICE_TEXT = "Một gói 250g có giá 200.000đ trước khi nói tới hương vị hôm nay."
FLAGGED_TEXT = "Nhiều người nói hạt này luôn ngon hơn hạt khác [NEEDS SOURCE]."
BAD_TEXT = (
    "Mộc Brew mang đến trải nghiệm cà phê đẳng cấp thế giới cho mọi khách hàng yêu cà phê! "
    "Sản phẩm được curated kỹ lưỡng để elevate buổi sáng và leverage năng lượng của bạn!"
)
# Brand-clean, fact-clean, craft-poor (same fixture as test_mcp_server).
MEDIOCRE_TEXT = (
    "Mình thấy cà phê này ngon và thơm ngon trong mỗi buổi sáng. "
    "Mình nghĩ hương vị tuyệt vời và hoàn hảo cho mọi người dùng thử. "
    "Mình bảo cà phê đặc biệt và chất lượng cao trong từng gói nhỏ. "
    "Mình mong bạn thấy cà phê cao cấp và sang trọng mỗi ngày mới."
)


def _sections(text: str, heading: str = "Giá và lịch rang") -> list[dict]:
    return [{"id": "s1", "heading": heading, "text": text}]


def _axis(report: dict, key: str) -> dict:
    return next(a for a in report["axes"] if a["axis"] == key)


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_CORE_DRAFTS_DIR", str(tmp_path / "drafts"))
    monkeypatch.setenv("CONTENT_CORE_QUALITY_PROFILES_DIR",
                       str(tmp_path / "quality_profiles"))


# -- presets & validation ----------------------------------------------------

def test_presets_sum_to_100_and_match_spec():
    for fmt, weights in PRESETS.items():
        assert set(weights) == set(AXIS_KEYS)
        assert sum(weights.values()) == 100, fmt
    assert PRESETS["blog"] == {"facts": 18, "voice": 14, "clarity": 14,
                               "coherence": 14, "seo": 22, "persuasion": 8,
                               "depth": 10}
    assert PRESETS["comparison"]["facts"] == 30
    assert FLOORS == {"facts": 70, "voice": 60, "clarity": 60, "coherence": 60,
                      "seo": 55, "persuasion": 55, "depth": 60}
    assert quality.PASS_TOTAL == 70


def test_preset_profile_shape():
    p = preset_profile("blog")
    assert p["id"] == "preset-blog" and p["format"] == "blog"
    assert p["floors"] == FLOORS and p["pass_total"] == 70


def test_profile_validation_rejects_bad_profiles():
    base = {"format": "blog", "weights": dict(PRESETS["blog"])}
    bad_sum = {"format": "blog", "weights": {**PRESETS["blog"], "seo": 21}}
    with pytest.raises(ValueError, match="sum to exactly 100"):
        validate_profile(bad_sum)
    missing = {"format": "blog",
               "weights": {k: v for k, v in PRESETS["blog"].items() if k != "seo"}}
    with pytest.raises(ValueError, match="exactly the 7 axes"):
        validate_profile(missing)
    too_big = {"format": "blog", "weights": {**PRESETS["blog"], "seo": 46,
                                             "facts": 2, "voice": 8}}
    with pytest.raises(ValueError, match=r"\[2, 45\]"):
        validate_profile(too_big)
    with pytest.raises(ValueError, match="unknown format"):
        validate_profile({"format": "tiktok", "weights": dict(PRESETS["blog"])})
    with pytest.raises(ValueError, match=r"\[0, 90\]"):
        validate_profile({**base, "floors": {"facts": 95}})
    ok = validate_profile({**base, "floors": {"seo": 40}})
    assert ok["floors"]["seo"] == 40 and ok["floors"]["facts"] == 70


def test_get_profile_resolution():
    assert get_profile("blog")["id"] == "preset-blog"
    assert get_profile("preset-email")["format"] == "email"
    with pytest.raises(ValueError, match="unknown quality profile"):
        get_profile("no-such-profile")


# -- axis scoring mappings -----------------------------------------------------

def test_facts_axis_mapping_documented_penalties():
    profile = preset_profile("blog")
    clean = evaluate_quality(_sections(CLEAN_TEXT), "blog", profile, None,
                             BRAND, FACTS, brief=CLEAN_BRIEF)
    assert _axis(clean, "facts")["score"] == 100

    wrong = evaluate_quality(_sections(WRONG_PRICE_TEXT), "blog", profile, None,
                             BRAND, FACTS, brief=CLEAN_BRIEF)
    facts_axis = _axis(wrong, "facts")
    assert facts_axis["score"] == 70  # 100 - 30 x 1 unflagged issue
    assert facts_axis["detail"]["unflagged_issues"] == 1

    flagged = evaluate_quality(_sections(FLAGGED_TEXT), "blog", profile, None,
                               BRAND, FACTS, brief=CLEAN_BRIEF)
    facts_axis = _axis(flagged, "facts")
    assert facts_axis["score"] == 90  # 100 - 10 x 1 flagged [NEEDS SOURCE]
    assert facts_axis["detail"]["flagged_claims"] == 1


def test_judged_grade_conversion():
    report = evaluate_quality(_sections(CLEAN_TEXT), "blog", preset_profile("blog"),
                              {"coherence": 0, "persuasion": 1, "depth": 2},
                              BRAND, FACTS, brief=CLEAN_BRIEF)
    assert _axis(report, "coherence")["score"] == 20
    assert _axis(report, "persuasion")["score"] == 60
    assert _axis(report, "depth")["score"] == 95
    assert all(_axis(report, a)["source"] == "judged" for a in JUDGED_AXES)
    with pytest.raises(ValueError, match="0, 1 or 2"):
        evaluate_quality(_sections(CLEAN_TEXT), "blog", preset_profile("blog"),
                         {"depth": 3}, BRAND, FACTS)


def test_renormalisation_when_judged_missing():
    report = evaluate_quality(_sections(CLEAN_TEXT), "blog", preset_profile("blog"),
                              None, BRAND, FACTS, brief=CLEAN_BRIEF)
    assert set(report["missing_axes"]) == set(JUDGED_AXES)
    assert set(report["judged_axes"]) == set()
    assert set(report["measured_axes"]) == {"facts", "voice", "clarity", "seo"}
    present = [a for a in report["axes"] if a["score"] is not None]
    expected = (sum(a["score"] * a["weight"] for a in present)
                / sum(a["weight"] for a in present))
    assert report["weighted_total"] == round(expected, 2)
    assert _axis(report, "depth")["score"] is None
    assert _axis(report, "depth")["below_floor"] is False


def test_anti_compensation_depth_floor_fails_despite_high_total():
    # Every measured axis is strong and the other judged axes are top
    # grade, but depth is graded 0 -> score 20 < floor 60. The weighted
    # total clears 70, yet the quality layer MUST fail (floors cannot be
    # compensated away).
    report = evaluate_quality(_sections(CLEAN_TEXT), "blog", preset_profile("blog"),
                              {"coherence": 2, "persuasion": 2, "depth": 0},
                              BRAND, FACTS, brief=CLEAN_BRIEF)
    assert report["weighted_total"] >= 70
    assert _axis(report, "depth")["score"] == 20
    assert report["floors_failed"] == ["depth"]
    assert report["quality_pass"] is False


# -- the unified gate ------------------------------------------------------------

def test_hard_gate_dominates_quality():
    # Banned terms + top judged grades + a quality profile: the quality
    # layer can never rescue a hard-gate failure.
    verdict = evaluate_gate(_sections(BAD_TEXT), "blog", BRAND, FACTS,
                            quality_profile=preset_profile("blog"),
                            judged={"coherence": 2, "persuasion": 2, "depth": 2},
                            brief=CLEAN_BRIEF)
    assert verdict["hard"]["passed"] is False
    assert verdict["hard"]["banned_terms_found"]
    assert verdict["passed"] is False


def test_gate_quality_layer_inactive_by_default():
    verdict = evaluate_gate(_sections(CLEAN_TEXT), "blog", BRAND, FACTS)
    assert verdict["quality"] is None
    assert verdict["passed"] is True


def _graph_gate_passed(sections: list[dict], fmt: str) -> bool:
    core = Core()
    state = {"brief": {"id": "parity", "format": fmt, "topic": "cà phê"},
             "sections": sections, "rewrite_count": 0, "gate_history": []}
    out = core.final_gate(state)  # type: ignore[arg-type]
    return out["status"] == "approved"


def _mcp_gate_passed(sections: list[dict], fmt: str) -> bool:
    draft_id = srv.start_draft({"id": "parity", "format": fmt,
                                "topic": "cà phê"})["draft_id"]
    for sec in sections:
        srv.save_section(draft_id, sec["id"], sec.get("heading", ""), sec["text"])
    return srv.final_gate(draft_id)["passed"]


@pytest.mark.parametrize("text,fmt,expected", [
    (CLEAN_TEXT, "blog", True),        # clean draft passes both paths
    (MEDIOCRE_TEXT, "blog", False),    # craft < 70 blocks BOTH paths now
    (BAD_TEXT, "blog", False),         # banned terms fail both paths
    (MEDIOCRE_TEXT, "email", True),    # craft advisory for email in both
])
def test_gate_parity_mcp_vs_graph(text, fmt, expected):
    sections = _sections(text)
    assert _mcp_gate_passed(sections, fmt) is expected
    assert _graph_gate_passed(sections, fmt) is expected


# -- the quality MCP tools (in-process) -------------------------------------------

def test_quality_tools_roundtrip():
    preset = srv.get_quality_profile("blog")
    assert preset["id"] == "preset-blog"
    assert sum(preset["weights"].values()) == 100
    assert srv.get_quality_profile("nope")["error"]

    custom_weights = {**PRESETS["blog"], "seo": 20, "persuasion": 10}
    saved = srv.save_quality_profile("blog", custom_weights)
    assert saved["id"].startswith("custom-blog-")
    assert srv.get_quality_profile(saved["id"])["weights"] == custom_weights
    bad = srv.save_quality_profile("blog", {**PRESETS["blog"], "seo": 21})
    assert "error" in bad  # sum 99 -> rejected

    draft_id = srv.start_draft(CLEAN_BRIEF)["draft_id"]
    srv.save_section(draft_id, "s1", "Giá và lịch rang", CLEAN_TEXT)
    report = srv.quality_score(draft_id, judged_scores={"depth": 2})
    assert report["draft_id"] == draft_id
    assert _axis(report, "depth")["score"] == 95
    assert set(report["missing_axes"]) == {"coherence", "persuasion"}
    assert report["profile_id"] == "preset-blog"


def test_final_gate_quality_layer_opt_in():
    # Legacy call: no quality key at all, verdict unchanged.
    draft_id = srv.start_draft(CLEAN_BRIEF)["draft_id"]
    srv.save_section(draft_id, "s1", "Giá và lịch rang", CLEAN_TEXT)
    legacy = srv.final_gate(draft_id)
    assert legacy["passed"] is True and "quality" not in legacy
    assert "quality" not in legacy["gate_history"][-1]

    # Quality call on the same clean draft with depth graded 0: the gate
    # now FAILS on the depth floor even though hard+craft pass.
    gated = srv.final_gate(draft_id, quality_profile="blog",
                           judged_scores={"coherence": 2, "persuasion": 2,
                                          "depth": 0})
    assert gated["passed"] is False
    assert gated["quality"]["floors_failed"] == ["depth"]
    assert gated["quality"]["weighted_total"] >= 70
    assert gated["gate_history"][-1]["quality"]["quality_pass"] is False
    assert "Quality layer FAILED" in gated["instruction"]

    # Judged scores alone activate the layer via the format preset.
    draft2 = srv.start_draft(CLEAN_BRIEF)["draft_id"]
    srv.save_section(draft2, "s1", "Giá và lịch rang", CLEAN_TEXT)
    ok = srv.final_gate(draft2, judged_scores={"coherence": 2, "persuasion": 2,
                                               "depth": 2})
    assert ok["passed"] is True and ok["quality"]["quality_pass"] is True

    # Unknown profile id is a clean error, not a crash.
    err = srv.final_gate(draft2, quality_profile="no-such-id")
    assert "error" in err
