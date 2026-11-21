# Evaluation Round 4 — Style Extraction & Matching (`content_core/style.py`)

- **Scope:** `fingerprint`, `analyze_samples`, `build_profile`, `style_match` — read-only evaluation, no code/data changed.
- **Baseline:** project at commit `8215b8c`, 213 tests green.
- **Corpora used:**
  - **CNM** — 5 real Cao Nguyên Mộc blog posts (`calibration/work/samples.json`): 629 / 889 / 616 / 968 / 979 words (4,081 total).
  - **MB** — 3 Mộc Brew samples behind the shipped profile (`data/style_samples_mocbrew.json`, 829 words total).
  - **SOM / DIN** — Sớm Coffee and Đỉnh Cao fixture sites (`tests/fixtures/`, via the project's own `fetch_site`): SOM pages 160+114 words, DIN pages 188+76 words. ⚠️ These are short *web-page* texts (nav, product blurbs), not prose articles — this matters for the discrimination findings.
  - **Synthetic voices written for this round:** HYPE (72-word VN sales hype, exclamation_rate 100), LEGAL (formal legalese), TERSE (32-word telegram style), ENMIX (65-word VN/EN code-switched review), ACADEMIC (formal research abstract).
- **Profiles:** shipped `cao-nguyen-moc-blog-voice-dd5678`, shipped `mocbrew-blog-voice`, plus profiles built in-session from the DIN and SOM fixture texts.
- **Method:** scratch script in `/tmp` driving the real module functions; manual verification with an independent tokenizer/sentence splitter. Raw results: `/tmp/r4_results.json`.

---

## 1. Split-half stability (CNM) — ✅ PASS, strong

Profile built from one half of the 5 posts, the other half matched against it:

| Split | Profile from | Matched posts | Individual scores | Concatenated score |
|---|---|---|---|---|
| A | posts 1–2 (1,518 w) | posts 3, 4, 5 | 92, 100, 92 | 100 |
| B (swapped) | posts 3–5 (2,563 w) | posts 1, 2 | 92, 100 | 100 |
| C (alternating) | posts 1, 3, 5 | posts 2, 4 | 92, 100 | 100 |
| D (swapped) | posts 2, 4 | posts 1, 3, 5 | 92, 92, 92 | 100 |

No held-out post from the same author ever drops below 92, in either direction, under two different splits. Within-author stability of the matcher is genuinely good. (The 2-sample profiles correctly carry the "only 2 sample(s)" warning while still matching well.)

## 2. Discrimination — cross-match matrix — ⚠️ diagonal wins on aggregates, but margins are thin and short texts confuse badly

Scores (rows = text, columns = profile; **bold** = own voice):

| Text | CNM prof | MB prof | DIN prof | SOM prof |
|---|---|---|---|---|
| CNM1 | **92** | 76 | 92 | 84 |
| CNM2 | **100** | 68 | 92 | 76 |
| CNM3 | **92** | 68 | 84 | 76 |
| CNM4 | **100** | 68 | 92 | 76 |
| CNM5 | **92** | 76 | 84 | 69 |
| CNM_all | **100** | 68 | 92 | 76 |
| MB1 | 84 | **100** | 92 | 92 |
| MB2 | 84 | **100** | 92 | 92 |
| MB3 | 76 | **100** | 84 | 92 |
| MB_all | 68 | **100** | 76 | 84 |
| SOM1 | 69 | 69 | 76 | **92** |
| SOM2 | 61 | 69 | 69 | **92** |
| SOM_all | 84 | 69 | 76 | **100** |
| DIN1 | 92 | 100 | **100** | 92 |
| DIN2 | 92 | 92 | **100** | 84 |
| DIN_all | 92 | **100** | **100** | 92 |
| HYPE (mine) | 54 | 54 | 54 | **69** ← wrong winner |
| LEGAL | **70** | 62 | 62 | 62 |
| TERSE | 62 | 62 | 62 | 62 (4-way tie) |
| ENMIX | 69 | 69 | 53 | 69 (3-way tie) |
| ACADEMIC | 70 | 62 | 62 | **77** ← wrong winner |

**What works:** every *aggregate* corpus (≥ ~800 words) ranks its own profile first, and the two real blog voices (CNM vs MB) separate cleanly (100 vs 68 both ways). The metric-level detail shows the separation is carried by `short_sentence_share`, `first_person_rate` and `reader_rate` — real voice signals.

**Confusions, with causes:**

1. **DIN_all scores 100 against the Mộc Brew profile** — a hype competitor's page text is a *perfect* match for the calm first-person brand voice. DIN_all is only 264 words, so all three density metrics are judged with the relaxed short-text bands; the decisive one is first-person: DIN 0.0 vs MB profile 3.5 passes because |0 − 3.5| = 3.5 ≤ max(1.0 × 3.5, 1.5). Sentence shape also passes legitimately (median 16 vs 13 within ±35 %/±3; stdev ratio 0.82; short-share 0.26 vs 0.28). Stacked tolerances, each defensible alone, sum to zero discrimination at this length.
2. **CNM posts score 84–92 against the DIN profile** — nearly own-voice level — because the DIN "profile" itself (264 words of web copy, median 16, stdev 7.14) sits close to ordinary VN prose shape. Garbage-in profile, plausible-looking scores out; nothing in `style_match` surfaces that the *profile* was built from 264 words with reliability warnings.
3. **HYPE's best match is SOM (69), not DIN (54)** — ironic, but partly a corpus artefact: the DIN fixture pages are not actually punchy at sentence level (median 16 words). Partly real: the matcher never looks at exclamation rate (see §5), HYPE's loudest signal.
4. **Short alien voices tie everywhere** (TERSE 62 ×4, ENMIX 69 ×3): below ~300 words the score mostly reflects the two key sentence metrics failing identically against every profile — the number carries little information, yet is presented as a plain 0–100 score.

## 3. Sample-size sensitivity — core shape stabilises fast; densities and TTR do not

CNM, cumulative profiles (order = blog order):

| n samples | words | median | stdev | short-share | 1st-person | reader | num-density | TTR |
|---|---|---|---|---|---|---|---|---|
| 1 | 629 | 14.0 | 10.44 | 0.05 | 0.00 | 0.00 | 0.79 | 0.41 |
| 2 | 1,518 | 14.0 | 10.52 | 0.05 | 0.00 | 0.07 | 1.65 | 0.29 |
| 3 | 2,134 | 13.5 | 10.04 | 0.05 | 0.00 | 0.14 | 2.25 | 0.23 |
| 4 | 3,102 | 13.0 | 9.83 | 0.06 | 0.00 | 0.16 | 2.03 | 0.18 |
| 5 | 4,081 | 14.0 | 10.44 | 0.05 | 0.02 | 0.20 | 1.67 | 0.16 |

Reverse order gives the same endpoint but a different path (n=3: num-density 1.68 vs 2.25; reader 0.27 vs 0.14) — density metrics are **order-dependent at n ≤ 3** and still swing ±20–35 % between n=3 and n=5. MB shows the same pattern (fp-rate 4.58 → 3.94 → 3.50; num-density 0.0 → 0.0 → 0.36).

**Verdict:** sentence median/stdev/short-share are effectively stable from **2 samples / ~1,500 words**; the full fingerprint including densities needs the recommended **3–5 samples**. TTR *never* stabilises — it falls monotonically with corpus size (0.41 → 0.16 CNM; 0.59 → 0.37 MB), a well-known length artefact, so TTR values are not comparable between profiles built from different amounts of text. (TTR is not used by `style_match` — good — but the fingerprint presents it as a voice trait with no caveat.)

**Warnings fire at the right places** (verified by probes): 1–2 samples always warn on count; <800 total words warns (699-word probe warns, 900-word probe doesn't); a single 979-word sample warns only on count, correctly; 3 truncated samples totalling 699 words warn on words only. One gap: warnings measure *quantity*, not *independence* — see duplicate-sample edge case in §5.

## 4. Fingerprint sanity — hand verification on CNM post 1 ("Cà phê rang mộc là gì?…", 629 words)

| Metric | `fingerprint()` | Independent manual count | Verdict |
|---|---|---|---|
| Sentence count | 37 | 37 (naive split on terminal punctuation) | ✅ exact |
| Sentence median | 14.0 | 14 | ✅ exact |
| Sentence mean / stdev | 17.0 / 10.44 | 17.0 / (lengths identical sentence-by-sentence) | ✅ exact |
| First-person rate | 0.0 /100 w | 0 markers in 629 words | ✅ exact |
| TTR | 0.41 | 256 unique / 627 tokens = 0.4083 | ✅ (rounding) |

Second check on Mộc Brew sample 1: manual count of `mình`/`tôi` tokens = 13, 13/284 × 100 = 4.58 — matches `first_person_rate` 4.58 exactly, and the "người ta" exclusion works (MONOTONE-type text scores 0.0).

**Minor discrepancies found:**
- **Two denominators in one fingerprint:** `word_count` = 629 (whitespace split) but TTR/token stats use 627 regex tokens. Cause: the date `29/10/2026` splits into 3 regex tokens (+2) while breadcrumb symbols `›`, `·` count as whitespace "words" but not regex tokens (−2, plus punctuation-attached tokens balancing). Effect ≈ 0.3 % — negligible numerically, but rates and TTR in the same report are computed over different tokenisations without saying so.
- **Micro-text tokenisation:** in "Giá 185.000đ cho gói 250g… giao 2-3 ngày", `185.000đ` becomes tokens `185` + `000đ`, and `2-3` becomes `2`, `3` — number *density* (whitespace-based) says 3 numeric tokens, token-based stats see 5. Again small, but it is why `top_words` for the CNM table test contains the junk entry `000đ` (×3).
- **`top_words` splits Vietnamese compounds:** CNM post 1's top words are `cà` (30) and `phê` (30) separately, `sản`/`phẩm` separately in the table test. As a "voice vocabulary" display this is misleading; the underlying counts are consistent.

## 5. Edge cases

| Case | Result | Assessment |
|---|---|---|
| **Punctuation swap attack** | Mộc Brew's own text with every `.`→`!` (exclamation_rate 0 → 75.41) still scores **100** vs its own profile; same for `?` (question_rate 75.41) | 🔴 `style_match` ignores `question_rate` and `exclamation_rate` entirely — they are measured in the fingerprint but absent from `_MATCH_METRICS`. CNM's signature question rate (22.9/100 sentences) and hype's exclamation rate (100) are invisible to the matcher. |
| **Duplicate samples** | `analyze_samples([post, post, post])` → **zero warnings**, sample_count 3, 1,887 words; profile self-match 100, other-post match 92 | 🟠 Duplicates silently satisfy the "3–5 samples" and "≥800 words" safeguards and the divergence check (identical samples have CV 0). No dedup or near-duplicate detection. |
| **Heavy table + list** | Markdown price table + bullet list parsed as **2 sentences, median 40 words, stdev 28**; matched vs CNM at 62 with advice "split and shorten sentences… drop some figures" | 🟠 Non-prose structure destroys sentence statistics and produces nonsense advice for a product table. (Linter has table-segment exclusion since the calibration round; `style.fingerprint` does not.) |
| **Numbered list only** | "1. Rang / 2. Xay / 3. Pha / 4. Uống" → 5 "sentences", median 2 words, number_density 50 | 🟡 List numbering is treated as sentence boundaries/content. |
| **English-mixed text** | ENMIX fingerprint is sane (fp 6.15, median 16.5); matches 69/69/53/69 | 🟡 Works mechanically (Latin tokens counted), but no discrimination; VN-only transition list still fires on "nhưng" inside an English sentence. |
| **Very short text** | "Mình thích cà phê." (4 words) → fp_rate 25.0, TTR 1.0, match score 54 vs MB with reliability note present | 🟡 The reliability note + widened bands work as designed; the raw fingerprint numbers (25/100 w) are still arithmetically absurd if read at face value. |
| **Punctuation-only text** | `"... ??? !!!"` → sentence_count 0 but question_rate **300**, exclamation_rate **300** | 🟡 Rates divide by `len(sentences) or 1` while counting raw punctuation — values >100 with zero sentences are nonsense output. |
| **Empty text** | `fingerprint("")` all zeros; `build_profile([])` raises ValueError; empties filtered in `analyze_samples` | ✅ handled. |
| **Mixed authors (control)** | CNM post + LEGAL + HYPE as samples → divergence warnings fire correctly (CV 0.79 on median and stdev) | ✅ the divergence detector works when authors genuinely differ. |
| **Stale stored warnings** | Shipped CNM profile still carries *"samples diverge on first_person_rate (CV 2.0 > 0.35) — possibly mixed authors"*; current code on the *identical* samples (same content hash `dd5678`, identical fingerprint) produces **no warning** (near-zero-mean guard added in the calibration fix round) | 🟠 Profiles freeze `consistency_warnings` at build time with no code/schema version stamp — a fixed false alarm is served forever, and conversely a future stricter check would never reach old profiles. |

## 6. Severity ranking

| # | Severity | Finding |
|---|---|---|
| F1 | 🔴 High | Short-text discrimination collapse: stacked wide tolerances + relaxed density bands let a 264-word hype page score 100 against the Mộc Brew profile (§2.1). Scores under ~300 words are close to uninformative but look authoritative. |
| F2 | 🔴 High | `style_match` ignores question/exclamation rates (and long-share, paragraph rhythm, transitions): punctuation-swap proof — excl 75 vs 0 still scores 100 (§5). The two most visible voice markers in VN marketing copy are unmeasured by the matcher. |
| F3 | 🟠 Medium | Stored profiles are frozen snapshots: stale pre-fix warnings persist, no version stamp, `style_match` doesn't surface a profile's own reliability (a 264-word, warning-laden profile scores others as confidently as a 4,081-word one) (§2.2, §5). |
| F4 | 🟠 Medium | Duplicate/near-duplicate samples defeat the sample-count, word-count and divergence safeguards (§5). |
| F5 | 🟠 Medium | Tables/lists corrupt sentence statistics and generate bad advice; style fingerprint lacks the linter's non-prose exclusion (§5). |
| F6 | 🟡 Low–Med | TTR is length-confounded (0.41→0.16 purely from corpus growth) yet presented as a comparable voice trait (§3). |
| F7 | 🟡 Low | Density metrics (number, reader) remain order-sensitive at n ≤ 3 samples; stabilisation claim should be scoped to sentence-shape metrics (§3). |
| F8 | 🟡 Low | Degenerate inputs yield nonsense rates (300/100 sentences at 0 sentences; fp_rate 25 on a 4-word text) (§5). |
| F9 | 🟡 Low | Dual tokenisation (whitespace vs regex) inside one fingerprint; junk tokens (`000đ`) in `top_words`; compound words split in `top_words` (§4). |

**Overall:** the *measurement* core is accurate — hand-verified metrics are exact, split-half stability is excellent, warnings fire at the documented boundaries, and genuinely mixed authors are caught. The weaknesses are all in the *comparison* layer: too few of the measured signals are used, tolerances stack too generously on short texts, and profiles carry no freshness/reliability metadata.

## 7. Improvement suggestions (not applied)

1. **Use the punctuation voice:** add `exclamation_rate` and `question_rate` to `_MATCH_METRICS` (per-100-sentences bands), at least as a capped penalty — a draft with excl 75 vs profile 0 should not score 100.
2. **Score honesty at short lengths:** when a text is <300 words, either cap the achievable score (e.g. ≤85) while >half the metrics are relaxed, or return the score with an explicit `confidence: low` field consumed by the gate layer; similarly expose the *profile's* word count/warnings in the match result.
3. **Version-stamp profiles:** store `style_code_version` (and the constants used) in `build_profile`; recompute or flag `consistency_warnings` on load when the version differs.
4. **Dedup samples:** hash exact duplicates in `analyze_samples` (and a cheap near-duplicate check, e.g. token-set Jaccard) and warn "samples are duplicates — effective sample count is N".
5. **Non-prose handling in `fingerprint`:** reuse the linter's table-segment exclusion and strip list markers/numbering before sentence statistics; report a `non_prose_share` so callers know the text wasn't prose.
6. **Length-fair vocabulary richness:** replace or supplement raw TTR with a fixed-window measure (e.g. moving-average TTR over 200-token windows) and always print TTR next to its word count.
7. **Guard degenerate rates:** when `sentence_count == 0`, return rates as 0/None rather than counts × 100; clamp per-100-sentence rates or document they can exceed 100.
8. **Unify tokenisation:** derive `word_count` from the same token stream used for TTR/top-words, or document the two denominators explicitly in the fingerprint docstring.

*End of round 4 report.*
