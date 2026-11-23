"""Taste layer — the learning loop's core side.

The owner marks finished articles with a verdict: approved unchanged,
or edited (BOTH versions are kept — the diff between what the system
wrote and what the owner changed it to is the highest-value learning
signal there is). Articles imported without a verdict sit at
"pending": candidates only, never learned from until marked.

When a brand has >= MIN_TASTE_SAMPLES verdicted articles,
`build_taste_profile` distils them into a per-brand style profile
(`<brand-id>-taste-voice`) through style.build_profile, attachable
via assemble_context like any other profile.

COPYRIGHT NOTE — why store_excerpts=True is legitimate HERE and only
here: taste samples are the OWNER'S OWN finished articles (written
for the owner's own service, then approved or hand-edited by the
owner). They are not third-party corpora. Every other path that
builds profiles from other people's writing (research packs, genre
corpora) forces store_excerpts=False.
"""
from __future__ import annotations

import hashlib

from . import loaders
from .style import build_profile as _build_style_profile

MIN_TASTE_SAMPLES = 3


def wp_edit_ref(site_url: str, wp_post_id: int) -> str:
    """The taste ref for a WordPress post's edit pair — stable per
    post, so every sync of the same post addresses the same entry."""
    return f"wordpress:{site_url}:post:{wp_post_id}"


def record_wp_edit(brand_id: str, site_url: str, wp_post_id: int,
                   original_text: str, edited_text: str) -> dict:
    """Record a WordPress edit pair as an "edited" taste entry
    (quality uplift WS3): original = the draft as exported, edited =
    the post as it now stands on the site. Provenance rides on the
    entry's `wp_sync` block (site, post id, content hash, synced_at).

    Idempotency + supersession follow the taste layer's own rule —
    re-marking the same ref REPLACES the entry:
    - same post, same edited-content hash → already_synced, the
      store is not touched;
    - same post, new edited content → the entry is replaced with
      the newest edited text (the original half stays the exported
      draft — that is the pair the owner is teaching with);
    - first sight → a new entry is created.
    Returns {entry, created, updated, already_synced}."""
    if not (original_text or "").strip():
        raise ValueError("a WP edit pair needs the exported draft text")
    if not (edited_text or "").strip():
        raise ValueError("a WP edit pair needs the post's current text")
    loaders.stored_file_stem(brand_id)
    ref = wp_edit_ref(site_url, wp_post_id)
    content_hash = hashlib.sha1(
        edited_text.encode("utf-8")).hexdigest()
    doc = _doc(brand_id)
    existing = next((e for e in doc["entries"] if e["ref"] == ref), None)
    if existing is not None \
            and (existing.get("wp_sync") or {}).get("content_hash") \
            == content_hash:
        return {"entry": existing, "created": False, "updated": False,
                "already_synced": True}
    now = loaders._utc_now()
    entry = {"id": _entry_id(ref, original_text), "ref": ref,
             "source": "wordpress", "verdict": "edited",
             "text": original_text, "edited_text": edited_text,
             "note": f"WordPress edit sync: post {wp_post_id} on "
                     f"{site_url}",
             "marked_at": now, "imported_at": None,
             "wp_sync": {"site_url": site_url,
                         "wp_post_id": wp_post_id,
                         "content_hash": content_hash,
                         "synced_at": now}}
    doc["entries"] = [e for e in doc["entries"] if e["ref"] != ref]
    doc["entries"].append(entry)
    loaders.save_taste_doc(doc)
    return {"entry": entry, "created": existing is None,
            "updated": existing is not None, "already_synced": False}


def _doc(brand_id: str) -> dict:
    doc = loaders.load_taste_doc(brand_id)
    if doc is None:
        doc = {"brand_id": brand_id, "entries": [], "taste_profile": None}
    doc.setdefault("entries", [])
    doc.setdefault("taste_profile", None)
    return doc


def _entry_id(ref: str, text: str) -> str:
    digest = hashlib.sha1(f"{ref}|{text}".encode("utf-8")).hexdigest()[:10]
    return f"taste-{digest}"


def import_articles(brand_id: str, articles: list[dict]) -> dict:
    """Import articles as PENDING (unmarked) entries. Each article:
    {ref, text}. Re-importing the same ref is a no-op — verdicts the
    owner already gave are never reset by an import."""
    loaders.stored_file_stem(brand_id)
    doc = _doc(brand_id)
    known_refs = {e["ref"] for e in doc["entries"]}
    imported = 0
    for article in articles:
        ref = str(article.get("ref") or "")
        text = str(article.get("text") or "")
        if not ref or not text.strip():
            raise ValueError("each imported article needs a ref and text")
        if ref in known_refs:
            continue
        doc["entries"].append({
            "id": _entry_id(ref, text), "ref": ref,
            "source": article.get("source", "file"),
            "verdict": "pending", "text": text, "edited_text": None,
            "note": "", "marked_at": None,
            "imported_at": loaders._utc_now()})
        known_refs.add(ref)
        imported += 1
    loaders.save_taste_doc(doc)
    return {"brand_id": brand_id, "imported": imported,
            "total": len(doc["entries"])}


def mark_verdict(brand_id: str, verdict: str, ref: str, text: str,
                 edited_text: str | None = None, source: str = "file",
                 note: str = "") -> dict:
    """Record the owner's verdict on one article. `ref` identifies
    the article (draft id or file path); re-marking the same ref
    replaces the entry (the latest verdict is the owner's word).
    An "edited" verdict REQUIRES the edited text — the edited
    version is what gets learned from, the original is kept as the
    other half of the diff."""
    if verdict not in ("approved_unchanged", "edited"):
        raise ValueError(
            f"verdict must be 'approved_unchanged' or 'edited', "
            f"got {verdict!r}")
    if verdict == "edited" and not (edited_text or "").strip():
        raise ValueError(
            "an 'edited' verdict needs edited_text — the owner's "
            "edited version is the learning signal")
    if not (text or "").strip():
        raise ValueError("marking a verdict needs the article text")
    loaders.stored_file_stem(brand_id)
    doc = _doc(brand_id)
    entry = {"id": _entry_id(ref, text), "ref": ref, "source": source,
             "verdict": verdict, "text": text,
             "edited_text": edited_text if verdict == "edited" else None,
             "note": note, "marked_at": loaders._utc_now(),
             "imported_at": None}
    doc["entries"] = [e for e in doc["entries"] if e["ref"] != ref]
    doc["entries"].append(entry)
    loaders.save_taste_doc(doc)
    return entry


def _final_text(entry: dict) -> str:
    """What gets learned from an entry: the owner's edited text when
    there is one, else the text as written."""
    if entry["verdict"] == "edited" and entry.get("edited_text"):
        return entry["edited_text"]
    return entry["text"]


def build_taste_profile(brand_id: str) -> dict:
    """Distil the brand's verdicted articles into its taste profile.
    Needs >= MIN_TASTE_SAMPLES approved/edited entries; pending
    entries never count. store_excerpts=True — the owner's own
    articles (see the module docstring)."""
    doc = _doc(brand_id)
    verdicted = [e for e in doc["entries"]
                 if e["verdict"] in ("approved_unchanged", "edited")]
    if len(verdicted) < MIN_TASTE_SAMPLES:
        raise ValueError(
            f"brand {brand_id!r} has {len(verdicted)} verdicted "
            f"article(s); a taste profile needs at least "
            f"{MIN_TASTE_SAMPLES} (pending/unmarked articles never "
            f"count — only the owner's verdicts teach)")
    samples = [_final_text(e) for e in verdicted]
    profile = _build_style_profile(
        f"{brand_id} — taste voice (owner-approved articles)",
        samples, qualitative=None, store_excerpts=True,
        brand_id=brand_id, profile_id=f"{brand_id}-taste-voice")
    loaders.save_style_profile(profile)
    doc["taste_profile"] = {
        "profile_id": profile["id"], "built_from": len(verdicted),
        "built_at": loaders._utc_now()}
    loaders.save_taste_doc(doc)
    return {"id": profile["id"], "brand_id": brand_id,
            "built_from": len(verdicted), "profile": profile}


def list_entries(brand_id: str) -> dict:
    """One summary row per taste entry: id, ref, source, verdict,
    imported/marked stamps and the article's word count — the
    listing counterpart to taste_status's counts (the admin needs
    the pending rows themselves back after a page reload, not just
    how many there are). The article TEXT is not returned — this
    is a listing, not a read.

    An entry too broken to summarise (not an object, an unknown
    verdict, no text snapshot — a hand-edited store) is LISTED as
    corrupt with its index, per the house list discipline: never
    silently skipped, never a crash. A corrupt document FILE is
    the loader's ValueError, like every taste read."""
    loaders.stored_file_stem(brand_id)
    doc = loaders.load_taste_doc(brand_id)
    entries: list[dict] = []
    if doc is not None:
        raw = doc.get("entries", [])
        if not isinstance(raw, list):
            raise ValueError(
                f"corrupt taste document for brand {brand_id!r}: "
                f"'entries' must be a list")
        for i, entry in enumerate(raw):
            if not isinstance(entry, dict) \
                    or entry.get("verdict") not in loaders.TASTE_VERDICTS \
                    or not str(entry.get("text") or "").strip():
                entries.append({"index": i, "corrupt": True})
                continue
            entries.append({
                "id": entry.get("id", ""), "ref": entry.get("ref", ""),
                "source": entry.get("source", ""),
                "verdict": entry["verdict"],
                "imported_at": entry.get("imported_at"),
                "marked_at": entry.get("marked_at"),
                "word_count": len(str(entry["text"]).split()),
                "corrupt": False})
    return {"brand_id": brand_id, "entries": entries,
            "count": len(entries)}


def taste_status(brand_id: str) -> dict:
    """Counts by verdict + whether a taste profile exists and what
    it was built from. A brand with no taste store reports zeros —
    absence is a state, not an error."""
    loaders.stored_file_stem(brand_id)
    doc = loaders.load_taste_doc(brand_id)
    counts = {"pending": 0, "approved_unchanged": 0, "edited": 0}
    profile = None
    if doc is not None:
        for entry in doc.get("entries", []):
            counts[entry["verdict"]] = counts.get(entry["verdict"], 0) + 1
        profile = doc.get("taste_profile")
    return {"brand_id": brand_id, "counts": counts,
            "taste_profile": profile}
