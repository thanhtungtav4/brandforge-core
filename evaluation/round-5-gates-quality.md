# Round 5 — Gates & Quality Profile: Property Evaluation

**Scope:** `content_core/gates.py` (`evaluate_gate`) + `content_core/quality.py`
(`evaluate_quality`, profiles) treated as one system, including both call
sites (MCP `final_gate` tool in `mcp_server.py`, LangGraph `Core.final_gate`
in `content_core/graph.py`).

**Method:** property-style testing. Drafts were generated programmatically
(a calibrated "perfect" base text, single-violation injectors/fixers,
randomised bases) and the gate/quality system was checked for *invariants*
(monotonicity, floor dominance, hard-gate dominance, renormalisation
arithmetic, parity), not just example behaviour. Baseline: commit `8215b8c`,
213 project tests green before and after; this round was **read-only** —
no core code, tests, or data were modified (scratch harnesses in `/tmp`).

**Calibrated base text (T0)** — one blog section, sentence lengths
[9, 12, 13, 18], median 12.5: brand 100, 0 fact issues, craft 100, and all
four measured quality axes = 100 in **all five formats**. Every property
below is measured as a delta against this base or against hand-computed
expectations.

## Headline results

| Property | Cases | Result |
|---|---|---|
| Monotonicity — fix chains (all 720 fix orders × 6 fixes) | 4,320 steps | **0 inversions** |
| Monotonicity — add chains on clean base (4 orders × 6 injectors) | 24 steps | **0 inversions** |
| Monotonicity — targeted median pair | 2 | **INVERSION (verdict flip)** |
| Monotonicity — random sweep (blog+email bases × 4 injectors) | 420 | **64 inversions, all from one injector** |
| Floor dominance (real text + all presets + stubbed arithmetic) | 107 | **all hold** |
| Hard-gate dominance (banned / prohibited / vacuous / wrong price) | 8 | **all hold** |
| Judged renormalisation vs hand computation (Fraction arithmetic) | 14 combos | **exact match, all** |
| Invalid judged grades / axes | 10 | **all rejected** (see notes) |
| Profile validation & persistence | 36 | **all behave as coded** (3 design findings) |
| MCP ↔ graph parity (adversarial sections) | 18 verdicts + 11 failing-section sets | **full agreement** |

The one real defect class: **the gate verdict and the quality total are
not monotonic in draft edits**, because two whole-text statistics — the
linter's sentence-length *median* and craft's rhythm *stdev* — can be
"repaired" by appending a single over-long sentence, which is itself a
violation. Everything else (floors, hard layer, renormalisation, parity)
held under every construction tried.

---

## 1. Monotonicity

Tracked measures per step: gate `passed`, brand score, quality
`weighted_total` (blog/email preset, all judged axes graded 2), and
unflagged fact-issue count. "Worse" = any measure degrades; "better" =
any measure improves.

### 1a. Fix direction — clean

A dirty draft (brand 65: banned term `delve` −15, median violation −10,
2 exclamations −10; 2 fact issues: wrong price 200.000đ + unsupported
claim "tốt nhất"; craft 100) was repaired by 6 single-violation fixes
applied in **all 720 permutations**. At every one of the 4,320 steps,
brand score, quality total and issue count moved only in the improving
direction, the verdict never flipped back, and every order ended at
(passed, brand 100, 0 issues). The add direction on the clean base T0
(6 injectors — banned term, exclamation, wrong price, unsupported claim,
weak word, too-long sentence — in 4 orders) likewise never improved any
measure. **For point-wise violations (terms, prices, claims, punctuation)
the system is exactly monotonic.**

### 1b. The median inversion — adding a violation flips FAIL → PASS

Base draft A (one blog section, 2 sentences of 10 and 11 words, 2
exclamation marks, sensory term present, fact-clean):

> `mùi hương thơm nhẹ lan tỏa trong không hậu ngọt! nhỏ yên tĩnh ly nước ấm vị dịu hậu rõ từng!`

Measured: sentence lengths [10, 11] → median 10.5, outside the blog band
[11, 14] → −10; exclamations −10 → **brand 80.0, gate FAIL**, quality
total 95.6.

Draft B = A + **one appended 26-word sentence** (a `sentence_too_long`
violation, −2):

> A + `ngụm bàn gỗ cửa sổ mở vườn xanh lá cây gió mát trời khách tới ngồi xuống chiếc ghế bên cạnh mùi hương thơm nhẹ lan.`

Measured: lengths [10, 11, 26] → median 11, **inside the band** → the
−10 median penalty disappears; net brand = 100 − 10 (exclamations)
− 2 (too-long) = **88.0, gate PASS**, quality total 96.72.

So appending a violating sentence improved *every* tracked measure and
flipped the verdict. Read in reverse it is worse: starting from B,
*removing* the draft's only length violation (the natural "fix") drops
the brand score 88 → 80 and flips PASS → FAIL. A rewrite loop told to
"fix the failing sections" can therefore oscillate or be gamed by
padding.

The inversion survives — and is amplified by — the style-profile half of
the voice axis. With the shipped `mocbrew-blog-voice` Style Profile
attached, voice = mean(brand, style_match): A scores voice 78.5
(brand 80, style 77), B scores voice **90.0** (brand 88, style 92),
because the long sentence also pushes the draft's sentence-length stdev
toward the profile's bursty fingerprint (target stdev 8.72). Total
95.39 → 97.0.

### 1c. Random sweep — two mechanisms, one injector

420 (base, injector) cases over randomised blog/email bases (2–6
pool-word sentences). **64 inversions, every one caused by the
"append a 26-word sentence" injector on blog bases**; the other three
injectors (banned term, exclamation, weak word) and all email cases
produced none. A focused re-run of just that injector (120 bases)
classifies the 44 inversions found there:

- **26 × brand-up (median mechanism).** E.g. base lengths [9, 11]
  (median 10 → −10, brand 90) + 26-word sentence → median 11 → brand 98.
  Any short base whose median sits just below the band can be pulled
  into the band by one long sentence.
- **18 × total-up-only (rhythm mechanism).** Brand correctly worsens
  (90 → 88) but the quality total *rises* (93.5 → 95.32): the base's
  joined text was `monotone_rhythm` (sentence-length stdev < 2.0, −15
  clarity), and the appended long sentence raises the stdev, erasing
  the −15. At blog weights the clarity gain (14 × 15) outweighs the
  voice loss (14 × 2) in the total. Exact repro — base (lengths
  [9, 10, 10, 12], craft 75) vs base + `thơm nhẹ lan tỏa trong không
  gian quán nhỏ yên tĩnh ly nước ấm vị dịu hậu rõ từng ngụm bàn gỗ cửa
  sổ mở vườn.` (craft 90):

  > `mùi hương thơm nhẹ lan tỏa trong không gian. không gian quán nhỏ yên tĩnh ly nước ấm vị. nước ấm vị dịu hậu rõ từng ngụm bàn gỗ. ngụm bàn gỗ cửa sổ mở vườn xanh lá cây gió mát.`

Root cause, both mechanisms: **sentences that violate the max-length
rule still count as evidence in the median and stdev statistics**, so a
violation can pay for itself. This is a scoring-design issue, not a
hard-gate breach — banned terms and fact issues remain strictly
monotonic and dominant (§3).

### 1d. Documented exception (not counted as a defect)

Adding `[NEEDS SOURCE]` to a sentence carrying an unflagged prohibited
claim converts a hard-blocking issue into a flagged claim: gate
(False, 1 issue, total 89.5) → (True, 0 issues, total 95.2). This is
the documented escape hatch working as designed; it is an *improvement
by adding a marker*, listed here so the monotonicity statement above is
precisely scoped to violations.

---

## 2. Floor dominance — holds everywhere (107/107)

For every axis, drafts were constructed with `weighted_total ≥
pass_total` but that axis below its floor; `quality_pass` was False with
`floors_failed == [axis]` in every case.

**Real text, blog preset** (all other axes at 100 / judged grade 2):

| Axis below floor | Construction | Axis score | Total |
|---|---|---|---|
| facts | wrong price + "giao trong 5 ngày" (2 issues) | 40 | 87.6 |
| voice | 9 exclamation marks (length-preserving `!!`) | 55 | 92.1 |
| clarity | weak-word/monotone/no-sensory text (craft 35) | 35 | 89.3 |
| seo | empty brief + empty heading | 25 | 81.9 |
| coherence | judged grade 0 | 20 | 87.9 |
| persuasion | judged grade 0 | 20 | 92.4 |
| depth | judged grade 0 | 20 | 90.9 |

**Judged axes, all 5 presets:** each judged axis at grade 0 (others 2)
fails on exactly that floor with totals 81.3–92.4 (all ≥ pass_total);
all three at 0 fails with all three floors listed.

**Arithmetic level** (measured scorers stubbed, judged scores set via
`GRADE_SCORES`): for each preset × each axis — axis at floor−1 with all
others at maximum → fails, `floors_failed == [axis]`, total ≥ 88;
axis *exactly at* its floor → **not** below floor (strict `<`), quality
passes. Boundary semantics are correct.

**All-axes-at-floor:** with every axis sitting exactly on its floor, no
floor fails, yet the weighted total is only ≈ 60.7 (blog), 61.9
(product), 61.0 (social), 61.3 (email), 62.4 (comparison) — all <
PASS_TOTAL 70, so the draft still fails, on the total. (Verified with
the persuasion floor raised to 60 in a test profile so grade 1 could
sit exactly on it; with the shipped floor of 55 the totals are lower
still.) Floors are true minimums, not a passing recipe — the design's
anti-compensation intent holds in both directions.

One structural note: the seo axis can only score {0, 25, 50, 75, 100}
and judged axes only {20, 60, 95}, so the seo floor (55) and persuasion
floor (55) never bind *at* their exact value from real inputs — the
nearest achievable scores straddle them (50/75 and 20/60). Harmless,
but the floors' effective thresholds are 75 and 60 respectively.

---

## 3. Hard-gate dominance — holds everywhere (8/8)

With the quality layer active and given every chance to pass (preset
profile, all judged axes grade 2):

- **H1 — banned term at the score boundary.** T0 with one word replaced
  by `delve`: brand score is exactly **85** (passes the score check) and
  `quality_pass` is **True** (total 96.3) — the gate still **fails**,
  on the banned-term rule alone.
- **H2 — unflagged prohibited claim.** T0 + "chữa mất ngủ": facts axis
  lands exactly on its floor (70) so `quality_pass` is True (93.0) —
  gate **fails** on the hard fact issue.
- **H3 — flagged prohibited claim** (same text + `[NEEDS SOURCE]`):
  gate **passes**, facts axis 90 — the documented escape hatch, working
  in both directions.
- **H4 — single wrong price:** gate fails while `quality_pass` is True
  (total 93.0, facts 70).
- **H5 — vacuous drafts.** `sections=[]` and a whitespace-only section
  both fail the gate via the vacuity guard, with perfect judged inputs.
  Inside the quality layer the empty draft reads facts **100** and
  voice **100** (lint/check of no text), and is caught only by clarity
  (0, too-short) and seo (50): `floors_failed == ["clarity", "seo"]`,
  total 73.4. The gate is safe; the *quality report on an empty draft*
  is misleading (see §6, finding 4).
- **H6 — banned term in the second section** of a two-section draft:
  fails, `failing_sections == ["s2"]`.
- **H7 — banned term in social format** (craft advisory there): still
  fails — hard dominance is format-independent.

---

## 4. Judged axes — renormalisation exact; input validation mostly strict

**Renormalisation verified by hand.** With T0's measured base
(Σ score·weight = 6800 over weight 68, blog preset), the expected total
for 11 judged combinations (0/1/2/3 axes supplied; grades 0, 1, 2) was
computed with exact `Fraction` arithmetic and matched
`evaluate_quality`'s rounded total **in all 11 cases** (e.g. no judged →
100.0; {coherence: 0} → 86.34; {coherence: 1, persuasion: 0, depth: 2} →
87.5; all grade 0 → 74.4). Three more combos on a non-perfect base
(wrong price, measured sum 6260) also matched exactly (92.06 / 92.44 /
78.0). Missing axes are listed in `missing_axes`, score `None`,
`below_floor` False — never invented, as documented. All-grade-1 gives
scores of exactly 60 on coherence/depth, which sit *on* their floors
(not below), and the draft passes — boundary behaviour confirmed.

**Invalid inputs** — all rejected with `ValueError`: grades `3`, `-1`,
`"high"`, `"1"`, `None`, `True`, `False` (the bool guard from audit
round 2 works — `False == 0` would otherwise grade as 0), `2.5`; judged
keys naming a measured axis (`{"facts": 2}`) or an unknown axis.

Two rough edges (low severity):

- **Float grades `0.0` / `1.0` / `2.0` are silently accepted** (`1.0 in
  {0, 1, 2}` is True in Python) and recorded as `"grade": 1.0`. Scoring
  is correct, but acceptance is inconsistent with the strict bool
  rejection — a JSON producer emitting `1.0` gets no signal.
- **`judged` passed as a non-dict** (e.g. a list of pairs) raises
  `AttributeError: 'list' object has no attribute 'items'`, not
  `ValueError`. The MCP `final_gate` catches only `ValueError`, so a
  malformed direct call crashes unhelpfully (MCP transports deliver
  JSON objects in practice, so exposure is limited to direct API use).
- Relatedly, `evaluate_gate(..., judged={...})` with a format that has
  no preset (`"tiktok"`) raises `ValueError: no quality preset` instead
  of returning a verdict (the MCP tool converts this to an error dict;
  the graph never passes judged).

---

## 5. Profile handling — validation strict; id resolution has a trap

All checks ran through the MCP tools (`save_quality_profile` /
`get_quality_profile`) against an isolated profile dir.

**Validation (all as designed):** weights summing to 99 or 101 rejected;
negative or zero weights that still sum to 100 rejected (range [2, 45]
checked per axis); weight 46 rejected; boundary profile
{45, 45, 2, 2, 2, 2, 2} accepted and round-trips; float weights summing
to exactly 100.0 accepted; unknown format rejected on save and on get;
floor 95 rejected, floor 90 accepted, unknown floor axis rejected, bool
floor rejected; `pass_total` 101 / −1 rejected. All 5 presets verified:
keys == 7 axes, weights sum to exactly 100, each in [2, 45], default
floors, `pass_total` 70. Content-derived ids are deterministic (same
content → same id; different floors → different id). Ids `"ab"` vs
`"a/b"` round-trip to **distinct** profiles (the injective-stem fix
holds here too). A corrupt stored file and a stored file with invalid
weights both surface as clean `{"error": ...}` dicts, not crashes.

**Findings:**

1. **Profile-id shadowing (the round's second real defect).**
   `save_quality_profile` happily stores a custom profile under the id
   `"preset-blog"` or `"blog"` — and `get_profile` resolves format
   names and `preset-*` ids *before* stored profiles, so the saved
   profile is **unreachable**: save reports success with seo weight 20;
   get returns the shipped preset (seo 22). The same silent substitution
   would occur inside `final_gate(quality_profile="preset-blog")`.
   Nothing warns at save time.
2. **Silent overwrite.** Saving twice under one explicit id replaces
   the first profile with no signal in the response (the second save's
   weights are simply what get returns). Plausibly intended upsert
   semantics, but invisible to the caller.
3. **The quality layer can be configured into a no-op.** A profile with
   all floors 0 and `pass_total` 0 validates fine; under it,
   `quality_pass` is True even for a banned-term draft with all judged
   axes graded 0 (measured total 44.56). Gate safety is *not* affected —
   the hard layer is independent and still fails that draft — but a
   setup UI should warn when a profile's floors/total make the layer
   vacuous.

---

## 6. Parity — MCP final_gate ≡ LangGraph final_gate (18/18)

Verdicts compared between `srv.final_gate(draft_id)` (sections stored
via `save_section`) and `Core().final_gate(state)` on identical section
lists: no sections; whitespace-only; empty text; clean; clean+banned in
both orders; clean+whitespace; craft-failing text in blog / product
(blocking → fail) and email / social / comparison (advisory → pass);
craft-failing + clean sections in blog; empty heading; fullwidth `！`
exclamations; duplicate section ids; two clean sections. **All 18
verdicts agree**, and in all 11 failing cases the `failing_sections`
sets are identical between the two paths. The graph node additionally
tolerates section dicts missing `text` / `id` / `heading` keys without
crashing (treated as empty / unnamed). The "one rule, two call sites"
claim in `gates.py` is empirically true for verdicts, including the
adversarial shapes the existing parity test (4 happy-path cases) does
not cover.

One asymmetry worth knowing (not a verdict divergence): only the MCP
path can activate the quality layer and the style report — the graph
calls `evaluate_gate` with neither, so graph flows are judged by the
hard+craft layers alone. If the product ever routes graph flows
expecting quality gating, that call site must pass a profile/judged
explicitly.

---

## 7. Minor observation — flagged percent claims vanish

A statistic claim phrased as a bare percentage behaves differently from
the other claim kinds when marked: `Có 90% khách hàng quay lại mua tiếp
[NEEDS SOURCE].` produces **no issue and no flagged entry** — the
percent branch in `check_section` simply skips marked sentences, while
marked claim phrases and marked statistic-shaped numbers *are* recorded
in `flagged_claims`. Unmarked, the same sentence yields an
`unsupported_statistic` issue. The gate outcome is correct in both
cases; the *report* silently loses the fact that a sourced-percentage
claim exists in the draft.

---

## Severity ranking

| # | Finding | Severity |
|---|---|---|
| 1 | Non-monotonicity: an over-long sentence can erase the median penalty (brand +8, verdict FAIL→PASS) and/or the monotone-rhythm penalty (quality total rises while brand falls). 64/420 random add-cases; verdict flip proven. | **Medium** |
| 2 | Custom quality profiles saved under a format name or `preset-*` id are silently shadowed by the shipped preset — save succeeds, the profile can never be loaded or applied. | **Low–Medium** |
| 3 | Quality report on a vacuous draft shows facts 100 / voice 100; only clarity+seo catch it inside the quality layer (gate itself is safe). Misleading if `quality_score` is ever shown standalone on an empty draft. | Low |
| 4 | Percent claims marked `[NEEDS SOURCE]` leave no trace in `flagged_claims`, unlike all other flagged claim kinds. | Low |
| 5 | Float judged grades (1.0) silently accepted; non-dict `judged` raises `AttributeError` instead of `ValueError`; gate + judged + preset-less format raises instead of returning a verdict. | Low |
| 6 | Silent overwrite on re-saving an explicit profile id; validation permits a fully vacuous quality profile (floors 0 / pass_total 0). | Info |

## Improvement suggestions (none applied — evaluation only)

1. **Close the median/rhythm loophole at its root:** exclude sentences
   longer than `max_sentence_words` from the median and rhythm-stdev
   statistics (they are already penalised individually). An over-long
   sentence then cannot repair the very statistics it violates, and
   both inversion mechanisms disappear without touching any threshold.
   Alternatively/additionally, make the per-sentence too-long penalty
   at least as large as the maximum median penalty it could erase
   (currently −2 vs −10).
2. **Reject colliding profile ids at save time** (format names and the
   `preset-` prefix), or resolve stored profiles before presets; have
   `save_quality_profile` report when it overwrites an existing id.
3. **Vacuity guard inside `evaluate_quality`:** a draft with zero total
   words should report measured axes as 0 (or an explicit
   `"empty": true` marker) instead of vacuous 100s, mirroring the
   gate's hard-layer guard.
4. **Record marked percent claims in `flagged_claims`** for report
   completeness, symmetric with claim phrases and statistic numbers.
5. **Normalise judged-grade validation:** accept only `int` grades
   (reject floats explicitly, as bools already are), and raise
   `ValueError` for a non-dict `judged` argument.
6. **Warn at setup** when a custom profile's floors/`pass_total` make
   the quality layer unable to fail anything.

## What was verified to be solid

- Fix-direction monotonicity across **all 720 fix orders** (4,320 steps,
  zero regressions) and add-direction monotonicity for every point-wise
  violation type.
- Floor dominance for all 7 axes on real text, all 5 presets for judged
  axes, and the full arithmetic boundary sweep (floor−1 fails, exactly-
  at-floor passes, all-at-floor fails on total ≈ 60–62 < 70).
- Hard-gate dominance at the sharpest edges: brand score exactly 85
  with a banned term, facts axis exactly at its floor with a prohibited
  claim — the quality layer passing in both cases and the gate still
  failing.
- Renormalisation arithmetic exact against hand computation in all 14
  combinations tried, including a non-perfect measured base.
- MCP ↔ graph verdict parity on 18 adversarial section shapes,
  including identical rewrite lists.

*Harnesses (read-only, rerunnable): `/tmp/r5_probe.py`,
`/tmp/r5_monotonicity.py`, `/tmp/r5_sweep_detail.py`, `/tmp/r5_floors.py`,
`/tmp/r5_hard_judged.py`, `/tmp/r5_profiles.py`, `/tmp/r5_parity.py`,
shared fixtures in `/tmp/r5_common.py`. Project state after the round:
213 tests green, `git status` clean except this report.*
