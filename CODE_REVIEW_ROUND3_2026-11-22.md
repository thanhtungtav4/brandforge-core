# Code Review — Round 3 (2026-11-22)

Method: awesome-code-review skill (phases 1–5, two axes), run as part of the
skills application run. Scope: the code added since round 2's fix commit —
`f9f00bb..1931076` (clear-corpus-choice excluded as already reviewed at build
time; quality uplift `925911c`, AI image tagging `5750c26`, Setup Assist
`1931076`) read DEEP (every changed production file in full), plus a read-only
sample of brandforge-app at `63685bc`. Spec axis: pmdecisions.md + the module
docstrings' stated hard rules. Every Suggestion below was verified with a
live repro against tmp stores on 2026-11-22, not just read.

## Summary

The three feature rounds hold their stated invariants — nothing goes live
without the existing approval paths, hard criteria stay outside every
weighting, and the Phase 1 regression sweep reproduced the recorded state
exactly (558 tests, 70 tools, 116/116 stored-draft verdicts agreeing). No
Critical or High findings. The real risks sit at two data seams: the
WordPress edit-feedback parser silently loses words from editor-shaped
HTML, and two orchestration paths (setup re-prepare, WP sync) can destroy
or abort more than their own contracts promise. **0 Critical · 0 High ·
1 Medium · 3 Suggestions · 4 Nice to have. Verdict: approve with
suggestions** — none of these blocks the current state; the Medium should
be fixed before the WP edit-feedback loop is relied on for real posts
(it has not yet recorded a real edit: all 4 live posts are unchanged).

## Critical (must fix before merge)

None found.

## Medium

- **M1 — `wordpress.py` `_BlockExtractor` silently drops list and table
  text, and glues words across `<br>` and unclosed blocks.**
  Contract: the module's own header — the sync reduces both sides to
  text blocks so "the comparison is about the words", and
  `sync_wordpress_edits` records the edited text into the taste layer
  as the owner's teaching signal. Runtime (measured 2026-11-22 against
  HEAD): `html_to_blocks("<p>Giới thiệu:</p><ul><li>Khô cá lóc</li>
  <li>Tôm khô</li></ul><p>Kết.</p>")` returns `['Giới thiệu:', 'Kết.']`
  — the list items vanish; table cells vanish the same way;
  `"<p>Dòng một<br>Dòng hai</p>"` returns `['Dòng mộtDòng hai']`
  (words glued); an unclosed `<p>` before an `<h2>` merges the two
  texts into one block with no separator. A shop owner editing a post
  in the block editor routinely produces lists and price tables, so a
  real edit can be missed entirely (both sides lose the same text
  differently) or recorded into the taste layer with the list/table
  content missing — the learning signal is corrupted silently, in the
  safe-looking direction. Correction: treat `li` (and table cells) as
  block boundaries, emit a separator at `<br>`, and close an open block
  when a new block tag opens. This is a behaviour change — it needs
  its own fail-first round, not a drive-by fix. Confidence: verified
  (measured outputs above).

## Suggestions (should fix or discuss)

- **S1 — `mcp_server.py` `_sync_one_wp_edit`: unguarded
  `int(record["wp_post_id"])` lets one malformed export record sink a
  whole brand sync.** Contract: the function's own docstring —
  failures "are per-draft errors, not exceptions, so one bad post
  cannot sink a brand-wide sync". Runtime (verified): a stored draft
  whose `wordpress_export.wp_post_id` is `"not-a-number"` makes
  `sync_wordpress_edits(brand_id=…)` raise an uncaught
  `ValueError: invalid literal for int()` out of the results list
  comprehension — no per-draft error, no results at all. The exporter
  itself always writes an int, so the trigger is a corrupt or
  hand-edited record — exactly the class the brand-path scanner
  already tolerates for unparseable files. Correction: coerce and
  validate the record inside the per-draft try, returning the
  structured error like the other failure paths.
- **S2 — `setup_assist.py` `prepare_brand_setup`: re-running prepare
  silently overwrites a human-edited brand draft.** Contract: the
  dossier positions the brand draft as awaiting the human's review;
  the draft store is where that review happens (the app edits drafts).
  Runtime (verified): prepare → add `banned_terms` to the draft (a
  human edit) → re-run prepare → `banned_terms` is `[]` again, with no
  warning anywhere in the returned dossier. The tool docstring does
  say drafts are "refreshed from the fresh fetch", so the behaviour is
  documented — but a refresh that erases the exact field the human is
  asked to supply (the dossier's own needs_human list: "add the
  brand's own banned_terms") should not be silent. Correction: when a
  draft already exists for the brand, either refuse with an error
  naming the existing draft, or preserve human-owned fields
  (banned_terms, prohibited claims, examples) across the rebuild and
  say so in the dossier.
- **S3 — `mcp_server.py` `score_section_variants`: a stale style
  profile in a draft's brief is silently dropped.** Runtime
  (verified): a draft whose brief names a nonexistent
  `style_profile_id` scores variants with no error and no note —
  `style_profile_id: None` in the result, indistinguishable from "no
  profile intended". `assemble_context` surfaces the same condition
  as `style_profile_error`. Correction: mirror assemble_context —
  return the error (or at minimum a `style_profile_error` field) when
  a brief-named profile fails to resolve; an explicit-arg profile
  already errors correctly.

## Nice to have

- **N1 — `setup_assist.py` facts-draft entry mislabels big packs:**
  the lorebook price line formats any weight ≥ 1000 g as the literal
  string `1kg`, so a 2000 g pack would read "1kg" in the draft entry
  a human reviews. Format as `{w/1000:g}kg` instead. (Draft-only text;
  the numeric facts fields are correct.)
- **N2 — `gate_history` recording shape differs by entry point**
  (found by the Phase 1 differential): entries recorded via the
  LangGraph graph path carry no craft fields; MCP `final_gate`
  entries do. Verdicts agreed on 116/116 stored drafts — this is a
  consumer-confusion risk, not a verdict risk. Record the same fields
  in both paths, or document the two shapes where gate_history is read.
- **N3 — `research.py` `_depth_excerpts` prefix citation match can
  over-cite:** `cited_url.startswith(source_url)` lets a source at
  `https://x/blog` claim a citation of `https://x/blogger`. Bounded
  (accepted sources only, ≤ 3 excerpts) and direction-safe. Require
  the character after the prefix to be `/` or end-of-string.
- **N4 — Docstring nuance in `prepare_brand_setup`:** "idempotent-ish
  … refreshed from the fresh fetch" — on re-run, `research.add_source`
  dedupes by URL and keeps the ORIGINAL fetched pages in the pack;
  the pack's stored source pages are not refreshed (the facts draft
  is). Harmless today because proposals are built only once; the
  wording should not promise more than the code does.

## Standards axis

- Possible Long Function (heuristic, repo-tolerated):
  `setup_assist.build_setup_dossier` (~230 lines) and
  `extract_facts_draft` (~130) — the same orchestrator shape
  `mcp_server.py` already uses throughout; named here only because
  round 3 created both. No action recommended inside this round.
- Comment hygiene: the new modules' comments explain *why*
  (invariants, provenance, failure directions) — no deodorant
  comments found. Two string literals use the ellipsis character `…`
  as a truncation marker in user-facing text (research depth claims,
  setup excerpts) — content, not typography slop; left alone.
- Security pass over the diff: the only new network surface is
  `wordpress.get_post` (GET, reuses the existing `_request` + browser
  User-Agent + secrets path; the password is never logged or stored
  by the new code) and `prepare_brand_setup`'s fetch of an
  owner-supplied URL — the Brand Builder's existing, intended
  surface. The MCP HTTP server's no-auth localhost stance is
  unchanged by this diff and already recorded as a v1 decision. No
  injection, secret, or auth findings in the diff.

## Spec axis

The three commits implement exactly the mechanisms Smith approved
(quality uplift WS1–WS3; AI tagging; setup assist), and their stated
hard rules are pinned by tests that pass at HEAD: pack items never
render before `applied` (depth material), contradictions withhold
contested values from the facts draft, no bulk approval exists,
corpus/persona are proposals only, WP sync is read-only against the
site. Two deviations between the stated spec and behaviour are
recorded above as findings rather than silently passed: S2 (the
refresh wording vs. human edits) and N4 (source-page refresh).

## App (read-only sample — brandforge-app @ 63685bc)

Sampled: `src/components/charts/DumbbellChart.tsx` in full and
`src/lib/core/stats.ts` aggregation paths. No Critical/High found in
the sample. The dumbbell implements the settled design honestly (floor
tick + score dot + weight ring; the header comment records *why* no
target dot is drawn — the core returns no per-axis target). Layout
uses Astryx components per the app repo's AGENTS.md; the raw `<svg>`
elements are chart internals, the documented round-3 exception. The
working tree was clean (the charts round had committed) — nothing in
the app was modified by this review.

## Not verified

- The full app codebase (5,304 lines) was NOT reviewed — only the two
  files above were read. A full app review needs its own round.
- M1's real-world frequency: no live post has been edited yet (all 4
  exported posts diff `changed: false`), so the list/table loss has
  never fired on real data; the measurement is from constructed
  editor-shaped HTML, not from a captured Gutenberg payload.
- The LangGraph graph path's gate_history writer (N2) was located by
  its output shape in stored drafts, not read line-by-line this round.
- Performance of the new code paths was not re-profiled (the perf
  round's numbers predate these commits; nothing in the diff touches
  the hot checker paths, but that is an inference, not a measurement).

## Closure addendum — M1 + S1 + S2 fix round (2026-11-22, Smith-approved)

Scope was exactly M1, S1, S2. S3 and N1–N4 were out of scope and
are untouched. Each fix landed as its own commit, fail-first, with
the full verification run fresh at the end (see below).

- **M1 — FIXED, commit `4fa6555`.** `_BlockExtractor` is now a
  flush-on-boundary machine: `li`/`td`/`th` are block tags, a new
  block tag closes the open block (unclosed `<p>` no longer merges
  into the following heading), `<br>` and a mid-block skipped
  subtree leave a word boundary, and EOF flushes a trailing
  unclosed block. Evidence: the review's measured repro now
  returns `['Giới thiệu:', 'Khô cá lóc', 'Tôm khô', 'Kết.']`
  (was `['Giới thiệu:', 'Kết.']`), `"<p>Dòng một<br>Dòng hai</p>"`
  → `['Dòng một Dòng hai']` (was glued), table cells survive as
  blocks. The 5 strict-xfail contract tests in
  `tests/test_wp_block_fidelity.py` pass and their markers are
  removed (file: 16/16). Exporter round-trip + WS3 suites stay
  green, and the live sanity below shows the fix invents no diffs
  on exporter-shaped content.
- **S1 — FIXED, commit `f9b3250`.** The `int(record["wp_post_id"])`
  coercion in `_sync_one_wp_edit` is validated inside the per-draft
  path: a malformed record returns that draft's structured error
  naming the bad value, and the brand sync continues. Evidence:
  fail-first test `test_ws3_malformed_export_record_is_a_per_draft_error`
  reproduced the review's crash pre-fix (uncaught
  `ValueError: invalid literal for int()` escaping
  `sync_wordpress_edits`) and passes post-fix — the good draft in
  the same brand syncs normally alongside the error entry.
- **S2 — FIXED, commit `d286881`.** Preservation rule, stated in
  the `setup_assist` docstring + README: generation is
  deterministic, so any field in which the stored brand draft
  differs from the fresh generation is a human edit and is
  PRESERVED across a re-prepare (`_preserve_human_fields` merge;
  human-added keys kept too); the run's preserved field paths are
  stored in the setup record and reported in the dossier's
  `preserved_fields` and the brand_draft step detail. Evidence:
  fail-first test reproduced the wipe pre-fix (`banned_terms`
  hand-set to `["ngon nhất", "số 1"]` came back `[]`); post-fix
  the edits survive and the dossier names `voice.banned_terms`,
  `voice.prohibited_claims`, `brand.name`; a noise-guard test pins
  that a plain re-prepare preserves nothing.

**Final verification (fresh output, this round):** pytest
**577 passed, 0 xfailed** (569 + 5 un-xfailed M1 contracts + 3 new
tests) · run_golden **7/7** · run_model_golden **6/6** ·
test_mcp_client **24/24** · test_mcp_http **PASS** (70 tools) ·
demos **4/4 exit 0**. Stored-draft re-gate spot check (run against
a copied drafts dir; the live store untouched): 120 drafts with
recorded gate_history compared — brand/fact/passed agree on
**120/120**; 117 identical on every compared field; the 3
remaining differ only by the known N2 recording shape (graph-path
entries carry no craft fields — brand/fact/passed still agree).
Live WP sanity: `sync_wordpress_edits(brand_id="mambagiaokhoe")`
against the real site returned `changed: false` for all four
exported posts (777, 778, 779, 780), no errors — M1's new
extractor produces zero phantom diffs on the exporter-shaped
posts, and the taste store gained no entries.
