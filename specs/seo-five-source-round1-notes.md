# SEO five-source — Round 1 implementation notes

Round 1 of `seo-five-source-implementation-plan.md`: the per-draft
SEO profile store + source management + provenance-checked
analysis. Core only — no outline/direction (Round 2), no
export-readiness wiring (Round 3), no app UI (Round 4). Built
2026-11-23 against core baseline `f993ad5` (72 tools → **79**).

## What landed

- Store: `data/seo_profiles/<draft-stem>.json`, one profile per
  `draft_id`, in loaders' house pattern (`CONTENT_CORE_SEO_PROFILES_DIR`
  env override, atomic writes, full `validate_seo_profile` on save
  and load). Record logic in `content_core/seo_profile.py`; the MCP
  tools load → mutate → save, serialised by the per-draft lock
  (`_draft_lock` gained a `directory` parameter so SEO mutations
  lock inside the seo_profiles dir, not the drafts dir).
- 7 tools: `create_seo_profile`, `get_seo_profile`,
  `add_seo_source`, `record_source_read`,
  `add_replacement_source`, `save_seo_analysis`,
  `record_shortfall_exception`.
- Backup: `seo_profiles` added to `backup.py`'s store list, so
  export includes it and restore validates it like every other
  store (spec A9; round-trip pinned by test).

## Workflow transition table (Round 1 rows only)

States (spec §4): collecting → analyzing → direction_review →
outline_review → writing → reviewing → ready_to_export → exported.

| From | Trigger | To | Round |
|---|---|---|---|
| — (create) | `create_seo_profile` | `collecting` | 1 |
| `collecting` | `save_seo_analysis` (first save) | `analyzing` | 1 |
| `analyzing` | `save_seo_analysis` (re-save) | `analyzing` (analysis version +1, previous kept in `analysis_history`) | 1 |
| `analyzing` | direction saved + approved | `direction_review` | 2 (not built) |
| … | … | … | 2–3 (not built) |

Round 2 has since landed — its transition table, the
partial-counts policy constant (ambiguity #1, still Smith's call,
currently True), the `page_type` owner (ambiguity #3: assigned in
`record_source_read`), and the invalidation semantics
(ambiguity #4: resolved via `reopen_seo_analysis`) are recorded
in `seo-five-source-round2-notes.md`.

Mutation guard: source, read, replacement, analysis and exception
mutations are allowed only while the profile is `collecting` or
`analyzing`. In any later state the tools refuse with a structured
error naming the state — later rounds own the invalidation rules
(spec A6) and a silent edit there would bypass them. No Round 1
tool moves a profile past `analyzing`.

## Design choices where the spec left room

1. **Search context = exactly the 5 spec fields, all required,
   no defaults.** Spec §9 leaves default market/device/language
   open, so the conservative option is taken: the core invents no
   provenance. Unknown extra fields are refused (a recorded
   context must be exactly what the user observed). `searched_at`
   gets a light ISO date/datetime parse check.
2. **Brand is pinned from the draft's brief.** A brief without a
   `brand_id` is refused rather than defaulted (the house rule:
   unknown brand raises, never a silent fallback). The optional
   `brand_id` argument exists only so a caller can assert the
   expectation; a mismatch is a structured error.
3. **One profile per draft.** Re-creating is refused ("already
   exists") rather than version-bumping the profile itself — the
   profile's `version` field is a store revision counter
   (bumped on every save), distinct from the analysis `version`.
4. **`read_count` counts read + partial; partial-ness stays
   visible.** Spec §3 says the workflow needs 5 sources "đã đọc"
   (read). A partial read is real analysis material (with the
   truncation flagged per source), so it counts toward
   sufficiency — but `get_seo_profile` also reports
   `full_read_count` (status `read` only), and every partial
   source keeps its `content_truncated` flag. See ambiguity #1.
5. **The shortfall exception authorises proceeding; it does not
   clear the shortfall.** `sufficiency.shortfall` stays `true`
   after an exception; `proceed_ready` is the field the Round 2
   outline gate should consume. The profile is never labelled as
   having analysed 5 sources it did not read (spec §3).
6. **Replacement rank semantics.** A replacement carries the rank
   at which IT was observed — a positive int, NOT capped at 5,
   because it was found beyond the original five. The original
   keeps its own rank and record untouched (the core never even
   auto-marks it excluded; that is the model's explicit
   `record_source_read` call to make).
7. **Claim confidence typing.** The spec names `confidence`
   without a type. Accepted: a number in [0, 1] or one of
   `low` / `medium` / `high`; anything else is refused at save.
   See ambiguity #2.
8. **`page_type` is schema-only in Round 1.** The field exists on
   every source record (spec §4) but no Round 1 tool sets it —
   it stays `null`. Setting it belongs with the analysis step's
   UI/model contract (Round 2/4). See ambiguity #3.
9. **Claims citing unread sources are stored WITH a flag, not
   refused.** Spec A4 requires the flag (stored analysis +
   response). Only two things refuse a save outright: a `sourced`
   claim citing zero sources, and citing an unknown source id.
10. **Read events are append-only.** Every `record_source_read`
    appends `{status, reason, content_truncated, at}` to the
    source's `read_events` and updates the current-status fields —
    a failed-then-read history survives whole (spec A2).

## Corrupt-record behaviour

House pattern, stated explicitly: `get_seo_profile` (and every
mutating tool) surfaces a corrupted profile file as a structured
`{"error": "corrupt JSON in stored file …"}` naming the file —
never a crash, never a silent empty profile. `list_seo_profiles`
marks the entry `corrupt` in listings instead of dropping it.
`create_seo_profile` over a corrupt existing file returns the
same corrupt error rather than overwriting evidence.

## Flagged ambiguities in the spec (for Smith / later rounds)

1. **Does a partial read count toward the required 5?** Spec §3
   says "đủ 5 nguồn đã đọc"; §4 defines `partial` as a first-class
   read status. Round 1 counts partial toward 5 and exposes
   `full_read_count` alongside, so a stricter Round 2 gate can
   tighten this without a data migration. Needs a decision
   before the outline gate (Round 2) is finalised.
2. **Confidence has no declared type or scale** in spec §4.
   Round 1 accepts [0, 1] numbers or low/medium/high. If the
   trial (Round 5) shows one form dominating, narrow the schema.
3. **`page_type` has no writer.** Spec §4 lists it as a source
   field but no step in §3 assigns it. Round 1 stores `null`;
   Round 2 (analysis/direction) or Round 4 (UI) needs to own it.
4. **Analysis re-save vs. state.** The spec's invalidation rules
   (A6) cover outline/brief/source changes invalidating
   approvals; whether re-saving the ANALYSIS after direction
   approval must invalidate the direction is a Round 2 question —
   Round 1's mutation guard (collecting/analyzing only) makes
   the question moot until then, deliberately.

## Acceptance-criteria coverage (tests/test_seo_five_source.py, 23 tests)

- **A1** — create persists keyword/context/state/brand and a
  reopen returns them; brand mismatch, brandless brief, unknown
  draft, duplicate create, each missing/blank context field all
  refused.
- **A2** — 5 originals keep rank/time/`entry_source: manual` and
  stay `pending`; duplicate rank / rank 0, 6, −1 / duplicate URL
  refused; failed read + replacement keep full history with the
  original record byte-untouched; read outcomes append events
  (failed → read sequence preserved); `pending`/`done` refused
  as read outcomes.
- **A3** — shortfall reported at 3 reads; 5 reads (incl. one
  partial) sufficient with `full_read_count` = 4; exception
  refused at 0 reads and on blank reason; a recorded exception
  keeps `shortfall: true`, stores `read_count_at_confirmation`,
  and sets `proceed_ready`.
- **A4** — sourced-without-sources refused; unknown kind
  refused; inference without sources accepted; citing a pending
  source flags the claim in the response AND in the stored
  record; unknown source id refused; first save moves state to
  `analyzing`; re-save versions the analysis and archives v1.
- **A7** — the full tool flow runs against seeded facts / brand
  draft / corpus registry / corpus choice / taste / drafts /
  style profiles / research packs stores; all are byte-identical
  afterwards.
- **A9** — backup export includes the `seo_profiles` store;
  delete + restore (no-confirm path for a missing file) brings
  the profile back byte-identical and readable.
- Plus: the later-state mutation guard, and corrupt-file
  behaviour (structured error + corrupt listing mark).

## What Round 2 needs from this foundation

- Consume `sufficiency.proceed_ready` (+ the recorded exception)
  as the outline gate's input; decide ambiguity #1 first.
- The direction/intent model can cite analysis claim ids
  (`claim-<hex>`) — claims are stable, versioned, and the
  previous analysis survives in `analysis_history`.
- Outline versioning should link to `make_outline` the way the
  analysis links here: server-assigned ids, prior versions kept,
  approvals recorded on the profile.
- Invalidation (A6): source/read/analysis mutations are already
  fenced to collecting/analyzing; Round 2 must define what
  happens to direction/outline approvals when it widens that
  fence (e.g. allowing a source swap during writing).
- `page_type` still needs an owner (ambiguity #3).
