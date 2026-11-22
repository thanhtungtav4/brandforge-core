#!/usr/bin/env python3
"""Demo: the article-images layer end to end (MCP mode).

Flow on a Mộc Brew blog draft: start_draft -> save_section x2 ->
add_library_image x3 (the brand's own photo library) -> plan_images ->
fulfil_images_from_library (featured + origins section come from the
library; the office-brewing section fits nothing -> needs_generation
with a style-locked prompt) -> set_image for the generated one ->
image_check (vision_unchecked warnings) -> record_vision_check x3 ->
get_image_manifest (ready). Then a second draft with broken variants
(stuffed alt, duplicate alt, featured reuse, vision grade 0) fails
image_check with exactly those issue types.

Stores are isolated in a temp dir via env overrides — the demo never
touches the real drafts/ or data/image_library/.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_DIR = Path(__file__).resolve().parent

BRIEF = {"id": "demo-images", "format": "blog", "topic": "cà phê văn phòng",
         "keyword": "cà phê", "title": "Cà phê văn phòng"}
SECTIONS = [
    {"id": "s1", "heading": "Nguồn gốc hạt Arabica Cầu Đất",
     "intent": "origins", "text": "Mình phối Arabica Cầu Đất cùng Robusta Đắk Lắk."},
    {"id": "s2", "heading": "Pha tại văn phòng", "intent": "office-brewing",
     "text": "Bạn pha phin ngay tại bàn, không cần máy móc cồng kềnh."},
]


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
    tmp = tempfile.mkdtemp(prefix="content-core-demo-images-")
    env = {**os.environ,
           "CONTENT_CORE_DRAFTS_DIR": str(Path(tmp) / "drafts"),
           "CONTENT_CORE_IMAGE_LIBRARY_DIR": str(Path(tmp) / "image_library")}
    params = StdioServerParameters(
        command=sys.executable, args=[str(PROJECT_DIR / "mcp_server.py")],
        cwd=str(PROJECT_DIR), env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            call = lambda name, args: session.call_tool(name, args)  # noqa: E731

            d = _parse(await call("start_draft", {"brief": BRIEF}))
            draft_id = d["draft_id"]
            for sec in SECTIONS:
                await call("save_section", {
                    "draft_id": draft_id, "section_id": sec["id"],
                    "heading": sec["heading"], "text": sec["text"],
                    "intent": sec["intent"], "target_words": 120})
            print(f"1 start_draft + save_section x2: {draft_id}")

            # The brand's own library: two relevant photos + one decoy.
            await call("add_library_image", {
                "brand_id": "moc-brew", "ref": "photos/goi-ca-phe.jpg",
                "description": "Gói cà phê Mộc Brew 250g trên bàn gỗ cạnh chiếc phin",
                "tags": ["cà phê", "gói 250g", "phin"]})
            await call("add_library_image", {
                "brand_id": "moc-brew", "ref": "ai/vuon-arabica.webp",
                "description": "Vườn cà phê Arabica ở Cầu Đất vào buổi sáng",
                "tags": ["arabica", "cầu đất", "vườn cà phê"], "source": "ai"})
            await call("add_library_image", {
                "brand_id": "moc-brew", "ref": "photos/meo.jpg",
                "description": "Con mèo nằm ngủ trên ghế sofa",
                "tags": ["mèo", "sofa"]})
            print("2 add_library_image x3: library stocked (2 relevant + 1 decoy)")

            p = _parse(await call("plan_images", {"draft_id": draft_id}))
            plan = p["plan"]
            print(f"3 plan_images: {[i['id'] for i in plan]}")
            assert [i["id"] for i in plan] == ["img-featured", "img-s1", "img-s2"]
            block = plan[0]["style_block"]
            assert block and all(i["prompt"].endswith(block) for i in plan)
            print(f"   style block locked on every prompt: {block[:60]}...")

            f = _parse(await call("fulfil_images_from_library",
                                  {"draft_id": draft_id}))
            got = {x["image_id"]: x["ref"] for x in f["fulfilled"]}
            print(f"4 fulfil_from_library: {got}")
            assert got == {"img-featured": "photos/goi-ca-phe.jpg",
                           "img-s1": "ai/vuon-arabica.webp"}
            gen = {g["image_id"]: g for g in f["needs_generation"]}
            assert set(gen) == {"img-s2"} and gen["img-s2"]["prompt"].endswith(block)
            print("   img-s2 fits nothing -> needs_generation (style-locked prompt)")

            # The writer "generates" img-s2 with exactly that prompt.
            await call("set_image", {
                "draft_id": draft_id, "image_id": "img-s2", "source": "ai",
                "ref": "ai/pha-tai-van-phong.webp",
                "alt": "Pha cà phê bằng phin ngay tại bàn làm việc",
                "description": "Phin cà phê trên bàn văn phòng",
                "prompt_used": gen["img-s2"]["prompt"]})
            print("5 set_image img-s2: generated with the style-locked prompt")

            chk = _parse(await call("image_check",
                                    {"draft_id": draft_id, "complete": True}))
            print(f"6 image_check: ok={chk['ok']} issues={chk['issues']}")
            assert chk["ok"]
            unchecked = [w for w in chk["flagged"]
                         if w["type"] == "vision_unchecked"]
            assert len(unchecked) == 3
            print("   flagged: 3 x vision_unchecked (warnings, not blockers)")

            # The writer looks at each image and grades it.
            for iid, cg, sg, notes in (
                    ("img-featured", 2, 2, ""),
                    ("img-s1", 2, 2, ""),
                    ("img-s2", 2, 1, "hơi tối ở góc trái, vẫn dùng được")):
                await call("record_vision_check", {
                    "draft_id": draft_id, "image_id": iid,
                    "content_grade": cg, "style_grade": sg, "notes": notes})
            man = _parse(await call("get_image_manifest",
                                    {"draft_id": draft_id}))
            print(f"7 record_vision_check x3 -> manifest ready={man['ready']}")
            assert man["ready"] and len(man["manifest"]) == 3
            assert man["manifest"][0]["alt_text"].startswith("Gói cà phê")
            print(f"   manifest[0]: {man['manifest'][0]['filename']} "
                  f"alt={man['manifest'][0]['alt_text'][:40]}...")

            # --- broken variants on a second draft ---
            d2 = _parse(await call("start_draft", {"brief": BRIEF}))
            bad_id = d2["draft_id"]
            for sec in SECTIONS:
                await call("save_section", {
                    "draft_id": bad_id, "section_id": sec["id"],
                    "heading": sec["heading"], "text": sec["text"],
                    "intent": sec["intent"], "target_words": 120})
            await call("plan_images", {"draft_id": bad_id})
            await call("set_image", {
                "draft_id": bad_id, "image_id": "img-featured",
                "source": "client", "ref": "photos/goi.jpg",
                "alt": "Gói cà phê Mộc Brew trên bàn gỗ cạnh phin",
                "caption": "c", "description": "d"})
            await call("set_image", {  # featured ref reused + stuffed alt
                "draft_id": bad_id, "image_id": "img-s1", "source": "client",
                "ref": "photos/goi.jpg",
                "alt": "Cà phê cà phê cho người mê cà phê"})
            await call("set_image", {  # duplicate of the featured alt
                "draft_id": bad_id, "image_id": "img-s2", "source": "client",
                "ref": "photos/pha.jpg",
                "alt": "Gói cà phê Mộc Brew trên bàn gỗ cạnh phin"})
            await call("record_vision_check", {  # writer sees it is wrong
                "draft_id": bad_id, "image_id": "img-s2",
                "content_grade": 0, "style_grade": 2,
                "notes": "ảnh chụp quán khác, không phải phin của mình"})
            chk2 = _parse(await call("image_check",
                                     {"draft_id": bad_id, "complete": True}))
            types = {i["type"] for i in chk2["issues"]}
            print(f"8 broken draft image_check issues: {sorted(types)}")
            assert {"featured_reused", "stuffed_alt", "duplicate_alt",
                    "vision_content_mismatch"} <= types
            assert _parse(await call("get_image_manifest",
                                     {"draft_id": bad_id}))["ready"] is False
            print("   manifest ready=False, as required")
            print("\nDEMO IMAGES OK")
            return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
