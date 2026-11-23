# SEO five-source — Round 3 implementation notes

Round 3 of `seo-five-source-implementation-plan.md`: review
records, ONE core export-readiness decision, and its enforcement
in the WordPress exporter. Core only — no app UI (Round 4).
Built 2026-11-23 on the Round 2 foundation (commit `e7a291f`,
84 tools → **86**).

## What landed

- 2 new tools: `record_seo_review`,
  `get_seo_export_readiness`. Record logic in
  `content_core/seo_profile.py` (`record_review`,
  `review_status`, `export_readiness_reasons`, `mark_exported`);
  validation extended in `loaders.validate_seo_profile` (the
  `reviews` / `export` keys are optional on load — a Round 1/2
  profile file still validates, spec A9).
- `get_seo_profile` now also returns `review_status` (computed
  against the draft's current fingerprint).
- `export_draft_to_wordpress` enforces readiness for drafts WITH
  an SEO profile and stamps the profile `exported` on success —
  including on the F2 adoption path. Drafts WITHOUT a profile
  are untouched: the F1/F2 path is byte-identical (pinned by the
  existing exporter suite plus an exact success-key-set test in
  the round-3 file).

## Review records

A review = `{id, recorded_at, outline_version, content_sha,
items[], verdict, expert_review, invalidated_at,
invalidation_reason}`.

- **Binding.** `outline_version` is the approved-current version
  at record time; `content_sha` is `gates.content_sha` of the
  draft's sections + exporter title at record time, computed by
  the tool (the record layer never loads drafts — the same split
  as outline approval). A review is CURRENT only while both
  still match; currency is computed (`review_status`), never
  stored, so it cannot go stale silently.
- **Items.** `{key (or id), label, status: ok|issue|na,
  mandatory: bool, note?}`; keys unique, list non-empty.
  Mandatory items with status `issue` are OPEN items: they block
  readiness and are reported BY KEY. Non-mandatory issues are
  recorded but do not block.
- **Verdict.** `approved` | `changes_requested`. A standing
  current review whose verdict is not `approved` blocks
  readiness (`review_changes_requested`) — the review is the
  recorded human decision, not a comment.
- **Expert review.** `{required, status:
  not_required|pending|done, reviewer?, reviewed_at}` for
  specialist claims (spec §6.5, plan Round 3: the record shows
  WHO and WHEN). `done` without a reviewer is refused;
  `required=True` with `not_required` is refused as a
  contradiction. Required + not done blocks readiness
  (`expert_review_pending`) — model scores never substitute for
  a qualified reviewer.
- Reviews are append-only. A fix earns a NEW review; the old one
  stays readable. The first review moves `writing` →
  `reviewing`.

## The readiness decision — and the eligible states

`get_seo_export_readiness(draft_id)` returns `{ready, reasons}`.
Eligible states: **`writing` and `reviewing`** — the two states
in which an approved outline stands and the article exists to be
checked. Every other state earns `state_not_exportable`.

### Reasons catalogue

| Code | Meaning |
|---|---|
| `state_not_exportable` | state not in writing / reviewing |
| `no_approved_outline` | no approved-current outline version |
| `edited_after_outline_approval` | current fingerprint ≠ the approved version's `content_sha_at_approval` |
| `no_review_recorded` | no review has ever been recorded |
| `review_invalidated` | reviews exist but all were invalidated (reopen) |
| `review_stale_outline` | the standing review bound to a superseded outline version |
| `review_stale_content` | the standing review bound to an earlier content fingerprint |
| `open_mandatory_items` | mandatory checklist items still `issue` (reason carries `items: [keys]`) |
| `expert_review_pending` | expert review required, not done |
| `review_changes_requested` | standing review's verdict is not `approved` |
| `never_ran_gate` / `gate_failed` / `edited_after_pass` / `pass_unverifiable` | the F1 gate codes, verbatim |

The gate part is NOT re-implemented: the tool calls the
exporter's own `_export_gate_refusal` and passes its
reason/detail into the decision. One gate rule, one export
rule — the readiness decision and the exporter cannot drift
apart.

### The supported writing order (fingerprint semantics)

Outline approval stamps the fingerprint of the content AT
APPROVAL TIME (Round 2). Writing after approval therefore
changes the fingerprint, and readiness reports
`edited_after_outline_approval` until an outline is approved
against the final content. Two supported orders:

1. propose → write the sections → approve (the stamp describes
   the final text) → gate → review → export; or
2. approve → write → propose + approve a new version against
   the final text → gate → review → export.

This is deliberate, not a quirk: the approval that readiness
trusts must cover the text that ships (spec A6 — an approval
silently stretched over later edits is exactly what the
fingerprint exists to prevent).

## Enforcement in the exporter

Order inside `export_draft_to_wordpress` for a draft WITH a
profile: existing checks (target, brand, duplicate, credential)
→ profile load (a CORRUPT profile refuses the export — an
unreadable workflow record is never shipped past) → F2 adoption
(a completed earlier attempt is adopted as before; adoption
also stamps the profile `exported`, since the export it
completes was readiness-checked when its attempt was recorded)
→ **readiness refusal** `{error, reason: "seo_not_ready",
reasons: [...]}` before any WordPress write and before a new
attempt is recorded → F1 gate check (unchanged; for SEO drafts
it is already implied by readiness) → the unchanged F2 flow.
On success the profile is stamped via a fresh load under the
SEO store's lock (`export = {wp_post_id, site_url,
exported_at}`, state `exported`); a stamp failure degrades to a
warning on the export result, never to a failed export — the
post exists by then, and the draft's `wordpress_export` record
remains the primary trace.

**Force re-export of an already-exported SEO draft** is refused
by the state rule (`exported` is not exportable, and reopen
from `exported` is refused): the workflow for that article is
complete. This is a deliberate conservative choice under spec
§6.6 — a new article is a new draft. (Non-SEO force behaviour
is unchanged.)

## No stored `ready_to_export` state — deviation, justified

Spec §4 lists `ready_to_export` in the state chain. Round 3
does NOT introduce it as a stored state, because readiness is a
pure function of live bindings (state, the approved outline +
its stamp, the standing review's bindings, the gate history):
a stored flag would be a second source of truth that every
edit path would have to remember to clear — precisely the
failure mode A6 exists to prevent ("không tin trạng thái nút
UI", plan Round 3). Computed readiness can never be stale; the
UI (Round 4) calls `get_seo_export_readiness` and displays the
decision. `exported` IS stored — it is a historical fact (a
post exists), not a derived condition.

## Invalidation extension (A6)

`REOPENABLE_STATES` gains `reviewing`: reopening there
invalidates the live reviews as well (each gains
`invalidated_at` + `invalidation_reason`, kept whole; the
reopen_log entry gains `invalidated_reviews: [ids]`, and the
tool result reports them). `exported` stays refused. A draft
edit after review does NOT mutate the profile (house pattern):
readiness simply reports the fingerprint mismatches.

## State-machine widening found by the tests

Round 2 fenced outline proposal/approval to
`outline_review`/`writing`. With `reviewing` a real state, that
fence made the review-stale-outline path unreachable except by
approving a pre-proposed version. Round 3 widens both to
`reviewing`: proposing from reviewing is the outline-iteration
path (the standing review goes stale against the new version,
computably), and approving the new version returns the profile
to `writing`, ready for a fresh review.

## Outcome-unknown on the SEO path

F2's slug adoption already covered the create call; the round-3
test proves it end-to-end for an SEO draft: the first call
creates the post server-side but loses the response → error,
profile still `reviewing`, no export stamp; the retry adopts
the SAME post (exactly one post, one create call) and only then
is the profile stamped `exported`. No gap was found — the test
is the deliverable, plus the adoption-path stamp above.

## Acceptance-criteria coverage (tests/test_seo_five_source_round3.py, 19 tests)

- **Review binding/currency** — binds outline version +
  fingerprint; `review_status` current; validation (empty
  items, bad status/verdict, duplicate keys, review before
  writing); expert rules (done needs a reviewer, required +
  not_required refused, who + when recorded).
- **A8 readiness naming** — early profile reports
  state/outline/review/gate reasons together; each single
  blocker isolated: no review, gate never ran, open mandatory
  item BY KEY (non-mandatory issue does not block), expert
  pending (cleared by a later done review), changes_requested,
  stale outline (fresh review restores), post-edit fingerprints
  (`edited_after_outline_approval` + `review_stale_content` +
  `edited_after_pass` together).
- **A8 enforcement** — refusal is `seo_not_ready` with the
  reasons list and ZERO requests reach the mock WP; success
  exports a draft post and stamps the profile; outcome-unknown
  retry adopts without duplicating and stamps only on adoption.
- **A6 extension** — reopen from `reviewing` invalidates the
  review (history whole, log entry carries the ids); reopen
  from `exported` refused.
- **Non-SEO differential** — exact pre-Round-3 success key set
  for a profile-less draft; the full F1/F2 corpus in
  tests/test_wordpress.py stays green unchanged.

## What Round 4 (app UI) needs from this

- `get_seo_export_readiness` IS the export panel's data source:
  render `ready` + the reasons list verbatim (code + detail);
  never re-derive readiness in the UI.
- `record_seo_review` takes the checklist as data — the UI
  supplies items/verdict/expert fields; the review form should
  show the binding (outline version + a "nội dung đã đổi từ lần
  review trước" signal comes free from `review_status`).
- `get_seo_profile` exposes `reviews[]`, `review_status` and
  `export` — enough to render the review history and the
  exported state without extra tools.
- The writing-order note above matters for UX copy: the flow
  should present outline approval as confirmable against the
  written text (approve after writing, or re-approve), not as a
  one-way gate before writing starts.
