#!/usr/bin/env python3
"""End-to-end demo v2 — WITH Layer 4 (Craft & Domain Library).
Same brief as demo_mcp_e2e.py (Mộc Brew product 250g). Muse rewrites the prose
using the triggered craft cards + product exemplar + domain glossary, then the
MCP tools judge: brand_check, fact_check, craft_check (old text vs new text),
seo_check, final_gate (craft advisory included).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_DIR = Path(__file__).resolve().parent

BRIEF = {"id": "demo-e2e-v2", "format": "product",
         "topic": "mô tả gói cà phê Mộc Brew 250g giá vị hương lịch rang giao hàng",
         "keyword": "cà phê",
         "title": "Gói cà phê Mộc Brew 250g"}

OLD_S1 = (
    "Một gói Mộc Brew 250g có giá 185.000đ, ghi rõ trước mọi mô tả. "
    "Mình phối Arabica Cầu Đất cùng Robusta Đắk Lắk cho vị cân bằng. "
    "Arabica cho vị chua nhẹ, Robusta giữ hậu đậm và lớp crema dày. "
    "Mình ghi ngày rang lên bao bì để bạn kiểm tra độ mới."
)
OLD_S2 = (
    "Mộc Brew chỉ rang vào thứ Ba hàng tuần, không rang tồn kho. "
    "Đơn xác nhận trước thứ Ba được rang và đóng gói ngay hôm đó. "
    "Hàng giao tới tay bạn trong 2-3 ngày theo lịch hẹn đã chốt. "
    "Bạn xay vừa đủ mỗi lần pha để giữ hương trong hai tuần đầu."
)

# NEW prose by Muse, written against the triggered craft cards:
# concrete-opening (price first, as a scene), show-dont-tell-sensory (glossary
# terms: caramel, chua sáng, hậu cacao, khói nhẹ, bọt nở), vary-rhythm
# (sentence lengths 11/13/15/17 and 13/11/5/16 — medians still inside the
# brand rule 11-14, max < 25).
NEW_S1 = (
    "185.000đ cho một gói 250g, ghi ngay ở dòng đầu tiên. "
    "Mở túi, mùi caramel của hạt rang còn ấm chạm mũi đầu tiên. "
    "Arabica Cầu Đất cho vị chua sáng, đến nhanh rồi đi nhanh trên đầu lưỡi. "
    "Rồi Robusta Đắk Lắk giữ lại hậu cacao đậm, hơi khói nhẹ, ở cuối lưỡi rất lâu."
)
NEW_S2 = (
    "Mộc Brew chỉ rang vào thứ Ba hàng tuần, không rang tồn kho. "
    "Rang xong, mình ghi ngày rang lên mép túi bằng tay. "
    "Hàng giao trong 2-3 ngày. "
    "Bạn xay vừa đủ, rót nước nóng, nghe bọt nở lách tách trong phin mỗi sáng."
)


def _parse(result) -> dict:
    if getattr(result, "structuredContent", None):
        sc = result.structuredContent
        return sc.get("result", sc) if isinstance(sc, dict) else sc
    for item in result.content:
        text = getattr(item, "text", None)
        if text:
            return json.loads(text)
    raise RuntimeError(f"no parseable content: {result}")


async def main() -> int:
    params = StdioServerParameters(
        command=sys.executable, args=[str(PROJECT_DIR / "mcp_server.py")], cwd=str(PROJECT_DIR))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            v = _parse(await session.call_tool("validate_brief", {"brief": BRIEF}))
            print(f"1 validate_brief: valid={v['valid']} refused={v['refused_claims']}")

            cards = _parse(await session.call_tool("get_craft_cards", {
                "format": "product", "intent": "sensory", "query": BRIEF["topic"]}))
            print(f"2 get_craft_cards: {[c['id'] for c in cards['cards']]}")

            ex = _parse(await session.call_tool("get_exemplars", {"format": "product"}))
            print(f"3 get_exemplars: {ex['count']} exemplar(s) for product")

            gloss = _parse(await session.call_tool("get_domain_glossary", {}))
            print(f"4 get_domain_glossary: groups={list(gloss.get('groups', gloss).keys()) if isinstance(gloss, dict) else '?'}")

            ctx = _parse(await session.call_tool("assemble_context", {"brief": BRIEF}))
            print(f"5 assemble_context: order={[o['part'] for o in ctx['ordered_context']]}")

            old_craft = _parse(await session.call_tool("craft_check", {
                "text": OLD_S1 + " " + OLD_S2, "format": "product"}))
            new_craft = _parse(await session.call_tool("craft_check", {
                "text": NEW_S1 + " " + NEW_S2, "format": "product"}))
            print(f"6 craft_check OLD: score={old_craft['score']} violations={[x['type'] for x in old_craft['violations']]} "
                  f"sensory={old_craft['metrics']['sensory_terms_used']}")
            print(f"  craft_check NEW: score={new_craft['score']} violations={[x['type'] for x in new_craft['violations']]} "
                  f"sensory={new_craft['metrics']['sensory_terms_used']} rhythm_stdev={new_craft['metrics']['rhythm_stdev']}")

            d = _parse(await session.call_tool("start_draft", {"brief": BRIEF}))
            draft_id = d["draft_id"]
            await session.call_tool("make_outline", {
                "brief": BRIEF, "draft_id": draft_id,
                "proposed_outline": [
                    {"heading": "Giá và hạt", "intent": "price_and_beans", "target_words": 60},
                    {"heading": "Lịch rang và giao hàng", "intent": "roast_and_delivery", "target_words": 50}]})
            await session.call_tool("save_section", {
                "draft_id": draft_id, "section_id": "s1", "heading": "Giá và hạt", "text": NEW_S1})
            await session.call_tool("save_section", {
                "draft_id": draft_id, "section_id": "s2", "heading": "Lịch rang và giao hàng", "text": NEW_S2})
            print(f"7 draft {draft_id}: sections saved (new prose)")

            b = _parse(await session.call_tool("brand_check", {"draft_id": draft_id}))
            print(f"8 brand_check: score={b['score']} violations={len(b['violations'])}")
            f = _parse(await session.call_tool("fact_check", {"draft_id": draft_id}))
            print(f"9 fact_check: ok={f['ok']} issues={f['issues']}")
            s = _parse(await session.call_tool("seo_check", {"draft_id": draft_id}))
            print(f"10 seo_check: keyword_in_title={s['keyword_in_title']} headings_ok={s['headings_ok']} meta_ok={s['meta_ok']}")

            g = _parse(await session.call_tool("final_gate", {"draft_id": draft_id}))
            print(f"11 final_gate: passed={g['passed']} brand={g['brand_score']} status={g['status']} "
                  f"craft_advisory={g.get('craft', {}).get('score') if g.get('craft') else g.get('craft_score')}")
            assert g["passed"], f"gate failed: {g}"
            assert new_craft["score"] > old_craft["score"], "craft did not improve"

            print("\n===== DELIVERABLE v2 =====\n")
            print(f"# {BRIEF['title']}\n")
            print(f"## Giá và hạt\n{NEW_S1}\n")
            print(f"## Lịch rang và giao hàng\n{NEW_S2}\n")
            print(f"Meta: {s['meta_description']}")
            return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
