"""Tests for the Brand Builder (fetch_site.py + content_core/brand_extract.py
+ the MCP brand tools).

Fixtures are two fictional coffee sites under tests/fixtures/:
- dinhcao/  "Đỉnh Cao Coffee" — competitor voice: hype, superlatives,
  exclamation-heavy, first-person ~0, red/gold kit.
- som/      "Sớm Coffee" — the user's own calmer site: first-person
  (mình/bạn), restrained, green kit.
No network anywhere: fetch_site runs over file:// URLs.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))
sys.path.insert(0, str(PROJECT_DIR / "tests"))

import mcp_server as srv  # noqa: E402
from content_core.brand_extract import (build_brand_draft,  # noqa: E402
                                        compare_brand_voice,
                                        draft_to_brand_yaml,
                                        extract_brand_kit,
                                        extract_vocabulary,
                                        find_site_claims, site_corpus,
                                        suggest_voice_rules)
from content_core.linter import lint_section  # noqa: E402
from fetch_site import fetch_site, parse_page  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"

QUAL = {"tone_traits": [{"dimension": "directness", "position": "nói thẳng",
                         "do": "nêu fact trước lợi ích", "dont": "hype"}],
        "audience_guess": "dân văn phòng 25-35",
        "positioning": "cà phê rang mẻ nhỏ, minh bạch ngày rang",
        "differentiators": ["ghi ngày rang lên túi"]}


@pytest.fixture(scope="module")
def som_pages() -> list[dict]:
    return fetch_site(f"file://{FIXTURES}/som/index.html")["pages"]


@pytest.fixture(scope="module")
def dinhcao_pages() -> list[dict]:
    return fetch_site(f"file://{FIXTURES}/dinhcao/index.html")["pages"]


# -- fetch_site: discovery + kit evidence from raw HTML ---------------------

def test_fetch_site_discovers_priority_pages(som_pages, dinhcao_pages):
    som_urls = [p["url"] for p in som_pages]
    assert som_urls[0].endswith("som/index.html")
    assert any(u.endswith("som/gioi-thieu.html") for u in som_urls)  # priority path
    din_urls = [p["url"] for p in dinhcao_pages]
    assert any(u.endswith("dinhcao/san-pham.html") for u in din_urls)
    for p in som_pages + dinhcao_pages:
        assert p["text"] and p["title"] and "html_meta" in p


def test_parse_page_meta_extraction():
    html = (FIXTURES / "dinhcao" / "index.html").read_text(encoding="utf-8")
    page = parse_page("file:///site/index.html", html)
    meta = page["html_meta"]
    assert meta["og_site_name"] == "Đỉnh Cao Coffee"
    assert meta["theme_color"] == "#c8102e"          # normalized lowercase
    assert meta["logo_url"].endswith("logo-dinhcao.png")
    families = [f["family"] for f in meta["fonts"]]
    assert "Montserrat" in families and "Playfair Display" in families
    hexes = [c["hex"] for c in meta["colors"]]
    assert "#c8102e" in hexes and "#ffd700" in hexes
    assert any("Gold Blend" in h for h in page["headings"])


# -- brand kit ---------------------------------------------------------------

def test_extract_brand_kit_merges_pages(som_pages, dinhcao_pages):
    kit = extract_brand_kit(dinhcao_pages)
    assert kit["brand_name"]["value"] == "Đỉnh Cao Coffee"
    assert kit["brand_name"]["source_url"].endswith("index.html")
    assert kit["theme_color"]["value"] == "#c8102e"
    assert kit["logo_url"]["value"].endswith("logo-dinhcao.png")
    assert kit["colors"][0]["hex"] == "#c8102e"       # most frequent wins
    assert len(kit["colors"]) <= 6 and len(kit["fonts"]) <= 3
    assert kit["fonts"][0]["family"] == "Montserrat"

    som = extract_brand_kit(som_pages)
    assert som["brand_name"]["value"] == "Sớm Coffee"
    assert som["theme_color"]["value"] == "#3e6b48"
    assert som["fonts"][0]["family"] == "Be Vietnam Pro"


def test_kit_name_fallbacks():
    pages = [{"url": "https://cafe-abc.vn/", "title": "Trang Chủ | Cafe ABC",
              "text": "x", "html_meta": {}},
             {"url": "https://cafe-abc.vn/blog", "title": "Blog | Cafe ABC",
              "text": "y", "html_meta": {}}]
    # no og:site_name -> repeated title segment
    assert extract_brand_kit(pages)["brand_name"]["value"] == "Cafe ABC"
    pages[0]["title"] = pages[1]["title"] = "Hello"
    # no repeated segment either -> domain label
    assert extract_brand_kit(pages)["brand_name"]["value"] == "Cafe-abc"


# -- voice suggestions ---------------------------------------------------------

def test_suggest_voice_rules_pov_and_median(som_pages, dinhcao_pages):
    som = suggest_voice_rules(site_corpus(som_pages)["all_text"])
    assert som["pov"] == "first-person"               # mình/bạn voice detected
    assert som["reader_address"] == "bạn"
    lo, hi = som["sentence_rules"]["median_words_default"]
    assert 3 <= lo < hi <= 30                          # sane clamped range

    din = suggest_voice_rules(site_corpus(dinhcao_pages)["all_text"])
    assert din["pov"] == "third-person"
    assert din["reader_address"] is None
    # hype punctuation is a review NOTE, never a copied rule
    assert any("exclamation" in n for n in din["formatting_notes"])


# -- claims --------------------------------------------------------------------

def test_find_site_claims_flagged_with_source(dinhcao_pages, som_pages):
    claims = find_site_claims(dinhcao_pages)
    assert len(claims) >= 5
    assert all(c["risk"] == "needs_evidence" and c["source_url"] for c in claims)
    assert any("tốt nhất" in c["phrase"] for c in claims)
    assert find_site_claims(som_pages) == []           # the calm site makes none


# -- vocabulary ------------------------------------------------------------------

def test_extract_vocabulary_exact_names(som_pages, dinhcao_pages):
    din = extract_vocabulary(dinhcao_pages)
    assert din["exact"][0] == "Đỉnh Cao Coffee"        # brand name first, exact casing
    assert "Đỉnh Cao Gold Blend" in din["exact"]
    assert any(u["term"] == "cà phê" for u in din["use"])

    som = extract_vocabulary(som_pages)
    assert som["exact"][0] == "Sớm Coffee"
    assert "Sớm Arabica Cầu Đất" in som["exact"]


# -- drafts ------------------------------------------------------------------------

def test_own_site_draft_shape(som_pages):
    draft = build_brand_draft(som_pages, QUAL, "own_site")
    assert draft["brand"]["review_status"] == "draft"
    assert draft["brand"]["source_type"] == "own_site"
    assert draft["brand"]["id"] == "som-coffee"
    assert draft["voice"]["banned_terms"] == []        # cannot be inferred
    assert draft["voice"]["traits"] == QUAL["tone_traits"]
    # F3 (calibration fix round): a draft must NOT inherit the house
    # brand's prohibited_claims — prohibitions are per-brand decisions.
    assert draft["voice"]["prohibited_claims"] == []
    assert draft["claims_and_guardrails"]["prohibited_claims"] == []
    assert any("prohibited_claims" in n for n in draft["review_notes"])
    assert any("banned_terms" in n for n in draft["review_notes"])
    assert "competitor_warning" not in draft
    assert all("reference_only" not in e for e in draft["examples"])
    assert draft["examples"] and all(e["source_url"] for e in draft["examples"])


def test_own_site_draft_converts_to_loadable_yaml(som_pages):
    draft = build_brand_draft(som_pages, QUAL, "own_site")
    brand = yaml.safe_load(draft_to_brand_yaml(draft))
    # draft-only fields are gone
    assert "review_status" not in brand["brand"]
    assert "review_notes" not in brand
    assert "site_claims_to_review" not in brand["claims_and_guardrails"]
    # and the result drives the real linter without error
    violations, score = lint_section(
        "Mình rang cà phê vào sáng thứ Ba hàng tuần cho bạn. "
        "Một gói hạt có giá ghi rõ ngay trên bao bì của mình.", "blog", brand)
    assert isinstance(score, float) and violations is not None
    assert brand["voice"]["banned_terms"] == []
    assert brand["voice"]["sentence_rules"]["max_sentence_words"] == 25


def test_competitor_draft_reference_only_and_conversion_refused(dinhcao_pages):
    draft = build_brand_draft(dinhcao_pages, QUAL, "competitor")
    assert draft["brand"]["source_type"] == "competitor"
    assert draft["competitor_warning"]
    assert draft["examples"] and all(e["reference_only"] is True
                                     for e in draft["examples"])
    assert len(draft["claims_and_guardrails"]["site_claims_to_review"]) >= 5
    with pytest.raises(ValueError, match="reference_only"):
        draft_to_brand_yaml(draft)
    # user replaces the passages with their own words -> conversion opens
    for e in draft["examples"]:
        e.pop("reference_only")
    assert yaml.safe_load(draft_to_brand_yaml(draft))["brand"]["name"] == "Đỉnh Cao Coffee"


def test_build_draft_rejects_bad_source_type(som_pages):
    with pytest.raises(ValueError):
        build_brand_draft(som_pages, QUAL, "stolen")


# -- compare ------------------------------------------------------------------------

def test_compare_brand_voice_finds_real_gaps(som_pages, dinhcao_pages):
    out = compare_brand_voice(som_pages, dinhcao_pages)
    assert out["labels"] == {"a": "Sớm Coffee", "b": "Đỉnh Cao Coffee"}
    assert out["metrics"]["first_person_rate"]["a"] > 3
    assert out["metrics"]["first_person_rate"]["b"] == 0
    assert out["claims"]["a"] == 0 and out["claims"]["b"] >= 5
    assert out["vocabulary"]["a_only"] and out["vocabulary"]["b_only"]
    gaps = " ".join(out["gaps"])
    assert "first-person" in gaps
    assert "restrained" in gaps                        # claims gap computed
    assert "exclamation" in gaps
    assert out["brand_kit"]["a"]["theme_color"]["value"] == "#3e6b48"
    assert out["brand_kit"]["b"]["theme_color"]["value"] == "#c8102e"


# -- MCP layer ------------------------------------------------------------------------

EXPECTED_NEW_TOOLS = {"analyze_site_brand", "save_brand_draft",
                      "get_brand_draft", "compare_brand_voice"}


def test_mcp_tool_list_now_29():
    # (Name kept from the 29-tool round; the count is now 67 after the
    # core-completion round added get_facts/save_facts, the images
    # round added the 8 image tools, core-completion round 2
    # added the 21 research-pack / corpus / taste / draft-admin /
    # backup tools, P3 added the 3 WordPress exporter tools, the
    # L5 closure added clear_corpus_choice, and the quality-uplift
    # round added score_section_variants + sync_wordpress_edits.)
    assert EXPECTED_NEW_TOOLS <= set(srv.TOOL_NAMES)
    assert len(srv.TOOL_NAMES) == 67
    for name in EXPECTED_NEW_TOOLS:
        assert callable(getattr(srv, name)), name


def test_mcp_brand_roundtrip(tmp_path, monkeypatch, som_pages, dinhcao_pages):
    monkeypatch.setenv("CONTENT_CORE_BRAND_DRAFTS_DIR", str(tmp_path / "brand_drafts"))

    analysis = srv.analyze_site_brand(dinhcao_pages)
    assert analysis["brand_kit"]["brand_name"]["value"] == "Đỉnh Cao Coffee"
    assert len(analysis["site_claims"]) >= 5
    assert analysis["warnings"]

    draft = srv.save_brand_draft(som_pages, QUAL, "own_site")
    assert draft["brand"]["id"] == "som-coffee"
    assert srv.get_brand_draft("som-coffee")["brand"]["name"] == "Sớm Coffee"
    listing = srv.get_brand_draft()
    assert any(d["id"] == "som-coffee" for d in listing["drafts"])
    assert srv.get_brand_draft("khong-co")["error"]

    comp = srv.save_brand_draft(dinhcao_pages, QUAL, "competitor")
    assert comp["competitor_warning"]

    out = srv.compare_brand_voice(som_pages, dinhcao_pages)
    assert out["gaps"]
