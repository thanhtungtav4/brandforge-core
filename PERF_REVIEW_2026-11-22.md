# Performance Review — brandforge-core (2026-11-22)

**Scope:** review-only performance pass over the whole content core at HEAD `a0ee031` (core-completion round 2), 471 tests, 60 MCP tools. No code was changed; this report is the only new file. All profiling ran from scripts in `/tmp/perf/` against temp-dir copies of the stores (env overrides), so the repo tree is untouched (`git status` clean before and after; `trials/` untracked is the pre-existing standing state).

**Method:** measure first, read code to explain the numbers. Wall times are best-of-3 on this VM (1.5 shared vCPU — absolute numbers carry machine noise; proportions and call counts are the load-bearing evidence). cProfile inflates absolute times ~3.5× (a 5.0 s gate profiles as 17.5 s), so profile output is used only for call counts and shares. Test drafts: the real stored 1,172-word mambagiaokhoe article (`d-44bf2dc3c8`, 5 sections) and replications of its sections with slight variation for the ~5k and ~42k sizes. Note the round-2 review measured 7.10 s / 5.57 s (quality / plain) on a *different* synthetic 42k draft; this review's 5.00 s / 3.56 s on a replicated-real draft is a different text, not an improvement claim.

## Executive verdict

**There is no asymptotic time bomb.** Every checker scales ~linearly in draft size (×8.3 words from 5k → 42k costs ×8.4–8.7 per checker; the gate costs a near-constant ~81–84 µs/word at both 1.2k and 42k). What the codebase has is a **large constant factor**, from three compounding habits: (1) the hottest text primitive, `normalize_for_match`, is re-run on identical inputs tens of thousands of times per gate; (2) craft's phrase matcher does O(phrases × text) work with a full string rebuild per phrase, and is invoked per sentence from three different call sites; (3) nothing is cached — not across tool calls, and in the worst case (the domain glossary) not even *within* one call, where it is re-read from disk once per section. A fourth, architectural factor: the writer's normal check loop re-runs every checker 3–5× over the same text, because each tool is stateless and `quality_score` re-runs lint+fact+craft from scratch. All of the top fixes are constant-factor and byte-identical-able; none requires touching a design value.

## Measured baseline

### Gate pipeline by draft size (wall, best of 3)

| Draft | lint | fact | craft | style | quality* | gate | gate+quality |
|---|---|---|---|---|---|---|---|
| 1,172 words (real article) | 0.012 s | 0.038 s | 0.043 s | 0.002 s | 0.093 s | 0.095 s | 0.137 s |
| 5,083 words | 0.050 s | 0.177 s | 0.193 s | 0.009 s | 0.410 s | 0.427 s | 0.604 s |
| 42,255 words (stress) | 0.434 s | 1.498 s | 1.622 s | 0.073 s | 3.358 s | 3.563 s | 5.000 s |

\* `quality` = `evaluate_quality` with **no** `_precomputed` — the `quality_score` tool path. Inside `final_gate`, the quality layer reuses the gate's lint/fact results (round-1 M4 fix), so gate+quality costs less than gate + quality\*.

### One realistic writer check loop (7 tool calls, the 1,172-word draft): 476 ms

`brand_check, fact_check, craft_check, style_match, quality_score, final_gate, final_gate(+quality)` — with these checker re-runs over the *same* text (cProfile call counts): `lint_draft` ×4, `check_draft` ×4, `craft_check_sections` ×3 plus whole-text `craft_check` ×2 more inside quality, `style_match` ×5, `normalize_for_match` **28,175 calls**, `_find_phrase_hits` 1,009 calls, `find_claim_detail` 512 calls.

### File reads per single tool call (counted via wrapped `Path.read_text`/`open`; cold = first call in process, warm = second)

Cold and warm counts are **identical for every tool** — there is no caching anywhere in `loaders.py` or `mcp_server.py`.

| Tool call | Reads | Wall | What it re-reads |
|---|---|---|---|
| `final_gate` | **9** | ~100 ms | domain_glossary.json **×5** (once per section), brand draft + facts profile, the draft, style profile |
| `final_gate` (+quality) | **10** | ~143 ms | glossary ×6 (quality's clarity pass resolves it again) |
| `craft_check` (draft) | 8 | ~68 ms | domain_glossary.json ×5, brand_mocbrew.yaml ×2 (YAML re-parse ~5.6 ms each) |
| `assemble_context` | 7 | ~7.5 ms | the brand's two store files ×3 between them, persona, craft library, exemplars, style profile |
| `quality_score` | 5 | ~97 ms | draft, brand draft + facts, style profile, glossary |
| `fact_check` (draft) | 3 | ~41 ms | brand draft, facts profile, draft |
| `brand_check` (draft) | 2 | ~12.5 ms | draft, brand draft |
| `list_drafts` (99 drafts) | **97** | ~5.8 ms | every draft file, fully parsed + shape-validated, to produce id/brand/status rows |
| `get_brand` | 1 | ~6.3 ms | brand_mocbrew.yaml — the 3 KB YAML parse is nearly the whole cost |
| `get_draft` / `get_pack` / `get_facts` / `get_persona` | 1 | ≤0.3 ms | — |
| `export_core_data` | 118 | ~34–41 ms | all stores (this one is *supposed* to read everything) |

### Batch operations and cold start

| Operation | Measured | Verdict |
|---|---|---|
| `export_core_data` (real stores, 118 files / 829 KB) | 34–41 ms | fine |
| `build_pack_proposals` (10 pages / 5,128 words) | 111 ms | fine |
| `build_pack_proposals` (30 pages / ~15,400 words) | 282 ms | fine, ~linear |
| `style.build_profile` (31 samples / 95,507 words) | 370 ms | fine (one-off setup) |
| `fulfil_images_from_library` (library of 500, 6 slots) | 12.7 ms | fine, linear in library size |
| `image_check` / `get_image_manifest` | ≤0.7 ms | fine |
| `propose_pack_item` (pack growing 57 KB → 78 KB) | ~2.5 ms, flat | fine |
| MCP server cold start (`import mcp_server`) | **1.5–2.1 s** | langgraph chain ≈1.07 s of it; mcp SDK ≈0.94 s |
| Full test loop | **≈37 s** | pytest 18.65 s + client 3.4 + http 3.2 + goldens 3.5 + demos 8.6 |

pytest slowest 10 (`--durations`): `test_concurrent_final_gates_both_record_history` 2.46 s, `test_concurrent_save_section_loses_nothing` 2.40 s (multi-process lock tests — inherent spawn/sleep cost, and they are the H3 regression net), `test_tool_list_complete_over_stdio` 1.67 s (server spawn ≈ the import cost above), `test_run_golden_via_tool` 1.14 s, `test_golden_runner_exit_code_zero` 1.08 s, `test_all_six_golden_briefs_pass` 0.64 s, `test_token_fallback_lock_steals_only_dead_holders` 0.55 s, three fetch/HTTP tests ≈0.51 s each.

## Findings (ranked by measured impact)

### P1 — High — Craft's phrase matcher is O(phrases × text) per call, re-normalizes everything, and is called per sentence from three sites

`craft.py:302-330` (`_find_phrase_hits`). Per call it: normalizes the text; normalizes **and sorts the entire phrase list again**; then, for each phrase, rebuilds the haystack with `"".join(masked)` (O(text) per phrase) and runs a freshly built regex over it — O(P·T) character work per call, for vocabularies (weak words, fillers, sensory terms) that are constant per glossary. The call sites multiply it: per section for weak+filler (`craft_check`, `craft.py:412,425`); **per sentence** for sensory terms (`_sensory_terms_used`, `craft.py:344-355`); and per sentence ×2 lists inside `_carries_violation` (`craft.py:448-456`), which the rhythm block itself evaluates up to twice per sentence — once in the `any(...)` probe and again in the median-imputation comprehension (`craft.py:~460-465`).

*Evidence:* craft is the largest single checker at stress size (1.62 s of the 3.56 s gate) and co-largest at article size. In the 42k gate+quality profile: `_find_phrase_hits` 15,513 calls, cum 8.85 s of 17.5 s profiled; `str.join` 899,097 calls, cum 8.58 s; `_carries_violation` 5,113 calls, cum 5.39 s; `sorted` 99,198 calls, cum 4.79 s (the per-call phrase re-sorts are a visible slice of this).

*Refactor:* prepare phrase tables once per vocabulary (normalized + length-sorted pairs, built at glossary resolution) and thread them through; replace the masked-string rebuild with a boolean mask array + `finditer` over the immutable normalized string, accepting only spans not already masked (same longest-first masking semantics); memoize `_carries_violation` per `craft_check` invocation. *Risk:* **low–medium** — outputs stay identical if the masking order is preserved exactly; craft scores are pinned by goldens/probes, which would catch a slip. *Effort:* **M**.

### P2 — High — `normalize_for_match` is the system's hottest function and is re-run on identical inputs constantly

`linter.py:98-114`. Each call lowercases, strips format chars via a replace loop, then does **two per-character Python generator joins** (NFD strip at line 112, confusables map at line 113) plus NFKC, NFD and a regex sub. It is a pure function, but nothing memoizes it and callers re-derive the same values repeatedly:

- every `_find_phrase_hits` call re-normalizes the whole phrase vocabulary (see P1);
- `fuzzy_phrase_spans` re-normalizes its phrase on every call (`linter.py:273`), though its regex family is already `lru_cache`d;
- `find_claim_detail` re-normalizes a sentence (`fact_check.py:575-590`: strip_markup + normalize + collapse) that `check_section`'s main loop already normalized (`fact_check.py:~858`);
- `check_section`'s two clause loops each re-split the sentence and re-normalize every clause (`fact_check.py:~897` and `~923`);
- `_word_numeral_variant` normalizes each claim phrase per sentence it is tried on (`fact_check.py:489`).

*Evidence:* 346,418 calls in one 42k gate+quality run, cum 12.0 s of 17.5 s profiled (the two genexpr lines alone: 7,097,002 calls each); 28,175 calls in one 7-call writer loop on a 1,172-word draft; `unicodedata.combining` 9.4 M calls, `unicodedata.normalize` 1.43 M calls in the stress profile.

*Refactor, in increasing risk order:* (a) thread precomputed norms through the call sites above (several already have the value in hand — `find_claim_detail` even *returns* the norm); (b) a bounded `lru_cache` on `normalize_for_match` for short inputs (phrases/sentences repeat verbatim across sections, drafts and calls; pure function ⇒ zero verdict risk; cap by length so book-length strings don't thrash it); (c) a `str.translate`-table reimplementation — potentially the biggest single win, but **medium risk**: NFKC/NFD edge behaviour must be proven byte-identical by differential testing over the shipped corpora before adoption. *Effort:* (a) M, (b) S, (c) M–L.

### P3 — Medium-High — The check loop re-runs every checker 3–5×; `quality_score` is a second full gate in disguise

Each MCP tool is stateless by design, but the composition the writer actually performs pays full price per tool: measured loop above shows lint ×4, fact ×4, craft ×3 (+2 whole-text), style ×5 for one draft revision cycle (476 ms at 1,172 words; the same loop at 42k would cost ~15–18 s by the per-size table). Two specific duplications:

- `quality_score` (`mcp_server.py:1787-1821`) calls `evaluate_quality` **without** `_precomputed`, re-running `check_draft` + `lint_draft` + whole-text `craft_check` + `style_match` (`quality.py:222-246,267-293`): 3.36 s at 42k ≈ the entire plain gate (3.56 s); 97 ms at article size ≈ `final_gate` itself (100 ms).
- Inside `final_gate` *with* quality, the fact/brand results are reused (round-1 M4 fix) but quality's clarity axis still runs a **second craft pass** over the joined text (`quality.py:287-293`), on top of the gate's per-section `craft_check_sections`: gate+quality − gate = **+1.44 s at 42k** (+43 ms at 1.2k), i.e. one extra full craft evaluation.

*Refactor options:* (i) accept and document (statelessness is a real virtue with multiple host apps); (ii) a combined check tool that runs each checker once and returns all reports — new surface, zero change to existing verdicts; (iii) a per-process, content-addressed memo: key = hash of the sections + brand/facts/profile identities + format, so repeated checker runs over an unchanged draft hit memory while any edit misses by construction (multi-process safe because it is per-process and keyed by content, not by draft id). *Risk:* (ii) low; (iii) **medium** — the key must capture *every* input (brand draft version, facts, glossary brand, style profile version) or a stale verdict becomes possible; that completeness proof is the work. **Do not** merge quality's clarity craft into the gate's per-section craft to save the pass: whole-text vs per-section aggregation produce different scores — that is a behaviour change, not a refactor. *Effort:* (ii) M, (iii) M–L.

### P4 — Medium — No load caching anywhere; the domain glossary is read from disk once per section per craft run

`loaders.py` has no cache of any kind: every `load_*` is a file read + parse + schema validation, on every call, in every process. Cold == warm for all 20 measured tools (table above). The standout is structural, not incidental: `craft_check_sections` calls `craft_check` per section with `glossary=None` (`craft.py:518-548`), and `craft_check` resolves the glossary via `resolve_glossary` → `load_domain_glossary()` — a disk read + JSON parse **per section** (`craft.py:124-137`). Hence glossary ×5 in one `final_gate`, ×6 with quality, ×5 in the `craft_check` tool (which additionally parses brand_mocbrew.yaml twice — `_brand_max_sentence_words`, `mcp_server.py:476-483`, plus brand resolution). `assemble_context` reads the brand's two store files 3 times between them. `get_brand` re-parses the YAML on every call (~5.6 ms warm for 3 KB).

*Invalidation honesty:* these stores are user-editable files, and a second server process (another AI app) can rewrite them at any time — a naive process-lifetime cache would serve stale brand rules/facts, which is a **correctness** hazard in a system whose whole job is judging against current brand data. The safe shapes are: (a) **within-call dedupe only** — resolve the glossary once per `craft_check_sections` call and pass it down (zero staleness risk; kills the ×5→×1 immediately); (b) mtime+size-keyed caching for genuinely read-only reference data (domain glossary, craft library, exemplars) — staleness bounded to same-mtime-same-size edits; (c) **no caching for drafts or packs** (mutation-heavy, lock-protocol participants). *Risk:* (a) none, (b) low with the mtime check. *Effort:* (a) S, (b) M.

### P5 — Medium — The linter prepares per-sentence and whole-text normalized forms before knowing whether there is anything to look for

`lint_section` (`linter.py:455-483`) unconditionally builds, per section: the per-sentence prepared list (`strip_markup` + `normalize_for_match` + `collapse_letter_spacing` + `_normalize_indexed` per sentence) **and** the whole-text equivalents over the full section text. Both are consumed *only* by the banned-term scan. For a brand with **zero banned terms** (the measured mambagiaokhoe context: `banned_terms=0`) the prepared data is never used — yet lint still costs 0.43 s at 42k, nearly all of it this preparation. `_normalize_indexed` (`linter.py:303-355`) is the slowest normalizer in the codebase (per-character loop with per-character NFKC+NFD): 2,726 calls, 1.05 s tottime in the stress profile; it runs per sentence *and* again over the whole text, duplicating the per-sentence work. Additionally, the whole-text preparation is built eagerly although the whole-text pass only *runs* for terms the sentence pass never saw (`linter.py:497-507`).

*Refactor:* skip preparation entirely when `banned_terms(brand)` is empty (byte-identical — the results are unused; the median-pool's `asserted_banned_sentences` stays empty either way); build the whole-text preparation lazily, only when an unseen term actually needs it. *Risk:* **none–low** (verified by reading: `prepared`/`whole_*` have no other consumer in `lint_section`). *Effort:* **S**. Expected effect: lint for banned-term-free brands drops to the sentence-statistics cost (an order of magnitude at stress size); no change for Mộc Brew-class brands beyond the lazy whole-text saving.

### P6 — Low-Medium — `fact_check.check_section` re-splits and re-normalizes the same text repeatedly

`fact_check.py:763-1050`. Per section: `split_sentences(text)` runs **3×** (delivery loop `:818`, main loop `:855`, cross-sentence block `:1010`); the two clause loops re-split each sentence on the same clause regex and normalize every clause independently (`:897`, `:923`); `find_money_amounts` runs over the full text, then again per sentence to build `_fact_spans` (`:966`); `find_claim_detail` re-normalizes per claim group (see P2). *Evidence:* fact is the second-largest checker (1.50 s at 42k); `check_section` 173 calls cum 5.36 s profiled; `find_claim_detail` 5,106 calls cum 3.45 s; `_record_claim` 5,106 calls cum 3.45 s. *Refactor:* split once per section and pass sentences (and their norms) down; split clauses once and run both origin checks over the same normalized clauses; add an optional precomputed-norm parameter to `find_claim_detail`. All byte-identical-able. *Risk:* low. *Effort:* M.

### P7 — Low — Regex compilation churn in hot loops

`re._compile` was called 606,433 times in the stress profile (cum 0.56 s, ~3% of profiled time). Most are cache lookups from string-pattern `re.*` calls, but some are real compiles: the pattern string built per phrase per call in `_find_phrase_hits` (`craft.py:322-324`); `re.split` with a string pattern per sentence/clause (`fact_check.py:872,897,923` — the clause splitter is a *constant* pattern recompiled via cache lookup on every sentence); f-string patterns per bean in the cross-sentence scan (`fact_check.py:1015,1022`), whose unique strings defeat the `re` cache entirely. *Refactor:* hoist constant patterns to module level (the codebase already does this elsewhere); cache the per-bean patterns alongside `_phrase_regexes_cached`. *Risk:* none (identical patterns). *Effort:* S.

### P8 — Low — Server cold start 1.5–2.1 s, about half of it langgraph imported for three reused nodes

`-X importtime`: `mcp_server` cumulative 2,195 ms; the `content_core.graph` → `langgraph` chain is 1,072 ms of it; the `mcp` SDK is 943 ms (unavoidable). The MCP layer reuses only the graph's validate/research/outline nodes (`mcp_server.py:121-128`), yet every server launch — and every stdio test spawn (see the 1.67 s tool-list test) — pays the full langgraph + langchain_core import. *Refactor:* lazy-import the Core/graph inside `_get_core()` / `_core_for_brief()` so `tools/list` and the pure-checker tools start without it; the first `assemble_context` pays instead. *Risk:* low (check for import-order side effects). *Effort:* S.

### P9 — Low — `list_drafts` fully parses and validates every draft file

97 reads / 5.8 ms at 99 drafts (536 KB) to produce summary rows; strictly linear in count × size (≈60 ms and ~5 MB parsed at 1,000 drafts). JSON admits no partial read, so the alternatives are a write-maintained index file (write-path complexity + a new staleness surface) or accepting the scan. *Recommendation:* accept today; revisit only if the draft count passes ~500. *Effort if done:* M. *Risk:* medium (index drift).

### P10 — Low — Developer loop ≈37 s; the two concurrency tests are 26% of pytest

pytest 18.65 s of the ≈37 s full loop. The top two tests (2.46 s + 2.40 s) are multi-process lock tests whose cost is inherent (process spawns + timing windows) — they are the regression net for the round-1 H3 lock break and should not be deleted or slept-down. The 1.67 s stdio test shrinks if P8 lands. `pytest-xdist` parallelism is *plausible* (most tests isolate stores via env overrides) but **unverified** — some tests read the real stores, and xdist safety needs an audit, not an assumption. *Effort:* S–M to investigate. *Risk:* low (test-only), with the audit caveat.

## NOT worth doing (measured fine, or premature)

- **`export_core_data`** — 34–41 ms to snapshot 118 files / 829 KB. Nothing to fix.
- **`build_pack_proposals`** — 111 ms on the real 10-page site, 282 ms at 30 pages, ~linear. The brand builder underneath is not a bottleneck at real site sizes.
- **Style layer** — `build_profile` over the full 95,507-word literary corpus: 370 ms, one-off at setup. `style_match`: 73 ms at 42k, 2 ms at article size — the cheapest checker in the gate. Leave alone.
- **Image layer** — fulfilment 12.7 ms against a 500-entry library (linear in library × slots; a real client library is tens of entries); `image_check` / manifest ≤0.7 ms.
- **Research-pack whole-file rewrites** — `propose_pack_item` stays ~2.5 ms flat while the pack grows 57 KB → 78 KB (fetched pages ride inside the file); `_detect_contradictions` is a single O(items) regroup per proposal (`research.py:167-200`). At manual-approval scale (tens of items) incremental/patch storage would be pure complexity. Do not build it.
- **Draft whole-file rewrite per `save_section`** — 4.0 ms at 259 KiB / 173 sections (0.4 ms at 5 sections). The session-aggregate is O(N²) in bytes but the constant is tiny: an entire 173-section drafting session spends ≈0.35 s total on draft IO. Leave alone.
- **Lock granularity** — locks are per draft id, held only around load→modify→store; there is no global lock and no measured contention outside the deliberate concurrency tests. Nothing to fix.
- **Parallelizing the gate checkers** — at real article size the whole gate is 95 ms; process spawn + serialization would eat the gain. Only the 42k stress case benefits, and book-length input is not a real workload for this tool (articles are gated per article).
- **In-memory caching of drafts/packs** — mutation-heavy stores shared across processes; a stale copy would undermine the lock protocol's assumptions. The correct fix for repeated *loads* is P4(a)/(b), not caching mutable state.
- **Rewriting `normalize_for_match` with translate tables *first*** — P2(c) is the riskiest item in this report; the cheap, zero-risk wins (P2a/P2b, P1's prepared tables) should be measured before anyone touches the normalizer's internals.

## Recommended fix-round scope (for Smith to choose)

**Tier 1 — high impact, no behaviour risk (all byte-identical by construction):**
P4(a) within-call glossary dedupe · P5 linter lazy/skipped preparation · P2(b) bounded short-input cache on `normalize_for_match` · P7 hoisted/cached compiled patterns.
*Estimated effect (estimate, not measurement):* gate at 42k ~3.56 s → ~2.4–2.8 s; `final_gate` file reads 9 → 4; lint for banned-term-free brands down ~5–10×.

**Tier 2 — medium effort, still byte-identical-able, verdicts pinned by the existing suites:**
P1 prepared phrase tables + mask-array matcher + `_carries_violation` memoization · P6 fact_check single-split + norm threading · P3(ii) combined check tool (or (iii) content-addressed checker memo, with the input-completeness proof as part of the work).
*Estimated effect:* craft at 42k ~1.62 s → ~0.6–0.9 s; the 7-call writer loop at article size ~476 ms → ~200–250 ms if the loop uses the combined path/memo.

**Tier 3 — polish, optional:**
P8 lazy langgraph import (~1 s off every server launch and stdio test) · P10 xdist investigation · P9 `list_drafts` index (defer until ~500 drafts) · P2(c) translate-table normalizer (only with differential testing over the shipped corpora).

**Explicitly not recommended at any tier:** merging quality's clarity craft into the gate's craft (behaviour change disguised as a refactor — P3 note); caching drafts/packs; parallelizing the gate.

*Profiling scripts: `/tmp/perf/profile_gate.py`, `profile_tools.py`, `profile_batch.py`, `profile_loop.py`, `profile_loop2.py`, `profile_draft_io.py` (ephemeral; re-runnable against the repo's `.venv`).*

---

## Fix round (2026-11-22, same day) — Tiers 1 + 2 + 3, all landed

Smith approved all three tiers. Every refactor is behaviour-preserving, proven by a **differential baseline**: before any code changed, the full gate pipeline + every checker + `quality_score` + the 7-call MCP writer loop were run over a fixed corpus (the 7 golden graph cases, every stored draft, the 5 CNM calibration posts, the mambagiaokhoe v2/v3 articles, and a 42,336-word stress draft — 124 corpus items) and the complete JSON outputs saved. The identical harness re-ran after Tier 1+2 and again after Tier 3: **byte-identical both times, zero differences.** All suites green at the end (below).

### Before / after (best of 3, same method and corpus as the review)

| Benchmark | Before | After | × |
|---|---|---|---|
| gate @ 1,172 words | 0.095 s | 0.017 s | 5.6× |
| gate @ 4,704 words | 0.378 s | 0.068 s | 5.6× |
| gate @ 42,336 words | 3.405 s | 0.623 s | 5.5× |
| gate+quality @ 42,336 words | 4.789 s | 0.789 s | 6.1× |
| lint @ 42,336 (banned-term-free brand) | 0.430 s | 0.030 s | 14× |
| fact @ 42,336 | 1.422 s | 0.411 s | 3.5× |
| craft @ 42,336 | 1.544 s | 0.170 s | 9.1× |
| quality standalone @ 42,336 | 3.244 s | 0.626 s | 5.2× |
| writer loop, 7 tool calls @ 1,172 | 0.451 s | 0.106 s | 4.3× |
| `final_gate` file reads | 9 | 5 | — |
| `final_gate` (+quality) file reads | 10 | 6 | — |
| `craft_check` tool file reads | 8 | 3 | — |
| MCP server cold start (`import mcp_server`) | 1.30 s | 0.64 s | 2.0× |
| pytest (full suite) | 471 passed / 18.65 s | 479 passed / 12.18 s | — |

One correction to the Tier-1 estimate above: `final_gate` reads land at **5, not 4** — the brand draft and the facts profile are two distinct store files that happen to share the stem `mambagiaokhoe.json`, so both are genuinely needed; the glossary went ×5 → ×1 as estimated (×2 with quality: the gate's per-section craft and quality's whole-text clarity craft are two separate checker calls by design, each resolving it once).

### What landed, per tier

**Tier 1.** P4(a): `craft_check_sections` resolves the glossary once per call and passes it down; the `craft_check` tool loads the server brand once for both derived values (`_brand_max_sentence_words` gained an optional brand parameter). P5: `lint_section` builds the per-sentence and whole-text preparations only when the brand has banned terms, and the whole-text preparation lazily (only for a term the sentence pass never saw). P2(b): bounded memo on `normalize_for_match` — inputs ≤ 256 chars via `lru_cache(maxsize=4096)`; longer inputs bypass. P7: clause-split patterns and per-bean `\b…\b` patterns in fact_check hoisted/cached (`_token_boundary_re`), the number-protection and sentence-opening regexes in linter/craft hoisted.

**Tier 2.** P1: craft phrase matching rebuilt on prepared phrase tables (`_phrase_table`, cached per vocabulary: normalized + length-sorted + deduped + compiled) with a mask-array single-pass matcher whose acceptance rule reproduces the legacy masking semantics exactly (guards evaluated on the effective text; a span containing a masked letter is rejected). One conservative exception: a vocabulary containing a **single-character phrase** routes to the kept legacy implementation — the one shape (a 1-char phrase masking exactly where a longer phrase has a space) the mask scan cannot reproduce. `_carries_violation` is memoized per `craft_check` call. P6: fact_check splits and normalizes sentences once per section and shares them across the delivery loop, the main loop and the cross-sentence block; the origin and other-region blocks share one clause split; `find_claim_detail` accepts a `prepared_norm` so the claim norm is derived once per sentence instead of per phrase list. P3: option **(ii)** was chosen over (iii) — a new combined tool **`check_draft_all`** (61 tools total) that resolves brand/facts/style once, runs lint/fact/craft/style once each and feeds lint/fact into quality via the gate's own `_precomputed` reuse. It changes no state and returns sub-reports that mirror the individual tools' shapes; a test pins its brand/fact/craft/quality sub-reports to the individual tools' outputs on the same draft. (iii)'s content-addressed memo was rejected as the medium-risk option: its stale-verdict safety rests on an input-completeness proof, while (ii) is stateless and cannot go stale. As flagged, quality's clarity craft pass was **not** merged into the gate's per-section craft — different aggregation, would be a behaviour change.

**Tier 3.** P8: lazy langgraph — `mcp_server` imports `Core` lazily via `_core_class()`, and `content_core/__init__.py` resolves `build_graph`/`run_flow` through PEP 562 `__getattr__` (the eager package-level graph import was the actual chain every submodule import paid). Server import now loads zero langgraph modules; the remaining ~0.64 s is the MCP SDK itself. P2(c): the per-character-table normalizer was **adopted** — the differential proof was airtight: zero differences vs the original implementation over all 1,112,064 Unicode codepoints, all 57,600 pairs of a 240-char tricky alphabet (Vietnamese precomposed letters, combining marks, confusables, fullwidth, ligatures), 30,000 seeded fuzz strings, and the full shipped corpus (230 texts / 859 sentences / 2,475 tokens). The original implementation survives as the reference in a committed differential test (`tests/test_perf_refactors.py`). P10/xdist: **investigated, not adopted** — the suite passes under `pytest -n 4` (478/478 at investigation time) but takes 16.66 s vs 11.75 s serial on this 1.5-shared-vCPU VM (worker startup + lock-test contention, no spare cores); the package was uninstalled again and the runner is unchanged. P9 (`list_drafts` index): not built — deferred per this report until ~500 drafts.

### Verification at the end of the round

- Differential corpus (124 items): **byte-identical** to the pre-round baseline.
- pytest **479 passed** (471 + 8 new pins: phrase-matcher differential vs the legacy matcher, phrase-table dedupe/order, normalizer cache bound, zero-banned-brand lint, letter-dotted whole-text catch, the P2c normalizer differential, and two `check_draft_all` tests); tool-count pins updated 60 → 61.
- run_golden **7/7**, run_model_golden **6/6**, test_mcp_client **24/24**, test_mcp_http **PASS (61 tools)**, all 4 demos exit 0.
- All 5 evaluation probes at their baselines (fact recall/FF OK, linter OK, sweep inversions 9 ≤ 12, CNM craft/violations OK, parity 16/16).

*Fix-round scripts: `/tmp/perf/diff_harness.py`, `/tmp/perf/bench.py`, `/tmp/perf/norm_diff.py`; baseline + after dumps in `/tmp/perf-baseline/` (ephemeral).*
