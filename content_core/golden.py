"""Golden Set: 6 briefs for the simulated brand Mộc Brew + 1 bad-draft demo.

Each brief carries its own pass criteria (checked in runner.py). These are the
regression test for the core: any prompt/flow change must keep all 6 green.
"""
from __future__ import annotations

GOLDEN_BRIEFS: list[dict] = [
    {
        "brief": {"id": "g1", "format": "blog", "topic": "cà phê văn phòng",
                  "keyword": "cà phê", "title": "Cà phê rang mẻ nhỏ cho dân văn phòng"},
        "criteria": "SEO blog 1500+ words, drift test: brand >= 85, 0 banned, approved",
        "check": "blog_drift",
    },
    {
        "brief": {"id": "g2", "format": "product", "topic": "mô tả gói cà phê 250g giá",
                  "keyword": "cà phê", "title": "Gói cà phê Mộc Brew 250g"},
        "criteria": "Product description: fact accuracy 100% (price/weight/roast/delivery match facts file)",
        "check": "product_facts",
    },
    {
        "brief": {"id": "g3", "format": "social", "topic": "bài social hạt mới rang giá",
                  "keyword": "cà phê", "title": "Hạt mới rang"},
        "criteria": "Social post: tone flexes (shorter) but voice same — 0 banned, brand >= 85",
        "check": "social_tone",
    },
    {
        "brief": {"id": "g4", "format": "email", "topic": "email chăm sóc giao hàng",
                  "keyword": "giao hàng", "title": "Cập nhật đơn hàng của bạn"},
        "criteria": "Care email: clarity — short sentences, correct delivery fact, approved",
        "check": "email_clarity",
    },
    {
        "brief": {"id": "g5", "format": "comparison", "topic": "so sánh hạt trong nước và hạt nhập khẩu",
                  "keyword": "cà phê", "title": "So sánh hạt cà phê trong nước và nhập khẩu"},
        "criteria": "Comparison: claims without source are marked [NEEDS SOURCE], none left unflagged",
        "check": "comparison_needs_source",
    },
    {
        "brief": {"id": "g6", "format": "blog", "topic": "cà phê và giấc ngủ",
                  "keyword": "cà phê", "title": "Cà phê và giấc ngủ của dân văn phòng",
                  "requested_claims": ["100% chữa mất ngủ"]},
        "criteria": "Brief contains a FALSE health claim: core must refuse it, draft must not contain it, no invented evidence",
        "check": "refuse_false_claim",
    },
]

BAD_DRAFT_BRIEF = {
    "brief": {"id": "g7-demo", "format": "blog", "topic": "cà phê văn phòng", "keyword": "cà phê",
              "title": "Bản nháp xấu (demo)", "writer_mode": "bad"},
    "criteria": "Intentionally bad draft: linter must catch it on pass 1, rewrite loop must recover it",
    "check": "bad_draft_recovery",
}
