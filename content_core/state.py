"""Typed state for the content graph — the single contract every node shares."""
from __future__ import annotations

from typing import Any, TypedDict


class Section(TypedDict, total=False):
    id: str
    heading: str
    intent: str
    target_words: int
    text: str


class ContentState(TypedDict, total=False):
    # inputs
    brief: dict[str, Any]          # {id, format, topic, keyword, title?, writer_mode?, requested_claims?}
    brand_id: str
    persona_id: str
    outline_approved: bool         # simulated human approval flag for the outline gate
    # working data
    sources: list[dict[str, Any]]  # lorebook/facts entries retrieved for this brief ONLY
    refused_claims: list[str]      # claims in the brief the core refuses to write (e.g. false health claims)
    outline: list[Section]
    sections: list[Section]
    # checks
    brand_violations: list[dict[str, Any]]
    fact_issues: list[dict[str, Any]]      # UNFLAGGED fact errors — these block the final gate
    flagged_claims: list[dict[str, Any]]   # claims correctly marked [NEEDS SOURCE] — do not block
    seo: dict[str, Any]
    scores: dict[str, Any]
    gate_history: list[dict[str, Any]]     # one entry per check pass, incl. failed first passes
    failing_sections: list[str]
    rewrite_count: int
    status: str  # validated|refused|awaiting_outline_approval|drafted|rewriting|approved|failed_gates|invalid_brief
