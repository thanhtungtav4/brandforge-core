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

Scope was exactly M1, S1, S2. S3 and N1–N4 were out of scope of
that round; S3 is fixed in the follow-up below, N1–N4 remain
untouched. Each fix landed as its own commit, fail-first, with
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

## Closure addendum — S3 fix (2026-11-22, 2h review run)

S3 was out of scope of the M1+S1+S2 round and is fixed here, in the
2-hourly review run's backlog slot. N1–N4 remain untouched.

- **S3 — FIXED.** `score_section_variants` now tracks where the
  wanted style profile came from: an explicit argument or a
  brief-named id that fails to resolve is an error (`style profile
  not found: <id>`, no ranking returned), so variants are never
  silently scored without the intended profile; a stale
  corpus-choice default still means no style part, exactly as
  `assemble_context` treats it. A corrupt stored profile
  (ValueError from the resolver, review M6) is likewise a
  structured error instead of an uncaught crash. Evidence:
  fail-first test `test_ws2_stale_brief_style_profile_is_an_error`
  reproduced the silent drop pre-fix (ranking with
  `style_profile_id: None`); 3 new tests in
  `tests/test_quality_uplift.py` (brief-stale, explicit-stale pin,
  corpus-choice-default silent pin). Differential over the real
  stores: 122 stored drafts + brand calls for mambagiaokhoe and
  moc-brew — before/after byte-identical.

**Verification (fresh output, this fix):** pytest **580 passed**
(577 + 3 new) · run_golden **7/7** · run_model_golden **6/6** ·
test_mcp_client **24/24** · test_mcp_http **PASS** (70 tools) ·
demos **4/4 exit 0**.

## Closure addendum — approved item A (2026-11-23, 2h review run)

Smith approved this on 22/11/2026 (23:54) as the follow-up to S3, done
here as the run's single item. N2 remains untouched — it is the next
approved item, for a later run.

- **A — FIXED.** The S3 stale-profile rule now also covers
  `quality_score` and `check_draft_all`: a draft whose brief names a
  `style_profile_id` that no longer resolves returns a structured
  error naming the id (`style profile not found: <id>`) instead of
  silently scoring with `style_profile=None`. The corpus-choice
  clause does not apply here — neither function consults the corpus
  choice (unlike `score_section_variants`' brand path and
  `assemble_context`), so there is no default to silently ignore. A
  corrupt stored profile was already a structured error in both
  functions (the resolve sits inside the existing ValueError guard)
  and is now pinned by a test. Evidence: fail-first tests
  `test_qs_stale_brief_style_profile_is_an_error` and
  `test_check_all_stale_brief_style_profile_is_an_error` reproduced
  the silent scoring pre-fix (full report, `style: None`); 4 new
  tests in `tests/test_quality_uplift.py` (2 stale-id, 1 corrupt
  pin, 1 valid-profile pin). Differential over the real stores:
  `quality_score` + `check_draft_all` over all 126 stored drafts —
  before/after byte-identical (all 7 briefs carrying a profile name
  ids that still resolve).

**Verification (fresh output, this fix):** pytest **584 passed**
(580 + 4 new) · run_golden **7/7** · run_model_golden **6/6** ·
test_mcp_client **24/24** · test_mcp_http **PASS** (70 tools) ·
demos **4/4 exit 0**.

## Closure addendum — approved item B / N2 (2026-11-23, 2h review run)

Smith approved this on 22/11/2026 (23:54) as the follow-up to S3, done
here as the run's single item. Both approved items A and B are now
closed; N1, N3, N4 remain untouched.

- **B / N2 — FIXED.** The LangGraph graph path's `gate_history` entries
  now record the FULL field set MCP `final_gate` records
  (`content_core/graph.py`, `Core.final_gate`): `craft_advisory` on
  every entry, plus `style_score` / `style_profile_id` when the brief
  names a style profile that resolves (same `style_match` computation
  over the joined draft text as `final_gate`). The quality layer is
  never active on the graph path and MCP omits `"quality"` exactly
  then, so nothing is recorded for it on either path — already
  consistent, no change. Only the record shape changed: no gate
  verdict, threshold, or checker call was touched. A stale brief
  profile id, or a corrupt stored profile file (the loader raises
  `ValueError`), resolves to None in the graph path, so the entry
  simply omits the style fields and the draft's verdict stands — the
  graph never had style handling, so a bad profile must not newly
  fail a draft that previously passed. (Whether `final_gate` itself
  should error on a stale profile is the pending S3 follow-up
  proposal from the 01:38 run, still Smith's call.) Old stored
  entries stay as-is (append-only): the 3 bare graph-path entries
  found in the store (drafts `d-60691d9d22`, `d-701a5040d9`,
  `d-dab89d314d` — no craft fields at all, predating even the
  `craft_score`/`craft_blocking` the graph already wrote) are
  historical records, not rewritten. Evidence: fail-first test
  `test_graph_gate_history_matches_mcp_entry_shape` reproduced the
  gap pre-fix (entry missing `craft_advisory`); 4 new tests in
  `tests/test_gates.py` (MCP-shape parity, style recorded, stale-id
  pin, corrupt-file pin). Differential over the real stores: the
  graph `final_gate` node over all 128 stored drafts — verdict
  (`status` / `failing_sections` / `passed`) identical before/after
  on **128/128**. Spot check on a real styled draft
  (`d-2a0202e3c7`, brief names `mien-tay-van-hoc-voice`): graph
  entry now carries exactly the MCP field set, `style_score` 92.0,
  `craft_advisory` False, `passed` True.

**Verification (fresh output, this fix):** pytest **588 passed**
(584 + 4 new) · run_golden **7/7** · run_model_golden **6/6** ·
test_mcp_client **24/24** · test_mcp_http **PASS** (70 tools) ·
demos **4/4 exit 0**.

## Closure addendum — N3 (2026-11-23, 2h review run)

N3 was the next untouched round-3 item after A and B closed; N1 and
N4 remain untouched.

- **N3 — FIXED.** `research._depth_excerpts` no longer treats a
  shared string prefix as a citation: the prefix clause now goes
  through `_cites_source`, which requires the cited URL to equal the
  source URL or sit on a path beneath it (the character after the
  source URL must be ``/`` or end-of-string, per the review's rule;
  a trailing slash on the stored source URL is normalised away
  first). A source at `https://x/blog` can no longer claim an
  excerpt for a citation of `https://x/blogger` — or of a tracked
  link `https://x/blog?ref=…`, which the rule likewise excludes
  (the safe direction: a missed excerpt loses local colour, never
  facts). Only the citation-matching clause changed: no gate,
  threshold, checker, or pack invariant was touched. Evidence:
  fail-first tests — the sibling-prefix and query-string cases
  failed pre-fix (excerpt wrongly contributed, 1 instead of 0)
  while the exact-URL, subpath, and trailing-slash controls passed
  throughout; 6 new tests in `tests/test_quality_uplift.py`
  (sibling-prefix, 3 own/subpath controls, trailing-slash source,
  query-string). Differential over the real stores:
  `depth_material` over the only real pack
  (`data/research_packs/mambagiaokhoe.json`, 9 proposed / 0
  applied items) is byte-identical before/after; a synthetic
  differential on the pack's real URL tree shows exactly the two
  over-cite cases changing — citing
  `https://mambagiaokhoe.com.vn` or `https://mambagiaokhoe.com2/x`
  against source `https://mambagiaokhoe.com` contributed an
  excerpt before (1) and contributes none after (0), while the
  subpage, exact-source, and unrelated-URL cases are unchanged.

**Verification (fresh output, this fix):** pytest **594 passed**
(588 + 6 new) · run_golden **7/7** · run_model_golden **6/6** ·
test_mcp_client **24/24** · test_mcp_http **PASS** (70 tools) ·
demos **4/4 exit 0**.

## Closure addendum — N1 (2026-11-23, 2h review run)

N1 was the next untouched round-3 item after A, B and N3 closed;
N4 remains untouched.

- **N1 — FIXED.** `setup_assist.extract_facts_draft` no longer
  writes the literal string `1kg` for every pack >= 1000 g in the
  facts-draft price line: the label is now
  `{w if w < 1000 else w / 1000:g}{'g' if w < 1000 else 'kg'}`,
  so a 2000 g pack reads "2kg", 1500 g reads "1.5kg", and 1000 g
  still reads "1kg" (1250 g → "1.25kg"). Only the human-facing
  lorebook text changed — the numeric facts fields
  (`pack_weights_g`, `price_vnd_per_250g`, `price_display`,
  `known_prices_vnd`) were always correct and are untouched, as
  are all gate/threshold/checker paths. Evidence: fail-first test
  `test_prepare_big_pack_weights_labelled_in_kg` reproduced the
  mislabel pre-fix (price line read "650.000đ cho gói 1kg,
  880.000đ cho gói 1kg"); 1 new test in
  `tests/test_setup_assist.py`. Differential over the prepare
  path: the clean fixture site (250g/500g/1kg) is byte-identical
  before/after, and the big-packs fixture changes exactly in the
  two kg labels ("1kg, 1kg" → "1.5kg, 2kg"); no stored real draft
  carried the mislabel (the only live "cho gói 1kg" entry,
  cao-nguyen-moc, is a genuine 1000 g pack).

**Verification (fresh output, this fix):** pytest **595 passed**
(594 + 1 new) · run_golden **7/7** · run_model_golden **6/6** ·
test_mcp_client **24/24** · test_mcp_http **PASS** (70 tools) ·
demos **4/4 exit 0**.

## Closure addendum — fix round F1–F5 (2026-11-23, Smith's chat review)

Five findings Smith handed over in chat (2 High, 2 Medium, 1 Low),
fixed in his priority order, one commit per finding, LOCAL ONLY —
nothing pushed this round. Fail-first tests per finding;
differentials over copies of the real stores wherever gate/verdict
paths were touched; NO writes to the real WordPress site (all
export evidence is against the local mock WP server).

- **F4 (Medium) — final_gate S3 parity — FIXED, core `abbbf71`.**
  `final_gate` now returns the structured error
  `{"error": "style profile not found: <id>", ...}` and records NO
  gate_history entry when the BRIEF's `style_profile_id` no longer
  resolves; a corrupt stored profile surfaces the same way via the
  loader's ValueError. Parity exception kept: briefs carrying
  `style_profile_source == "brand_corpus_choice"` treat a stale id
  as silently no style part (assemble_context behaviour). The
  LangGraph graph path was deliberately NOT changed — its resolver
  swallows stale ids by documented design (see the B/N2 addendum).
  Evidence: 4 fail-first tests (`test_fg_*`, tests/test_quality_uplift.py);
  differential over all 140 real drafts: verdicts identical
  **140/140** (0 real briefs name a missing profile). Suite 626.

- **F3 (Medium) — stale style fingerprints — FIXED, core `f2fe952`.**
  `refresh_profile` now stamps `fingerprint_stale`
  ({from_version, to_version, reason}, sticky once saved) whenever
  it version-bumps a profile without recomputing the fingerprint;
  `style_match` adds an additive `fingerprint_warning` naming both
  versions. Scores/metrics byte-identical. Data fix: the one known
  real case, `mien-tay-van-hoa-voice` (stored at algorithm v2;
  original samples unrecoverable — NO recompute fabricated), was
  stamped through the refresh path; its fingerprint is verified
  byte-identical before/after and it is the ONLY live-store change
  of this round (md5 d2ca0a5b… → b0509f75…). The other 4 stored
  profiles were already v3. Evidence: 4 new tests in
  tests/test_style.py. Suite 630.

- **F1 (High) — gate-checked WordPress export — FIXED, core
  `940b93f`.** New `gates.content_sha(sections, title)` (sha256 of
  canonical JSON) is recorded as `content_sha` on every gate_history
  entry in BOTH gate paths (MCP final_gate and the graph node).
  `export_draft_to_wordpress` refuses BEFORE any WordPress write
  unless the LATEST gate entry passed AND its content_sha matches
  the content as it stands now. Structured reasons: `never_ran_gate`
  / `gate_failed` / `edited_after_pass` / `pass_unverifiable`.
  LEGACY RULE (the safer choice): passes recorded before
  fingerprinting are NOT honoured — a workflow status cannot prove
  the text never changed, so such a draft re-runs final_gate once.
  Evidence: 4 refusal tests each asserting the reason AND zero
  requests reaching the mock, plus content_sha unit tests
  (tests/test_wordpress.py, tests/test_gates.py); differential vs
  the post-F4 baseline **140/140** identical; the 4 real exported
  drafts (posts 777–780) keep their records untouched — the
  duplicate rule still fires before the gate check for them.
  Suite 638.

- **F2 (High) — idempotent export — FIXED, core `fda6a00`, app
  `07397e3`.** Marker mechanism: a deterministic post SLUG
  (`bf-<draft>-<attempt prefix>`) — stock WP REST creates with an
  explicit slug and queries by it; a meta field would need
  register_meta on the client's site, which the exporter cannot
  assume. The export records `wordpress_export_attempt`
  (attempt id + slug + site + timestamp) on the draft BEFORE any
  upload/create; on a later call it first searches the site for the
  open attempt's slug and ADOPTS a found post
  (`adopted_existing_post: True`) instead of creating a second one;
  a retry whose attempt never created reuses the same attempt +
  slug; force mints a fresh attempt. App side: `callTool` gains
  `{retry: false}` and the export call opts out of the blind
  reconnect retry; WpExportPanel's banner reports adoption honestly.
  Evidence (MOCK ONLY — not verified against a real WordPress):
  simulated lost response (create lands, WPError raised) → retry
  adopts post 4242, mock post count stays 1, no second create, no
  media re-upload; slug-reuse and find_post_by_slug roundtrip tests
  in tests/test_wordpress.py (mock extended with post storage + GET
  slug search). Suite 642.

- **F5 (Low) — app stats staleness — FIXED, app `2910724`.**
  "Latest quality" in src/lib/core/stats.ts is now the latest RUN's
  own weighted_total — a draft whose newest run carries no quality
  report contributes no score instead of silently presenting an
  older run's; the series still plots all quality-bearing runs as
  history; totals are the brand-weighted mean of the brand values.
  Verified against the real store: NO displayed average changes
  today — mambagiaokhoe (the only real brand with gated drafts:
  9 drafts, 7 counted) has quality on every latest run, brand and
  totals averages stay 96.07; across all 138 gated drafts zero
  exhibit the stale pattern, so this closes a latent mislabelling.

**Verification (fresh output, this round):** pytest **642 passed**
· run_golden **7/7** · run_model_golden **6/6** · test_mcp_client
**24/24** · test_mcp_http **PASS** (72 tools) · demos **4/4 exit 0**
· app `tsc --noEmit` clean · app production build PASS. Live
stores md5-verified against the pre-round snapshot: identical
except the single intended F3 stamp above (two demo-created drafts
from the verification run were deleted via delete_draft and the
store re-verified).
