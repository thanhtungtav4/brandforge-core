#!/usr/bin/env python3
"""MCP server for the Content Core — the host app's model is the writer,
these tools are its hands and its referee.

Transports (official Python MCP SDK, mcp 1.30.0, FastMCP):
- stdio (default): .venv/bin/python mcp_server.py
- http:  .venv/bin/python mcp_server.py --transport http
  Streamable HTTP on 127.0.0.1:8765 (env CONTENT_CORE_HTTP_HOST / _PORT).
  NO auth in v1 — localhost only; never expose it beyond localhost
  without a tunnel the user controls (see README).

Design rule: NO logic is duplicated here. Every tool wraps an existing
content_core function (loaders / linter / fact_check / seo / graph / runner /
craft / quality). The writing model produces prose; these tools do
everything deterministic: retrieval, context assembly, linting, fact
checking, SEO checking, craft (prose-quality) checking, gate scoring and
draft storage. THE gate rule lives in content_core/gates.py and is shared
with the LangGraph graph: hard layer (brand >= 85, 0 banned, 0 unflagged
fact issues) + craft blocking for blog/product (score < 70 fails) + the
optional Quality Profile layer (weighted axes with per-axis floors).

Test: .venv/bin/python test_mcp_client.py   (stdio, end-to-end)
      .venv/bin/python test_mcp_http.py     (http smoke test)
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotations only — the runtime import is lazy (P8)
    from content_core.graph import Core

try:
    import fcntl
    _FLOCK_AVAILABLE = True
except ImportError:  # non-POSIX platform: token-lockfile fallback below
    _FLOCK_AVAILABLE = False

from mcp.server.fastmcp import FastMCP

from content_core.craft import craft_check as _craft_check_text
from content_core.craft import craft_check_sections as _craft_check_draft
from content_core.craft import get_exemplars as _get_exemplars
from content_core.craft import glossary_group as _glossary_group
from content_core.craft import retrieve_cards as _retrieve_cards
from content_core.fact_check import check_draft as _fact_check_draft
from content_core.fact_check import check_section as _fact_check_section
from content_core.fact_check import find_refused_claims
# Gate policy lives in ONE place: content_core/gates.py (BRAND_PASS_SCORE,
# MAX_REWRITES, and the craft constants re-exported from craft.py). Both
# this server's final_gate and the LangGraph graph call gates.evaluate_gate.
from content_core.gates import BRAND_PASS_SCORE, CRAFT_PASS_SCORE, MAX_REWRITES
from content_core.gates import content_sha as _content_sha
from content_core.gates import evaluate_gate as _evaluate_gate
# NOTE: content_core.graph (and its langgraph chain, ~1 s of import) is
# deliberately NOT imported at module level — see _core_class (perf P8).
from content_core.linter import lint_draft as _lint_draft
from content_core.linter import lint_section as _lint_section
from content_core.loaders import DEFAULT_STYLE_PROFILE_ID, atomic_write_text, stored_file_stem
from content_core.loaders import banned_terms as _brand_banned_terms
from content_core.loaders import brand_check_context as _brand_check_context
from content_core.loaders import list_style_profiles as _list_style_profiles
from content_core.loaders import list_facts_profiles as _list_facts_profiles
from content_core.loaders import load_brand, load_facts, load_persona
from content_core.loaders import load_persona_by_id as _load_persona_by_id
from content_core.loaders import save_facts as _save_facts_file
from content_core.loaders import validate_facts as _validate_facts
from content_core.loaders import load_style_profile as _load_style_profile
from content_core.loaders import save_quality_profile as _save_quality_profile_file
from content_core.loaders import list_quality_profiles as _list_quality_profiles
from content_core.loaders import load_quality_profile as _load_quality_profile
from content_core.loaders import save_style_profile as _save_style_profile_file
from content_core.quality import PRESETS as _QUALITY_PRESETS
from content_core.quality import evaluate_quality as _evaluate_quality
from content_core.quality import get_profile as _quality_get_profile
from content_core.quality import make_profile as _make_quality_profile
from content_core.quality import preset_profile as _quality_preset
from content_core.brand_extract import build_brand_draft as _build_brand_draft
from content_core.brand_extract import clean_corpus_text as _clean_corpus_text
from content_core.brand_extract import compare_brand_voice as _compare_brand_voice
from content_core.brand_extract import extract_brand_kit as _extract_brand_kit
from content_core.brand_extract import extract_vocabulary as _extract_vocabulary
from content_core.brand_extract import find_site_claims as _find_site_claims
from content_core.brand_extract import site_corpus as _site_corpus
from content_core.brand_extract import suggest_voice_rules as _suggest_voice_rules
from content_core.loaders import list_brand_drafts as _list_brand_drafts
from content_core.loaders import load_brand_draft as _load_brand_draft
from content_core.loaders import save_brand_draft as _save_brand_draft_file
from content_core.images import best_library_match as _best_library_match
from content_core.images import build_manifest as _build_image_manifest
from content_core.images import check_images as _check_images
from content_core.images import image_sources_of as _image_sources_of
from content_core.images import invalid_edit_ops as _invalid_edit_ops
from content_core.images import match_allowlist as _match_allowlist
from content_core.images import normalise_ref as _normalise_ref
from content_core.images import plan_images as _plan_images
from content_core.images import suggest_alt as _suggest_alt
from content_core.images import suggest_filename as _suggest_filename
from content_core.images import vetting_rejection as _vetting_rejection
from content_core.images import EDIT_OPS as _EDIT_OPS
from content_core.images import (MAX_CANDIDATES_PER_SLOT
                                 as _MAX_CANDIDATES_PER_SLOT)
from content_core.images import VETTING_ISSUES as _VETTING_ISSUES
from content_core.loaders import (IMAGE_LICENSE_TYPES
                                  as _IMAGE_LICENSE_TYPES)
from content_core.loaders import add_image_library_entry as _add_library_image_file
from content_core.loaders import list_image_library as _list_image_library_file
from content_core.loaders import set_image_library_tags as _set_library_tags_file
from content_core.seo import seo_pass as _seo_pass
from content_core import research as _research
from content_core import seo_profile as _seo
from content_core import setup_assist as _setup_assist
from content_core import taste as _taste
from content_core import variants as _variants
from content_core import backup as _backup
from content_core import wordpress as _wordpress
from content_core import wp_convert as _wp_convert
from content_core.loaders import load_wordpress_target as _load_wp_target
from content_core.loaders import save_wordpress_target as _save_wp_target
from content_core.loaders import load_corpus_choice as _load_corpus_choice
from content_core.loaders import delete_corpus_choice as _delete_corpus_choice
from content_core.loaders import load_corpus_entry as _load_corpus_entry
from content_core.loaders import list_corpus_entries as _list_corpus_entries
from content_core.loaders import load_research_pack as _load_research_pack
from content_core.loaders import load_seo_profile as _load_seo_profile
from content_core.loaders import save_seo_profile as _save_seo_profile
from content_core.loaders import seo_profiles_dir as _seo_profiles_dir
from content_core.loaders import save_corpus_choice as _save_corpus_choice
from content_core.loaders import save_corpus_entry as _save_corpus_entry
from content_core.loaders import save_research_pack as _save_research_pack
from content_core.style import analyze_samples as _analyze_samples
from content_core.style import fingerprint as _fingerprint
from content_core.style import build_profile as _build_profile
from content_core.style import refresh_profile as _refresh_style_profile
from content_core.style import style_match as _style_match

PROJECT_DIR = Path(__file__).resolve().parent

mcp = FastMCP("content-core")

_core: Core | None = None


def _core_class() -> type[Core]:
    """The graph Core class, imported LAZILY (perf P8): importing
    content_core.graph pulls in the langgraph chain (~1 s), and most
    server launches never touch the graph — tools/list, the pure
    checker tools and every stdio test spawn start without it. The
    first call that needs a Core (assemble_context / make_outline /
    validate_brief / run_golden) pays the import instead."""
    from content_core.graph import Core
    return Core


def _get_core() -> Core:
    """One Core instance (MockWriter inside is never used by the MCP tools —
    the graph nodes we reuse are validate/research/outline, which don't write)."""
    global _core
    if _core is None:
        _core = _core_class()()
    return _core


def _brand() -> dict:
    return load_brand()


def _facts() -> dict:
    return load_facts()


def _resolve_style_profile(profile_id: str | None) -> dict | None:
    """Resolve a style profile id ('default'/'' → the shipped default
    profile) to the stored profile dict, or None when it does not exist.
    All profile file IO goes through loaders (the one place it lives).
    Loaded profiles pass through style.refresh_profile: a profile
    stored by an older algorithm version serves CURRENT warnings,
    never frozen ones (evaluation R4)."""
    if not profile_id:
        return None
    pid = DEFAULT_STYLE_PROFILE_ID if profile_id == "default" else profile_id
    profile = _load_style_profile(pid)
    return _refresh_style_profile(profile) if profile is not None else None


def _style_context_text(profile: dict) -> str:
    """Render a Style Profile as plain-language targets for the writer:
    fingerprint numbers translated into instructions + the qualitative
    do/don't + never_do the model distilled from the samples."""
    fp = profile.get("fingerprint", {})
    sl = fp.get("sentence_len", {})
    q = profile.get("qualitative", {})
    lines = [
        f"Style profile: {profile.get('name', '')} (id={profile.get('id')}) "
        f"— distilled from {profile.get('created_from', {}).get('sample_count', 0)} samples. "
        f"Write so your fingerprint lands near these targets:",
        f"- Sentence length: median ~{sl.get('median')} words, with HIGH burstiness "
        f"(stdev ~{sl.get('stdev')}): mix short 3-6 word sentences with longer ones — "
        f"uniform sentence lengths are the main 'AI voice' tell.",
        f"- Short sentences (<=6 words): ~{round(fp.get('short_sentence_share', 0) * 100)}% of sentences.",
        f"- First person ('mình/tôi'): ~{fp.get('first_person_rate')} per 100 words; "
        f"address the reader as 'bạn' ~{fp.get('reader_rate')} per 100 words.",
        f"- Concrete numbers: ~{fp.get('number_density')} digit-bearing tokens per 100 words — "
        f"specifics over adjectives.",
    ]
    for t in q.get("tone_traits", []):
        lines.append(f"- Tone '{t.get('trait', '')}': DO {t.get('do', '')} | DON'T {t.get('dont', '')}")
    for label, key in (("Opening patterns", "opening_patterns"),
                       ("Closing patterns", "closing_patterns"),
                       ("Signature devices", "signature_devices")):
        for item in q.get(key, []):
            lines.append(f"- {label}: {item}")
    if q.get("stance"):
        lines.append(f"- Stance: {q['stance']}")
    for item in q.get("never_do", []):
        lines.append(f"- NEVER: {item}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Draft state storage (local JSON files, one per draft)
# --------------------------------------------------------------------------

def drafts_dir() -> Path:
    d = Path(os.environ.get("CONTENT_CORE_DRAFTS_DIR", PROJECT_DIR / "drafts"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _draft_path(draft_id: str) -> Path:
    # Same injective stem mapping as loaders.stored_file_stem: clean ids
    # keep their legacy filename; ids the sanitiser had to change get a
    # stable hash suffix instead of colliding with another draft's file.
    try:
        stem = stored_file_stem(draft_id)
    except ValueError:
        raise ValueError("invalid draft_id") from None
    return drafts_dir() / f"{stem}.json"


def _load_draft(draft_id: str) -> dict:
    """Load + validate a stored draft (evaluation R7 F11/F12).

    Shape is validated, not just JSON syntax: the stored value must be
    a JSON object carrying at least one draft key, `sections` must be
    a list and `brief` a dict — anything else is the named corrupt-file
    error, not a downstream AttributeError ('str' object has no
    attribute 'get'). Missing bookkeeping keys are filled with defaults
    (a draft written by an older version, or missing rewrite_count,
    used to load fine and then KeyError AFTER a save had committed)."""
    path = _draft_path(draft_id)
    if not path.exists():
        raise ValueError(f"draft not found: {draft_id}")
    # Error messages name the FILE only, never the absolute path
    # (review M7: the corrupt-JSON message used to leak the server's
    # directory layout into tool results).
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"corrupt JSON in draft file {path.name}: {exc}") from None
    if not isinstance(data, dict) or not ({"draft_id", "brief", "sections"} & set(data)):
        raise ValueError(
            f"corrupt draft file {path.name}: expected a JSON object with "
            f"draft keys (draft_id / brief / sections)")
    if not isinstance(data.get("sections", []), list):
        raise ValueError(f"corrupt draft file {path.name}: 'sections' must be a list")
    if not isinstance(data.get("brief", {}), dict):
        raise ValueError(f"corrupt draft file {path.name}: 'brief' must be an object")
    for key, default in (("brief", {}), ("outline", []), ("sections", []),
                         ("gate_history", []), ("awaiting_rewrite", [])):
        data.setdefault(key, type(default)())
    data.setdefault("rewrite_count", 0)
    data.setdefault("status", "drafting")
    data.setdefault("draft_id", draft_id)
    # Entry shapes (review M8): a section/outline ENTRY of the wrong
    # type (a string where an object belongs) used to load fine and
    # then crash consumers with 'str' object has no attribute 'get' —
    # the exact failure class this loader exists to prevent. Entries
    # must be objects; missing fields get the consumers' defaults.
    for i, entry in enumerate(data["sections"]):
        if not isinstance(entry, dict):
            raise ValueError(
                f"corrupt draft file {path.name}: section {i} must be an object")
        entry.setdefault("id", f"s{i + 1}")
        entry.setdefault("heading", "")
        entry.setdefault("intent", "")
        entry.setdefault("target_words", 0)
        entry.setdefault("text", "")
    for i, entry in enumerate(data["outline"]):
        if not isinstance(entry, dict):
            raise ValueError(
                f"corrupt draft file {path.name}: outline item {i} must be an object")
        entry.setdefault("id", f"s{i + 1}")
        entry.setdefault("heading", "")
        entry.setdefault("intent", "")
        entry.setdefault("target_words", 0)
    return data


def _load_draft_or_error(draft_id: str) -> tuple[dict | None, dict | None]:
    """(draft, None) or (None, {"error": ...}) — the unified tool-level
    error dialect (evaluation R7 F1): a missing/corrupt/invalid draft
    is a structured tool result everywhere, never a protocol exception
    whose twin in another tool returns {"error": ...}."""
    try:
        return _load_draft(draft_id), None
    except ValueError as exc:
        return None, {"error": str(exc), "draft_id": draft_id}


def _store_draft(draft: dict) -> dict:
    # Atomic write (temp file + os.replace, via loaders): a crash
    # mid-write can no longer corrupt the stored draft's JSON.
    atomic_write_text(_draft_path(draft["draft_id"]),
                      json.dumps(draft, ensure_ascii=False, indent=2))
    return draft


# Per-draft serialisation for stored-draft read-modify-write
# (core-completion round, evaluation R7): the MCP server is launched
# per AI-app session, so two processes can write ONE draft — and
# load→modify→store was not atomic across processes (measured: 2
# processes x 15 save_section -> 29/30 sections survived; concurrent
# final_gates lost gate_history entries).
#
# LOCK REDESIGN (code review H3/M5, 2026-11-22). The first scheme —
# an O_EXCL lockfile stolen when its mtime passed 30 s and unlinked
# unconditionally on release — was unsound in three ways: a LIVE
# holder inside a >30 s critical section (a slow gate run) had its
# lock stolen, and its unconditional release-unlink then deleted the
# THIEF's lock, so exclusion could collapse entirely mid-flight; the
# check-then-unlink steal raced; and nested same-process acquisition
# deadlocked against the 10 s timeout. The sound scheme:
# - PRIMARY (POSIX): flock(LOCK_EX) on a persistent per-draft lock
#   file held via an open fd for the whole critical section. The
#   kernel releases it when the holder exits — however it exits — so
#   there is no staleness to judge and nothing to steal. The lock
#   file is NEVER unlinked: unlinking a locked file would let a
#   newcomer create and lock a NEW inode while the old holder still
#   holds the old one.
# - FALLBACK (no fcntl): a token lockfile. The file carries the
#   owner's pid+uuid token; a heartbeat thread refreshes its mtime
#   every _DRAFT_LOCK_HEARTBEAT_S; a waiter may steal ONLY after the
#   mtime is older than _DRAFT_LOCK_STALE_S AND a grace re-read
#   proves token+mtime unchanged (a live holder's heartbeat would
#   have moved the mtime); release unlinks ONLY when the token is
#   still ours — a holder never unlinks another holder's lock.
# - REENTRANCY: a per-process registry makes same-thread nested
#   acquisition a depth increment instead of a self-deadlock.
_DRAFT_LOCK_TIMEOUT_S = 10.0   # bounded retry before a structured error
_DRAFT_LOCK_STALE_S = 30.0     # fallback only: heartbeat older than this
_DRAFT_LOCK_POLL_S = 0.05
_DRAFT_LOCK_HEARTBEAT_S = 5.0  # fallback only: holder mtime refresh

_HELD_LOCKS: dict[str, list] = {}   # lock path -> [owner thread ident, depth]
_HELD_LOCKS_GUARD = threading.Lock()


def _lock_timeout_error(draft_id: str) -> ValueError:
    return ValueError(
        f"draft {draft_id} is locked by another writer — "
        f"retry shortly (lock timeout {_DRAFT_LOCK_TIMEOUT_S:.0f}s)")


def _acquire_flock(lock_path: Path, draft_id: str):
    """flock acquire with a bounded wait; returns a release callable."""
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    deadline = time.monotonic() + _DRAFT_LOCK_TIMEOUT_S
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.monotonic() >= deadline:
                os.close(fd)
                raise _lock_timeout_error(draft_id) from None
            time.sleep(_DRAFT_LOCK_POLL_S)
    try:  # owner identity, for humans debugging a stuck lock
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, f"pid={os.getpid()} uuid={uuid.uuid4().hex}\n".encode())
    except OSError:
        pass

    def release() -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)  # the file itself stays: never unlink a lock file

    return release


def _acquire_token_lock(lock_path: Path, draft_id: str):
    """Token-lockfile fallback (see the design note above); returns a
    release callable."""
    token = f"{os.getpid()}:{uuid.uuid4().hex}".encode()
    deadline = time.monotonic() + _DRAFT_LOCK_TIMEOUT_S
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            stolen = False
            try:
                st1 = lock_path.stat()
                content1 = lock_path.read_bytes()
            except OSError:
                st1, content1 = None, None  # vanished mid-check: retry
            if st1 is not None and \
                    time.time() - st1.st_mtime > _DRAFT_LOCK_STALE_S:
                # Grace re-read: steal only if token AND mtime are
                # unchanged — a live holder heartbeats every ~5 s, so
                # an unchanged stale mtime across the grace proves the
                # holder is gone (a check-then-unlink without this
                # re-read was the old scheme's race).
                time.sleep(0.25)
                try:
                    st2 = lock_path.stat()
                    content2 = lock_path.read_bytes()
                except OSError:
                    st2, content2 = None, None
                if st2 is not None and st2.st_mtime == st1.st_mtime \
                        and content2 == content1:
                    try:
                        lock_path.unlink()
                        stolen = True
                    except OSError:
                        pass
            if stolen:
                continue
            if time.monotonic() >= deadline:
                raise _lock_timeout_error(draft_id)
            time.sleep(_DRAFT_LOCK_POLL_S)
            continue
        try:
            os.write(fd, token)
        finally:
            os.close(fd)
        break

    stop = threading.Event()

    def _heartbeat() -> None:
        while not stop.wait(_DRAFT_LOCK_HEARTBEAT_S):
            try:
                if lock_path.read_bytes() == token:
                    os.utime(lock_path, None)
            except OSError:
                pass

    threading.Thread(target=_heartbeat, daemon=True).start()

    def release() -> None:
        stop.set()
        try:  # unlink ONLY if the lock is still ours
            if lock_path.read_bytes() == token:
                lock_path.unlink()
        except OSError:
            pass

    return release


@contextlib.contextmanager
def _draft_lock(draft_id: str, directory: Path | None = None):
    """Hold the per-draft lock (see the design note above). Timeout
    raises ValueError, which the tools surface as {"error": ...}.

    `directory` overrides WHERE the lock file lives (default: the
    drafts dir). The SEO profile tools lock in the seo_profiles
    dir: they serialise read-modify-write on a DIFFERENT file than
    the draft itself, so sharing the draft's lock file would both
    over-serialise and, worse, suggest the two files move together
    when they do not."""
    try:
        stem = stored_file_stem(draft_id)
    except ValueError:
        raise ValueError("invalid draft_id") from None
    lock_path = (directory or drafts_dir()) / f"{stem}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    key = str(lock_path)
    ident = threading.get_ident()
    with _HELD_LOCKS_GUARD:
        held = _HELD_LOCKS.get(key)
        if held is not None and held[0] == ident:
            held[1] += 1
            nested = True
        else:
            nested = False
    if nested:
        try:
            yield
        finally:
            with _HELD_LOCKS_GUARD:
                entry = _HELD_LOCKS.get(key)
                if entry is not None:
                    entry[1] -= 1
                    if entry[1] <= 0:
                        del _HELD_LOCKS[key]
        return
    release = (_acquire_flock(lock_path, draft_id) if _FLOCK_AVAILABLE
               else _acquire_token_lock(lock_path, draft_id))
    with _HELD_LOCKS_GUARD:
        _HELD_LOCKS[key] = [ident, 1]
    try:
        yield
    finally:
        with _HELD_LOCKS_GUARD:
            _HELD_LOCKS.pop(key, None)
        release()


def _sections_of(draft: dict) -> list[dict]:
    return draft.get("sections", [])


def _draft_title(draft: dict) -> str:
    """The title the WordPress exporter would post for this draft —
    ONE resolution shared by the exporter and the gate's content
    fingerprint (fix round F1), so the fingerprint covers exactly
    the title that ships."""
    return draft.get("brief", {}).get("title") or \
        draft.get("title") or "Untitled"


def _brand_max_sentence_words(brand: dict | None = None) -> int | None:
    """The server brand's own max sentence length — passed to craft so
    its rhythm statistic excludes over-long sentences exactly like the
    gate paths do (monotonicity fix, evaluation R5). Accepts an
    already-loaded brand dict so a caller needing several brand-derived
    values loads the brand file ONCE (perf P4)."""
    try:
        b = brand if brand is not None else _brand()
        return int(b["voice"]["sentence_rules"]["max_sentence_words"])
    except (KeyError, TypeError, ValueError):
        return None


def _resolve_brand_facts(brand_id: str) -> tuple[dict, dict]:
    """(brand, facts) for a brand id — the per-brand resolution every
    checking tool shares (core-completion round). ''/'moc-brew' → the
    server brand + its facts. Any other id → the stored Brand Builder
    draft (the brand rules) + the stored facts profile (the numbers).
    Raises ValueError for an unknown brand: facts and rules are NEVER
    borrowed from another brand."""
    if not brand_id or brand_id == "moc-brew":
        return _brand(), _facts()
    draft = _load_brand_draft(brand_id)
    if draft is None:
        raise ValueError(
            f"unknown brand_id: {brand_id!r} — no stored brand draft; "
            f"build the brand first (analyze_site_brand/save_brand_draft) "
            f"and save its facts (save_facts)")
    return draft, load_facts(brand_id)


def _resolve_brand_only(brand_id: str) -> dict:
    """The brand DICT for a brand id, without touching the fact base —
    the image layer needs brand context (name, visual style), not
    numbers. ''/'moc-brew' → the server brand; any other id → the
    stored Brand Builder draft. Raises ValueError for an unknown
    brand (same never-borrow rule as _resolve_brand_facts)."""
    if not brand_id or brand_id == "moc-brew":
        return _brand()
    draft = _load_brand_draft(brand_id)
    if draft is None:
        raise ValueError(
            f"unknown brand_id: {brand_id!r} — no stored brand draft")
    return draft


def _resolve_writer_context(brief: dict) -> tuple[dict, dict, dict]:
    """(brand, facts, persona) for the WRITER-FACING tools
    (validate_brief / assemble_context) — resolved from the brief
    exactly like the gate path resolves its brand (trial bug 1: these
    tools used to serve the server brand/persona unconditionally, so a
    brief naming brand X was validated and contextualised as Mộc Brew /
    Linh while the gate judged it as X). brief.brand_id → that brand's
    stored draft + facts profile; brief.persona_id → that persona's
    card. Absent keys → the server defaults. An unknown brand/persona
    raises ValueError — never a silent fall back to another brand's
    rules or another persona's voice."""
    brand, facts = _resolve_brand_facts(brief.get("brand_id", ""))
    persona_id = brief.get("persona_id")
    persona = (_load_persona_by_id(persona_id) if persona_id
               else load_persona())
    return brand, facts, persona


def _core_for_brief(brief: dict) -> Core:
    """A Core bound to the brief's own brand/facts/persona when the
    brief names any of them; the shared server Core otherwise, so the
    default path is byte-identical to before."""
    if brief.get("brand_id") or brief.get("persona_id"):
        brand, facts, persona = _resolve_writer_context(brief)
        return _core_class()(brand=brand, facts=facts, persona=persona)
    return _get_core()


def _draft_images_state(draft: dict) -> dict:
    """The draft's images state {"plan": [...], "images": {...},
    "candidates": {...}}, defaulted for drafts started before the
    image layer existed. `candidates` is the searched-image pool
    (spec §3.2): {slot_id: [candidate, ...]}, living in the draft
    like the plan and the fulfilments — no separate store."""
    state = draft.get("images")
    if not isinstance(state, dict):
        state = {}
    if not isinstance(state.get("plan"), list):
        state["plan"] = []
    if not isinstance(state.get("images"), dict):
        state["images"] = {}
    if not isinstance(state.get("candidates"), dict):
        state["candidates"] = {}
    return state


def _run_gate(draft: dict, quality_profile: dict | None = None,
              judged: dict | None = None,
              style_profile: dict | None = None,
              brand: dict | None = None,
              facts: dict | None = None) -> dict:
    """Run THE shared gate (gates.evaluate_gate — the same function the
    LangGraph graph's final_gate node calls) over a stored draft.
    Brand/facts default to the server brand; final_gate passes the
    draft brief's own brand resolution when the brief names one."""
    fmt = draft.get("brief", {}).get("format", "blog")
    return _evaluate_gate(_sections_of(draft), fmt,
                          brand if brand is not None else _brand(),
                          facts if facts is not None else _facts(),
                          craft_mode="blocking",
                          quality_profile=quality_profile, judged=judged,
                          style_profile=style_profile,
                          brief=draft.get("brief", {}))


# --------------------------------------------------------------------------
# Tools — data layers
# --------------------------------------------------------------------------

def _brand_persona_ids(brand_id: str) -> list[str]:
    """The distinct persona_ids the brand's stored drafts name in
    their briefs — the persona evidence the draft/gate path resolves
    from (_resolve_writer_context reads brief.persona_id). Drafts
    naming no brand belong to the server brand (moc-brew), exactly
    as list_drafts maps them. Corrupt draft files contribute
    nothing: no brief can be read from them."""
    found: list[str] = []
    d = drafts_dir()
    if not d.exists():
        return found
    for path in sorted(d.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        brief = data.get("brief") if isinstance(data.get("brief"), dict) else {}
        if (brief.get("brand_id") or "moc-brew") != brand_id:
            continue
        pid = str(brief.get("persona_id") or "").strip()
        if pid and pid not in found:
            found.append(pid)
    return found


@mcp.tool()
def get_persona(brand_id: str = "") -> dict:
    """Return the author Persona (Character Card V2 — who writes).

    With no brand_id: the server default persona (Linh), exactly as
    before. With a brand_id: the persona THAT BRAND's drafts
    resolve — the persona resolution the writer/gate path applies
    (_resolve_writer_context): the persona_id the brand's stored
    draft briefs name, loaded via load_persona_by_id, with
    persona_source="brand" marking the provenance. A brand whose
    drafts name no persona gets the server default WITH
    persona_source="server_default" — the fallback is explicit,
    never silent. An unknown brand, drafts naming conflicting
    personas, or a named persona that no longer resolves are
    structured errors: a persona is never borrowed or guessed."""
    if not brand_id:
        return load_persona()
    try:
        _resolve_brand_only(brand_id)  # unknown brand → ValueError
    except ValueError as exc:
        return {"error": str(exc), "brand_id": brand_id}
    persona_ids = _brand_persona_ids(brand_id)
    if len(persona_ids) > 1:
        return {"error": f"brand {brand_id!r} drafts name conflicting "
                         f"personas {sorted(persona_ids)} — refusing to "
                         f"guess which one the brand writes as",
                "brand_id": brand_id}
    if persona_ids:
        try:
            persona = _load_persona_by_id(persona_ids[0])
        except ValueError as exc:  # named persona no longer resolves
            return {"error": str(exc), "brand_id": brand_id}
        return {**persona, "persona_source": "brand"}
    return {**load_persona(), "persona_source": "server_default"}


@mcp.tool()
def get_brand() -> dict:
    """Return the Mộc Brew Brand Voice profile (parsed YAML): checkable voice
    rules, banned terms, prohibited claims, few-shot examples, anti-examples."""
    return load_brand()


@mcp.tool()
def get_facts(brand_id: str = "moc-brew") -> dict:
    """Return the fact base for a brand (default: the server brand,
    moc-brew). Facts are PER-BRAND: each brand's prices/weights/
    origins live in its own facts profile. With no brand_id argument
    behaviour is unchanged; pass '' to list the stored profiles. An
    unknown brand returns a structured error — never another brand's
    facts."""
    if not brand_id:
        return {"profiles": _list_facts_profiles(),
                "default": load_facts("moc-brew")}
    try:
        return load_facts(brand_id)
    except ValueError as exc:
        return {"error": str(exc), "available": _list_facts_profiles()}


@mcp.tool()
def save_facts(brand_id: str, facts: dict) -> dict:
    """Validate + persist the fact base for a brand (per-brand facts
    store). `facts` is the facts document: {facts: {price_vnd_per_250g
    or price_display, pack_weight_g, origins, known_prices_vnd?,
    pack_weights_g?, delivery_days?, roast_weekday?}, entries?}.
    The brand_id argument is stamped into the document. Validation is
    the save_brand_draft discipline: schema-checked BEFORE writing,
    atomic write; a failed validation returns a structured error and
    writes nothing."""
    doc = dict(facts) if isinstance(facts, dict) else {}
    doc["brand_id"] = brand_id
    try:
        _validate_facts(doc)
        return _save_facts_file(doc)
    except ValueError as exc:
        # The save itself sat OUTSIDE the try (review M6): a store-level
        # ValueError (e.g. an unusable brand_id) escaped as a protocol
        # exception while its sibling tools returned {"error": ...}.
        return {"error": str(exc)}


@mcp.tool()
def research_facts(query: str) -> dict:
    """Keyword-triggered lorebook retrieval for a brief/query text.

    Facts are the ONLY source of numbers/claims. Entries stay dormant until a
    key matches — exactly the graph's research node (Core.research reused).
    Returns the triggered entries plus the canonical facts table."""
    core = _get_core()
    state = core.research({"brief": {"topic": query, "text": query}})  # type: ignore[arg-type]
    return {"query": query, "sources": state["sources"], "facts": _facts()["facts"]}


@mcp.tool()
def validate_brief(brief: dict) -> dict:
    """Validate a brief {topic, format, keyword?, title?, requested_claims?,
    brand_id?, persona_id?}.

    Includes prohibited-claim detection: a brief ASKING for e.g.
    '100% chữa mất ngủ' is refused — `valid` is False, `status` is
    "refused", and the claims are listed in refused_claims. Do not write
    the brief as asked; the claims must not appear in any draft. A brief
    that merely DISCLAIMS a claim ("cam kết không chữa mất ngủ") is not
    a request and validates clean.

    Brand/persona resolution (trial bug 1): the brief is validated
    against ITS OWN brand — brief.brand_id selects that brand's rules
    (including ITS prohibited claims) and brief.persona_id that
    persona; absent keys validate against the server brand (Mộc Brew /
    Linh), as before. An unknown brand/persona returns a structured
    error, never a silent validation against another brand."""
    try:
        core = _core_for_brief(brief)
    except ValueError as exc:
        return {"error": str(exc)}
    result = core.validate_brief({"brief": brief})  # type: ignore[arg-type]
    refused = result.get("refused_claims",
                         find_refused_claims(str(brief), core.brand))
    if refused:
        instruction = (f"Brief REFUSED: it requests prohibited claims {refused}. "
                       f"Do not write it as asked — ask the user to re-scope "
                       f"the brief without those claims.")
    else:
        instruction = "Brief is clean to write."
    return {
        "valid": result.get("status") == "validated",
        "status": result.get("status"),
        "refused_claims": refused,
        "brand_id": result.get("brand_id"),
        "persona_id": result.get("persona_id"),
        "instruction": instruction,
    }


@mcp.tool()
def get_craft_cards(format: str = "blog", intent: str = "", query: str = "") -> dict:
    """Layer 4: retrieve triggered technique cards (lorebook-style).

    Cards stay dormant until format/intent/keyword triggers fire; at most 3
    cards, at most 1 always-on core card. Cards are DISTILLED principles
    paraphrased into checkable instructions — never copied book text."""
    cards = _retrieve_cards(format, intent, query)
    return {"format": format, "intent": intent, "cards": cards, "count": len(cards)}


@mcp.tool()
def get_exemplars(format: str) -> dict:
    """Layer 4: the exemplar (few-shot style anchor) passage(s) for a format:
    blog_intro, product, social, email, comparison. All passages are ORIGINAL
    Mộc Brew texts written as style anchors, with why_it_works notes."""
    exemplars = _get_exemplars(format)
    return {"format": format, "exemplars": exemplars, "count": len(exemplars)}


@mcp.tool()
def get_domain_glossary(group: str = "") -> dict:
    """Layer 4: coffee domain glossary. No group → all groups + weak words.
    Groups: aroma, acidity, body, aftertaste, brewing, roast. Weak words are
    vague terms to avoid, each with a precise suggested replacement."""
    return _glossary_group(group or None)


@mcp.tool()
def craft_check(text: str = "", format: str = "blog", draft_id: str = "",
                brand_id: str = "") -> dict:
    """Layer 4: deterministic prose-QUALITY check (separate from brand_check).

    Scores 0-100 on writing quality only: weak/vague words (with suggested
    replacements), filler phrases, monotone sentence rhythm, repeated
    sentence openings, sensory detail (product/blog). Pass a raw `text` or a
    `draft_id` (worst section score wins). This tool only reports; the
    BLOCKING decision lives in final_gate (craft < 70 fails blog/product
    drafts there; advisory for social/email/comparison).

    `brand_id` (optional) selects the judging glossary: the shipped
    glossary belongs to Mộc Brew — pass another brand's id to judge with
    the generic brand-neutral weak-word/sensory set instead of Mộc
    Brew's coffee vocabulary. Empty = the default Mộc Brew path."""
    server_brand = not brand_id or brand_id == "moc-brew"
    # Load the server brand ONCE when its context is needed (perf P4:
    # the YAML used to be parsed twice per call, once per derived value).
    server = _brand() if server_brand else None
    if draft_id:
        draft, error = _load_draft_or_error(draft_id)
        if error:
            return error
        fmt = draft.get("brief", {}).get("format", format)
        return _craft_check_draft(
            _sections_of(draft), fmt, brand_id=brand_id or None,
            max_sentence_words=_brand_max_sentence_words(server) if server_brand else None,
            banned_terms=_brand_banned_terms(server) if server_brand else None)
    return _craft_check_text(text, format, brand_id=brand_id or None,
                             max_sentence_words=_brand_max_sentence_words(server)
                             if server_brand else None,
                             banned_terms=_brand_banned_terms(server)
                             if server_brand else None)


@mcp.tool()
def analyze_style(samples: list[str]) -> dict:
    """Style Extraction: measure the deterministic style fingerprint of
    pasted sample texts (fetched beforehand — this server stays offline).

    Returns per-sample fingerprints + the aggregate fingerprint + a
    consistency report with warnings (too few samples, too few words, or
    samples diverging — the mixed-authors signal). Code measures; it never
    guesses qualitative traits."""
    return _analyze_samples(samples)


@mcp.tool()
def save_style_profile(name: str, samples: list[str], qualitative: dict | None = None,
                       store_excerpts: bool | str = False,
                       brand_id: str = "moc-brew") -> dict:
    """Build + persist a Style Profile from samples and the qualitative
    schema YOU (the model) filled in after reading them: {tone_traits:
    [{trait, do, dont}], opening_patterns, closing_patterns,
    signature_devices, stance, never_do}.

    COPYRIGHT RULE: store_excerpts defaults to False — exemplar passages
    are stored only for the user's OWN or licensed samples. For other
    people's writing, keep the fingerprint + distilled traits only.
    `store_excerpts` must be a real boolean: a string like "yes" is a
    structured error, never silently coerced to True (evaluation R7 —
    excerpt storage is a copyright decision, not a truthy string).
    Returns the stored profile, including its generated id."""
    if not isinstance(store_excerpts, bool):
        return {"error": f"store_excerpts must be a boolean (true/false), "
                         f"got {store_excerpts!r}"}
    try:
        profile = _build_profile(name, samples, qualitative=qualitative,
                                 store_excerpts=store_excerpts, brand_id=brand_id)
        return _save_style_profile_file(profile)
    except ValueError as exc:  # e.g. zero usable samples / unusable name
        return {"error": str(exc)}


@mcp.tool()
def get_style_profile(profile_id: str = "") -> dict:
    """Return a stored Style Profile by id ('default' → the shipped
    mocbrew-blog-voice profile). With no id: the list of available
    profiles plus the default profile (or null when none is stored)."""
    try:
        if profile_id:
            pid = DEFAULT_STYLE_PROFILE_ID if profile_id == "default" else profile_id
            profile = _load_style_profile(pid)
            if profile is None:
                return {"error": f"style profile not found: {profile_id}",
                        "available": _list_style_profiles()}
            return _refresh_style_profile(profile)
        default_profile = _load_style_profile(DEFAULT_STYLE_PROFILE_ID)
    except ValueError as exc:
        # A corrupt stored profile is a structured error (review M6),
        # not a protocol exception.
        return {"error": str(exc), "available": _list_style_profiles()}
    return {"profiles": _list_style_profiles(),
            "default_id": DEFAULT_STYLE_PROFILE_ID,
            "default": _refresh_style_profile(default_profile)
            if default_profile is not None else None}


@mcp.tool()
def style_match(text: str = "", draft_id: str = "", profile_id: str = "default") -> dict:
    """Compare a text (or a stored draft's joined sections) against a Style
    Profile's fingerprint: sentence median, burstiness (sentence-length
    stdev — the key human signal), short-sentence share, first-person and
    reader rates, number density. Returns a 0-100 closeness score,
    per-metric in/out-of-range, and concrete fix advice. ADVISORY: it
    measures closeness to the sample voice, never blocks a gate in v1."""
    try:
        profile = _resolve_style_profile(profile_id)
    except ValueError as exc:  # corrupt stored profile (review M6)
        return {"error": str(exc), "available": _list_style_profiles()}
    if profile is None:
        return {"error": f"style profile not found: {profile_id}",
                "available": _list_style_profiles()}
    if draft_id:
        draft, error = _load_draft_or_error(draft_id)
        if error:
            return error
        text = "\n\n".join(s.get("text", "") for s in _sections_of(draft))
    result = _style_match(text, profile)
    return {**result, "profile_id": profile["id"]}


@mcp.tool()
def analyze_site_brand(pages: list[dict]) -> dict:
    """Brand Builder: the deterministic analysis of a fetched site (pages
    from fetch_site.py: [{url, title, text, headings, html_meta}]).

    Returns corpus stats, the style fingerprint of the site's copy, the
    visual brand kit (each value with its source_url evidence), voice-rule
    SUGGESTIONS derived from the measurements, vocabulary candidates, and
    the site's own claims flagged needs_evidence. This is the analysis a
    model/user reviews BEFORE a draft is saved — nothing here is an
    approved brand."""
    corpus = _site_corpus(pages)
    # The fingerprint measures the site's PROSE (trial bug 2): the raw
    # corpus's repeated chrome blocks glue into pseudo-sentences and
    # corrupt every sentence statistic; voice_suggestions below apply
    # the same filtering internally.
    fp = _fingerprint(_clean_corpus_text(corpus["all_text"]))
    claims = _find_site_claims(pages)
    warnings: list[str] = []
    if corpus["total_words"] < 800:
        warnings.append(f"corpus is small ({corpus['total_words']} words from "
                        f"{corpus['page_count']} page(s)) — fetch more pages "
                        f"(blog/product) for a trustworthy profile")
    if claims:
        warnings.append(f"site makes {len(claims)} claim(s) needing evidence — "
                        f"they are listed for review, never approved silently")
    # The kit is computed ONCE (review L12): _suggest_voice_rules
    # used to re-extract it internally just to read the brand name —
    # it now receives the name from this kit.
    kit = _extract_brand_kit(pages)
    return {"corpus": {"page_count": corpus["page_count"],
                       "total_words": corpus["total_words"],
                       "per_page": corpus["per_page"]},
            "fingerprint": fp,
            "brand_kit": kit,
            "voice_suggestions": _suggest_voice_rules(
                corpus["all_text"],
                brand_name=(kit.get("brand_name") or {}).get("value")),
            "vocabulary": _extract_vocabulary(pages),
            "site_claims": claims,
            "warnings": warnings}


@mcp.tool()
def save_brand_draft(pages: list[dict], qualitative: dict | None = None,
                     source_type: str = "own_site",
                     brand_name: str | None = None) -> dict:
    """Brand Builder: build + persist a DRAFT Brand Voice profile from a
    fetched site's pages, in the brand_mocbrew.yaml structure.

    `qualitative` is the schema YOU (the model) fill after reading the
    pages: {tone_traits: [{dimension, position, do, dont}], audience_guess,
    positioning, differentiators}. Deterministic parts (kit, voice-rule
    suggestions, vocabulary, site claims) are computed in code.

    The result is a DRAFT (review_status="draft") with review_notes listing
    what was inferred and what the user MUST decide — banned_terms always
    starts empty (a site cannot reveal its own bans). With
    source_type="competitor", every example is marked reference_only and
    the draft carries a competitor_warning: competitor copy is analysis
    material, never an approved brand."""
    try:
        draft = _build_brand_draft(pages, qualitative=qualitative,
                                   source_type=source_type, brand_name=brand_name)
        return _save_brand_draft_file(draft)
    except ValueError as exc:  # bad source_type / unusable draft (review M6)
        return {"error": str(exc)}


@mcp.tool()
def get_brand_draft(draft_id: str = "") -> dict:
    """Return a stored brand draft by id (the brand slug, e.g. "som-coffee").
    With no id: the list of available brand drafts."""
    if draft_id:
        try:
            draft = _load_brand_draft(draft_id)
        except ValueError as exc:  # corrupt stored draft (review M6)
            return {"error": str(exc), "available": _list_brand_drafts()}
        if draft is None:
            return {"error": f"brand draft not found: {draft_id}",
                    "available": _list_brand_drafts()}
        return draft
    return {"drafts": _list_brand_drafts()}


def _merge_brand_draft_patch(existing: dict, patch: dict) -> dict:
    """Apply a partial patch to a stored brand draft: shallow-merge
    per top-level section — when both the stored value and the patch
    value for a key are objects, the patch's keys win WITHIN that
    section (one level deep); lists and scalars are replaced whole,
    and only when the key is present in the patch. Keys the patch
    does not name are carried over untouched."""
    merged = dict(existing)
    for key, value in patch.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = {**current, **value}
        else:
            merged[key] = value
    return merged


@mcp.tool()
def update_brand_draft(brand_id: str, patch: dict) -> dict:
    """Edit the EXISTING stored brand draft in place with a partial
    patch — the structured-editor counterpart to save_brand_draft
    (which builds a NEW draft from fetched pages and would clobber
    the human edits prepare_brand_setup works to preserve, S2).

    Merge rule: shallow per top-level section — an object section in
    the patch merges one level deep into the stored section (the
    patch's keys win); list and scalar fields are replaced whole,
    only when present in the patch (a field explicitly set to null
    is set to null). EVERYTHING the patch does not name is preserved
    exactly — inferred_fields stamps and brand.review_status
    included — and those change only when the patch names them
    explicitly. brand.id is the draft's identity: a patch naming a
    different brand.id is refused. The merged draft goes through the
    same validation as save_brand_draft (validate_brand_draft:
    visual / image_sources boundary rules, competitor reference_only
    re-stamping) BEFORE anything is written; an invalid patch writes
    nothing. A brand with no stored draft is a structured error —
    build it first (save_brand_draft / prepare_brand_setup).
    Returns {draft, updated_fields, brand_id}."""
    if not isinstance(patch, dict):
        return {"error": f"patch must be an object of section updates, "
                         f"got {type(patch).__name__}",
                "brand_id": brand_id}
    try:
        existing = _load_brand_draft(brand_id)
    except ValueError as exc:  # corrupt stored draft (review M6)
        return {"error": str(exc), "brand_id": brand_id}
    if existing is None:
        return {"error": f"brand draft not found: {brand_id} — build it "
                         f"first (save_brand_draft / prepare_brand_setup)",
                "brand_id": brand_id,
                "available": _list_brand_drafts()}
    patch_brand = patch.get("brand")
    if isinstance(patch_brand, dict) and patch_brand.get("id") not in (
            None, brand_id):
        return {"error": f"patch brand.id {patch_brand.get('id')!r} does "
                         f"not match brand_id {brand_id!r} — a patch "
                         f"cannot rename or move a brand draft",
                "brand_id": brand_id}
    merged = _merge_brand_draft_patch(existing, patch)
    try:
        saved = _save_brand_draft_file(merged)
    except ValueError as exc:  # validation refused the merged draft
        return {"error": str(exc), "brand_id": brand_id}
    return {"draft": saved, "updated_fields": sorted(patch),
            "brand_id": brand_id}


@mcp.tool()
def compare_brand_voice(pages_a: list[dict], pages_b: list[dict]) -> dict:
    """Brand Builder compare mode: contrast two fetched sites' voices
    (typically A = my client's site, B = a competitor).

    Side-by-side fingerprints (sentence median/stdev, short-sentence share,
    first-person/reader rates, number density, punctuation), vocabulary
    symmetric difference, superlative-claim counts, both brand kits, and
    `gaps`: positioning observations computed in code from the measured
    deltas (e.g. who speaks first-person more, who makes more unevidenced
    claims, whose sentences run shorter)."""
    return _compare_brand_voice(pages_a, pages_b)


# ---------------------------------------------------------------------------
# assemble_context budget (evaluation R8): the response used to carry the
# full structured sources/cards/exemplars AND the same content rendered
# into context_text — a ~3x duplication (~13.8k chars of JSON for a
# 4.6k-char context), with no bound at all on fat inputs. Now the
# rendered text is the single representation; the structured payloads
# are replaced by lightweight metadata (counts + per-part sizes). A
# documented SOFT BUDGET caps each part; an over-cap part is truncated
# with an explicit marker and named in `truncated_parts` — never cut
# silently. Standard briefs sit far below every cap and are untouched.
# ---------------------------------------------------------------------------

_CONTEXT_PART_CAPS = {"persona": 8000, "brand_rules": 6000,
                      "craft_cards": 4000, "style_profile": 4000,
                      "facts_triggered": 3000, "depth_material": 6000,
                      "exemplar": 3000,
                      "task": 1000, "post_history_reminder": 4000}
_MAX_BRAND_EXAMPLES = 8   # few-shot examples rendered into brand_rules
_MAX_FACT_ENTRIES = 12    # lorebook entries rendered into facts_triggered
_MAX_EXEMPLARS = 2        # passages rendered into the exemplar part


def _cap_part(text: str, cap: int, part: str, truncated: list[str]) -> str:
    """Truncate `text` to `cap` chars with an explicit marker, recording
    the part name in `truncated`. Under-cap text is returned unchanged."""
    if len(text) <= cap:
        return text
    truncated.append(part)
    marker = f"\n[truncated: {part} exceeded its {cap}-char context budget]"
    return text[:max(0, cap - len(marker))] + marker


@mcp.tool()
def assemble_context(brief: dict, format: str | None = None,
                     style_profile_id: str | None = None) -> dict:
    """Assemble the ordered prompt context the writing model must follow.

    Order: 1 persona → 2 brand rules → 3 craft cards (Layer 4, triggered for
    this format/topic) → 4 triggered facts → 5 exemplar (matching format) →
    6 task → 7 post-history reminder (strongest weight, read last).
    `format` overrides brief['format'] when given.

    DEPTH MATERIAL (quality uplift WS1): when the brief's brand has a
    research pack with APPLIED items, a `depth_material` part is
    inserted after the facts (before the exemplar): the applied facts
    with their provenance plus bounded excerpts from the accepted
    sources they cite, labelled as SOURCE MATERIAL for depth —
    specifics, process detail, local colour. It is NOT part of the
    fact base: a claim taken from it must still match the facts or
    carry [NEEDS SOURCE], and fact_check judges it exactly as
    before. Brands without applied items get no part and a context
    identical to before; the result then carries a `depth_material`
    metadata block ({applied_items, fact_items, source_excerpts}).

    The response carries the rendered `context_text` (the single
    representation of the content) + lightweight metadata: `counts`
    (sources / craft_cards / exemplars), `ordered_context` (part order,
    name and char size — no second copy of the content), and
    `truncated_parts` naming any part cut by the context budget
    (see _CONTEXT_PART_CAPS; truncation is always marked in the text).

    With `style_profile_id` (a stored Style Profile id, or "default"), a
    `style_profile` part is inserted AFTER craft_cards and BEFORE the facts
    (8 parts total): fingerprint targets in plain language + the distilled
    qualitative do/don't + never_do. Without it the context is exactly the
    7 parts above. A requested-but-missing profile is reported in
    `style_profile_error` and the context is built without the part.
    When NO style_profile_id is given but the brief names a brand that
    has a recorded corpus choice (corpus registry), that choice's style
    profile is attached as a DEFAULT and the result's
    `style_profile_source` says "brand_corpus_choice" (an explicit
    style_profile_id always wins and reports "brief").

    Brand/persona/facts resolution (trial bug 1): EVERYTHING is resolved
    from the brief — brief.brand_id selects that brand's rules and its
    fact base (the facts part is triggered from THAT brand's lorebook),
    brief.persona_id that persona's card; absent keys use the server
    defaults (Mộc Brew / Linh), as before. The result names the
    resolution (`brand_id`, `persona_id`). An unknown brand/persona
    returns a structured error — the context is never silently built
    for another brand."""
    fmt = format or brief.get("format", "blog")
    brief = {**brief, "format": fmt}
    try:
        brand, facts, persona_card = _resolve_writer_context(brief)
    except ValueError as exc:
        return {"error": str(exc)}
    persona = persona_card["data"]
    # Lorebook retrieval against the RESOLVED brand's fact base: the
    # graph's research node bound to a Core for this brief's brand,
    # fed the same state shape the research_facts tool uses, so key
    # triggering is identical — only the fact base differs.
    _brief_core = _core_class()(brand=brand, facts=facts, persona=persona_card)
    sources = _brief_core.research(
        {"brief": {"topic": str(brief), "text": str(brief)}})["sources"]  # type: ignore[arg-type]

    persona_text = "\n".join([
        persona.get("system_prompt", ""),
        f"Description: {persona.get('description', '')}",
        f"Personality: {persona.get('personality', '')}",
        f"Scenario: {persona.get('scenario', '')}",
        f"Example voice:\n{persona.get('mes_example', '')}",
    ])
    v = brand["voice"]
    traits = "\n".join(f"- {t['dimension']}: {t['position']} | DO: {t['do']} | DON'T: {t['dont']}"
                       for t in v.get("traits", []))
    all_examples = brand.get("examples", [])
    examples = "\n".join(f"- [{e.get('format')}] {e.get('text')}"
                         for e in all_examples[:_MAX_BRAND_EXAMPLES])
    if len(all_examples) > _MAX_BRAND_EXAMPLES:
        examples += (f"\n- [truncated: {len(all_examples) - _MAX_BRAND_EXAMPLES} "
                     f"more brand examples over the context budget]")
    brand_text = "\n".join([
        f"Brand: {brand['brand']['name']} (id={brand['brand']['id']})",
        f"POV: {v.get('pov')}; address the reader as: {v.get('reader_address')}",
        f"Sentence rules: {json.dumps(v.get('sentence_rules', {}), ensure_ascii=False)}",
        f"Formatting: {'; '.join(v.get('formatting_rules', []))}",
        f"BANNED terms (never use): {', '.join(v.get('banned_terms', []))}",
        f"PROHIBITED claims (refuse): {', '.join(v.get('prohibited_claims', []))}",
        f"Traits:\n{traits}",
        f"Few-shot examples:\n{examples}",
    ])
    facts_text = "\n".join(f"- {s['content']}" for s in sources[:_MAX_FACT_ENTRIES]) \
        or "- (no facts triggered by this brief)"
    if len(sources) > _MAX_FACT_ENTRIES:
        facts_text += (f"\n- [truncated: {len(sources) - _MAX_FACT_ENTRIES} "
                       f"more fact entries over the context budget]")

    # Layer 4a: technique cards triggered by this brief's format/intent/topic.
    cards = _retrieve_cards(fmt, str(brief.get("intent", "")),
                            f"{brief.get('topic', '')} {brief.get('title', '')}")
    craft_text = "\n".join(
        f"- [{c['id']}] {c['name']}: {c['instruction']} Good: {c['good_example']}"
        for c in cards) or "- (no craft cards triggered)"

    # Layer 4b: exemplar for this format — resolved through craft's
    # ONE format mapping (blog -> blog_intro), shared with the
    # get_exemplars tool (this call site used to carry a private copy).
    exemplars = _get_exemplars(fmt)
    exemplar_text = "\n".join(
        f"- [{e['format']}] {e['text']} (why it works: {'; '.join(e.get('why_it_works', []))})"
        for e in exemplars[:_MAX_EXEMPLARS]) or "- (no exemplar for this format)"
    if len(exemplars) > _MAX_EXEMPLARS:
        exemplar_text += (f"\n- [truncated: {len(exemplars) - _MAX_EXEMPLARS} "
                          f"more exemplars over the context budget]")

    task_text = (f"Write format={fmt} about: {brief.get('topic', '')}. "
                 f"Keyword: {brief.get('keyword', '')}. Title: {brief.get('title', '')}. "
                 "Write section by section. Facts only from the facts section above; "
                 "any claim without a source must be marked [NEEDS SOURCE]. "
                 "For the headline, opening and CTA/closing sections, draft "
                 "2-5 variants and call score_section_variants to pick the "
                 "strongest before saving the section.")
    reminder = persona.get("post_history_instructions", "")

    # Style Extraction: optional style_profile part, after craft_cards.
    style_profile = None
    style_error = None
    style_profile_source = None
    if style_profile_id:
        try:
            style_profile = _resolve_style_profile(style_profile_id)
        except ValueError as exc:  # corrupt stored profile (review M6)
            style_error = str(exc)
        if style_profile is None and style_error is None:
            style_error = f"style profile not found: {style_profile_id}"
        if style_profile is not None:
            style_profile_source = "brief"
    elif brief.get("brand_id"):
        # Brand corpus-choice DEFAULT (corpus registry, Smith's
        # decision): when the brief names a brand but no explicit
        # style profile, the brand's chosen system corpus supplies
        # the default style profile. It is ONLY a default — the
        # explicit style_profile_id branch above always wins, and a
        # brand with no recorded choice gets no style part, exactly
        # as before.
        try:
            choice = _load_corpus_choice(brief["brand_id"])
        except ValueError:  # corrupt choice record: no default
            choice = None
        if choice is not None and choice.get("profile_id"):
            try:
                style_profile = _resolve_style_profile(choice["profile_id"])
            except ValueError as exc:  # corrupt stored profile
                style_error = str(exc)
            if style_profile is not None:
                style_profile_source = "brand_corpus_choice"

    # Depth material (WS1, quality uplift): the brand's APPLIED
    # research-pack items, rendered by research.depth_material as
    # labelled SOURCE MATERIAL after the facts — never merged into
    # the fact base. Absent entirely when the brand's pack has no
    # applied items, so other contexts are byte-identical to before.
    depth = None
    depth_error = None
    try:
        pack = _load_research_pack(brand["brand"]["id"])
        if pack is not None:
            depth = _research.depth_material(pack)
    except ValueError as exc:  # corrupt pack record: report, don't crash
        depth_error = str(exc)

    parts = [("persona", persona_text), ("brand_rules", brand_text),
             ("craft_cards", craft_text)]
    if style_profile is not None:
        parts.append(("style_profile", _style_context_text(style_profile)))
    parts.append(("facts_triggered", facts_text))
    if depth is not None:
        parts.append(("depth_material", depth["text"]))
    parts += [("exemplar", exemplar_text),
              ("task", task_text), ("post_history_reminder", reminder)]
    truncated_parts: list[str] = []
    parts = [(name, _cap_part(content, _CONTEXT_PART_CAPS[name], name,
                              truncated_parts))
             for name, content in parts]
    ordered = [{"order": i + 1, "part": name, "chars": len(content)}
               for i, (name, content) in enumerate(parts)]
    result = {
        "format": fmt,
        "brand_id": brand["brand"]["id"],
        "persona_id": persona.get("name", ""),
        "counts": {"sources": len(sources), "craft_cards": len(cards),
                   "exemplars": len(exemplars)},
        "style_profile": ({"id": style_profile["id"], "name": style_profile.get("name", "")}
                          if style_profile else None),
        "style_profile_source": style_profile_source,
        "ordered_context": ordered,
        "context_text": "\n\n".join(
            f"[{i + 1}. {name.upper()}]\n{content}"
            for i, (name, content) in enumerate(parts)),
        "truncated_parts": truncated_parts,
    }
    if style_error:
        result["style_profile_error"] = style_error
    if depth is not None:
        result["depth_material"] = {
            "applied_items": depth["applied_items"],
            "fact_items": depth["fact_items"],
            "source_excerpts": depth["source_excerpts"]}
    if depth_error:
        result["depth_material_error"] = depth_error
    return result


@mcp.tool()
def make_outline(brief: dict, proposed_outline: list[dict] | None = None,
                 draft_id: str | None = None) -> dict:
    """Make an outline for a brief.

    No proposed_outline → the deterministic template outline (Core.outline).
    With proposed_outline (list of {heading, intent?, target_words?}) → it is
    validated (every item needs a heading), ids s1..sN are assigned, and it is
    used as-is. If draft_id is given, the outline is stored into that draft."""
    core = _get_core()
    if proposed_outline is not None:
        outline = []
        for i, item in enumerate(proposed_outline):
            if not isinstance(item, dict):
                return {"ok": False,
                        "error": f"outline item {i} must be an object with a heading",
                        "outline": []}
            if not str(item.get("heading", "")).strip():
                return {"ok": False, "error": f"outline item {i} has no heading", "outline": []}
            # bool is an int subclass (review L4): True must not
            # silently become a 1-word target.
            if isinstance(item.get("target_words", 120), bool):
                return {"ok": False,
                        "error": f"outline item {i} has a non-numeric target_words: "
                                 f"{item.get('target_words')!r}",
                        "outline": []}
            try:
                target_words = int(item.get("target_words", 120))
            except (TypeError, ValueError):
                return {"ok": False,
                        "error": f"outline item {i} has a non-numeric target_words: "
                                 f"{item.get('target_words')!r}",
                        "outline": []}
            outline.append({"id": f"s{i+1}", "heading": item["heading"],
                            "intent": item.get("intent", ""),
                            "target_words": target_words, "text": ""})
        source = "model_proposed_validated"
    else:
        outline = core.outline({"brief": brief})["outline"]  # type: ignore[arg-type]
        source = "deterministic_template"
    stored = False
    if draft_id:
        try:
            with _draft_lock(draft_id):
                draft, error = _load_draft_or_error(draft_id)
                if error:
                    return {**error, "ok": False, "outline": []}
                draft["outline"] = outline
                _store_draft(draft)
        except ValueError as exc:  # lock timeout / invalid draft id
            return {"ok": False, "error": str(exc), "outline": []}
        stored = True
    return {"ok": True, "source": source, "outline": outline, "stored_in_draft": stored}


# --------------------------------------------------------------------------
# Tools — draft state
# --------------------------------------------------------------------------

@mcp.tool()
def start_draft(brief: dict) -> dict:
    """Start a new draft for a brief. Returns draft_id; the draft state is
    persisted to drafts/<draft_id>.json. Validate the brief first."""
    draft_id = f"d-{uuid.uuid4().hex[:10]}"
    draft = {"draft_id": draft_id, "brief": brief, "outline": [], "sections": [],
             "rewrite_count": 0, "gate_history": [], "awaiting_rewrite": [],
             "status": "drafting"}
    _store_draft(draft)
    return {"draft_id": draft_id, "status": "drafting", "brief": brief}


@mcp.tool()
def save_section(draft_id: str, section_id: str, heading: str, text: str,
                 intent: str | None = None,
                 target_words: int | None = None) -> dict:
    """Save (or overwrite) ONE section of a draft. Write section by section —
    never one long generation. Overwriting a section that the final gate
    flagged counts toward the max-2 rewrite budget once all flagged sections
    have been rewritten.

    `intent` / `target_words` are OPTIONAL (evaluation R8): when omitted
    on an overwrite, the existing section's values are preserved — a
    rewrite used to silently wipe them to ""/0. Saving into an
    "approved" draft resets its status to "drafting": the approval no
    longer describes the stored text.

    The load→modify→store runs under the per-draft lock (see
    _draft_lock): a concurrent writer in another server process can
    no longer silently lose this save; a lock timeout is a structured
    error."""
    try:
        with _draft_lock(draft_id):
            return _save_section_impl(draft_id, section_id, heading, text,
                                      intent, target_words)
    except ValueError as exc:
        return {"error": str(exc), "draft_id": draft_id}


def _save_section_impl(draft_id: str, section_id: str, heading: str, text: str,
                       intent: str | None = None,
                       target_words: int | None = None) -> dict:
    # bool is an int subclass (review L4): target_words=True used to
    # be stored as the int 1.
    if isinstance(target_words, bool):
        return {"error": f"target_words must be an int, got {target_words!r}",
                "draft_id": draft_id}
    draft, error = _load_draft_or_error(draft_id)
    if error:
        return error
    sections = _sections_of(draft)
    existing = next((s for s in sections if s["id"] == section_id), None)
    if existing:
        record = {"id": section_id, "heading": heading,
                  "intent": existing.get("intent", "") if intent is None else intent,
                  "target_words": (existing.get("target_words", 0)
                                   if target_words is None else target_words),
                  "text": text}
        sections[sections.index(existing)] = record
    else:
        sections.append({"id": section_id, "heading": heading,
                         "intent": intent or "",
                         "target_words": target_words or 0, "text": text})
    draft["sections"] = sections
    if draft.get("status") == "approved":
        draft["status"] = "drafting"

    awaiting = list(draft.get("awaiting_rewrite", []))
    if section_id in awaiting:
        awaiting.remove(section_id)
        if not awaiting:  # one full rewrite cycle completed
            draft["rewrite_count"] = draft.get("rewrite_count", 0) + 1
        draft["awaiting_rewrite"] = awaiting
    _store_draft(draft)
    return {"draft_id": draft_id, "saved": section_id,
            "sections_saved": [s["id"] for s in sections],
            "rewrite_count": draft.get("rewrite_count", 0)}


@mcp.tool()
def get_draft(draft_id: str) -> dict:
    """Return the full stored draft state (brief, outline, sections, gate
    history). A missing or corrupt draft is a structured {"error": ...}
    result, like every other tool-level failure."""
    draft, error = _load_draft_or_error(draft_id)
    if error:
        return error
    return draft


@mcp.tool()
def list_drafts() -> dict:
    """List every stored draft: draft_id, brand (the brief's brand_id,
    or the server default moc-brew), status, and last-updated time.
    Corrupt files are listed as corrupt, never silently skipped (the
    house list discipline)."""
    out = []
    for path in sorted(drafts_dir().glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not a JSON object")
        except (json.JSONDecodeError, OSError, ValueError):
            out.append({"draft_id": path.stem, "corrupt": True})
            continue
        brief = data.get("brief") if isinstance(data.get("brief"), dict) else {}
        out.append({
            "draft_id": data.get("draft_id", path.stem),
            "brand_id": brief.get("brand_id") or "moc-brew",
            "status": data.get("status", "drafting"),
            "title": brief.get("title", ""),
            "updated": datetime.fromtimestamp(
                path.stat().st_mtime, timezone.utc).isoformat(
                    timespec="seconds"),
            "corrupt": False})
    return {"drafts": out, "count": len(out)}


@mcp.tool()
def delete_draft(draft_id: str, confirm: bool = False) -> dict:
    """Delete ONE stored draft, under the per-draft lock. Deleting an
    APPROVED draft requires confirm=True — approval is the owner's
    sign-off, so removing it must be a deliberate act, never a
    default. The per-draft lock file itself is left in place (the
    lock design never unlinks lock files)."""
    try:
        with _draft_lock(draft_id):
            draft, error = _load_draft_or_error(draft_id)
            if error:
                return error
            if draft.get("status") == "approved" and not confirm:
                return {"error": f"draft {draft_id} is approved — pass "
                                 f"confirm=True to delete an approved draft",
                        "draft_id": draft_id}
            _draft_path(draft_id).unlink()
    except ValueError as exc:  # lock timeout / invalid draft id
        return {"error": str(exc), "draft_id": draft_id}
    return {"deleted": draft_id, "status_was": draft.get("status", "drafting")}


# --------------------------------------------------------------------------
# Tools — article images (ADVISORY layer, like style_match: these
# tools NEVER enter final_gate's verdict in this round. The core
# plans, ranks the brand's photo library, checks SEO metadata and
# style lock, records the writer's vision grades, and packages the
# WordPress-export manifest. Generating the actual image files is
# the host/writer's job — exactly like writing the prose.)
# --------------------------------------------------------------------------

@mcp.tool()
def plan_images(draft_id: str,
                product_specific: list[str] | None = None) -> dict:
    """Plan a draft's image slots: one featured image + one in-content
    image per section (capped — see content_core/images.py). Each slot
    carries a deterministic generation prompt = scene + the brand's
    visual style block (byte-identical across slots; that shared block
    is the style lock). Planning is explicit: starting a draft does
    not auto-plan. Re-planning keeps fulfilments AND candidate pools
    whose slot ids still exist. The plan lives in the draft's
    "images" state, under the per-draft lock.

    `product_specific` marks slots (by section id, slot id, or
    "featured") that must show the brand's OWN product — the writer
    sets it from the section's intent at planning time. A marked slot
    is fenced off from searched stock entirely: library first,
    generation last, never a candidate."""
    try:
        with _draft_lock(draft_id):
            draft, error = _load_draft_or_error(draft_id)
            if error:
                return error
            try:
                brand = _resolve_brand_only(
                    draft.get("brief", {}).get("brand_id", ""))
            except ValueError as exc:
                return {"error": str(exc), "draft_id": draft_id}
            plan = _plan_images(_sections_of(draft), draft.get("brief", {}),
                                brand, product_specific=product_specific)
            state = _draft_images_state(draft)
            slot_ids = {p["id"] for p in plan}
            kept = {iid: e for iid, e in state["images"].items()
                    if iid in slot_ids}
            kept_pools = {iid: pool
                          for iid, pool in state["candidates"].items()
                          if iid in slot_ids}
            draft["images"] = {"plan": plan, "images": kept,
                               "candidates": kept_pools}
            _store_draft(draft)
    except ValueError as exc:  # lock timeout / invalid draft id
        return {"error": str(exc), "draft_id": draft_id}
    return {"draft_id": draft_id, "plan": plan, "images": kept}


@mcp.tool()
def set_image(draft_id: str, image_id: str, source: str, ref: str,
              alt: str = "", caption: str = "", description: str = "",
              filename: str = "", prompt_used: str = "",
              candidate_id: str = "", edit_ops: list[str] | None = None,
              attribution_text: str = "",
              mark_generated: bool = False) -> dict:
    """Fulfil ONE planned slot with an image the writer generated or
    selected. `source` is "ai" (generated for this article), "client"
    (the client's real photo) or "searched" (a real photo found via
    the brand's image_sources allowlist — see add_image_candidate);
    `ref` is its path or URL — the core stores refs and metadata
    only, never image bytes. For a searched image, `ref` is the
    LOCAL file downloaded from the candidate's source_url.

    `filename` defaults to the SEO suggestion (keyword-led slug);
    `alt` defaults to the template suggestion and is then marked
    `alt_suggested` — replace it with alt written from the actual
    image whenever you can. For an "ai" image, pass `prompt_used`:
    the full prompt actually used, which must carry the brand's style
    block (image_check verifies it). Overwriting a slot drops any
    vision_check recorded for the previous image. Returns the stored
    entry plus this image's immediate check notes.

    Searched fulfilment: pass `candidate_id` (a VETTED, viable
    candidate from this slot's pool) and `edit_ops` — the AI edits
    actually applied, from the fixed vocabulary (crop, resize,
    color_grade, exposure, white_balance, sharpen,
    background_cleanup). Any other op is refused: an edit must never
    change the product's colour/shape/label/quantity, replace the
    background, or remove a watermark. The stored entry gains an
    `origin` block built from the CANDIDATE's record (source_url and
    license_type cannot be restated by the caller). `attribution_text`
    overrides the candidate's recorded attribution. With
    `mark_generated=True` (ai source only, the fulfil_images_v2
    last-resort path) the entry gains origin kind "generated" so the
    manifest marks it as generated."""
    if source not in ("ai", "client", "searched"):
        return {"error": f"source must be 'ai', 'client' or 'searched', "
                         f"got {source!r}", "draft_id": draft_id}
    if not str(ref or "").strip():
        return {"error": "ref must be a non-empty path or URL",
                "draft_id": draft_id}
    try:
        with _draft_lock(draft_id):
            draft, error = _load_draft_or_error(draft_id)
            if error:
                return error
            state = _draft_images_state(draft)
            item = next((p for p in state["plan"] if p["id"] == image_id),
                        None)
            if item is None:
                return {"error": f"unknown image slot: {image_id!r} — "
                                 f"call plan_images first",
                        "draft_id": draft_id}
            origin = None
            candidate = None
            if source == "searched":
                if item.get("product_specific"):
                    return {"error": f"slot {image_id!r} is "
                                     f"product_specific — searched stock "
                                     f"can never fulfil it; use the brand "
                                     f"library or generation",
                            "draft_id": draft_id}
                if not candidate_id:
                    return {"error": "source 'searched' requires "
                                     "candidate_id — register and vet a "
                                     "candidate first (add_image_candidate "
                                     "/ record_candidate_vetting)",
                            "draft_id": draft_id}
                pool = state["candidates"].get(image_id) or []
                candidate = next(
                    (c for c in pool if c.get("id") == candidate_id), None)
                if candidate is None:
                    return {"error": f"unknown candidate {candidate_id!r} "
                                     f"in slot {image_id!r}'s pool",
                            "draft_id": draft_id}
                if candidate.get("status") == "rejected":
                    return {"error": f"candidate {candidate_id!r} was "
                                     f"rejected at vetting "
                                     f"({candidate.get('rejection_reason', '')})"
                                     f" — it can never fulfil a slot",
                            "draft_id": draft_id}
                if candidate.get("status") not in ("viable", "fulfilled"):
                    return {"error": f"candidate {candidate_id!r} has not "
                                     f"passed vetting yet (status "
                                     f"{candidate.get('status')!r}) — record "
                                     f"its vetting first",
                            "draft_id": draft_id}
                bad_ops = _invalid_edit_ops(edit_ops)
                if bad_ops:
                    return {"error": f"edit_ops {bad_ops} are outside the "
                                     f"allowed vocabulary "
                                     f"{sorted(_EDIT_OPS)} — an edit must "
                                     f"never change the product or remove "
                                     f"a watermark",
                            "draft_id": draft_id}
                ops = list(edit_ops or [])
                origin = {
                    "kind": "searched_edited" if ops
                            else "searched_unedited",
                    "source_url": candidate.get("source_url", ""),
                    "license_type": candidate.get("license_type", ""),
                    "candidate_id": candidate["id"],
                    "edit_ops": ops,
                    "attribution_text": attribution_text
                    or candidate.get("attribution_text", ""),
                }
            elif mark_generated:
                if source != "ai":
                    return {"error": "mark_generated is only valid with "
                                     "source 'ai'", "draft_id": draft_id}
                origin = {"kind": "generated", "source_url": "",
                          "license_type": "", "candidate_id": "",
                          "edit_ops": [], "attribution_text": ""}
            brief = draft.get("brief", {})
            alt_suggested = not str(alt or "").strip()
            entry = {
                "source": source, "ref": ref,
                "filename": filename or _suggest_filename(item, brief),
                "alt": _suggest_alt(item, brief) if alt_suggested else alt,
                "alt_suggested": alt_suggested,
                "caption": caption, "description": description,
                "prompt_used": prompt_used, "from_library": None,
            }
            if origin is not None:
                entry["origin"] = origin
            state["images"][image_id] = entry
            if candidate is not None:
                candidate["status"] = "fulfilled"
                candidate["fulfilled_ref"] = ref
            draft["images"] = state
            _store_draft(draft)
            try:
                brand = _resolve_brand_only(brief.get("brand_id", ""))
            except ValueError:
                brand = {}
            issues, flagged = _check_images(state["plan"], state["images"],
                                            brief, brand)
    except ValueError as exc:  # lock timeout / invalid draft id
        return {"error": str(exc), "draft_id": draft_id}
    notes = [n for n in issues + flagged if n["image_id"] == image_id]
    return {"draft_id": draft_id, "image_id": image_id, "image": entry,
            "notes": notes}


@mcp.tool()
def image_check(draft_id: str, complete: bool = False) -> dict:
    """Deterministic image checker: SEO metadata (alt presence /
    duplication / keyword stuffing / length, filename slug rules,
    featured keyword + no featured reuse), the AI style lock
    (prompt_used must carry the brand's visual style block), and the
    vision grades. With complete=True, unfulfilled slots count as
    missing_image issues and ungraded images are flagged
    vision_unchecked. Advisory: this never changes final_gate."""
    draft, error = _load_draft_or_error(draft_id)
    if error:
        return error
    state = _draft_images_state(draft)
    if not state["plan"]:
        return {"error": "draft has no image plan — call plan_images first",
                "draft_id": draft_id}
    try:
        brand = _resolve_brand_only(draft.get("brief", {}).get("brand_id", ""))
    except ValueError as exc:
        return {"error": str(exc), "draft_id": draft_id}
    issues, flagged = _check_images(state["plan"], state["images"],
                                    draft.get("brief", {}), brand,
                                    complete=complete)
    return {"ok": not issues, "draft_id": draft_id, "complete": complete,
            "issues": issues, "flagged": flagged}


@mcp.tool()
def record_vision_check(draft_id: str, image_id: str, content_grade: int,
                        style_grade: int, notes: str = "") -> dict:
    """Record the WRITER's vision grades for one fulfilled image —
    the judged layer for images (the core cannot see images; you
    can). Look at the actual image first, then grade: content_grade
    = does it show what the slot needs; style_grade = does it fit
    the brand look (for a client photo this judges fit, not the
    photo's fault). Rubric: 2 = matches, 1 = usable with
    reservations (say why in notes), 0 = wrong content / off-style —
    a 0 makes image_check fail that slot until the image is
    regenerated or replaced. Applies to library/client photos too:
    content relevance matters for them equally."""
    for name, grade in (("content_grade", content_grade),
                        ("style_grade", style_grade)):
        if isinstance(grade, bool) or not isinstance(grade, int) \
                or grade not in (0, 1, 2):
            return {"error": f"{name} must be an int in 0..2, "
                             f"got {grade!r}", "draft_id": draft_id}
    try:
        with _draft_lock(draft_id):
            draft, error = _load_draft_or_error(draft_id)
            if error:
                return error
            state = _draft_images_state(draft)
            if not state["plan"]:
                return {"error": "draft has no image plan — call "
                                 "plan_images first", "draft_id": draft_id}
            entry = state["images"].get(image_id)
            if not isinstance(entry, dict) \
                    or not str(entry.get("ref") or "").strip():
                return {"error": f"image slot not fulfilled: {image_id!r}",
                        "draft_id": draft_id}
            entry["vision_check"] = {
                "content_grade": content_grade, "style_grade": style_grade,
                "notes": notes,
                "checked_at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds")}
            state["images"][image_id] = entry
            draft["images"] = state
            _store_draft(draft)
    except ValueError as exc:  # lock timeout / invalid draft id
        return {"error": str(exc), "draft_id": draft_id}
    return {"draft_id": draft_id, "image_id": image_id,
            "vision_check": entry["vision_check"]}


@mcp.tool()
def get_image_manifest(draft_id: str) -> dict:
    """The WordPress-export-ready media manifest: fulfilled images in
    plan order as {role, section_id, ref, filename, alt_text,
    caption, description} — the fields the future WP exporter
    uploads. `ready` is the image layer's own completeness signal:
    every slot fulfilled AND no hard issues in a complete check
    (a vision grade of 0 is a hard issue). Unchecked images do not
    block ready; they are listed under `warnings`."""
    draft, error = _load_draft_or_error(draft_id)
    if error:
        return error
    state = _draft_images_state(draft)
    if not state["plan"]:
        return {"draft_id": draft_id, "manifest": [], "ready": False,
                "issues": [], "flagged": [], "warnings": [],
                "note": "no image plan — call plan_images first"}
    try:
        brand = _resolve_brand_only(draft.get("brief", {}).get("brand_id", ""))
    except ValueError as exc:
        return {"error": str(exc), "draft_id": draft_id}
    issues, flagged = _check_images(state["plan"], state["images"],
                                    draft.get("brief", {}), brand,
                                    complete=True)
    return {"draft_id": draft_id,
            "manifest": _build_image_manifest(state["plan"],
                                              state["images"]),
            "ready": not issues, "issues": issues, "flagged": flagged,
            "warnings": flagged}


@mcp.tool()
def add_library_image(brand_id: str, ref: str, description: str,
                      tags: list[str] | None = None,
                      source: str = "client",
                      ai_tags: dict | None = None,
                      dimensions: dict | None = None) -> dict:
    """Add one image to a brand's own image library (refs only — the
    core never copies image bytes). `description` says what the image
    shows: library ranking and alt suggestions read it, so write it
    like a caption. `source` is "client" (the client's real photo,
    the default) or "ai" (a generated image kept for reuse).
    Re-adding the same ref+description replaces, never duplicates.

    Import flows may pass `ai_tags` (the tag payload described under
    tag_library_image) and `dimensions` ({width, height} in px) in
    the SAME call — they are validated on the tag tool's path and
    stamped onto the entry. A re-add that passes neither preserves
    the tags already recorded."""
    try:
        return _add_library_image_file(brand_id, ref, description, tags,
                                       source, ai_tags, dimensions)
    except ValueError as exc:
        return {"error": str(exc), "brand_id": brand_id}


@mcp.tool()
def tag_library_image(brand_id: str, ref: str, ai_tags: dict,
                      dimensions: dict | None = None) -> dict:
    """Record YOUR AI tags for one library image — the retrieval
    layer for photos (the core cannot see images; you can, the same
    split as record_vision_check). Look at the actual image first,
    then describe it in the FIXED tag shape:

    {subjects: [short noun phrases, <= 4 words each, <= 8 of them],
     objects: [short noun phrases, <= 10], scene: one short line
     (<= 12 words), colours: [short strings, <= 6],
     text_in_image: bool}

    An empty tag set is refused — a record with no signal only
    pretends the image was tagged. The core validates, stamps
    (tagged_by="host_model", tagged_at, tag_count) and stores the
    record on the entry; the tag tokens join the token set library
    ranking reads, which is what makes a thinly described photo
    findable by fulfil_images_from_library. Re-tagging REPLACES the
    record and bumps tag_count (no full history is kept).
    `dimensions` ({width, height} in px) replaces the stored pair
    when given. The ref must match exactly ONE entry of THIS brand's
    library (refs are compared normalised, like fulfilment) — an
    unknown ref, a ref that lives under another brand, or a ref
    shared by several entries is an error, never a guess."""
    try:
        entries = _list_image_library_file(brand_id)
    except ValueError as exc:
        return {"error": str(exc), "brand_id": brand_id}
    nref = _normalise_ref(ref)
    matches = [e for e in entries
               if _normalise_ref(e.get("ref") or "") == nref]
    if not matches:
        return {"error": f"no library image with ref {ref!r} in "
                         f"brand {brand_id!r}", "brand_id": brand_id}
    if len(matches) > 1:
        return {"error": f"ref {ref!r} matches {len(matches)} library "
                         f"entries in brand {brand_id!r} — ambiguous, "
                         f"nothing was tagged", "brand_id": brand_id}
    entry_id = matches[0].get("id")
    if not entry_id:
        return {"error": f"library entry for ref {ref!r} in brand "
                         f"{brand_id!r} has no id — the library file "
                         f"is corrupt", "brand_id": brand_id}
    try:
        return _set_library_tags_file(brand_id, entry_id, ai_tags,
                                      dimensions)
    except ValueError as exc:
        return {"error": str(exc), "brand_id": brand_id}


@mcp.tool()
def list_library_images(brand_id: str) -> dict:
    """List a brand's image library entries ([] when the brand has
    no library yet). Entries carry `ai_tags` (subjects / scene /
    objects / colours / text_in_image + the core's stamps) and
    `dimensions` when the host model has recorded them."""
    try:
        entries = _list_image_library_file(brand_id)
    except ValueError as exc:
        return {"error": str(exc), "brand_id": brand_id}
    return {"brand_id": brand_id, "images": entries}


@mcp.tool()
def fulfil_images_from_library(draft_id: str) -> dict:
    """Library-first fulfilment: for each unfulfilled plan slot, rank
    the DRAFT BRAND's library by token overlap (entry tags +
    description + AI tags vs the slot's heading/intent + brief
    keyword) and fulfil with the
    best unused entry above the match threshold — one library image
    never fulfils two slots in a draft. Alt text is suggested from
    the entry description (marked alt_suggested — the writer should
    still review it). Slots with no fitting entry are reported as
    needs_generation with their style-locked generation prompt."""
    try:
        with _draft_lock(draft_id):
            draft, error = _load_draft_or_error(draft_id)
            if error:
                return error
            state = _draft_images_state(draft)
            if not state["plan"]:
                return {"error": "draft has no image plan — call "
                                 "plan_images first", "draft_id": draft_id}
            brief = draft.get("brief", {})
            brand_id = brief.get("brand_id") or "moc-brew"
            try:
                entries = _list_image_library_file(brand_id)
            except ValueError as exc:
                return {"error": str(exc), "draft_id": draft_id}
            images = state["images"]
            used = {e.get("from_library") for e in images.values()
                    if isinstance(e, dict) and e.get("from_library")}
            # Used REFS too (review round 2 M1): the same photo can
            # sit in the library as two entries (same ref, different
            # descriptions) — entry-id dedupe alone would let it fill
            # two slots; the ref is the uniqueness unit.
            used_refs = {_normalise_ref(e.get("ref") or "")
                         for e in images.values() if isinstance(e, dict)
                         and str(e.get("ref") or "").strip()}
            fulfilled, needs_generation = [], []
            for item in state["plan"]:
                existing = images.get(item["id"])
                if isinstance(existing, dict) \
                        and str(existing.get("ref") or "").strip():
                    continue
                match = _best_library_match(item, brief, entries, used,
                                            used_refs)
                if match is None:
                    needs_generation.append({
                        "image_id": item["id"], "prompt": item["prompt"],
                        "style_block": item.get("style_block", "")})
                    continue
                entry, score = match
                used.add(entry["id"])
                used_refs.add(_normalise_ref(entry.get("ref") or ""))
                images[item["id"]] = {
                    "source": entry.get("source", "client"),
                    "ref": entry.get("ref", ""),
                    "filename": _suggest_filename(item, brief),
                    "alt": _suggest_alt(
                        item, brief,
                        description=entry.get("description", "")),
                    "alt_suggested": True,
                    "caption": "",
                    "description": entry.get("description", ""),
                    "prompt_used": "", "from_library": entry["id"],
                }
                fulfilled.append({"image_id": item["id"],
                                  "library_id": entry["id"],
                                  "ref": entry.get("ref", ""),
                                  "score": round(score, 3)})
            state["images"] = images
            draft["images"] = state
            _store_draft(draft)
    except ValueError as exc:  # lock timeout / invalid draft id
        return {"error": str(exc), "draft_id": draft_id}
    return {"draft_id": draft_id, "fulfilled": fulfilled,
            "needs_generation": needs_generation, "images": images}


@mcp.tool()
def add_image_candidate(draft_id: str, image_id: str, source_url: str,
                        license_type: str, source_name: str = "",
                        license_note: str = "",
                        attribution_text: str = "") -> dict:
    """Register ONE searched-photo candidate for a planned slot —
    step 2 of the searched-image chain (spec:
    specs/image-search-edit-feature.md). YOU do the searching with
    your own tools; the core keeps the pool and referees the license
    discipline. The DRAFT BRAND must carry an `image_sources`
    allowlist: without one the search step is off and this is an
    error. The candidate's source must match the allowlist by
    `source_name` or by the source_url's host, and `license_type`
    must be in the fixed set (stock_commercial, stock_paid,
    brand_owned, user_supplied, public_domain, cc_attribution) and
    allowed for that source entry — anything else is refused and the
    pool does not change. At most 5 candidates per slot. A
    product_specific slot refuses candidates at every step. The
    candidate then needs a vetting record (record_candidate_vetting)
    before it can fulfil the slot via set_image(source="searched")."""
    try:
        with _draft_lock(draft_id):
            draft, error = _load_draft_or_error(draft_id)
            if error:
                return error
            state = _draft_images_state(draft)
            item = next((p for p in state["plan"] if p["id"] == image_id),
                        None)
            if item is None:
                return {"error": f"unknown image slot: {image_id!r} — "
                                 f"call plan_images first",
                        "draft_id": draft_id}
            if item.get("product_specific"):
                return {"error": f"slot {image_id!r} is product_specific "
                                 f"— it must show the brand's own product, "
                                 f"so searched candidates are forbidden for "
                                 f"it at every step",
                        "draft_id": draft_id}
            try:
                brand = _resolve_brand_only(
                    draft.get("brief", {}).get("brand_id", ""))
            except ValueError as exc:
                return {"error": str(exc), "draft_id": draft_id}
            sources = _image_sources_of(brand)
            if not sources:
                return {"error": "the draft's brand has no image_sources "
                                 "allowlist — the searched-image step is "
                                 "off for this brand (library, then "
                                 "generation, as before)",
                        "draft_id": draft_id}
            if license_type not in _IMAGE_LICENSE_TYPES:
                return {"error": f"license_type {license_type!r} is "
                                 f"outside the fixed set "
                                 f"{list(_IMAGE_LICENSE_TYPES)} — an image "
                                 f"whose license cannot be classified is "
                                 f"refused, never registered",
                        "draft_id": draft_id}
            if not str(source_url or "").strip().startswith(
                    ("http://", "https://")):
                return {"error": "source_url must be an http(s) URL — "
                                 "it is the candidate's provenance",
                        "draft_id": draft_id}
            matched = _match_allowlist(sources, source_url, source_name)
            if matched is None:
                return {"error": f"source of {source_url!r} is not on "
                                 f"the brand's image_sources allowlist "
                                 f"{[s.get('name') for s in sources]} — "
                                 f"only allowlisted sources may be "
                                 f"registered",
                        "draft_id": draft_id}
            allowed = matched.get("license_types")
            if allowed and license_type not in allowed:
                return {"error": f"source {matched.get('name')!r} allows "
                                 f"license_types {allowed}, not "
                                 f"{license_type!r}",
                        "draft_id": draft_id}
            pool = state["candidates"].setdefault(image_id, [])
            if len(pool) >= _MAX_CANDIDATES_PER_SLOT:
                return {"error": f"candidate pool for slot {image_id!r} "
                                 f"is full ({_MAX_CANDIDATES_PER_SLOT} "
                                 f"candidates max) — vet or drop the "
                                 f"search before registering more",
                        "draft_id": draft_id}
            if any(c.get("source_url") == source_url for c in pool):
                return {"error": f"candidate {source_url!r} is already "
                                 f"registered for slot {image_id!r}",
                        "draft_id": draft_id}
            candidate = {
                "id": f"cand-{uuid.uuid4().hex[:10]}",
                "slot_id": image_id,
                "source_url": source_url,
                "source_name": matched.get("name", ""),
                "license_type": license_type,
                "license_note": license_note,
                "attribution_text": attribution_text,
                "registered_at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"),
                "vetting": None,
                "status": "pending_vetting",
                "rejection_reason": "",
            }
            pool.append(candidate)
            draft["images"] = state
            _store_draft(draft)
    except ValueError as exc:  # lock timeout / invalid draft id
        return {"error": str(exc), "draft_id": draft_id}
    return {"draft_id": draft_id, "image_id": image_id,
            "candidate": candidate, "pool_size": len(pool)}


@mcp.tool()
def record_candidate_vetting(draft_id: str, image_id: str,
                             candidate_id: str, content_grade: int,
                             issues: list[str] | None = None,
                             notes: str = "") -> dict:
    """Record YOUR vetting judgement for one searched candidate —
    step 3 of the chain, the same judged-layer split as
    record_vision_check (the core cannot see the image; you can).
    Look at the actual candidate first: `content_grade` 0/1/2 = how
    well its subject and scene fit the slot's intent; `issues` from
    the fixed vocabulary (watermark, embedded_text, low_resolution,
    wrong_subject, off_brand_scene). A candidate with grade 0, a
    watermark or the wrong subject is REJECTED — the reason is kept
    in the pool as trial data, and a rejected candidate can never
    fulfil the slot. Watermarked images are rejected, never cleaned:
    removing a watermark is not an allowed edit op anywhere in this
    system. Re-recording vetting for a non-fulfilled candidate
    replaces the record."""
    if isinstance(content_grade, bool) or not isinstance(content_grade, int) \
            or content_grade not in (0, 1, 2):
        return {"error": f"content_grade must be an int in 0..2, "
                         f"got {content_grade!r}", "draft_id": draft_id}
    bad_issues = [i for i in issues or [] if i not in _VETTING_ISSUES]
    if bad_issues:
        return {"error": f"issues {bad_issues} are outside the fixed "
                         f"vocabulary {sorted(_VETTING_ISSUES)}",
                "draft_id": draft_id}
    try:
        with _draft_lock(draft_id):
            draft, error = _load_draft_or_error(draft_id)
            if error:
                return error
            state = _draft_images_state(draft)
            pool = state["candidates"].get(image_id) or []
            candidate = next(
                (c for c in pool if c.get("id") == candidate_id), None)
            if candidate is None:
                return {"error": f"unknown candidate {candidate_id!r} "
                                 f"in slot {image_id!r}'s pool",
                        "draft_id": draft_id}
            if candidate.get("status") == "fulfilled":
                return {"error": f"candidate {candidate_id!r} already "
                                 f"fulfilled slot {image_id!r} — its "
                                 f"vetting is part of the shipped record",
                        "draft_id": draft_id}
            vetting = {
                "content_grade": content_grade,
                "issues": sorted(set(issues or [])),
                "notes": notes,
                "checked_at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"),
            }
            reason = _vetting_rejection(vetting)
            candidate["vetting"] = vetting
            candidate["status"] = "rejected" if reason else "viable"
            candidate["rejection_reason"] = reason
            draft["images"] = state
            _store_draft(draft)
    except ValueError as exc:  # lock timeout / invalid draft id
        return {"error": str(exc), "draft_id": draft_id}
    return {"draft_id": draft_id, "image_id": image_id,
            "candidate": candidate}


@mcp.tool()
def fulfil_images_v2(draft_id: str, allow_generation: bool = False) -> dict:
    """The full slot-filling chain (spec §3.1): library first, then
    the searched-candidate step, generation LAST. Unlike
    fulfil_images_from_library (the v1 tool, unchanged), this tool
    orchestrates all six steps and reports a per-slot status:

    - library matches are fulfilled immediately (origin "library");
    - a slot with viable vetted candidates reports
      "candidates_ready" — pick one via set_image(source="searched");
    - a fulfilled searched image whose vision grade is 0 on either
      axis reports "reopened" with the deterministic next step:
      try the candidate's UNEDITED original, then the next viable
      candidate, then generation;
    - a slot whose pool is empty reports "awaiting_candidates", all
      rejected reports "search_exhausted" (reasons kept), unvetted
      reports "awaiting_vetting";
    - generation appears in `needs_generation` ONLY when
      `allow_generation=True` — your explicit confirmation that the
      search is exhausted for those slots. Without the flag such
      slots report "awaiting_generation_confirmation" (or their
      search status). product_specific slots skip the search steps
      entirely: library, then the generation path. For a brand
      without an image_sources allowlist there is no search step, so
      unfilled slots go straight to the generation path."""
    try:
        with _draft_lock(draft_id):
            draft, error = _load_draft_or_error(draft_id)
            if error:
                return error
            state = _draft_images_state(draft)
            if not state["plan"]:
                return {"error": "draft has no image plan — call "
                                 "plan_images first", "draft_id": draft_id}
            brief = draft.get("brief", {})
            brand_id = brief.get("brand_id") or "moc-brew"
            try:
                brand = _resolve_brand_only(
                    draft.get("brief", {}).get("brand_id", ""))
            except ValueError as exc:
                return {"error": str(exc), "draft_id": draft_id}
            sources = _image_sources_of(brand)
            try:
                entries = _list_image_library_file(brand_id)
            except ValueError as exc:
                return {"error": str(exc), "draft_id": draft_id}
            images = state["images"]
            pools = state["candidates"]
            used = {e.get("from_library") for e in images.values()
                    if isinstance(e, dict) and e.get("from_library")}
            used_refs = {_normalise_ref(e.get("ref") or "")
                         for e in images.values() if isinstance(e, dict)
                         and str(e.get("ref") or "").strip()}
            fulfilled: list[dict] = []
            slots: list[dict] = []
            needs_generation: list[dict] = []

            def _generation_path(item: dict, exhausted_status: str,
                                 detail: str) -> None:
                if allow_generation:
                    needs_generation.append({
                        "image_id": item["id"], "prompt": item["prompt"],
                        "style_block": item.get("style_block", ""),
                        "mark_generated": True})
                else:
                    slots.append({"image_id": item["id"],
                                  "status": exhausted_status,
                                  "detail": detail})

            for item in state["plan"]:
                iid = item["id"]
                existing = images.get(iid)
                if isinstance(existing, dict) \
                        and str(existing.get("ref") or "").strip():
                    # Grade-0 fallback for searched fulfilments only:
                    # the chain reopens the slot with its next step.
                    origin = existing.get("origin") or {}
                    vc = existing.get("vision_check")
                    grades_zero = isinstance(vc, dict) and (
                        vc.get("content_grade") == 0
                        or vc.get("style_grade") == 0)
                    if origin.get("kind") in (
                            "searched_edited", "searched_unedited") \
                            and grades_zero:
                        pool = pools.get(iid) or []
                        if origin["kind"] == "searched_edited":
                            slots.append({
                                "image_id": iid, "status": "reopened",
                                "next_step": {
                                    "action": "try_unedited",
                                    "candidate_id":
                                        origin.get("candidate_id", "")}})
                            continue
                        nxt = next(
                            (c for c in pool
                             if c.get("status") == "viable"
                             and c.get("id") != origin.get(
                                 "candidate_id")), None)
                        if nxt is not None:
                            slots.append({
                                "image_id": iid, "status": "reopened",
                                "next_step": {
                                    "action": "try_candidate",
                                    "candidate_id": nxt["id"]}})
                            continue
                        _generation_path(
                            item, "search_exhausted",
                            "the searched candidates for this slot all "
                            "failed the vision check")
                    continue
                match = _best_library_match(item, brief, entries, used,
                                            used_refs)
                if match is not None:
                    entry, score = match
                    used.add(entry["id"])
                    used_refs.add(_normalise_ref(entry.get("ref") or ""))
                    images[iid] = {
                        "source": entry.get("source", "client"),
                        "ref": entry.get("ref", ""),
                        "filename": _suggest_filename(item, brief),
                        "alt": _suggest_alt(
                            item, brief,
                            description=entry.get("description", "")),
                        "alt_suggested": True,
                        "caption": "",
                        "description": entry.get("description", ""),
                        "prompt_used": "", "from_library": entry["id"],
                        "origin": {"kind": "library", "source_url": "",
                                   "license_type": "", "candidate_id": "",
                                   "edit_ops": [], "attribution_text": ""},
                    }
                    fulfilled.append({"image_id": iid,
                                      "library_id": entry["id"],
                                      "ref": entry.get("ref", ""),
                                      "score": round(score, 3),
                                      "origin": "library"})
                    continue
                # No library match: the search steps, unless fenced.
                if item.get("product_specific") or not sources:
                    _generation_path(
                        item, "awaiting_generation_confirmation",
                        "product_specific slot — library or generation "
                        "only" if item.get("product_specific") else
                        "brand has no image_sources allowlist — no "
                        "search step")
                    continue
                pool = pools.get(iid) or []
                viable = [c for c in pool if c.get("status") == "viable"]
                if viable:
                    slots.append({"image_id": iid,
                                  "status": "candidates_ready",
                                  "candidates": [c["id"] for c in viable]})
                    continue
                rejected = [c for c in pool
                            if c.get("status") == "rejected"]
                pending = [c for c in pool
                           if c.get("status") == "pending_vetting"]
                if pool and not pending:
                    if allow_generation:
                        needs_generation.append({
                            "image_id": iid, "prompt": item["prompt"],
                            "style_block": item.get("style_block", ""),
                            "mark_generated": True})
                    else:
                        slots.append({
                            "image_id": iid, "status": "search_exhausted",
                            "rejected": [{
                                "candidate_id": c["id"],
                                "reason": c.get("rejection_reason", "")}
                                for c in rejected]})
                    continue
                if pending:
                    slots.append({"image_id": iid,
                                  "status": "awaiting_vetting",
                                  "pool_size": len(pool),
                                  "pending_vetting": len(pending)})
                    continue
                if allow_generation:
                    needs_generation.append({
                        "image_id": iid, "prompt": item["prompt"],
                        "style_block": item.get("style_block", ""),
                        "mark_generated": True})
                else:
                    slots.append({"image_id": iid,
                                  "status": "awaiting_candidates",
                                  "pool_size": 0})
            state["images"] = images
            draft["images"] = state
            _store_draft(draft)
    except ValueError as exc:  # lock timeout / invalid draft id
        return {"error": str(exc), "draft_id": draft_id}
    return {"draft_id": draft_id, "fulfilled": fulfilled, "slots": slots,
            "needs_generation": needs_generation, "images": images}


# --------------------------------------------------------------------------
# Tools — deterministic checks & the final gate
# --------------------------------------------------------------------------

@mcp.tool()
def brand_check(text: str = "", format: str = "blog", draft_id: str = "",
                brand_id: str = "") -> dict:
    """Deterministic brand linter + score (0-100). Pass either a raw `text`
    (checked as one section) or a `draft_id` (all saved sections checked;
    overall score = worst section). Catches banned terms, exclamation marks
    in blog, sentence-length rule violations.

    Brand resolution (trial bug 1 — the standalone tool used to lint
    EVERY draft against the server brand, so a mambagiaokhoe draft
    scored 88 against Mộc Brew's rules while its own gate scored 100):
    `brand_id` when given; else the draft's brief.brand_id; else the
    server brand (moc-brew) — the same order fact_check uses. The
    result names the brand it used (`brand_id`). An unknown brand is a
    structured error, never a silent lint against Mộc Brew."""
    if draft_id:
        draft, error = _load_draft_or_error(draft_id)
        if error:
            return error
        brand_id = brand_id or draft.get("brief", {}).get("brand_id", "")
    try:
        brand = _resolve_brand_only(brand_id)
    except ValueError as exc:
        return {"error": str(exc)}
    resolved_id = brand.get("brand", {}).get("id", "")
    if draft_id:
        fmt = draft.get("brief", {}).get("format", format)
        violations, score, failing = _lint_draft(_sections_of(draft), fmt, brand)
        return {"score": score, "violations": violations, "failing_sections": failing,
                "brand_id": resolved_id,
                "banned_found": [v["detail"] for v in violations if v["type"] == "banned_term"]}
    violations, score = _lint_section(text, format, brand, "text")
    # The free-text path applies the SAME failing rule as the draft
    # path (review L7): lint_draft fails a section on a non-warning
    # violation or a score under the pass mark — the old
    # `if violations` here marked a warning-only text (score 100)
    # as failing, so the two paths disagreed about the same copy.
    failing = bool(violations) and (
        any(not v.get("warning") for v in violations) or score < BRAND_PASS_SCORE)
    return {"score": score, "violations": violations,
            "failing_sections": ["text"] if failing else [],
            "brand_id": resolved_id,
            "banned_found": [v["detail"] for v in violations if v["type"] == "banned_term"]}


@mcp.tool()
def fact_check(text: str = "", draft_id: str = "", brand_id: str = "") -> dict:
    """Deterministic fact checker. Every price/weight/delivery number must
    match the ACTIVE brand's facts. Claims marked [NEEDS SOURCE] are
    flagged (do not block); unmarked unsupported/prohibited claims are
    issues (block).

    Brand resolution: `brand_id` when given; else the draft's
    brief.brand_id; else the server brand (moc-brew). A non-default
    brand resolves its rules from its stored Brand Builder draft and
    its numbers from its stored facts profile — an unknown brand is a
    structured error, never a silent check against Mộc Brew."""
    if draft_id:
        draft, error = _load_draft_or_error(draft_id)
        if error:
            return error
        brand_id = brand_id or draft.get("brief", {}).get("brand_id", "")
    try:
        brand, facts = _resolve_brand_facts(brand_id)
    except ValueError as exc:
        return {"error": str(exc)}
    if draft_id:
        issues, flagged, failing = _fact_check_draft(_sections_of(draft), facts, brand)
    else:
        issues, flagged = _fact_check_section(text, facts, brand, "text")
        failing = ["text"] if issues else []
    return {"ok": not issues, "issues": issues, "flagged_claims": flagged,
            "failing_sections": failing}


@mcp.tool()
def seo_check(draft_id: str = "", title: str = "", keyword: str = "", topic: str = "") -> dict:
    """Deterministic SEO check (title / meta / headings / keyword coverage).
    With draft_id, checks the stored draft; title/keyword/topic override the
    draft's brief fields when given."""
    if draft_id:
        draft, error = _load_draft_or_error(draft_id)
        if error:
            return error
        brief = {**draft.get("brief", {})}
        sections = _sections_of(draft)
    else:
        brief, sections = {}, []
    if title:
        brief["title"] = title
    if keyword:
        brief["keyword"] = keyword
    if topic:
        brief["topic"] = topic
    return _seo_pass(brief, sections)


@mcp.tool()
def get_quality_profile(format_or_id: str) -> dict:
    """Quality Profile: the 7-axis weighting for the quality gate layer.

    Pass a format name (blog / product / social / email / comparison) for
    the shipped preset, 'preset-<format>', or the id of a custom profile
    saved with save_quality_profile. A profile is {id, format, weights
    (7 axes, sum exactly 100), floors (per-axis minimums), pass_total}."""
    try:
        return _quality_get_profile(format_or_id)
    except ValueError as exc:
        return {"error": str(exc), "formats": sorted(_QUALITY_PRESETS)}


@mcp.tool()
def save_quality_profile(format: str, weights: dict, floors: dict | None = None,
                         pass_total: float | None = None,
                         profile_id: str = "") -> dict:
    """Validate + persist a CUSTOM Quality Profile for a format.

    `weights` must cover exactly the 7 axes (facts, voice, clarity,
    coherence, seo, persuasion, depth), each within [2, 45], summing to
    exactly 100 — the budget bar from setup. `floors` overrides the
    default per-axis floors (each within [0, 90]); `pass_total` overrides
    the default 70. Returns the stored profile with its id."""
    try:
        profile = _make_quality_profile(format, weights, floors, pass_total,
                                        profile_id or None)
        return _save_quality_profile_file(profile)
    except ValueError as exc:
        # The save sat outside the try (review M6) — same dialect fix
        # as save_facts.
        return {"error": str(exc)}


@mcp.tool()
def list_quality_profiles() -> dict:
    """List the saved CUSTOM Quality Profiles: id, format, pass_total
    and the weights summary (the 7-axis budget) for each — the
    listing counterpart to get_quality_profile's by-id lookup.
    Shipped format presets are NOT listed (they are not stored
    profiles; read one with get_quality_profile(<format>)). Corrupt
    files are listed as corrupt, never silently skipped (the house
    list discipline)."""
    out = []
    for entry in _list_quality_profiles():
        entry = dict(entry)
        if not entry.get("corrupt"):
            profile = _load_quality_profile(entry["id"])
            if profile is not None:
                entry["weights"] = profile.get("weights")
        out.append(entry)
    return {"profiles": out, "count": len(out)}


@mcp.tool()
def quality_score(draft_id: str, judged_scores: dict | None = None,
                  quality_profile: str = "") -> dict:
    """Score a stored draft on the Quality Profile's 7 axes — WITHOUT
    running the gate (report only; final_gate is the verdict).

    Measured axes (facts / voice / clarity / seo) are computed in code.
    Judged axes (coherence / persuasion / depth) come from YOU, the
    writing model: pass `judged_scores` as rubric grades 0/1/2 per axis
    (converted 0→20, 1→60, 2→95). A judged axis you omit is EXCLUDED and
    the weights renormalise — it is never invented. `quality_profile` is
    a format name or a saved custom id; default is the draft format's
    preset. The report is dumbbell-ready: per-axis score vs floor, plus
    weighted_total and quality_pass (total >= pass_total AND no axis
    below its floor). It also carries `depth_context` — what material
    the depth axis was graded against: "research_pack" (the brand's
    research pack has applied items), "brand_facts_only", or "none".
    Metadata only: it never changes a score.

    A brief that names a style_profile_id which no longer resolves is
    a structured error naming the id — the draft is never silently
    scored without the intended profile (round-3 S3 rule, approved
    item A)."""
    draft, error = _load_draft_or_error(draft_id)
    if error:
        return error
    fmt = draft.get("brief", {}).get("format", "blog")
    try:
        profile = (_quality_get_profile(quality_profile) if quality_profile
                   else _quality_preset(fmt))
        wanted_style = draft.get("brief", {}).get("style_profile_id")
        style_profile = _resolve_style_profile(wanted_style)
        if wanted_style and style_profile is None:
            return {"error": f"style profile not found: {wanted_style}",
                    "draft_id": draft_id}
        # The fact base (and brand rules) belong to the draft's brand:
        # brief.brand_id when the brief names one, else the server brand.
        gate_brand, gate_facts = _resolve_brand_facts(
            draft.get("brief", {}).get("brand_id", ""))
        report = _evaluate_quality(_sections_of(draft), fmt, profile,
                                   judged_scores, gate_brand, gate_facts,
                                   style_profile=style_profile,
                                   brief=draft.get("brief", {}))
    except ValueError as exc:
        return {"error": str(exc), "draft_id": draft_id}
    # depth_context (quality uplift WS1): what material the depth
    # grading had available — additive metadata only; no score in
    # the report above depends on it.
    return {**report, "draft_id": draft_id,
            "depth_context": _research.depth_context(
                gate_brand.get("brand", {}).get("id", ""), gate_facts)}


@mcp.tool()
def check_draft_all(draft_id: str, judged_scores: dict | None = None,
                    quality_profile: str = "") -> dict:
    """Combined check for a stored draft: brand + fact + craft + style +
    quality in ONE call, each checker run ONCE (perf P3).

    The writer's normal revision loop used to call brand_check,
    fact_check, craft_check, style_match and quality_score separately,
    and every stateless tool re-ran every checker over the same text
    (lint/fact 4x, craft 3x per cycle). This tool resolves the draft's
    brand/facts/style ONCE, runs each checker once, and feeds the
    lint/fact results into the quality layer through the same
    _precomputed reuse the gate itself uses (review M4) — so every
    number here is exactly what the individual tool (or final_gate)
    reports for the same draft. It changes NO state: no gate history,
    no verdict — final_gate remains the only verdict.

    Returns {brand, fact, craft, style, quality, draft_id} where each
    sub-report mirrors the corresponding tool's shape: brand =
    brand_check's draft result, fact = fact_check's, craft =
    craft_check's (judged with the draft brand's own context, exactly
    as final_gate wires it), style = style_match's report (+profile_id)
    or None when the brief carries no style_profile_id, quality =
    quality_score's report. `judged_scores` / `quality_profile` behave
    exactly as in quality_score. A brief that names a
    style_profile_id which no longer resolves is a structured error
    naming the id (round-3 S3 rule, approved item A) — never a
    combined report with style None and a style-less quality."""
    draft, error = _load_draft_or_error(draft_id)
    if error:
        return error
    fmt = draft.get("brief", {}).get("format", "blog")
    try:
        profile = (_quality_get_profile(quality_profile) if quality_profile
                   else _quality_preset(fmt))
        wanted_style = draft.get("brief", {}).get("style_profile_id")
        style_profile = _resolve_style_profile(wanted_style)
        if wanted_style and style_profile is None:
            return {"error": f"style profile not found: {wanted_style}",
                    "draft_id": draft_id}
        brand, facts = _resolve_brand_facts(
            draft.get("brief", {}).get("brand_id", ""))
    except ValueError as exc:
        return {"error": str(exc), "draft_id": draft_id}
    sections = _sections_of(draft)
    violations, brand_score, lint_failing = _lint_draft(sections, fmt, brand)
    issues, flagged, fact_failing = _fact_check_draft(sections, facts, brand)
    brand_id, max_sentence_words, gate_banned_terms = _brand_check_context(brand)
    craft = _craft_check_draft(sections, fmt, brand_id=brand_id,
                               max_sentence_words=max_sentence_words,
                               banned_terms=gate_banned_terms)
    style = None
    if style_profile is not None:
        joined = "\n\n".join(s.get("text", "") for s in sections)
        style = {**_style_match(joined, style_profile),
                 "profile_id": style_profile["id"]}
    try:
        quality = _evaluate_quality(
            sections, fmt, profile, judged_scores, brand, facts,
            style_profile=style_profile, brief=draft.get("brief", {}),
            _precomputed={"fact_issues": issues, "fact_flagged": flagged,
                          "brand_violations": violations,
                          "brand_score": brand_score})
    except ValueError as exc:
        return {"error": str(exc), "draft_id": draft_id}
    resolved_id = brand.get("brand", {}).get("id", "")
    return {
        "brand": {"score": brand_score, "violations": violations,
                  "failing_sections": lint_failing, "brand_id": resolved_id,
                  "banned_found": [v["detail"] for v in violations
                                   if v["type"] == "banned_term"]},
        "fact": {"ok": not issues, "issues": issues,
                 "flagged_claims": flagged, "failing_sections": fact_failing},
        "craft": craft,
        "style": style,
        "quality": {**quality, "draft_id": draft_id,
                    "depth_context": _research.depth_context(
                        resolved_id, facts)},
        "draft_id": draft_id,
    }


@mcp.tool()
def score_section_variants(section_role: str, variants: list[str],
                           draft_id: str = "", brand_id: str = "",
                           format: str = "",
                           style_profile_id: str = "") -> dict:
    """Best-of-N: rank 2–5 candidate texts for ONE section role
    (headline / opening / cta / closing) with the gate's own checkers,
    so the writer picks the strongest variant BEFORE saving it.

    Context: pass `draft_id` (brand, facts, format and style profile
    resolve from the draft, exactly as quality_score resolves them)
    or `brand_id` (+ optional `format`, default blog; the style
    profile then defaults from the brand's corpus choice, as in
    assemble_context). An explicit `style_profile_id` always wins.
    A profile the brief or the caller names that no longer resolves
    is an error — variants are never silently scored without the
    intended profile; a stale corpus-choice default just means no
    style part.

    Each variant is scored IN ISOLATION as a one-section draft:
    brand (linter), fact (fact_check), craft and style_match where a
    profile applies. A variant with a HARD violation — a banned term
    or an unflagged fact issue — is INELIGIBLE and ranks last however
    fluent it is (hard criteria stay outside the weighting, as in
    the gate). The composite is the mean of the component scores
    that could be computed; a variant too short for the craft
    checker reports craft as not scored instead of a fake 0. Axes
    that need the full draft (seo, coherence) are listed in
    `not_scored` with reasons — never faked. Changes NO state."""
    style_profile = None
    # Where the wanted profile came from: "explicit" (the caller's
    # argument) and "brief" (the draft's brief) name a profile the
    # writer INTENDED, so a stale id is an error (round-3 S3);
    # "corpus_choice" is only a default, so a stale default silently
    # means no style part — exactly as assemble_context treats it.
    wanted_from = None
    if draft_id:
        draft, error = _load_draft_or_error(draft_id)
        if error:
            return error
        brief = draft.get("brief", {})
        fmt = format or brief.get("format", "blog")
        try:
            brand, facts = _resolve_brand_facts(brief.get("brand_id", ""))
        except ValueError as exc:
            return {"error": str(exc), "draft_id": draft_id}
        wanted_style = style_profile_id or brief.get("style_profile_id")
        if wanted_style:
            wanted_from = "explicit" if style_profile_id else "brief"
    elif brand_id:
        fmt = format or "blog"
        try:
            brand, facts = _resolve_brand_facts(brand_id)
        except ValueError as exc:
            return {"error": str(exc), "brand_id": brand_id}
        wanted_style = style_profile_id
        if wanted_style:
            wanted_from = "explicit"
        if not wanted_style:
            try:
                choice = _load_corpus_choice(brand_id)
            except ValueError:  # corrupt choice record: no default
                choice = None
            if choice is not None:
                wanted_style = choice.get("profile_id")
                if wanted_style:
                    wanted_from = "corpus_choice"
    else:
        return {"error": "pass draft_id or brand_id so the variants "
                         "are judged in a brand's context"}
    if wanted_style:
        try:
            style_profile = _resolve_style_profile(wanted_style)
        except ValueError as exc:  # corrupt stored profile (review M6)
            return {"error": str(exc)}
        if style_profile is None and wanted_from in ("explicit", "brief"):
            return {"error": f"style profile not found: {wanted_style}"}
    try:
        result = _variants.score_variants(
            variants, section_role, fmt, brand, facts,
            style_profile=style_profile)
    except ValueError as exc:
        return {"error": str(exc)}
    if draft_id:
        result["draft_id"] = draft_id
    return result


@mcp.tool()
def final_gate(draft_id: str, quality_profile: str = "",
               judged_scores: dict | None = None) -> dict:
    """The final gate for a stored draft — THE shared gate rule
    (content_core/gates.py, also used by the LangGraph graph). PASS only if
    brand score >= 85 AND 0 banned terms AND 0 unflagged fact issues AND —
    for blog/product drafts only — craft score >= 70 (CRAFT_PASS_SCORE)
    AND, when the quality layer is active, the Quality Profile passes.
    On failure returns exactly which sections must be rewritten (rewrite
    only those). Rewrite budget: max 2 cycles, tracked in the draft state;
    the result is recorded in the draft's gate_history.

    CRAFT GATE (P2b): craft BLOCKS for formats in CRAFT_BLOCKING_FORMATS
    (blog, product): a craft score below 70 fails the gate, the craft-failing
    sections join failing_sections, and the instruction tells the writer to
    rewrite them for craft (weak words / rhythm / sensory detail). For other
    formats (social, email, comparison) craft stays ADVISORY: reported in
    `craft`, recorded in gate_history, never blocking (`craft_advisory` is
    True exactly when the format is non-blocking).

    QUALITY LAYER (optional): pass `quality_profile` (a format name or a
    saved custom profile id) and/or `judged_scores` ({coherence,
    persuasion, depth} as rubric grades 0/1/2, judged by YOU the writer)
    to activate it — with judged scores alone, the draft format's preset
    profile is used. When active, the verdict includes `quality` (per-axis
    scores vs floors + weighted total) and the gate FAILS if the weighted
    total is under pass_total or ANY axis is below its floor — hard gates
    still dominate: no quality score can rescue banned terms or fact
    errors. With NEITHER argument given, the quality layer is inactive
    and the result is exactly the legacy hard+craft verdict.

    STYLE (advisory, v1): when the draft's brief carries `style_profile_id`,
    a `style_match` report against that Style Profile is included and its
    score recorded in gate_history as `style_score`. It NEVER changes
    `passed`. Without a profile in the brief, the result is unchanged.
    A brief naming a profile that no longer resolves is a structured
    error naming the id (S3 parity with quality_score / check_draft_all,
    fix round F4) — unless the id arrived as the brand's corpus-choice
    default (`style_profile_source` == "brand_corpus_choice"), which is
    silently skipped exactly as assemble_context treats a stale default.

    The gate's load→verdict→store runs under the per-draft lock (see
    _draft_lock): concurrent gates in another server process queue
    instead of losing each other's gate_history entries; a lock
    timeout is a structured error."""
    try:
        with _draft_lock(draft_id):
            return _final_gate_impl(draft_id, quality_profile, judged_scores)
    except ValueError as exc:
        return {"error": str(exc), "draft_id": draft_id}


_GATE_HISTORY_CAP = 50   # review L6: stored gate_history bound


def _final_gate_impl(draft_id: str, quality_profile: str = "",
                     judged_scores: dict | None = None) -> dict:
    draft, error = _load_draft_or_error(draft_id)
    if error:
        return error
    fmt = draft.get("brief", {}).get("format", "blog")

    if not _sections_of(draft):
        # Empty draft (evaluation R8): there is nothing to gate. Say so
        # plainly instead of the old "Rewrite ONLY sections []".
        return {"passed": False, "draft_id": draft_id,
                "status": draft.get("status", "drafting"),
                "failing_sections": [],
                "instruction": ("This draft has no sections yet — write at "
                                "least one section with save_section, then "
                                "call final_gate again.")}

    profile = None
    if quality_profile:
        try:
            profile = _quality_get_profile(quality_profile)
        except ValueError as exc:
            return {"error": str(exc), "draft_id": draft_id}

    # Style profile (if the brief carries one): feeds the advisory style
    # report below AND the quality layer's voice axis when that runs.
    # S3 parity (fix round F4, Smith-approved via the 23 Nov review):
    # a brief that NAMES a profile which no longer resolves is a
    # structured error naming the id — the gate never silently runs
    # without the intended profile, exactly as quality_score and
    # check_draft_all already behave (approved item A). The one
    # exception mirrors assemble_context: a brief whose id arrived as
    # the brand's corpus-choice DEFAULT (style_profile_source ==
    # "brand_corpus_choice") treats a stale id as silently no style
    # part. A corrupt stored profile raises in the loader and becomes
    # the same structured error — never a crash, never a verdict.
    wanted_style = draft.get("brief", {}).get("style_profile_id")
    style_profile = None
    if wanted_style:
        try:
            style_profile = _resolve_style_profile(wanted_style)
        except ValueError as exc:  # corrupt stored profile (review M6)
            return {"error": str(exc), "draft_id": draft_id}
        if style_profile is None and draft.get(
                "brief", {}).get("style_profile_source") \
                != "brand_corpus_choice":
            return {"error": f"style profile not found: {wanted_style}",
                    "draft_id": draft_id}

    try:
        # The gate judges against the DRAFT's brand (brief.brand_id)
        # and that brand's fact base — never another brand's numbers.
        gate_brand, gate_facts = _resolve_brand_facts(
            draft.get("brief", {}).get("brand_id", ""))
        verdict = _run_gate(draft, quality_profile=profile, judged=judged_scores,
                            style_profile=style_profile,
                            brand=gate_brand, facts=gate_facts)
    except ValueError as exc:  # e.g. an invalid judged grade / unknown brand
        return {"error": str(exc), "draft_id": draft_id}
    hard = verdict["hard"]
    craft = verdict["craft"]["report"]
    craft_blocking = verdict["craft"]["blocking"]
    craft_failed = verdict["craft"]["failed"]
    quality = verdict["quality"]
    passed = verdict["passed"]
    failing = verdict["failing_sections"]
    result = {"brand_score": hard["brand_score"],
              "brand_violations": hard["brand_violations"],
              "banned_terms_found": hard["banned_terms_found"],
              "fact_issues": hard["fact_issues"],
              "flagged_claims": hard["flagged_claims"]}

    # Style match — ADVISORY only (v1): reported + recorded, never blocking.
    style_report = None
    if style_profile is not None:
        joined = "\n\n".join(s.get("text", "") for s in _sections_of(draft))
        style_report = {**_style_match(joined, style_profile),
                        "profile_id": style_profile["id"]}

    history = list(draft.get("gate_history", []))
    # Pass numbers stay monotonic even once the history is capped
    # (review L6): derive from the last entry, not the list length.
    entry = {"pass": (history[-1].get("pass", len(history)) + 1
                      if history else 1),
             "brand_score": hard["brand_score"],
             "brand_violations": len(hard["brand_violations"]),
             "fact_issues": len(hard["fact_issues"]), "passed": passed,
             "craft_score": craft["score"], "craft_blocking": craft_blocking,
             "craft_advisory": not craft_blocking,
             # Fix round F1: the fingerprint of the exact content
             # this verdict describes — the WordPress exporter
             # refuses to ship a draft whose current content no
             # longer matches its recorded pass.
             "content_sha": _content_sha(_sections_of(draft),
                                         _draft_title(draft))}
    if style_report is not None:
        entry["style_score"] = style_report["score"]
        entry["style_profile_id"] = style_report["profile_id"]
    if quality is not None:
        entry["quality"] = {"profile_id": quality["profile_id"],
                            "weighted_total": quality["weighted_total"],
                            "quality_pass": quality["quality_pass"],
                            "floors_failed": quality["floors_failed"]}
    history.append(entry)
    # Bounded (review L6): a long-lived draft's gate_history used to
    # grow without limit and bloat every get_draft response.
    draft["gate_history"] = history[-_GATE_HISTORY_CAP:]

    if passed:
        draft["status"] = "approved"
        draft["awaiting_rewrite"] = []
    elif draft.get("rewrite_count", 0) < MAX_REWRITES:
        draft["status"] = "rewriting"
        draft["awaiting_rewrite"] = failing
    else:
        draft["status"] = "failed_gates"
        draft["awaiting_rewrite"] = []
    _store_draft(draft)

    if passed:
        instruction = "Draft approved — deliver it."
    else:
        instruction = (f"Rewrite ONLY sections {failing}, save them with "
                       f"save_section, then call final_gate again.")
        if craft_failed:
            instruction += (f" Craft is BLOCKING for {fmt}: score {craft['score']} "
                            f"< {CRAFT_PASS_SCORE} — rewrite the craft-failing sections "
                            f"for craft: cut weak/vague words, vary sentence rhythm, "
                            f"add concrete sensory detail from the domain glossary.")
        if quality is not None and not quality["quality_pass"]:
            instruction += (f" Quality layer FAILED (profile {quality['profile_id']}): "
                            f"weighted total {quality['weighted_total']} vs pass_total "
                            f"{quality['pass_total']}; axes below floor: "
                            f"{quality['floors_failed'] or 'none'} — strengthen the "
                            f"weak axes (the quality report names them) and re-run "
                            f"final_gate with the same quality arguments.")
    gate_result = {**result, "passed": passed, "failing_sections": failing,
                   "draft_id": draft_id, "status": draft["status"],
                   "craft": craft, "craft_score": craft["score"],
                   "craft_blocking": craft_blocking, "craft_advisory": not craft_blocking,
                   "rewrite_count": draft.get("rewrite_count", 0),
                   "rewrites_remaining": max(0, MAX_REWRITES - draft.get("rewrite_count", 0)),
                   "gate_history": draft["gate_history"],
                   "instruction": instruction}
    if quality is not None:
        gate_result["quality"] = quality
    if style_report is not None:
        gate_result["style_match"] = style_report
        gate_result["style_advisory"] = True
    return gate_result


# --------------------------------------------------------------------------
# Tools — research pack (specs/research-config-feature.md)
#
# The setup-phase flow: gather sources into a pack, propose items
# with provenance, the owner decides item by item (manual — there is
# deliberately NO bulk-approve tool), and apply_pack is the only door
# into live configuration. All logic lives in content_core/research.py;
# these tools are the thin MCP surface over it, in the uniform
# {"error": ...} dialect.
# --------------------------------------------------------------------------

def _pack_or_error(brand_id: str) -> tuple[dict | None, dict | None]:
    """(pack, None) or (None, {"error": ...}) for the pack tools."""
    try:
        pack = _load_research_pack(brand_id)
    except ValueError as exc:  # corrupt stored pack / unusable id
        return None, {"error": str(exc), "brand_id": brand_id}
    if pack is None:
        return None, {"error": f"research pack not found: {brand_id} — "
                               f"create it first (create_research_pack)",
                      "brand_id": brand_id}
    return pack, None


@mcp.tool()
def create_research_pack(brand_id: str) -> dict:
    """Create the research pack for a brand (the pre-approval store
    for a brand setup: sources, proposed items, contradictions,
    quality rejections). Re-creating an existing pack starts a NEW
    VERSION: prior counts are archived into the pack's history and
    the item lists reset — live configuration is never touched by
    creating or re-creating a pack."""
    try:
        pack = _research.create_pack(brand_id)
    except ValueError as exc:
        return {"error": str(exc), "brand_id": brand_id}
    return {"brand_id": pack["brand_id"], "version": pack["version"],
            "created_at": pack["created_at"],
            "history": pack.get("history", [])}


@mcp.tool()
def add_pack_source(brand_id: str, url: str, kind: str,
                    legitimacy_basis: str = "",
                    quality_verdict: str = "accept",
                    quality_reason: str = "",
                    pages: list | None = None) -> dict:
    """Record one gathered source in the pack. kind: own / competitor
    / genre / factual. The quality gate's verdict is recorded with
    its reason; a "reject" verdict ALSO lands in the pack's
    quality_rejections[] (spec US-6) and the source contributes
    nothing downstream. `pages` (fetched page dicts from
    fetch_site.py) ride on an own source so build_pack_proposals can
    work from them — this server stays offline and never fetches."""
    pack, error = _pack_or_error(brand_id)
    if error:
        return error
    try:
        source = _research.add_source(
            pack, url, kind, legitimacy_basis=legitimacy_basis,
            quality_verdict=quality_verdict, quality_reason=quality_reason,
            pages=pages)
        _save_research_pack(pack)
    except ValueError as exc:
        return {"error": str(exc), "brand_id": brand_id}
    return {k: v for k, v in source.items() if k != "pages"} | {
        "page_count": len(source.get("pages") or [])}


@mcp.tool()
def build_pack_proposals(brand_id: str) -> dict:
    """Build the deterministic proposals from the pack's accepted OWN
    sources: one brand_rule item (build_brand_draft over the fetched
    pages) plus one fact item per site claim flagged needs_evidence.
    Price/weight/origin facts are proposed individually through
    propose_pack_item — contradiction detection runs on every
    proposal, whoever made it."""
    pack, error = _pack_or_error(brand_id)
    if error:
        return error
    try:
        result = _research.build_proposals(pack)
        _save_research_pack(pack)
    except ValueError as exc:
        return {"error": str(exc), "brand_id": brand_id}
    return {"brand_id": brand_id, **result,
            "counts": _research.pack_counts(pack)}


@mcp.tool()
def propose_pack_item(brand_id: str, kind: str, payload: dict,
                      provenance: dict, reference: bool = False) -> dict:
    """Propose ONE configuration item into the pack (status:
    proposed). kind: brand_rule / fact / style_candidate /
    persona_frame / corpus_choice. Every item carries provenance
    {source_url, note}; a FACT item without a source_url is refused
    (spec US-1). Fact payloads are {field, value} with field one of:
    price_vnd_per_250g, price_display, pack_weight_g, origin,
    known_price_vnd, pack_weight_option_g, delivery_days,
    roast_weekday, entry, claim. A fact whose field+value competes
    with an existing fact item spawns a contradiction record and
    locks both items until it is resolved. reference=True marks
    competitor material: it can be analysed and decided, but apply
    will refuse it."""
    pack, error = _pack_or_error(brand_id)
    if error:
        return error
    try:
        item = _research.propose_item(pack, kind, payload, provenance,
                                      reference=reference)
        _save_research_pack(pack)
    except ValueError as exc:
        return {"error": str(exc), "brand_id": brand_id}
    return item


@mcp.tool()
def get_pack(brand_id: str) -> dict:
    """Return the full research pack for a brand: sources, items,
    contradictions, quality rejections, plus `counts` (items by
    status/kind) and `open_contradictions` — everything the review
    checklist needs in one call (spec 3.5)."""
    pack, error = _pack_or_error(brand_id)
    if error:
        return error
    return {**pack, "counts": _research.pack_counts(pack),
            "open_contradictions": _research.pack_counts(pack)[
                "open_contradictions"]}


@mcp.tool()
def decide_pack_item(brand_id: str, item_id: str, decision: str,
                     edited_payload: dict | None = None,
                     note: str = "") -> dict:
    """Record the owner's manual decision on ONE pack item:
    decision = approve / edit / reject. An edit REQUIRES
    edited_payload; the original payload is kept on the item for
    audit. Decisions are per item by design (Smith's standing rule:
    no bulk approval). An item locked by an open contradiction
    cannot be decided — resolve the contradiction first. An applied
    item is final."""
    pack, error = _pack_or_error(brand_id)
    if error:
        return error
    try:
        item = _research.decide_item(pack, item_id, decision,
                                     edited_payload=edited_payload,
                                     note=note)
        _save_research_pack(pack)
    except ValueError as exc:
        return {"error": str(exc), "brand_id": brand_id}
    return item


@mcp.tool()
def resolve_contradiction(brand_id: str, contradiction_id: str,
                          chosen_item_id: str, note: str = "") -> dict:
    """Resolve an open contradiction by choosing ONE of its items:
    the chosen item becomes approved, every other linked item is
    rejected (it survives only as pack history — the unchosen value
    never reaches live config, spec US-2), and the record closes
    with the resolution for audit."""
    pack, error = _pack_or_error(brand_id)
    if error:
        return error
    try:
        con = _research.resolve_contradiction(
            pack, contradiction_id, chosen_item_id, note=note)
        _save_research_pack(pack)
    except ValueError as exc:
        return {"error": str(exc), "brand_id": brand_id}
    return con


@mcp.tool()
def apply_pack(brand_id: str) -> dict:
    """Apply the pack's approved/edited items into live configuration
    through the existing save paths (brand draft, facts profile,
    style profile, persona file, corpus choice). HARD INVARIANTS:
    proposed/rejected items never apply; an item linked to an open
    contradiction is blocked; reference material is refused; style
    items are saved with store_excerpts=False whatever the payload
    asked for. Idempotent: already-applied items are skipped and
    reported separately. Returns {applied, already_applied, blocked,
    failed} — `applied` is exactly the set of ids that landed in
    this call, for cross-checking against the approved set."""
    pack, error = _pack_or_error(brand_id)
    if error:
        return error
    report = _research.apply_pack(pack)
    _save_research_pack(pack)
    return report


# --------------------------------------------------------------------------
# Tools — SEO five-source research profiles (specs/
# seo-five-source-feature.md, Round 1: profile store + sources +
# analysis). ONE profile per DRAFT: the 5 observed Google results
# for one keyword, their read states, the host model's provenance-
# checked analysis claims, and any shortfall exception the owner
# confirmed. Deliberately separate from research packs: a pack
# configures a BRAND, a profile researches ONE article — and
# nothing here writes to facts / brand / corpus / taste stores
# (spec A7). Mutations run under the per-draft lock in the SEO
# store's own dir (see _draft_lock's `directory` parameter).
# --------------------------------------------------------------------------

def _seo_profile_or_error(draft_id: str) -> tuple[dict | None, dict | None]:
    """(profile, None) or (None, {"error": ...}) for the SEO tools."""
    try:
        profile = _load_seo_profile(draft_id)
    except ValueError as exc:  # corrupt stored profile / unusable id
        return None, {"error": str(exc), "draft_id": draft_id}
    if profile is None:
        return None, {"error": f"seo profile not found: {draft_id} — "
                               f"create it first (create_seo_profile)",
                      "draft_id": draft_id}
    return profile, None


@mcp.tool()
def create_seo_profile(draft_id: str, keyword: str,
                       search_context: dict,
                       brand_id: str | None = None) -> dict:
    """Create the SEO research profile for ONE draft (five-source
    workflow, Round 1). The brand is PINNED from the draft's own
    brief — a brief without a brand_id is refused rather than
    assumed, and a `brand_id` argument that disagrees with the
    brief is a structured error. `search_context` records where
    the results were observed: {market, language, location,
    device, searched_at} — all five required, no defaults are
    invented. One profile per draft; re-creating is refused.
    Starts in state `collecting`."""
    try:
        with _draft_lock(draft_id, directory=_seo_profiles_dir()):
            draft, error = _load_draft_or_error(draft_id)
            if error:
                return error
            brief_brand = str(
                draft.get("brief", {}).get("brand_id") or "").strip()
            if not brief_brand:
                return {"error":
                        f"draft {draft_id} brief carries no brand_id "
                        f"— an SEO profile pins the draft's own "
                        f"brand; refusing to assume one",
                        "draft_id": draft_id}
            if brand_id is not None \
                    and str(brand_id).strip() != brief_brand:
                return {"error":
                        f"brand_id mismatch: the draft's brief pins "
                        f"brand {brief_brand!r}, not {brand_id!r}",
                        "draft_id": draft_id}
            try:
                existing = _load_seo_profile(draft_id)
            except ValueError as exc:  # corrupt stored profile
                return {"error": str(exc), "draft_id": draft_id}
            if existing is not None:
                return {"error":
                        f"seo profile already exists for draft "
                        f"{draft_id}", "draft_id": draft_id}
            profile = _seo.new_profile(draft_id, brief_brand, keyword,
                                       search_context)
            _save_seo_profile(profile)
            return {**profile,
                    "sufficiency": _seo.sufficiency(profile)}
    except ValueError as exc:
        return {"error": str(exc), "draft_id": draft_id}


@mcp.tool()
def get_seo_profile(draft_id: str) -> dict:
    """Return the draft's full SEO profile — keyword, search
    context, workflow state, sources with read states, analysis
    claims with provenance flags, shortfall exception, the
    direction + its recorded user decision, the outline versions
    with their approval status, and the reopen log — plus the
    `sufficiency` report: read_count (policy: partial counts,
    see seo_profile.PARTIAL_READS_COUNT_TOWARD_REQUIRED) and
    full_read_count against the required 5, whether a shortfall
    stands, whether an exception is recorded, and whether
    proceeding is authorised (spec A3)."""
    profile, error = _seo_profile_or_error(draft_id)
    if error:
        return error
    return {**profile, "sufficiency": _seo.sufficiency(profile),
            "review_status": _seo.review_status(
                profile, _draft_content_sha_or_none(draft_id))}


def _draft_content_sha_or_none(draft_id: str) -> str | None:
    """The draft's current content fingerprint, or None when the
    draft cannot be loaded (currency checks then report stale —
    an unverifiable review is never reported current)."""
    draft, error = _load_draft_or_error(draft_id)
    if error:
        return None
    return _content_sha(_sections_of(draft), _draft_title(draft))


@mcp.tool()
def add_seo_source(draft_id: str, url: str, observed_rank: int,
                   title: str | None = None,
                   observed_at: str | None = None) -> dict:
    """Record ONE of the original observed Google results for the
    draft's keyword. Ranks 1..5 are unique among originals and are
    never overwritten (spec A2); entry is always `manual` in V1 —
    the user searched and enters what they saw. `observed_at`
    defaults to the profile's searched_at. The new source starts
    with read_status `pending`: having a URL says nothing about
    whether it was read."""
    try:
        with _draft_lock(draft_id, directory=_seo_profiles_dir()):
            profile, error = _seo_profile_or_error(draft_id)
            if error:
                return error
            source = _seo.add_source(profile, url, observed_rank,
                                     title=title,
                                     observed_at=observed_at)
            _save_seo_profile(profile)
            return source
    except ValueError as exc:
        return {"error": str(exc), "draft_id": draft_id}


@mcp.tool()
def record_source_read(draft_id: str, source_id: str,
                       read_status: str, reason: str = "",
                       content_truncated: bool = False,
                       page_type: str | None = None) -> dict:
    """Record the host model's explicit read outcome for one
    source: read / partial / failed / excluded, with the reason
    (a failure without a reason is a dead end for the user) and
    whether the content was truncated. Every recording appends to
    the source's read_events — a failed-then-read history is kept,
    not overwritten (spec A2). `page_type` (article / product /
    category / video / tool / other) is assigned here, at read
    time — the moment the model has actually seen the page;
    omitting it leaves a previously set value alone."""
    try:
        with _draft_lock(draft_id, directory=_seo_profiles_dir()):
            profile, error = _seo_profile_or_error(draft_id)
            if error:
                return error
            source = _seo.record_read(profile, source_id, read_status,
                                      reason=reason,
                                      content_truncated=content_truncated,
                                      page_type=page_type)
            _save_seo_profile(profile)
            return source
    except ValueError as exc:
        return {"error": str(exc), "draft_id": draft_id}


@mcp.tool()
def add_replacement_source(draft_id: str, replaces_source_id: str,
                           url: str, observed_rank: int, reason: str,
                           title: str | None = None,
                           observed_at: str | None = None) -> dict:
    """Record a replacement for a source that could not be used
    (unreadable, off-intent). The ORIGINAL record is never
    rewritten: the replacement is a new source linked via
    replaces_source_id, carrying the rank IT was observed at and
    the reason it exists (spec section 3.3) — it never pretends
    to be one of the original top 5."""
    try:
        with _draft_lock(draft_id, directory=_seo_profiles_dir()):
            profile, error = _seo_profile_or_error(draft_id)
            if error:
                return error
            source = _seo.add_replacement(
                profile, replaces_source_id, url, observed_rank,
                reason, title=title, observed_at=observed_at)
            _save_seo_profile(profile)
            return source
    except ValueError as exc:
        return {"error": str(exc), "draft_id": draft_id}


@mcp.tool()
def save_seo_analysis(draft_id: str, claims: list) -> dict:
    """Save the host model's analysis of the read sources as
    provenance-checked claims: [{text, kind, source_ids,
    confidence?}] where kind is `sourced` (must cite >= 1 recorded
    source) or `inference` (the model's own reading, labelled as
    such). A claim citing a source that is not read/partial is
    stored WITH a flag and reported in `flagged_claim_ids` — it is
    never silently treated as fully analysed (spec A4). Saving
    moves the profile to state `analyzing`; a re-save versions the
    analysis and keeps the previous one in analysis_history."""
    try:
        with _draft_lock(draft_id, directory=_seo_profiles_dir()):
            profile, error = _seo_profile_or_error(draft_id)
            if error:
                return error
            analysis, flagged = _seo.save_analysis(profile, claims)
            _save_seo_profile(profile)
            return {"draft_id": draft_id, "state": profile["state"],
                    "analysis_version": analysis["version"],
                    "claims": analysis["claims"],
                    "flagged_claim_ids": flagged}
    except ValueError as exc:
        return {"error": str(exc), "draft_id": draft_id}


@mcp.tool()
def record_shortfall_exception(draft_id: str, reason: str) -> dict:
    """Record the owner's deliberate decision to proceed with
    fewer than 5 read sources (spec 'Thiếu nguồn'): the reason is
    required and at least 1 source must have been read — with 0
    reads there is nothing to analyse and no exception can
    authorise proceeding (spec A3). The exception is stored with
    the read count at confirmation; the shortfall itself stays
    reported — the profile is never labelled as having analysed
    5 sources it did not read."""
    try:
        with _draft_lock(draft_id, directory=_seo_profiles_dir()):
            profile, error = _seo_profile_or_error(draft_id)
            if error:
                return error
            exception = _seo.record_exception(profile, reason)
            _save_seo_profile(profile)
            return {"draft_id": draft_id,
                    "shortfall_exception": exception,
                    "sufficiency": _seo.sufficiency(profile)}
    except ValueError as exc:
        return {"error": str(exc), "draft_id": draft_id}


# --------------------------------------------------------------------------
# Tools — SEO five-source, Round 2: direction + the user's decision,
# the sufficiency-gated versioned outline (linked to the draft via
# make_outline — the draft stays the writing artefact), and A6
# invalidation through reopen_seo_analysis, the only way back once
# approvals exist.
# --------------------------------------------------------------------------

@mcp.tool()
def save_seo_direction(draft_id: str, primary_intent: str,
                       secondary_intents: list | None = None,
                       rationale_claim_ids: list | None = None) -> dict:
    """Propose the article's direction from the saved analysis:
    ONE primary intent (when the top 5 serve competing intents,
    the choice is a decision, not an average — a proposal without
    a primary intent is refused, spec A5), optional secondary
    intents, and the analysis claim ids the reading rests on
    (each must resolve to a claim of the CURRENT analysis).
    Moves the profile analyzing → direction_review; the direction
    is only a proposal until the user decides."""
    try:
        with _draft_lock(draft_id, directory=_seo_profiles_dir()):
            profile, error = _seo_profile_or_error(draft_id)
            if error:
                return error
            direction = _seo.save_direction(
                profile, primary_intent,
                secondary_intents=secondary_intents,
                rationale_claim_ids=rationale_claim_ids)
            _save_seo_profile(profile)
            return {"draft_id": draft_id, "state": profile["state"],
                    "direction": direction}
    except ValueError as exc:
        return {"error": str(exc), "draft_id": draft_id}


@mcp.tool()
def record_direction_decision(draft_id: str, decision: str,
                              note: str = "") -> dict:
    """Record the USER's decision on the proposed direction:
    `approved` (direction_review → outline_review; the direction
    and its decision stay readable on the profile) or `rejected`
    (the direction moves to direction_history with its decision,
    and the profile returns to analyzing for a new proposal).
    The core never approves its own proposal (spec section 6.2)."""
    try:
        with _draft_lock(draft_id, directory=_seo_profiles_dir()):
            profile, error = _seo_profile_or_error(draft_id)
            if error:
                return error
            direction = _seo.decide_direction(profile, decision,
                                              note=note)
            _save_seo_profile(profile)
            return {"draft_id": draft_id, "state": profile["state"],
                    "direction": direction}
    except ValueError as exc:
        return {"error": str(exc), "draft_id": draft_id}


@mcp.tool()
def propose_seo_outline(draft_id: str, sections: list) -> dict:
    """Propose a new outline version for the draft: `sections` is
    the make_outline shape ([{heading, intent?, target_words?}]).
    GATE (spec A3): the direction must be approved AND
    sufficiency.proceed_ready must hold — 5 counted reads, or
    fewer under a recorded shortfall exception; the refusal names
    both ways forward. The sections go through make_outline into
    the DRAFT (the writing artefact — no second outline store);
    the profile records the version, mirroring the normalised
    sections so any version can be read back. A still-pending
    proposed version is superseded; an approved version stays
    approved-current until a newer one is approved."""
    try:
        with _draft_lock(draft_id, directory=_seo_profiles_dir()):
            profile, error = _seo_profile_or_error(draft_id)
            if error:
                return error
            if not isinstance(sections, list) or not sections:
                return {"error": "sections must be a non-empty list "
                                 "of {heading, intent?, "
                                 "target_words?} — an empty outline "
                                 "is not an outline",
                        "draft_id": draft_id}
            _seo.require_outline_proposable(profile)
            draft, derr = _load_draft_or_error(draft_id)
            if derr:
                return derr
            result = make_outline(draft.get("brief", {}),
                                  proposed_outline=sections,
                                  draft_id=draft_id)
            if not result.get("ok"):
                return {"error": result.get(
                    "error", "outline refused by make_outline"),
                    "draft_id": draft_id}
            record = _seo.record_outline_proposal(
                profile, result["outline"])
            _save_seo_profile(profile)
            return {"draft_id": draft_id, "state": profile["state"],
                    "version": record["version"], "outline": record}
    except ValueError as exc:
        return {"error": str(exc), "draft_id": draft_id}


@mcp.tool()
def approve_seo_outline(draft_id: str, version: int) -> dict:
    """Approve ONE proposed outline version — the user's act,
    recorded with its timestamp. The previously approved version
    (if any) becomes superseded: exactly one approved-current
    version exists at any time. A superseded or invalidated
    version can never be approved (it is history). Approval moves
    the profile to `writing` and stamps the version with the
    draft's current content fingerprint (gates.content_sha) —
    Round 3's export check compares the shipped content against
    it (spec A6)."""
    try:
        with _draft_lock(draft_id, directory=_seo_profiles_dir()):
            profile, error = _seo_profile_or_error(draft_id)
            if error:
                return error
            draft, derr = _load_draft_or_error(draft_id)
            if derr:
                return derr
            sha = _content_sha(_sections_of(draft), _draft_title(draft))
            record = _seo.approve_outline(profile, version, sha)
            _save_seo_profile(profile)
            return {"draft_id": draft_id, "state": profile["state"],
                    "outline": record}
    except ValueError as exc:
        return {"error": str(exc), "draft_id": draft_id}


@mcp.tool()
def reopen_seo_analysis(draft_id: str, reason: str) -> dict:
    """Reopen the analysis after approvals exist (spec A6) — the
    ONLY way to change sources / analysis once a direction or
    outline stands: direct mutation in those states stays refused.
    The current direction and every live outline version are
    marked INVALIDATED and kept whole in history with the reason
    and timestamp (never deleted, never silently mutated); the
    profile returns to `analyzing`, where inputs change normally
    again and the flow re-runs. A reason is required."""
    try:
        with _draft_lock(draft_id, directory=_seo_profiles_dir()):
            profile, error = _seo_profile_or_error(draft_id)
            if error:
                return error
            summary = _seo.reopen_analysis(profile, reason)
            _save_seo_profile(profile)
            return {"draft_id": draft_id, "state": profile["state"],
                    "invalidated": {
                        "outlines": summary["invalidated_outlines"],
                        "reviews": summary["invalidated_reviews"],
                        "direction": summary["direction_invalidated"]},
                    "from_state": summary["from_state"]}
    except ValueError as exc:
        return {"error": str(exc), "draft_id": draft_id}


# --------------------------------------------------------------------------
# Tools — SEO five-source, Round 3: review records bound to the
# approved outline version + the current content fingerprint, and
# the ONE export-readiness decision the exporter enforces. There
# is deliberately no stored `ready_to_export` state: readiness is
# computed from live bindings every time it is asked, so it can
# never go stale (specs/seo-five-source-round3-notes.md).
# --------------------------------------------------------------------------

def _seo_readiness(draft: dict, profile: dict) -> dict:
    """Assemble the readiness decision for one draft + profile:
    the F1 gate verdict comes from _export_gate_refusal itself
    (reused, never re-implemented), the fingerprint from the same
    helper the exporter and the gate share."""
    refusal = _export_gate_refusal(draft)
    current_sha = _content_sha(_sections_of(draft), _draft_title(draft))
    reasons = _seo.export_readiness_reasons(
        profile, current_sha,
        refusal["reason"] if refusal else None,
        refusal["error"] if refusal else "")
    return {"draft_id": draft.get("draft_id", ""),
            "state": profile.get("state"),
            "ready": not reasons, "reasons": reasons}


def _mark_seo_exported(draft_id: str, wp_post_id: int,
                       site_url: str) -> str | None:
    """After a successful export, stamp the draft's SEO profile
    `exported` (post id + site + time). Reloads under the SEO
    store's own lock so a concurrent profile change is never
    clobbered. Returns an error string when the stamp could not
    be written (the export itself already happened — the caller
    surfaces this as a warning, never as an export failure), or
    None when there is nothing to stamp / the stamp landed."""
    try:
        with _draft_lock(draft_id, directory=_seo_profiles_dir()):
            profile = _load_seo_profile(draft_id)
            if profile is None:
                return None
            if profile.get("state") == "exported" \
                    and (profile.get("export") or {}).get(
                        "wp_post_id") == int(wp_post_id):
                return None  # already stamped for this very post
            _seo.mark_exported(profile, wp_post_id, site_url)
            _save_seo_profile(profile)
    except ValueError as exc:
        return str(exc)
    return None


@mcp.tool()
def record_seo_review(draft_id: str, items: list, verdict: str,
                      expert_review: dict | None = None) -> dict:
    """Record ONE review of the written article (five-source
    Round 3). `items` is the checklist: [{key (or id), label,
    status: ok|issue|na, mandatory: bool, note?}] — keys unique.
    `verdict` is the review's decision: approved |
    changes_requested. `expert_review` (optional) covers
    specialist claims: {required: bool, status:
    not_required|pending|done, reviewer?} — a required review is
    pending until done, and `done` MUST name the reviewer (who +
    when are recorded; automated scores never substitute for a
    qualified reviewer, spec section 6.5).

    The review BINDS to the current approved outline version and
    to the draft's content fingerprint right now: edit the article
    or approve a newer outline afterwards and the review is stale
    — computably, via get_seo_profile's review_status — and export
    readiness falls until a fresh review is recorded. The first
    review moves the profile writing → reviewing."""
    try:
        with _draft_lock(draft_id, directory=_seo_profiles_dir()):
            profile, error = _seo_profile_or_error(draft_id)
            if error:
                return error
            draft, derr = _load_draft_or_error(draft_id)
            if derr:
                return derr
            sha = _content_sha(_sections_of(draft), _draft_title(draft))
            record = _seo.record_review(profile, items, verdict,
                                        expert_review, sha)
            _save_seo_profile(profile)
            return {"draft_id": draft_id, "state": profile["state"],
                    "review": record,
                    "review_status": _seo.review_status(profile, sha)}
    except ValueError as exc:
        return {"error": str(exc), "draft_id": draft_id}


@mcp.tool()
def get_seo_export_readiness(draft_id: str) -> dict:
    """THE export-readiness decision for a draft WITH an SEO
    profile (spec section 6.6) — the same decision
    export_draft_to_wordpress enforces. Returns {ready, reasons}:
    every unmet condition as a named reason {code, detail} —
    state_not_exportable / no_approved_outline /
    edited_after_outline_approval / no_review_recorded /
    review_invalidated / review_stale_outline /
    review_stale_content / open_mandatory_items (with the item
    keys) / expert_review_pending / review_changes_requested /
    the F1 gate codes (never_ran_gate / gate_failed /
    edited_after_pass / pass_unverifiable). An empty reasons list
    means ready. Drafts WITHOUT a profile get a structured error:
    legacy drafts are governed by the exporter's F1 gate rule
    alone, and this tool does not pretend otherwise."""
    profile, error = _seo_profile_or_error(draft_id)
    if error:
        return error
    draft, derr = _load_draft_or_error(draft_id)
    if derr:
        return derr
    return _seo_readiness(draft, profile)


# --------------------------------------------------------------------------
# Tools — corpus registry (system-level) + brand corpus choices
# --------------------------------------------------------------------------

@mcp.tool()
def create_corpus_profile(name: str, samples: list[str]) -> dict:
    """Build + persist a Style Profile for a NEW corpus from pasted
    sample texts — the extraction half of corpus registration (the
    admin's "create corpus" flow, app gap #8).

    COPYRIGHT RULE (hard): a corpus profile holds statistics +
    distilled traits ONLY, so excerpts are NEVER stored on this path
    — store_excerpts is forced False server-side and is not a
    parameter a caller can flip. (The taste layer is the only
    store_excerpts=True path in the system, for the owner's own
    articles.)

    Requires at least 3 DISTINCT non-empty samples: duplicates
    collapse to one sample of evidence (analyze_samples' rule), so
    pasting the same text three times is refused, not triple-counted.
    Returns {profile_id, profile, word_count, sample_count} — the
    counts are MEASURED from the samples, ready to hand to
    register_corpus verbatim."""
    usable = [s for s in samples if s and s.strip()]
    analysis = _analyze_samples(usable)
    if analysis["sample_count"] < 3:
        return {"error": f"a corpus profile needs at least 3 distinct "
                         f"non-empty samples, got "
                         f"{analysis['sample_count']} — samples that "
                         f"repeat each other count once"}
    try:
        profile = _build_profile(name, usable, qualitative=None,
                                 store_excerpts=False, brand_id="")
        saved = _save_style_profile_file(profile)
    except ValueError as exc:  # e.g. an unusable profile name
        return {"error": str(exc)}
    return {"profile_id": saved["id"], "profile": saved,
            "word_count": analysis["fingerprint"]["word_count"],
            "sample_count": analysis["sample_count"]}


@mcp.tool()
def register_corpus(corpus_id: str, version: int, title: str, kind: str,
                    profile_id: str, legitimacy_basis: str,
                    word_count: int, sample_count: int = 0) -> dict:
    """Register a genre corpus ONCE at system level (Smith's
    decision): id, version, title, kind (press / literature / genre /
    other), the style profile it feeds, the legitimacy basis for
    using it (a corpus without a recorded basis cannot register),
    and its measured size. Registering an id that already exists is
    an error — a corpus revision is a new version, decided
    deliberately, never a silent overwrite."""
    if _load_corpus_entry(corpus_id) is not None:
        return {"error": f"corpus already registered: {corpus_id} — "
                         f"a revision is a new corpus id or an explicit "
                         f"re-registration decision, not an overwrite"}
    entry = {"id": corpus_id, "version": version, "title": title,
             "kind": kind, "profile_id": profile_id,
             "legitimacy_basis": legitimacy_basis,
             "word_count": word_count, "sample_count": sample_count,
             "status": "active",
             "registered_at": datetime.now(timezone.utc).isoformat(
                 timespec="seconds")}
    try:
        return _save_corpus_entry(entry)
    except ValueError as exc:
        return {"error": str(exc)}


@mcp.tool()
def list_corpora() -> dict:
    """List every registered system corpus (full registry records:
    id, version, kind, profile_id, legitimacy basis, word count)."""
    return {"corpora": _list_corpus_entries(),
            "count": len(_list_corpus_entries())}


@mcp.tool()
def get_corpus(corpus_id: str) -> dict:
    """Return one registered corpus entry by id."""
    try:
        entry = _load_corpus_entry(corpus_id)
    except ValueError as exc:
        return {"error": str(exc)}
    if entry is None:
        return {"error": f"corpus not registered: {corpus_id}",
                "available": [c["id"] for c in _list_corpus_entries()
                              if not c.get("corrupt")]}
    return entry


@mcp.tool()
def set_corpus_choice(brand_id: str, corpus_id: str) -> dict:
    """Record a brand's corpus choice directly (the same choice a
    research pack's corpus_choice item makes at apply): the brand
    attaches the registered corpus at its CURRENT version, and
    assemble_context will default the brand's style profile from it
    when a brief names the brand without an explicit style profile."""
    entry = _load_corpus_entry(corpus_id)
    if entry is None:
        return {"error": f"corpus not registered: {corpus_id}",
                "brand_id": brand_id}
    try:
        return _save_corpus_choice(brand_id, {
            "corpus_id": entry["id"], "version": entry["version"],
            "profile_id": entry["profile_id"], "source": "direct"})
    except ValueError as exc:
        return {"error": str(exc), "brand_id": brand_id}


@mcp.tool()
def get_corpus_choice(brand_id: str) -> dict:
    """Return a brand's recorded corpus choice (corpus_id, version,
    resolved profile_id, source), or an error when the brand has not
    chosen a corpus."""
    try:
        choice = _load_corpus_choice(brand_id)
    except ValueError as exc:
        return {"error": str(exc), "brand_id": brand_id}
    if choice is None:
        return {"error": f"brand {brand_id} has no corpus choice",
                "brand_id": brand_id}
    return choice


@mcp.tool()
def clear_corpus_choice(brand_id: str) -> dict:
    """Remove a brand's recorded corpus choice entirely — BOTH the
    choice store record and the stamp a research-pack apply left on
    the brand draft (get_corpus_choice honours either, so both must
    go). After clearing, get_corpus_choice reports no choice and
    assemble_context no longer defaults the brand's style profile
    from a corpus; an explicit style_profile_id in a brief is
    unaffected. Clearing a brand with no choice is a clean no-op
    ({cleared: false, ...}), not an error. Returns {brand_id,
    cleared, store_cleared, draft_stamp_cleared}."""
    try:
        return _delete_corpus_choice(brand_id)
    except ValueError as exc:
        return {"error": str(exc), "brand_id": brand_id}


# --------------------------------------------------------------------------
# Tools — taste layer (learning loop, core side)
# --------------------------------------------------------------------------

@mcp.tool()
def import_taste_articles(brand_id: str, file_refs: list[str]) -> dict:
    """Import finished article FILES into a brand's taste store as
    PENDING (unmarked) entries. Pending is a candidate state, not an
    approval: imported articles never feed a taste profile until the
    owner marks them with mark_article_verdict. Re-importing the
    same file is a no-op."""
    articles = []
    for ref in file_refs:
        path = Path(ref)
        if not path.exists():
            return {"error": f"article file not found: {ref}",
                    "brand_id": brand_id}
        articles.append({"ref": ref, "source": "file",
                         "text": path.read_text(encoding="utf-8")})
    try:
        return _taste.import_articles(brand_id, articles)
    except ValueError as exc:
        return {"error": str(exc), "brand_id": brand_id}


@mcp.tool()
def mark_article_verdict(brand_id: str, verdict: str, draft_id: str = "",
                         file_ref: str = "", text: str = "",
                         edited_text: str = "", note: str = "") -> dict:
    """Record the OWNER's verdict on one finished article:
    verdict = approved_unchanged / edited. The article is identified
    by draft_id (its stored sections are snapshotted) or file_ref
    (the file's content is snapshotted) or raw `text`. An "edited"
    verdict REQUIRES edited_text: both versions are kept — the
    owner's edited version is what the taste profile learns from,
    the original stays as the other half of the diff. Only the owner
    gives verdicts; nothing is ever marked on the owner's behalf."""
    ref = draft_id or file_ref
    source = "draft" if draft_id else "file"
    if draft_id:
        draft, error = _load_draft_or_error(draft_id)
        if error:
            return error
        text = "\n\n".join(
            (f"## {s.get('heading', '')}\n\n{s.get('text', '')}"
             if s.get("heading") else s.get("text", ""))
            for s in _sections_of(draft))
    elif file_ref:
        path = Path(file_ref)
        if not path.exists():
            return {"error": f"article file not found: {file_ref}",
                    "brand_id": brand_id}
        text = path.read_text(encoding="utf-8")
    if not ref:
        return {"error": "identify the article with draft_id or file_ref",
                "brand_id": brand_id}
    try:
        entry = _taste.mark_verdict(
            brand_id, verdict, ref=ref, text=text,
            edited_text=edited_text or None, source=source, note=note)
    except ValueError as exc:
        return {"error": str(exc), "brand_id": brand_id}
    return {"brand_id": brand_id, "entry_id": entry["id"], "ref": ref,
            "verdict": entry["verdict"], "marked_at": entry["marked_at"]}


@mcp.tool()
def build_taste_profile(brand_id: str) -> dict:
    """Distil a brand's verdicted articles into its taste style
    profile (`<brand-id>-taste-voice`), attachable via
    assemble_context like any style profile. Needs at least 3
    approved/edited articles; pending ones never count.
    store_excerpts=True is legitimate HERE and only here: the
    samples are the owner's own finished articles, not a third-party
    corpus (research-pack style items still force False)."""
    try:
        return _taste.build_taste_profile(brand_id)
    except ValueError as exc:
        return {"error": str(exc), "brand_id": brand_id}


@mcp.tool()
def get_taste_status(brand_id: str) -> dict:
    """A brand's taste-layer status: verdict counts (pending /
    approved_unchanged / edited) and whether a taste profile exists
    and how many articles it was built from."""
    try:
        return _taste.taste_status(brand_id)
    except ValueError as exc:
        return {"error": str(exc), "brand_id": brand_id}


@mcp.tool()
def list_taste_entries(brand_id: str) -> dict:
    """List a brand's taste entries — one summary row per article
    (id, ref, source, verdict, imported/marked stamps, word count;
    the article text itself is not returned). The listing
    counterpart to get_taste_status's counts: it is how pending
    articles are found again for marking. An entry too broken to
    summarise is listed as corrupt with its index, never skipped;
    a brand with no taste store lists empty."""
    try:
        return _taste.list_entries(brand_id)
    except ValueError as exc:
        return {"error": str(exc), "brand_id": brand_id}


# --------------------------------------------------------------------------
# Tools — local data backup
# --------------------------------------------------------------------------

@mcp.tool()
def export_core_data() -> dict:
    """Snapshot ALL local stores (personas, brand files/drafts, facts,
    style/quality profiles, image libraries, research packs, SEO
    profiles, corpus registry + choices, taste stores, drafts) into
    ONE timestamped
    zip in the backups dir, with a manifest (per-store counts,
    per-file sha256). Files matching credential patterns are EXCLUDED
    and recorded in the manifest — the stores hold no credentials by
    design, and this keeps it that way even if one is planted."""
    return _backup.export_core_data()


@mcp.tool()
def list_backups() -> dict:
    """List the backup archives (newest first) with each archive's
    manifest summary: created_at, total files/bytes. An archive whose
    manifest cannot be read is listed as corrupt, never skipped."""
    backups = _backup.list_backups()
    return {"backups": backups, "count": len(backups)}


@mcp.tool()
def restore_core_data(archive_name: str, confirm: bool = False,
                      dry_run: bool = False) -> dict:
    """Restore files from a backup archive (see list_backups) into
    the live stores. Per archived file: missing -> restored;
    identical -> skipped; differing -> restored only with
    confirm=True, otherwise skipped and reported. dry_run=True
    returns the full plan and writes NOTHING. Before any real write
    the current live stores are snapshotted as a pre-restore-*
    archive, so a restore is itself reversible. The archive is
    validated first: any traversal/absolute entry path refuses the
    whole restore; a credential-pattern entry, a sha256 mismatch
    against the manifest, or unparseable JSON is refused per file
    and named in the report — never landed."""
    try:
        return _backup.restore_core_data(archive_name, confirm=confirm,
                                         dry_run=dry_run)
    except ValueError as exc:
        return {"error": str(exc), "archive": archive_name}


# --------------------------------------------------------------------------
# Tools — WordPress draft exporter (P3). Delivery decision
# (pmdecisions 2026-11-22): finished articles are delivered as
# DRAFTS on the client's own WordPress site — never published; the
# client reviews and publishes. The target store holds only
# non-secret config (site URL, username, post type, category); the
# Application Password is read at export time from the local
# secrets file / environment (content_core/wordpress.py) and never
# stored, logged or returned. The created post's status is forced
# to "draft" in wordpress.build_post_payload — there is no publish
# path. All logic lives in content_core/wordpress.py + loaders;
# these tools orchestrate draft state, in the {"error": ...}
# dialect.
# --------------------------------------------------------------------------

@mcp.tool()
def set_wordpress_target(brand_id: str, site_url: str, username: str,
                         post_type: str = "posts",
                         default_category_id: int | None = None) -> dict:
    """Record a brand's WordPress delivery target — NON-SECRET
    config only: the site's base URL, the WP username the
    Application Password belongs to, the REST post type (default
    "posts") and an optional default category id. The Application
    Password itself is NEVER accepted or stored here: configure it
    in the local secrets file or the BRANDFORGE_WP_PASSWORD_<BRAND>
    environment variable (see content_core/wordpress.py). Returns
    the stored target plus `credentials_configured` (a boolean —
    whether a password is available for this brand)."""
    try:
        record = _save_wp_target({
            "brand_id": brand_id, "site_url": site_url,
            "username": username, "post_type": post_type,
            "default_category_id": default_category_id})
    except ValueError as exc:
        return {"error": str(exc), "brand_id": brand_id}
    return {**record,
            "credentials_configured":
                _wordpress.credentials_configured(brand_id)}


@mcp.tool()
def get_wordpress_target(brand_id: str) -> dict:
    """Return a brand's WordPress target (non-secret config) plus
    `credentials_configured`. An unconfigured brand is a structured
    error. The Application Password is never returned — it is not
    in this store and this tool never reads it."""
    try:
        target = _load_wp_target(brand_id)
    except ValueError as exc:
        return {"error": str(exc), "brand_id": brand_id}
    if target is None:
        return {"error": f"no WordPress target configured for brand "
                         f"{brand_id} — set one with "
                         f"set_wordpress_target",
                "brand_id": brand_id}
    return {**target,
            "credentials_configured":
                _wordpress.credentials_configured(brand_id)}


def _export_attempt_slug(draft_id: str, attempt_id: str) -> str:
    """The deterministic WordPress slug marking one export attempt
    (fix round F2): "bf-<draft>-<attempt prefix>". The slug is the
    reconciliation marker — stock WP REST creates with an explicit
    slug and queries by it, so no site-side registration is needed.
    Stored on the draft's attempt record, so every retry of the
    SAME attempt looks for the SAME slug."""
    import re as _re
    base = _re.sub(r"[^a-z0-9]+", "-", str(draft_id).lower()
                   ).strip("-")[:60] or "draft"
    return f"bf-{base}-{str(attempt_id)[:8]}"


def _export_gate_refusal(draft: dict) -> dict | None:
    """Fix round F1: the exporter's gate eligibility — a draft may
    ship to WordPress ONLY when its CURRENT content carries a
    recorded final_gate PASS. Returns {"error", "reason"} or None.

    The proof is the gate entry's content fingerprint
    (gates.content_sha, recorded by both gate paths since F1): the
    LATEST gate entry must be a pass AND its fingerprint must equal
    the fingerprint of the content as it stands now. Reasons:
    - never_ran_gate: no gate_history at all;
    - gate_failed: the latest gate run failed;
    - edited_after_pass: the latest run passed, but the content has
      changed since (fingerprint mismatch);
    - pass_unverifiable: the latest pass predates fingerprinting
      (a legacy entry with no content_sha). Deliberately NOT
      honoured: a workflow status flag cannot prove the text never
      changed — the fingerprint is the only content-bound evidence,
      so a legacy draft simply re-runs final_gate once.
    """
    draft_id = draft.get("draft_id", "")
    history = draft.get("gate_history") or []
    if not history:
        return {"error": f"draft {draft_id} has never run final_gate "
                         f"— run final_gate and fix any failures "
                         f"before exporting to WordPress",
                "reason": "never_ran_gate"}
    latest = history[-1]
    if not latest.get("passed"):
        return {"error": f"draft {draft_id}'s latest final_gate run "
                         f"FAILED — fix the failing sections and "
                         f"re-run final_gate before exporting to "
                         f"WordPress",
                "reason": "gate_failed"}
    recorded = latest.get("content_sha")
    if not recorded:
        return {"error": f"draft {draft_id}'s recorded final_gate "
                         f"pass predates content fingerprinting and "
                         f"cannot be verified against the current "
                         f"text — re-run final_gate before exporting "
                         f"to WordPress",
                "reason": "pass_unverifiable"}
    current = _content_sha(_sections_of(draft), _draft_title(draft))
    if recorded != current:
        return {"error": f"draft {draft_id} was edited after its "
                         f"recorded final_gate pass — re-run "
                         f"final_gate before exporting to WordPress",
                "reason": "edited_after_pass"}
    return None


@mcp.tool()
def export_draft_to_wordpress(brand_id: str, draft_id: str,
                              force: bool = False) -> dict:
    """Export a stored draft to the brand's WordPress site as a
    DRAFT post (status is always "draft" — this tool cannot
    publish): uploads the draft's images per its media manifest
    (the featured image becomes the post's featured_media, alt text
    / caption / description are set on each media item), builds the
    post body from the draft's sections with in-content images
    placed at their sections, creates the post, and records the
    export on the draft (wordpress_export: site, post id, edit
    link, media map).

    Duplicate rule: a draft that already carries a wordpress_export
    record is REFUSED — pass force=True to deliberately create a
    fresh post and replace the record. A draft with no image plan
    exports text-only; unfulfilled plan slots are reported as a
    warning, never silently dropped. Errors are structured and
    credential-free: WP failures name the HTTP status and WP's
    message with the password scrubbed out.

    GATE RULE (fix round F1): the draft ships ONLY when its current
    content carries a recorded final_gate PASS — the latest gate
    entry must be a pass whose content fingerprint (content_sha)
    matches the content as it stands now. A draft that never ran
    the gate, whose latest run failed, that was edited after its
    pass, or whose pass predates fingerprinting is REFUSED with a
    structured `reason` (never_ran_gate / gate_failed /
    edited_after_pass / pass_unverifiable) BEFORE any WordPress
    write is made.

    SEO RULE (five-source Round 3): when the draft HAS an SEO
    profile, export additionally requires the core readiness
    decision (get_seo_export_readiness) to be READY for the
    current content — approved outline standing with its
    fingerprint intact, a current review with no open mandatory
    items and expert review satisfied, on top of the gate rule
    below. Refusal is structured: reason `seo_not_ready` plus the
    named `reasons` list, before any WordPress write. A successful
    SEO export stamps the profile `exported` (including when an
    earlier attempt's post is adopted under the idempotency rule).

    IDEMPOTENCY (fix round F2): before creating anything, the
    export records an attempt on the draft (attempt id + a
    deterministic slug marker, "bf-<draft>-<attempt>") and creates
    the post WITH that slug. If an earlier attempt's outcome was
    lost (post created, response/record never landed), the next
    call first searches the site for the open attempt's slug and
    ADOPTS that post (returned with adopted_existing_post=True)
    instead of creating a duplicate.

    WP-REWRITE (specs/wp-rewrite-feature.md §3.6): a draft imported
    from WordPress (source.kind == "wp_import") ships ONLY under a
    standing `rewrite` verdict (set_rewrite_verdict) — audit_only /
    drop / no verdict and superseded imports are refused before any
    write. The created post is a NEW draft titled with the
    "[Viết lại] " prefix, categories inherited from the original
    (tags are not), the original's own images reused by media id
    (never re-uploaded), and the export record carries
    `rewrite_of` {wp_post_id, wp_url, site_url}. No request in this
    flow ever addresses the original post's id."""
    try:
        target = _load_wp_target(brand_id)
    except ValueError as exc:
        return {"error": str(exc), "brand_id": brand_id,
                "draft_id": draft_id}
    if target is None:
        return {"error": f"no WordPress target configured for brand "
                         f"{brand_id} — set one with "
                         f"set_wordpress_target",
                "brand_id": brand_id, "draft_id": draft_id}
    try:
        with _draft_lock(draft_id):
            draft, error = _load_draft_or_error(draft_id)
            if error:
                return error
            draft_brand = draft.get("brief", {}).get("brand_id") \
                or "moc-brew"
            if draft_brand != brand_id:
                return {"error": f"draft {draft_id} belongs to brand "
                                 f"{draft_brand}, not {brand_id}",
                        "brand_id": brand_id, "draft_id": draft_id}
            if draft.get("wordpress_export") and not force:
                prev = draft["wordpress_export"]
                return {"error": f"draft {draft_id} was already "
                                 f"exported to {prev.get('site_url')} "
                                 f"as WP post {prev.get('wp_post_id')} "
                                 f"— pass force=True to export again",
                        "brand_id": brand_id, "draft_id": draft_id}
            # WP-rewrite branch (specs/wp-rewrite-feature.md §3.6):
            # a superseded import is locked out entirely, and an
            # imported draft ships ONLY under a standing `rewrite`
            # verdict — audit_only / drop / no verdict are refusals
            # with the verdict named, before any WordPress write.
            draft_source = draft.get("source")
            is_rewrite = isinstance(draft_source, dict) \
                and draft_source.get("kind") == "wp_import"
            if draft.get("status") == "superseded":
                return {"error": f"draft {draft_id} was superseded "
                                 f"by a newer import "
                                 f"({draft.get('superseded_by')}) — "
                                 f"export the current import instead",
                        "reason": "superseded",
                        "brand_id": brand_id, "draft_id": draft_id}
            if is_rewrite:
                standing = draft.get("verdict")
                standing = standing.get("verdict") \
                    if isinstance(standing, dict) else None
                if standing != "rewrite":
                    return {"error": f"draft {draft_id} is a "
                                     f"WordPress import with verdict "
                                     f"{standing!r} — only a "
                                     f"'rewrite' verdict may export "
                                     f"(record one with "
                                     f"set_rewrite_verdict)",
                            "reason": "verdict_not_rewrite",
                            "brand_id": brand_id,
                            "draft_id": draft_id}
            password = _wordpress.load_app_password(brand_id)
            if password is None:
                return {"error": _wordpress.not_configured_message(
                    brand_id), "brand_id": brand_id,
                    "draft_id": draft_id}

            # SEO five-source, Round 3: if this draft HAS an SEO
            # profile, its export is governed by the readiness
            # decision below. A corrupt profile refuses the export
            # (the ValueError lands in the structured handler) —
            # an unreadable workflow record is never shipped past.
            # Drafts WITHOUT a profile skip everything SEO here
            # and keep the F1/F2 behaviour byte-identically.
            with _draft_lock(draft_id, directory=_seo_profiles_dir()):
                seo_profile = _load_seo_profile(draft_id)

            # F2: idempotency reconciliation. An OPEN attempt from
            # an earlier call whose outcome was lost (the post was
            # created but the response/record never landed) is
            # resolved first: search the site for the attempt's
            # slug and ADOPT the post instead of creating a second
            # one. This runs before the gate check on purpose: an
            # attempt could only be recorded by a call that already
            # passed the gate, and the adopted post embodies that
            # gated content — nothing new is shipped by adopting.
            attempt = draft.get("wordpress_export_attempt")
            if not isinstance(attempt, dict):
                attempt = None
            if attempt and attempt.get("slug") and \
                    attempt.get("site_url") == target["site_url"]:
                found = _wordpress.find_post_by_slug(
                    target, password, str(attempt["slug"]))
                if found is not None:
                    post_id = int(found["id"])
                    record = {"brand_id": brand_id,
                              "site_url": target["site_url"],
                              "wp_post_id": post_id,
                              "edit_link": _wordpress.edit_link(
                                  target["site_url"], post_id),
                              "post_link": found.get("link", ""),
                              "status": found.get("status") or "draft",
                              "exported_at": datetime.now(
                                  timezone.utc).isoformat(
                                      timespec="seconds"),
                              "slug": attempt["slug"],
                              "attempt_id": attempt.get("attempt_id"),
                              "adopted_existing_post": True,
                              "media": []}
                    if is_rewrite:
                        record["rewrite_of"] = {
                            "wp_post_id": draft_source.get(
                                "wp_post_id"),
                            "wp_url": draft_source.get("wp_url", ""),
                            "site_url": target["site_url"]}
                    draft["wordpress_export"] = record
                    draft.pop("wordpress_export_attempt", None)
                    _store_draft(draft)
                    adopt_warnings = [
                        "an earlier export attempt had "
                        "already created this post on "
                        "WordPress — adopted it instead of "
                        "creating a duplicate; its media "
                        "were uploaded by that attempt"]
                    if seo_profile is not None:
                        # The adoption COMPLETES an SEO export:
                        # stamp the profile exported for this post.
                        stamp_error = _mark_seo_exported(
                            draft_id, post_id, target["site_url"])
                        if stamp_error:
                            adopt_warnings.append(
                                "the SEO profile could not be "
                                "marked exported: " + stamp_error)
                    return {"brand_id": brand_id,
                            "draft_id": draft_id,
                            "wp_post_id": post_id,
                            "edit_link": record["edit_link"],
                            "post_link": record["post_link"],
                            "status": record["status"],
                            "site_url": target["site_url"],
                            "media_uploaded": 0, "media": [],
                            "adopted_existing_post": True,
                            "warnings": adopt_warnings}

            # SEO readiness (Round 3) — before any WordPress
            # WRITE, and before the attempt is recorded: an SEO
            # draft ships only when the core confirms EVERY
            # condition for the CURRENT content (approved outline
            # standing + fingerprint match, a current review with
            # no open mandatory items and expert review satisfied,
            # and the F1 gate — whose check is part of the
            # readiness decision itself, reused not duplicated).
            if seo_profile is not None:
                readiness = _seo_readiness(draft, seo_profile)
                if not readiness["ready"]:
                    codes = ", ".join(r["code"]
                                        for r in readiness["reasons"])
                    return {"error":
                            f"draft {draft_id} is not ready to "
                            f"export under the SEO five-source "
                            f"workflow: {codes} — see `reasons` "
                            f"(get_seo_export_readiness reports "
                            f"the same decision)",
                            "reason": "seo_not_ready",
                            "reasons": readiness["reasons"],
                            "brand_id": brand_id,
                            "draft_id": draft_id}

            # F1: gate eligibility — before any WordPress WRITE.
            refusal = _export_gate_refusal(draft)
            if refusal is not None:
                return {**refusal, "brand_id": brand_id,
                        "draft_id": draft_id}

            # F2: record the attempt BEFORE uploading or creating
            # anything, so any failure from here leaves a
            # reconcilable trace. A retry whose earlier attempt
            # never created a post reuses the SAME attempt + slug;
            # force (a deliberate fresh post) mints a fresh attempt.
            if force or attempt is None:
                attempt_id = uuid.uuid4().hex
                attempt = {
                    "attempt_id": attempt_id,
                    "slug": _export_attempt_slug(draft_id, attempt_id),
                    "site_url": target["site_url"],
                    "started_at": datetime.now(
                        timezone.utc).isoformat(timespec="seconds")}
                draft["wordpress_export_attempt"] = attempt
                _store_draft(draft)

            state = _draft_images_state(draft)
            manifest = (_build_image_manifest(state["plan"],
                                              state["images"])
                        if state["plan"] else [])
            warnings: list[str] = []
            if state["plan"] and len(manifest) < len(state["plan"]):
                warnings.append(
                    f"{len(state['plan']) - len(manifest)} image "
                    f"slot(s) unfulfilled — exported without them")

            uploaded: list[dict] = []
            media_by_section: dict[str, dict] = {}
            featured_media_id: int | None = None
            media_uploaded_count = 0
            for entry in manifest:
                if entry.get("wp_media_id"):
                    # An imported post's own image (wp-rewrite
                    # §3.3): it already lives on THIS WordPress —
                    # reuse the media id as-is, never re-upload.
                    media_id = int(entry["wp_media_id"])
                    record = {"filename": entry.get("filename", ""),
                              "role": entry.get("role", ""),
                              "section_id": entry.get("section_id"),
                              "wp_media_id": media_id,
                              "source_url": entry.get("ref", ""),
                              "existing_wp": True}
                    uploaded.append(record)
                    if entry.get("role") == "featured":
                        featured_media_id = media_id
                    elif entry.get("section_id"):
                        media_by_section[str(entry["section_id"])] = {
                            **entry,
                            "source_url": entry.get("ref", "")}
                    continue
                ref = str(entry.get("ref") or "")
                if ref.startswith(("http://", "https://")):
                    return {"error": f"image ref is a URL, not a "
                                     f"local file — cannot upload: "
                                     f"{ref}",
                            "brand_id": brand_id, "draft_id": draft_id}
                path = Path(ref)
                if not path.is_file():
                    return {"error": f"image file not found for "
                                     f"manifest entry "
                                     f"{entry.get('filename')}: {ref}",
                            "brand_id": brand_id, "draft_id": draft_id}
                media = _wordpress.upload_media(
                    target, password,
                    entry.get("filename") or path.name,
                    path.read_bytes())
                media_uploaded_count += 1
                media_id = int(media["id"])
                fields = {"alt_text": entry.get("alt_text", ""),
                          "caption": entry.get("caption", ""),
                          "description": entry.get("description", "")}
                if any(fields.values()):
                    _wordpress.update_media(target, password,
                                            media_id, fields)
                record = {"filename": entry.get("filename", ""),
                          "role": entry.get("role", ""),
                          "section_id": entry.get("section_id"),
                          "wp_media_id": media_id,
                          "source_url": media.get("source_url", "")}
                uploaded.append(record)
                if entry.get("role") == "featured":
                    featured_media_id = media_id
                elif entry.get("section_id"):
                    media_by_section[str(entry["section_id"])] = {
                        **entry, "source_url": media.get(
                            "source_url", "")}

            content = _wordpress.build_content_html(
                _sections_of(draft), media_by_section)
            export_title = _draft_title(draft)
            category_id = target.get("default_category_id")
            if is_rewrite:
                # Linked-draft shape (wp-rewrite §3.6 + §12b): the
                # prefix pairs the rewrite with its original in
                # wp-admin; categories are inherited from the
                # original (tags are NOT — §12b decision 2); the
                # create still targets a NEW post — no request in
                # this flow ever addresses the original's id.
                if not export_title.startswith(
                        _wp_convert.REWRITE_TITLE_PREFIX):
                    export_title = (_wp_convert.REWRITE_TITLE_PREFIX
                                    + export_title)
                orig_categories = (draft.get("original") or {}).get(
                    "categories") or []
                if orig_categories:
                    category_id = orig_categories[0]
                orig_featured = (draft.get("original") or {}).get(
                    "featured_media")
                if orig_featured and featured_media_id is None:
                    warnings.append(
                        f"the original's featured media "
                        f"{orig_featured} was not reused (not in "
                        f"the draft's image state) — the rewrite "
                        f"draft carries no featured image")
            payload = _wordpress.build_post_payload(
                title=export_title, content_html=content,
                featured_media=featured_media_id,
                category_id=category_id,
                slug=attempt["slug"])
            post = _wordpress.create_post(target, password, payload)
            post_id = int(post["id"])
            record = {"brand_id": brand_id,
                      "site_url": target["site_url"],
                      "wp_post_id": post_id,
                      "edit_link": _wordpress.edit_link(
                          target["site_url"], post_id),
                      "post_link": post.get("link", ""),
                      "status": "draft",
                      "exported_at": datetime.now(
                          timezone.utc).isoformat(timespec="seconds"),
                      "slug": attempt["slug"],
                      "attempt_id": attempt["attempt_id"],
                      "media": uploaded}
            if is_rewrite:
                record["rewrite_of"] = {
                    "wp_post_id": draft_source.get("wp_post_id"),
                    "wp_url": draft_source.get("wp_url", ""),
                    "site_url": target["site_url"]}
            draft["wordpress_export"] = record
            draft.pop("wordpress_export_attempt", None)
            _store_draft(draft)
            if seo_profile is not None:
                stamp_error = _mark_seo_exported(
                    draft_id, post_id, target["site_url"])
                if stamp_error:
                    warnings.append(
                        "the SEO profile could not be marked "
                        "exported: " + stamp_error)
            result = {"brand_id": brand_id, "draft_id": draft_id,
                      "wp_post_id": post_id,
                      "edit_link": record["edit_link"],
                      "post_link": record["post_link"],
                      "status": "draft",
                      "site_url": target["site_url"],
                      "media_uploaded": media_uploaded_count,
                      "media": uploaded, "warnings": warnings}
            if is_rewrite:
                # The parts of the original the rewrite does NOT
                # restore on its own (spec §3.6): the owner adds
                # them by hand in wp-admin if they matter.
                original = draft.get("original") or {}
                result["rewrite_of"] = record["rewrite_of"]
                result["unrecovered_raw_blocks"] = \
                    original.get("raw_blocks", [])
                result["unrecovered_links"] = \
                    original.get("links", [])
            return result
    except _wordpress.WPError as exc:
        return {"error": str(exc), "brand_id": brand_id,
                "draft_id": draft_id}
    except ValueError as exc:  # lock timeout / invalid draft id
        return {"error": str(exc), "brand_id": brand_id,
                "draft_id": draft_id}


def _sync_one_wp_edit(brand_id: str, draft: dict) -> dict:
    """One draft's edit sync (see sync_wordpress_edits): GET the
    exported post, diff its current blocks against the draft's,
    and record an edited taste pair when they differ. Never writes
    to WordPress; WP/credential failures are per-draft errors, not
    exceptions, so one bad post cannot sink a brand-wide sync."""
    record = draft["wordpress_export"]
    base = {"draft_id": draft.get("draft_id", ""),
            "site_url": record.get("site_url", "")}
    try:
        post_id = int(record["wp_post_id"])
    except (KeyError, TypeError, ValueError):
        # A corrupt or hand-edited export record (review round 3,
        # S1): the failure is THIS draft's structured error, never
        # an exception that sinks the brand-wide sync.
        return {**base, "wp_post_id": record.get("wp_post_id"),
                "error": f"malformed wordpress_export record: "
                         f"wp_post_id {record.get('wp_post_id')!r} "
                         f"is not an integer"}
    base["wp_post_id"] = post_id
    try:
        target = _load_wp_target(brand_id)
    except ValueError as exc:
        return {**base, "error": str(exc)}
    if target is None:
        return {**base, "error": f"no WordPress target configured "
                                 f"for brand {brand_id}"}
    password = _wordpress.load_app_password(brand_id)
    if password is None:
        return {**base, "error": _wordpress.not_configured_message(
            brand_id)}
    try:
        post = _wordpress.get_post(target, password, post_id)
    except _wordpress.WPError as exc:
        return {**base, "error": str(exc)}
    edited_blocks = _wordpress.html_to_blocks(
        (post.get("content") or {}).get("rendered", ""))
    original_blocks = _wordpress.sections_to_blocks(_sections_of(draft))
    if edited_blocks == original_blocks:
        return {**base, "changed": False, "already_synced": False}
    diff = _wordpress.diff_blocks(original_blocks, edited_blocks)
    try:
        outcome = _taste.record_wp_edit(
            brand_id, record.get("site_url") or target["site_url"],
            post_id, "\n\n".join(original_blocks),
            "\n\n".join(edited_blocks))
    except ValueError as exc:
        return {**base, "error": str(exc)}
    if outcome["already_synced"]:
        return {**base, "changed": False, "already_synced": True}
    return {**base, "changed": True, "diff": diff,
            "taste_entry_id": outcome["entry"]["id"],
            "taste_entry_created": outcome["created"],
            "taste_entry_updated": outcome["updated"]}


@mcp.tool()
def sync_wordpress_edits(brand_id: str = "", draft_id: str = "") -> dict:
    """Sync the owner's WordPress edits back into the taste layer
    (quality uplift WS3) — the feedback loop that teaches the system
    from what Smith actually changed before publishing.

    For each exported draft (one `draft_id`, or every exported draft
    of a `brand_id`): GET the post's current content from the live
    site (READ-ONLY — this tool never writes to WordPress),
    normalise it to text blocks, and diff against the exported
    draft's blocks. Unchanged → {changed: false}. Changed → the
    (exported, current) pair is recorded in the brand's taste layer
    as an "edited" verdict entry (both versions kept, provenance =
    WP post id + site), and the result carries a diff summary
    (blocks changed, words added/removed). Re-syncing the same post
    without further edits is idempotent (keyed on post id + content
    hash); a post edited AGAIN supersedes its entry with the newest
    edited text — the taste layer's re-mark rule."""
    if draft_id:
        draft, error = _load_draft_or_error(draft_id)
        if error:
            return error
        if not draft.get("wordpress_export"):
            return {"error": f"draft {draft_id} has no "
                             f"wordpress_export record — export it "
                             f"before syncing edits",
                    "draft_id": draft_id}
        record = draft["wordpress_export"]
        brand = record.get("brand_id") \
            or draft.get("brief", {}).get("brand_id") or "moc-brew"
        return {"brand_id": brand, "draft_id": draft_id,
                "results": [_sync_one_wp_edit(brand, draft)]}
    if not brand_id:
        return {"error": "pass brand_id (sync every exported draft "
                         "of the brand) or draft_id (sync one)"}
    candidates = []
    for path in sorted(drafts_dir().glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, ValueError):
            continue  # corrupt drafts are list_drafts' story, not ours
        if not isinstance(data, dict) or not data.get("wordpress_export"):
            continue
        brief = data.get("brief") if isinstance(data.get("brief"),
                                                dict) else {}
        if (brief.get("brand_id") or "moc-brew") != brand_id:
            continue
        data.setdefault("draft_id", path.stem)
        candidates.append(data)
    return {"brand_id": brand_id,
            "results": [_sync_one_wp_edit(brand_id, d)
                        for d in candidates]}


# --------------------------------------------------------------------------
# Tools — WordPress rewrite path (specs/wp-rewrite-feature.md):
# list + import + audit + verdict for posts already published on a
# brand's site. The importer issues GET requests ONLY; the original
# post is never written to by any step of this flow (a hard rule in
# the same class as the exporter's never-publish rule, pinned by
# the mock-server tests). Export of an imported draft goes through
# export_draft_to_wordpress above — its wp_import branch is the
# only addition there.
# --------------------------------------------------------------------------

def _find_import_draft(brand_id: str, wp_post_id: int) -> dict | None:
    """The live (non-superseded) imported draft for a (brand,
    wp_post_id) pair, or None — the import duplicate rule's lookup.
    Corrupt draft files are list_drafts' story, not the importer's:
    they are skipped here exactly as the edit sync skips them."""
    for path in sorted(drafts_dir().glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        source = data.get("source")
        if not isinstance(source, dict) \
                or source.get("kind") != "wp_import" \
                or source.get("brand_id") != brand_id \
                or source.get("wp_post_id") != wp_post_id \
                or data.get("status") == "superseded":
            continue
        data.setdefault("draft_id", path.stem)
        return data
    return None


def _audit_original(draft: dict) -> dict:
    """The original-post audit (spec §3.4): fact check + prohibited
    claims + brand/craft scores over the CONVERTED ORIGINAL text —
    advisory context for the owner's verdict, stored on the draft
    as `original_audit`, NEVER in gate_history: the old post's bad
    scores must not dirty the rewrite's gate record, and the audit
    blocks nothing.

    The audit also carries the conversion coverage: re-measured
    from the draft's stored `original.content_html` snapshot on
    every run (the converter is deterministic, so this is the same
    number the import reported — and an imported draft that
    predates persisted coverage still gets a genuinely measured
    value here, never an invented one). The audit NEVER writes
    into the `original` block itself: only the import tool does."""
    brand_id = draft.get("brief", {}).get("brand_id", "")
    brand, facts = _resolve_brand_facts(brand_id)
    sections = _sections_of(draft)
    fmt = draft.get("brief", {}).get("format", "blog")
    issues, flagged, _failing = _fact_check_draft(sections, facts,
                                                  brand)
    _violations, brand_score, _lf = _lint_draft(sections, fmt, brand)
    ctx_id, max_sw, banned = _brand_check_context(brand)
    craft = _craft_check_draft(sections, fmt, brand_id=ctx_id,
                               max_sentence_words=max_sw,
                               banned_terms=banned)
    coverage = None
    low_coverage = None
    original = draft.get("original")
    if isinstance(original, dict):
        content_html = original.get("content_html")
        if isinstance(content_html, str):
            coverage = _wp_convert.convert_post_html(
                content_html)["coverage"]
        elif isinstance(original.get("coverage"), (int, float)):
            coverage = original["coverage"]
        if coverage is not None:
            low_coverage = coverage < _wp_convert.COVERAGE_FLOOR
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "coverage": coverage,
        "low_coverage": low_coverage,
        "fact_issues": issues,
        "prohibited_claims": [i for i in issues
                              if i.get("type") == "prohibited_claim"],
        "flagged_claims": flagged,
        "brand_score": brand_score,
        "craft_score": craft.get("score"),
    }


@mcp.tool()
def list_wordpress_posts(brand_id: str, status: str = "",
                         per_page: int = 20) -> dict:
    """List a brand's WordPress posts (READ-ONLY — a GET): id,
    title, status, date, modified, link. With credentials
    configured, `status` may name any WP status (e.g. "draft") and
    defaults to "publish"; WITHOUT credentials only published
    posts are visible (the public view) whatever status is asked
    for — reported honestly via `credentials_configured`."""
    try:
        target = _load_wp_target(brand_id)
    except ValueError as exc:
        return {"error": str(exc), "brand_id": brand_id}
    if target is None:
        return {"error": f"no WordPress target configured for brand "
                         f"{brand_id} — set one with "
                         f"set_wordpress_target",
                "brand_id": brand_id}
    password = _wordpress.load_app_password(brand_id)
    effective_status = status or "publish"
    if password is None:
        effective_status = "publish"
    try:
        posts = _wordpress.list_posts(target, password,
                                      status=effective_status,
                                      per_page=per_page)
    except _wordpress.WPError as exc:
        return {"error": str(exc), "brand_id": brand_id}
    out = []
    for post in posts:
        if not isinstance(post, dict) or "id" not in post:
            continue
        out.append({
            "wp_post_id": int(post["id"]),
            "title": _wp_convert.plain_text(
                (post.get("title") or {}).get("rendered", "")),
            "status": post.get("status", ""),
            "date": post.get("date", ""),
            "modified": post.get("modified", ""),
            "link": post.get("link", "")})
    return {"brand_id": brand_id, "site_url": target["site_url"],
            "credentials_configured": password is not None,
            "posts": out, "count": len(out)}


@mcp.tool()
def import_wordpress_post(brand_id: str, wp_post_id: int,
                          force: bool = False) -> dict:
    """Import ONE published (or draft) WordPress post into a core
    draft (READ-ONLY against WordPress — GETs only): the post's
    content.rendered is snapshotted BYTE-VERBATIM into the draft's
    immutable `original` block, converted to sections by the fixed
    rule table (content_core/wp_convert.py), and audited against
    the brand's current facts (the audit lands in `original_audit`,
    never gate_history). The post's own images enter the draft's
    image state as already-fulfilled `existing_wp` entries carrying
    their wp_media_id, so a later export reuses them without
    re-uploading.

    Duplicate rule (the exporter's shape): a live imported draft
    for the same (brand, wp_post_id) REFUSES the import and names
    itself; force=True snapshots fresh into a NEW draft whose
    source.supersedes points at the old one, which is marked
    `superseded` and locked out of export — the old draft's audit
    trail is never overwritten in place.

    A password-protected post is refused (its rendered content is
    empty). The import report carries the conversion coverage:
    below 0.97 the draft is flagged low_coverage and the suggested
    verdict is audit_only until the owner looks by hand. The
    coverage, the low_coverage flag and the import warnings are
    also PERSISTED on the draft — in the `original` block, next to
    the converter's other output (media / raw_blocks / links) —
    and the coverage lands in the stored `original_audit` too, so
    a reader of the draft (e.g. the admin panel) sees the same
    numbers without the import call's return value."""
    try:
        target = _load_wp_target(brand_id)
    except ValueError as exc:
        return {"error": str(exc), "brand_id": brand_id}
    if target is None:
        return {"error": f"no WordPress target configured for brand "
                         f"{brand_id} — set one with "
                         f"set_wordpress_target",
                "brand_id": brand_id}
    try:
        brand = _resolve_brand_only(brand_id)
        _resolve_brand_facts(brand_id)
    except ValueError as exc:
        return {"error": str(exc), "brand_id": brand_id}
    existing = _find_import_draft(brand_id, wp_post_id)
    if existing is not None and not force:
        return {"error": f"post {wp_post_id} on {target['site_url']} "
                         f"is already imported as draft "
                         f"{existing['draft_id']} — pass force=True "
                         f"to snapshot it again into a new draft",
                "reason": "already_imported",
                "brand_id": brand_id,
                "draft_id": existing["draft_id"]}
    password = _wordpress.load_app_password(brand_id)
    try:
        post = _wordpress.get_post(target, password, wp_post_id)
    except _wordpress.WPError as exc:
        message = str(exc)
        if password is None:
            message += (" — without credentials only published "
                        "posts are visible: "
                        + _wordpress.not_configured_message(brand_id))
        return {"error": message, "brand_id": brand_id,
                "wp_post_id": wp_post_id}
    content = post.get("content") or {}
    if content.get("protected"):
        return {"error": f"post {wp_post_id} is password-protected "
                         f"(content.protected) — its rendered "
                         f"content is empty, so there is nothing "
                         f"to import",
                "brand_id": brand_id, "wp_post_id": wp_post_id}
    content_html = str(content.get("rendered") or "")
    title = _wp_convert.plain_text(
        (post.get("title") or {}).get("rendered", ""))
    converted = _wp_convert.convert_post_html(content_html)
    sections = converted["sections"]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    warnings: list[str] = []
    low_coverage = converted["coverage"] < _wp_convert.COVERAGE_FLOOR
    if low_coverage:
        warnings.append(
            f"conversion coverage {converted['coverage']:.3f} is "
            f"below {_wp_convert.COVERAGE_FLOOR} — part of the "
            f"post's text lives in constructs the converter does "
            f"not route; suggested verdict: audit_only")
    if converted["no_text"]:
        warnings.append("the post has no extractable text "
                        "(no_text) — audit_only or drop are the "
                        "workable verdicts")
    if converted["word_count"] > _wp_convert.LONG_POST_WORDS:
        warnings.append(
            f"long post: {converted['word_count']} words "
            f"(> {_wp_convert.LONG_POST_WORDS})")

    brief = {"id": f"wp-import-{brand_id}-{wp_post_id}",
             "format": "blog", "title": title, "topic": title,
             "keyword": title, "brand_id": brand_id}
    draft_id = f"d-{uuid.uuid4().hex[:10]}"
    import_round = 1
    supersedes = None
    if existing is not None:
        import_round = int(existing.get("source", {}).get(
            "import_round") or 1) + 1
        supersedes = existing["draft_id"]

    # Images state: the standard plan over the converted sections,
    # with the post's own media pre-fulfilled as existing_wp (spec
    # §3.3) — the exporter reuses their wp_media_id, no re-upload.
    plan = _plan_images(sections, brief, brand)
    images: dict[str, dict] = {}
    featured_id = post.get("featured_media") or 0
    if featured_id:
        try:
            media = _wordpress.get_media(target, password,
                                         int(featured_id))
            images["img-featured"] = {
                "source": "existing_wp",
                "ref": media.get("source_url", ""),
                "filename": str(media.get("source_url", "")).rsplit(
                    "/", 1)[-1],
                "alt": media.get("alt_text", ""),
                "alt_suggested": False, "caption": "",
                "description": "", "prompt_used": "",
                "from_library": None,
                "wp_media_id": int(featured_id)}
        except _wordpress.WPError as exc:
            warnings.append(
                f"featured media {featured_id} could not be read "
                f"({exc}) — the snapshot keeps the id; a rewrite "
                f"export will go out without it")
    slot_ids = {p["id"] for p in plan}
    for section in sections:
        slot = f"img-{section['id']}"
        if slot not in slot_ids or not section["source_media"]:
            continue
        first = section["source_media"][0]
        if not first.get("wp_media_id"):
            continue
        images[slot] = {
            "source": "existing_wp", "ref": first["source_url"],
            "filename": str(first["source_url"]).rsplit("/", 1)[-1],
            "alt": first.get("alt", ""), "alt_suggested": False,
            "caption": first.get("caption", ""), "description": "",
            "prompt_used": "", "from_library": None,
            "wp_media_id": int(first["wp_media_id"])}

    draft = {
        "draft_id": draft_id, "brief": brief,
        "outline": [{"id": s["id"], "heading": s["heading"],
                     "intent": "", "target_words": 0}
                    for s in sections],
        "sections": sections,
        "rewrite_count": 0, "gate_history": [], "awaiting_rewrite": [],
        "status": "drafting",
        "source": {"kind": "wp_import", "brand_id": brand_id,
                   "site_url": target["site_url"],
                   "wp_post_id": int(wp_post_id),
                   "wp_url": post.get("link", ""),
                   "wp_status": post.get("status", ""),
                   "wp_modified": post.get("modified", ""),
                   "imported_at": now, "import_round": import_round,
                   "supersedes": supersedes},
        "original": {
            "title": title, "slug": post.get("slug", ""),
            "excerpt": _wp_convert.plain_text(
                (post.get("excerpt") or {}).get("rendered", "")),
            "content_html": content_html,
            "featured_media": int(featured_id or 0),
            "categories": list(post.get("categories") or []),
            "tags": list(post.get("tags") or []),
            "media": converted["media"],
            "raw_blocks": converted["raw_blocks"],
            "links": [{"section_id": s["id"], **lnk}
                      for s in sections for lnk in s["links"]],
            "coverage": converted["coverage"],
            "low_coverage": low_coverage,
            "warnings": warnings},
        "original_audit": None,
        "verdict": None,
        "images": {"plan": plan, "images": images, "candidates": {}},
    }
    draft["original_audit"] = _audit_original(draft)
    _store_draft(draft)
    if existing is not None:
        with _draft_lock(existing["draft_id"]):
            old, error = _load_draft_or_error(existing["draft_id"])
            if error is None:
                old["status"] = "superseded"
                old["superseded_by"] = draft_id
                _store_draft(old)
    return {"brand_id": brand_id, "draft_id": draft_id,
            "wp_post_id": int(wp_post_id),
            "sections": len(sections),
            "coverage": converted["coverage"],
            "low_coverage": low_coverage,
            "raw_blocks": len(converted["raw_blocks"]),
            "demoted_headings": converted["demoted_headings"],
            "links_extracted": converted["links_extracted"],
            "word_count": converted["word_count"],
            "no_text": converted["no_text"],
            "suggested_verdict": "audit_only"
            if (low_coverage or converted["no_text"]) else None,
            "warnings": warnings,
            "original_audit": draft["original_audit"]}


@mcp.tool()
def audit_imported_post(draft_id: str) -> dict:
    """Re-run the original-post audit on an imported draft (e.g.
    after the brand's facts profile changed): fact issues,
    prohibited claims and brand/craft scores are recomputed over
    the converted original text and OVERWRITE `original_audit`.
    The refreshed audit also carries the conversion coverage,
    re-measured from the stored snapshot (see _audit_original).
    gate_history is never touched — the audit is advisory context
    for the verdict, not a gate. Refused for drafts that did not
    come from import_wordpress_post."""
    try:
        with _draft_lock(draft_id):
            draft, error = _load_draft_or_error(draft_id)
            if error:
                return error
            source = draft.get("source")
            if not isinstance(source, dict) \
                    or source.get("kind") != "wp_import":
                return {"error": f"draft {draft_id} is not a "
                                 f"WordPress import — "
                                 f"audit_imported_post only applies "
                                 f"to imported drafts",
                        "draft_id": draft_id}
            try:
                draft["original_audit"] = _audit_original(draft)
            except ValueError as exc:
                return {"error": str(exc), "draft_id": draft_id}
            _store_draft(draft)
    except ValueError as exc:  # lock timeout / invalid draft id
        return {"error": str(exc), "draft_id": draft_id}
    return {"draft_id": draft_id,
            "original_audit": draft["original_audit"]}


@mcp.tool()
def set_rewrite_verdict(draft_id: str, verdict: str,
                        reason: str = "") -> dict:
    """Record the OWNER's verdict on an imported draft (spec §3.5):
    `rewrite` (the draft enters the normal writing flow; export
    opens), `audit_only` (the audit stands as the record; no
    rewrite, no export) or `drop` (the post should not exist; the
    core records the verdict + reason only — removing the post on
    WordPress is the owner's hand, never a tool's). One verdict per
    post, the owner's call, like the research pack's per-item
    discipline. Export of an imported draft is refused unless the
    standing verdict is `rewrite`."""
    if verdict not in ("rewrite", "audit_only", "drop"):
        return {"error": f"verdict must be 'rewrite', 'audit_only' "
                         f"or 'drop', got {verdict!r}",
                "draft_id": draft_id}
    try:
        with _draft_lock(draft_id):
            draft, error = _load_draft_or_error(draft_id)
            if error:
                return error
            source = draft.get("source")
            if not isinstance(source, dict) \
                    or source.get("kind") != "wp_import":
                return {"error": f"draft {draft_id} is not a "
                                 f"WordPress import — verdicts only "
                                 f"apply to imported drafts",
                        "draft_id": draft_id}
            record = {"verdict": verdict, "reason": reason,
                      "decided_at": datetime.now(
                          timezone.utc).isoformat(timespec="seconds")}
            draft["verdict"] = record
            _store_draft(draft)
    except ValueError as exc:  # lock timeout / invalid draft id
        return {"error": str(exc), "draft_id": draft_id}
    return {"draft_id": draft_id, "verdict": record}


@mcp.tool()
def prepare_brand_setup(site_url: str, brand_id: str,
                        display_name: str = "") -> dict:
    """Setup Assist: prepare a NEW brand's whole setup from its site
    in ONE call and return the SETUP DOSSIER — the AI does the work,
    the human only decides. Fetches the site (fetch_site machinery),
    builds + saves the brand DRAFT via the Brand Builder (a visual
    palette inferred from the site's CSS is stamped as a
    'setup_assist' proposal), extracts a FACTS DRAFT (prices /
    weights / origin candidates / delivery promises, with provenance
    per value), creates the research pack if none exists and builds
    its proposals from the site's own pages, and PROPOSES a corpus
    (best registered match + reasoning + confidence) and persona
    frame candidates.

    HARD RULES: nothing goes live — the brand stays a draft, the
    facts stay a draft inside the setup record (NOT the live facts
    profile), pack items are all "proposed" (per-item manual
    approval only), the corpus is proposed never chosen, no persona
    is saved. Contradictions the site carries against itself (two
    prices for one pack size, two delivery promises) are surfaced
    as blockers and the contested values are WITHHELD from the
    facts draft, never silently resolved. Re-running prepare is
    idempotent-ish: the pack is reused (never version-bumped), its
    proposals are built only once, and the facts draft is refreshed
    from the fresh fetch — but the brand draft is refreshed WITHOUT
    erasing the human's review: fields the stored draft carries
    that differ from the fresh generation (banned_terms above all)
    are preserved, and the dossier's `preserved_fields` names
    every one of them."""
    try:
        return _setup_assist.prepare_brand_setup(
            site_url, brand_id, display_name=display_name or None)
    except ValueError as exc:
        return {"error": str(exc), "brand_id": brand_id}


@mcp.tool()
def get_brand_setup(brand_id: str) -> dict:
    """Setup Assist: the current setup checklist for a prepared
    brand — steps (site_fetch / brand_draft / facts / research_pack
    / corpus_choice / persona / wordpress_target) with status
    done | proposed | blocked | needs_human, the facts-draft
    summary, pack counts, the corpus proposal + any live choice,
    all open contradictions, and `needs_human`: exactly what only
    the owner can decide. Step state is DERIVED from the live
    stores, so completing a step through the existing tools
    (save_facts, set_corpus_choice, decide_pack_item,
    set_wordpress_target…) flips it here automatically."""
    try:
        return _setup_assist.build_setup_dossier(brand_id)
    except ValueError as exc:
        return {"error": str(exc), "brand_id": brand_id}


@mcp.tool()
def run_golden() -> dict:
    """Run the golden set (6 Mộc Brew briefs + 1 bad-draft demo) through the
    LangGraph core with MockWriter. Regression test for the core itself —
    run after any prompt/flow/core change."""
    from content_core.golden import BAD_DRAFT_BRIEF, GOLDEN_BRIEFS
    from content_core.graph import run_flow
    from content_core.runner import check_case

    results = []
    for case in [*GOLDEN_BRIEFS, BAD_DRAFT_BRIEF]:
        state = run_flow(case["brief"], outline_approved=True)
        ok, detail = check_case(case, state)
        results.append({"id": case["brief"]["id"], "passed": ok,
                        "criteria": case["criteria"], "detail": detail,
                        "status": state.get("status"),
                        "brand_score": state.get("scores", {}).get("brand")})
    passed = sum(1 for r in results if r["passed"])
    return {"passed": passed, "total": len(results), "all_passed": passed == len(results),
            "results": results}


#: The tool list is DERIVED from the FastMCP registry (review M19):
#: the hand-maintained literal this replaces had already drifted from
#: the README once (29 vs 31) — a derived list cannot drift.
TOOL_NAMES = sorted(t.name for t in mcp._tool_manager.list_tools())


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Content Core MCP server — the host app's model writes, "
                    "these tools are its hands + referee.")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio",
                        help="stdio (default; launched by the AI app) or http "
                             "(streamable HTTP for local apps that connect "
                             "over HTTP)")
    args = parser.parse_args()
    if args.transport == "http":
        # Localhost ONLY in v1: there is no auth. Host/port come from the
        # environment so the app/tunnel setup controls them; the defaults
        # bind 127.0.0.1:8765 and nothing else. The loopback default is
        # now ENFORCED, not just documented (review M9): a non-loopback
        # CONTENT_CORE_HTTP_HOST refuses to start unless the operator
        # explicitly sets CONTENT_CORE_ALLOW_NONLOCALHOST=1 — binding
        # an unauthenticated server to the LAN must be a deliberate act.
        host = os.environ.get("CONTENT_CORE_HTTP_HOST", "127.0.0.1")
        port = int(os.environ.get("CONTENT_CORE_HTTP_PORT", "8765"))
        if host not in ("127.0.0.1", "::1", "localhost") and \
                os.environ.get("CONTENT_CORE_ALLOW_NONLOCALHOST") != "1":
            print(f"refusing to bind non-loopback host {host!r}: this "
                  f"server has NO authentication. Set "
                  f"CONTENT_CORE_ALLOW_NONLOCALHOST=1 to override "
                  f"(only behind a tunnel you control).", file=sys.stderr)
            sys.exit(2)
        mcp.settings.host = host
        mcp.settings.port = port
        print(f"content-core MCP (streamable-http) on http://{host}:{port}"
              f"{mcp.settings.streamable_http_path} — bound to {host}, "
              f"NO AUTHENTICATION", file=sys.stderr)
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
