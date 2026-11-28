"""Per-brand glossaries (daily proposal 3, 2026-11-28, Smith-approved).

Before this round the glossary split was: the shipped
data/domain_glossary.json (Mộc Brew's own) + GENERIC_GLOSSARY, the
brand-neutral fallback in craft.py for every other brand (calibration
F8). The F8 note named per-brand glossaries "the long-term answer";
this round builds that store:

- loaders.load_brand_glossary reads data/glossaries/<brand-id>.json
  (tests point CONTENT_CORE_GLOSSARIES_DIR at a tmp dir);
- craft.resolve_glossary MERGES a brand's file over GENERIC_GLOSSARY
  — the generic weak words and sensory terms keep judging the brand
  (F8 safety), the brand's own groups/terms are added;
- the get_domain_glossary MCP tool takes an optional brand_id so the
  writer's lookup path resolves the same glossary the checker uses.

First content: the mambagiaokhoe dish glossary — mắm dish names with
their dish pairings, distilled from the verified research-pack item
(item-f7b4c100cc, vietnambyregion.com Mekong mắm guide, 11/2026).
The dish group is NOT a sensory group, so merging it changes no
existing craft score: weak-word and sensory vocabularies are the
generic ones, exactly as before.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import mcp_server as srv  # noqa: E402
from content_core import backup, craft  # noqa: E402
from content_core.craft import (GENERIC_GLOSSARY, glossary_group,  # noqa: E402
                                resolve_glossary, sensory_terms,
                                weak_word_entries)
from content_core.loaders import load_brand_glossary  # noqa: E402

MAM_TERMS = ["mắm chưng", "lẩu mắm", "mắm Thái", "mắm bò hóc",
             "cá linh non mùa nước nổi"]
MAM_GROUP = "mon-mam"


def _terms(doc: dict) -> set[str]:
    return {t["term"] for g in doc.get("groups", {}).values()
            for t in g.get("terms", [])}


# ---------------------------------------------------------------------------
# The store + the shipped mambagiaokhoe file
# ---------------------------------------------------------------------------

def test_shipped_mambagiaokhoe_glossary_loads_with_all_terms():
    doc = load_brand_glossary("mambagiaokhoe")
    assert doc is not None
    assert doc["brand_id"] == "mambagiaokhoe"
    assert set(MAM_TERMS) <= _terms(doc)


def test_mam_term_notes_carry_pairings_and_source():
    doc = load_brand_glossary("mambagiaokhoe")
    by_term = {t["term"]: t["note"]
               for g in doc["groups"].values() for t in g["terms"]}
    # Each pairing from the verified research item rides in the note
    # (the entry schema is {term, note} — no separate source field,
    # so the source is named in the note itself).
    assert "cá lóc" in by_term["mắm chưng"]
    assert "cá linh" in by_term["lẩu mắm"] and "cá sặc" in by_term["lẩu mắm"]
    assert "bún" in by_term["mắm Thái"] and "thịt luộc" in by_term["mắm Thái"]
    assert "bún nước lèo" in by_term["mắm bò hóc"]
    assert "6 tháng" in by_term["mắm bò hóc"]
    assert "lẩu mắm" in by_term["cá linh non mùa nước nổi"]
    for term in MAM_TERMS:
        assert "vietnambyregion" in by_term[term], term


def test_load_brand_glossary_missing_brand_is_none():
    assert load_brand_glossary("no-such-brand") is None
    assert load_brand_glossary("") is None
    assert load_brand_glossary(None) is None


def test_glossaries_store_registered_for_backup():
    names = [name for name, _path, _rec in backup._store_sources()]
    assert "glossaries" in names


# ---------------------------------------------------------------------------
# resolve_glossary merge semantics
# ---------------------------------------------------------------------------

def test_resolve_glossary_mambagiaokhoe_merges_generic_base():
    resolved = resolve_glossary(brand_id="mambagiaokhoe")
    assert resolved["brand_id"] == "mambagiaokhoe"
    # The brand's dish group is there, via the same lookup the
    # writer/checker use.
    group = glossary_group(MAM_GROUP, glossary=resolved)
    assert "error" not in group
    assert set(MAM_TERMS) <= {t["term"] for t in group["terms"]}
    # F8 safety preserved: the generic weak words and sensory terms
    # still judge this brand — the brand file EXTENDS the generic set.
    generic_weak = {w["word"] for w in weak_word_entries(GENERIC_GLOSSARY)}
    assert generic_weak <= {w["word"] for w in weak_word_entries(resolved)}
    assert set(sensory_terms(GENERIC_GLOSSARY)) <= set(sensory_terms(resolved))
    # The dish group is vocabulary, not sensory evidence: it must NOT
    # join the sensory groups (existing craft scores stay identical).
    assert MAM_GROUP not in resolved.get("sensory_groups", [])


def test_resolve_glossary_brand_without_file_stays_generic():
    resolved = resolve_glossary(brand_id="cao-nguyen-moc")
    assert resolved.get("generic") is True
    assert MAM_GROUP not in resolved.get("groups", {})


def test_resolve_glossary_default_and_shipped_brand_unchanged():
    assert resolve_glossary()["brand_id"] == "moc-brew"
    assert resolve_glossary(brand_id="moc-brew")["brand_id"] == "moc-brew"
    # An explicit glossary still wins over everything.
    explicit = {"brand_id": "x", "groups": {}, "weak_words": [],
                "sensory_groups": []}
    assert resolve_glossary(explicit, brand_id="mambagiaokhoe") is explicit


def test_per_brand_glossary_store_reads_tmp_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_CORE_GLOSSARIES_DIR", str(tmp_path))
    doc = {"brand_id": "testbrand", "name": "Test glossary",
           "groups": {"dishes": {"label_vi": "Món",
                                 "terms": [{"term": "món thử",
                                            "note": "ghi chú thử"}]}},
           "sensory_groups": [], "weak_words": []}
    (tmp_path / "testbrand.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    resolved = resolve_glossary(brand_id="testbrand")
    assert "món thử" in _terms(resolved)
    # …and the shipped brand is NOT visible through the tmp store.
    assert load_brand_glossary("mambagiaokhoe") is None


# ---------------------------------------------------------------------------
# The lookup paths: MCP tool (writer) + craft_check (checker)
# ---------------------------------------------------------------------------

def test_get_domain_glossary_tool_resolves_brand():
    out = srv.get_domain_glossary(MAM_GROUP, brand_id="mambagiaokhoe")
    assert "error" not in out, out
    assert set(MAM_TERMS) <= {t["term"] for t in out["terms"]}
    # Whole-glossary lookup for the brand carries the group too.
    whole = srv.get_domain_glossary("", brand_id="mambagiaokhoe")
    assert MAM_GROUP in whole["groups"]


def test_get_domain_glossary_tool_default_unchanged():
    out = srv.get_domain_glossary("aroma")
    assert "error" not in out
    assert out["group"] == "aroma"


def test_craft_check_mambagiaokhoe_still_flags_generic_weak_words():
    text = ("Món mắm này thật sự tuyệt vời và hoàn hảo vô cùng, ai ăn "
            "cũng mê ngay từ miếng đầu tiên trong bữa cơm gia đình "
            "sum họp đầm ấm bên nhau mỗi buổi chiều quê yên bình.")
    out = craft.craft_check(text, "blog", brand_id="mambagiaokhoe")
    weak = [v for v in out["violations"] if v["type"] == "weak_word"]
    assert {v["detail"] for v in weak} >= {"tuyệt vời", "hoàn hảo"}
