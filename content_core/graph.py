"""The core graph (real LangGraph StateGraph).

Flow: validate_brief -> research -> outline -> outline_gate -[approved]->
draft_sections -> brand_check -> fact_check -> seo_pass -> final_gate
final_gate -[fail & rewrite_count<2]-> rewrite_sections -> brand_check (loop)
final_gate -[pass or rewrites exhausted]-> END
"""
from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from .fact_check import check_draft, find_refused_claims
from .gates import MAX_REWRITES, evaluate_gate
from .linter import lint_draft
from .loaders import load_brand, load_facts, load_persona
from .seo import seo_pass as run_seo
from .state import ContentState
from .writer import Writer, get_writer

OUTLINE_TEMPLATES: dict[str, list[tuple[str, str, int]]] = {
    "blog": [
        ("Giới thiệu", "intro", 320),
        ("Nguồn gốc hạt", "origins", 320),
        ("Lịch rang và độ tươi", "roast", 320),
        ("Pha tại văn phòng", "office-brewing", 320),
        ("Giá và giao hàng", "price-delivery", 320),
    ],
    "product": [
        ("Thông tin chính", "origins", 90),
        ("Lịch rang", "roast", 90),
        ("Giá và giao hàng", "price-delivery", 90),
    ],
    "social": [("Bài social", "social", 45)],
    "email": [
        ("Lời chào", "greeting", 40),
        ("Cập nhật đơn hàng", "update", 50),
        ("Bước tiếp theo", "next-step", 50),
    ],
    "comparison": [
        ("Tiêu chí so sánh", "origins", 130),
        ("Điểm khác nhau cần kiểm chứng", "comparison-claims", 130),
        ("Giá và giao hàng", "price-delivery", 130),
    ],
}
DEFAULT_OUTLINE = OUTLINE_TEMPLATES["blog"]


class Core:
    def __init__(self, writer: Writer | None = None, brand: dict | None = None,
                 facts: dict | None = None, persona: dict | None = None):
        self.writer = writer or get_writer()
        self.brand = brand or load_brand()
        self.facts = load_facts() if facts is None else facts
        self.persona = persona or load_persona()

    # -- nodes -------------------------------------------------------------
    def validate_brief(self, state: ContentState) -> dict[str, Any]:
        brief = state.get("brief", {})
        if not brief.get("topic") or not brief.get("format"):
            return {"status": "invalid_brief"}
        refused = find_refused_claims(str(brief), self.brand)
        if refused:
            # Refusal routing (evaluation R8): a brief that ASKS for
            # prohibited claims ends here with the distinct status
            # "refused" — it is not drafted toward "approved" with the
            # claims merely noted. The fact checker remains the backstop
            # for claim-shaped text that slips into a draft anyway.
            # Disclaimers ("cam kết không chữa mất ngủ") never reach
            # this branch: find_refused_claims is negation-aware.
            return {"status": "refused", "refused_claims": refused,
                    "rewrite_count": 0,
                    "brand_id": self.brand["brand"]["id"],
                    "persona_id": self.persona["data"]["name"]}
        return {"status": "validated", "refused_claims": refused, "rewrite_count": 0,
                "brand_id": self.brand["brand"]["id"],
                "persona_id": self.persona["data"]["name"]}

    def research(self, state: ContentState) -> dict[str, Any]:
        """Keyword-triggered lorebook retrieval ONLY — no external sources."""
        haystack = str(state.get("brief", {})).lower()
        sources = [e for e in self.facts["entries"]
                   if e.get("constant") or any(k.lower() in haystack for k in e["keys"])]
        return {"sources": sources}

    def outline(self, state: ContentState) -> dict[str, Any]:
        fmt = state["brief"].get("format", "blog")
        template = OUTLINE_TEMPLATES.get(fmt, DEFAULT_OUTLINE)
        outline = [{"id": f"s{i+1}", "heading": h, "intent": intent, "target_words": tw, "text": ""}
                   for i, (h, intent, tw) in enumerate(template)]
        return {"outline": outline}

    def outline_gate(self, state: ContentState) -> dict[str, Any]:
        if state.get("outline_approved"):
            return {"status": "outline_approved"}
        return {"status": "awaiting_outline_approval"}

    def draft_sections(self, state: ContentState) -> dict[str, Any]:
        ctx = {"brief": state["brief"], "sources": state.get("sources", []),
               "facts": self.facts, "brand": self.brand, "persona": self.persona}
        sections = [{**sec, "text": self.writer.write_section(sec, ctx)} for sec in state["outline"]]
        return {"sections": sections, "status": "drafted"}

    def brand_check(self, state: ContentState) -> dict[str, Any]:
        violations, score, _failing = lint_draft(state["sections"], state["brief"]["format"], self.brand)
        scores = {**state.get("scores", {}), "brand": score}
        return {"brand_violations": violations, "scores": scores}

    def fact_check(self, state: ContentState) -> dict[str, Any]:
        issues, flagged, _failing = check_draft(state["sections"], self.facts, self.brand)
        return {"fact_issues": issues, "flagged_claims": flagged}

    def seo_pass(self, state: ContentState) -> dict[str, Any]:
        return {"seo": run_seo(state["brief"], state["sections"])}

    def final_gate(self, state: ContentState) -> dict[str, Any]:
        # THE gate rule lives in gates.evaluate_gate — the SAME function
        # the MCP final_gate tool calls (one rule, two call sites). Craft
        # blocking is unified: MockWriter's golden output measures craft
        # >= 70 for blog/product, so the graph applies the craft layer in
        # blocking mode exactly like MCP (see gates.py's note).
        verdict = evaluate_gate(state.get("sections", []),
                                state["brief"]["format"], self.brand, self.facts,
                                craft_mode="blocking")
        hard = verdict["hard"]
        history = list(state.get("gate_history", []))
        history.append({"pass": len(history) + 1, "brand_score": hard["brand_score"],
                        "brand_violations": len(hard["brand_violations"]),
                        "fact_issues": len(hard["fact_issues"]),
                        "passed": verdict["passed"],
                        "craft_score": verdict["craft"]["score"],
                        "craft_blocking": verdict["craft"]["blocking"]})
        failing_ids = verdict["failing_sections"]
        if verdict["passed"]:
            return {"status": "approved", "failing_sections": [], "gate_history": history}
        if state.get("rewrite_count", 0) < MAX_REWRITES:
            return {"status": "rewriting", "failing_sections": failing_ids, "gate_history": history}
        return {"status": "failed_gates", "failing_sections": failing_ids, "gate_history": history}

    def rewrite_sections(self, state: ContentState) -> dict[str, Any]:
        ctx = {"brief": state["brief"], "sources": state.get("sources", []),
               "facts": self.facts, "brand": self.brand, "persona": self.persona}
        failing = set(state.get("failing_sections", []))
        issues = state.get("brand_violations", []) + state.get("fact_issues", [])
        sections = []
        for sec in state["sections"]:
            if sec["id"] in failing:
                sec = {**sec, "text": self.writer.rewrite_section(sec, ctx, issues)}
            sections.append(sec)
        return {"sections": sections, "rewrite_count": state.get("rewrite_count", 0) + 1}

    # -- routing -----------------------------------------------------------
    @staticmethod
    def _after_validate(state: ContentState) -> str:
        return END if state.get("status") in ("invalid_brief", "refused") else "research"

    @staticmethod
    def _after_outline_gate(state: ContentState) -> str:
        return "draft_sections" if state.get("status") == "outline_approved" else END

    @staticmethod
    def _after_final_gate(state: ContentState) -> str:
        return "rewrite_sections" if state.get("status") == "rewriting" else END


def build_graph(writer: Writer | None = None, **data) -> Any:
    core = Core(writer=writer, **data)
    g = StateGraph(ContentState)
    for name, fn in [("validate_brief", core.validate_brief), ("research", core.research),
                     ("outline", core.outline), ("outline_gate", core.outline_gate),
                     ("draft_sections", core.draft_sections), ("brand_check", core.brand_check),
                     ("fact_check", core.fact_check), ("seo_pass", core.seo_pass),
                     ("final_gate", core.final_gate), ("rewrite_sections", core.rewrite_sections)]:
        g.add_node(name, fn)
    g.add_edge(START, "validate_brief")
    g.add_conditional_edges("validate_brief", Core._after_validate, ["research", END])
    g.add_edge("research", "outline")
    g.add_edge("outline", "outline_gate")
    g.add_conditional_edges("outline_gate", Core._after_outline_gate, ["draft_sections", END])
    g.add_edge("draft_sections", "brand_check")
    g.add_edge("brand_check", "fact_check")
    g.add_edge("fact_check", "seo_pass")
    g.add_edge("seo_pass", "final_gate")
    g.add_conditional_edges("final_gate", Core._after_final_gate, ["rewrite_sections", END])
    g.add_edge("rewrite_sections", "brand_check")
    return g.compile()


def run_flow(brief: dict, outline_approved: bool = True, writer: Writer | None = None, **data) -> ContentState:
    graph = build_graph(writer=writer, **data)
    return graph.invoke({"brief": brief, "outline_approved": outline_approved})
