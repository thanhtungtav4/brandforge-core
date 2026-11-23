# Code Review — Round 2 (2026-11-22)

**Scope:** read-only review of the whole content core at HEAD `77f4d74` (article-images feature) on top of `922d212` (pm docs) on top of `f52ab20` (round-1 fixes). 414 tests, 39 MCP tools. Three parallel reviewers: (A) the new article-images layer, (B) regression sweep of round 1, (C) docs/tests/product-claims consistency for the new material. No code was changed; all probes ran in temp dirs with env-overridden stores; the git tree was clean before and after (including after a full pytest run).

*Bookkeeping note: the review brief named HEAD as 922d212; the actual HEAD is its child 77f4d74. Both commits were present and reviewed; nothing turns on the difference.*

## Executive verdict

The core remains sound, and round 1's fixes all hold — the regression sweep reproduced every headline repro exactly (money-role scoping, lock behaviour, validator contracts, diacritics parity, performance range, all five evaluation probes at their recorded baselines). The new image layer is well built in its mechanics: deterministic ranking with an exact threshold boundary, hardened alt normalisation, a genuinely advisory boundary with `final_gate`, and an honestly documented vision trust model. It is **not yet at the text layers' standard**: 4 Medium findings, all in the images layer, all of the same family — the layer's guarantees are stated slightly stronger than the code enforces (a photo can ship twice in a "ready" manifest; a brand with no visual style still gets hard style-lock errors; a naturally-written string palette silently garbles every generation prompt; stored vision grades are not re-validated by the checker). None is a verdict bypass for text, none touches the gate. **0 Critical · 0 High · 4 Medium · 11 Low · ~8 Info.**

## Findings

### Medium (4)

| # | Finding | Evidence |
|---|---|---|
| M1 | **Same photo can ship twice in one article with `ready: True`.** Fulfil's never-double-use tracks library *entry ids* (sha1 of ref+description), so the same photo added twice with different descriptions is two entries and can fulfil two slots; the checker has no general duplicate-ref rule (only featured-vs-content reuse). | `mcp_server.py:1445-1447`, `loaders.py:702-704`, `images.py:394-399`. Repro (MCP, isolated stores): SHARED.jpg under two descriptions fulfilled img-s1 and img-s2; `image_check` issues `[]`; manifest `ready: True` with the ref twice. Also reproducible via plain `set_image` with the same ref on two slots. |
| M2 | **A brand with NO `visual` section still gets hard `style_not_locked` for AI images with empty `prompt_used`** — contradicting the module's own contract ("no style block, no style lock, everything else works"). The empty-prompt branch fires unconditionally; with no block to compare, any non-empty text passes, so the issue enforces nothing about style exactly where it fires, yet blocks manifest `ready`. | `images.py:97-103` (contract) vs `images.py:425-432` (unconditional branch). Repro: visual-less brand, ai image, `prompt_used=""` → `style_not_locked`. Existing test misses it (its fixture passes the block-less plan prompt as `prompt_used`). |
| M3 | **`style_block` garbles a string-valued `palette`/`avoid` into per-character joins, and no layer can catch it.** `", ".join(...)` over a string iterates characters; there is no schema validation of the brand `visual` section anywhere. Planning, the style lock and the mismatch check all derive the expected block from the same garbled rendering, so everything stays green while the prompts sent to the image generator are corrupted. A scalar string is the natural way to write a palette in YAML. | `images.py:117-118, 125-126`. Repro: `palette: "warm earth tones"` → `palette: w, a, r, m, , e, a, r, t, h, …`. Related: the README's `medium (photo\|illustration\|mixed)` enum is likewise unenforced (Info). |
| M4 | **`check_images` accepts out-of-range and boolean vision grades in stored drafts** (reported by two reviewers; graded Medium/Low, consolidated at Medium). The checker validates stored grades by type only — no range check, and `bool` is an `int` subclass — while `record_vision_check` correctly rejects bools and out-of-range values at write time. The same validator/checker contract-gap class as round 1's M1/M2. | `images.py:428-444` vs `mcp_server.py:1322-1327`. Probe: stored grades {3,2}, {2,−1}, {5,5} → no issue, no flag, counts as graded for manifest `ready`; {True,True} → read as grade 1 (`vision_reservations`). Reachable via hand-edited/legacy draft JSON, not via the tool. |

### Low (11)

- **L1 — Duplicate section ids self-collide the plan.** Slot ids are `img-<section_id>` with no uniqueness check; two sections with id `s1` produce two identical plan rows, the fulfilled image is flagged for duplicating *its own* alt, and the manifest emits it twice. Reachable only via hand-edited drafts or direct `plan_images` use (`save_section` overwrites by id). `images.py:164`.
- **L2 — `style_block_mismatch` blames the majority.** The reference block is whichever AI image comes first in plan order; with the featured image off-brand and two correct images after it, both correct images are flagged. The verdict stays safe (the odd one gets `style_not_locked` when a brand block exists), but the writer is told to regenerate the good images. `images.py:471-479`.
- **L3 — Stuffed-alt evasion via glued/spaced keyword tokens.** Alt `"càphê càphê ngon"` or letter-spaced keyword passes the stuffing check; the linter's letter-run collapse (round-1 fix) is not reused by `check_images`. Bounded impact (visibly spammy alts), same evasion class the project previously closed for banned terms. `images.py:365-371`.
- **L4 — `fulfil_images_from_library` never resolves the brand.** It reads the library for `brief.brand_id or "moc-brew"` without the brand resolution the other image tools do, so a draft whose brand draft was removed gets structured `unknown brand_id` errors from `image_check`/manifest but a silent "everything needs generation" from fulfil. Harmless direction, inconsistent behaviour. `mcp_server.py:1436-1441`.
- **L5 — `set_image` overwrite silently resets more than documented.** Overwriting rebuilds the entry from the call's arguments: `caption`, `description`, `prompt_used` reset to `""` if not re-passed; only the vision-check drop is documented. Failure direction is safe (emptied prompt re-triggers `style_not_locked`). `mcp_server.py:1239-1240, 1261-1269`.
- **L6 — README alt-length range off by one.** README says flagged is "alt 125–200 chars"; the code flags stripped length 126–200 and hard-fails ≥201. `README.md:731` vs `images.py:383-388` (probe-verified at 125/126/200/201).
- **L7 — README's library-ranking query description is wrong for the featured slot.** README (and the fulfil tool docstring) describe ranking vs "heading/intent + keyword"; the featured slot has no heading/intent and actually queries title + topic + keyword — as `images.py`'s own docstring correctly states. `README.md:705-708, 680`; `mcp_server.py:1420-1427`; `images.py:243-254`.
- **L8 — One stale check-count survived in the README.** `README.md:763` still says `test_mcp_client.py` has "22 end-to-end checks"; two other places correctly say 24. Same drift class as round 1's M18; the fix round missed a third instance.
- **L9 — PM context files are stale about the images layer (but identical to each other).** `.pmcontext.md`/`AGENTS.md` line 21 says "377 tests" (suite is now 414) and neither file mentions the article-images layer, the library, or the WP manifest — the very artefact `pmdecisions.md`'s WordPress exporter will consume. Ordinary drift (the docs were committed before the feature), recorded so the link gets written down.
- **L10 — `tests/test_images.py` pinning/coverage gaps.** Cap and alt-cap pins are self-referential (compared against the module's own constants, so a constant change stays green); the 0.35 threshold is not pinned at all; exact alt boundaries (125/126/200/201), the client-wins tie-break, contended dedupe (one entry best for two slots), replan-preservation and `set_image` vision-drop are untested; the two local-file flags are confounded in a single 300KB `.bmp` fixture; one decorative `assert plan` at line 434. Code behaviour probed correct in each case — this is coverage, not defects.
- **L11 — Advisory-boundary test proves less than its name.** `test_images_do_not_change_final_gate` uses a draft whose gate already fails (`passed=False` probed); "a *passing* draft stays passing with broken images" is untested. Code reading supports the claim (no image path into `gates.evaluate_gate`); the test does not.

### Info (~8)

- Style lock verifies block *presence*, not scene fidelity — a `prompt_used` consisting of only the style block passes. Matches the documented contract; scene correctness is delegated to vision grades.
- The vision trust model is documented honestly in code, tool docstrings and README: grades are writer-supplied, "the core enforces the RECORD, never the seeing", mirroring the Quality Profile's judged axes. Nothing overclaims.
- The checker's local-file path does `os.path.exists`/`getsize` on writer-supplied refs (e.g. `/etc/passwd` → a `file_format` flag; a large file's exact size appears in a flag detail). No contents are ever read; local single-user tool; documented behaviour.
- Library ranking is recall-only (query-token coverage, no entry-precision term): a broadly-stuffed description can tie a precise one at 1.0, winner decided by entry id. Deterministic; quality observation only.
- Style-lock verification is normalised (`normalize_for_match`), not byte-verbatim as README's "appended verbatim" suggests — planning appends byte-identically; checking tolerates case/diacritics/whitespace differences. Safe direction.
- Manifest returns `warnings` as the same list object as `flagged` (`mcp_server.py:1387-1388`) — the README claim ("vision-unchecked under `warnings` only") is true but the duplication is worth knowing.
- `demo_images.py` isolates only the two stores it writes (drafts, image library) — sufficient for its flow; the test fixture isolates three.

## Regression sweep (round 1 → round 2)

| Item | Expectation | Current result | Verdict |
|---|---|---|---|
| H1 money-role sentence scoping | Cross-sentence ship fee raises `wrong_price` | Repro raises `wrong_price`; same-sentence excuse intact by design | ✅ Holds |
| H2 shipped CNM brand draft | claims `[]`, max sentence 39, no `var(--…)`, 1 claim | All invariants verified in the live file + pinned test | ✅ Holds |
| H3 draft lock | >30s hold unstealable; SIGKILL frees; R7 repro lossless | SIGKILL re-acquire 0.00s; mtime backdated 1h while held → no steal, structured timeout error; 30/30 sections, 2/2 gate history | ✅ Holds |
| M1/M2 validator contracts | Accepted facts docs consumable, no KeyError | Minimal doc flows through `check_section` and graph research; raw doc also safe | ✅ Holds |
| M3 craft diacritics parity | Accented/unaccented pair scores identically | Independent pair: 63.0 = 63.0, identical weak hits | ✅ Holds |
| Performance (~42k-word draft, quality gate) | ~5.4–8.1s post-fix | 7.10s (quality) / 5.57s (plain) on a comparable synthetic draft | ✅ No regression |
| Fact probe | 96.6% recall / 2.4% false-flag / marked 4/5 | 112/116, 2/84, 4/5 — exact | ✅ Holds |
| Linter evasions | 0/14 | 0/14 | ✅ Holds |
| Sweep inversions | 9 (documented residual) | 9/420, same `weak`-injector class | ✅ Holds |
| CNM style/craft | craft 90×5, violations 10, trial style 85 | Exact match | ✅ Holds |
| MCP parity | 16/16 stdio↔HTTP | 16/16 | ✅ Holds |
| M10 judgement call | Docs match the rescoped `source_type` trust model | "Honest scope" paragraph intact in `brand_extract.py`; no drift | ✅ No drift |
| L6 partial | Draft delete/list tool still absent, documented as a feature | Still absent at 39 tools; still documented as a feature | ✅ As documented |

**Regressions found: none.**

## Verified sound

- **Image mechanics:** threshold boundary exact (7/20 tokens = 0.35 fulfils, 6/20 does not); tie-breaking deterministic (score → hits → client-before-ai → entry id); caps exact (0 sections → featured-only plan; 20 sections → featured + 5); re-plan keeps fulfilments for surviving slot ids; unknown draft/slot/brand ids and bad `set_image` inputs return structured `{"error": ...}`.
- **Alt checker normalisation:** duplicate-alt detection catches the round-2 evasion classes (trailing punctuation, double spaces, zero-width insertion, case change, diacritics removal) via the hardened `normalize_for_match`; standard stuffed forms (punctuation-separated keyword repeats, keyword-only alt) are caught.
- **Advisory boundary, both directions:** `final_gate`/`save_section` leave `draft["images"]` byte-intact; broken image state does not change the gate verdict; `gates.evaluate_gate` never reads image state.
- **Manifest vs WordPress:** keys `{role, section_id, ref, filename, alt_text, caption, description}` match WP media semantics; fulfilled-only, plan order; `ready` semantics match the docs (vision-unchecked warns, grade 0 blocks, grade 1 warns).
- **Library store safety:** brand ids pass through the hashed `stored_file_stem` (traversal contained); refs are stored strings, never content-read; re-adding the same ref+description is an idempotent replace; per-brand isolation holds.
- **README accuracy:** tool count 39 and all 8 tool names, cap, budgets, aspects, webp target, threshold value, vision semantics, library location/shape, manifest fields — all verified against code (exceptions are L6/L7/L8 above).
- **PM docs:** `.pmcontext.md` and `AGENTS.md` are **byte-identical** (4038 bytes, same md5); `pmdecisions.md` does not contradict the context (the WordPress-draft decision matches the context's "Settled 2026-11-22" note; the uncaptured client JTBD is a different question and remains honestly open).
- **Test hermeticity:** full pytest (414 passed) left `git status` clean and the `data/` + `drafts/` trees byte- and timestamp-identical; `tests/test_images.py` isolates its stores via env overrides; `demo_images.py` asserts real outcomes (10 substantive asserts, no try/except — failures exit non-zero) over the real stdio transport.
- **Repo hygiene:** `.gitignore` covers `data/image_library/`; no user-data-shaped file from the two new commits is tracked (code, tests, docs, simulated-brand data only).

## Ranked fix backlog (proposed; Smith approves scope as usual)

1. **M1** — track used *refs* in fulfil + add a `duplicate_ref` hard issue for any ref on 2+ slots.
2. **M2** — fire `style_not_locked` for missing `prompt_used` only when the brand actually has a style block.
3. **M3** — validate the brand `visual` section at load/save (palette/avoid must be lists; medium in the documented enum), or accept a scalar string as a one-item list — pick one and pin it.
4. **M4** — `check_images` re-validates stored vision grades (int, not bool, 0..2); invalid stored grade → `vision_unchecked`-class flag or a named issue.
5. **Low batch** — plan-time duplicate-section-id guard (L1); mismatch reference = brand block when one exists (L2); reuse the linter's letter-run collapse in the stuffing check (L3); fulfil resolves the brand like its sibling tools (L4); document `set_image`'s full-replace semantics (L5); README corrections (L6, L7, L8); refresh the pm context files for the images layer and the 414 count (L9); literal pins + the missing boundary/tie/contention tests and a passing-draft advisory test (L10, L11).

*No documented non-goal was re-reported: D13, spelled-out money, cross-sentence claim splits, word-salad/coherence scoring, the 9 residual sweep inversions, and sub-scale word numbers remain settled as recorded in evaluation/SUMMARY.md and the round-1 addendum.*

---

## Fix round — addendum (2026-11-22)

Smith approved fixing **only the 4 Medium findings**; the 11 Low findings are explicitly **left as-is** per that scope choice (they remain documented above for a future round). Each Medium was fixed fail-first from the report's own repro; 15 new tests in `tests/test_images.py`; no design values changed, no pins updated.

| # | Disposition |
|---|---|
| M1 | **Fixed.** The uniqueness unit is now the normalised ref (`images.normalise_ref`: stripped, `os.path.normpath` for local paths). `check_images` raises a hard `duplicate_ref` when two fulfilled slots share a normalised ref — the featured-vs-content pair is still diagnosed once, as `featured_reused`. `fulfil_images_from_library` tracks used refs alongside used entry ids and `best_library_match` skips entries whose ref is already used, so the same photo stored under two descriptions can fill only one slot. Library entry-id dedupe (re-add = replace) is unchanged. |
| M2 | **Fixed.** `style_not_locked` for a missing `prompt_used` now fires only when the brand actually has a style block; a visual-less brand has no lock to break, per the module contract. Behaviour with a visual section is unchanged. Both directions pinned by tests. |
| M3 | **Fixed.** New `loaders.validate_visual`: list fields (`palette`, `avoid`) accept a list of strings or one plain string — a string is split on commas ("brown, cream" → two items; "warm earth tones" → one) — and anything else raises a `ValueError` naming the field; scalar fields must be strings. Applied at every boundary: `load_brand`, `validate_brand_draft`, `load_brand_draft`, and defensively in `images.visual_of` / `images.style_block`, so the renderer can never emit a per-character join again. The report's "w, a, r, m" repro is a regression test. |
| M4 | **Fixed.** `check_images` re-validates stored vision grades with the write side's rule (strict int, not bool, 0..2). A present-but-invalid record never counts as checked in either mode; in complete mode it is a hard `vision_grade_invalid` issue, so it blocks manifest `ready` until the grades are re-recorded. Pinned for stored grades {3,2}, {2,−1}, {5,5}, {True,True}, {"2",2} plus an end-to-end tampered-draft manifest test. |

**Verification after fixes:** pytest 429 passed (414 baseline + 15 new), run_golden 7/7, run_model_golden 6/6, test_mcp_client 24/24, test_mcp_http PASS (39 tools), all four demos exit 0 (demo_images OK). No evaluation number moved — the fixes touch only the image layer's checker/store boundaries.

## Closure addendum — L3 (2026-11-23, 2h review run)

L3 was the next untouched round-2 Low after the Mediums closed; L1, L2,
L4–L11 remain untouched.

- **L3 — FIXED.** `check_images`' stuffed-alt scan now also catches
  the two evasion shapes the report demonstrated: glued keyword
  tokens (`"càphê càphê ngon"`) and letter-spaced keywords
  (`"c à p h ê …"`). The alt is additionally checked
  letter-spacing-collapsed (reusing the linter's
  `collapse_letter_spacing`, the report's prescribed reuse) and with
  spaces removed, so the keyword is counted in its glued form too;
  a glued alt made of only the keyword is likewise stuffed. The new
  disjuncts are purely additive — the old count and token-set clauses
  are kept verbatim, so nothing previously flagged can un-flag. No
  gate, threshold, or checker contract was touched; only the image
  layer's alt scan. Evidence: fail-first tests — the glued-repeat,
  letter-spaced-repeat, letter-spaced-only-keyword, and glued-only-
  keyword cases all failed pre-fix; 3 new tests in
  `tests/test_images.py` (plus a control pinning that a single glued
  occurrence, `"càphê rang xay thơm ngon"`, is NOT stuffed).
  Differential over the real stores: `image_check` (both modes) +
  `get_image_manifest` over all 138 stored drafts —
  byte-identical before/after (2 drafts carry image plans; 0
  stuffed_alt in the store either way).

**Verification (fresh output, this fix):** pytest **622 passed**
· run_golden **7/7** · run_model_golden **6/6** ·
test_mcp_client **24/24** · test_mcp_http **PASS** (72 tools) ·
demos **4/4 exit 0**.

## Closure addendum — L1 (2026-11-23, 2h review run)

L1 was the next untouched round-2 Low after L3 closed; L2, L4–L11
remain untouched.

- **L1 — FIXED.** `plan_images` now guards slot-id uniqueness at
  plan time: it raises `ValueError` naming the duplicated slot id
  when two planned sections share an id, when two sections both
  have no id (both map to `img-`), or when a section id of
  `"featured"` collides with the featured slot itself. The guard
  covers the featured slot plus the planned slice only
  (`sections[:MAX_IN_CONTENT]`); a duplicate id past the cap never
  becomes a slot and still plans fine. The MCP tool needs no change:
  its existing `except ValueError` returns the guard as a structured
  `{"error": ...}` and stores no half-written plan. Valid drafts are
  unaffected — `save_section` overwrites by id, so a duplicate can
  only arrive via a hand-edited draft or a direct call — and the
  plan builder now reuses the single guarded slice instead of
  re-slicing. Evidence: fail-first tests — the duplicate-id,
  missing-id, `featured`-collision, and MCP end-to-end cases all
  failed pre-fix (the MCP case showed the live bug: two `img-s1`
  rows in one plan); 5 new tests in `tests/test_images.py`
  (4 unit + 1 MCP, plus a control pinning that a duplicate past
  the cap still plans). Differential over the real stores:
  `plan_images` over all 151 stored drafts — byte-identical
  before/after (0 drafts carry a duplicate section id, so the guard
  fires nowhere in the store). `check_images` is unchanged by
  design: the guard lives at plan time, the one place a plan is
  built.

**Verification (fresh output, this fix):** pytest **711 passed**
· run_golden **7/7** · run_model_golden **6/6** ·
test_mcp_client **24/24** · test_mcp_http **PASS** (86 tools) ·
demos **4/4 exit 0**.
