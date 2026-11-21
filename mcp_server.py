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
import time
import uuid
from pathlib import Path

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
from content_core.gates import CRAFT_PASS_SCORE, MAX_REWRITES
from content_core.gates import evaluate_gate as _evaluate_gate
from content_core.graph import Core
from content_core.linter import lint_draft as _lint_draft
from content_core.linter import lint_section as _lint_section
from content_core.loaders import DEFAULT_STYLE_PROFILE_ID, atomic_write_text, stored_file_stem
from content_core.loaders import banned_terms as _brand_banned_terms
from content_core.loaders import list_style_profiles as _list_style_profiles
from content_core.loaders import list_facts_profiles as _list_facts_profiles
from content_core.loaders import load_brand, load_facts, load_persona
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
from content_core.brand_extract import compare_brand_voice as _compare_brand_voice
from content_core.brand_extract import extract_brand_kit as _extract_brand_kit
from content_core.brand_extract import extract_vocabulary as _extract_vocabulary
from content_core.brand_extract import find_site_claims as _find_site_claims
from content_core.brand_extract import site_corpus as _site_corpus
from content_core.brand_extract import suggest_voice_rules as _suggest_voice_rules
from content_core.loaders import list_brand_drafts as _list_brand_drafts
from content_core.loaders import load_brand_draft as _load_brand_draft
from content_core.loaders import save_brand_draft as _save_brand_draft_file
from content_core.seo import seo_pass as _seo_pass
from content_core.style import analyze_samples as _analyze_samples
from content_core.style import fingerprint as _fingerprint
from content_core.style import build_profile as _build_profile
from content_core.style import refresh_profile as _refresh_style_profile
from content_core.style import style_match as _style_match

PROJECT_DIR = Path(__file__).resolve().parent

mcp = FastMCP("content-core")

_core: Core | None = None


def _get_core() -> Core:
    """One Core instance (MockWriter inside is never used by the MCP tools —
    the graph nodes we reuse are validate/research/outline, which don't write)."""
    global _core
    if _core is None:
        _core = Core()
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
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"corrupt JSON in draft file {path}: {exc}") from None
    if not isinstance(data, dict) or not ({"draft_id", "brief", "sections"} & set(data)):
        raise ValueError(
            f"corrupt draft file {path}: expected a JSON object with "
            f"draft keys (draft_id / brief / sections)")
    if not isinstance(data.get("sections", []), list):
        raise ValueError(f"corrupt draft file {path}: 'sections' must be a list")
    if not isinstance(data.get("brief", {}), dict):
        raise ValueError(f"corrupt draft file {path}: 'brief' must be an object")
    for key, default in (("brief", {}), ("outline", []), ("sections", []),
                         ("gate_history", []), ("awaiting_rewrite", [])):
        data.setdefault(key, type(default)())
    data.setdefault("rewrite_count", 0)
    data.setdefault("status", "drafting")
    data.setdefault("draft_id", draft_id)
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
# final_gates lost gate_history entries). An O_EXCL lockfile per
# draft serialises the critical section. Single-writer use is
# unaffected: an uncontended acquire is one syscall.
_DRAFT_LOCK_TIMEOUT_S = 10.0   # bounded retry before a structured error
_DRAFT_LOCK_STALE_S = 30.0     # a lock older than this is a crashed holder's
_DRAFT_LOCK_POLL_S = 0.05


@contextlib.contextmanager
def _draft_lock(draft_id: str):
    """Hold the per-draft lock (see the constants above). Bounded
    retry; a stale lock (mtime older than _DRAFT_LOCK_STALE_S) is
    stolen; the lock is always released in finally. Timeout raises
    ValueError, which the tools surface as {"error": ...}."""
    try:
        stem = stored_file_stem(draft_id)
    except ValueError:
        raise ValueError("invalid draft_id") from None
    lock_path = drafts_dir() / f"{stem}.lock"
    deadline = time.monotonic() + _DRAFT_LOCK_TIMEOUT_S
    fd = None
    while fd is None:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
            except OSError:
                age = 0.0  # vanished mid-check: retry the acquire
            if age > _DRAFT_LOCK_STALE_S:
                try:
                    lock_path.unlink()
                except OSError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise ValueError(
                    f"draft {draft_id} is locked by another writer — "
                    f"retry shortly (lock timeout "
                    f"{_DRAFT_LOCK_TIMEOUT_S:.0f}s)")
            time.sleep(_DRAFT_LOCK_POLL_S)
    try:
        yield
    finally:
        try:
            os.close(fd)
        finally:
            try:
                lock_path.unlink()
            except OSError:
                pass


def _sections_of(draft: dict) -> list[dict]:
    return draft.get("sections", [])


def _brand_max_sentence_words() -> int | None:
    """The server brand's own max sentence length — passed to craft so
    its rhythm statistic excludes over-long sentences exactly like the
    gate paths do (monotonicity fix, evaluation R5)."""
    try:
        return int(_brand()["voice"]["sentence_rules"]["max_sentence_words"])
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
    except ValueError as exc:
        return {"error": str(exc)}
    return _save_facts_file(doc)


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
    """Validate a brief {topic, format, keyword?, title?, requested_claims?}.

    Includes prohibited-claim detection: a brief ASKING for e.g.
    '100% chữa mất ngủ' is refused — `valid` is False, `status` is
    "refused", and the claims are listed in refused_claims. Do not write
    the brief as asked; the claims must not appear in any draft. A brief
    that merely DISCLAIMS a claim ("cam kết không chữa mất ngủ") is not
    a request and validates clean."""
    core = _get_core()
    result = core.validate_brief({"brief": brief})  # type: ignore[arg-type]
    refused = result.get("refused_claims", find_refused_claims(str(brief), _brand()))
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
    if draft_id:
        draft, error = _load_draft_or_error(draft_id)
        if error:
            return error
        fmt = draft.get("brief", {}).get("format", format)
        return _craft_check_draft(
            _sections_of(draft), fmt, brand_id=brand_id or None,
            max_sentence_words=_brand_max_sentence_words() if server_brand else None,
            banned_terms=_brand_banned_terms(_brand()) if server_brand else None)
    return _craft_check_text(text, format, brand_id=brand_id or None,
                             max_sentence_words=_brand_max_sentence_words()
                             if server_brand else None,
                             banned_terms=_brand_banned_terms(_brand())
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
    except ValueError as exc:  # e.g. zero usable samples
        return {"error": str(exc)}
    return _save_style_profile_file(profile)


@mcp.tool()
def get_style_profile(profile_id: str = "") -> dict:
    """Return a stored Style Profile by id ('default' → the shipped
    mocbrew-blog-voice profile). With no id: the list of available
    profiles plus the default profile (or null when none is stored)."""
    if profile_id:
        pid = DEFAULT_STYLE_PROFILE_ID if profile_id == "default" else profile_id
        profile = _load_style_profile(pid)
        if profile is None:
            return {"error": f"style profile not found: {profile_id}",
                    "available": _list_style_profiles()}
        return _refresh_style_profile(profile)
    default_profile = _load_style_profile(DEFAULT_STYLE_PROFILE_ID)
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
    profile = _resolve_style_profile(profile_id)
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
    fp = _fingerprint(corpus["all_text"])
    claims = _find_site_claims(pages)
    warnings: list[str] = []
    if corpus["total_words"] < 800:
        warnings.append(f"corpus is small ({corpus['total_words']} words from "
                        f"{corpus['page_count']} page(s)) — fetch more pages "
                        f"(blog/product) for a trustworthy profile")
    if claims:
        warnings.append(f"site makes {len(claims)} claim(s) needing evidence — "
                        f"they are listed for review, never approved silently")
    return {"corpus": {"page_count": corpus["page_count"],
                       "total_words": corpus["total_words"],
                       "per_page": corpus["per_page"]},
            "fingerprint": fp,
            "brand_kit": _extract_brand_kit(pages),
            "voice_suggestions": _suggest_voice_rules(corpus["all_text"]),
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
    draft = _build_brand_draft(pages, qualitative=qualitative,
                               source_type=source_type, brand_name=brand_name)
    return _save_brand_draft_file(draft)


@mcp.tool()
def get_brand_draft(draft_id: str = "") -> dict:
    """Return a stored brand draft by id (the brand slug, e.g. "som-coffee").
    With no id: the list of available brand drafts."""
    if draft_id:
        draft = _load_brand_draft(draft_id)
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
    `style_profile_error` and the context is built without the part."""
    fmt = format or brief.get("format", "blog")
    brief = {**brief, "format": fmt}
    persona = load_persona()["data"]
    brand = _brand()
    sources = research_facts(str(brief))["sources"]

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
    if style_profile_id:
        style_profile = _resolve_style_profile(style_profile_id)
        if style_profile is None:
            style_error = f"style profile not found: {style_profile_id}"

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
        "counts": {"sources": len(sources), "craft_cards": len(cards),
                   "exemplars": len(exemplars)},
        "style_profile": ({"id": style_profile["id"], "name": style_profile.get("name", "")}
                          if style_profile else None),
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
            if not str(item.get("heading", "")).strip():
                return {"ok": False, "error": f"outline item {i} has no heading", "outline": []}
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


# --------------------------------------------------------------------------
# Tools — deterministic checks & the final gate
# --------------------------------------------------------------------------

@mcp.tool()
def brand_check(text: str = "", format: str = "blog", draft_id: str = "") -> dict:
    """Deterministic brand linter + score (0-100). Pass either a raw `text`
    (checked as one section) or a `draft_id` (all saved sections checked;
    overall score = worst section). Catches banned terms, exclamation marks
    in blog, sentence-length rule violations."""
    brand = _brand()
    if draft_id:
        draft, error = _load_draft_or_error(draft_id)
        if error:
            return error
        fmt = draft.get("brief", {}).get("format", format)
        violations, score, failing = _lint_draft(_sections_of(draft), fmt, brand)
        return {"score": score, "violations": violations, "failing_sections": failing,
                "banned_found": [v["detail"] for v in violations if v["type"] == "banned_term"]}
    violations, score = _lint_section(text, format, brand, "text")
    return {"score": score, "violations": violations, "failing_sections": ["text"] if violations else [],
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
    except ValueError as exc:
        return {"error": str(exc)}
    return _save_quality_profile_file(profile)


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
    entry = {"pass": len(history) + 1, "brand_score": hard["brand_score"],
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
    draft["gate_history"] = history

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
                   "gate_history": history,
                   "instruction": instruction}
    if quality is not None:
        gate_result["quality"] = quality
    if style_report is not None:
        gate_result["style_match"] = style_report
        gate_result["style_advisory"] = True
    return gate_result


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


TOOL_NAMES = ["get_persona", "get_brand", "research_facts", "assemble_context",
              "validate_brief", "make_outline", "start_draft", "save_section",
              "get_draft", "brand_check", "fact_check", "seo_check",
              "final_gate", "run_golden",
              "get_craft_cards", "get_exemplars", "get_domain_glossary", "craft_check",
              "analyze_style", "save_style_profile", "get_style_profile", "style_match",
              "analyze_site_brand", "save_brand_draft", "get_brand_draft",
              "compare_brand_voice",
              "get_quality_profile", "save_quality_profile", "quality_score",
              "get_facts", "save_facts"]


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
        # bind 127.0.0.1:8765 and nothing else. Never expose this port
        # beyond localhost without a tunnel the user controls.
        host = os.environ.get("CONTENT_CORE_HTTP_HOST", "127.0.0.1")
        port = int(os.environ.get("CONTENT_CORE_HTTP_PORT", "8765"))
        mcp.settings.host = host
        mcp.settings.port = port
        print(f"content-core MCP (streamable-http) on http://{host}:{port}"
              f"{mcp.settings.streamable_http_path} — localhost only, no auth",
              file=sys.stderr)
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
