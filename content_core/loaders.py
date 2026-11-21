"""Load the 3 separate data layers: Persona (Card V2) / Brand (YAML) / Facts (lorebook JSON)."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

import yaml

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` atomically: write a temp file in the SAME
    directory, fsync, then os.replace over the target. A crash mid-write
    leaves the previous file intact (at worst an orphan temp file), never
    a half-written JSON. Versioning/locking is a separate, deferred
    concern (P3) — this is rename atomicity only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent),
                                    prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
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
    (audit round 2)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"corrupt JSON in stored file {path}: {exc}") from None


def load_persona(path: Path | None = None) -> dict:
    return json.loads((path or DATA_DIR / "persona_linh.json").read_text(encoding="utf-8"))


def load_brand(path: Path | None = None) -> dict:
    return yaml.safe_load((path or DATA_DIR / "brand_mocbrew.yaml").read_text(encoding="utf-8"))


def load_facts(path: Path | None = None) -> dict:
    return json.loads((path or DATA_DIR / "facts_lorebook.json").read_text(encoding="utf-8"))


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
    """Return the stored profile, or None when no such profile exists."""
    path = _style_profile_path(profile_id)
    if not path.exists():
        return None
    return _load_json_file(path)


def list_style_profiles() -> list[dict]:
    """Summaries of every stored profile: id, name, brand, sample stats."""
    d = style_profiles_dir()
    if not d.exists():
        return []
    out = []
    for path in sorted(d.glob("*.json")):
        try:
            p = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append({"id": p.get("id", path.stem), "name": p.get("name", ""),
                    "brand_id": p.get("brand_id", ""),
                    "sample_count": p.get("created_from", {}).get("sample_count", 0),
                    "total_words": p.get("created_from", {}).get("total_words", 0)})
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


def save_brand_draft(draft: dict) -> dict:
    """Persist a brand draft dict (keyed by draft['brand']['id']).
    Returns the draft."""
    atomic_write_text(_brand_draft_path(draft["brand"]["id"]),
                      json.dumps(draft, ensure_ascii=False, indent=2))
    return draft


def load_brand_draft(draft_id: str) -> dict | None:
    """Return the stored brand draft, or None when it does not exist."""
    path = _brand_draft_path(draft_id)
    if not path.exists():
        return None
    return _load_json_file(path)


def list_brand_drafts() -> list[dict]:
    """Summaries of every stored brand draft: id, name, source, status."""
    d = brand_drafts_dir()
    if not d.exists():
        return []
    out = []
    for path in sorted(d.glob("*.json")):
        try:
            draft = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        brand = draft.get("brand", {})
        out.append({"id": brand.get("id", path.stem),
                    "name": brand.get("name", ""),
                    "source_type": brand.get("source_type", ""),
                    "review_status": brand.get("review_status", ""),
                    "source_urls": brand.get("source_urls", [])})
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
    """Return the stored quality profile, or None when it does not exist."""
    path = _quality_profile_path(profile_id)
    if not path.exists():
        return None
    return _load_json_file(path)


def list_quality_profiles() -> list[dict]:
    """Summaries of every stored custom quality profile: id, format."""
    d = quality_profiles_dir()
    if not d.exists():
        return []
    out = []
    for path in sorted(d.glob("*.json")):
        try:
            p = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append({"id": p.get("id", path.stem), "format": p.get("format", ""),
                    "pass_total": p.get("pass_total")})
    return out


def banned_terms(brand: dict) -> list[str]:
    return [str(t).lower() for t in brand["voice"]["banned_terms"]]


def prohibited_claims(brand: dict) -> list[str]:
    return [str(t).lower() for t in brand["voice"].get("prohibited_claims", [])]


def median_range(brand: dict, fmt: str) -> tuple[int, int]:
    rules = brand["voice"]["sentence_rules"]
    lo, hi = rules.get("median_words_by_format", {}).get(fmt, rules["median_words_default"])
    return int(lo), int(hi)
