#!/usr/bin/env python3
"""End-to-end demo: Muse (this script's author) is the writer, MCP tools are the hands.
Brief: product description for Mộc Brew 250g (golden brief g2 style).
Flow: validate_brief -> research_facts -> assemble_context -> start_draft ->
make_outline -> save_section x2 (prose written by Muse, embedded below) ->
brand_check -> fact_check -> seo_check -> final_gate -> get_draft (deliver).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_DIR = Path(__file__).resolve().parent

BRIEF = {"id": "demo-e2e", "format": "product",
         "topic": "mô tả gói cà phê Mộc Brew 250g giá lịch rang giao hàng",
         "keyword": "cà phê",
         "title": "Gói cà phê Mộc Brew 250g"}

# Prose written by Muse (the model), section by section. Sentences 13-14 words,
# facts only from research_facts, price before benefit, no banned terms.
S1 = (
    "Một gói Mộc Brew 250g có giá 185.000đ, ghi rõ trước mọi mô tả. "
    "Mình phối Arabica Cầu Đất cùng Robusta Đắk Lắk cho vị cân bằng. "
    "Arabica cho vị chua nhẹ, Robusta giữ hậu đậm và lớp crema dày. "
    "Mình ghi ngày rang lên bao bì để bạn kiểm tra độ mới."
)
S2 = (
    "Mộc Brew chỉ rang vào thứ Ba hàng tuần, không rang tồn kho. "
    "Đơn xác nhận trước thứ Ba được rang và đóng gói ngay hôm đó. "
    "Hàng giao tới tay bạn trong 2-3 ngày theo lịch hẹn đã chốt. "
    "Bạn xay vừa đủ mỗi lần pha để giữ hương trong hai tuần đầu."
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
            assert v["valid"] and not v["refused_claims"]

            r = _parse(await session.call_tool("research_facts", {"query": BRIEF["topic"]}))
            print(f"2 research_facts: triggered={[s['id'] for s in r['sources']]} price={r['facts']['price_display']}")
            # Middle steps assert too (review, Info: only steps 1 and
            # 10 used to — a silent regression in between passed).
            assert r["facts"]["price_display"] == "185.000đ"
            assert any(s["id"] == "price" for s in r["sources"])

            ctx = _parse(await session.call_tool("assemble_context", {"brief": BRIEF}))
            print(f"3 assemble_context: order={[o['part'] for o in ctx['ordered_context']]}")
            assert [o["part"] for o in ctx["ordered_context"]][0] == "persona"
            assert "185.000đ" in ctx["context_text"]

            d = _parse(await session.call_tool("start_draft", {"brief": BRIEF}))
            draft_id = d["draft_id"]
            print(f"4 start_draft: {draft_id}")
            assert draft_id

            o = _parse(await session.call_tool("make_outline", {
                "brief": BRIEF, "draft_id": draft_id,
                "proposed_outline": [
                    {"heading": "Giá và hạt", "intent": "price_and_beans", "target_words": 55},
                    {"heading": "Lịch rang và giao hàng", "intent": "roast_and_delivery", "target_words": 55}]}))
            print(f"5 make_outline: ok={o['ok']} source={o['source']}")
            assert o["ok"] and len(o["outline"]) == 2

            await session.call_tool("save_section", {
                "draft_id": draft_id, "section_id": "s1", "heading": "Giá và hạt", "text": S1})
            await session.call_tool("save_section", {
                "draft_id": draft_id, "section_id": "s2", "heading": "Lịch rang và giao hàng", "text": S2})
            print("6 save_section: s1, s2 saved (prose by Muse)")

            b = _parse(await session.call_tool("brand_check", {"draft_id": draft_id}))
            print(f"7 brand_check: score={b['score']} violations={len(b['violations'])}")
            assert b["score"] >= 85, f"brand_check: {b}"
            f = _parse(await session.call_tool("fact_check", {"draft_id": draft_id}))
            print(f"8 fact_check: ok={f['ok']} issues={f['issues']} flagged={f['flagged_claims']}")
            assert f["ok"], f"fact_check: {f}"
            s = _parse(await session.call_tool("seo_check", {"draft_id": draft_id}))
            print(f"9 seo_check: keyword_in_title={s['keyword_in_title']} headings_ok={s['headings_ok']} meta_ok={s['meta_ok']}")
            assert s["keyword_in_title"] and s["headings_ok"]

            g = _parse(await session.call_tool("final_gate", {"draft_id": draft_id}))
            print(f"10 final_gate: passed={g['passed']} brand={g['brand_score']} status={g['status']}")
            assert g["passed"], f"gate failed: {g}"

            full = _parse(await session.call_tool("get_draft", {"draft_id": draft_id}))
            print("\n===== DELIVERABLE =====\n")
            print(f"# {BRIEF['title']}\n")
            for sec in full["sections"]:
                print(f"## {sec['heading']}\n{sec['text']}\n")
            print(f"Meta: {s['meta_description']}")
            return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
