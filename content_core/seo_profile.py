"""SEO five-source research profiles — Round 1 (specs/
seo-five-source-feature.md, plan Round 1): the per-draft store's
record logic. Pure dict operations like research.py: the MCP tools
load a profile through loaders, mutate it here, and save it back
through loaders; this module never touches the disk and NEVER
writes to facts / brand / corpus / taste stores (spec A7 — the
competitor data in a profile is research material for ONE article,
not live configuration).

Design choices where the spec leaves room (recorded in
specs/seo-five-source-round1-notes.md):
- The search context is exactly the five spec fields, all required,
  no invented defaults (spec section 9 leaves defaults open).
- `read_count` counts sources read fully OR partially; the partial
  ones stay visible via `full_read_count` and their per-source
  truncation flag — partial-ness is reported, never hidden.
- Source/analysis mutations are allowed only while the profile is
  `collecting` or `analyzing`. Later states are reached only
  through the Round 2 transitions (direction → outline), and the
  ONLY way back is `reopen_analysis`, which invalidates the
  dependent approvals and keeps them in history (spec A6).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from urllib.parse import urlparse

from . import loaders

#: States in which sources and analysis may still change.
MUTABLE_STATES = ("collecting", "analyzing")

#: POLICY (Smith's open call, round-1 notes ambiguity #1): does a
#: PARTIAL read count toward the required 5 sources at the outline
#: gate? Round 1 behaviour — and the current setting — is True: a
#: partial read is real analysis material whose truncation stays
#: flagged per source. Flipping this one constant to False makes
#: only full reads count, everywhere `read_count` / `sufficiency`
#: are consumed (the outline gate included); `full_read_count`
#: always reports full reads only, either way.
PARTIAL_READS_COUNT_TOWARD_REQUIRED = True

#: States from which reopen_analysis may invalidate approvals and
#: return the profile to `analyzing` (Round 2). Round 3 extends
#: the list with `reviewing` — reopening there invalidates the
#: review records too (see reopen_analysis). `exported` stays
#: refused: a shipped article's workflow is complete, and the
#: WordPress post it created is never unwound by the core.
REOPENABLE_STATES = ("direction_review", "outline_review", "writing",
                     "reviewing")

#: States in which a review may be recorded (Round 3): the content
#: exists and an approved outline stands (`writing`), or a review
#: already stands and is being re-recorded after fixes
#: (`reviewing`).
REVIEWABLE_STATES = ("writing", "reviewing")

#: States from which an SEO draft may export (Round 3). Readiness
#: is a COMPUTED decision (export_readiness_reasons), never a
#: stored `ready_to_export` state — see the round-3 notes for why
#: the stored state was deliberately not introduced.
EXPORTABLE_STATES = ("writing", "reviewing")

#: States in which the shortfall exception may be RECORDED. The
#: exception is the owner's authorisation, not an input change —
#: it alters no source, claim or approval — so it stays recordable
#: up to the outline gate itself (direction_review / outline_
#: review), not only while inputs are mutable. Once writing has
#: begun an outline already passed the gate; changing inputs then
#: goes through reopen_analysis, not a retroactive exception.
EXCEPTION_STATES = MUTABLE_STATES + ("direction_review",
                                     "outline_review")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------

def validate_search_context(context: dict) -> dict:
    """Validate the search context recorded with a profile: the
    five spec fields (market, language, location, device,
    searched_at), each a non-empty string, no extras — and NO
    defaults: where/how the user searched is provenance, and a
    context the core invented would be false provenance. Returns
    the cleaned context (exactly the five fields, stripped)."""
    if not isinstance(context, dict):
        raise ValueError("search_context must be an object with "
                         "market / language / location / device / "
                         "searched_at")
    unknown = sorted(set(context) - set(loaders.SEO_SEARCH_CONTEXT_FIELDS))
    if unknown:
        raise ValueError(
            f"search_context has unknown fields: {unknown} — only "
            f"{list(loaders.SEO_SEARCH_CONTEXT_FIELDS)} are recorded")
    cleaned = {}
    for field in loaders.SEO_SEARCH_CONTEXT_FIELDS:
        value = context.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"search_context.{field} is required and must be a "
                f"non-empty string — the core invents no default "
                f"for it (spec section 9)")
        cleaned[field] = value.strip()
    searched_at = cleaned["searched_at"]
    try:
        datetime.fromisoformat(searched_at.replace("Z", "+00:00"))
    except ValueError:
        try:
            date.fromisoformat(searched_at)
        except ValueError:
            raise ValueError(
                f"search_context.searched_at must be an ISO date "
                f"or datetime, got {searched_at!r}") from None
    return cleaned


def new_profile(draft_id: str, brand_id: str, keyword: str,
                search_context: dict) -> dict:
    """Build a fresh profile record (state `collecting`). The
    brand_id is pinned by the CALLER from the draft's brief — this
    function only checks it is present."""
    if not str(keyword or "").strip():
        raise ValueError("keyword must be a non-empty string")
    if not str(brand_id or "").strip():
        raise ValueError("brand_id must be a non-empty string")
    now = _now()
    return {"schema_version": loaders.SEO_SCHEMA_VERSION,
            "draft_id": draft_id, "brand_id": brand_id,
            "keyword": keyword.strip(),
            "search_context": validate_search_context(search_context),
            "state": "collecting", "version": 1,
            "created_at": now, "updated_at": now,
            "sources": [], "analysis": None, "analysis_history": [],
            "shortfall_exception": None,
            "direction": None, "direction_history": [],
            "outlines": [], "reopen_log": [],
            "reviews": [], "export": None}


# ---------------------------------------------------------------------------
# Guards + lookups
# ---------------------------------------------------------------------------

def require_mutable(profile: dict) -> None:
    """Refuse a source/analysis mutation outside the states Round 1
    owns. Later workflow states are reached only through later
    rounds' transitions, which carry their own invalidation rules
    (spec A6) — a silent edit there would bypass them."""
    state = profile.get("state")
    if state not in MUTABLE_STATES:
        raise ValueError(
            f"seo profile for draft {profile.get('draft_id')} is in "
            f"state {state!r} — sources and analysis can only change "
            f"while {'/'.join(MUTABLE_STATES)} (later states belong "
            f"to later workflow rounds)")


def find_source(profile: dict, source_id: str) -> dict:
    for source in profile.get("sources", []):
        if source.get("source_id") == source_id:
            return source
    raise ValueError(
        f"unknown source_id: {source_id!r} for draft "
        f"{profile.get('draft_id')}")


def _check_url(profile: dict, url: str) -> str:
    if not isinstance(url, str) or not url.strip():
        raise ValueError("url must be a non-empty string")
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"url must be an http(s) URL with a host, "
                         f"got {url!r}")
    if any(s.get("url") == url for s in profile.get("sources", [])):
        raise ValueError(
            f"url already recorded in this profile: {url} — each "
            f"observed result is recorded once")
    return url


def _check_rank(observed_rank: object, *, original: bool) -> int:
    if isinstance(observed_rank, bool) \
            or not isinstance(observed_rank, int):
        raise ValueError(
            f"observed_rank must be an int, got {observed_rank!r}")
    if original and not 1 <= observed_rank <= loaders.SEO_REQUIRED_SOURCES:
        raise ValueError(
            f"observed_rank for an original result must be 1.."
            f"{loaders.SEO_REQUIRED_SOURCES} (the first "
            f"{loaders.SEO_REQUIRED_SOURCES} organic results), got "
            f"{observed_rank}")
    if not original and observed_rank < 1:
        raise ValueError(
            f"observed_rank must be a positive int, got "
            f"{observed_rank}")
    return observed_rank


def _source_record(profile: dict, url: str, title: str | None,
                   observed_rank: int, observed_at: str | None,
                   replaces_source_id: str | None,
                   replacement_reason: str = "") -> dict:
    return {"source_id": _new_id("src"), "url": url,
            "title": title.strip() if isinstance(title, str) else "",
            "observed_rank": observed_rank,
            "observed_at": observed_at
                or profile["search_context"]["searched_at"],
            "entry_source": "manual", "page_type": None,
            "read_status": "pending", "read_reason": "",
            "read_at": None, "content_truncated": False,
            "read_events": [],
            "replaces_source_id": replaces_source_id,
            "replacement_reason": replacement_reason}


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def add_source(profile: dict, url: str, observed_rank: int,
               title: str | None = None,
               observed_at: str | None = None) -> dict:
    """Record ONE original observed result. Ranks 1..5 are unique
    among originals: a rank already taken is refused, never
    overwritten — the original observation record is history
    (spec A2)."""
    require_mutable(profile)
    url = _check_url(profile, url)
    rank = _check_rank(observed_rank, original=True)
    taken = [s for s in profile["sources"]
             if s.get("replaces_source_id") is None
             and s.get("observed_rank") == rank]
    if taken:
        raise ValueError(
            f"observed_rank {rank} is already recorded for this "
            f"profile ({taken[0]['url']}) — original results are "
            f"never overwritten; record a replacement source "
            f"instead if the result changed")
    source = _source_record(profile, url, title, rank, observed_at,
                            None)
    profile["sources"].append(source)
    return source


def record_read(profile: dict, source_id: str, read_status: str,
                reason: str = "",
                content_truncated: bool = False,
                page_type: str | None = None) -> dict:
    """Record an explicit read outcome for one source. The status
    is whatever the host model reports — the core never infers a
    read from the URL existing (spec section 4). Every recording
    appends to the source's read_events, so a failed-then-read
    history survives (spec A2). `page_type` (Round 2, closing
    round-1 ambiguity #3) is assigned HERE, at read time — the
    moment the model has actually seen the page: one of loaders.
    SEO_PAGE_TYPES; omitting it leaves a previously set value
    alone."""
    require_mutable(profile)
    if read_status not in loaders.SEO_RECORDED_READ_STATUSES:
        raise ValueError(
            f"read_status must be one of "
            f"{loaders.SEO_RECORDED_READ_STATUSES}, got "
            f"{read_status!r} ('pending' is the initial state, "
            f"not a read outcome)")
    if not isinstance(content_truncated, bool):
        raise ValueError("content_truncated must be a boolean")
    if page_type is not None \
            and page_type not in loaders.SEO_PAGE_TYPES:
        raise ValueError(
            f"page_type must be one of {loaders.SEO_PAGE_TYPES}, "
            f"got {page_type!r}")
    source = find_source(profile, source_id)
    if page_type is not None:
        source["page_type"] = page_type
    event = {"status": read_status, "reason": str(reason or ""),
             "content_truncated": content_truncated, "at": _now()}
    source["read_events"].append(event)
    source["read_status"] = read_status
    source["read_reason"] = event["reason"]
    source["read_at"] = event["at"]
    source["content_truncated"] = content_truncated
    return source


def add_replacement(profile: dict, replaces_source_id: str, url: str,
                    observed_rank: int, reason: str,
                    title: str | None = None,
                    observed_at: str | None = None) -> dict:
    """Record a replacement for a source that could not be used.
    The ORIGINAL record is never rewritten (spec section 3.3): the
    replacement is a new record linked by replaces_source_id, with
    the rank IT was observed at (not constrained to 1..5 — it was
    found beyond the original five) and the reason it exists."""
    require_mutable(profile)
    find_source(profile, replaces_source_id)  # must exist
    if not str(reason or "").strip():
        raise ValueError(
            "a replacement source needs a reason (why the original "
            "could not be used) — an unexplained swap would rewrite "
            "the observed top-5 history")
    url = _check_url(profile, url)
    rank = _check_rank(observed_rank, original=False)
    source = _source_record(profile, url, title, rank, observed_at,
                            replaces_source_id,
                            replacement_reason=reason.strip())
    profile["sources"].append(source)
    return source


# ---------------------------------------------------------------------------
# Analysis (provenance-checked claims)
# ---------------------------------------------------------------------------

def _check_confidence(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("claim confidence must be a number in "
                         "[0, 1] or low/medium/high")
    if isinstance(value, (int, float)):
        if not 0 <= value <= 1:
            raise ValueError(
                f"claim confidence must be within [0, 1], got "
                f"{value!r}")
        return value
    if isinstance(value, str) \
            and value.strip().lower() in ("low", "medium", "high"):
        return value.strip().lower()
    raise ValueError(
        f"claim confidence must be a number in [0, 1] or "
        f"low/medium/high, got {value!r}")


def save_analysis(profile: dict, claims: list) -> tuple[dict, list[str]]:
    """Validate + store the host model's analysis claims. Rules
    (spec A4): a `sourced` claim must cite at least one source;
    every cited id must exist in the profile; a claim citing a
    source that is not read/partial is FLAGGED on the stored claim
    (and reported) — it is stored, because the analysis is the
    model's proposal, but it is never silently treated as fully
    analysed. The previous analysis (if any) moves whole into
    analysis_history. Returns (analysis, flagged_claim_ids)."""
    require_mutable(profile)
    if not isinstance(claims, list) or not claims:
        raise ValueError("claims must be a non-empty list — an "
                         "analysis with no claims is not an analysis")
    by_id = {s["source_id"]: s for s in profile.get("sources", [])}
    stored: list[dict] = []
    flagged: list[str] = []
    for i, claim in enumerate(claims):
        if not isinstance(claim, dict):
            raise ValueError(f"claim {i} must be an object")
        text = claim.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"claim {i} needs a non-empty text")
        kind = claim.get("kind")
        if kind not in loaders.SEO_CLAIM_KINDS:
            raise ValueError(
                f"claim {i} kind must be one of "
                f"{loaders.SEO_CLAIM_KINDS}, got {kind!r} — every "
                f"claim is either sourced or labelled inference "
                f"(spec A4)")
        source_ids = claim.get("source_ids") or []
        if not isinstance(source_ids, list) \
                or not all(isinstance(s, str) for s in source_ids):
            raise ValueError(
                f"claim {i} source_ids must be a list of source "
                f"id strings")
        if kind == "sourced" and not source_ids:
            raise ValueError(
                f"claim {i} is 'sourced' but cites no source — "
                f"cite at least one source or label it 'inference'")
        unknown = [s for s in source_ids if s not in by_id]
        if unknown:
            raise ValueError(
                f"claim {i} cites unknown source ids: {unknown} — "
                f"provenance must resolve to a recorded source")
        flags = [f"cites_unread_source:{sid}" for sid in source_ids
                 if by_id[sid].get("read_status")
                 not in ("read", "partial")]
        record = {"id": _new_id("claim"), "text": text.strip(),
                  "source_ids": list(source_ids), "kind": kind,
                  "confidence": _check_confidence(
                      claim.get("confidence")),
                  "flags": flags}
        if flags:
            flagged.append(record["id"])
        stored.append(record)
    previous = profile.get("analysis")
    if previous is not None:
        profile.setdefault("analysis_history", []).append(previous)
    version = (previous.get("version", 1) + 1) if previous else 1
    analysis = {"version": version, "saved_at": _now(),
                "claims": stored}
    profile["analysis"] = analysis
    if profile.get("state") == "collecting":
        profile["state"] = "analyzing"
    return analysis, flagged


# ---------------------------------------------------------------------------
# Sufficiency + shortfall exception
# ---------------------------------------------------------------------------

def read_count(profile: dict) -> int:
    """Sources usable as analysis basis. A replacement counts when
    IT was read; the failed original it replaced does not. Whether
    a PARTIAL read counts is the PARTIAL_READS_COUNT_TOWARD_
    REQUIRED policy constant (Smith's open call): True counts
    read + partial, False counts full reads only."""
    statuses = ("read", "partial") \
        if PARTIAL_READS_COUNT_TOWARD_REQUIRED else ("read",)
    return sum(1 for s in profile.get("sources", [])
               if s.get("read_status") in statuses)


def full_read_count(profile: dict) -> int:
    return sum(1 for s in profile.get("sources", [])
               if s.get("read_status") == "read")


def sufficiency(profile: dict) -> dict:
    """The shortfall report (spec A3): how many sources are read
    against the required 5, whether a shortfall stands, whether
    the owner recorded an exception, and whether proceeding is
    authorised (enough reads, or an exception over >= 1 read)."""
    count = read_count(profile)
    exception = profile.get("shortfall_exception")
    return {"read_count": count,
            "full_read_count": full_read_count(profile),
            "required_sources": loaders.SEO_REQUIRED_SOURCES,
            "shortfall": count < loaders.SEO_REQUIRED_SOURCES,
            "exception_recorded": exception is not None,
            "proceed_ready": count >= loaders.SEO_REQUIRED_SOURCES
                or (exception is not None and count >= 1)}


def record_exception(profile: dict, reason: str) -> dict:
    """Record the owner's deliberate decision to proceed with
    fewer than 5 read sources (spec section 3, 'Thiếu nguồn').
    Requires a reason and at least ONE read source — with 0 reads
    there is nothing to analyse and no exception can authorise
    proceeding. The exception does not alter the shortfall report:
    the profile is never labelled as having analysed 5 sources
    it did not read. Recording is allowed in EXCEPTION_STATES —
    wider than the input-mutation fence, because the exception is
    an authorisation, not an input change (see the constant)."""
    state = profile.get("state")
    if state not in EXCEPTION_STATES:
        raise ValueError(
            f"seo profile for draft {profile.get('draft_id')} is in "
            f"state {state!r} — a shortfall exception can only be "
            f"recorded while {'/'.join(EXCEPTION_STATES)} (once "
            f"writing has begun, an approved outline already passed "
            f"the gate; changing inputs goes through "
            f"reopen_seo_analysis)")
    if not str(reason or "").strip():
        raise ValueError(
            "a shortfall exception needs a reason — proceeding "
            "with fewer than 5 read sources is a deliberate, "
            "recorded exception, not a silent default")
    count = read_count(profile)
    if count == 0:
        raise ValueError(
            "cannot record a shortfall exception with 0 read "
            "sources — at least 1 source must have been read "
            "(spec A3)")
    exception = {"reason": reason.strip(), "confirmed_at": _now(),
                 "read_count_at_confirmation": count}
    profile["shortfall_exception"] = exception
    return exception


# ---------------------------------------------------------------------------
# Direction (Round 2, spec section 3.5 + A5)
# ---------------------------------------------------------------------------

def save_direction(profile: dict, primary_intent: str,
                   secondary_intents: list | None = None,
                   rationale_claim_ids: list | None = None) -> dict:
    """Record the model's proposed direction for the article. The
    direction names ONE primary intent — when the top 5 serve
    competing intents, the disambiguation is a decision, not an
    average (spec A5): a proposal without a primary intent is
    refused. Rationale claims must resolve to claims of the
    CURRENT analysis. Saving moves analyzing → direction_review;
    re-proposing while a direction still awaits its decision
    supersedes the pending proposal (kept in history)."""
    state = profile.get("state")
    if state not in ("analyzing", "direction_review"):
        raise ValueError(
            f"seo profile for draft {profile.get('draft_id')} is in "
            f"state {state!r} — a direction is proposed from "
            f"analyzing (save an analysis first, then the direction; "
            f"from a later state, reopen_seo_analysis is the way "
            f"back)")
    analysis = profile.get("analysis")
    if not isinstance(analysis, dict) or not analysis.get("claims"):
        raise ValueError(
            "a direction needs a saved analysis first — the "
            "direction is a reading OF the analysis, not a guess "
            "before it")
    if not isinstance(primary_intent, str) \
            or not primary_intent.strip():
        raise ValueError(
            "a direction must name ONE primary intent — when the "
            "results serve competing intents, the user chooses one "
            "(spec A5); secondary intents go in secondary_intents")
    secondary = secondary_intents if secondary_intents is not None \
        else []
    if not isinstance(secondary, list) \
            or not all(isinstance(s, str) and s.strip()
                       for s in secondary):
        raise ValueError(
            "secondary_intents must be a list of non-empty strings")
    rationale = rationale_claim_ids if rationale_claim_ids is not None \
        else []
    if not isinstance(rationale, list) \
            or not all(isinstance(c, str) for c in rationale):
        raise ValueError(
            "rationale_claim_ids must be a list of analysis claim "
            "id strings")
    known = {c["id"] for c in analysis["claims"]}
    unknown = [c for c in rationale if c not in known]
    if unknown:
        raise ValueError(
            f"direction cites unknown claim ids: {unknown} — "
            f"rationale must resolve to claims of the current "
            f"analysis (version {analysis.get('version')})")
    pending = profile.get("direction")
    if pending is not None:
        pending["status"] = "superseded"
        profile.setdefault("direction_history", []).append(pending)
    record = {"primary_intent": primary_intent.strip(),
              "secondary_intents": [s.strip() for s in secondary],
              "rationale_claim_ids": list(rationale),
              "analysis_version": analysis.get("version"),
              "proposed_at": _now(), "status": "proposed",
              "decision": None}
    profile["direction"] = record
    profile["state"] = "direction_review"
    return record


def decide_direction(profile: dict, decision: str,
                     note: str = "") -> dict:
    """Record the USER's decision on the proposed direction — the
    core never approves its own proposal (spec section 6.2).
    approved: direction_review → outline_review, the direction
    stays current with its decision recorded. rejected: the
    direction moves to history with its decision and the profile
    returns to analyzing, ready for a new proposal."""
    if decision not in ("approved", "rejected"):
        raise ValueError(
            f"decision must be 'approved' or 'rejected', got "
            f"{decision!r} — the direction is the user's call")
    direction = profile.get("direction")
    if profile.get("state") != "direction_review" \
            or not isinstance(direction, dict) \
            or direction.get("status") != "proposed":
        raise ValueError(
            f"seo profile for draft {profile.get('draft_id')} is in "
            f"state {profile.get('state')!r} with no proposed "
            f"direction awaiting a decision — propose a direction "
            f"first (save_seo_direction)")
    direction["decision"] = {"decision": decision,
                             "note": str(note or ""),
                             "decided_at": _now()}
    if decision == "approved":
        direction["status"] = "approved"
        profile["state"] = "outline_review"
    else:
        direction["status"] = "rejected"
        profile.setdefault("direction_history", []).append(direction)
        profile["direction"] = None
        profile["state"] = "analyzing"
    return direction


# ---------------------------------------------------------------------------
# Outline versions (Round 2, spec section 4 + plan Round 2)
# ---------------------------------------------------------------------------
# The profile records outline VERSIONS + approvals; it is NOT a
# second outline store. The writing artefact stays the DRAFT's
# outline field, written through make_outline — the MCP tool
# pushes the proposed sections there and mirrors the normalised
# result into the version record below, so the approved version
# can always be read back (spec A5) even after the draft moves on.

def require_outline_proposable(profile: dict) -> None:
    """The outline gate (spec section 3, 'Thiếu nguồn'): an outline
    may be proposed only in outline_review / writing (an approved
    direction stands) AND with sufficiency.proceed_ready — 5
    counted reads, or fewer under a recorded shortfall exception.
    The refusal names both ways forward."""
    state = profile.get("state")
    if state not in ("outline_review", "writing", "reviewing"):
        raise ValueError(
            f"seo profile for draft {profile.get('draft_id')} is in "
            f"state {state!r} — an outline is proposed only after "
            f"the user approves a direction (states outline_review "
            f"/ writing / reviewing; proposing from reviewing is "
            f"Round 3's outline-iteration path — the standing "
            f"review goes stale against the new version, "
            f"computably, never silently)")
    report = sufficiency(profile)
    if not report["proceed_ready"]:
        raise ValueError(
            f"cannot propose an outline yet: {report['read_count']} "
            f"of {report['required_sources']} sources read and no "
            f"shortfall exception recorded — read more sources, or "
            f"the owner records the exception "
            f"(record_shortfall_exception) to proceed with fewer "
            f"(spec A3); the profile is never labelled as having "
            f"analysed 5 sources it did not read")


def record_outline_proposal(profile: dict,
                            sections: list[dict]) -> dict:
    """Append a new outline version mirroring the normalised
    sections make_outline stored into the draft. A still-pending
    proposed version is superseded (one live proposal at a time);
    an approved version is untouched — it stays approved-current
    until a newer version is APPROVED (see approve_outline)."""
    require_outline_proposable(profile)
    outlines = profile.setdefault("outlines", [])
    for record in outlines:
        if record.get("status") == "proposed":
            record["status"] = "superseded"
    version = max((r.get("version", 0) for r in outlines),
                  default=0) + 1
    direction = profile.get("direction") or {}
    analysis = profile.get("analysis") or {}
    record = {"version": version, "sections": list(sections),
              "status": "proposed", "proposed_at": _now(),
              "approved_at": None, "content_sha_at_approval": None,
              "analysis_version": analysis.get("version"),
              "direction_primary_intent":
                  direction.get("primary_intent")}
    outlines.append(record)
    return record


def current_approved_outline(profile: dict) -> dict | None:
    """The one approved-current outline version, or None. The
    invariant — at most one — is maintained by approve_outline
    (superseding) and reopen_analysis (invalidating)."""
    for record in profile.get("outlines", []):
        if record.get("status") == "approved":
            return record
    return None


def approve_outline(profile: dict, version: int,
                    content_sha_at_approval: str | None) -> dict:
    """Approve ONE proposed outline version — the user's act,
    recorded with its timestamp (spec section 4). The previously
    approved version (if any) becomes superseded, so exactly one
    approved-current version exists at any time. Approval moves
    the profile to `writing`. `content_sha_at_approval` is the
    gates.content_sha fingerprint of the draft's content at this
    moment, computed by the caller (the record layer never loads
    drafts); Round 3's export check compares against it."""
    state = profile.get("state")
    if state not in ("outline_review", "writing", "reviewing"):
        raise ValueError(
            f"seo profile for draft {profile.get('draft_id')} is in "
            f"state {state!r} — an outline is approved from "
            f"outline_review / writing / reviewing only")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ValueError(
            f"outline version must be an int, got {version!r}")
    record = next((r for r in profile.get("outlines", [])
                   if r.get("version") == version), None)
    if record is None:
        raise ValueError(
            f"unknown outline version: {version} for draft "
            f"{profile.get('draft_id')} — proposed versions: "
            f"{[r.get('version') for r in profile.get('outlines', [])]}")
    if record.get("status") != "proposed":
        raise ValueError(
            f"outline version {version} is {record.get('status')!r} "
            f"— only a proposed version can be approved (a "
            f"superseded or invalidated version is history, never "
            f"revived)")
    previous = current_approved_outline(profile)
    if previous is not None:
        previous["status"] = "superseded"
    record["status"] = "approved"
    record["approved_at"] = _now()
    record["content_sha_at_approval"] = content_sha_at_approval
    profile["state"] = "writing"
    return record


# ---------------------------------------------------------------------------
# Invalidation (Round 2, spec A6)
# ---------------------------------------------------------------------------

def reopen_analysis(profile: dict, reason: str) -> dict:
    """The controlled way back after approvals exist (spec A6):
    return the profile to `analyzing`, marking the current
    direction, every live (proposed / approved) outline version
    and every live review record (Round 3) INVALIDATED — kept
    whole in history with the reason and
    timestamp, never deleted, never silently mutated. Superseded
    records are already history and stay as they are. Direct
    source/analysis mutation in the later states stays refused
    (require_mutable); this tool is the only door. Returns a
    summary of what was invalidated."""
    if not str(reason or "").strip():
        raise ValueError(
            "reopening the analysis needs a reason — invalidation "
            "is a recorded event in the profile's history, not a "
            "silent reset")
    state = profile.get("state")
    if state not in REOPENABLE_STATES:
        raise ValueError(
            f"seo profile for draft {profile.get('draft_id')} is in "
            f"state {state!r} — nothing to invalidate while "
            f"{'/'.join(REOPENABLE_STATES)} has not been reached "
            f"(inputs still change directly); from `exported` the "
            f"workflow is complete and is never rewound")
    reason = reason.strip()
    now = _now()
    invalidated_outlines = []
    for record in profile.get("outlines", []):
        if record.get("status") in ("proposed", "approved"):
            record["status"] = "invalidated"
            record["invalidated_at"] = now
            record["invalidation_reason"] = reason
            invalidated_outlines.append(record.get("version"))
    invalidated_reviews = []
    for review in profile.get("reviews", []):
        if not review.get("invalidated_at"):
            review["invalidated_at"] = now
            review["invalidation_reason"] = reason
            invalidated_reviews.append(review.get("id"))
    direction_invalidated = False
    direction = profile.get("direction")
    if isinstance(direction, dict):
        direction["status"] = "invalidated"
        direction["invalidated_at"] = now
        direction["invalidation_reason"] = reason
        profile.setdefault("direction_history", []).append(direction)
        profile["direction"] = None
        direction_invalidated = True
    profile.setdefault("reopen_log", []).append(
        {"reason": reason, "at": now, "from_state": state,
         "invalidated_outlines": invalidated_outlines,
         "invalidated_reviews": invalidated_reviews,
         "direction_invalidated": direction_invalidated})
    profile["state"] = "analyzing"
    return {"from_state": state,
            "invalidated_outlines": invalidated_outlines,
            "invalidated_reviews": invalidated_reviews,
            "direction_invalidated": direction_invalidated}


# ---------------------------------------------------------------------------
# Reviews + export readiness (Round 3, spec sections 6.5-6.7 + A8)
# ---------------------------------------------------------------------------
# A review is the recorded human/model pass over the WRITTEN
# article: a checklist (each item ok / issue / na, mandatory or
# not), a verdict, and — for specialist claims — an expert-review
# record naming WHO reviewed and WHEN (plan Round 3). It BINDS to
# the approved outline version and the content fingerprint at
# record time, exactly like the outline approval binds to the
# fingerprint: any later change makes it stale, computably, with
# nothing stored to go out of date.
#
# Readiness is likewise a pure function of (profile, current
# fingerprint, the F1 gate verdict) — there is deliberately NO
# stored `ready_to_export` state (round-3 notes give the reason).

def _check_expert_review(value: object) -> dict:
    """Normalise the expert_review field of a review. Absent means
    no specialist review applies. When one is required, its status
    is pending or done — never not_required — and `done` must name
    the reviewer: a specialist claim signed off by nobody is not
    signed off (spec section 6.5)."""
    if value is None:
        return {"required": False, "status": "not_required",
                "reviewer": None, "reviewed_at": None}
    if not isinstance(value, dict):
        raise ValueError("expert_review must be an object "
                         "{required, status, reviewer?}")
    required = value.get("required")
    if not isinstance(required, bool):
        raise ValueError("expert_review.required must be a boolean")
    status = value.get("status") or ("pending" if required
                                     else "not_required")
    if status not in loaders.SEO_EXPERT_REVIEW_STATUSES:
        raise ValueError(
            f"expert_review.status must be one of "
            f"{loaders.SEO_EXPERT_REVIEW_STATUSES}, got {status!r}")
    reviewer = value.get("reviewer")
    reviewer = reviewer.strip() if isinstance(reviewer, str) else None
    if not required and status != "not_required":
        raise ValueError(
            "expert_review with required=False cannot carry status "
            f"{status!r} — either a specialist review applies or "
            f"it does not")
    if required and status == "not_required":
        raise ValueError(
            "expert_review.required=True contradicts status "
            "'not_required' — a required review is pending until "
            "it is done")
    if status == "done" and not reviewer:
        raise ValueError(
            "expert_review status 'done' must name the reviewer — "
            "specialist claims are signed off by a person, and the "
            "record shows who and when (spec section 6.5)")
    return {"required": required, "status": status,
            "reviewer": reviewer,
            "reviewed_at": _now() if status == "done" else None}


def _check_review_items(items: object) -> list[dict]:
    if not isinstance(items, list) or not items:
        raise ValueError("review items must be a non-empty list — "
                         "a review with no checklist is not a review")
    stored: list[dict] = []
    seen: set[str] = set()
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"review item {i} must be an object")
        key = item.get("key") or item.get("id")
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"review item {i} needs a non-empty "
                             f"key (or id)")
        key = key.strip()
        if key in seen:
            raise ValueError(
                f"duplicate review item key: {key!r} — each "
                f"checklist entry is recorded once")
        seen.add(key)
        label = item.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"review item {key!r} needs a "
                             f"non-empty label")
        status = item.get("status")
        if status not in loaders.SEO_REVIEW_ITEM_STATUSES:
            raise ValueError(
                f"review item {key!r} status must be one of "
                f"{loaders.SEO_REVIEW_ITEM_STATUSES}, got "
                f"{status!r}")
        mandatory = item.get("mandatory", False)
        if not isinstance(mandatory, bool):
            raise ValueError(f"review item {key!r} mandatory must "
                             f"be a boolean")
        note = item.get("note")
        stored.append({"key": key, "label": label.strip(),
                       "status": status, "mandatory": mandatory,
                       "note": note.strip()
                       if isinstance(note, str) else ""})
    return stored


def record_review(profile: dict, items: list, verdict: str,
                  expert_review: dict | None,
                  content_sha: str) -> dict:
    """Record one review of the written article. Binds to the
    CURRENT approved outline version and to `content_sha` — the
    gates.content_sha fingerprint of the draft's content at record
    time, computed by the caller (the record layer never loads
    drafts). The first review moves writing → reviewing; later
    reviews append (the latest live one is the current one).
    Reviews are never edited in place: a fix earns a NEW review,
    and reopen_analysis is what invalidates them (spec A6)."""
    state = profile.get("state")
    if state not in REVIEWABLE_STATES:
        raise ValueError(
            f"seo profile for draft {profile.get('draft_id')} is in "
            f"state {state!r} — a review is recorded while "
            f"{'/'.join(REVIEWABLE_STATES)} (an approved outline "
            f"stands and the article is written)")
    approved = current_approved_outline(profile)
    if approved is None:
        raise ValueError(
            "a review binds to an approved outline version, but "
            "this profile has none — approve an outline first")
    if verdict not in loaders.SEO_REVIEW_VERDICTS:
        raise ValueError(
            f"review verdict must be one of "
            f"{loaders.SEO_REVIEW_VERDICTS}, got {verdict!r} — "
            f"the review is a decision, recorded as such")
    record = {"id": _new_id("rev"), "recorded_at": _now(),
              "outline_version": approved.get("version"),
              "content_sha": content_sha,
              "items": _check_review_items(items),
              "verdict": verdict,
              "expert_review": _check_expert_review(expert_review),
              "invalidated_at": None, "invalidation_reason": None}
    profile.setdefault("reviews", []).append(record)
    if state == "writing":
        profile["state"] = "reviewing"
    return record


def current_review(profile: dict) -> dict | None:
    """The latest review that has not been invalidated, or None."""
    for review in reversed(profile.get("reviews", [])):
        if not review.get("invalidated_at"):
            return review
    return None


def review_status(profile: dict, current_sha: str | None) -> dict:
    """Is there a CURRENT review, and if not, exactly why not?
    A review is current only while the approved outline version
    it bound to is still the approved one AND the content
    fingerprint still matches what it reviewed (spec A6). Open
    mandatory items and a pending expert review are reported
    alongside — they block readiness, not currency."""
    review = current_review(profile)
    if review is None:
        recorded = bool(profile.get("reviews"))
        return {"exists": False, "current": False, "review_id": None,
                "verdict": None, "outline_version": None,
                "stale_reasons": ["review_invalidated"] if recorded
                else ["no_review_recorded"],
                "open_mandatory_items": [], "expert_blocking": False}
    stale: list[str] = []
    approved = current_approved_outline(profile)
    if approved is None \
            or review.get("outline_version") != approved.get("version"):
        stale.append("review_stale_outline")
    if not current_sha or review.get("content_sha") != current_sha:
        stale.append("review_stale_content")
    expert = review.get("expert_review") or {}
    return {"exists": True, "current": not stale,
            "review_id": review.get("id"),
            "verdict": review.get("verdict"),
            "outline_version": review.get("outline_version"),
            "stale_reasons": stale,
            "open_mandatory_items": [
                item["key"] for item in review.get("items", [])
                if item.get("mandatory")
                and item.get("status") == "issue"],
            "expert_blocking": bool(expert.get("required"))
                and expert.get("status") != "done"}


def export_readiness_reasons(profile: dict, current_sha: str | None,
                             gate_reason: str | None,
                             gate_detail: str = "") -> list[dict]:
    """THE export-readiness decision for an SEO draft (spec
    section 6.6): every unmet condition as a named reason
    {code, detail} — an empty list means ready. Pure: the caller
    supplies the draft's current fingerprint and the F1 gate
    verdict (`gate_reason` is the _export_gate_refusal code, or
    None when the gate condition holds) so this module keeps its
    never-loads-drafts rule and the F1 logic is reused, never
    duplicated."""
    reasons: list[dict] = []

    def add(code: str, detail: str, **extra: object) -> None:
        reasons.append({"code": code, "detail": detail, **extra})

    state = profile.get("state")
    if state not in EXPORTABLE_STATES:
        add("state_not_exportable",
            f"profile is in state {state!r} — an SEO draft exports "
            f"from {'/'.join(EXPORTABLE_STATES)} (approved outline "
            f"recorded, article written, review standing)")
    approved = current_approved_outline(profile)
    if approved is None:
        add("no_approved_outline",
            "no approved outline version stands — approve an "
            "outline before exporting (spec A5/A8)")
    elif approved.get("content_sha_at_approval") != current_sha:
        add("edited_after_outline_approval",
            "the content changed after the outline approval was "
            "stamped (fingerprint mismatch) — re-approve an "
            "outline against the current content (spec A6)")
    status = review_status(profile, current_sha)
    if not status["exists"]:
        add(status["stale_reasons"][0],
            "no review stands for this article — record one "
            "(record_seo_review) against the current content"
            if status["stale_reasons"][0] == "no_review_recorded"
            else "the recorded review was invalidated — record a "
                 "new review against the current content")
    elif not status["current"]:
        for code in status["stale_reasons"]:
            add(code,
                "the standing review was recorded against outline "
                "version "
                f"{status['outline_version']} / an earlier content "
                "fingerprint — record a new review against the "
                "current outline and content (spec A6)")
    else:
        if status["open_mandatory_items"]:
            add("open_mandatory_items",
                "mandatory review items still marked 'issue': "
                + ", ".join(status["open_mandatory_items"]),
                items=list(status["open_mandatory_items"]))
        if status["expert_blocking"]:
            add("expert_review_pending",
                "a specialist (expert) review is required and has "
                "not been recorded as done — automated scores never "
                "substitute for a qualified reviewer (spec 6.5)")
        if status["verdict"] != "approved":
            add("review_changes_requested",
                "the standing review's verdict is "
                f"{status['verdict']!r}, not 'approved' — fix the "
                f"article and record a new review")
    if gate_reason:
        add(gate_reason,
            gate_detail or "the final_gate condition does not hold "
                           "for the current content (fix round F1)")
    return reasons


def mark_exported(profile: dict, wp_post_id: int,
                  site_url: str) -> dict:
    """Record a completed export on the profile: state →
    `exported`, with the post id + site + timestamp kept as the
    historical fact of the shipment (the draft carries the full
    wordpress_export record; this is the profile's own trace)."""
    record = {"wp_post_id": int(wp_post_id), "site_url": site_url,
              "exported_at": _now()}
    profile["export"] = record
    profile["state"] = "exported"
    return record
