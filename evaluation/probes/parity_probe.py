"""MCP stdio <-> HTTP parity spot-check (rebuild of the R7 method).

Runs the same deterministic, read-only tool calls over both transports
and diffs the parsed results. Volatile calls (draft creation etc.) are
excluded. Exit code 1 if any call differs.

    python evaluation/probes/parity_probe.py
"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[2]

TEXT = ("Mình mở túi hạt mới rang sáng nay, mùi caramel chạm mũi đầu tiên. "
        "Một gói 250g giá 185.000đ, rang vào thứ Ba, giao trong 2-3 ngày. "
        "Bạn pha phin chậm rãi rồi ngắm vườn xanh mát ngoài cửa sổ.")

CALLS = [
    ("get_persona", {}),
    ("get_brand", {}),
    ("research_facts", {"query": "giá gói 250g giao hàng"}),
    ("assemble_context", {"brief": {"format": "blog", "topic": "cà phê văn phòng giá",
                                    "keyword": "cà phê", "title": "Cà phê văn phòng"}}),
    ("get_craft_cards", {"format": "product", "intent": "mở bài",
                         "keywords": "giá vị hương mô tả sản phẩm"}),
    ("get_exemplars", {"format": "blog"}),
    ("get_domain_glossary", {}),
    ("craft_check", {"text": TEXT, "format": "blog"}),
    ("analyze_style", {"samples": [TEXT, TEXT.replace("185.000đ", "185.000đ")]}),
    ("get_style_profile", {"profile_id": "mocbrew-blog-voice"}),
    ("style_match", {"text": TEXT, "profile_id": "mocbrew-blog-voice"}),
    ("get_quality_profile", {"format_or_id": "blog"}),
    ("validate_brief", {"brief": {"format": "blog", "topic": "cà phê chữa mất ngủ",
                                  "keyword": "cà phê", "title": "Cà phê tối"}}),
    ("brand_check", {"text": TEXT, "format": "blog"}),
    ("fact_check", {"text": TEXT}),
    ("seo_check", {"text": TEXT, "keyword": "cà phê",
                   "title": "Cà phê văn phòng", "format": "blog"}),
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


async def _run_stdio() -> dict:
    params = StdioServerParameters(
        command=sys.executable, args=[str(ROOT / "mcp_server.py")], cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return {name: _parse(await session.call_tool(name, args))
                    for name, args in CALLS}


async def _run_http(port: int) -> dict:
    url = f"http://127.0.0.1:{port}/mcp"
    http_client = httpx.AsyncClient(trust_env=False, timeout=30.0)
    async with streamable_http_client(url, http_client=http_client) as (read, write, _sid):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return {name: _parse(await session.call_tool(name, args))
                    for name, args in CALLS}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    stdio_results = asyncio.run(_run_stdio())
    port = _free_port()
    env = {**os.environ, "CONTENT_CORE_HTTP_HOST": "127.0.0.1",
           "CONTENT_CORE_HTTP_PORT": str(port)}
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "mcp_server.py"), "--transport", "http"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 30
        http_results = None
        while time.time() < deadline:
            try:
                http_results = asyncio.run(_run_http(port))
                break
            except Exception:
                time.sleep(0.5)
        if http_results is None:
            raise RuntimeError("HTTP server did not come up")
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    diffs = 0
    for name, _args in CALLS:
        a, b = stdio_results[name], http_results[name]
        same = json.dumps(a, sort_keys=True, ensure_ascii=False) == \
            json.dumps(b, sort_keys=True, ensure_ascii=False)
        if not same:
            diffs += 1
            print(f"DIFF {name}:\n  stdio: {json.dumps(a, ensure_ascii=False)[:200]}\n"
                  f"  http:  {json.dumps(b, ensure_ascii=False)[:200]}")
    print(f"parity: {len(CALLS) - diffs}/{len(CALLS)} calls identical across transports")
    sys.exit(1 if diffs else 0)


if __name__ == "__main__":
    main()
