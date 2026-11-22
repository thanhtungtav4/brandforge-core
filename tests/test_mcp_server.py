"""Tests for the MCP layer (mcp_server.py).

Two levels:
- in-process: call the tool functions directly (FastMCP's @tool decorator
  returns the original function, so they stay plain callables);
- over the wire: spawn the server over stdio with the official MCP client
  and do initialize + tools/list + key calls.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import mcp_server as srv  # noqa: E402

EXPECTED_TOOLS = {"get_persona", "get_brand", "research_facts", "assemble_context",
                  "validate_brief", "make_outline", "start_draft", "save_section",
                  "get_draft", "brand_check", "fact_check", "seo_check",
                  "final_gate", "run_golden",
                  "get_craft_cards", "get_exemplars", "get_domain_glossary", "craft_check",
                  "analyze_style", "save_style_profile", "get_style_profile", "style_match",
                  "analyze_site_brand", "save_brand_draft", "get_brand_draft",
                  "compare_brand_voice",
                  "get_quality_profile", "save_quality_profile", "quality_score",
                  "check_draft_all",
                  "get_facts", "save_facts",
                  "plan_images", "set_image", "image_check",
                  "record_vision_check", "get_image_manifest",
                  "add_library_image", "list_library_images",
                  "fulfil_images_from_library",
                  "list_drafts", "delete_draft",
                  "create_research_pack", "add_pack_source",
                  "build_pack_proposals", "propose_pack_item", "get_pack",
                  "decide_pack_item", "resolve_contradiction", "apply_pack",
                  "register_corpus", "list_corpora", "get_corpus",
                  "set_corpus_choice", "get_corpus_choice",
                  "clear_corpus_choice",
                  "import_taste_articles", "mark_article_verdict",
                  "build_taste_profile", "get_taste_status",
                  "set_wordpress_target", "get_wordpress_target",
                  "export_draft_to_wordpress", "sync_wordpress_edits",
                  "score_section_variants",
                  "export_core_data", "list_backups"}

CLEAN_TEXT = (
    "Mộc Brew rang cà phê vào thứ Ba hàng tuần cho bạn. "
    "Một gói 250g có giá 185.000đ trước khi nói tới hương vị. "
    "Hạt Arabica Cầu Đất cho vị chua nhẹ và hậu ngọt rõ. "
    "Mình ghi rõ ngày rang trên từng gói gửi tới tay bạn."
)
BAD_TEXT = (
    "Mộc Brew mang đến trải nghiệm cà phê đẳng cấp thế giới cho mọi khách hàng yêu cà phê! "
    "Sản phẩm được curated kỹ lưỡng để elevate buổi sáng và leverage năng lượng của bạn!"
)


@pytest.fixture(autouse=True)
def _isolated_drafts(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_CORE_DRAFTS_DIR", str(tmp_path / "drafts"))


# -- tool list completeness -------------------------------------------------

def test_tool_list_complete_in_process():
    assert set(srv.TOOL_NAMES) == EXPECTED_TOOLS
    for name in EXPECTED_TOOLS:
        assert callable(getattr(srv, name)), name


def test_tool_list_complete_over_stdio():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def _run():
        params = StdioServerParameters(command=sys.executable,
                                       args=[str(PROJECT_DIR / "mcp_server.py")],
                                       cwd=str(PROJECT_DIR))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                return {t.name for t in tools.tools}

    # Exact equality (review M19): the old subset assertion let the
    # wire list grow tools the in-process list did not know about.
    assert EXPECTED_TOOLS == asyncio.run(_run())


# -- data / research / validation -------------------------------------------

def test_get_persona_and_brand():
    assert srv.get_persona()["data"]["name"] == "Linh"
    brand = srv.get_brand()
    assert brand["brand"]["id"] == "moc-brew"
    assert "delve" in brand["voice"]["banned_terms"]


def test_research_facts_keyword_triggered():
    out = srv.research_facts("giá gói 250g là bao nhiêu")
    ids = [s["id"] for s in out["sources"]]
    assert "price" in ids
    assert out["facts"]["price_display"] == "185.000đ"
    dormant = srv.research_facts("thời tiết hôm nay ở biển")
    assert dormant["sources"] == []


def test_validate_brief_refuses_health_claim():
    out = srv.validate_brief({"format": "blog", "topic": "cà phê và giấc ngủ",
                              "requested_claims": ["100% chữa mất ngủ"]})
    # Backlog item 7: a brief requesting prohibited claims is REFUSED —
    # valid=False with status "refused" (was valid=True before the
    # refusal-routing fix; see evaluation R7 F7 / R8 F8-7).
    assert out["valid"] is False
    assert out["status"] == "refused"
    assert "100% chữa mất ngủ" in out["refused_claims"]


def test_validate_brief_invalid_without_topic():
    out = srv.validate_brief({"format": "blog"})
    assert out["valid"] is False


def test_assemble_context_order():
    # Order updated for Layer 4: craft cards + exemplar inserted before facts/task.
    out = srv.assemble_context({"format": "blog", "topic": "cà phê văn phòng giá",
                                "keyword": "cà phê", "title": "Cà phê văn phòng"})
    parts = [o["part"] for o in out["ordered_context"]]
    assert parts == ["persona", "brand_rules", "craft_cards", "facts_triggered",
                     "exemplar", "task", "post_history_reminder"]
    assert "185.000đ" in out["context_text"]


# -- checks -------------------------------------------------------------------

def test_brand_check_catches_banned_words():
    out = srv.brand_check(text=BAD_TEXT, format="blog")
    assert out["score"] < 85
    assert {"elevate", "curated", "leverage"} <= set(out["banned_found"])


def test_fact_check_wrong_price_fails():
    out = srv.fact_check(text="Một gói 250g có giá 200.000đ hôm nay thôi bạn nhé.")
    assert out["ok"] is False
    assert out["issues"][0]["type"] == "wrong_price"


def test_fact_check_marked_claim_is_flagged_not_blocking():
    out = srv.fact_check(text="Nhiều người nói hạt này luôn ngon hơn hạt khác [NEEDS SOURCE].")
    assert out["ok"] is True
    assert out["flagged_claims"]


# -- draft lifecycle + final gate ---------------------------------------------

def _clean_draft() -> str:
    draft_id = srv.start_draft({"id": "pytest", "format": "blog", "topic": "cà phê văn phòng",
                                "keyword": "cà phê", "title": "Cà phê văn phòng"})["draft_id"]
    srv.make_outline({"format": "blog", "topic": "cà phê"},
                     proposed_outline=[{"heading": "Giá và lịch rang"}], draft_id=draft_id)
    srv.save_section(draft_id, "s1", "Giá và lịch rang", CLEAN_TEXT)
    return draft_id


def test_draft_lifecycle_and_final_gate_passes():
    draft_id = _clean_draft()
    draft = srv.get_draft(draft_id)
    assert draft["sections"][0]["text"] == CLEAN_TEXT
    assert draft["outline"][0]["heading"] == "Giá và lịch rang"
    assert Path(srv._draft_path(draft_id)).exists()
    gate = srv.final_gate(draft_id)
    assert gate["passed"] is True
    assert gate["status"] == "approved"
    assert gate["brand_score"] == 100.0
    # Layer 4 craft report rides along; blog is a craft-BLOCKING format (P2b)
    # and this clean draft's craft (85) clears the 70 threshold.
    assert gate["craft_blocking"] is True
    assert gate["craft_advisory"] is False
    assert "score" in gate["craft"] and "violations" in gate["craft"]
    assert gate["gate_history"][-1]["craft_score"] == gate["craft"]["score"]


def test_check_draft_all_matches_individual_tools():
    # Perf P3(ii): the combined check must report EXACTLY what the
    # individual tools report for the same draft (it only saves the
    # repeated checker runs, it must never change a number).
    draft_id = _clean_draft()
    combined = srv.check_draft_all(draft_id, judged_scores={"coherence": 2})
    assert combined["brand"] == srv.brand_check(draft_id=draft_id)
    assert combined["fact"] == srv.fact_check(draft_id=draft_id)
    assert combined["craft"] == srv.craft_check(draft_id=draft_id)
    assert combined["style"] is None  # brief carries no style_profile_id
    quality = srv.quality_score(draft_id, judged_scores={"coherence": 2})
    assert combined["quality"] == quality
    # No state change: the combined check is not a gate.
    draft = srv.get_draft(draft_id)
    assert not draft.get("gate_history")
    assert draft.get("status") in (None, "", "drafting")


def test_check_draft_all_unknown_draft_is_error():
    out = srv.check_draft_all("d-does-not-exist")
    assert "error" in out and out["draft_id"] == "d-does-not-exist"


# Brand-clean, fact-clean, but craft-poor: weak words, monotone rhythm,
# repeated openings, no sensory detail -> craft well under 70.
MEDIOCRE_TEXT = (
    "Mình thấy cà phê này ngon và thơm ngon trong mỗi buổi sáng. "
    "Mình nghĩ hương vị tuyệt vời và hoàn hảo cho mọi người dùng thử. "
    "Mình bảo cà phê đặc biệt và chất lượng cao trong từng gói nhỏ. "
    "Mình mong bạn thấy cà phê cao cấp và sang trọng mỗi ngày mới."
)


def test_final_gate_low_craft_blog_is_blocked_p2b():
    # P2b: for blog, craft < 70 BLOCKS the gate even when brand+fact are clean.
    draft_id = srv.start_draft({"id": "mediocre", "format": "blog", "topic": "cà phê văn phòng",
                                "keyword": "cà phê", "title": "Cà phê văn phòng"})["draft_id"]
    srv.save_section(draft_id, "s1", "Giới thiệu chung", MEDIOCRE_TEXT)
    gate = srv.final_gate(draft_id)
    assert gate["brand_score"] >= 85 and not gate["fact_issues"]  # brand+fact clean
    assert gate["craft"]["score"] < srv.CRAFT_PASS_SCORE == 70
    assert gate["craft_blocking"] is True and gate["craft_advisory"] is False
    assert gate["passed"] is False
    assert gate["status"] == "rewriting"
    assert gate["failing_sections"] == ["s1"]
    assert "Craft is BLOCKING" in gate["instruction"]
    assert gate["gate_history"][-1]["craft_blocking"] is True


def test_final_gate_low_craft_email_stays_advisory_p2b():
    # Same craft-poor text as EMAIL: craft is advisory there -> gate passes.
    draft_id = srv.start_draft({"id": "mediocre-email", "format": "email", "topic": "cà phê văn phòng",
                                "keyword": "cà phê", "title": "Cà phê văn phòng"})["draft_id"]
    srv.save_section(draft_id, "s1", "Nội dung email", MEDIOCRE_TEXT)
    gate = srv.final_gate(draft_id)
    assert gate["craft"]["score"] < 70  # same low craft score...
    assert gate["craft_blocking"] is False and gate["craft_advisory"] is True
    assert gate["passed"] is True       # ...but advisory craft does NOT block
    assert gate["status"] == "approved"


def test_final_gate_fails_bad_draft_and_tracks_rewrites():
    draft_id = srv.start_draft({"id": "bad", "format": "blog", "topic": "cà phê",
                                "keyword": "cà phê", "title": "x"})["draft_id"]
    srv.save_section(draft_id, "s1", "Mở đầu", BAD_TEXT)
    gate = srv.final_gate(draft_id)
    assert gate["passed"] is False
    assert gate["failing_sections"] == ["s1"]
    assert gate["status"] == "rewriting"
    assert gate["rewrites_remaining"] == 2
    # Rewrite the failing section clean -> one rewrite cycle counted, gate passes.
    srv.save_section(draft_id, "s1", "Mở đầu", CLEAN_TEXT)
    assert srv.get_draft(draft_id)["rewrite_count"] == 1
    gate2 = srv.final_gate(draft_id)
    assert gate2["passed"] is True
    assert len(gate2["gate_history"]) == 2


def test_seo_check_on_draft():
    draft_id = _clean_draft()
    seo = srv.seo_check(draft_id=draft_id)
    assert seo["headings_ok"] is True
    assert seo["keyword_in_title"] is True


def test_make_outline_deterministic_default():
    out = srv.make_outline({"format": "product", "topic": "mô tả gói cà phê"})
    assert out["ok"] is True and out["source"] == "deterministic_template"
    assert len(out["outline"]) == 3


def test_run_golden_via_tool():
    out = srv.run_golden()
    assert out["all_passed"] is True
    assert out["passed"] == out["total"] == 7
    assert json.dumps(out)  # fully JSON-serializable for MCP transport
