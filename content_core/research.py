"""Research pack operations (specs/research-config-feature.md).

The pack is a PRE-APPROVAL artefact: AI/operator propose items with
provenance, the owner decides item by item (manual, per Smith's
standing decision — no bulk approval), and `apply_pack` is the only
door into live configuration. Apply writes through the EXISTING save
paths (loaders.save_brand_draft / save_facts / save_style_profile,
persona files, corpus choices) — the pack never keeps a second copy
of live config.

Hard invariants (spec section 8, pinned by tests):
- Only approved / edited items apply. Proposed and rejected items
  never touch a live store.
- An item linked to an OPEN contradiction cannot be decided
  (approval is locked, spec 3.2) and cannot be applied; resolving
  the contradiction approves the chosen item and rejects the rest.
- Reference material (competitor-sourced) never applies: brand items
  go through the draft_to_brand_yaml conversion gate, which refuses
  drafts still carrying reference_only examples, and any item
  flagged reference=True is refused outright.
- Style items are saved with store_excerpts=False — enforced here,
  whatever the payload asks for (spec US-4).
- Apply is idempotent: applied items are skipped on re-apply, and
  the report lists exactly which ids landed.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import loaders
from .brand_extract import build_brand_draft as _build_brand_draft
from .brand_extract import draft_to_brand_yaml as _draft_to_brand_yaml
from .brand_extract import find_site_claims as _find_site_claims
from .style import build_profile as _build_style_profile


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

#: Fact fields that name the same underlying quantity in different
#: notations — grouped for contradiction detection. The mambagiaokhoe
#: precedent: a listing int price and a price-table display string
#: are ONE field ("price") with two competing values.
_FACT_FIELD_GROUPS = {
    "price_vnd_per_250g": "price",
    "price_display": "price",
    "pack_weight_g": "pack_weight_g",
    "delivery_days": "delivery_days",
    "roast_weekday": "roast_weekday",
}

_FACT_SCALAR_FIELDS = set(_FACT_FIELD_GROUPS) | {
    "origin", "known_price_vnd", "pack_weight_option_g", "entry", "claim"}


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _fact_group_value(item: dict) -> tuple[str, object] | None:
    """(canonical field, comparable value) for contradiction
    detection, or None for fields that accumulate (origins, known
    prices, entries, claims never contradict — they add up)."""
    payload = item.get("payload", {})
    field = payload.get("field")
    if field not in _FACT_FIELD_GROUPS:
        return None
    value = payload.get("value")
    if _FACT_FIELD_GROUPS[field] == "price":
        if isinstance(value, int) and not isinstance(value, bool):
            return "price", value
        digits = "".join(c for c in str(value) if c.isdigit())
        return "price", int(digits) if digits else str(value)
    return _FACT_FIELD_GROUPS[field], value


# ---------------------------------------------------------------------------
# Pack lifecycle
# ---------------------------------------------------------------------------

def create_pack(brand_id: str) -> dict:
    """Create a pack for a brand. Re-creating an existing brand's
    pack starts a NEW VERSION (spec section 5): the previous
    version's counts are archived into `history`, the item lists
    reset, and live configuration is never touched by re-creating."""
    loaders.stored_file_stem(brand_id)  # validates the id
    existing = loaders.load_research_pack(brand_id)
    history: list[dict] = []
    version = 1
    if existing is not None:
        version = int(existing.get("version", 1)) + 1
        history = list(existing.get("history", []))
        history.append({
            "version": existing.get("version", 1),
            "archived_at": _now(),
            "items": len(existing.get("items", [])),
            "applied": sum(1 for i in existing.get("items", [])
                           if i.get("status") == "applied"),
        })
    pack = {"brand_id": brand_id, "version": version,
            "created_at": _now(),
            "updated_at": _now(),
            "sources": [], "items": [], "contradictions": [],
            "quality_rejections": [], "history": history}
    return loaders.save_research_pack(pack)


def get_pack(brand_id: str) -> dict | None:
    return loaders.load_research_pack(brand_id)


def pack_counts(pack: dict) -> dict:
    """Status/kind counts + the open-contradiction list — the single
    get_pack payload the review surface needs (spec 3.5)."""
    by_status: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for item in pack.get("items", []):
        by_status[item["status"]] = by_status.get(item["status"], 0) + 1
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
    return {"items_total": len(pack.get("items", [])),
            "by_status": by_status, "by_kind": by_kind,
            "sources": len(pack.get("sources", [])),
            "open_contradictions": [
                c for c in pack.get("contradictions", [])
                if c.get("status") == "open"]}


def add_source(pack: dict, url: str, kind: str,
               legitimacy_basis: str = "", quality_verdict: str = "accept",
               quality_reason: str = "", pages: list | None = None,
               word_count: int | None = None) -> dict:
    """Record one gathered source. A rejected source is recorded AND
    mirrored into quality_rejections[] with its reason (spec US-6):
    a rejected source contributes nothing downstream."""
    if quality_verdict not in ("accept", "reject"):
        raise ValueError(
            f"quality_verdict must be 'accept' or 'reject', "
            f"got {quality_verdict!r}")
    url = str(url or "").strip()
    if not url:
        raise ValueError("source needs a non-empty url")
    for existing in pack["sources"]:
        if existing["url"] == url:
            return existing  # canonical-url dedupe (spec section 5)
    source = {"id": _new_id("src"), "url": url, "kind": kind,
              "legitimacy_basis": legitimacy_basis,
              "quality": {"verdict": quality_verdict,
                          "reason": quality_reason},
              "fetched_at": _now(),
              "word_count": word_count,
              "pages": pages if pages is not None else []}
    pack["sources"].append(source)
    if quality_verdict == "reject":
        pack["quality_rejections"].append(
            {"source_id": source["id"], "url": url,
             "reason": quality_reason, "at": _now()})
    return source


# ---------------------------------------------------------------------------
# Proposals + contradiction detection
# ---------------------------------------------------------------------------

def _detect_contradictions(pack: dict) -> None:
    """Group live fact items by canonical field; differing values
    create (or extend) ONE open contradiction per field, and every
    involved item is stamped with its id. Same-value duplicates and
    already-rejected items never contradict."""
    groups: dict[str, list[dict]] = {}
    for item in pack["items"]:
        if item["kind"] != "fact" or item["status"] in ("rejected",):
            continue
        grouped = _fact_group_value(item)
        if grouped is None:
            continue
        groups.setdefault(grouped[0], []).append((grouped[1], item))
    for field, entries in groups.items():
        values = {repr(v) for v, _ in entries}
        if len(values) < 2:
            continue
        open_con = next(
            (c for c in pack["contradictions"]
             if c["field"] == field and c["status"] == "open"), None)
        if open_con is None:
            open_con = {"id": _new_id("con"), "field": field,
                        "values": [], "item_ids": [], "status": "open",
                        "resolution": None,
                        "created_at": _now()}
            pack["contradictions"].append(open_con)
        for value, item in entries:
            if item["id"] not in open_con["item_ids"]:
                open_con["item_ids"].append(item["id"])
            entry = {"value": value, "item_id": item["id"],
                     "source_url": item["provenance"].get("source_url", "")}
            if entry not in open_con["values"]:
                open_con["values"].append(entry)
            item["contradiction_id"] = open_con["id"]


def propose_item(pack: dict, kind: str, payload: dict, provenance: dict,
                 reference: bool = False) -> dict:
    """Append one proposal item (status proposed) and run the
    contradiction detector. Fact payloads are field-checked here so a
    malformed fact fails at proposal time, not at apply time."""
    if not isinstance(payload, dict):
        raise ValueError("item payload must be an object")
    if not isinstance(provenance, dict):
        raise ValueError("item provenance must be an object")
    if kind == "fact":
        field = payload.get("field")
        if field not in _FACT_SCALAR_FIELDS:
            raise ValueError(
                f"fact field must be one of {sorted(_FACT_SCALAR_FIELDS)}, "
                f"got {field!r}")
        if "value" not in payload:
            raise ValueError("fact item payload needs a value")
    item = {"id": _new_id("item"), "kind": kind, "payload": payload,
            "provenance": {"source_url": provenance.get("source_url", ""),
                           "note": provenance.get("note", "")},
            "status": "proposed", "reference": bool(reference),
            "contradiction_id": None, "original_payload": None,
            "decided_at": None, "applied_at": None}
    pack["items"].append(item)
    _detect_contradictions(pack)
    return item


def build_proposals(pack: dict) -> dict:
    """Deterministic proposal building from the pack's accepted OWN
    sources (their fetched pages ride on the source records): one
    brand_rule item from analyze/build_brand_draft, plus one fact
    item per site claim find_site_claims flags needs_evidence.
    Price/weight/origin facts are proposed by the operator through
    propose_item — the contradiction detector, not the extractor,
    is the code's job there."""
    own = [s for s in pack["sources"]
           if s["kind"] == "own" and s["quality"]["verdict"] == "accept"
           and s.get("pages")]
    if not own:
        raise ValueError(
            "no accepted own-source pages in the pack to build "
            "proposals from — add a source with pages first")
    proposed: list[str] = []
    for source in own:
        pages = source["pages"]
        draft = _build_brand_draft(pages, source_type="own_site")
        item = propose_item(
            pack, "brand_rule", draft,
            {"source_url": source["url"],
             "note": f"build_brand_draft over {len(pages)} own page(s)"})
        proposed.append(item["id"])
        seen_claims: set[str] = set()
        for claim in _find_site_claims(pages):
            if claim["claim"] in seen_claims:
                continue
            seen_claims.add(claim["claim"])
            item = propose_item(
                pack, "fact",
                {"field": "claim", "value": claim["claim"],
                 "phrase": claim.get("phrase", "")},
                {"source_url": claim.get("source_url", source["url"]),
                 "note": "site claim flagged needs_evidence"})
            proposed.append(item["id"])
    return {"proposed": proposed}


# ---------------------------------------------------------------------------
# Decisions + contradiction resolution
# ---------------------------------------------------------------------------

def _find_item(pack: dict, item_id: str) -> dict:
    item = next((i for i in pack["items"] if i["id"] == item_id), None)
    if item is None:
        raise ValueError(f"pack item not found: {item_id}")
    return item


def decide_item(pack: dict, item_id: str, decision: str,
                edited_payload: dict | None = None,
                note: str = "") -> dict:
    """One manual decision on one item (no bulk path exists, by
    Smith's decision). Decisions: approve / edit / reject. An item
    linked to an OPEN contradiction is locked — the contradiction
    must be resolved first (spec 3.2). An applied item is final."""
    if decision not in ("approve", "edit", "reject"):
        raise ValueError(
            f"decision must be 'approve', 'edit' or 'reject', "
            f"got {decision!r}")
    item = _find_item(pack, item_id)
    if item["status"] == "applied":
        raise ValueError(f"item {item_id} is already applied and final")
    if item.get("contradiction_id"):
        con = next((c for c in pack["contradictions"]
                    if c["id"] == item["contradiction_id"]), None)
        if con is not None and con["status"] == "open":
            raise ValueError(
                f"item {item_id} is locked by open contradiction "
                f"{con['id']} — resolve the contradiction first")
    if decision == "approve":
        item["status"] = "approved"
    elif decision == "reject":
        item["status"] = "rejected"
    else:  # edit
        if not isinstance(edited_payload, dict):
            raise ValueError("an edit decision needs edited_payload")
        if item.get("original_payload") is None:
            item["original_payload"] = item["payload"]
        item["payload"] = edited_payload
        item["status"] = "edited"
    item["decided_at"] = _now()
    if note:
        item["decision_note"] = note
    return item


def resolve_contradiction(pack: dict, contradiction_id: str,
                          chosen_item_id: str, note: str = "") -> dict:
    """Resolve an open contradiction by choosing one of its items:
    the chosen item becomes approved, every other linked item is
    rejected (it survives only as pack history — spec US-2), and the
    record closes with the resolution."""
    con = next((c for c in pack["contradictions"]
                if c["id"] == contradiction_id), None)
    if con is None:
        raise ValueError(f"contradiction not found: {contradiction_id}")
    if con["status"] != "open":
        raise ValueError(f"contradiction {contradiction_id} is already resolved")
    if chosen_item_id not in con["item_ids"]:
        raise ValueError(
            f"chosen item {chosen_item_id} is not part of contradiction "
            f"{contradiction_id}")
    chosen = _find_item(pack, chosen_item_id)
    for item_id in con["item_ids"]:
        item = _find_item(pack, item_id)
        item["contradiction_id"] = None
        item["decided_at"] = _now()
        if item_id == chosen_item_id:
            item["status"] = "approved"
            item["decision_note"] = "approved via contradiction resolution"
        else:
            item["status"] = "rejected"
            item["decision_note"] = "rejected via contradiction resolution"
    con["status"] = "resolved"
    con["resolution"] = {"chosen_item_id": chosen_item_id,
                         "chosen_value": chosen["payload"].get("value"),
                         "note": note,
                         "resolved_at": _now()}
    return con


# ---------------------------------------------------------------------------
# Apply — the only door into live configuration
# ---------------------------------------------------------------------------

def _merge_fact_items(brand_id: str, items: list[dict]) -> None:
    """Merge approved fact items into the brand's facts document and
    save it through loaders.save_facts (validated BEFORE writing:
    an incomplete merge raises and writes nothing)."""
    doc = loaders.load_facts_profile(brand_id)
    if doc is None:
        doc = {"brand_id": brand_id, "facts": {"origins": []}, "entries": []}
    facts = doc.setdefault("facts", {})
    facts.setdefault("origins", [])
    entries = doc.setdefault("entries", [])
    for item in items:
        field, value = item["payload"]["field"], item["payload"]["value"]
        if field == "price_vnd_per_250g":
            facts["price_vnd_per_250g"] = value
        elif field == "price_display":
            facts["price_display"] = value
        elif field == "pack_weight_g":
            facts["pack_weight_g"] = value
        elif field == "origin":
            if value not in facts["origins"]:
                facts["origins"].append(value)
        elif field == "known_price_vnd":
            known = facts.setdefault("known_prices_vnd", [])
            if value not in known:
                known.append(value)
        elif field == "pack_weight_option_g":
            weights = facts.setdefault("pack_weights_g", [])
            if value not in weights:
                weights.append(value)
        elif field in ("delivery_days", "roast_weekday"):
            facts[field] = value
        elif field == "entry":
            entries[:] = [e for e in entries
                          if e.get("id") != value.get("id")]
            entries.append(value)
        elif field == "claim":
            entry = {"id": f"claim-{item['id']}",
                     "keys": [],
                     "content": f"Approved claim: {value}"}
            entries[:] = [e for e in entries if e.get("id") != entry["id"]]
            entries.append(entry)
    loaders.save_facts(doc)


def _apply_brand_item(brand_id: str, item: dict) -> None:
    draft = item["payload"]
    # The conversion gate runs FIRST (spec US-3): a draft still
    # carrying reference_only competitor examples is refused exactly
    # the way draft_to_brand_yaml refuses it, and nothing is saved.
    _draft_to_brand_yaml(draft)
    loaders.save_brand_draft(draft)


def _apply_style_item(brand_id: str, item: dict) -> dict:
    payload = item["payload"]
    samples = payload.get("samples")
    if not (isinstance(samples, list) and samples):
        raise ValueError("style item payload needs a non-empty samples list")
    # store_excerpts is FORCED to False here (spec US-4 / section 8):
    # pack-sourced corpora are other people's writing — statistics
    # and distilled traits only, whatever the payload asked for.
    profile = _build_style_profile(
        str(payload.get("name") or f"{brand_id} style"),
        samples, qualitative=payload.get("qualitative"),
        store_excerpts=False, brand_id=brand_id,
        profile_id=payload.get("profile_id"))
    return loaders.save_style_profile(profile)


def _apply_persona_item(brand_id: str, item: dict) -> Path:
    payload = item["payload"]
    data = payload.get("data")
    if not isinstance(data, dict) or not str(data.get("name") or "").strip():
        raise ValueError("persona item payload needs data.name")
    card = {"spec": "chara_card_v2", "spec_version": "2.0", "data": data}
    path = loaders.personas_dir() / f"persona_{loaders.slugify(data['name'])}.json"
    loaders.atomic_write_text(
        path, json.dumps(card, ensure_ascii=False, indent=2))
    return path


def _apply_corpus_choice_item(brand_id: str, item: dict) -> dict:
    payload = item["payload"]
    corpus_id = payload.get("corpus_id")
    entry = loaders.load_corpus_entry(str(corpus_id or ""))
    if entry is None:
        raise ValueError(f"corpus not registered: {corpus_id!r}")
    if payload.get("version") is not None \
            and payload["version"] != entry["version"]:
        raise ValueError(
            f"corpus version mismatch: item pins v{payload['version']}, "
            f"registry has v{entry['version']} — re-decide the item")
    record = loaders.save_corpus_choice(brand_id, {
        "corpus_id": entry["id"], "version": entry["version"],
        "profile_id": entry["profile_id"], "source": "pack"})
    # Stamp the choice onto the brand's stored draft when one exists,
    # so the brand's own config shows it (the choices store stays the
    # authoritative record; loaders.load_corpus_choice reads it
    # first).
    draft = loaders.load_brand_draft(brand_id)
    if draft is not None:
        draft["corpus_choice"] = record
        loaders.save_brand_draft(draft)
    return record


def apply_pack(pack: dict) -> dict:
    """Apply every approved/edited item through the existing save
    paths. Returns {applied, already_applied, blocked, failed} —
    applied lists exactly the ids that transitioned in THIS call.
    Blocked: items held back by an open contradiction or by being
    reference material. Failed: items whose save path raised (the
    live store for that item is untouched)."""
    brand_id = pack["brand_id"]
    report: dict = {"brand_id": brand_id, "applied": [],
                    "already_applied": [], "blocked": [], "failed": []}
    open_cons = {c["id"] for c in pack["contradictions"]
                 if c["status"] == "open"}
    fact_items: list[dict] = []

    def mark_applied(item: dict) -> None:
        item["status"] = "applied"
        item["applied_at"] = _now()
        report["applied"].append(item["id"])

    for item in pack["items"]:
        if item["status"] == "applied":
            report["already_applied"].append(item["id"])
            continue
        # An open contradiction is reported as BLOCKED ahead of the
        # status skip: it is the more specific, more important signal.
        # decide_item already refuses to approve a locked item, so an
        # approved-but-locked item should be unreachable — this is
        # defence in depth, and it keeps a locked proposed item
        # visible in the report instead of silently skipped.
        if item.get("contradiction_id") is not None \
                and item.get("contradiction_id") in open_cons:
            report["blocked"].append({
                "item_id": item["id"],
                "reason": f"open contradiction {item['contradiction_id']}"})
            continue
        if item["status"] not in ("approved", "edited"):
            continue
        if item.get("reference") and item["kind"] != "brand_rule":
            report["blocked"].append({
                "item_id": item["id"],
                "reason": "reference-only material cannot be applied"})
            continue
        # brand_rule items are deliberately NOT short-circuited by
        # the reference flag: _apply_brand_item runs the
        # draft_to_brand_yaml conversion gate first, so a draft
        # still carrying reference_only examples FAILS with the
        # gate's own refusal (spec US-3) instead of a generic block.
        if item["kind"] == "fact":
            fact_items.append(item)  # merged + saved as one document
            continue
        try:
            if item["kind"] == "brand_rule":
                _apply_brand_item(brand_id, item)
            elif item["kind"] == "style_candidate":
                _apply_style_item(brand_id, item)
            elif item["kind"] == "persona_frame":
                _apply_persona_item(brand_id, item)
            elif item["kind"] == "corpus_choice":
                _apply_corpus_choice_item(brand_id, item)
            mark_applied(item)
        except ValueError as exc:
            report["failed"].append({"item_id": item["id"],
                                     "error": str(exc)})
    if fact_items:
        try:
            _merge_fact_items(brand_id, fact_items)
            for item in fact_items:
                mark_applied(item)
        except ValueError as exc:
            for item in fact_items:
                report["failed"].append({"item_id": item["id"],
                                         "error": str(exc)})
    return report
