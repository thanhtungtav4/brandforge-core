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
from content_core.images import normalise_ref as _normalise_ref
from content_core.images import plan_images as _plan_images
from content_core.images import suggest_alt as _suggest_alt
from content_core.images import suggest_filename as _suggest_filename
from content_core.loaders import add_image_library_entry as _add_library_image_file
from content_core.loaders import list_image_library as _list_image_library_file
from content_core.seo import seo_pass as _seo_pass
from content_core import research as _research
from content_core import taste as _taste
from content_core import backup as _backup
from content_core import wordpress as _wordpress
from content_core.loaders import load_wordpress_target as _load_wp_target
from content_core.loaders import save_wordpress_target as _save_wp_target
from content_core.loaders import load_corpus_choice as _load_corpus_choice
from content_core.loaders import load_corpus_entry as _load_corpus_entry
from content_core.loaders import list_corpus_entries as _list_corpus_entries
from content_core.loaders import load_research_pack as _load_research_pack
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
def _draft_lock(draft_id: str):
    """Hold the per-draft lock (see the design note above). Timeout
    raises ValueError, which the tools surface as {"error": ...}."""
    try:
        stem = stored_file_stem(draft_id)
    except ValueError:
        raise ValueError("invalid draft_id") from None
    lock_path = drafts_dir() / f"{stem}.lock"
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
    """The draft's images state {"plan": [...], "images": {...}},
    defaulted for drafts started before the image layer existed."""
    state = draft.get("images")
    if not isinstance(state, dict):
        state = {}
    if not isinstance(state.get("plan"), list):
        state["plan"] = []
    if not isinstance(state.get("images"), dict):
        state["images"] = {}
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

@mcp.tool()
def get_persona() -> dict:
    """Return the author Persona: Linh, Character Card V2 (who writes)."""
    return load_persona()


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
                      "facts_triggered": 3000, "exemplar": 3000,
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
                 "any claim without a source must be marked [NEEDS SOURCE].")
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

    parts = [("persona", persona_text), ("brand_rules", brand_text),
             ("craft_cards", craft_text)]
    if style_profile is not None:
        parts.append(("style_profile", _style_context_text(style_profile)))
    parts += [("facts_triggered", facts_text), ("exemplar", exemplar_text),
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
def plan_images(draft_id: str) -> dict:
    """Plan a draft's image slots: one featured image + one in-content
    image per section (capped — see content_core/images.py). Each slot
    carries a deterministic generation prompt = scene + the brand's
    visual style block (byte-identical across slots; that shared block
    is the style lock). Planning is explicit: starting a draft does
    not auto-plan. Re-planning keeps fulfilments whose slot ids still
    exist. The plan lives in the draft's "images" state, under the
    per-draft lock."""
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
                                brand)
            state = _draft_images_state(draft)
            slot_ids = {p["id"] for p in plan}
            kept = {iid: e for iid, e in state["images"].items()
                    if iid in slot_ids}
            draft["images"] = {"plan": plan, "images": kept}
            _store_draft(draft)
    except ValueError as exc:  # lock timeout / invalid draft id
        return {"error": str(exc), "draft_id": draft_id}
    return {"draft_id": draft_id, "plan": plan, "images": kept}


@mcp.tool()
def set_image(draft_id: str, image_id: str, source: str, ref: str,
              alt: str = "", caption: str = "", description: str = "",
              filename: str = "", prompt_used: str = "") -> dict:
    """Fulfil ONE planned slot with an image the writer generated or
    selected. `source` is "ai" (generated for this article) or
    "client" (the client's real photo); `ref` is its path or URL —
    the core stores refs and metadata only, never image bytes.

    `filename` defaults to the SEO suggestion (keyword-led slug);
    `alt` defaults to the template suggestion and is then marked
    `alt_suggested` — replace it with alt written from the actual
    image whenever you can. For an "ai" image, pass `prompt_used`:
    the full prompt actually used, which must carry the brand's style
    block (image_check verifies it). Overwriting a slot drops any
    vision_check recorded for the previous image. Returns the stored
    entry plus this image's immediate check notes."""
    if source not in ("ai", "client"):
        return {"error": f"source must be 'ai' or 'client', got {source!r}",
                "draft_id": draft_id}
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
            state["images"][image_id] = entry
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
                      source: str = "client") -> dict:
    """Add one image to a brand's own image library (refs only — the
    core never copies image bytes). `description` says what the image
    shows: library ranking and alt suggestions read it, so write it
    like a caption. `source` is "client" (the client's real photo,
    the default) or "ai" (a generated image kept for reuse).
    Re-adding the same ref+description replaces, never duplicates."""
    try:
        return _add_library_image_file(brand_id, ref, description, tags,
                                       source)
    except ValueError as exc:
        return {"error": str(exc), "brand_id": brand_id}


@mcp.tool()
def list_library_images(brand_id: str) -> dict:
    """List a brand's image library entries ([] when the brand has
    no library yet)."""
    try:
        entries = _list_image_library_file(brand_id)
    except ValueError as exc:
        return {"error": str(exc), "brand_id": brand_id}
    return {"brand_id": brand_id, "images": entries}


@mcp.tool()
def fulfil_images_from_library(draft_id: str) -> dict:
    """Library-first fulfilment: for each unfulfilled plan slot, rank
    the DRAFT BRAND's library by token overlap (tags + description vs
    the slot's heading/intent + brief keyword) and fulfil with the
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
    below its floor)."""
    draft, error = _load_draft_or_error(draft_id)
    if error:
        return error
    fmt = draft.get("brief", {}).get("format", "blog")
    try:
        profile = (_quality_get_profile(quality_profile) if quality_profile
                   else _quality_preset(fmt))
        style_profile = _resolve_style_profile(
            draft.get("brief", {}).get("style_profile_id"))
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
    return {**report, "draft_id": draft_id}


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
    exactly as in quality_score."""
    draft, error = _load_draft_or_error(draft_id)
    if error:
        return error
    fmt = draft.get("brief", {}).get("format", "blog")
    try:
        profile = (_quality_get_profile(quality_profile) if quality_profile
                   else _quality_preset(fmt))
        style_profile = _resolve_style_profile(
            draft.get("brief", {}).get("style_profile_id"))
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
        "quality": {**quality, "draft_id": draft_id},
        "draft_id": draft_id,
    }


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
    style_profile = _resolve_style_profile(
        draft.get("brief", {}).get("style_profile_id"))

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
             "craft_advisory": not craft_blocking}
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
# Tools — corpus registry (system-level) + brand corpus choices
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# Tools — local data backup
# --------------------------------------------------------------------------

@mcp.tool()
def export_core_data() -> dict:
    """Snapshot ALL local stores (personas, brand files/drafts, facts,
    style/quality profiles, image libraries, research packs, corpus
    registry + choices, taste stores, drafts) into ONE timestamped
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
    message with the password scrubbed out."""
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
            password = _wordpress.load_app_password(brand_id)
            if password is None:
                return {"error": _wordpress.not_configured_message(
                    brand_id), "brand_id": brand_id,
                    "draft_id": draft_id}

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
            for entry in manifest:
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
            title = draft.get("brief", {}).get("title") or \
                draft.get("title") or "Untitled"
            payload = _wordpress.build_post_payload(
                title=title, content_html=content,
                featured_media=featured_media_id,
                category_id=target.get("default_category_id"))
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
                      "media": uploaded}
            draft["wordpress_export"] = record
            _store_draft(draft)
            return {"brand_id": brand_id, "draft_id": draft_id,
                    "wp_post_id": post_id,
                    "edit_link": record["edit_link"],
                    "post_link": record["post_link"],
                    "status": "draft",
                    "site_url": target["site_url"],
                    "media_uploaded": len(uploaded),
                    "media": uploaded, "warnings": warnings}
    except _wordpress.WPError as exc:
        return {"error": str(exc), "brand_id": brand_id,
                "draft_id": draft_id}
    except ValueError as exc:  # lock timeout / invalid draft id
        return {"error": str(exc), "brand_id": brand_id,
                "draft_id": draft_id}


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
