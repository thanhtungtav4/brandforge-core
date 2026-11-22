"""Setup Assist — AI-assisted new-brand setup, orchestrated.

Smith's request (22 Nov 2026): the AI should do most of the work of
setting up a NEW brand; the human only decides. Every piece already
existed as a separate tool the human had to drive in sequence —
Brand Builder (fetch → draft), facts profiles, research packs,
corpus registry, persona/visual in the brand schema, WordPress
targets. This module is the ORCHESTRATION over those pieces: one
`prepare_brand_setup` call produces a SETUP DOSSIER — everything
prepared as drafts/proposals plus a checklist of exactly what still
needs a human decision.

HARD RULES (Smith's standing decisions — the tests pin them):
- NOTHING goes live without the existing approval paths. The brand
  lands as a brand DRAFT (review_status="draft"); the facts land as
  a FACTS DRAFT inside the setup record, never in the live facts
  profile; research-pack items are created only through the pack's
  own proposal path (status "proposed" — per-item manual approval
  only, no bulk approve, no auto-apply); the corpus is PROPOSED,
  never chosen; the persona is proposed as candidates, never saved.
- Contradictions found during setup (two prices for one pack size,
  two delivery promises) are surfaced as BLOCKERS in the dossier
  and the contested values are WITHHELD from the facts draft —
  never silently resolved, never averaged, never first-wins.
- Inferred fields carry provenance "setup_assist" wherever they
  land (the brand draft's `inferred_fields`, the proposals).
- A re-prepare NEVER silently overwrites the human's review
  (review round 3, S2): generation is deterministic, so any field
  in which the stored brand draft differs from the fresh
  generation is a human edit (banned_terms above all — the field
  the dossier itself asks the human to supply). Differing fields
  are PRESERVED from the stored draft, and the dossier's
  `preserved_fields` names every one of them — no silent
  overwrite, no silent keep.

Division of labour mirrors the rest of the core: this module is
deterministic (fetch, measure, extract, score); the qualitative
brand schema stays the model's job through the existing Brand
Builder flow; every approval stays the human's job through the
existing tools. The checklist (`build_setup_dossier`) derives step
state from the LIVE stores wherever possible, so completing a step
through the existing tools (set_corpus_choice, apply_pack,
save_facts, set_wordpress_target…) flips it here automatically —
the dossier cannot drift from reality.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import loaders, research
from .brand_extract import build_brand_draft, site_corpus
from .fact_check import (DELIVERY, DELIVERY_RANGE_WORDS, DELIVERY_WITHIN,
                         SINGLE_DELIVERY, WEIGHT, _has_delivery_context,
                         _weight_grams, find_money_amounts)
from .linter import normalize_for_match, split_sentences

#: Provenance stamped on everything this module infers or proposes.
PROVENANCE = "setup_assist"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Fetch — the one place setup assist touches the network
# ---------------------------------------------------------------------------

def _default_fetcher(site_url: str, max_pages: int) -> dict:
    """Fetch a site with the project's fetch_site machinery
    (fetch_site.py, the Brand Builder's CLI helper — stdlib only,
    sitemap + link discovery, file:// supported). Imported lazily:
    fetch_site.py lives at the project root, outside the package,
    and the rest of the core stays offline by design."""
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import fetch_site
    return fetch_site.fetch_site(site_url, max_pages=max_pages)


# ---------------------------------------------------------------------------
# Facts draft extraction — prices / weights / origins / delivery
# ---------------------------------------------------------------------------
# Deterministic extraction over the fetched pages, reusing the fact
# checker's own machinery (find_money_amounts, WEIGHT,
# _has_delivery_context + the DELIVERY patterns) so the draft speaks
# the same numeric language the gate will later check against.
# Association rule (documented, deliberately conservative):
# money and weights are paired WITHIN one sentence — one weight in
# the sentence lends itself to every amount in it; equal counts pair
# in order of appearance ("250g 89.000đ, 500g 179.000đ" and the
# reversed order both work); anything else leaves the amount
# unassociated rather than guessing. A weight carrying two distinct
# prices is a CONTRADICTION (on a real shop that is either a stale
# page or two different products at one size — a human decides
# which; the draft withholds the contested prices entirely).

def _sentence_price_pairs(sentence: str) -> list[tuple[int, int | None]]:
    """(price_vnd, weight_g|None) pairs from one sentence."""
    amounts = find_money_amounts(sentence)
    if not amounts:
        return []
    weights = [(m.start(), round(_weight_grams(m)))
               for m in WEIGHT.finditer(sentence)]
    pairs: list[tuple[int, int | None]] = []
    if len(weights) == 1:
        weight = weights[0][1]
        pairs = [(value, weight) for _s, _e, value, _raw in amounts]
    elif len(weights) == len(amounts) and len(amounts) > 1:
        ordered_weights = [w for _pos, w in sorted(weights)]
        ordered_amounts = sorted(amounts, key=lambda a: a[0])
        pairs = [(value, weight) for (_s, _e, value, _raw), weight
                 in zip(ordered_amounts, ordered_weights)]
    else:
        pairs = [(value, None) for _s, _e, value, _raw in amounts]
    return pairs


def _price_observations(pages: list[dict]) -> list[dict]:
    """Aggregate price observations across pages: one record per
    (price, weight) pair with every source URL and one example
    sentence as evidence."""
    by_pair: dict[tuple[int, int | None], dict] = {}
    order: list[tuple[int, int | None]] = []
    for page in pages:
        url = page.get("url", "")
        for sentence in split_sentences(page.get("text", "")):
            for price, weight in _sentence_price_pairs(sentence):
                key = (price, weight)
                if key not in by_pair:
                    by_pair[key] = {"price_vnd": price, "weight_g": weight,
                                    "source_urls": [], "example": sentence.strip()}
                    order.append(key)
                if url and url not in by_pair[key]["source_urls"]:
                    by_pair[key]["source_urls"].append(url)
    return [by_pair[key] for key in order]


#: Origin markers: the phrase that introduces where a product comes
#: from, followed by the capitalised place/product name itself
#: ("trồng tại Buôn Ma Thuột", "Nguồn gốc hạt: Robusta Đức Cơ").
_ORIGIN_MARKER_RE = re.compile(
    r"(?:trồng\s+tại|trồng\s+ở|nguồn\s+gốc(?:\s+hạt)?\s*[:\-]?|đến\s+từ|"
    r"xuất\s+xứ(?:\s+từ)?|sản\s+xuất\s+tại|thu\s+hoạch\s+tại)\s+",
    re.IGNORECASE)
_CAP_RUN_RE = re.compile(r"[A-ZÀ-Ỹ][\wà-ỹ]*(?:\s+[A-ZÀ-Ỹ][\wà-ỹ]*)*")


def _origin_candidates(pages: list[dict]) -> list[dict]:
    """Origin candidates: capitalised runs right after an origin
    marker, each with its source URL + sentence. Candidates, not
    conclusions — they land in a DRAFT the human reviews."""
    found: list[dict] = []
    seen: set[str] = set()
    for page in pages:
        for sentence in split_sentences(page.get("text", "")):
            for marker in _ORIGIN_MARKER_RE.finditer(sentence):
                run = _CAP_RUN_RE.match(sentence[marker.end():].lstrip())
                if not run:
                    continue
                name = " ".join(run.group(0).split())
                if len(name) < 3 or name.casefold() in seen:
                    continue
                seen.add(name.casefold())
                found.append({"name": name, "source_url": page.get("url", ""),
                              "example": sentence.strip()})
    return found[:6]


def _delivery_candidates(pages: list[dict]) -> list[dict]:
    """Distinct delivery promises, normalised to the facts doc's
    string shapes ("2-3 ngày" for a range, "trong 3 ngày" for a
    single bound), each with a source URL. Only sentences carrying
    delivery context count (the fact checker's own gate), so
    shelf-life prose is not a delivery promise."""
    found: dict[str, dict] = {}
    for page in pages:
        for sentence in split_sentences(page.get("text", "")):
            if not _has_delivery_context(sentence):
                continue
            value = None
            m = DELIVERY.search(sentence) or DELIVERY_RANGE_WORDS.search(sentence)
            if m:
                value = f"{m.group(1)}-{m.group(2)} ngày"
            else:
                m = DELIVERY_WITHIN.search(sentence) \
                    or SINGLE_DELIVERY.search(sentence)
                if m:
                    value = f"trong {m.group(1)} ngày"
            if value and value not in found:
                found[value] = {"value": value,
                                "source_url": page.get("url", ""),
                                "example": sentence.strip()}
    return list(found.values())


def extract_facts_draft(pages: list[dict], brand_id: str) -> dict:
    """Build the FACTS DRAFT for a brand from fetched pages.

    Returns {doc, observations, blocked_fields, contradictions,
    origin_candidates, delivery_candidates, save_ready,
    validation_error, notes}. `doc` is shaped exactly like a facts
    profile document (so a human can save it via save_facts after
    review) but it is NOT saved anywhere live by this module.
    Contested values are withheld from `doc` and named in
    blocked_fields + contradictions — the draft is incomplete on
    purpose where the site disagrees with itself."""
    observations = _price_observations(pages)
    by_weight: dict[int, set[int]] = {}
    unassociated: set[int] = set()
    for obs in observations:
        if obs["weight_g"] is None:
            unassociated.add(obs["price_vnd"])
        else:
            by_weight.setdefault(obs["weight_g"], set()).add(obs["price_vnd"])

    contradictions: list[dict] = []
    blocked_fields: list[str] = []
    facts: dict = {"origins": []}
    known_prices: set[int] = set(unassociated)
    clean_pairs: dict[int, int] = {}
    for weight in sorted(by_weight):
        prices = by_weight[weight]
        if len(prices) > 1:
            urls = sorted({u for o in observations
                           if o["weight_g"] == weight
                           for u in o["source_urls"]})
            contradictions.append({
                "field": "price", "weight_g": weight,
                "values": sorted(prices),
                "source_urls": urls,
                "detail": f"{len(prices)} different prices for the "
                          f"{weight}g pack — different products at one "
                          f"size, or a stale page; a human decides which "
                          f"product line the brand facts describe"})
            blocked_fields.append(f"price for the {weight}g pack")
        else:
            clean_pairs[weight] = next(iter(prices))

    if clean_pairs:
        weights = sorted(clean_pairs)
        facts["pack_weights_g"] = weights
        facts["pack_weight_g"] = weights[0]
        primary_weight = weights[0]
        if 250 in clean_pairs:
            facts["price_vnd_per_250g"] = clean_pairs[250]
            facts["price_display"] = loaders.format_vnd(clean_pairs[250])
            primary_weight = 250
        else:
            facts["price_display"] = loaders.format_vnd(clean_pairs[weights[0]])
        for weight, price in clean_pairs.items():
            if weight != primary_weight:
                known_prices.add(price)
    if known_prices:
        facts["known_prices_vnd"] = sorted(known_prices)

    origins = _origin_candidates(pages)
    facts["origins"] = [o["name"] for o in origins]

    delivery = _delivery_candidates(pages)
    if len(delivery) == 1:
        facts["delivery_days"] = delivery[0]["value"]
    elif len(delivery) > 1:
        contradictions.append({
            "field": "delivery_days", "weight_g": None,
            "values": [d["value"] for d in delivery],
            "source_urls": sorted({d["source_url"] for d in delivery}),
            "detail": "the site promises different delivery times on "
                      "different pages — a human decides which promise "
                      "the brand stands behind"})
        blocked_fields.append("delivery_days")

    entries: list[dict] = []
    if clean_pairs:
        table = ", ".join(
            f"{loaders.format_vnd(clean_pairs[w])} cho gói "
            f"{w if w < 1000 else '1kg'}{'g' if w < 1000 else ''}"
            for w in sorted(clean_pairs))
        entries.append({"id": "price",
                        "keys": ["giá", "bao nhiêu"],
                        "content": f"Giá: {table}."})
    if origins:
        entries.append({"id": "origins",
                        "keys": ["nguồn gốc", "xuất xứ"],
                        "content": "Nguồn gốc (ứng viên từ site, chờ "
                                   "duyệt): "
                                   + "; ".join(o["name"] for o in origins)})

    notes = [
        "FACTS DRAFT — extracted by setup assist, NOT live. A human "
        "reviews it and saves it via save_facts; until then the brand "
        "has no fact base and fact_check refuses to borrow another "
        "brand's.",
    ]
    if 250 not in clean_pairs and clean_pairs:
        notes.append("no uncontested 250g price was found, so "
                     "price_vnd_per_250g is unset — price_display "
                     "carries the smallest pack's price instead")
    if not observations:
        notes.append("no prices were found in the fetched pages — "
                     "the facts draft needs prices from the human "
                     "(or a fetch of the shop/product pages)")
    if not origins:
        notes.append("no origin candidates were found (no origin "
                     "marker phrases on the fetched pages) — the "
                     "human supplies origins; the draft cannot be "
                     "saved until it has at least one")

    doc = {"brand_id": brand_id, "facts": facts, "entries": entries,
           "simulated": False,
           "note": "Facts DRAFT prepared by setup assist "
                   f"({PROVENANCE}) — not live until a human saves "
                   "it via save_facts."}
    try:
        loaders.validate_facts(doc)
        save_ready, validation_error = True, ""
    except ValueError as exc:
        save_ready, validation_error = False, str(exc)
    return {"doc": doc, "observations": observations,
            "blocked_fields": blocked_fields,
            "contradictions": contradictions,
            "origin_candidates": origins,
            "delivery_candidates": delivery,
            "save_ready": save_ready,
            "validation_error": validation_error,
            "notes": notes}


# ---------------------------------------------------------------------------
# Corpus proposal — best registered match, never a choice
# ---------------------------------------------------------------------------

def _signal_terms(draft: dict) -> list[str]:
    """The site's content vocabulary as match signals: the builder's
    `use` bigrams + glossary candidates + exact names."""
    vocab = draft.get("vocabulary", {})
    terms: list[str] = []
    for entry in vocab.get("use", []):
        terms.append(str(entry.get("term", "")))
    for entry in vocab.get("glossary", []):
        terms.append(str(entry.get("term", "")))
    terms.extend(str(t) for t in vocab.get("exact", []))
    return [t for t in dict.fromkeys(terms) if t.strip()]


def propose_corpus(draft: dict, entries: list[dict]) -> dict:
    """Score every registered corpus against the site's vocabulary
    signals (normalised phrase overlap with the corpus's title /
    kind / legitimacy basis) and propose the best. PROPOSAL ONLY —
    the choice is recorded by a human through set_corpus_choice or
    a pack corpus_choice item. When nothing matches, the proposal
    is an explicit None with the reason: a registry that lacks the
    brand's genre is a finding, not a gap to paper over."""
    signals = _signal_terms(draft)
    scored: list[dict] = []
    for entry in entries:
        if entry.get("corrupt"):
            continue
        haystack = normalize_for_match(
            f"{entry.get('title', '')} {entry.get('kind', '')} "
            f"{entry.get('legitimacy_basis', '')} {entry.get('id', '')}")
        matched = [t for t in signals
                   if normalize_for_match(t) and normalize_for_match(t) in haystack]
        confidence = round(len(matched) / max(3, len(signals)), 2)
        scored.append({"corpus_id": entry.get("id"),
                       "title": entry.get("title", ""),
                       "kind": entry.get("kind", ""),
                       "profile_id": entry.get("profile_id", ""),
                       "matched_terms": matched,
                       "confidence": min(confidence, 1.0),
                       "word_count": entry.get("word_count", 0)})
    scored.sort(key=lambda s: (-len(s["matched_terms"]),
                               -s["word_count"], str(s["corpus_id"])))
    best = scored[0] if scored else None
    if best is None or not best["matched_terms"]:
        return {"status": "no_match", "corpus_id": None,
                "confidence": 0.0,
                "reasoning": "no registered corpus shares vocabulary "
                             "with this site's content signals "
                             f"({len(signals)} terms checked) — the "
                             "registry has no corpus for this brand's "
                             "genre/region yet; register one or write "
                             "without a corpus default",
                "matched_terms": [], "candidates": scored,
                "provenance": PROVENANCE}
    reasoning = (f"site vocabulary matches {best['corpus_id']} on "
                 f"{len(best['matched_terms'])} term(s): "
                 + ", ".join(best["matched_terms"][:6]))
    if best["confidence"] < 0.2:
        reasoning += (" — a WEAK match: the registry's fit for this "
                      "brand is poor, treat the proposal as a "
                      "starting point, not a recommendation")
    return {"status": "proposed", "corpus_id": best["corpus_id"],
            "title": best["title"], "kind": best["kind"],
            "profile_id": best["profile_id"],
            "confidence": best["confidence"],
            "matched_terms": best["matched_terms"],
            "reasoning": reasoning,
            "candidates": scored, "provenance": PROVENANCE}


# ---------------------------------------------------------------------------
# Persona proposal — candidate frames, never a saved persona
# ---------------------------------------------------------------------------

def propose_persona(draft: dict) -> dict:
    """Persona-frame CANDIDATES derived from the measured voice
    (the draft's pov / reader_address suggestions). Three archetypes
    are always offered; their ORDER follows the measurement — the
    archetype the site's own copy already performs comes first.
    Every candidate is a proposal: a human picks, renames and saves
    the persona card through the existing flow."""
    brand = draft.get("brand", {})
    voice = draft.get("voice", {})
    name = str(brand.get("name") or "Brand")
    short = name.split()[-1] if name.split() else name
    pov = str(voice.get("pov") or "")
    reader = voice.get("reader_address") or "bạn"
    reader_part = f"gọi người đọc là '{reader}'" if reader else \
        "chưa rõ cách gọi người đọc"

    brand_person = {
        "name": short, "archetype": "brand-as-person",
        "stance": f"the brand speaks AS itself in first person "
                  f"('{short}'), {reader_part} — a person, not a "
                  f"company voice",
        "pov": pov or "first-person (as brand name)",
        "reader_address": reader,
        "basis": "the site copy already narrates this way (the "
                 "builder's pov measurement) — the persona formalises "
                 "the voice the brand actually uses"}
    insider = {
        "name": "", "archetype": "insider-guide",
        "stance": "a knowledgeable insider from the brand's own "
                  "world (the maker/seller) guides the reader in "
                  f"first person, {reader_part}; specific, warm, "
                  "no corporate distance",
        "pov": "first-person",
        "reader_address": reader,
        "basis": "fits a brand whose authority is first-hand "
                 "experience of its own product"}
    expert = {
        "name": "", "archetype": "neutral-expert",
        "stance": "a third-person expert explainer: measured, "
                  "evidence-first, the brand cited by name rather "
                  "than speaking",
        "pov": "third-person",
        "reader_address": reader,
        "basis": "fits a brand whose site copy speaks about itself "
                 "in third person"}
    if "brand name" in pov:
        ordered = [brand_person, insider, expert]
    elif pov.startswith("first-person"):
        ordered = [insider, brand_person, expert]
    else:
        ordered = [expert, insider, brand_person]
    return {"status": "proposal", "provenance": PROVENANCE,
            "candidates": ordered,
            "note": "candidates only — the human names/picks one "
                    "and saves the persona card; setup assist never "
                    "creates a persona"}


# ---------------------------------------------------------------------------
# Prepare — the one-call orchestration
# ---------------------------------------------------------------------------

def _preserve_human_fields(existing: dict, fresh: dict,
                           _path: str = "") -> tuple[dict, list[str]]:
    """Merge a freshly generated brand draft over the stored one,
    PRESERVING human edits (review round 3, S2).

    The rule: generation is deterministic (the same fetch yields
    the identical draft — pinned by test), so wherever the STORED
    draft differs from the fresh generation, the stored value is
    a human edit and wins; keys the human added are kept too.
    Returns (merged_draft, preserved_field_paths) — the paths are
    reported in the dossier, so the preservation is visible, never
    silent in either direction."""
    merged = dict(fresh)
    preserved: list[str] = []
    for key, old in existing.items():
        path = f"{_path}{key}"
        if key not in fresh:
            merged[key] = old
            preserved.append(path)
        elif isinstance(old, dict) and isinstance(fresh[key], dict):
            sub, sub_preserved = _preserve_human_fields(
                old, fresh[key], path + ".")
            merged[key] = sub
            preserved.extend(sub_preserved)
        elif old != fresh[key]:
            merged[key] = old
            preserved.append(path)
    return merged, preserved




def prepare_brand_setup(site_url: str, brand_id: str,
                        display_name: str | None = None,
                        *, fetcher=None, max_pages: int = 8) -> dict:
    """Prepare a new brand's whole setup from its site and return
    the SETUP DOSSIER (the same shape get_brand_setup returns).

    Steps: fetch the site (fetch_site machinery) → build + save the
    brand DRAFT via the Brand Builder (id forced to `brand_id`;
    a visual palette inferred from the site kit is stamped as a
    setup_assist proposal) → extract the FACTS DRAFT into the setup
    record → create the research pack if none exists and build its
    proposals from the site's own pages (idempotent: an existing
    pack is reused, never version-bumped, and proposals are built
    only when the pack has no brand_rule item yet) → propose a
    corpus + persona frame → persist the setup record → return the
    dossier derived from live state.

    Re-prepare preservation (review round 3, S2): when a brand
    draft is already stored, fields in which it differs from the
    fresh generation are the human's edits — they are PRESERVED
    (see _preserve_human_fields) and the run's preserved field
    paths are recorded in the setup record and reported in the
    dossier's `preserved_fields`."""
    loaders.stored_file_stem(brand_id)  # validates the id up front
    fetch = fetcher or _default_fetcher
    result = fetch(site_url, max_pages)
    pages = [p for p in (result.get("pages") or []) if p and p.get("text")]
    if not pages:
        errors = result.get("errors") or ["no pages returned"]
        raise ValueError(
            f"fetch produced no usable pages for {site_url}: "
            f"{'; '.join(str(e) for e in errors)}")
    root_url = pages[0].get("url") or site_url
    corpus = site_corpus(pages)

    # --- brand draft (existing Brand Builder path) ---
    draft = build_brand_draft(pages, source_type="own_site",
                              brand_name=display_name or None)
    draft["brand"]["id"] = brand_id
    if display_name:
        draft["brand"]["name"] = display_name
    kit = draft.get("brand_kit", {})
    inferred: list[dict] = []
    colors = [str(c.get("hex")) for c in kit.get("colors", [])
              if isinstance(c, dict) and c.get("hex")][:6]
    if colors:
        draft["visual"] = {"palette": colors}
        inferred.append({
            "field": "visual.palette", "provenance": PROVENANCE,
            "basis": "colours mined from the site's own CSS by the "
                     "brand-kit extractor",
            "value": colors})
        draft.setdefault("review_notes", []).append(
            "visual.palette được SUY từ màu CSS của site (setup "
            "assist) — đề xuất có provenance 'setup_assist', duyệt "
            "trước khi chốt; các trường visual khác (medium, mood…) "
            "chưa suy được từ site.")
    if inferred:
        draft["inferred_fields"] = inferred
    # Human edits to the stored draft survive the refresh (S2).
    existing_draft = _load_or_none(loaders.load_brand_draft, brand_id)
    preserved_fields: list[str] = []
    if existing_draft is not None:
        draft, preserved_fields = _preserve_human_fields(
            existing_draft, draft)
    loaders.save_brand_draft(draft)

    # --- facts draft (setup record only — never the live profile) ---
    facts_draft = extract_facts_draft(pages, brand_id)

    # --- research pack (create only when absent; proposals once) ---
    pack = loaders.load_research_pack(brand_id)
    pack_created = False
    if pack is None:
        pack = research.create_pack(brand_id)
        pack_created = True
    research.add_source(
        pack, root_url, "own",
        legitimacy_basis="the brand's own site — fetched by setup "
                         "assist from the URL the owner provided",
        pages=pages, word_count=corpus["total_words"])
    proposals_built = False
    proposals_error = ""
    if not any(i.get("kind") == "brand_rule" for i in pack.get("items", [])):
        try:
            research.build_proposals(pack)
            proposals_built = True
        except ValueError as exc:
            proposals_error = str(exc)
    loaders.save_research_pack(pack)

    # --- proposals (recorded, never applied) ---
    corpus_proposal = propose_corpus(
        draft, loaders.list_corpus_entries())
    persona_proposal = propose_persona(draft)

    existing = loaders.load_brand_setup(brand_id)
    record = {
        "brand_id": brand_id,
        "site_url": site_url,
        "root_url": root_url,
        "display_name": draft["brand"].get("name", ""),
        "prepared_at": (existing or {}).get("prepared_at") or _now(),
        "runs": int((existing or {}).get("runs", 0)) + 1,
        "fetch_summary": {
            "pages_fetched": corpus["page_count"],
            "total_words": corpus["total_words"],
            "js_shell_suspected": bool(result.get("js_shell_suspected")),
            "sitemap_fallback_used": bool(result.get("sitemap_fallback_used")),
            "errors": result.get("errors", []),
        },
        "brand_draft_id": brand_id,
        "preserved_fields": [{"field": p} for p in preserved_fields],
        "facts_draft": facts_draft,
        "corpus_proposal": corpus_proposal,
        "persona_proposal": persona_proposal,
        "pack": {"created_by_prepare": pack_created,
                 "proposals_built": proposals_built,
                 "proposals_error": proposals_error,
                 "version": pack.get("version", 1)},
    }
    loaders.save_brand_setup(record)
    return build_setup_dossier(brand_id)


# ---------------------------------------------------------------------------
# The dossier — checklist state derived from the live stores
# ---------------------------------------------------------------------------

def _load_or_none(loader, brand_id: str):
    try:
        return loader(brand_id)
    except ValueError:
        return None


def build_setup_dossier(brand_id: str) -> dict:
    """The setup checklist for a prepared brand, derived from live
    state: completing a step through the EXISTING tools flips it
    here (a saved facts profile makes the facts step "done", a set
    corpus choice makes the corpus step "done", an applied
    persona_frame pack item makes the persona step "done"…).
    Raises ValueError when no setup was prepared for the brand."""
    record = loaders.load_brand_setup(brand_id)
    if record is None:
        raise ValueError(
            f"no setup prepared for brand {brand_id!r} — run "
            f"prepare_brand_setup first")
    fetch_summary = record.get("fetch_summary", {})
    facts_draft = record.get("facts_draft", {})
    draft = _load_or_none(loaders.load_brand_draft, brand_id)
    live_facts = _load_or_none(loaders.load_facts_profile, brand_id)
    pack = _load_or_none(loaders.load_research_pack, brand_id)
    choice = _load_or_none(loaders.load_corpus_choice, brand_id)
    target = _load_or_none(loaders.load_wordpress_target, brand_id)

    steps: list[dict] = []
    steps.append({
        "key": "site_fetch", "status": "done",
        "detail": f"{fetch_summary.get('pages_fetched', 0)} page(s), "
                  f"{fetch_summary.get('total_words', 0)} words from "
                  f"{record.get('root_url', record.get('site_url', ''))}"})

    preserved = record.get("preserved_fields", [])
    if draft is not None:
        detail = (f"draft '{brand_id}' saved "
                  f"(review_status="
                  f"{draft.get('brand', {}).get('review_status', 'draft')}) "
                  f"— awaiting the human's review + approval; "
                  f"banned_terms starts empty by design")
        if preserved:
            detail += (f"; the latest prepare PRESERVED "
                       f"{len(preserved)} human-edited field(s): "
                       + ", ".join(p.get("field", "")
                                   for p in preserved))
        steps.append({
            "key": "brand_draft", "status": "proposed",
            "detail": detail})
    else:
        steps.append({
            "key": "brand_draft", "status": "needs_human",
            "detail": "no brand draft is stored for this brand — "
                      "re-run prepare_brand_setup or build one with "
                      "save_brand_draft"})

    blocked = facts_draft.get("blocked_fields", [])
    if live_facts is not None:
        steps.append({"key": "facts", "status": "done",
                      "detail": "a live facts profile exists for "
                                "this brand (saved through save_facts)"})
    elif blocked:
        steps.append({"key": "facts", "status": "blocked",
                      "detail": "facts draft prepared but blocked "
                                "on: " + "; ".join(blocked)
                                + " — resolve the contradiction(s), "
                                  "then save via save_facts"})
    else:
        steps.append({"key": "facts", "status": "proposed",
                      "detail": "facts draft prepared in the setup "
                                "record (save_ready="
                                f"{facts_draft.get('save_ready')}) — "
                                "a human reviews it and saves via "
                                "save_facts"})

    pack_counts = research.pack_counts(pack) if pack is not None else None
    pack_open = pack_counts["open_contradictions"] if pack_counts else []
    if pack is None:
        steps.append({"key": "research_pack", "status": "needs_human",
                      "detail": "no research pack exists for this "
                                "brand — create one with "
                                "create_research_pack"})
    elif pack_open:
        steps.append({"key": "research_pack", "status": "blocked",
                      "detail": f"pack v{pack.get('version', 1)} has "
                                f"{len(pack_open)} open "
                                f"contradiction(s) locking items"})
    elif pack_counts["by_status"].get("proposed", 0) > 0:
        steps.append({"key": "research_pack", "status": "proposed",
                      "detail": f"pack v{pack.get('version', 1)}: "
                                f"{pack_counts['items_total']} item(s), "
                                f"{pack_counts['by_status']['proposed']} "
                                f"awaiting per-item decisions"})
    else:
        steps.append({"key": "research_pack", "status": "done",
                      "detail": f"pack v{pack.get('version', 1)}: "
                                f"{pack_counts['items_total']} item(s), "
                                f"none left undecided"})

    proposal = record.get("corpus_proposal") or {}
    if choice is not None:
        steps.append({"key": "corpus_choice", "status": "done",
                      "detail": f"corpus chosen: "
                                f"{choice.get('corpus_id')} "
                                f"(profile {choice.get('profile_id')})"})
    elif proposal.get("status") == "no_match":
        steps.append({"key": "corpus_choice", "status": "needs_human",
                      "detail": "no registered corpus fits this "
                                "brand — register a genre corpus or "
                                "proceed without a corpus default"})
    else:
        steps.append({"key": "corpus_choice", "status": "proposed",
                      "detail": f"proposed: {proposal.get('corpus_id')} "
                                f"(confidence "
                                f"{proposal.get('confidence')}) — "
                                f"confirm via set_corpus_choice"})

    persona_applied = bool(pack) and any(
        i.get("kind") == "persona_frame" and i.get("status") == "applied"
        for i in pack.get("items", []))
    candidates = (record.get("persona_proposal") or {}).get("candidates", [])
    if persona_applied:
        steps.append({"key": "persona", "status": "done",
                      "detail": "a persona_frame pack item has been "
                                "applied for this brand"})
    else:
        steps.append({"key": "persona", "status": "proposed",
                      "detail": f"{len(candidates)} candidate "
                                f"frame(s) proposed — the human "
                                f"picks/edits one and saves the "
                                f"persona card"})

    if target is not None:
        steps.append({"key": "wordpress_target", "status": "done",
                      "detail": f"target set: {target.get('site_url')} "
                                f"(user {target.get('username')})"})
    else:
        steps.append({"key": "wordpress_target", "status": "needs_human",
                      "detail": "no WordPress target — set one with "
                                "set_wordpress_target; the Application "
                                "Password goes through the secure path "
                                "only, never into brand files"})

    # --- contradictions, merged from both detectors ---
    contradictions: list[dict] = []
    for con in facts_draft.get("contradictions", []):
        contradictions.append({**con, "source": "facts_draft"})
    for con in pack_open:
        contradictions.append({
            "field": con.get("field"), "weight_g": None,
            "values": [v.get("value") for v in con.get("values", [])],
            "source_urls": [v.get("source_url", "")
                            for v in con.get("values", [])],
            "detail": "research-pack contradiction "
                      f"{con.get('id')} — resolve it in the pack "
                      "(resolve_contradiction) by choosing one value",
            "source": "research_pack", "id": con.get("id")})

    # --- what only a human can do ---
    needs_human: list[dict] = []
    for con in contradictions:
        needs_human.append({
            "key": f"contradiction:{con.get('field')}",
            "detail": con.get("detail", "")})
    if pack_counts and pack_counts["by_status"].get("proposed", 0) > 0:
        needs_human.append({
            "key": "pack_approvals",
            "detail": f"{pack_counts['by_status']['proposed']} "
                      f"research-pack item(s) await MANUAL per-item "
                      f"decisions (decide_pack_item) — there is no "
                      f"bulk approval, by design"})
    if draft is not None:
        needs_human.append({
            "key": "brand_draft_approval",
            "detail": "review the brand draft (voice, examples, "
                      "claims), add the brand's own banned_terms + "
                      "prohibited_claims — a site cannot reveal its "
                      "own bans — then approve it through the "
                      "existing draft → brand flow"})
    if live_facts is None:
        detail = ("review the facts draft and save it via "
                  "save_facts so the brand gets its own fact base")
        if blocked:
            detail = ("resolve the blocked fact field(s) first ("
                      + "; ".join(blocked) + "), then review + "
                      "save_facts")
        elif not facts_draft.get("save_ready"):
            detail = ("complete the facts draft first ("
                      + str(facts_draft.get("validation_error", ""))
                      + "), then review + save_facts")
        needs_human.append({"key": "facts_review", "detail": detail})
    if choice is None and proposal.get("status") != "no_match":
        needs_human.append({
            "key": "corpus_choice",
            "detail": f"confirm the corpus proposal "
                      f"({proposal.get('corpus_id')}, confidence "
                      f"{proposal.get('confidence')}) via "
                      f"set_corpus_choice — or choose a different "
                      f"corpus"})
    if not persona_applied:
        needs_human.append({
            "key": "persona_choice",
            "detail": "pick one persona candidate (or edit it), "
                      "name the persona, and save the persona card"})
    if target is None:
        needs_human.append({
            "key": "wordpress_target",
            "detail": "set the WordPress target for the client's "
                      "site and store its Application Password "
                      "through the secure vault path — never in "
                      "chat, brand files or git"})

    facts_summary = {
        "doc": facts_draft.get("doc"),
        "blocked_fields": blocked,
        "save_ready": facts_draft.get("save_ready"),
        "validation_error": facts_draft.get("validation_error", ""),
        "observations": len(facts_draft.get("observations", [])),
        "notes": facts_draft.get("notes", []),
    }
    return {
        "brand_id": brand_id,
        "site_url": record.get("site_url", ""),
        "display_name": record.get("display_name", ""),
        "prepared_at": record.get("prepared_at", ""),
        "updated_at": record.get("updated_at", ""),
        "runs": record.get("runs", 1),
        "steps": steps,
        "brand_draft_id": record.get("brand_draft_id", brand_id),
        "preserved_fields": preserved,
        "facts_draft": facts_summary,
        "pack": ({"version": pack.get("version", 1),
                  "counts": pack_counts} if pack is not None else None),
        "corpus_proposal": proposal,
        "corpus_choice": choice,
        "persona_proposal": record.get("persona_proposal", {}),
        "contradictions": contradictions,
        "needs_human": needs_human,
    }
