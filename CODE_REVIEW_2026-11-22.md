# Code Review — brandforge-core (content core)

**Date:** 2026-11-22 · **Baseline:** git main @ `4561d72`, 339 tests, 31 MCP tools · **Mode:** read-only review (4 parallel reviewers: core logic / MCP+storage / fetch+builder+data / tests+docs+hygiene). No code was changed; this report is the only new file. Documented non-goals in `evaluation/SUMMARY.md` (D13, spelled-out money, cross-sentence claims, word-salad/coherence, the 9 residual sweep inversions, sub-scale word numbers) were checked for drift and **not** re-reported as defects — none found.

## Executive verdict

**The core is sound in architecture and unusually well tested — but "complete" needs one qualification round.** The load-bearing claims hold up under adversarial checking: the single gate rule is real (one `evaluate_gate`, both call sites delegate), layer separation is intact, path traversal is contained in every store, the golden design values are pinned as literals, and all 339 tests + goldens + demos reproduce green. No Critical findings.

However, three High findings mean the core is not yet as finished as its own summary says:

1. A natural copy shape (a shipping/discount sentence followed by a price sentence) silently excuses a **wrong price** past the hard fact gate.
2. The shipped Cao Nguyên Mộc brand draft is a **pre-fix artefact**: the live gate path still judges that brand by Mộc Brew's prohibited claims, a CSS-variable "font", a blog-thumbnail "logo", and the old 25-word sentence cap — the builder was fixed, the data never was.
3. The new draft lockfile's mutual exclusion **provably breaks** for any critical section held longer than 30 s — the exact lost-update class it was built to close can recur.

Below those sit a coherent Medium theme: several Tier-2 hardening items (unified error dialect, stored-shape validation, validator/checker contract agreement) were finished for drafts/facts/quality but **never extended to the style and brand-draft stores**, and the fetch layer has a cluster of real-world robustness gaps (charset, redirect base, sitemap parsing).

**Counts: 0 Critical · 3 High · 19 Medium · 20 Low · ~9 Info.**

---

## Findings by severity

### High

| # | Finding | Evidence |
|---|---|---|
| H1 | **Money-role markers leak across sentence boundaries — a wrong price is excused by an unrelated earlier sentence.** `_money_role` claims a "same sentence window" but searches the previous ~90 raw chars with no sentence/clause boundary check. | `content_core/fact_check.py:269-285`. Repro: `"Phí ship được tính riêng theo đơn. Gói 250g có giá 200.000đ hôm nay."` → `check_section` returns `[]` (no `wrong_price`); same for a preceding "giảm" sentence. Control (price sentence alone) flags correctly. |
| H2 | **Shipped CNM brand draft is a pre-fix artefact consumed live by the gate.** Its prohibited claims are Mộc Brew's list, top font is `var(--display)`, logo is a blog thumbnail, max sentence is 25 not the derived ~39. `_resolve_brand_facts` returns the stored draft as the brand dict for `fact_check`/`final_gate`/`quality_score`, so CNM drafts are blocked on claims CNM never adopted. SUMMARY records the CNM *style profile* rebuild; the *brand draft* was never rebuilt. | `data/brand_drafts/cao-nguyen-moc.json`; `mcp_server.py:303`; builder-side fixes at `content_core/brand_extract.py:535-545`. |
| H3 | **Lockfile mutual exclusion breaks after 30 s.** Lock file is created empty (no pid/token), mtime never refreshed while held, any lock older than 30 s is unconditionally unlinked by a contender, and the holder's `finally` unlinks whatever is at the path — including a *new* owner's lock. A second race: two contenders can both stat the same stale lock and the second unlink can delete the first contender's fresh lock. | `mcp_server.py:230-286`. Repro (mtime backdated): second acquire succeeds while first holder is still inside; after first release, a third acquire succeeds while the second still holds → three-way exclusion broken. |

### Medium

| # | Finding | Evidence |
|---|---|---|
| M1 | **`check_section` crashes `KeyError: 'price_display'`** on a facts doc `validate_facts` explicitly accepts (either/or rule: `price_vnd_per_250g` without `price_display` is valid; the checker direct-indexes the display field). Reachable via `save_facts` → `load_facts` → `final_gate`. | `content_core/loaders.py:147-155` vs `content_core/fact_check.py:613` |
| M2 | **Graph `research` node crashes `KeyError: 'keys'`** on facts entries `validate_facts` accepts (validator requires only `id`+`content`; the node does `e["keys"]`). Any second brand saved via `save_facts` without `keys` in every entry breaks the whole graph flow. | `content_core/loaders.py:180-184` vs `content_core/graph.py:99-100` |
| M3 | **Craft layer never normalises diacritics** — weak-word and filler detection are raw substring matches on `text.lower()`, unlike the linter/fact checker (hardened in audit R2 precisely because unaccented Vietnamese is common). Craft is a *blocking* layer for blog/product. | `content_core/craft.py:386-399`. Repro: accented text scores 67 with 3 weak hits; the same text unaccented scores 85 with 0 hits. |
| M4 | **Performance: linter re-normalises/re-compiles per (term × sentence); quality-enabled gate re-runs every checker.** Measured: ~42k-word draft → `lint_draft` 5.6 s, `evaluate_gate` 10.5 s, with quality profile 21.0 s (quality layer re-runs `check_draft`/`lint_draft`/`craft_check` from scratch). ~1.6k-word blog: 1.8 s vs 0.57 s. Constant factor ~50× what the algorithms need. | `content_core/linter.py:440-459`; `content_core/quality.py:222,229,287` |
| M5 | **Lockfile, second class:** dead (SIGKILLed) holders block writers for the full 10 s timeout then hard-fail; lock is non-reentrant (latent 10 s deadlock for any future nested tool composition). | `mcp_server.py:230-286` |
| M6 | **Error dialect not unified: 6 tools raise raw exceptions** for invalid/too-long ids and corrupt files (`get_style_profile`, `get_brand_draft`, `style_match`, `assemble_context`, `save_facts`, `save_quality_profile` — the last two because the save call sits outside the `try`). Wire-verified: same bad input returns structured `{"error"}` from `get_draft` but `isError=True` plain text from `get_style_profile`. | `content_core/loaders.py:46,73`; `mcp_server.py:120,~379` |
| M7 | **Stored-shape validation inconsistent across stores.** Quality profiles and drafts validate on load; style/brand/facts don't — a file containing `[]` is returned verbatim by `get_style_profile`/`get_brand_draft`/`get_facts` from tools annotated `-> dict`; a `{}` style profile is silently "refreshed" into a husk with no id/name/fingerprint. | loaders + `mcp_server.py` store loaders |
| M8 | **Draft shape check doesn't cover section entries**, contradicting `_load_draft`'s own docstring: a draft with `"sections": ["just-a-string"]` loads fine, then `save_section` raises `TypeError`, `brand_check`/`final_gate`/`seo_check`/`quality_score`/`craft_check` raise raw `AttributeError`. `outline` likewise unvalidated. Needs a hand-edited/legacy file — exactly the case the check was added for. | `mcp_server.py:186-215` |
| M9 | **HTTP host binding unenforced; startup banner lies.** `CONTENT_CORE_HTTP_HOST` passes straight to the server with no allowlist/warning; with `0.0.0.0` the socket listens on all interfaces, an unauthenticated `initialize` returns 200 — while stderr prints "localhost only, no auth". README says "localhost-only by design" — nothing in code enforces it. (Remote reachability not confirmable from the sandbox network namespace; the finding is the missing guardrail + false banner.) | `mcp_server.py:1300-1315`; `README.md:738-744` |
| M10 | **`draft_to_brand_yaml`'s competitor guard is a single truthiness check, bypassed three ways:** (a) mislabelled `source_type="own_site"` at build time — nothing verifies it against the pages' origin, competitor text converts verbatim; (b) `save_brand_draft` (loaders) persists any dict with no schema/reference_only check (contrast `save_facts`, which validates); falsy spellings `0/""/None` also convert; (c) guard inspects only `examples` — competitor text in `anti_examples[].bad` converts untouched. Plus the H2 path: stored drafts become live gate brands regardless of `review_status`/`source_type`. The module's promise ("can *never* become an approved brand by accident") is stronger than the mechanism. Local tool, requires mislabelling or hand-crafting — hence Medium. | `content_core/brand_extract.py:673`; `content_core/loaders.py:308`; `mcp_server.py:608` |
| M11 | **Conversion silently drops `claims_and_guardrails.prohibited_claims`.** The draft carries prohibited claims in two places; the converter builds guardrails from only `requires_evidence` + the unsupported-claim marker. A claim a user added only to the guardrails list vanishes from the approved YAML with no warning. | `content_core/brand_extract.py:606,616-620,716-719` |
| M12 | **Fetch ignores charset entirely** — both fetchers decode as UTF-8 with `errors="replace"`; no charset/Content-Type handling anywhere. Legacy windows-1258 Vietnamese sites (the realistic victim) decode to mojibake, and fingerprint/vocabulary/POV/claims then run over corrupted text with no flag. | `fetch_site.py:378`; `fetch_sample.py:165`. Repro: ISO-8859-1 fixture → title `"Caf�"`. |
| M13 | **Redirect base bug:** link/sitemap discovery resolves against the *pre-redirect* root URL, not the recorded final URL. Repro (local server): `/old/` 301→`/new/home` with relative link `about` → the real `/new/about` is never fetched; a spurious `/old/about` 404 is recorded. Apex→www / http→https redirects also lose absolute links to the final host and seek the sitemap on the old origin. | `fetch_site.py:406-407` |
| M14 | **Sitemap `<loc>` values are regex-scraped, not XML-parsed:** `&amp;` (the XML-mandated encoding) survives literally into fetched URLs (parameter becomes `amp;b`); CDATA locs and `<loc attr=…>` are silently dropped; a `.xml.gz` sitemap child is not recognised as a sitemap and is fetched as a *page* (no gzip handling exists in either fetcher). | `fetch_site.py:289,335` |
| M15 | **Claim extraction still flags suffix/distant negations and idiomatic "tốt nhất"** — and the shipped data proves it: 4 of 5 `site_claims_to_review` entries in the CNM draft are false positives ("Tốt nhất là dùng bã để khử mùi", a heading question, etc.). The same matcher feeds fact_check, where the repros produce **blocking** `unsupported_claim` issues. (Distinct from H1/M6-class items already fixed: prefix negation works; suffix and distant denial don't.) Claim provenance text is also stored as mangled table-flattened prose truncated at 200 chars. | `content_core/brand_extract.py` `find_site_claims`; `content_core/fact_check.py:446`; shipped draft data |
| M16 | **Calibration raw evidence is tracked in git (~5.4 MB)** contrary to the exclusion intent: `calibration/work/homepage_raw.html` (2.9 MB), `bigscript.txt` (1.9 MB), `body_clean.txt` (639 KB) + 20 more files incl. two 0-byte `.err` files. `.gitignore` has no `calibration/` entry; nothing in tests/code/README references the three raw files. | `git ls-files`; committed in `8215b8c` |
| M17 | **README's "No real client data anywhere in this repo" is false at HEAD.** Cao Nguyên Mộc is a real business: its calibration report (`simulated: false` brand YAML, verified real prices in `data/facts_profiles/cao-nguyen-moc.json`) is tracked; README itself acknowledges the real-site run at line 476 — the file contradicts itself. | `README.md:9` vs `calibration/caonguyenmoc-report.md:1`, `README.md:476` |
| M18 | **README Layout still says "29 tools"** — actual is 31 (verified three ways). SUMMARY's core-completion section claims the README pin was updated; line 814 was missed. Document/tree drift exactly where the summary says there is none. | `README.md:814` vs `README.md:634` |
| M19 | **Tool-completeness tests are list-vs-list tautologies.** `TOOL_NAMES` is a hand-maintained literal; two pytest files assert it equals another hand-written literal, and the over-stdio test asserts only a subset (`EXPECTED <= actual`) — adding a tool and forgetting the list passes all of pytest. Only the non-pytest scripts (`test_mcp_client.py`/`test_mcp_http.py`, not collected by `pytest -q`) assert the exact count over the wire. | `mcp_server.py:1278`; `tests/test_mcp_server.py:54,73`; `tests/test_craft.py:183` |

### Low (20)

**Core logic**
- L1. Only the first percent per sentence is examined — a composition percent first makes a genuinely unsupported second percent invisible. `fact_check.py:793-806` (the claim-phrase path documents its bound; the percent path doesn't).
- L2. Brand-context extraction block duplicated verbatim in `gates.py:85-100` and `quality.py:275-285` — the exact wiring whose omission caused the R3-S4 bug class; two hand-maintained copies can drift again. Also `_slugify` and `_WORD_RE`/`_tokens` duplicated between `style.py` and `brand_extract.py`.
- L3. Dead code: `linter.count_phrase`/`phrase_in` (no production caller), `fact_check._ORIGIN_PAIRS_NORM` legacy alias (referenced nowhere).

**MCP / storage**
- L4. `bool` silently becomes `1` for `target_words` — wire-verified on both transports (`save_section`, and inside `make_outline` items). The `store_excerpts` class of bug, still open; inconsistent with the explicit bool rejection in quality grades and facts validation. Also `quality_score` accepts grade `2.0` as `2`.
- L5. Atomic write never fsyncs the directory (rename not durable across power loss, despite the docstring's crash framing); SIGKILL/power-loss temp orphans are never swept; `mkstemp`'s 0600 mode silently propagates to all stored files.
- L6. Unbounded growth & silent corruption: `gate_history` uncapped, no draft delete/list tool (78 draft files on disk already), orphan `.lock` files persist forever; every `list_*` silently skips corrupt files (a broken profile vanishes from listings while direct get errors); corrupt-file errors embed absolute paths — a small regression against the R7 fix that removed paths from id errors.
- L7. MCP `brand_check` free-text path counts **warning** violations (`banned_term_context`) as failing sections, contradicting `lint_draft`'s documented rule that warnings alone never force a rewrite; the same tool's draft path uses the correct list — the two paths of one tool disagree. `mcp_server.py:963-965`.

**Fetch / builder / data**
- L8. `_canonical_url` corrupts IPv6 (`http://[::1]:8080/a` → `http://::1:8080/a`, an invalid URL used as dedupe key and fetch target), strips credentials (dedupe collisions across identities), drops path params; query order and percent-encoding are *not* normalised (missed dedupes → double fetches). `fetch_site.py`.
- L9. No Content-Type check on fetched roots — a binary file returns `word_count: 81` of garbage and would enter a style/brand corpus unflagged; `_SKIP_EXTENSIONS` guards only discovered links.
- L10. `_sanitize_brand_kit` is weaker than the extraction rules it claims to re-apply: generic fonts (`sans-serif`, `inherit`) pass conversion although extraction would reject them; the logo rule is a path substring test (a banner at `…/logo-ish/banner.png` is kept as the approved logo). `brand_extract.py:638`.
- L11. `fetch_sample.fetch_url` never records the post-redirect URL (no `geturl()`), unlike fetch_site — style-sample provenance can cite a URL that no longer serves the text.
- L12. `analyze_site_brand` (MCP) calls `_suggest_voice_rules` **without** `brand_name`, so the brand-name-as-speaker POV detection never fires in the analysis a user reviews first; the saved draft can carry a different POV than the analysis showed.
- L13. Vocabulary `exact` noise ships in the CNM draft: place names and three fused grinder model codes (`W180 W240 W320`) presented as brand/product names with exact casing.

**Tests / docs / hygiene**
- L14. Tests silently depend on the "scratch" `calibration/work/` directory (~5 tests read `writer_trial.txt`/`samples.json`/CNM posts from it) — it is simultaneously raw evidence (M16) and a load-bearing fixture dir; nothing documents which files are which, so an M16 cleanup done naively breaks tests.
- L15. `test_mcp_client.py` doesn't isolate the drafts or facts-profiles dirs (its checks write into the real `drafts/`); its 22 checks never call `get_facts`/`save_facts` end-to-end over stdio — the two newest tools are covered only in-process and by name.
- L16. Coverage gaps: **`writer.get_writer`'s custom-writer branch (`CONTENT_WRITER=module:ClassName`) is truly untested** — the documented plug-in point for a real LLM writer, including its `ValueError` path. Many other public functions have only indirect coverage (via gates/graph/MCP); no coverage tooling is installed to quantify it.
- L17. Evaluation probes (except parity) cannot fail: print-only `main()`, no exit code on regression — the headline SUMMARY numbers (fact recall 96.6%, evasions 0/14, inversions 9) are enforced by no runnable check.
- L18. Flaky-risk: free-port TOCTOU in `test_mcp_http.py:25-29` (bind 0, close, hand the number to a subprocess). Everything else timing-related checked out as bounded and sound.
- L19. `EXPECTED_TOOLS` literal triplicated across three test files (currently agreeing — a drift hazard, cf. M19). One stale test name (`test_mcp_tool_list_now_29` asserts 31).
- L20. One tautological assertion: `tests/test_quality.py:86` asserts `floors == FLOORS` where the code copies that same dict — mitigated by literal pins of `FLOORS`/`PASS_TOTAL` at lines 78-80 of the same file.

### Info (~9)

- **Gate unification holds** (see Verified sound) with two peripheral second copies of the numbers: `runner.py:23-28` re-states hard criteria as golden assertions (harness, not a decision path), and L7 above.
- **Layer separation holds** with two documented-by-design crossings: craft receives brand banned terms only for rhythm-statistic imputation (creates no violations from them); style influences a verdict only via the quality layer's voice axis and only when that layer is explicitly activated. `brand_extract._claim_patterns` scans sites with the shipped brand's claim list as documented "house patterns", review-only output.
- Doc/shape drifts: `fact_check.py:3` docstring still says facts live ONLY in `facts_lorebook.json` (stale since per-brand profiles); `state.py:35` status enum omits `"refused"`; `craft_check_sections` returns a reduced `metrics` dict vs `craft_check`; `lint_draft([])` returns 100.0 — only the gate's vacuity guard makes an empty draft safe, a trap for any future direct consumer.
- SSRF/file posture: fetch CLIs accept any user-supplied URL (`file://`, localhost, metadata IPs, embedded credentials). Acceptable for a local user-invoked CLI with an offline MCP server — noted, not a defect.
- Dead/inconsistent shipped schema: persona `character_book` + `extensions.default_brand_id`, approved-brand `vocabulary`/`brand_kit`, YAML `review_date`/`knowledge_collection` — no code reads them; shipped and converted brands have different shapes (loaders tolerate it). Provenance shape: absent kit scalars return `{value: None, source_url: ""}` — "no logo on page" is indistinguishable from "provenance lost".
- `save_quality_profile(profile_id="../../evil")` is safely contained (hashed stem inside the store) but the returned profile still claims the raw id; reserved stems pass through (Linux-harmless, Windows portability trap).
- README Layout section omits `data/facts_profiles/`, the CNM style profile, five test files, the jsshell fixtures, and the entire `evaluation/` and `calibration/` directories. Demo v1 prints brand/fact/seo check results but asserts only `validate_brief` and the final gate — its middle steps are decorative.

---

## Per-area notes

**Core logic (Reviewer 1).** The gate-unification and layer-separation claims — the two architectural promises the whole design rests on — were independently verified and hold. The serious issues are local: one boundary bug in the money-role window (H1), two validator/checker contract mismatches that crash on *valid* input (M1, M2), one un-normalised blocking layer (M3), and a performance constant factor that will bite at book-length inputs (M4). No drift from any documented non-goal.

**MCP + storage (Reviewer 2).** Traversal containment and atomic-write mechanics are genuinely solid. The lock (H3/M5) needs an ownership token and heartbeat to mean what it claims. The recurring pattern is *partial application of finished fixes*: error dialect (M6), shape validation (M7/M8), and path-free error messages (L6) were each completed for some stores and not others — a checklist problem, not a design problem.

**Fetch + builder + data (Reviewer 3).** The fetch bounds (byte caps, timeouts, sitemap caps, truncation flags) are real. The gaps are the classic real-web trio — charset (M12), redirect base (M13), sitemap parsing (M14) — plus the review's most consequential data finding (H2): the flagship real-brand artefact in the repo predates the fixes it is cited as proving. The conversion guard (M10/M11) works on its happy path but its boundaries don't support the module docstring's "never".

**Tests + docs + hygiene (Reviewer 4).** The test suite itself is unusually disciplined: mutation-style, non-vacuous, hermetic, design values pinned as literals, a tamper test proving the model-golden expectations can fail. The problems are at the repo boundary: real-site scrape tracked in git (M16) next to a README denying real data exists (M17), a stale tool count the project's own summary claims was fixed (M18), and a completeness test that compares two hand-written lists (M19).

---

## Ranked proposed fix backlog (if Smith approves a round)

1. **H1** — Scope money-role markers to the amount's own sentence/clause (the docstring already promises this). Small, surgical, closes a hard-gate bypass.
2. **H2** — Rebuild the shipped CNM brand draft with the current builder (or delete it and regenerate in the calibration flow); add a regression test asserting no shipped draft carries another brand's prohibited claims.
3. **H3 + M5** — Lockfile ownership token (pid+random written into the lock), mtime heartbeat for long holds, unlink only if the token still matches; treat fresh-but-dead locks via pid liveness where available.
4. **M1 + M2** — One facts-schema contract: make `validate_facts` require what consumers index (`price_display` derivable at load; `entries[].keys` defaulted at load), so valid input can never crash a consumer.
5. **M6 + M7 + M8 (+ L6 path leak)** — Finish the Tier-2 hardening uniformly: structured errors, load-shape validation, and section-entry validation for *all* stores, with a cross-store test matrix so the next store can't drift.
6. **M3** — Route craft's weak/filler/sensory matching through the same normalised matching the linter uses.
7. **M12 + M13 + M14** — Fetch real-web trio: honour charset (header → meta → UTF-8 fallback, flag when non-UTF-8), resolve discovery against the final URL, parse sitemaps as XML (entities, CDATA, `.xml.gz`).
8. **M15** — Claim matcher: suffix/distant negation handling; stop storing mangled table-flattened provenance text.
9. **M10 + M11** — Validate brand drafts on save (like facts); convert guardrails' prohibited claims; inspect `anti_examples` too; soften or enforce the docstring's "never".
10. **M16 + M17 + M18 + L14** — Untrack the three raw evidence files (split fixtures from raw evidence first), correct README's data claim and tool count.
11. **M19 + L17** — Derive `TOOL_NAMES` from the FastMCP registry; give the evaluation probes exit codes (or fold their headline cases into pytest).
12. **M4** — Hoist per-sentence normalisation out of the linter's term loop, cache compiled phrase regexes, and let the quality layer reuse the gate's checker results.
13. **M9** — Refuse/warn on non-loopback `CONTENT_CORE_HTTP_HOST` unless an explicit override env is set; make the banner print the actual binding.
14. **L4** — Reject `bool` for int parameters (`target_words` and friends), matching the discipline already used for grades/facts.
15. **L16** — One test for the `CONTENT_WRITER` custom-writer plug-in path (success + bad path).

---

## Verified sound (checked and held up)

- **All suites reproduce green at HEAD:** pytest 339 passed; golden 7/7; model golden 6/6; MCP client 22/22; HTTP PASS (31 tools); 3 demos exit 0. Git tree clean before and after the review.
- **Single gate rule is real:** `gates.evaluate_gate` is the only verdict implementation; graph node, MCP `final_gate`, and the model-golden harness all delegate; craft blocking is on in both production paths; MCP check tools return raw reports with no verdict of their own.
- **Layer separation intact:** no checker reads Persona data (Persona reaches only writer context); facts never influence brand score; style touches a verdict only via the explicitly-activated quality layer's voice axis; unknown-brand fact resolution *raises* rather than falling back to Mộc Brew facts.
- **Vacuity guard works:** empty draft scores 100 in `lint_draft` but `evaluate_gate` returns `passed=False`.
- **Path traversal contained in every store** (drafts, brand drafts, style, quality, facts, lockfiles): `../`, absolute paths, backslashes, 201-char and Unicode ids all hash into in-store stems or raise; the injective stem mapping holds (`a/b` ≠ `ab`).
- **Atomic-write mechanics (process-crash scope):** same-directory temp (same-filesystem `os.replace`), collision-free `mkstemp` names, fsync before replace, temp unlinked on exception.
- **Lock happy paths:** exception inside the critical section releases the lock; timeout surfaces as a structured error in all three locked tools; the shipped 2-process × 15-section concurrency test passes (30/30).
- **Coercion guards that were finished, hold:** `store_excerpts` strict-bool (string/int → structured error, both transports); quality weights/floors and judged grades reject strings/bools; `save_facts` rejects bool/string price and weight and writes nothing.
- **stdio ↔ HTTP parity:** same app both transports; probed calls (structured errors, coercions, unauthenticated reads) behave identically. HTTP defaults bind `127.0.0.1:8765` exactly as README states, and README's no-auth/tunnel warnings are candid.
- **Golden design values verified from source + independent literal pins:** craft 70 (`craft.py:58`), brand 85 (`gates.py:45`), PASS_TOTAL 70, all 5 quality presets sum to exactly 100, floors pinned as literals; model-golden includes a tamper test proving expectations are non-vacuous.
- **Fetch tests are hermetic:** all use `file://` fixtures or loopback servers; real-looking URLs in tests are string inputs to pure parsers — no external fetch anywhere in the suite.
- **No vacuous tests found:** assert-free tests all use `pytest.raises` correctly; monkeypatching is limited to env isolation, crash injection, and one lock-timeout shrink — the real code runs in every test. No contradictory pins across files.
- **Shipped data largely validates against its own rules:** both facts files pass `validate_facts`; the brand YAML satisfies every loader assumption for all 5 formats; all 12 craft cards and the exemplars/glossary are structurally complete; both shipped style profiles load, refresh, and score with `algorithm_version: 2` and no stale warnings.
- **Fetch bounds are real:** byte caps with truncation flags in both fetchers, timeouts threaded through every path, sitemap doc cap (10) with a surfaced truncation flag, robots.txt sitemap channel works with a malformed default sitemap, nested sitemap indexes resolve.
- **The honest Brand Builder path works as designed:** correctly-labelled competitor drafts get `reference_only=True` throughout and conversion refuses them; conversion does sanitise the kit (drops `var(--…)` fonts, nulls thumbnail logos) — the exposures are the guard's boundaries (M10) and the un-sanitised draft-as-brand path (H2), not the happy path.
- **Normalisation alignment discipline in the linter:** both indexed-normalisation call sites verify alignment and fall back conservatively on mismatch; `_protect_numbers` is not pathological (2k–8k dotted groups split in ≤2.9 ms); craft's weak-word masking avoids double-counting and is near-linear on adversarial repeats; money bulk-role arithmetic and span dedup read correctly (H1 is solely the boundary leak).
- **Documented non-goals have not drifted:** D13, spelled-out money, sub-scale word numbers, cross-sentence claims, word-salad, and the 9 residual sweep inversions are all exactly where SUMMARY.md says they are — one is even pinned by an explicitly-labelled boundary test.

---

## Review fix round — addendum (2026-11-22, same day)

Smith approved fixing ALL findings (3 High + 19 Medium + 20 Low; Info
when trivial). Every H/M/L finding is dispositioned below. Method:
fail-first test per finding (new `tests/test_review_fixes.py`, 36
tests, plus rewritten/added tests elsewhere), all suites green at the
end, no Smith-approved design value changed (craft 70, brand 85,
quality presets/floors/PASS_TOTAL untouched).

**Final state:** pytest **377 passed** (was 339); golden 7/7; model
golden 6/6; MCP client **24/24** (was 22 — facts tools now covered
end-to-end); HTTP smoke PASS; 3 demos OK. Evaluation probes are
**unchanged from the review's baselines** — fact recall 96.6%,
false-flag 2.4%, marked 4/5; linter evasions 0/14; sweep inversions 9
(documented residual); CNM posts craft 90×5, brand violations 10,
writer-trial style 85; parity 16/16 — so no evaluation/SUMMARY.md
note was needed. M4 timing on the review's synthetic 41,580-word
draft: `lint_draft` 3.19 s → ~1.0 s; quality-enabled gate 15.38 s →
5.4–8.1 s (shared-CPU variance), with a byte-identical verdict digest
(brand 100, craft 75, quality total 77.6, same axes).

### High — all fixed
- **H1 fixed.** `_money_role` is scoped to the amount's own sentence
  (new `_sentence_span`/`_is_sentence_boundary`); the cross-sentence
  ship-fee repro raises `wrong_price` again, same-sentence roles
  still excuse. Pinned in test_review_fixes.
- **H2 fixed.** `data/brand_drafts/cao-nguyen-moc.json` regenerated
  with the fixed builder from `calibration/work/pages_setB.json`
  (existing qualitative kept): prohibited_claims `[]` in both places,
  max_sentence_words 39 (derived), fonts Lora/Be Vietnam
  Pro/Fraunces (no `var(--…)`), logo null (the corpus meta's only
  candidate was the blog thumbnail — hand-corrected and documented
  in the draft's review_notes), model codes gone from vocabulary,
  claims re-extracted 5 → 1 (the genuine freshness-table entry).
  Regression test pins the shipped file's invariants + the builder's
  guarantees. Note: the file is gitignored (live server data), so it
  is not part of the commit — the invariants travel with the test.
- **H3 fixed (redesign).** Primary: `fcntl.flock` on a persistent
  per-draft lock file (never unlinked; kernel releases on holder
  death — no staleness judgement at all). Fallback (no fcntl): token
  lockfile (pid+uuid) + 5 s heartbeat; steal only after a grace
  re-read proves token+mtime unchanged past 30 s; release unlinks
  only its own token. A >30 s critical section can no longer be
  stolen from, and no holder can unlink another's lock. The R7
  concurrency repro stays lossless.

### Medium — all fixed
- **M1/M2 fixed (contract at load).** `loaders.load_facts` now
  normalises every accepted doc (`price_display` derived from the
  int price via `format_vnd`, `entries[].keys` defaulted), so any
  doc `validate_facts` accepts is consumable by `check_section`;
  the graph research node also uses `.get` throughout (defence in
  depth). Pinned both ways in tests.
- **M3 fixed.** Craft weak/filler/sensory matching runs on
  `normalize_for_match` text with token boundaries; hits report the
  original accented forms. The review's pair now scores identically
  (accented == unaccented, same weak hits); CNM probe unchanged.
- **M4 fixed.** Linter precomputes per-sentence normalised forms
  once (phrase regexes cached); `evaluate_gate` passes its own
  checker results into `evaluate_quality` (`_precomputed`) instead
  of re-running them — clarity still computed separately (different
  semantics). Timing above; verdict-identical proof in tests.
- **M5 fixed** with H3: SIGKILLed holders free the lock immediately
  (kernel), and a per-process registry makes same-thread nested
  acquisition reentrant instead of a latent deadlock.
- **M6 fixed.** All six named tools (plus `save_style_profile`'s and
  `save_brand_draft`'s save/build calls) return structured
  `{"error"}` on ValueError; corrupt stored profiles/drafts verified
  over the in-process path.
- **M7 fixed.** All loaders validate stored shape on load
  (`_require_stored_object`); `_load_draft` corrupt messages name
  the file only, not the absolute path; every `list_*` surfaces
  corrupt files as `{"corrupt": True}` entries (healthy entries now
  carry `"corrupt": False` for a uniform shape).
- **M8 fixed.** `_load_draft` validates section/outline entries are
  objects and fills consumer defaults; a string entry is the named
  corrupt error.
- **M9 fixed.** Non-loopback `CONTENT_CORE_HTTP_HOST` refuses to
  start (exit 2) unless `CONTENT_CORE_ALLOW_NONLOCALHOST=1`; the
  banner prints the actual binding + "NO AUTHENTICATION"; README
  updated to match.
- **M10 fixed (mechanism) + rescoped (promise).** `save_brand_draft`
  validates draft shape and re-stamps `reference_only=True` on
  competitor examples at save (cannot be stripped on the way in);
  the conversion guard also inspects anti-examples (incl. dicts
  nested in `bad` lists) and treats present-but-falsy flags
  (0/""/None) as still-marked for competitor drafts. The docstring's
  "never" is corrected: `source_type` is the user's declaration —
  the guard makes accidental conversion impossible for a draft
  declared as a competitor's; it cannot detect a mislabelled
  "own_site" offline, and the addendum records that honestly rather
  than claiming detection.
- **M11 fixed.** Conversion unions `voice.prohibited_claims` with
  `claims_and_guardrails.prohibited_claims` (order-preserving).
- **M12 fixed.** Shared `fetch_sample.decode_body`: Content-Type
  charset → `<meta charset>` sniff → UTF-8; the encoding used is
  returned and surfaced on pages/results (windows-1258 verified).
- **M13 fixed.** Discovery + sitemap resolve against the FINAL
  (post-redirect) root URL; cross-host redirect test added.
- **M14 fixed.** Sitemaps parse via ElementTree (namespace-stripped
  localnames; entities/CDATA decoded), `.xml.gz` children
  decompressed, regex kept as malformed-XML fallback; doc cap and
  truncated flag unchanged.
- **M15 fixed.** Distant-denial window widened (24 → 40 chars, more
  verbs, `không phải` included — object nouns between denial and
  claim); suffix denial for "…đâu" shapes with the `không chỉ`
  affirmation guard preserved; "Tốt nhất là…" idiom and
  freshness-window ("ngon nhất trong vài tuần") rules added;
  question-framed claims are flagged, not blocking. Site-claim
  extraction skips interrogatives/idioms/freshness windows and
  stores readable provenance (table cell, or a phrase-centred
  window). CNM draft claims 5 → 1 as a result (see H2). Accepted
  bound: "Tốt nhất dùng…" without "là" still flags — conservative
  direction.
- **M16 fixed.** The three raw evidence files are `git rm --cached`
  + gitignored; they remain local working-tree evidence (never on
  GitHub). The other tracked calibration files are curated
  artefacts/fixtures and stay (see L14 note file).
- **M17 fixed.** README's opening now states the testbed is
  simulated AND the repo contains the documented real-site
  calibration with its verified public prices.
- **M18 fixed.** "29 tools" → 31; layout section completed (see
  Info below); the second stale count (`test_mcp_client.py`, 22
  checks) → 24 in both places it appears.
- **M19 fixed.** `TOOL_NAMES` is derived from the FastMCP registry;
  the one canonical `EXPECTED_TOOLS` literal lives in
  tests/test_mcp_server.py and its stdio assertion is now exact
  equality; the wire scripts compare against `srv.TOOL_NAMES`.

### Low — all fixed except the noted partial
- **L1 fixed** — every percent in a sentence is examined; the first
  non-composition one decides (pinned).
- **L2 fixed** — `loaders.slugify`, `linter.WORD_RE`/`word_tokens`,
  and `loaders.brand_check_context` are the single implementations;
  style.py/brand_extract.py/gates.py/quality.py delegate.
- **L3 fixed** — `count_phrase`/`phrase_in` and `_ORIGIN_PAIRS_NORM`
  removed (no callers).
- **L4 fixed** — bool `target_words` rejected in `save_section` and
  `make_outline`; judged grades must be real ints (2.0 rejected).
- **L5 fixed** — atomic write fsyncs the directory, preserves an
  existing file's mode (new files 0644), and sweeps stale `*.tmp`
  orphans.
- **L6 fixed, one part left.** `gate_history` capped at 50 with
  monotonic pass numbers; corrupt files surface in listings (M7);
  error messages drop absolute paths. Left: a draft delete/list
  *tool* — a feature addition, not a fix; the tool surface stays at
  Smith's 31. Note the per-draft `.lock` file now persists BY DESIGN
  (flock requires it; unlinking would split the lock).
- **L7 fixed** — free-text `brand_check` applies `lint_draft`'s
  failing rule (non-warning violation or score < 85); warning-only
  text no longer "fails".
- **L8 fixed** — `_canonical_url`: IPv6 brackets, userinfo kept,
  `;params` preserved, query normalised (sorted, consistently
  re-encoded), path percent-escapes normalised (unreserved decoded,
  the rest uppercased).
- **L9 fixed** — page fetches reject a declared non-HTML
  Content-Type (root → errors result; link → error entry + skip);
  `fetch_sample` flags `binary_suspected` instead of stripping
  binaries silently.
- **L10 fixed** — `_sanitize_brand_kit` mirrors the extraction rules:
  generic font families dropped; the logo rule is filename-token
  based ("logo-ish/banner.png" no longer kept).
- **L11 fixed** — `fetch_sample.fetch_url` records `final_url`.
- **L12 fixed** — `analyze_site_brand` computes the kit once and
  passes its brand name into `_suggest_voice_rules`, so the
  brand-as-speaker POV detection fires in the reviewed analysis too.
- **L13 fixed** — `_capitalized_names` drops all-model-code
  candidates ("W180 W240 W320"); place names stay (they are
  legitimate exact-casing vocabulary).
- **L14 fixed** — `calibration/work/README.md` documents which files
  are fixtures (read by tests/probes) and which are local-only raw
  evidence.
- **L15 fixed** — `test_mcp_client.py` isolates ALL writable stores
  (drafts + facts-profiles included; the facts env var is
  `CONTENT_CORE_FACTS_PROFILES_DIR`) and covers get_facts/save_facts
  end-to-end (22 → 24 checks).
- **L16 fixed** — `writer.get_writer` plugin test: temp
  `module:Class` loads; a colon-less path raises ValueError.
- **L17 fixed** — all five probes exit non-zero past regression
  thresholds set at current values (fact recall ≥ 90%, false-flag
  ≤ 10%, evasions missed = 0, inversions ≤ 12, CNM craft ≥ 90 &
  violations ≤ 10, parity exact). All five exit 0 after this round.
- **L18 fixed** — the HTTP smoke test retries over candidate ports
  (5 attempts) instead of losing the free-port race.
- **L19 fixed** — one canonical tool literal (see M19); the stale
  `test_mcp_tool_list_now_29` is renamed and imports the canonical
  list.
- **L20 fixed** — the floors assertion compares against the literal
  Smith-approved dict, not the module constant it copies.

### Info — fixed where trivial, else left with reason
Fixed: stale `fact_check` docstring (facts live per-brand now);
`state.py` status comment includes "refused"; `craft_check_sections`
returns the full metrics shape (word_count/filler_found/
rhythm_stdev); `lint_draft` docstring warns the empty-draft 100.0
is only safe behind the gate's vacuity guard; demo_mcp_e2e asserts
its middle steps (research/assemble/brand/fact/seo); README layout
completed (facts_profiles/, CNM style profile, evaluation/,
calibration/, all test files, jsshell fixtures).
Left, deliberately: persona `character_book`/`extensions` and
approved-brand `vocabulary`/`brand_kit` dead schema fields (loaders
tolerate both shapes by design; removing fields is a data migration,
not a fix); the absent-kit provenance shape `{value: None,
source_url: ""}` (changing it ripples through every kit consumer);
`save_quality_profile` returning the raw requested id (the same stem
function resolves it on read — consistent, if inelegant); the fetch
CLIs' SSRF/file posture (local user-invoked tools, documented in the
review itself as acceptable).

### Pin updates (with justification)
- `tests/test_core_closing.py::test_stale_lock_is_stolen_and_fresh_lock_errors`
  rewritten: it pinned the OLD lock semantics (bare-file presence =
  held, mtime staleness = stealable, unlink on release) — those
  semantics ARE H3. The replacement pins the new contract: stale
  unheld file never wedges; a genuinely held lock is never stolen
  (mtime backdated past the old bound); a SIGKILLed holder frees
  immediately; the lock file persists. Two tests added: token
  fallback steal discipline + same-thread reentrancy (M5).
- `tests/test_mcp_server.py` stdio assertion: subset → exact
  equality (M19 — the subset form was the drift hole).
- `tests/test_craft.py`: duplicate tool literal removed; test
  renamed (L19).
- `tests/test_quality.py`: floors tautology → literal dict (L20).
