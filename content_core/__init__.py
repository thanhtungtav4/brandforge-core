"""Core Content Flow — testable skeleton. No UI, no external LLM calls."""
from __future__ import annotations

from typing import Any

from .writer import MockWriter, Writer, get_writer

__all__ = ["build_graph", "run_flow", "Writer", "MockWriter", "get_writer"]


def __getattr__(name: str) -> Any:
    # build_graph / run_flow live in .graph, whose langgraph import
    # chain costs ~1 s (perf P8). They resolve LAZILY (PEP 562) so
    # importing any content_core submodule — the MCP server above all —
    # does not pay for langgraph until the graph is actually used.
    # The public package API is unchanged.
    if name in ("build_graph", "run_flow"):
        from . import graph
        return getattr(graph, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
