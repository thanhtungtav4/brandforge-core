"""Minimal deterministic SEO pass: title / meta / headings / keyword coverage."""
from __future__ import annotations


def seo_pass(brief: dict, sections: list[dict]) -> dict:
    keyword = (brief.get("keyword") or brief.get("topic") or "").strip()
    title = (brief.get("title") or brief.get("topic") or "").strip()
    first_text = sections[0].get("text", "") if sections else ""
    meta = " ".join(first_text.split()[:24])
    if len(meta) > 160:
        meta = meta[:157].rsplit(" ", 1)[0] + "…"
    headings = [s.get("heading", "") for s in sections]
    return {
        "title": title,
        "meta_description": meta,
        "keyword": keyword,
        "keyword_in_title": keyword.lower() in title.lower() if keyword else False,
        "headings": headings,
        "headings_ok": bool(headings) and all(h.strip() for h in headings),
        "meta_length": len(meta),
        "meta_ok": 0 < len(meta) <= 160,
    }
