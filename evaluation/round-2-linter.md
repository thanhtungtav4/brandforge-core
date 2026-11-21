# Evaluation Round 2 — Brand Linter Adversarial Evaluation

**Target:** `content_core/linter.py` (`lint_section` / `lint_draft`), read-only evaluation, no code changed.
**Brands:** Mộc Brew (`data/brand_mocbrew.yaml` — median 11–14 blog/product/comparison, 5–14 social, 8–14 email; max sentence 25w; 9 banned terms) and Cao Nguyên Mộc (`calibration/caonguyenmoc-brand.yaml` — median 11–17 blog, max 25w; banned: `ngon nhất việt nam`, `tốt nhất việt nam`, `số 1`, `đẳng cấp thế giới`, `thần dược`).
**Reference gate context:** `gates.py` hard-passes a draft only at brand score ≥ 85 **and 0 banned terms** — so any banned-term hit is a hard fail regardless of score, and any non-banned violation that leaves score ≥ 85 does not block the hard gate.
**Harness:** 98 scripted cases + 6 extra probes (scratch `/tmp/eval_linter.py`, results `/tmp/eval_results.json`). Detection criterion per rule = the corresponding violation type present in `lint_section` output.

## Per-rule results

| Rule | Attempts | Caught | Evasion rate | Notes |
|---|---|---|---|---|
| Banned terms — Mộc Brew | 30 engineered | 20 | **33%** (10/30) | +4 more evasions in extra probes (ZWJ, Cyrillic in `game-changing`, HTML-tag split, HTML entity) → 14 successful evasions overall |
| Banned terms — Cao Nguyên Mộc | 4 engineered | 4 | 0% | incl. diacritics-free `so 1` and `số 1 Việt Nam` (substring) |
| Exclamation in blog | 8 real exclamation cases | 5 | **37.5%** (3/8) | only `!` (U+0021) and `！` (U+FF01) are counted |
| Max sentence (25w) | synthetic + disguised | fires on plain long sentences | **100% evasion via pipe disguise** | a 60-word "sentence" containing 2 `\|` chars is excluded from all length statistics |
| Median sentence length | 8 synthetic | fires whenever out of range | gameable | median is outlier-robust by design; padding compliant sentences neutralises any number of long ones (each costs only −2) |

## Successful evasions (quoted exactly)

### Banned terms — all scored 100.0, zero violations (Mộc Brew, blog)

1. Zero-width space U+200B inside term: `ele\u200bvate` — in context: "Bài này sẽ ele\u200bvate trải nghiệm uống cà phê mỗi sáng của bạn."
2. Soft hyphen U+00AD: `ele\u00advate`
3. U+FEFF (BOM / zero-width no-break space): `ele\ufeffvate`
4. Zero-width joiner U+200D: `ele\u200dvate`
5. Cyrillic homoglyph а (U+0430): `elev\u0430te`; also `g\u0430me-changing`
6. Fullwidth Latin: `ｅｌｅｖａｔｅ` (NFD normalisation does not fold fullwidth or Cyrillic; the regex `[^a-z0-9]+` turns them into spaces, splitting the term)
7. Digit-guard abuse: `elevate7` and `7elevate` — the `(?<!\d)…(?!\d)` boundary guards (built to stop `số 1` matching inside `số 12`) also block alphabetic terms glued to a digit
8. Letter-spacing: `s e a m l e s s`; letter-dotting: `e.l.e.v.a.t.e` (the latter was caught only by an accidental median violation from the fragments, score 90, no `banned_term`)
9. HTML markup split: `game-<b>changing</b>` (tag name becomes a token: normalised `game b changing`)
10. HTML entity: `game&#45;changing` (normalised `game 45 changing`)
11. Inserted word: "cà phê **tốt nhất ở Việt Nam**" — phrase `tốt nhất Việt Nam` no longer contiguous

Caught correctly (credit): case changes, diacritics dropped (`dang cap the gioi`), decomposed (NFD) Vietnamese input, punctuation/hyphen/underscore/markdown variants, line breaks inside terms, split across a sentence boundary ("đẳng cấp. Thế giới"), spaced percent (`100 % chữa mất ngủ`), `100%chữa` attached, non-breaking hyphen U+2011, combining-mark insertion inside a Latin word (`ele\u0301vate` — combining marks are stripped). Substring over-matching also catches derivations: `seamlessly`, `leveraged` both flagged (no word boundaries for Latin terms).

### Exclamation rule — evaded (blog, score 100.0, zero violations)

- "Mẻ rang hôm nay thơm quá**‼**" — U+203C DOUBLE EXCLAMATION MARK
- "Mẻ rang hôm nay thơm quá**❗**" — U+2757 HEAVY EXCLAMATION MARK SYMBOL
- "Mẻ rang hôm nay thơm quá**¡**" — U+00A1 INVERTED EXCLAMATION MARK

All three render as exclamation marks in feeds/readers. Caught correctly: ASCII `!`, fullwidth `！`, French-style spaced ` !`. By design (brand YAML declares the rule for blog only) `!` is **not** flagged in social/email/product/comparison — a spec-coverage gap for the long-form product/comparison formats rather than a code bug.

### Sentence-length rules — evaded / neutralised

- **Pipe disguise (complete evasion):** a 60-word sentence written as `…28 words… | …16 words… | …16 words…` produced **score 100.0, zero violations** — `is_table_like` (≥2 `|`) excludes the segment from median *and* max statistics. A section consisting only of table-like rows also scores 100.
- **Cost-only evasion:** one 60-word sentence among eight 12-word sentences → score **98.0** (flat −2). One 200-word run-on sentence → −2 for `sentence_too_long` (plus median effects). A 26-word sentence and a 200-word sentence cost exactly the same −2, and the violation does not block the hard gate (score stays ≥ 85, no banned term).
- **Median gaming:** median is robust to outliers, so padding with compliant sentences keeps the median in range while long sentences accumulate at −2 each.
- **Splitter surface:** semicolon-joined clauses count as one sentence (splitter only breaks on `.!?…！？`); text with no terminal punctuation is one sentence. Both are detected as long, but again at flat −2.
- **Empty input:** `lint_section("")` and whitespace-only text return **score 100.0, no violations** (no lengths → no checks). The gate has a separate vacuity guard, but `lint_section` alone is unsafe as a standalone quality signal; table-only text has the same property.

## False flags (benign set: 16 cases, 12 flagged, only 4 scored a clean 100)

### Semantic false flags — banned/exclamation hits on non-assertive usage (6)

The linter has no negation or quotation awareness (unlike `fact_check`, which is negation-aware), and banned terms hard-fail the gate:

1. Negation (Mộc Brew): "Mộc Brew **không nhận là tốt nhất Việt Nam** như nhiều nơi quảng cáo." → `banned_term: tốt nhất việt nam`, score 75
2. Quotation/criticism (Mộc Brew): "Nhiều quảng cáo ghi **tốt nhất Việt Nam** nhưng không đưa ra căn cứ nào." → `banned_term`, score 85 (still a hard gate fail)
3. Step enumeration (CNM): "**Bước số 1:** bạn chọn hạt chín đỏ đều màu." → `banned_term: số 1`
4. Priority statement (CNM): "**Ưu tiên số 1:** hạt phải chín đỏ mới hái xuống." → `banned_term: số 1`
5. Negation (CNM): "Hạt điều **không phải thần dược** như lời đồn trên mạng xã hội." → `banned_term: thần dược`
6. Quoted customer speech (CNM blog): "Khách nhắn lại rằng **pha ngon quá!** Rồi khách đặt thêm…" → `exclamation_in_blog` (headings are likewise counted — a `##` heading containing `!` is flagged)

### Self-inconsistency — the brands' own approved texts fail their own rules (6)

- Mộc Brew's own anti-example **"good"** text ("Hạt rang thứ Ba hàng tuần. Bạn pha phin hoặc V60 đều rõ vị.") → `median 7.0 words, target 11-14`, score 90. Two-sentence sections make the median extremely jumpy.
- All **3 approved examples in the CNM brand YAML** fail the CNM brand: medians **19 / 19 / 25** vs target 11–17, plus sentences of **32 words > 25** and **37 words > 25**. The machine-derived median band [11,17] does not fit the brand's own approved copy.
- Carefully hand-written clean product and comparison drafts (Mộc Brew) scored 90 on median alone (medians 9 and 10 vs 11–14) — with 5–6 sentence sections, a single short sentence swings the median out of range. Clean blog/social/email drafts and the CNM hand-written blog passed at 100.

### Real Cao Nguyên Mộc posts (5 posts from `calibration/work/samples.json`, fmt=blog)

- vs their own CNM brand: **28 violations, 100% `sentence_too_long`**, scores 86–92 (all ≥ 85, so all pass the score bar; zero banned/exclamation/median hits).
- vs Mộc Brew brand: identical, plus 1 extra `median_sentence_length` on the Robusta-vs-Arabica post (score 76).
- Because `lint_draft` lists every section with any violation as failing, **5/5 real published posts would enter the rewrite loop** despite passing scores. The 25-word cap is stricter than this brand's real explanatory prose (26–39-word sentences), and some hits are page-furniture/table artefacts of extraction rather than prose.

## Score behaviour

| Probe | Result | Observation |
|---|---|---|
| 1 banned term, short text | 85.0 | exactly at `BRAND_PASS_SCORE`, but gate hard-fails on the banned term anyway |
| 1 banned term in a ~600-word compliant draft | 85.0 | **no density/length scaling** — identical penalty in 10 words or 600 |
| Same banned term ×3 | 55.0 | linear −15/occurrence |
| 7 different banned terms | 0.0 | floor `max(0, score)` saturates — worse drafts are indistinguishable |
| `Ngon quá` + 20×`!` (one token) | 0.0 | **−5 per mark, unbounded**: a single punctuation run can zero a section, outweighing multiple banned terms |
| `!!!!!` (5 marks) | −25 from exclamations alone | run of marks counted per character, not per occurrence |
| Median 15 vs range ≤14 (off by 1) | −10 | flat penalty… |
| Median 40 vs range ≤14 | −10 (median component) | …identical regardless of deviation magnitude |
| 26-word sentence vs 200-word sentence | −2 each | flat per-sentence penalty regardless of overage |
| `lint_draft` aggregation | worst section's score | one weak section tanks the whole draft (documented design); the failing list also includes every section with any violation, however minor |

Net: penalties are flat, unnormalised by length or deviation, and asymmetric — exclamation runs are the most expensive mistake per character, sentence-length violations the cheapest, and the score floor (0) plus pass threshold (85) compress most realistic drafts into a narrow band.

## Severity ranking

1. **HIGH — Table/pipe exemption abuse.** Two `|` characters exempt arbitrary prose from all length statistics (score 100, zero violations). Trivial, complete evasion of both length rules.
2. **HIGH — Invisible-character evasion of banned terms.** ZWSP U+200B, ZWJ U+200D, soft hyphen U+00AD, U+FEFF inside a term split it during normalisation. Realistic via copy-paste from editors/AI tools; yields score 100.
3. **HIGH — Homoglyph / fullwidth evasion.** Cyrillic lookalikes and fullwidth Latin are not folded (NFD only, no NFKC/confusable handling) and evade every phrase rule.
4. **MEDIUM — Digit-guard evasion.** `elevate7` / `7elevate` evade because digit-boundary guards apply to all phrases, including purely alphabetic ones.
5. **MEDIUM — Exclamation glyph coverage.** U+203C `‼`, U+2757 `❗`, U+00A1 `¡` uncounted — 3/8 blog exclamation cases evaded.
6. **MEDIUM — No negation/quotation context.** Negated denials and quoted/criticised claims are hard-flagged as banned terms (gate hard-fail); CNM's `số 1` also flags step enumerations and priority statements.
7. **MEDIUM — `sentence_too_long` is nearly free.** Flat −2 regardless of overage, never blocks the hard gate; median gaming by padding is trivial.
8. **MEDIUM — Exclamation penalty disproportion.** −5/mark unbounded; one mark-run can zero a section, dominating far more serious violations; score saturates at 0.
9. **LOW — Markup/entity/letter-spacing splits.** `game-<b>changing</b>`, `game&#45;changing`, `s e a m l e s s`, `e.l.e.v.a.t.e`, and inserted-word variants evade phrase matching.
10. **LOW — Median instability on short sections + self-inconsistent brand files.** Brands' own approved/good examples fail their own median rules; medians of ≤6 sentences swing on one sentence; all 5 real CNM posts (28 `sentence_too_long`) would be sent to rewrite despite passing scores.
11. **LOW — Degenerate inputs score 100.** Empty, whitespace-only, and table-only texts return a perfect score from `lint_section` (mitigated at gate level by the vacuity guard only).
12. **LOW — Substring over-matching of Latin terms.** `seamlessly`, `leveraged`, and the URL `elevate.com` are flagged; strictness may be intended, but there is no word-boundary or allowlist mechanism.

## Improvement suggestions (not implemented)

- Strip Unicode category Cf (format) characters in `normalize_for_match` before collapsing punctuation; apply NFKC and a Latin confusable-skeleton fold so homoglyph/fullwidth variants match.
- Scope the digit-boundary guards to phrases that actually end/begin with a digit (e.g. `số 1`), letting alphabetic terms match when glued to digits.
- Normalise exclamation variants (`‼`, `❗`, `¡`, `❢`…) to `!` before counting; count a *run* of marks as one occurrence for scoring.
- Add a negation/quotation context check for banned phrases (reuse the negation approach already in `fact_check`); consider downgrading quoted/negated hits to flagged-for-review instead of hard violations. For ultra-short banned phrases like `số 1`, require claim-like context (e.g. followed by a noun/superlative frame) or accept enumerations (`Bước số 1:`, `ưu tiên số 1:`) via an allowlist pattern.
- Make the table exemption safer: only exempt segments when the *section* is predominantly table-like (multiple rows / high pipe density), or cap the exempted share of total words; never let exempted text reach a perfect score unexamined.
- Scale `sentence_too_long` penalty by overage (e.g. −2 per 10 words over) and consider making extreme overages (>2× cap) gate-relevant.
- Cap the exclamation penalty per section (or per run) so punctuation cannot outweigh banned terms; report raw counts separately from score impact.
- Strip HTML tags/entities before matching; optionally collapse single-letter spacing (`s e a m l e s s`) in a second matching pass.
- Report a low-confidence note when a section has < ~8 sentences, since the median is unstable there; re-derive the CNM median band from the brand's approved examples (they centre on 19–25, not 11–17).
- Give `lint_section` an explicit empty/no-prose result instead of 100 so standalone callers cannot mistake it for a pass.

## Method note

Adversarial drafts were engineered per rule and padded with compliant text where needed so the targeted rule was the deciding factor; benign drafts were hand-written to each brand's stated rules per format, plus both brands' own YAML example/anti-example texts and the 5 real CNM posts in `calibration/work/samples.json`. Some short synthetic cases also tripped the median rule as a side effect; those side effects are visible in the raw results and were excluded when judging whether the targeted rule fired. Raw per-case output: `/tmp/eval_results.json` (98 cases).
