#!/usr/bin/env python3
"""MCP client test: spawns mcp_server.py over stdio (official MCP SDK client),
does initialize + tools/list, then exercises the key tools end-to-end.

Usage: .venv/bin/python test_mcp_client.py   (exit code non-zero on any FAIL)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

PROJECT_DIR = Path(__file__).resolve().parent

# Brand-compliant sentences (12-13 words each, facts correct) — the "model"
# (this script, standing in for Muse) writes these and saves them section by section.
CLEAN_S1 = (
    "Mộc Brew rang cà phê vào thứ Ba hàng tuần cho bạn. "
    "Một gói 250g có giá 185.000đ trước khi nói tới hương vị. "
    "Hạt Arabica Cầu Đất cho vị chua nhẹ và hậu ngọt rõ. "
    "Mình ghi rõ ngày rang trên từng gói gửi tới tay bạn."
)
CLEAN_S2 = (
    "Đơn hàng được giao trong 2-3 ngày sau khi bạn xác nhận. "
    "Bạn pha phin chậm rãi vào buổi sáng trước giờ làm việc. "
    "Văn phòng nhỏ chỉ cần phin và ấm nước là pha được. "
    "Bạn bảo quản hạt trong hộp kín và tránh ánh nắng trực tiếp."
)
BAD_TEXT = (
    "Mộc Brew mang đến trải nghiệm cà phê đẳng cấp thế giới cho mọi khách hàng yêu cà phê! "
    "Sản phẩm được curated kỹ lưỡng để elevate buổi sáng và leverage năng lượng của bạn!"
)
WRONG_PRICE_TEXT = "Một gói 250g có giá 200.000đ trước khi nói tới hương vị hôm nay."

# Brand Builder fixture pages (the page dicts fetch_site.py produces):
# a calm own-site (first-person) and a hype competitor.
BRAND_PAGE_SOM = {
    "url": "https://som.example/", "title": "Trang Chủ | Sớm Coffee",
    "text": ("Mình rang cà phê vào sáng thứ Ba hàng tuần cho bạn. "
             "Một gói 250g có giá ghi rõ ngay trên bao bì, bạn không cần hỏi lại mình. "
             "Mình chọn hạt Arabica từ Cầu Đất và Robusta từ Đắk Lắk cho bạn. "
             "Bạn pha phin vào buổi sáng, nghe mùi caramel ấm bốc lên rất chậm."),
    "headings": ["Cà phê rang mẻ nhỏ, gửi tới bạn mỗi tuần", "Sớm Arabica Cầu Đất"],
    "html_meta": {"og_site_name": "Sớm Coffee", "og_image": None,
                  "theme_color": "#3e6b48", "logo_url": "https://som.example/logo.png",
                  "fonts": [{"family": "Be Vietnam Pro", "count": 2}],
                  "colors": [{"hex": "#3e6b48", "count": 3}]},
}
BRAND_PAGE_DINHCAO = {
    "url": "https://dinhcao.example/", "title": "Trang Chủ | Đỉnh Cao Coffee",
    "text": ("Đỉnh Cao Coffee tự hào là thương hiệu cà phê tốt nhất Việt Nam! "
             "Đỉnh Cao Coffee mang đến trải nghiệm số 1 cho quý khách hàng! "
             "Hương vị ngon nhất Đông Nam Á trong từng tách cà phê! "
             "Giá ưu đãi nhất thị trường, đặt hàng ngay hôm nay!"),
    "headings": ["Cà Phê Đẳng Cấp Thế Giới", "Đỉnh Cao Gold Blend"],
    "html_meta": {"og_site_name": "Đỉnh Cao Coffee", "og_image": None,
                  "theme_color": "#c8102e", "logo_url": "https://dinhcao.example/logo.png",
                  "fonts": [{"family": "Montserrat", "count": 3}],
                  "colors": [{"hex": "#c8102e", "count": 3}]},
}
BRAND_QUAL = {"tone_traits": [{"dimension": "directness", "position": "nói thẳng",
                                "do": "nêu fact trước", "dont": "hype"}],
              "audience_guess": "dân văn phòng", "positioning": "rang mẻ nhỏ",
              "differentiators": ["ghi ngày rang"]}

# Style Extraction samples: two short conversational texts in one voice.
STYLE_SAMPLE_A = (
    "Sáng nay mình pha phin sớm hơn thường lệ. Bạn tin không, cả văn phòng tỉnh hẳn. "
    "Mình rót nước, chờ bọt nở, rồi nghe tiếng giọt rơi chậm rãi xuống cốc thủy tinh quen thuộc. "
    "Ngắn thôi. Nhưng đủ. Bạn thử một lần xem, mình cá là bạn sẽ bỏ cà phê hòa tan ngay tuần này."
)
STYLE_SAMPLE_B = (
    "Mình hay ghi ngày rang lên mép túi cho bạn nhớ. Vì hạt để lâu là mất mùi. "
    "Có hôm mình rang hỏng cả một mẻ chỉ vì mải nghe điện thoại của khách hàng quen thuộc. "
    "Buồn. Nhưng thà bỏ. Bạn mua hạt của mình thì phải nhận đúng mẻ mình dám uống trước."
)


def _parse(result) -> dict:
    if getattr(result, "structuredContent", None):
        sc = result.structuredContent
        return sc.get("result", sc) if isinstance(sc, dict) else sc
    for item in result.content:
        text = getattr(item, "text", None)
        if text:
            return json.loads(text)
    raise RuntimeError(f"no parseable content in result: {result}")


async def main() -> int:
    failures = 0
    total = 0

    def check(name: str, ok: bool, detail: str = ""):
        nonlocal failures, total
        total += 1
        print(f"{'PASS' if ok else 'FAIL'}  {name:<28} {detail}")
        failures += 0 if ok else 1

    # Isolate style-profile writes: save_style_profile in this test must not
    # drop files into the shipped data/style_profiles/ directory.
    env = {**get_default_environment(), **os.environ,
           "CONTENT_CORE_STYLE_PROFILES_DIR": str(Path(tempfile.mkdtemp()) / "profiles"),
           "CONTENT_CORE_BRAND_DRAFTS_DIR": str(Path(tempfile.mkdtemp()) / "brand_drafts"),
           "CONTENT_CORE_QUALITY_PROFILES_DIR": str(Path(tempfile.mkdtemp()) / "quality_profiles")}
    params = StdioServerParameters(
        command=sys.executable, args=[str(PROJECT_DIR / "mcp_server.py")],
        cwd=str(PROJECT_DIR), env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            check("initialize", init.serverInfo.name == "content-core",
                  f"server={init.serverInfo.name} protocol={init.protocolVersion}")

            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            expected = ["get_persona", "get_brand", "research_facts", "assemble_context",
                        "validate_brief", "make_outline", "start_draft", "save_section",
                        "get_draft", "brand_check", "fact_check", "seo_check",
                        "final_gate", "run_golden",
                        "get_craft_cards", "get_exemplars", "get_domain_glossary", "craft_check",
                        "analyze_style", "save_style_profile", "get_style_profile", "style_match",
                        "analyze_site_brand", "save_brand_draft", "get_brand_draft",
                        "compare_brand_voice",
                        "get_quality_profile", "save_quality_profile", "quality_score"]
            check("tools/list", all(n in names for n in expected) and len(names) == 29,
                  f"{len(names)} tools: {names}")

            brand = _parse(await session.call_tool("get_brand", {}))
            check("get_brand", brand["brand"]["id"] == "moc-brew" and "delve" in brand["voice"]["banned_terms"],
                  f"brand={brand['brand']['name']}, banned={len(brand['voice']['banned_terms'])} terms")

            research = _parse(await session.call_tool(
                "research_facts", {"query": "giá một gói 250g và lịch rang giao hàng"}))
            ids = [s["id"] for s in research["sources"]]
            check("research_facts", "price" in ids and "roast-schedule" in ids,
                  f"triggered={ids}, price fact={research['facts']['price_display']}")

            verdict = _parse(await session.call_tool("validate_brief", {"brief": {
                "id": "t6", "format": "blog", "topic": "cà phê và giấc ngủ", "keyword": "cà phê",
                "title": "Cà phê và giấc ngủ", "requested_claims": ["100% chữa mất ngủ"]}}))
            check("validate_brief refuses", bool(verdict["refused_claims"]),
                  f"refused_claims={verdict['refused_claims']}")

            bad = _parse(await session.call_tool(
                "brand_check", {"text": BAD_TEXT, "format": "blog"}))
            check("brand_check catches bad", len(bad["banned_found"]) >= 3 and bad["score"] < 85,
                  f"score={bad['score']}, banned_found={bad['banned_found']}")

            wrong = _parse(await session.call_tool("fact_check", {"text": WRONG_PRICE_TEXT}))
            check("fact_check wrong price", not wrong["ok"] and wrong["issues"][0]["type"] == "wrong_price",
                  f"issues={wrong['issues']}")

            # Clean draft end-to-end: start -> outline -> save sections -> final_gate
            draft = _parse(await session.call_tool("start_draft", {"brief": {
                "id": "t-clean", "format": "blog", "topic": "cà phê văn phòng giá rang giao",
                "keyword": "cà phê", "title": "Cà phê rang mẻ nhỏ cho dân văn phòng"}}))
            draft_id = draft["draft_id"]
            await session.call_tool("make_outline", {
                "brief": {"format": "blog", "topic": "cà phê văn phòng"},
                "proposed_outline": [{"heading": "Giá và lịch rang", "intent": "price", "target_words": 60},
                                     {"heading": "Giao hàng và pha", "intent": "delivery", "target_words": 60}],
                "draft_id": draft_id})
            await session.call_tool("save_section", {
                "draft_id": draft_id, "section_id": "s1", "heading": "Giá và lịch rang", "text": CLEAN_S1})
            await session.call_tool("save_section", {
                "draft_id": draft_id, "section_id": "s2", "heading": "Giao hàng và pha", "text": CLEAN_S2})
            gate = _parse(await session.call_tool("final_gate", {"draft_id": draft_id}))
            check("final_gate clean draft", gate["passed"] and gate["status"] == "approved",
                  f"passed={gate['passed']} brand={gate['brand_score']} status={gate['status']}")

            # Layer 4 tools over the wire.
            cards = _parse(await session.call_tool(
                "get_craft_cards", {"format": "blog", "intent": "sensory", "query": "vị hương hạt pha"}))
            card_ids = [c["id"] for c in cards["cards"]]
            check("get_craft_cards", "show-dont-tell-sensory" in card_ids and cards["count"] <= 3,
                  f"cards={card_ids}")

            ex = _parse(await session.call_tool("get_exemplars", {"format": "product"}))
            check("get_exemplars", ex["count"] == 1 and 60 <= len(ex["exemplars"][0]["text"].split()) <= 120,
                  f"format=product words={len(ex['exemplars'][0]['text'].split())}")

            craft = _parse(await session.call_tool(
                "craft_check", {"text": BAD_TEXT, "format": "blog"}))
            check("craft_check catches weak", craft["score"] < 100,
                  f"score={craft['score']} weak={craft['metrics']['weak_words_found']}")
            check("final_gate craft gate fields", "craft" in gate
                  and "craft_score" in gate["gate_history"][-1]
                  and gate.get("craft_blocking") is True  # blog blocks on craft (P2b)
                  and gate.get("craft_advisory") is False
                  and gate["craft"]["score"] >= 70,
                  f"craft_score={gate['gate_history'][-1].get('craft_score')}")

            # Style Extraction flow: analyze -> save profile -> style_match.
            analysis = _parse(await session.call_tool(
                "analyze_style", {"samples": [STYLE_SAMPLE_A, STYLE_SAMPLE_B]}))
            check("analyze_style", analysis["sample_count"] == 2
                  and analysis["fingerprint"]["word_count"] > 0
                  and any("only 2 sample" in w for w in analysis["consistency"]["warnings"]),
                  f"samples=2 warnings={analysis['consistency']['warnings']}")

            saved = _parse(await session.call_tool("save_style_profile", {
                "name": "Giọng Mẫu Client",
                "samples": [STYLE_SAMPLE_A, STYLE_SAMPLE_B],
                "qualitative": {"stance": "nói thẳng",
                                "never_do": ["không hứa sức khỏe"]}}))
            check("save_style_profile", bool(saved.get("id")) and saved["exemplars"] == [],
                  f"id={saved.get('id')} excerpts_note={saved.get('excerpts_note', '')[:40]}")

            match = _parse(await session.call_tool(
                "style_match", {"text": STYLE_SAMPLE_A, "profile_id": saved["id"]}))
            check("style_match", match.get("profile_id") == saved["id"]
                  # 8 metrics since backlog item 5a (question/exclamation rates)
                  and 0 <= match["score"] <= 100 and len(match["metrics"]) == 8,
                  f"score={match.get('score')}")

            # Brand Builder flow: analyze -> save draft -> get -> compare.
            analysis = _parse(await session.call_tool(
                "analyze_site_brand", {"pages": [BRAND_PAGE_DINHCAO]}))
            check("analyze_site_brand", analysis["brand_kit"]["brand_name"]["value"] == "Đỉnh Cao Coffee"
                  and len(analysis["site_claims"]) >= 3
                  and analysis["brand_kit"]["theme_color"]["value"] == "#c8102e",
                  f"claims={len(analysis['site_claims'])} warnings={len(analysis['warnings'])}")

            draft = _parse(await session.call_tool("save_brand_draft", {
                "pages": [BRAND_PAGE_SOM], "qualitative": BRAND_QUAL,
                "source_type": "own_site"}))
            check("save_brand_draft", draft["brand"]["id"] == "som-coffee"
                  and draft["brand"]["review_status"] == "draft"
                  and draft["voice"]["banned_terms"] == [],
                  f"id={draft['brand']['id']} notes={len(draft['review_notes'])}")

            got = _parse(await session.call_tool(
                "get_brand_draft", {"draft_id": "som-coffee"}))
            check("get_brand_draft", got["brand"]["name"] == "Sớm Coffee",
                  f"name={got['brand']['name']}")

            contrast = _parse(await session.call_tool("compare_brand_voice", {
                "pages_a": [BRAND_PAGE_SOM], "pages_b": [BRAND_PAGE_DINHCAO]}))
            check("compare_brand_voice", bool(contrast["gaps"])
                  and contrast["claims"]["b"] > contrast["claims"]["a"],
                  f"gaps={len(contrast['gaps'])} claims a={contrast['claims']['a']} b={contrast['claims']['b']}")

            # Quality Profile flow: preset -> quality_score with judged
            # scores on the clean draft -> save a custom profile.
            preset = _parse(await session.call_tool(
                "get_quality_profile", {"format_or_id": "blog"}))
            check("get_quality_profile", preset["id"] == "preset-blog"
                  and sum(preset["weights"].values()) == 100
                  and preset["floors"]["facts"] == 70,
                  f"id={preset['id']} weights_sum={sum(preset['weights'].values())}")

            qreport = _parse(await session.call_tool("quality_score", {
                "draft_id": draft_id,
                "judged_scores": {"coherence": 2, "persuasion": 1, "depth": 0}}))
            depth_axis = next(a for a in qreport["axes"] if a["axis"] == "depth")
            check("quality_score", qreport["profile_id"] == "preset-blog"
                  and depth_axis["score"] == 20 and depth_axis["source"] == "judged"
                  and qreport["floors_failed"] == ["depth"]
                  and qreport["quality_pass"] is False,
                  f"total={qreport['weighted_total']} floors_failed={qreport['floors_failed']}")

            custom = _parse(await session.call_tool("save_quality_profile", {
                "format": "blog",
                "weights": {"facts": 18, "voice": 14, "clarity": 14, "coherence": 14,
                            "seo": 20, "persuasion": 10, "depth": 10}}))
            fetched = _parse(await session.call_tool(
                "get_quality_profile", {"format_or_id": custom.get("id", "")}))
            check("save_quality_profile", custom.get("id", "").startswith("custom-blog-")
                  and fetched["weights"]["seo"] == 20,
                  f"id={custom.get('id')}")

    print(f"\n{total - failures}/{total} MCP client checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
