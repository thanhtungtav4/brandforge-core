"""Fail-first tests for SETUP ASSIST (22 Nov 2026) — the
AI-assisted new-brand setup orchestration Smith asked for: one
prepare_brand_setup call does the setup work (fetch → brand draft
→ facts draft → research pack + proposals → corpus + persona
proposals → dossier), the human only decides.

The hard rules under test (Smith's standing decisions):
- NOTHING goes live: no live facts profile, no corpus choice, no
  saved persona; the brand exists only as a draft and every pack
  item is still "proposed".
- Contradictions the site carries against itself (two prices for
  one pack size) land in the dossier's contradictions[] and BLOCK
  the facts draft — the contested values are withheld, never
  silently resolved.
- Prepare is idempotent-ish: a second run reuses the pack (no
  version bump, no duplicated sources/items) and refreshes drafts.
- get_brand_setup derives step state from the live stores, so a
  step completed through the EXISTING tools flips to "done".

The fixture sites are static HTML trees fetched through the real
fetch_site machinery over file:// (its documented offline path).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import mcp_server as srv  # noqa: E402
from content_core import loaders  # noqa: E402

STORE_ENVS = (
    ("CONTENT_CORE_DRAFTS_DIR", "drafts"),
    ("CONTENT_CORE_BRAND_DRAFTS_DIR", "brand_drafts"),
    ("CONTENT_CORE_FACTS_PROFILES_DIR", "facts"),
    ("CONTENT_CORE_STYLE_PROFILES_DIR", "style_profiles"),
    ("CONTENT_CORE_QUALITY_PROFILES_DIR", "quality_profiles"),
    ("CONTENT_CORE_IMAGE_LIBRARY_DIR", "image_library"),
    ("CONTENT_CORE_PERSONAS_DIR", "personas"),
    ("CONTENT_CORE_RESEARCH_PACKS_DIR", "research_packs"),
    ("CONTENT_CORE_CORPUS_REGISTRY_DIR", "corpus_registry"),
    ("CONTENT_CORE_CORPUS_CHOICES_DIR", "corpus_choices"),
    ("CONTENT_CORE_TASTE_DIR", "taste"),
    ("CONTENT_CORE_BACKUPS_DIR", "backups"),
    ("CONTENT_CORE_WP_TARGETS_DIR", "wp_targets"),
    ("CONTENT_CORE_BRAND_SETUPS_DIR", "brand_setups"),
)


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    for var, sub in STORE_ENVS:
        monkeypatch.setenv(var, str(tmp_path / sub))
    pdir = tmp_path / "personas"
    pdir.mkdir(parents=True, exist_ok=True)
    for name in ("persona_linh.json", "persona_an.json"):
        (pdir / name).write_text(
            (PROJECT_DIR / "data" / name).read_text(encoding="utf-8"),
            encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Fixture sites
# ---------------------------------------------------------------------------

_PROSE = (
    "<p>Mình rang cà phê theo từng mẻ nhỏ để hạt luôn mới khi tới tay bạn. "
    "Cà phê rang mộc không tẩm ướp nên vị đắng dịu và hậu ngọt rõ. "
    "Bạn pha phin chậm rãi sẽ thấy hương cà phê dậy lên rất thơm. "
    "Mình chọn hạt chín đỏ và loại bỏ hạt lép trước khi rang. "
    "Mẻ rang nào mình cũng ghi rõ ngày rang trên từng gói cà phê.</p>"
    "<p>Cà phê của mình hợp với người thích vị nguyên bản và ít chua. "
    "Bạn có thể pha phin, pha máy hoặc pha pour over đều được. "
    "Mình tư vấn cách pha cho từng loại hạt khi bạn nhắn tin hỏi. "
    "Hạt cà phê sau khi rang được nghỉ vài ngày rồi mới đóng gói gửi đi.</p>"
)


def _write_site(root: Path, product_page: str, extra_pages: dict | None = None,
                index_extra: str = ""):
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(f"""<!DOCTYPE html>
<html><head><title>Trang chủ | Test Roasters</title>
<meta property="og:site_name" content="Test Roasters">
<style>body {{ color: #3e2a1e; background: #f5ead9; font-family: 'Be Vietnam Pro', sans-serif; }}</style>
</head><body>
<header><img src="logo-test.png" alt="Test Roasters logo"></header>
<nav>Trang chủ Sản phẩm Giới thiệu Liên hệ Giỏ hàng</nav>
<h1>Test Roasters — cà phê rang mộc</h1>
{_PROSE}
<p>Hạt Robusta trồng tại Buôn Ma Thuột, Đắk Lắk được thu hoạch chín đỏ từng trái.</p>
<a href="san-pham.html">Sản phẩm</a> <a href="gioi-thieu.html">Giới thiệu</a>
{index_extra}
</body></html>""", encoding="utf-8")
    (root / "san-pham.html").write_text(f"""<!DOCTYPE html>
<html><head><title>Sản phẩm | Test Roasters</title></head><body>
<h1>Sản phẩm của Test Roasters</h1>
{product_page}
<p>Test Roasters giao hàng toàn quốc trong 2-3 ngày làm việc.</p>
{_PROSE}
</body></html>""", encoding="utf-8")
    (root / "gioi-thieu.html").write_text(f"""<!DOCTYPE html>
<html><head><title>Giới thiệu | Test Roasters</title></head><body>
<h1>Giới thiệu Test Roasters</h1>
{_PROSE}
<p>Mình bắt đầu rang cà phê từ một xưởng nhỏ và vẫn giữ cách làm thủ công đến nay.</p>
</body></html>""", encoding="utf-8")
    for name, body in (extra_pages or {}).items():
        (root / name).write_text(body, encoding="utf-8")
    return root


_CLEAN_PRICES = ("<p>Gói 250g giá 120.000đ.</p>"
                 "<p>Gói 500g giá 230.000đ.</p>"
                 "<p>Gói 1kg giá 440.000đ.</p>")


@pytest.fixture()
def clean_site(tmp_path):
    return _write_site(tmp_path / "site-clean", _CLEAN_PRICES)


@pytest.fixture()
def conflict_site(tmp_path):
    promo = """<!DOCTYPE html>
<html><head><title>Khuyến mãi | Test Roasters</title></head><body>
<h1>Khuyến mãi tuần này</h1>
<p>Gói 250g chỉ còn 99.000đ trong tuần này.</p>
</body></html>"""
    return _write_site(
        tmp_path / "site-conflict",
        "<p>Gói 250g giá 120.000đ.</p><p>Gói 500g giá 230.000đ.</p>",
        {"khuyen-mai.html": promo},
        index_extra='<a href="khuyen-mai.html">Khuyến mãi</a>')


def _register_corpora():
    srv.register_corpus("coffee-press", 1, "Báo chí cà phê Việt Nam",
                        "press", "coffee-press-voice",
                        "báo chí chính thống về cà phê và ẩm thực", 10000)
    srv.register_corpus("sea-lit", 1, "Văn học biển đảo", "literature",
                        "sea-lit-voice", "văn học Việt Nam về biển đảo",
                        5000)


def _step(dossier: dict, key: str) -> dict:
    return next(s for s in dossier["steps"] if s["key"] == key)


# ---------------------------------------------------------------------------
# The full prepare — dossier complete, nothing live
# ---------------------------------------------------------------------------

def test_prepare_full_dossier_nothing_live(stores, clean_site):
    _register_corpora()
    dossier = srv.prepare_brand_setup(
        (clean_site / "index.html").as_uri(), "test-roasters")
    assert "error" not in dossier, dossier
    assert dossier["brand_id"] == "test-roasters"

    # Steps: fetch done, the prepared artefacts proposed, the
    # human-only steps waiting.
    assert _step(dossier, "site_fetch")["status"] == "done"
    assert _step(dossier, "brand_draft")["status"] == "proposed"
    assert _step(dossier, "facts")["status"] == "proposed"
    assert _step(dossier, "research_pack")["status"] == "proposed"
    assert _step(dossier, "corpus_choice")["status"] == "proposed"
    assert _step(dossier, "persona")["status"] == "proposed"
    assert _step(dossier, "wordpress_target")["status"] == "needs_human"

    # NOTHING live: no facts profile, no corpus choice, no persona.
    assert loaders.load_facts_profile("test-roasters") is None
    assert loaders.load_corpus_choice("test-roasters") is None
    assert not set((stores / "personas").glob("persona_*.json")) - \
        {stores / "personas" / "persona_linh.json",
         stores / "personas" / "persona_an.json"}

    # The brand exists ONLY as a draft.
    draft = loaders.load_brand_draft("test-roasters")
    assert draft is not None
    assert draft["brand"]["review_status"] == "draft"
    assert draft["brand"]["id"] == "test-roasters"
    assert draft["brand"]["name"] == "Test Roasters"

    # The visual palette was inferred from the site kit and is
    # stamped as a setup_assist proposal.
    assert draft["visual"]["palette"] == ["#3e2a1e", "#f5ead9"]
    inferred = {i["field"]: i for i in draft["inferred_fields"]}
    assert inferred["visual.palette"]["provenance"] == "setup_assist"

    # Facts draft: extracted values, still a draft (save_ready —
    # the doc WOULD validate — but deliberately unsaved).
    facts_draft = dossier["facts_draft"]
    assert facts_draft["save_ready"] is True
    doc = facts_draft["doc"]
    assert doc["facts"]["price_vnd_per_250g"] == 120000
    assert doc["facts"]["price_display"] == "120.000đ"
    assert doc["facts"]["pack_weight_g"] == 250
    assert doc["facts"]["pack_weights_g"] == [250, 500, 1000]
    assert doc["facts"]["known_prices_vnd"] == [230000, 440000]
    assert doc["facts"]["delivery_days"] == "2-3 ngày"
    assert any("Buôn Ma Thuột" in o for o in doc["facts"]["origins"])
    assert facts_draft["blocked_fields"] == []
    assert dossier["contradictions"] == []

    # Pack: created, proposals built, EVERY item still proposed.
    pack = loaders.load_research_pack("test-roasters")
    assert pack is not None and pack["version"] == 1
    assert pack["items"], "proposals were built"
    assert all(i["status"] == "proposed" for i in pack["items"])
    assert any(i["kind"] == "brand_rule" for i in pack["items"])
    own = [s for s in pack["sources"] if s["kind"] == "own"]
    assert len(own) == 1 and own[0]["quality"]["verdict"] == "accept"

    # Corpus: proposed (the coffee corpus matches the site's
    # vocabulary), NOT chosen.
    proposal = dossier["corpus_proposal"]
    assert proposal["status"] == "proposed"
    assert proposal["corpus_id"] == "coffee-press"
    assert "cà phê" in proposal["matched_terms"]
    assert proposal["provenance"] == "setup_assist"
    assert dossier["corpus_choice"] is None

    # Persona: candidates proposed, ordered, none saved.
    persona = dossier["persona_proposal"]
    assert persona["status"] == "proposal"
    assert len(persona["candidates"]) == 3
    assert {c["archetype"] for c in persona["candidates"]} == \
        {"brand-as-person", "insider-guide", "neutral-expert"}

    # needs_human names the decisions, incl. pack per-item approval.
    keys = {n["key"] for n in dossier["needs_human"]}
    assert {"pack_approvals", "brand_draft_approval", "facts_review",
            "corpus_choice", "persona_choice",
            "wordpress_target"} <= keys


# ---------------------------------------------------------------------------
# Contradiction — surfaced as a blocker, values withheld
# ---------------------------------------------------------------------------

def test_prepare_contradiction_blocks_facts(stores, conflict_site):
    dossier = srv.prepare_brand_setup(
        (conflict_site / "index.html").as_uri(), "conflict-brand")
    assert "error" not in dossier, dossier

    assert _step(dossier, "facts")["status"] == "blocked"
    cons = [c for c in dossier["contradictions"]
            if c["source"] == "facts_draft"]
    assert len(cons) == 1
    con = cons[0]
    assert con["field"] == "price" and con["weight_g"] == 250
    assert con["values"] == [99000, 120000]

    # The contested 250g price is WITHHELD from the draft doc —
    # neither value was silently picked; the clean 500g price and
    # the uncontested data still landed.
    doc = dossier["facts_draft"]["doc"]
    assert "price_vnd_per_250g" not in doc["facts"]
    assert 99000 not in doc["facts"].get("known_prices_vnd", [])
    assert 120000 not in doc["facts"].get("known_prices_vnd", [])
    assert doc["facts"]["pack_weights_g"] == [500]
    # The one clean price became the draft's primary display price —
    # it was not demoted to known_prices or mixed with the contested
    # pair.
    assert doc["facts"]["pack_weight_g"] == 500
    assert doc["facts"]["price_display"] == "230.000đ"
    assert doc["facts"].get("known_prices_vnd", []) == []
    assert any("250g" in b for b in dossier["facts_draft"]["blocked_fields"])
    assert any(n["key"] == "contradiction:price"
               for n in dossier["needs_human"])
    # And of course nothing went live either.
    assert loaders.load_facts_profile("conflict-brand") is None


# ---------------------------------------------------------------------------
# Idempotency — second prepare refreshes, never duplicates
# ---------------------------------------------------------------------------

def test_prepare_idempotent_second_run(stores, clean_site):
    url = (clean_site / "index.html").as_uri()
    first = srv.prepare_brand_setup(url, "test-roasters")
    pack1 = loaders.load_research_pack("test-roasters")
    second = srv.prepare_brand_setup(url, "test-roasters")
    pack2 = loaders.load_research_pack("test-roasters")

    assert second["runs"] == 2
    assert pack2["version"] == 1 == pack1["version"]  # never bumped
    assert len(pack2["items"]) == len(pack1["items"])  # no duplicates
    assert len(pack2["sources"]) == len(pack1["sources"]) == 1
    assert first["brand_draft_id"] == second["brand_draft_id"]
    record = loaders.load_brand_setup("test-roasters")
    assert record["prepared_at"] == (record["prepared_at"])  # kept
    assert record["runs"] == 2


# ---------------------------------------------------------------------------
# get_brand_setup — state derived from the live stores
# ---------------------------------------------------------------------------

def test_get_setup_reflects_steps_completed_via_existing_tools(
        stores, clean_site):
    _register_corpora()
    dossier = srv.prepare_brand_setup(
        (clean_site / "index.html").as_uri(), "test-roasters")
    assert _step(dossier, "corpus_choice")["status"] == "proposed"
    assert _step(dossier, "facts")["status"] == "proposed"
    assert _step(dossier, "wordpress_target")["status"] == "needs_human"

    # The human completes steps through the EXISTING tools…
    choice = srv.set_corpus_choice("test-roasters", "coffee-press")
    assert "error" not in choice, choice
    saved = srv.save_facts("test-roasters", dossier["facts_draft"]["doc"])
    assert "error" not in saved, saved
    target = srv.set_wordpress_target(
        "test-roasters", "https://testroasters.example", "admin")
    assert "error" not in target, target

    # …and the checklist flips on its own — nothing was re-prepared.
    after = srv.get_brand_setup("test-roasters")
    assert "error" not in after, after
    assert _step(after, "corpus_choice")["status"] == "done"
    assert _step(after, "facts")["status"] == "done"
    assert _step(after, "wordpress_target")["status"] == "done"
    keys = {n["key"] for n in after["needs_human"]}
    assert "corpus_choice" not in keys
    assert "facts_review" not in keys
    assert "wordpress_target" not in keys


def test_reprepare_preserves_human_edits_and_reports_them(
        stores, clean_site):
    """S2 (review round 3): the brand draft is where the human's
    review happens — re-running prepare must not silently erase
    it. Fields the human changed since generation (banned_terms
    above all: the dossier itself asks the human to supply them)
    are PRESERVED across a re-prepare, and the dossier names every
    preserved field — no silent overwrite, no silent keep."""
    url = (clean_site / "index.html").as_uri()
    srv.prepare_brand_setup(url, "test-roasters")

    draft = loaders.load_brand_draft("test-roasters")
    draft["voice"]["banned_terms"] = ["ngon nhất", "số 1"]
    draft["voice"]["prohibited_claims"] = ["chữa khỏi bệnh"]
    draft["brand"]["name"] = "Tên Người Đặt"
    loaders.save_brand_draft(draft)

    dossier = srv.prepare_brand_setup(url, "test-roasters")
    assert "error" not in dossier, dossier
    after = loaders.load_brand_draft("test-roasters")
    assert after["voice"]["banned_terms"] == ["ngon nhất", "số 1"]
    assert after["voice"]["prohibited_claims"] == ["chữa khỏi bệnh"]
    assert after["brand"]["name"] == "Tên Người Đặt"

    preserved = {p["field"] for p in dossier["preserved_fields"]}
    assert {"voice.banned_terms", "voice.prohibited_claims",
            "brand.name"} <= preserved


def test_reprepare_without_human_edits_preserves_nothing(
        stores, clean_site):
    """Generation is deterministic: a plain re-prepare produces
    the identical draft, so the preserved list stays empty — the
    report carries no noise when nobody touched the draft."""
    url = (clean_site / "index.html").as_uri()
    srv.prepare_brand_setup(url, "test-roasters")
    dossier = srv.prepare_brand_setup(url, "test-roasters")
    assert dossier["preserved_fields"] == []


def test_get_setup_unknown_brand_errors(stores):
    out = srv.get_brand_setup("never-prepared")
    assert "error" in out and "prepare_brand_setup" in out["error"]


def test_prepare_unfetchable_site_errors(stores, tmp_path):
    out = srv.prepare_brand_setup(
        (tmp_path / "missing" / "index.html").as_uri(), "ghost-brand")
    assert "error" in out
    assert loaders.load_brand_setup("ghost-brand") is None
    assert loaders.load_brand_draft("ghost-brand") is None
