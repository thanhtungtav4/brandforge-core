#!/usr/bin/env python3
"""MCP HTTP smoke test: starts mcp_server.py with --transport http on a
free localhost port, connects with the official MCP streamable-HTTP
client, lists tools, calls get_quality_profile, and shuts the server down.

Usage: .venv/bin/python test_mcp_http.py   (exit code non-zero on any FAIL)
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

PROJECT_DIR = Path(__file__).resolve().parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _parse(result) -> dict:
    if getattr(result, "structuredContent", None):
        sc = result.structuredContent
        return sc.get("result", sc) if isinstance(sc, dict) else sc
    for item in result.content:
        text = getattr(item, "text", None)
        if text:
            return json.loads(text)
    raise RuntimeError(f"no parseable content in result: {result}")


async def _exercise(port: int) -> tuple[bool, str]:
    url = f"http://127.0.0.1:{port}/mcp"
    # Explicit client with trust_env=False: this is a localhost call and
    # must never route through HTTP(S)_PROXY env vars (some environments
    # export proxy settings whose NO_PROXY entries httpx cannot parse).
    http_client = httpx.AsyncClient(trust_env=False, timeout=30.0)
    async with streamable_http_client(url, http_client=http_client) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            if init.serverInfo.name != "content-core":
                return False, f"unexpected server: {init.serverInfo.name}"
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            # Exact comparison against the registry-derived list
            # (review M19) — stdio and HTTP must serve the same tools.
            import mcp_server as _srv
            if set(names) != set(_srv.TOOL_NAMES) or "get_quality_profile" not in names:
                return False, f"tools over http: {len(names)} {names}"
            preset = _parse(await session.call_tool(
                "get_quality_profile", {"format_or_id": "blog"}))
            if preset.get("id") != "preset-blog" or sum(preset["weights"].values()) != 100:
                return False, f"get_quality_profile over http: {preset}"
            return True, f"server={init.serverInfo.name}, {len(names)} tools, preset-blog weights sum 100"


def _start_server() -> tuple[subprocess.Popen, int] | None:
    """Start the server on a free port; None when the port was taken
    between probing and binding (review L18: the old single-shot
    free-port probe lost that race as a flaky 'server exited early')."""
    port = _free_port()
    env = {**os.environ, "CONTENT_CORE_HTTP_HOST": "127.0.0.1",
           "CONTENT_CORE_HTTP_PORT": str(port)}
    proc = subprocess.Popen(
        [sys.executable, str(PROJECT_DIR / "mcp_server.py"),
         "--transport", "http"],
        cwd=str(PROJECT_DIR), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Wait until the port accepts connections (server startup).
    deadline = time.time() + 30
    while True:
        if proc.poll() is not None:
            return None  # exited early (e.g. port taken): caller retries
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return proc, port
        except OSError:
            if time.time() > deadline:
                proc.terminate()
                return None
            time.sleep(0.2)


def main() -> int:
    started = None
    for _attempt in range(5):
        started = _start_server()
        if started is not None:
            break
    if started is None:
        print("FAIL  server did not start listening after 5 attempts")
        return 1
    proc, port = started
    try:
        ok, detail = asyncio.run(_exercise(port))
        print(f"{'PASS' if ok else 'FAIL'}  http smoke test  {detail}")
        return 0 if ok else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main())
