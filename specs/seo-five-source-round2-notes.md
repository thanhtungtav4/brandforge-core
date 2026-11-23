# SEO five-source — Round 2 implementation notes

Round 2 of `seo-five-source-implementation-plan.md`: direction +
the user's decision, the sufficiency-gated versioned outline, and
predictable invalidation. Core only — no review/export-readiness
wiring (Round 3), no app UI (Round 4). Built 2026-11-23 on the
Round 1 foundation (commit `6826d53`, 79 tools → **84**).

## What landed

- 5 new tools: `save_seo_direction`,
  `record_direction_decision`, `propose_seo_outline`,
  `approve_seo_outline`, `reopen_seo_analysis`. Record logic in
  `content_core/seo_profile.py`, validation extended in
  `loaders.validate_seo_profile` (all Round 2 keys optional on
  load — a Round 1 profile file still validates, spec A9).
- `record_source_read` gained the optional `page_type` parameter
  (fixed vocabulary in `loaders.SEO_PAGE_TYPES`: article /
  product / category / video / tool / other), closing round-1
  ambiguity #3: the type is assigned at read time, the moment the
  model has actually seen the page; omitting it on a later read
  event leaves a set value alone.

## Workflow transition table (Round 2 rows)

| From | Trigger | To |
|---|---|---|
| `analyzing` | `save_seo_direction` | `direction_review` |
| `direction_review` | `save_seo_direction` (re-proposal) | `direction_review` (pending direction → history as `superseded`) |
| `direction_review` | `record_direction_decision(approved)` | `outline_review` |
| `direction_review` | `record_direction_decision(rejected)` | `analyzing` (direction → history as `rejected`, decision kept) |
| `outline_review` | `propose_seo_outline` (gate passed) | `outline_review` (new version `proposed`) |
| `outline_review` / `writing` | `approve_seo_outline` | `writing` |
| `writing` | `propose_seo_outline` (gate passed) | `writing` (new version `proposed`; approved-current untouched) |
| `direction_review` / `outline_review` / `writing` | `reopen_seo_analysis(reason)` | `analyzing` (approvals invalidated, history kept) |

## The outline gate and the partial-counts policy

`propose_seo_outline` refuses unless the state is
`outline_review` / `writing` (an approved direction stands) AND
`sufficiency.proceed_ready` holds. The refusal names both ways
forward: read more sources, or the owner records the exception.

Whether a PARTIAL read counts toward the required 5 is round-1
ambiguity #1, still Smith's call. It now runs through ONE named
policy constant, `PARTIAL_READS_COUNT_TOWARD_REQUIRED` in
`content_core/seo_profile.py`, currently **True** (the Round 1
behaviour — `read_count` = read + partial). `read_count` and
`sufficiency` both consume it, so flipping it to False is a
one-line change that tightens every consumer at once (the
exception's ≥1-read floor included); `full_read_count` always
reports full reads only. The gate tests exercise the rule through
the constant both ways (4 read + 1 partial: ready under True,
refused under False).

## Direction semantics (A5)

- A proposal must name ONE `primary_intent` (non-empty) —
  competing intents are the user's choice, not an average.
  `secondary_intents` are recorded alongside, never merged into
  the primary. The core cannot detect "competing intents" itself:
  analysis claims are free text, so detection stays the model's /
  UI's job; the core enforces the shape of the decision.
- `rationale_claim_ids` must resolve to claims of the CURRENT
  analysis; the direction record stamps the analysis version it
  rests on.
- The decision is the user's: `record_direction_decision` stores
  `{decision, note, decided_at}` on the direction record itself,
  so an approval (or rejection) is readable back whole, forever —
  in `direction` while current, in `direction_history` after.

## Outline versions — linked, not duplicated

The draft remains the writing artefact: `propose_seo_outline`
pushes the sections through `make_outline` (same validation, same
normalisation, ids `s1..sN`) into the draft's `outline` field,
and the profile's version record MIRRORS the normalised sections
so any version — approved, superseded, invalidated — can be read
back exactly as proposed (A5). There is no second outline store
and no second source of truth: the profile owns versions +
approvals, the draft owns the live outline.

Invariants, enforced in the record layer and re-checked by
`loaders.validate_seo_profile` on every save/load:

- At most ONE live `proposed` version (a re-proposal supersedes
  the pending one) and exactly-at-most-one `approved` version
  (approving supersedes the previous approved). Validation
  refuses a stored profile with two approved versions.
- Only a `proposed` version can be approved; superseded and
  invalidated versions are history and can never be revived.
- `approve_seo_outline` stamps the version with
  `content_sha_at_approval` — the `gates.content_sha` fingerprint
  of the draft's content (sections + exporter title) at approval
  time, computed by the tool (the record layer never loads
  drafts). At approval the draft normally has no written sections
  yet, so the stamp is the fingerprint of the empty/partial
  content; Round 3 compares the current fingerprint against it
  to detect post-approval edits.

## Invalidation semantics as implemented (A6)

- Direct mutation stays fenced: source / read / replacement /
  analysis mutations refuse outside `collecting` / `analyzing`,
  with the current state named in the error. Nothing about an
  approved direction or outline can change silently.
- The one deliberate exception to the fence: the SHORTFALL
  EXCEPTION became recordable in `direction_review` /
  `outline_review` too (`EXCEPTION_STATES`). Rationale: the
  exception is the owner's authorisation, not an input change —
  it alters no source, claim or approval, and the natural moment
  to record it is at the outline gate itself, which may only be
  reached after direction approval. In `writing` and beyond it
  stays refused (an approved outline already passed the gate).
  Round 1 behaviour in collecting/analyzing is unchanged.
- `reopen_seo_analysis(draft_id, reason)` is the controlled way
  back from `direction_review` / `outline_review` / `writing`:
  the current direction and every live (`proposed` / `approved`)
  outline version are marked `invalidated` with
  `invalidated_at` + `invalidation_reason`, kept whole (the
  direction moves to `direction_history` WITH its recorded
  decision), a `reopen_log` entry records
  `{reason, at, from_state, invalidated_outlines,
  direction_invalidated}`, and the state returns to `analyzing`.
  Superseded records are already history and are not touched.
  From `analyzing` / `collecting` reopening is refused (nothing
  to invalidate — inputs still change directly); from
  `reviewing` / `ready_to_export` / `exported` it is refused for
  now — those states are Round 3's, and their invalidation rules
  (post-gate edits killing export readiness) land there.
- Editing the DRAFT's content after outline approval does not
  change the profile state in this round, by design: the
  `content_sha_at_approval` stamp is the hook Round 3's
  export-readiness decision compares against.
- Round-1 ambiguity #4 is thereby resolved: re-saving the
  ANALYSIS after direction approval is impossible except via
  reopen, which invalidates the direction — so an analysis can
  never quietly change under an approved direction.

## Acceptance-criteria coverage (tests/test_seo_five_source_round2.py, 22 tests)

- **A5** — direction without a primary intent refused (state
  unmoved); direction before any analysis refused; proposal →
  `direction_review` with claim citations (unknown claim id
  refused); user approval recorded with note + timestamp and
  readable back; rejection returns to `analyzing` with the
  rejected direction + decision kept in history, and a fresh
  direction can follow; invalid decision values refused.
- **Outline gate** — propose refused at 3 reads with no exception
  (error names the shortfall/exception paths, no version
  recorded); allowed with a recorded exception; the
  partial-counts rule exercised through the policy constant in
  both positions.
- **Outline versions** — proposal mirrors sections and links
  them into the draft's outline (ids `s1..sN`); section
  validation (empty list, missing heading, boolean target_words);
  approval records version + timestamp + the exact
  `content_sha` of the untouched draft; re-proposal supersedes
  the pending version; a new approval supersedes the previous
  approved — exactly one approved-current at every step;
  approving an unknown or superseded version refused; proposing
  from `analyzing` refused with the state named.
- **A6** — add/read/analysis/exception mutations all refused in
  `writing` with the state named; reopen requires a reason,
  invalidates the direction (decision preserved in history) and
  the approved outline, logs the event, returns to `analyzing`;
  the invalidated version can never be approved; inputs are
  mutable again and the full flow re-runs to a new approved
  version; reopen from `direction_review` works; reopen from
  `analyzing` refused.
- **page_type** — invalid value refused naming the vocabulary;
  assigned on read; a later read without it leaves it alone;
  readable back via `get_seo_profile`.
- **A7 re-check** — the full Round 2 flow (including reopen and
  a second approval) leaves the seeded facts / brand draft /
  corpus registry / corpus choice / taste / style / research-pack
  stores byte-identical; the draft itself changes ONLY in its
  `outline` field (the sanctioned make_outline link) — brief,
  sections, gate history and status compared equal.

## What Round 3 needs from this foundation

- The export-readiness decision can read: state, the
  approved-current outline version (`current_approved_outline`),
  and its `content_sha_at_approval` vs the draft's current
  `gates.content_sha(sections, title)` — a mismatch means the
  content changed after approval (A6's second half).
- Review records (Round 3) should bind to the approved outline
  version + content fingerprint the same way.
- `REOPENABLE_STATES` deliberately excludes `reviewing` /
  `ready_to_export` / `exported`; Round 3 extends invalidation
  to those states when it defines post-review edit rules.
- Legacy drafts (no SEO profile) keep the F1/F2 export path —
  nothing in Round 2 touches the exporter.

---

**Round 3 addendum (2026-11-23):** `REOPENABLE_STATES` now
includes `reviewing` (reopen invalidates reviews too), and
outline proposal/approval are allowed from `reviewing` as the
outline-iteration path — the standing review goes stale against
the new version, computably. Both changes are recorded in
`seo-five-source-round3-notes.md`.
