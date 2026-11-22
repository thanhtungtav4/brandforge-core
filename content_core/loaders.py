"""Load the 3 separate data layers: Persona (Card V2) / Brand (YAML) / Facts (lorebook JSON)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

import yaml

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _fsync_dir(dir_path: Path) -> None:
    """Best-effort fsync of a directory so a rename inside it is durable
    across a power loss, not just a process crash (review L5: the file
    fsync alone does not make the directory entry durable). Platforms
    that cannot open/fsync a directory simply skip it."""
    try:
        dfd = os.open(dir_path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dfd)
    except OSError:
        pass
    finally:
        os.close(dfd)


#: Orphan temp files from a SIGKILL/power-loss mid-write are swept once
#: they are this old (review L5): a live writer's temp is seconds old,
#: so an hour is far beyond any in-flight write.
_TEMP_ORPHAN_AGE_S = 3600.0


def _sweep_stale_temps(dir_path: Path) -> None:
    """Remove orphaned `*.tmp` files older than _TEMP_ORPHAN_AGE_S from
    a store directory (best-effort: any error is ignored — sweeping
    must never break a write)."""
    import time as _time
    try:
        entries = list(dir_path.glob("*.tmp"))
    except OSError:
        return
    now = _time.time()
    for entry in entries:
        try:
            if now - entry.stat().st_mtime > _TEMP_ORPHAN_AGE_S:
                entry.unlink()
        except OSError:
            pass


def atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` atomically: write a temp file in the SAME
    directory, fsync, then os.replace over the target, then fsync the
    directory so the rename itself is durable. A crash mid-write
    leaves the previous file intact (at worst an orphan temp file,
    swept once it is an hour old), never a half-written JSON.

    File mode (review L5): mkstemp creates the temp at 0600, which used
    to propagate silently to every stored file. An existing target's
    mode is preserved; a new file gets 0644 (subject to umask) like
    any ordinary data file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _sweep_stale_temps(path.parent)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent),
                                    prefix=path.name + ".", suffix=".tmp")
    try:
        try:
            mode = path.stat().st_mode & 0o777
        except OSError:
            mode = 0o644
        os.chmod(tmp_name, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
        _fsync_dir(path.parent)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


#: Stored ids longer than this are rejected at the boundary
#: (evaluation R7 F2: a 10k-char id used to sail through sanitising and
#: explode at open() with a raw "[Errno 36] File name too long" that
#: leaked the absolute store path). 200 chars is far above every real
#: id in the system (generated ids are slugs + short hashes).
MAX_STORED_ID_LENGTH = 200


def stored_file_stem(raw_id: str) -> str:
    """Map a stored id to its filename stem, INJECTIVELY.

    The old sanitiser simply dropped disallowed characters, which mapped
    distinct ids ("a/b" and "ab") to the SAME file — the second save
    silently overwrote the first. Now: characters outside [alnum-_]
    are dropped as before, but when dropping changed the id, a short
    stable hash of the ORIGINAL id is appended, so distinct ids stay
    distinct. Ids that were already clean keep their exact legacy stem
    ("mocbrew-blog-voice" -> "mocbrew-blog-voice.json"), so every file
    stored before this change resolves exactly as before.

    Raises ValueError for an empty/unusable id and for an id longer
    than MAX_STORED_ID_LENGTH — the message never contains a path."""
    if len(raw_id) > MAX_STORED_ID_LENGTH:
        raise ValueError(
            f"id too long ({len(raw_id)} chars > {MAX_STORED_ID_LENGTH})")
    safe = "".join(c for c in raw_id if c.isalnum() or c in "-_")
    if not safe:
        raise ValueError(f"invalid id: {raw_id!r}")
    if safe != raw_id:
        digest = hashlib.sha1(raw_id.encode("utf-8")).hexdigest()[:8]
        safe = f"{safe}-{digest}"
    return safe


def _load_json_file(path: Path) -> dict:
    """json.loads with a human-actionable error: a corrupt stored file
    (interrupted write, hand edit) must surface as a clear ValueError
    naming the file, not a bare JSONDecodeError from deep in a tool call
    (audit round 2). The error names the FILE only, never the absolute
    path (review L6: tool errors are shown to the writing model and
    logged; the store's filesystem layout is not the caller's business
    — the R7 fix removed paths from id errors, this closes the twin)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"corrupt JSON in stored file {path.name}: {exc}") from None


def _require_stored_object(data: object, path: Path, kind: str,
                           required: str | None = None) -> dict:
    """Stored-shape validation on LOAD (review M7): every store's file
    must contain a JSON OBJECT (and, when `required` is named, that
    key) — a file containing `[]` or `{}` used to be returned verbatim
    by some stores while drafts/quality validated theirs, so the same
    corruption produced a clean error in one tool and a downstream
    AttributeError (or a silently 'refreshed' husk) in another."""
    if not isinstance(data, dict):
        raise ValueError(
            f"corrupt {kind} file {path.name}: expected a JSON object, "
            f"got {type(data).__name__}")
    if required is not None and required not in data:
        raise ValueError(
            f"corrupt {kind} file {path.name}: missing required key "
            f"{required!r}")
    return data


def personas_dir() -> Path:
    override = os.environ.get("CONTENT_CORE_PERSONAS_DIR")
    return Path(override) if override else DATA_DIR


def load_persona(path: Path | None = None) -> dict:
    return json.loads(
        (path or personas_dir() / "persona_linh.json").read_text(encoding="utf-8"))


def load_persona_by_id(persona_id: str) -> dict:
    """Resolve a persona by id — the name a brief carries as
    `persona_id` (the graph state's persona_id is the persona's NAME,
    e.g. "Linh" / "An"). Resolution: the persona_<slug>.json file for
    the slugified id, else any persona_*.json whose data.name matches
    case-insensitively. An unknown id raises ValueError: the writer
    tools must NEVER silently serve the default persona for a brief
    that named another one (trial finding, 22 Nov 2026 — the mirror of
    the facts store's never-borrow rule)."""
    pid = str(persona_id or "").strip()
    if not pid:
        raise ValueError("persona_id must be a non-empty string")
    direct = personas_dir() / f"persona_{slugify(pid)}.json"
    if direct.exists():
        return _require_stored_object(_load_json_file(direct), direct,
                                      "persona", required="data")
    for path in sorted(personas_dir().glob("persona_*.json")):
        try:
            card = _load_json_file(path)
        except ValueError:
            continue
        if not isinstance(card, dict):
            continue
        data = card.get("data")
        name = data.get("name") if isinstance(data, dict) else None
        if isinstance(name, str) and name.casefold() == pid.casefold():
            return _require_stored_object(card, path, "persona",
                                          required="data")
    raise ValueError(
        f"unknown persona_id: {persona_id!r} — no stored persona card; "
        f"refusing to fall back to the default persona")


def load_brand(path: Path | None = None) -> dict:
    brand = yaml.safe_load(
        (path or DATA_DIR / "brand_mocbrew.yaml").read_text(encoding="utf-8"))
    if isinstance(brand, dict) and brand.get("visual") is not None:
        brand["visual"] = validate_visual(brand["visual"])
    return brand


# --- Brand visual section (article-images style lock) ---
# The optional `visual` section of a brand ({style_summary, palette[],
# medium, mood, composition, avoid[]}) is rendered by
# content_core.images.style_block into the style block appended to
# every generation prompt. It is validated + normalised HERE, at the
# load/save boundaries, like every other stored shape (review round 2
# M3: nothing validated it, so a palette written as a plain YAML
# string — the natural way to write one — was character-joined by the
# renderer, "warm" -> "w, a, r, m", and every check derived the
# expected block from the same garbled rendering, so the corruption
# was invisible).

_VISUAL_LIST_FIELDS = ("palette", "avoid")
_VISUAL_SCALAR_FIELDS = ("medium", "mood", "composition", "style_summary")


def validate_visual(visual: dict) -> dict:
    """Validate + normalise a brand `visual` section; returns a
    normalised COPY (unknown extra keys are preserved untouched).

    List fields (`palette`, `avoid`) accept a list of strings OR one
    plain string: a string is split on commas into items, because
    that is how a human writes it in YAML — "brown, cream" means two
    palette entries, "warm earth tones" (no comma) means one. Scalar
    fields (`medium`, `mood`, `composition`, `style_summary`) must
    be strings. Anything else — a non-object section, a numeric or
    mapping list field, a non-string list item, a non-string scalar —
    raises ValueError NAMING the field, so a malformed brand fails
    loudly at load instead of garbling prompts downstream."""
    if not isinstance(visual, dict):
        raise ValueError(
            f"brand visual section must be an object, "
            f"got {type(visual).__name__}")
    out = dict(visual)
    for field in _VISUAL_LIST_FIELDS:
        value = visual.get(field)
        if value is None:
            continue
        if isinstance(value, str):
            items = [part.strip() for part in value.split(",")]
            out[field] = [part for part in items if part]
        elif isinstance(value, list):
            if not all(isinstance(item, str) for item in value):
                raise ValueError(
                    f"brand visual {field} must be a list of strings")
            out[field] = [item.strip() for item in value if item.strip()]
        else:
            raise ValueError(
                f"brand visual {field} must be a list of strings or a "
                f"comma-separated string, got {type(value).__name__}")
    for field in _VISUAL_SCALAR_FIELDS:
        value = visual.get(field)
        if value is not None and not isinstance(value, str):
            raise ValueError(
                f"brand visual {field} must be a string, "
                f"got {type(value).__name__}")
    return out


# --- Facts profiles (per-brand fact bases) ---
# The fact base is PER-BRAND (core-completion round): fact_check used
# to be hardwired to Mộc Brew's facts file, so truthful content for
# any second brand could never pass (the Cao Nguyên Mộc calibration:
# its true 89.000đ price failed as wrong_price against Mộc Brew's
# 185.000đ). Facts documents now live in a store keyed by brand_id —
# data/facts_profiles/<brand-id>.json, the same stored-profile pattern
# as style/quality profiles; tests point
# CONTENT_CORE_FACTS_PROFILES_DIR at a tmp dir. The shipped
# data/facts_lorebook.json remains the moc-brew fallback when no
# stored moc-brew profile exists (byte-equivalent behaviour).

def facts_profiles_dir() -> Path:
    override = os.environ.get("CONTENT_CORE_FACTS_PROFILES_DIR")
    return Path(override) if override else DATA_DIR / "facts_profiles"


def _facts_profile_path(brand_id: str) -> Path:
    return facts_profiles_dir() / f"{stored_file_stem(brand_id)}.json"


def validate_facts(doc: dict) -> dict:
    """Schema-check a facts document (the save_facts discipline, same
    as save_brand_draft's: checked BEFORE anything is written).
    Required: brand_id; facts.price (price_vnd_per_250g positive int
    OR a price_display containing digits); facts.pack_weight_g > 0;
    facts.origins a non-empty list of names. Optional:
    known_prices_vnd (verified prices beyond the primary pack),
    pack_weights_g (the brand's real pack sizes), delivery_days,
    roast_weekday, and lorebook `entries` (dicts with id + content).
    Raises ValueError with a specific message; returns the doc."""
    if not isinstance(doc, dict):
        raise ValueError("facts document must be an object")
    brand_id = doc.get("brand_id")
    if not isinstance(brand_id, str) or not brand_id.strip():
        raise ValueError("facts document needs a non-empty brand_id")
    facts = doc.get("facts")
    if not isinstance(facts, dict):
        raise ValueError("facts document needs a 'facts' object")
    price = facts.get("price_vnd_per_250g")
    display = facts.get("price_display")
    if not (isinstance(price, int) and not isinstance(price, bool) and price > 0) \
            and not (isinstance(display, str) and any(c.isdigit() for c in display)):
        raise ValueError(
            "facts.price: give price_vnd_per_250g (positive int) or a "
            "price_display containing the amount")
    weight = facts.get("pack_weight_g")
    if not (isinstance(weight, (int, float)) and not isinstance(weight, bool)
            and weight > 0):
        raise ValueError("facts.pack_weight_g must be a positive number")
    origins = facts.get("origins")
    if not (isinstance(origins, list) and origins
            and all(isinstance(o, str) and o.strip() for o in origins)):
        raise ValueError("facts.origins must be a non-empty list of origin names")
    for key in ("known_prices_vnd",):
        values = facts.get(key)
        if values is not None and not (
                isinstance(values, list)
                and all(isinstance(v, int) and not isinstance(v, bool) and v > 0
                        for v in values)):
            raise ValueError(f"facts.{key} must be a list of positive ints")
    weights = facts.get("pack_weights_g")
    if weights is not None and not (
            isinstance(weights, list) and weights
            and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                    and v > 0 for v in weights)):
        raise ValueError("facts.pack_weights_g must be a non-empty list of positive numbers")
    for key in ("delivery_days", "roast_weekday"):
        if key in facts and not isinstance(facts[key], str):
            raise ValueError(f"facts.{key} must be a string when present")
    entries = doc.get("entries")
    if entries is not None:
        if not isinstance(entries, list) or not all(
                isinstance(e, dict) and e.get("id") and e.get("content")
                for e in entries):
            raise ValueError("entries must be a list of objects with id and content")
    return doc


def save_facts(doc: dict) -> dict:
    """Validate + persist a facts document, keyed by its brand_id
    (atomic write, like every stored profile). Returns the doc."""
    validate_facts(doc)
    atomic_write_text(_facts_profile_path(doc["brand_id"]),
                      json.dumps(doc, ensure_ascii=False, indent=2))
    return doc


def load_facts_profile(brand_id: str) -> dict | None:
    """Return the stored facts document for a brand, or None."""
    path = _facts_profile_path(brand_id)
    if not path.exists():
        return None
    return _require_stored_object(_load_json_file(path), path,
                                  "facts profile", required="facts")


def list_facts_profiles() -> list[dict]:
    """Summaries of every stored facts profile: brand_id, price.
    A corrupt file is LISTED as corrupt (review L6) — silently
    skipping it made a broken profile vanish from every listing while
    a direct get errored, the worst of both worlds."""
    d = facts_profiles_dir()
    if not d.exists():
        return []
    out = []
    for path in sorted(d.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                raise ValueError("not a JSON object")
        except (json.JSONDecodeError, OSError, ValueError):
            out.append({"brand_id": path.stem, "corrupt": True})
            continue
        facts = doc.get("facts") if isinstance(doc.get("facts"), dict) else {}
        out.append({"brand_id": doc.get("brand_id", path.stem),
                    "price_display": facts.get("price_display", ""),
                    "simulated": doc.get("simulated", False),
                    "corrupt": False})
    return out


def format_vnd(amount: int) -> str:
    """185000 -> '185.000đ' (the display form the facts files use)."""
    return f"{amount:,}".replace(",", ".") + "đ"


def normalize_facts_doc(doc: dict) -> dict:
    """Derive/default the fields consumers index, AT LOAD TIME
    (review M1/M2 — the validator/checker contract fix).

    validate_facts accepts a facts doc with `price_vnd_per_250g` and
    NO `price_display` (an either/or rule), and lorebook entries with
    only `id` + `content`. Two consumers used to index the missing
    fields directly — fact_check's wrong_price detail did
    `f['price_display']` (KeyError) and the graph research node did
    `e['keys']` (KeyError) — so a document the validator explicitly
    accepted crashed the checker and the whole graph flow. The
    contract now lives HERE, at the single load choke point: any doc
    load_facts returns carries a derived price_display (formatted
    from the int price) and a `keys` list on every entry. The stored
    file is not rewritten; the derivation is deterministic."""
    if not isinstance(doc, dict):
        return doc
    facts = doc.get("facts")
    if isinstance(facts, dict):
        price = facts.get("price_vnd_per_250g")
        if not facts.get("price_display") \
                and isinstance(price, int) and not isinstance(price, bool) \
                and price > 0:
            facts["price_display"] = format_vnd(price)
    entries = doc.get("entries")
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict):
                entry.setdefault("keys", [])
    return doc


def load_facts(brand_id: str = "moc-brew", path: Path | None = None) -> dict:
    """Resolve the fact base for a brand.

    `path` (explicit file) wins — the legacy single-file load. Otherwise
    the stored facts profile for `brand_id`; for "moc-brew" with no
    stored profile, the shipped data/facts_lorebook.json. An unknown
    brand_id raises ValueError: NEVER a silent fallback to another
    brand's facts — a wrong fact base is worse than no fact base."""
    if path is not None:
        return normalize_facts_doc(_load_json_file(path))
    stored = load_facts_profile(brand_id)
    if stored is not None:
        return normalize_facts_doc(stored)
    if brand_id == "moc-brew":
        return normalize_facts_doc(_load_json_file(DATA_DIR / "facts_lorebook.json"))
    raise ValueError(
        f"no facts stored for brand_id {brand_id!r} — save a facts "
        f"profile first (save_facts); refusing to fall back to "
        f"another brand's facts")


# --- Layer 4: Craft & Domain Library (separate from Persona/Brand/Facts) ---

def load_craft_library(path: Path | None = None) -> dict:
    return json.loads((path or DATA_DIR / "craft_library.json").read_text(encoding="utf-8"))


def load_exemplars(path: Path | None = None) -> dict:
    return json.loads((path or DATA_DIR / "exemplars.json").read_text(encoding="utf-8"))


def load_domain_glossary(path: Path | None = None) -> dict:
    return json.loads((path or DATA_DIR / "domain_glossary.json").read_text(encoding="utf-8"))


# --- Style profiles (Style Extraction) ---
# ALL file IO for style profiles lives HERE, in loaders.py — the same rule
# the MCP server follows for drafts. content_core/style.py is pure
# computation (fingerprint / build / match) and never touches the disk;
# mcp_server.py persists profiles only through these helpers.
# Profiles live in data/style_profiles/<id>.json; tests point
# CONTENT_CORE_STYLE_PROFILES_DIR at a tmp dir, like CONTENT_CORE_DRAFTS_DIR.

DEFAULT_STYLE_PROFILE_ID = "mocbrew-blog-voice"


def style_profiles_dir() -> Path:
    override = os.environ.get("CONTENT_CORE_STYLE_PROFILES_DIR")
    return Path(override) if override else DATA_DIR / "style_profiles"


def _style_profile_path(profile_id: str) -> Path:
    return style_profiles_dir() / f"{stored_file_stem(profile_id)}.json"


def save_style_profile(profile: dict) -> dict:
    """Persist a profile dict (must carry an 'id'). Returns the profile."""
    atomic_write_text(_style_profile_path(profile["id"]),
                      json.dumps(profile, ensure_ascii=False, indent=2))
    return profile


def load_style_profile(profile_id: str) -> dict | None:
    """Return the stored profile, or None when no such profile exists.
    The stored shape is validated (review M7): a non-object file, or
    an object with no 'id', is the named corrupt-file error — a `{}`
    profile used to be silently 'refreshed' into a husk with no
    id/name/fingerprint and served as if it were a real profile."""
    path = _style_profile_path(profile_id)
    if not path.exists():
        return None
    return _require_stored_object(_load_json_file(path), path,
                                  "style profile", required="id")


def list_style_profiles() -> list[dict]:
    """Summaries of every stored profile: id, name, brand, sample stats.
    Corrupt files are listed as corrupt, never silently skipped
    (review L6 — see list_facts_profiles)."""
    d = style_profiles_dir()
    if not d.exists():
        return []
    out = []
    for path in sorted(d.glob("*.json")):
        try:
            p = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(p, dict):
                raise ValueError("not a JSON object")
        except (json.JSONDecodeError, OSError, ValueError):
            out.append({"id": path.stem, "corrupt": True})
            continue
        created = p.get("created_from") if isinstance(p.get("created_from"), dict) else {}
        out.append({"id": p.get("id", path.stem), "name": p.get("name", ""),
                    "brand_id": p.get("brand_id", ""),
                    "sample_count": created.get("sample_count", 0),
                    "total_words": created.get("total_words", 0),
                    "corrupt": False})
    return out


# --- Brand drafts (Brand Builder) ---
# Same pattern as style profiles: ALL file IO for brand drafts lives HERE.
# content_core/brand_extract.py is pure computation; mcp_server.py persists
# drafts only through these helpers. Drafts live in
# data/brand_drafts/<brand-id>.json; tests point
# CONTENT_CORE_BRAND_DRAFTS_DIR at a tmp dir, like the profiles dir.

def brand_drafts_dir() -> Path:
    override = os.environ.get("CONTENT_CORE_BRAND_DRAFTS_DIR")
    return Path(override) if override else DATA_DIR / "brand_drafts"


def _brand_draft_path(draft_id: str) -> Path:
    return brand_drafts_dir() / f"{stored_file_stem(draft_id)}.json"


def validate_brand_draft(draft: dict) -> dict:
    """Schema-check a brand draft BEFORE it is persisted (review M10:
    save_brand_draft used to persist ANY dict — contrast save_facts,
    which validates — so a hand-crafted draft could strip the
    competitor guard's markings and later become a live gate brand
    via _resolve_brand_facts). Rules: the draft is an object with a
    `brand` object carrying a non-empty id; `examples` /
    `anti_examples`, when present, are lists of objects; and for a
    COMPETITOR draft every example is (re)stamped reference_only=True
    — the marking is enforced here, at the persistence boundary, not
    merely trusted from the builder. Raises ValueError; returns the
    (possibly stamped) draft."""
    if not isinstance(draft, dict):
        raise ValueError("brand draft must be an object")
    brand = draft.get("brand")
    if not isinstance(brand, dict) or not str(brand.get("id") or "").strip():
        raise ValueError("brand draft needs a 'brand' object with a non-empty id")
    for key in ("examples", "anti_examples"):
        entries = draft.get(key)
        if entries is not None and not (
                isinstance(entries, list)
                and all(isinstance(e, dict) for e in entries)):
            raise ValueError(f"brand draft {key} must be a list of objects")
    if brand.get("source_type") == "competitor":
        for entry in draft.get("examples", []):
            entry["reference_only"] = True
    if draft.get("visual") is not None:
        # Normalised at the persistence boundary like the rest of the
        # draft (review round 2 M3): a string palette is stored as the
        # list it means, a malformed one never reaches disk.
        draft["visual"] = validate_visual(draft["visual"])
    return draft


def save_brand_draft(draft: dict) -> dict:
    """Validate + persist a brand draft dict (keyed by
    draft['brand']['id']). Returns the draft."""
    validate_brand_draft(draft)
    atomic_write_text(_brand_draft_path(draft["brand"]["id"]),
                      json.dumps(draft, ensure_ascii=False, indent=2))
    return draft


def load_brand_draft(draft_id: str) -> dict | None:
    """Return the stored brand draft, or None when it does not exist.
    Stored shape is validated like every other store (review M7):
    an object with a `brand` object, or the named corrupt error."""
    path = _brand_draft_path(draft_id)
    if not path.exists():
        return None
    data = _require_stored_object(_load_json_file(path), path,
                                  "brand draft", required="brand")
    if not isinstance(data.get("brand"), dict):
        raise ValueError(
            f"corrupt brand draft file {path.name}: 'brand' must be an object")
    if data.get("visual") is not None:
        # Legacy / hand-edited files get the same normalisation the
        # save boundary applies (review round 2 M3).
        data["visual"] = validate_visual(data["visual"])
    return data


def list_brand_drafts() -> list[dict]:
    """Summaries of every stored brand draft: id, name, source, status.
    Corrupt files are listed as corrupt, never silently skipped
    (review L6 — see list_facts_profiles)."""
    d = brand_drafts_dir()
    if not d.exists():
        return []
    out = []
    for path in sorted(d.glob("*.json")):
        try:
            draft = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(draft, dict):
                raise ValueError("not a JSON object")
        except (json.JSONDecodeError, OSError, ValueError):
            out.append({"id": path.stem, "corrupt": True})
            continue
        brand = draft.get("brand") if isinstance(draft.get("brand"), dict) else {}
        out.append({"id": brand.get("id", path.stem),
                    "name": brand.get("name", ""),
                    "source_type": brand.get("source_type", ""),
                    "review_status": brand.get("review_status", ""),
                    "source_urls": brand.get("source_urls", []),
                    "corrupt": False})
    return out


# --- Quality profiles (Quality Profile gate layer) ---
# Same pattern as style profiles: ALL file IO for quality profiles lives
# HERE. content_core/quality.py is pure computation + validation and never
# touches the disk; mcp_server.py persists profiles only through these
# helpers. Profiles live in data/quality_profiles/<id>.json; tests point
# CONTENT_CORE_QUALITY_PROFILES_DIR at a tmp dir, like the other dirs.

def quality_profiles_dir() -> Path:
    override = os.environ.get("CONTENT_CORE_QUALITY_PROFILES_DIR")
    return Path(override) if override else DATA_DIR / "quality_profiles"


def _quality_profile_path(profile_id: str) -> Path:
    return quality_profiles_dir() / f"{stored_file_stem(profile_id)}.json"


def save_quality_profile(profile: dict) -> dict:
    """Persist a quality profile dict (must carry an 'id'). Returns it."""
    atomic_write_text(_quality_profile_path(profile["id"]),
                      json.dumps(profile, ensure_ascii=False, indent=2))
    return profile


def load_quality_profile(profile_id: str) -> dict | None:
    """Return the stored quality profile, or None when it does not
    exist. Non-object files are the named corrupt error (review M7);
    field-level validation stays in quality.validate_profile, which
    get_profile applies to whatever this returns."""
    path = _quality_profile_path(profile_id)
    if not path.exists():
        return None
    return _require_stored_object(_load_json_file(path), path,
                                  "quality profile")


def list_quality_profiles() -> list[dict]:
    """Summaries of every stored custom quality profile: id, format.
    Corrupt files are listed as corrupt, never silently skipped
    (review L6 — see list_facts_profiles)."""
    d = quality_profiles_dir()
    if not d.exists():
        return []
    out = []
    for path in sorted(d.glob("*.json")):
        try:
            p = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(p, dict):
                raise ValueError("not a JSON object")
        except (json.JSONDecodeError, OSError, ValueError):
            out.append({"id": path.stem, "corrupt": True})
            continue
        out.append({"id": p.get("id", path.stem), "format": p.get("format", ""),
                    "pass_total": p.get("pass_total"), "corrupt": False})
    return out


def slugify(name: str | None, fallback: str = "brand") -> str:
    """URL/id slug for a display name: lowercase, Vietnamese diacritics
    folded to ASCII, non-alphanumeric runs become single dashes. ONE
    implementation (review L2): style.py and brand_extract.py each
    carried a private copy that differed in the fallback word and in
    None handling — exactly the kind of twin that drifts."""
    nfkd = unicodedata.normalize("NFD", (name or "").lower().replace("đ", "d"))
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-") or fallback


def brand_check_context(brand: dict) -> tuple[str | None, int | None, list[str] | None]:
    """(brand_id, max_sentence_words, banned_terms) for the checking
    layers — the brand-derived context craft needs from the brand dict
    (evaluation R3-S4 wiring). ONE implementation (review L2):
    gates.py and quality.py each carried a verbatim copy of this
    extraction; the R3-S4 bug class was precisely this wiring being
    present in one path and missing in another, and two hand-kept
    copies invite the same drift again. Missing/malformed pieces
    degrade to None (the checkers' documented defaults), never raise."""
    brand_id = (brand.get("brand") or {}).get("id")
    try:
        max_sentence_words = int(
            brand["voice"]["sentence_rules"]["max_sentence_words"])
    except (KeyError, TypeError, ValueError):
        max_sentence_words = None
    try:
        terms: list[str] | None = banned_terms(brand)
    except (KeyError, TypeError):
        terms = None
    return brand_id, max_sentence_words, terms


def banned_terms(brand: dict) -> list[str]:
    return [str(t).lower() for t in brand["voice"]["banned_terms"]]


def prohibited_claims(brand: dict) -> list[str]:
    return [str(t).lower() for t in brand["voice"].get("prohibited_claims", [])]


def median_range(brand: dict, fmt: str) -> tuple[int, int]:
    rules = brand["voice"]["sentence_rules"]
    lo, hi = rules.get("median_words_by_format", {}).get(fmt, rules["median_words_default"])
    return int(lo), int(hi)


# --- Image library (per-brand photo store) ---
# Same pattern as the other stores: ALL file IO lives HERE;
# content_core/images.py is pure computation; mcp_server.py persists
# only through these helpers. Each brand/client has its own library
# at data/image_library/<brand-id>/index.json; tests point
# CONTENT_CORE_IMAGE_LIBRARY_DIR at a tmp dir, like the other dirs.
# The library stores REFS ONLY (paths/URLs) — the core never copies
# image bytes, exactly as it never generates images.

def image_library_dir() -> Path:
    override = os.environ.get("CONTENT_CORE_IMAGE_LIBRARY_DIR")
    return Path(override) if override else DATA_DIR / "image_library"


def _image_library_index_path(brand_id: str) -> Path:
    return image_library_dir() / stored_file_stem(brand_id) / "index.json"


def validate_image_library_entry(entry: dict) -> dict:
    """Schema-check one library entry BEFORE it is persisted (the
    save_facts / save_brand_draft discipline). Required: ref
    (non-empty), description (non-empty — ranking and alt suggestion
    both read it), source in {"client", "ai"}. tags defaults to [].
    Raises ValueError; returns the entry."""
    if not isinstance(entry, dict):
        raise ValueError("image library entry must be an object")
    if not str(entry.get("ref") or "").strip():
        raise ValueError("image library entry needs a non-empty ref")
    if not str(entry.get("description") or "").strip():
        raise ValueError(
            "image library entry needs a non-empty description "
            "(what the image shows — ranking and alt text read it)")
    if entry.get("source") not in ("client", "ai"):
        raise ValueError(
            f"image library entry source must be 'client' or 'ai', "
            f"got {entry.get('source')!r}")
    tags = entry.get("tags")
    if tags is not None and not (isinstance(tags, list)
                                 and all(isinstance(t, str) for t in tags)):
        raise ValueError("image library entry tags must be a list of strings")
    return entry


def load_image_library(brand_id: str) -> dict:
    """The brand's library document {"brand_id", "images": [...]}.
    A brand with no library yet gets an empty one (not an error —
    fulfilment then simply reports every slot as needs_generation).
    Stored shape is validated like every other store (review M7)."""
    path = _image_library_index_path(brand_id)
    if not path.exists():
        return {"brand_id": brand_id, "images": []}
    data = _require_stored_object(_load_json_file(path), path,
                                  "image library", required="images")
    if not isinstance(data.get("images"), list) \
            or not all(isinstance(e, dict) for e in data["images"]):
        raise ValueError(
            f"corrupt image library file {path.name}: 'images' must be "
            f"a list of objects")
    data.setdefault("brand_id", brand_id)
    return data


def save_image_library(brand_id: str, library: dict) -> dict:
    """Persist a library document (atomic write). Returns it."""
    path = _image_library_index_path(brand_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(library, ensure_ascii=False, indent=2))
    return library


def add_image_library_entry(brand_id: str, ref: str, description: str,
                            tags: list[str] | None = None,
                            source: str = "client") -> dict:
    """Validate + append one entry to a brand's library. The entry id
    derives from ref+description, so re-adding the same image is an
    idempotent replace, never a duplicate. Returns the entry."""
    from datetime import datetime, timezone

    entry = {"id": "lib-" + hashlib.sha1(
        f"{ref}|{description}".encode("utf-8")).hexdigest()[:10],
        "ref": ref, "source": source, "description": description,
        "tags": tags if tags is not None else [],
        "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    validate_image_library_entry(entry)
    library = load_image_library(brand_id)
    entries = [e for e in library["images"] if e.get("id") != entry["id"]]
    entries.append(entry)
    library["images"] = entries
    save_image_library(brand_id, library)
    return entry


def list_image_library(brand_id: str) -> list[dict]:
    """All entries of a brand's library ([] when none exists yet).
    The brand id is validated at the boundary like every store."""
    stored_file_stem(brand_id)  # raises ValueError for unusable ids
    return load_image_library(brand_id)["images"]


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- Research packs (specs/research-config-feature.md) ---
# The pack is the ONE new core artefact of the research &
# configuration feature: a per-brand PRE-APPROVAL store. Nothing in a
# pack is live configuration — items become live only when apply
# writes them through the existing save paths (save_brand_draft /
# save_facts / save_style_profile / persona files / corpus choices).
# One JSON per brand at data/research_packs/<brand-id>.json; tests
# point CONTENT_CORE_RESEARCH_PACKS_DIR at a tmp dir.

PACK_ITEM_KINDS = ("brand_rule", "fact", "style_candidate",
                   "persona_frame", "corpus_choice")
PACK_ITEM_STATUSES = ("proposed", "approved", "edited", "rejected",
                      "applied")
PACK_SOURCE_KINDS = ("own", "competitor", "genre", "factual")


def research_packs_dir() -> Path:
    override = os.environ.get("CONTENT_CORE_RESEARCH_PACKS_DIR")
    return Path(override) if override else DATA_DIR / "research_packs"


def _research_pack_path(brand_id: str) -> Path:
    return research_packs_dir() / f"{stored_file_stem(brand_id)}.json"


def validate_research_pack(pack: dict) -> dict:
    """Schema-check a research pack BEFORE it is persisted (the
    save_facts / save_brand_draft discipline). Required: brand_id;
    sources / items / contradictions / quality_rejections are lists
    of objects; every item carries a known kind + status; every FACT
    item carries a provenance source_url (spec US-1: a fact item
    without a source URL must not exist in a pack). Raises ValueError;
    returns the pack."""
    if not isinstance(pack, dict):
        raise ValueError("research pack must be an object")
    if not str(pack.get("brand_id") or "").strip():
        raise ValueError("research pack needs a non-empty brand_id")
    for key in ("sources", "items", "contradictions", "quality_rejections"):
        entries = pack.get(key, [])
        if not isinstance(entries, list) \
                or not all(isinstance(e, dict) for e in entries):
            raise ValueError(f"research pack {key} must be a list of objects")
    for item in pack.get("items", []):
        if item.get("kind") not in PACK_ITEM_KINDS:
            raise ValueError(
                f"pack item kind must be one of {PACK_ITEM_KINDS}, "
                f"got {item.get('kind')!r}")
        if item.get("status") not in PACK_ITEM_STATUSES:
            raise ValueError(
                f"pack item status must be one of {PACK_ITEM_STATUSES}, "
                f"got {item.get('status')!r}")
        if not isinstance(item.get("payload"), dict):
            raise ValueError("pack item payload must be an object")
        provenance = item.get("provenance")
        if not isinstance(provenance, dict):
            raise ValueError("pack item provenance must be an object")
        if item["kind"] == "fact" \
                and not str(provenance.get("source_url") or "").strip():
            raise ValueError(
                "fact items need a provenance source_url (spec US-1: "
                "a fact without a source must not exist in a pack)")
    for source in pack.get("sources", []):
        if source.get("kind") not in PACK_SOURCE_KINDS:
            raise ValueError(
                f"pack source kind must be one of {PACK_SOURCE_KINDS}, "
                f"got {source.get('kind')!r}")
    return pack


def save_research_pack(pack: dict) -> dict:
    """Validate + persist a research pack (atomic write). Returns it."""
    validate_research_pack(pack)
    pack["updated_at"] = _utc_now()
    atomic_write_text(_research_pack_path(pack["brand_id"]),
                      json.dumps(pack, ensure_ascii=False, indent=2))
    return pack


def load_research_pack(brand_id: str) -> dict | None:
    """Return the stored pack for a brand, or None. Stored shape is
    validated like every other store (review M7)."""
    path = _research_pack_path(brand_id)
    if not path.exists():
        return None
    return _require_stored_object(_load_json_file(path), path,
                                  "research pack", required="brand_id")


def list_research_packs() -> list[dict]:
    """Summaries of every stored pack: brand_id, version, item counts.
    Corrupt files are listed as corrupt, never silently skipped."""
    d = research_packs_dir()
    if not d.exists():
        return []
    out = []
    for path in sorted(d.glob("*.json")):
        try:
            pack = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(pack, dict):
                raise ValueError("not a JSON object")
        except (json.JSONDecodeError, OSError, ValueError):
            out.append({"brand_id": path.stem, "corrupt": True})
            continue
        out.append({"brand_id": pack.get("brand_id", path.stem),
                    "version": pack.get("version", 1),
                    "items": len(pack.get("items", [])),
                    "corrupt": False})
    return out


# --- Corpus registry (system-level, Smith's decision 2026-11-22) ---
# Genre corpora are registered ONCE at system level; brands then
# CHOOSE a corpus (see corpus choices below) and the AI uses the
# chosen corpus's distilled style profile as the reference sample set.
# The registry records provenance for the corpus itself: what it is,
# which style profile it feeds, the legitimacy basis for using it,
# and its measured size. One JSON per corpus at
# data/corpus_registry/<corpus-id>.json; tests point
# CONTENT_CORE_CORPUS_REGISTRY_DIR at a tmp dir.

CORPUS_KINDS = ("press", "literature", "genre", "other")


def corpus_registry_dir() -> Path:
    override = os.environ.get("CONTENT_CORE_CORPUS_REGISTRY_DIR")
    return Path(override) if override else DATA_DIR / "corpus_registry"


def _corpus_entry_path(corpus_id: str) -> Path:
    return corpus_registry_dir() / f"{stored_file_stem(corpus_id)}.json"


def validate_corpus_entry(entry: dict) -> dict:
    """Schema-check a corpus registry entry BEFORE it is persisted.
    Required: id, version (positive int), title, kind, profile_id
    (the style profile the corpus feeds), legitimacy_basis (why the
    corpus may be used — a corpus without a recorded basis must not
    be registered), word_count (non-negative int). Raises ValueError;
    returns the entry."""
    if not isinstance(entry, dict):
        raise ValueError("corpus entry must be an object")
    if not str(entry.get("id") or "").strip():
        raise ValueError("corpus entry needs a non-empty id")
    version = entry.get("version")
    if not (isinstance(version, int) and not isinstance(version, bool)
            and version > 0):
        raise ValueError("corpus entry version must be a positive int")
    for key in ("title", "profile_id", "legitimacy_basis"):
        if not str(entry.get(key) or "").strip():
            raise ValueError(f"corpus entry needs a non-empty {key}")
    if entry.get("kind") not in CORPUS_KINDS:
        raise ValueError(
            f"corpus kind must be one of {CORPUS_KINDS}, "
            f"got {entry.get('kind')!r}")
    words = entry.get("word_count")
    if not (isinstance(words, int) and not isinstance(words, bool)
            and words >= 0):
        raise ValueError("corpus entry word_count must be a non-negative int")
    return entry


def save_corpus_entry(entry: dict) -> dict:
    """Validate + persist a registry entry (atomic write)."""
    validate_corpus_entry(entry)
    atomic_write_text(_corpus_entry_path(entry["id"]),
                      json.dumps(entry, ensure_ascii=False, indent=2))
    return entry


def load_corpus_entry(corpus_id: str) -> dict | None:
    """Return the registry entry for a corpus, or None."""
    path = _corpus_entry_path(corpus_id)
    if not path.exists():
        return None
    return _require_stored_object(_load_json_file(path), path,
                                  "corpus entry", required="id")


def list_corpus_entries() -> list[dict]:
    """Every registered corpus entry (full records — the registry is
    small and reference data). Corrupt files are listed as corrupt."""
    d = corpus_registry_dir()
    if not d.exists():
        return []
    out = []
    for path in sorted(d.glob("*.json")):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(entry, dict):
                raise ValueError("not a JSON object")
        except (json.JSONDecodeError, OSError, ValueError):
            out.append({"id": path.stem, "corrupt": True})
            continue
        entry.setdefault("corrupt", False)
        out.append(entry)
    return out


# --- Brand corpus choices ---
# The per-brand half of the corpus decision: which registered corpus
# (and version) a brand has chosen, and which style profile that
# resolves to. One JSON per brand at
# data/corpus_choices/<brand-id>.json; tests point
# CONTENT_CORE_CORPUS_CHOICES_DIR at a tmp dir. assemble_context reads
# the choice as a DEFAULT style profile only — an explicit
# style_profile_id in the brief always wins.

def corpus_choices_dir() -> Path:
    override = os.environ.get("CONTENT_CORE_CORPUS_CHOICES_DIR")
    return Path(override) if override else DATA_DIR / "corpus_choices"


def _corpus_choice_path(brand_id: str) -> Path:
    return corpus_choices_dir() / f"{stored_file_stem(brand_id)}.json"


def save_corpus_choice(brand_id: str, choice: dict) -> dict:
    """Validate + persist a brand's corpus choice. The choice records
    corpus_id, version and the resolved profile_id so the default is
    stable even if the registry entry is later edited. Returns it."""
    if not isinstance(choice, dict):
        raise ValueError("corpus choice must be an object")
    if not str(choice.get("corpus_id") or "").strip():
        raise ValueError("corpus choice needs a corpus_id")
    if not str(choice.get("profile_id") or "").strip():
        raise ValueError("corpus choice needs a profile_id")
    record = {"brand_id": brand_id,
              "corpus_id": choice["corpus_id"],
              "version": choice.get("version"),
              "profile_id": choice["profile_id"],
              "source": choice.get("source", "direct"),
              "set_at": _utc_now()}
    atomic_write_text(_corpus_choice_path(brand_id),
                      json.dumps(record, ensure_ascii=False, indent=2))
    return record


def load_corpus_choice(brand_id: str) -> dict | None:
    """Return the brand's stored corpus choice, or None. When no
    choice record exists, a brand DRAFT carrying a `corpus_choice`
    key is honoured as a fallback (the choice stamped onto the
    brand's config at apply time)."""
    path = _corpus_choice_path(brand_id)
    if path.exists():
        return _require_stored_object(_load_json_file(path), path,
                                      "corpus choice", required="corpus_id")
    draft = load_brand_draft(brand_id)
    if draft is not None and isinstance(draft.get("corpus_choice"), dict) \
            and draft["corpus_choice"].get("corpus_id"):
        return draft["corpus_choice"]
    return None


def delete_corpus_choice(brand_id: str) -> dict:
    """Remove a brand's corpus choice from BOTH places it can live:
    the choice store record and the `corpus_choice` stamp a research
    pack apply leaves on the brand draft (load_corpus_choice honours
    either, so clearing only one would leave the choice alive).
    Returns a report — {brand_id, cleared, store_cleared,
    draft_stamp_cleared} — where `cleared` is True when anything was
    actually removed; clearing a brand with no choice is a clean
    no-op report, not an error. The draft is loaded BEFORE any write
    so a corrupt draft surfaces as the usual ValueError with nothing
    touched."""
    draft = load_brand_draft(brand_id)
    path = _corpus_choice_path(brand_id)
    store_cleared = path.exists()
    if store_cleared:
        path.unlink()
    stamp_cleared = False
    if draft is not None and "corpus_choice" in draft:
        del draft["corpus_choice"]
        save_brand_draft(draft)
        stamp_cleared = True
    return {"brand_id": brand_id,
            "cleared": store_cleared or stamp_cleared,
            "store_cleared": store_cleared,
            "draft_stamp_cleared": stamp_cleared}


# --- Taste stores (learning loop, core side) ---
# Per-brand record of the OWNER's verdicts on finished articles:
# approved unchanged, or edited (both versions kept — the diff is the
# learning signal). Imported-but-unmarked articles sit at verdict
# "pending": they are candidates, NOT approvals, and never feed a
# taste profile until the owner marks them. One JSON per brand at
# data/taste/<brand-id>.json; tests point CONTENT_CORE_TASTE_DIR at a
# tmp dir.

TASTE_VERDICTS = ("pending", "approved_unchanged", "edited")


def taste_dir() -> Path:
    override = os.environ.get("CONTENT_CORE_TASTE_DIR")
    return Path(override) if override else DATA_DIR / "taste"


def _taste_path(brand_id: str) -> Path:
    return taste_dir() / f"{stored_file_stem(brand_id)}.json"


def validate_taste_doc(doc: dict) -> dict:
    """Schema-check a taste document BEFORE it is persisted. Required:
    brand_id; entries a list of objects each carrying a known verdict
    and the article text snapshot. Raises ValueError; returns doc."""
    if not isinstance(doc, dict):
        raise ValueError("taste document must be an object")
    if not str(doc.get("brand_id") or "").strip():
        raise ValueError("taste document needs a non-empty brand_id")
    entries = doc.get("entries", [])
    if not isinstance(entries, list) \
            or not all(isinstance(e, dict) for e in entries):
        raise ValueError("taste entries must be a list of objects")
    for entry in entries:
        if entry.get("verdict") not in TASTE_VERDICTS:
            raise ValueError(
                f"taste verdict must be one of {TASTE_VERDICTS}, "
                f"got {entry.get('verdict')!r}")
        if not str(entry.get("text") or "").strip():
            raise ValueError("taste entry needs the article text snapshot")
    return doc


def save_taste_doc(doc: dict) -> dict:
    """Validate + persist a taste document (atomic write)."""
    validate_taste_doc(doc)
    atomic_write_text(_taste_path(doc["brand_id"]),
                      json.dumps(doc, ensure_ascii=False, indent=2))
    return doc


def load_taste_doc(brand_id: str) -> dict | None:
    """Return the brand's taste document, or None when the brand has
    no taste store yet."""
    path = _taste_path(brand_id)
    if not path.exists():
        return None
    return _require_stored_object(_load_json_file(path), path,
                                  "taste document", required="brand_id")


# --- WordPress export targets (P3) ---
# Per-brand NON-SECRET delivery config for the WordPress draft
# exporter (pmdecisions 2026-11-22: finished articles are delivered
# as drafts on the client's own WordPress site, never published).
# The store holds ONLY: site base URL, WP username, REST post type,
# an optional default category id. The Application Password is a
# credential and NEVER lives here — it is read at export time from
# the local secrets file / environment (content_core/wordpress.py).
# Validation deliberately keeps ONLY the known fields: a hand-edited
# target file cannot smuggle in a post status (or a credential) —
# the exporter always creates drafts, by construction.
# One JSON per brand at data/wordpress_targets/<brand-id>.json;
# tests point CONTENT_CORE_WP_TARGETS_DIR at a tmp dir.


def wordpress_targets_dir() -> Path:
    override = os.environ.get("CONTENT_CORE_WP_TARGETS_DIR")
    return Path(override) if override else DATA_DIR / "wordpress_targets"


def _wordpress_target_path(brand_id: str) -> Path:
    return wordpress_targets_dir() / f"{stored_file_stem(brand_id)}.json"


def validate_wordpress_target(target: dict) -> dict:
    """Schema-check + NORMALISE a WordPress target before it is
    persisted. Returns a fresh dict with ONLY the known fields:
    brand_id, site_url (http/https with a host, trailing slash
    stripped), username, post_type (REST route base, default
    "posts"), default_category_id (positive int or None). Raises
    ValueError on anything malformed."""
    if not isinstance(target, dict):
        raise ValueError("wordpress target must be an object")
    brand_id = str(target.get("brand_id") or "").strip()
    if not brand_id:
        raise ValueError("wordpress target needs a non-empty brand_id")
    stored_file_stem(brand_id)  # validates the id is storable
    site_url = str(target.get("site_url") or "").strip().rstrip("/")
    parsed = urlparse(site_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(
            f"site_url must be an http(s) URL with a host, "
            f"got {target.get('site_url')!r}")
    username = str(target.get("username") or "").strip()
    if not username:
        raise ValueError("wordpress target needs a non-empty username")
    post_type = str(target.get("post_type") or "posts").strip()
    if not re.fullmatch(r"[a-z0-9_-]+", post_type):
        raise ValueError(
            f"post_type must be a REST route base like 'posts', "
            f"got {post_type!r}")
    category = target.get("default_category_id")
    if category is not None:
        if isinstance(category, bool) or not isinstance(category, int) \
                or category <= 0:
            raise ValueError(
                "default_category_id must be a positive int or None")
    return {"brand_id": brand_id, "site_url": site_url,
            "username": username, "post_type": post_type,
            "default_category_id": category}


def save_wordpress_target(target: dict) -> dict:
    """Validate + persist a brand's WordPress target (atomic write).
    Returns the normalised record with its updated_at stamp."""
    record = validate_wordpress_target(target)
    record["updated_at"] = _utc_now()
    atomic_write_text(_wordpress_target_path(record["brand_id"]),
                      json.dumps(record, ensure_ascii=False, indent=2))
    return record


def load_wordpress_target(brand_id: str) -> dict | None:
    """Return the brand's stored WordPress target, normalised on
    load (a hand-edited file is re-validated, so planted fields
    never reach the exporter), or None."""
    path = _wordpress_target_path(brand_id)
    if not path.exists():
        return None
    stored = _require_stored_object(_load_json_file(path), path,
                                    "wordpress target",
                                    required="brand_id")
    return validate_wordpress_target(stored)


def list_wordpress_targets() -> list[dict]:
    """Summaries of every stored WordPress target. Corrupt files are
    listed as corrupt, never silently skipped (house discipline)."""
    d = wordpress_targets_dir()
    if not d.exists():
        return []
    out = []
    for path in sorted(d.glob("*.json")):
        try:
            target = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(target, dict):
                raise ValueError("not a JSON object")
        except (json.JSONDecodeError, OSError, ValueError):
            out.append({"brand_id": path.stem, "corrupt": True})
            continue
        out.append({"brand_id": target.get("brand_id", path.stem),
                    "site_url": target.get("site_url", ""),
                    "corrupt": False})
    return out

