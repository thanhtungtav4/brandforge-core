# Evaluation Round 3 — Craft Library & `craft_check` Validity

**Scope:** `content_core/craft.py`, `data/craft_library.json` (12 cards), `data/exemplars.json` (5 exemplars), `data/domain_glossary.json` (25 terms, 16 weak words).
**Mode:** READ-ONLY. No core code, tests, or data were modified. All probes are original texts written for this round, run in-process against the shipped functions (`craft_check`, `craft_check_sections`, `retrieve_cards`, `get_exemplars`, `sensory_terms`, `resolve_glossary`). Probe script + full JSON output: `/tmp/r3_probes.py`, `/tmp/r3_probes2.py`, `/tmp/r3_out.json`.
**Baseline state:** 213 tests green, commit `8215b8c`. Real-world reference data: `calibration/caonguyenmoc-report.md` + `calibration/work/self_consistency.json` (5 real Cao Nguyên Mộc posts).

---

## 1. Method

1. **Graded ladders.** Two 6-rung ladders on a fixed topic (blog: a phin-brewing section; product: a 250 g plain-roast coffee bag), written from deliberately poor (weak-word stuffing, filler, uniform sentence lengths, same opening word, zero sensory terms) to strong (concrete facts, varied rhythm, glossary sensory detail). A valid quality score must increase monotonically rung by rung.
2. **Adversarial pairs.** Texts engineered to break the *construct* (does the score measure prose quality?): sensory-rich word salad; a meta-discussion *about* the weak word "ngon"; deliberate rhetorical anaphora.
3. **Component isolation.** Each scoring component (weak words, filler, monotone rhythm, repeated openings, sensory, too-short guard) probed with should-trigger and should-not-trigger texts, including the generic-glossary path (`brand_id` ≠ `moc-brew`) and the calibration "cà" tokenisation artefact.
4. **Retrieval matrix.** `retrieve_cards` over format × intent sweeps (15 intents × 5 formats with empty text, plus 21 targeted keyword/intent cases), judged for relevance, cap-of-3 losses, and dormancy misfires.
5. **Data audit.** Exemplar coverage per format + exemplars scored by `craft_check` itself; every card's good/bad example pair scored; glossary cross-checks (duplicates, substring overlaps, replacements containing weak words, generic-vs-shipped diff).

---

## 2. Scoring validity — ladder results

### 2.1 Blog ladder (topic: pha phin tại nhà)

| Rung | Intended quality | Score | Deductions |
|---|---|---|---|
| L1 | Worst: 7 weak hits, 5 fillers, monotone, "Cà phê…" ×4, no sensory | **23** | weak −30 (cap), filler −12 (cap), monotone −15, openings −10, sensory −10 |
| L2 | Poor: 2 weak, monotone, repeated openings, no sensory | **53** | weak −12, monotone −15, openings −10, sensory −10 |
| L3 | Plain-correct: clean words, uniform lengths, no sensory | **75** | monotone −15, sensory −10 |
| L4 | Competent: varied rhythm, still no sensory term | **90** | sensory −10 |
| L5 | Good: varied rhythm + exactly 1 sensory term ("caramel") | **100** | — |
| L6 | Strong: concrete numbers, scene, 5+ sensory terms | **100** | — |

### 2.2 Product ladder (topic: gói cà phê rang mộc 250 g)

| Rung | Intended quality | Score | Deductions |
|---|---|---|---|
| L1 | Worst: 11 weak hits, 4 fillers, monotone, openings, no sensory | **23** | weak −30 (cap), filler −12 (cap), monotone −15, openings −10, sensory −10 |
| L2 | Poor: 2 weak, monotone, repeated openings, no sensory | **53** | as blog L2 |
| L3 | Plain spec-sheet prose (packaging, weights, dates), uniform lengths | **85** | monotone −15 |
| L4 | Competent, concrete (price/date/packaging), varied rhythm, no sensory term | **90** | sensory −10 |
| L5 | Good: + 1 sensory term | **100** | — |
| L6 | Strong: full sensory arc, facts first, varied rhythm | **100** | — |

\* Product L3 is a pure packaging spec sheet, yet it *passes* the sensory check: the phrase "Hạt rang nguyên hạt" contains the aroma-group term "hạt rang". The check rewards any incidental glossary string, however non-sensory the passage — see §3.5.

**Monotonicity verdict:** No inversions inside either ladder — scores are non-decreasing (23 → 53 → 75/85 → 90 → 100 → 100). The ordering machinery works. The validity problems are at the **ends of the scale and in the construct**, not in rung ordering:

- **Ceiling saturation.** L5 and L6 both score 100. One dropped-in sensory term ("mùi caramel thoảng lên") buys the same score as a fully realised sensory passage. The score cannot distinguish "clean and adequate" from "genuinely strong" — the entire top half of prose quality is compressed into 90–100, and 100 is reachable by a checklist, not by quality.
- **Floor/cap saturation.** A text with **5** weak-word hits and a text with **16** weak-word hits score identically (70, social format; weak component capped at −30 in both). Below the cap threshold the score stops measuring how bad the text is.
- **Dynamic range.** Of the 100 points, only five surface defects can ever move the score (weak ≤30, filler ≤12, monotone 15, openings 10, sensory 10 = max −77; observed floor on real text 23). Everything else a writing teacher would grade — coherence, logic, accuracy of imagery, paragraph unity, voice, verb choice — is unmeasured (see §2.3).

### 2.3 Adversarial results — where the construct breaks

| Probe | Human judgement | Score | Why |
|---|---|---|---|
| **Word salad** stuffed with sensory terms ("Cacao caramel khói nhẹ bay qua cửa sổ tím… một con mèo body dày ngồi đếm mười bảy đám mây chua sáng… đất ẩm hát, crema tròn vị nhảy múa…") | Incoherent nonsense | **100** | No weak/filler hits, varied lengths, no repeated opening, sensory terms present. `craft_check` certifies gibberish as perfect prose. |
| **Meta-discussion of the word "ngon"** (an essay explaining why good writers avoid "ngon", quoting it 3×) | Good, purposeful writing *about* craft | **57** | −18 for the 3 quoted "ngon" hits, −15 monotone, −10 no-sensory. Mention = use; the checker has no quotation/meta exemption. |
| **Deliberate anaphora** ("Pha phin cần… Pha phin cần… Pha phin cần…" — a classic rhetorical device) | Intentional, effective repetition | **65** | −10 repeated_openings ("pha" ×3) + −15 monotone (parallel structure ⇒ uniform lengths) + −10 sensory. The two rhythm components jointly punish a recognised good technique. |
| **Uniform recipe steps** ("Bước một, cân… Bước hai, rót… Bước ba, rót… Bước bốn, chờ…" — stdev 0.0) | Correct instructional format | **75** (social) | −15 monotone + −10 repeated_openings ("bước" ×4): enumeration is double-punished for doing exactly what instructions should do. |

**Card self-consistency check.** Each library card's own `good_example` vs `bad_example` was scored as blog text. For 10/12 cards good > bad. But for **two cards the pair is indistinguishable (90 vs 90)**: `one-idea-per-paragraph` and `active-voice` — because *no scoring component measures paragraph unity or passive voice at all*. The library teaches 12 techniques; the checker can only see 5 defect types, and two flagship techniques are entirely outside its vision. Also, `logical-transitions`' good example scores only 84 — it is fined −6 because its own good example contains the word "ngon" ("Hạt nhập khẩu đắt hơn không phải vì ngon hơn…", a *negated/comparative* use). Several good examples score 90 rather than 100 solely because a 1–2-sentence example cannot satisfy the blog sensory-term rule — the examples were not written to the checker's spec, or the spec is wrong for short texts; either way the two halves of Layer 4 disagree measurably.

---

## 3. Component audit

### 3.1 Weak words (−6/hit, cap −30)

- **Cap saturation** (above): 5 hits ≡ 16 hits ≡ −30. Severity as a *ranking* signal is lost exactly where texts are worst.
- **No context sensitivity.** Negation is still a hit: "Gói này **không ngon** như lời quảng cáo…" (a *negative* review sentence) is fined −6 for "ngon". Quoted/meta use is fined (§2.3, 57-point essay). Comparative/negated use inside the library's own good example is fined (§2.3).
- **Masking works correctly**: "thơm ngon" is counted once, not double-counted as "ngon" (probe `masking_probe` = ["thơm ngon", "tuyệt vời"]). Longest-first masking is sound.
- **Generic path**: with `brand_id="cao-nguyen-moc"`, "ngon" is correctly *not* fined (CNM-style text: default 84 → generic **100**). But see §3.6 — the generic weak list turned out to be a strict subset of the shipped list.
- **What does not trigger**: weak-word detection is raw substring search on lowercased text with no word boundaries. In Vietnamese this is mostly safe (words are space-separated syllables), but multi-syllable weak phrases can fire inside larger innocent strings in principle; no live false positive of this type was produced in this round's probes (the masking design prevents the main overlap case).

### 3.2 Filler phrases (−4/hit, cap −12)

- Mostly sound. One false positive: **"ngày nay"** is fined even in a legitimate historical contrast ("Ngày nay người trẻ uống cà phê pha máy… Ngày xưa ông bà chỉ có phin…") — score 96, −4. "Ngày nay" is a real temporal marker in comparative/historical writing, not always filler. Low impact (−4) but it is a standing tax on a common, meaningful phrase.
- The filler list overlaps the `logical-transitions` card's targets ("đáng chú ý là", "và hơn thế nữa") — consistent between library and checker. Good.

### 3.3 Monotone rhythm (−15; ≥4 sentences, sample stdev of sentence word-counts < 2.0)

- **The ≥4-sentence gate is a cliff**: a 3-sentence text with stdev 0.0 scores **100** (probe `monotone_3sentences_escapes`), while the same uniformity in 4 sentences costs −15. Short sections — the unit the gate actually rewrites — routinely have 3 sentences and are structurally immune to the rhythm check.
- **Uniformity is not always a defect**: numbered recipe steps (stdev 0.0) are fined −15 (plus −10 openings, §3.4). Instructional, spec, and list-like prose has naturally uniform sentence lengths.
- The metric is **word-count stdev only**: two texts with identical length profiles but completely different cadence (clause structure, punctuation) are indistinguishable. It also uses *sample* stdev (`statistics.stdev`), so short sections get inflated variance estimates — a 4-sentence section needs quite extreme uniformity to trigger, while a 20-sentence post triggers on mild uniformity. Sensitivity therefore varies with section length in a way the −15 flat penalty does not reflect.
- Correctly *not* triggered by the ladders' varied rungs, and the pinned demo behaviour (old demo = 85, monotone only) reproduces.

### 3.4 Repeated openings (−10; same first token opens ≥3 sentences) — the "cà" artefact, characterised

This is the weakest component. Mechanism (craft.py): for each sentence, take `sentence.split()[0]`, strip non-word characters, lowercase, count; any token with count ≥ 3 costs a flat −10.

Precisely characterised failure modes, all reproduced this round:

1. **Vietnamese tokenisation artefact (the calibration finding).** Vietnamese orthographic words are multi-syllable; the checker counts the first *syllable-token*, not the first word. Every sentence opening "Cà phê…" counts as opening **"cà"**. On the 5 real Cao Nguyên Mộc posts this fired on **every post** (−10 each; counts ×6–×14 per the calibration self-consistency data, e.g. `'cà' x6, 'cách' x3`). A single-topic how-to post *must* start sentences with its topic noun; the deduction is a constant tax on topical prose, carrying almost no quality signal.
2. **Cross-topic merging.** Probe with three *different* subjects — "Cà phê phin cần…", "Cà chua chín đỏ…", "Cà tím nướng mỡ hành…" (coffee / tomato / aubergine) — counts as `'cà' x3` and is fined. The component cannot distinguish one repeated opening from three different words that share a first syllable.
3. **Brand-name subjects.** "Mộc rang… / Mộc ghi… / Mộc chỉ dùng…" (brand name as sentence subject, the house style of the very brand family this system serves) counts `'mộc' x3` → −10. Subject continuity — normally a *clarity virtue* — is fined.
4. **Enumeration.** "Bước một… Bước hai… Bước ba… Bước bốn…" → `'bước' x4`, −10, stacked on top of the monotone fine for the same text (§2.3).
5. **Flat penalty.** ×3 and ×14 cost the same −10; there is no gradation, and no exemption for anaphora, enumeration, or topic nouns. The component also only sees the *first* token: genuinely repetitive openings with varied first syllables ("Cà phê này… / Loại cà phê… / Với cà phê…") escape entirely.

Net: on real published Vietnamese prose this component fired 5/5 times for artefactual reasons. As calibrated, it is close to a constant −10 on topical blog writing rather than a detector of lazy openings.

### 3.5 Sensory detail (−10; blog/product only; zero glossary sensory terms found)

The check is **glossary-membership, not sensory presence**. All three failure directions reproduced:

- **False negative (rich sensory prose, wrong vocabulary).** A passage dense with sensory detail — "mùi hoa nhài và mật ong rừng… chát nhẹ ở đầu lưỡi… đắng dịu… màu hổ phách sáng" — using *zero* shipped-glossary terms scores **90 with `no_sensory_detail`**. The Cao Nguyên Mộc writer-trial (149 words, *about* preserving aroma: "mùi thơm giữ được lâu hơn") likewise scores 90 under the default glossary because "mùi thơm" is not a Mộc Brew term — the exact false fire recorded in calibration.
- **False positive (mention ≠ depiction).** "Gói này tuyệt đối **không có mùi cacao** như quảng cáo vẫn viết" — a sentence *denying* the sensory quality — counts "cacao" as sensory detail used and passes at 100. A packaging description whose only "sensory" string is the *colour of the bag* ("Túi giấy màu **caramel**") also passes at 100.
- **Metric double-counting.** `sensory_terms_used` is a raw substring set: the single phrase "hậu cacao" reports **both** "cacao" and "hậu cacao" as used terms, inflating the metric (and any downstream "≥5 terms" style assertions — cf. the pinned rich-product test expecting ≥5 terms, which this inflation helps satisfy).
- **Binary.** One incidental term = full pass; there is no density or relevance gradation. Combined with §2.2's spec-sheet pass ("hạt rang" in a packaging paragraph), the check certifies *vocabulary contact*, not sensory writing.
- **Generic path is nearly free**: the generic sensory set includes the bare token **"thơm"**, matched as a substring — virtually any Vietnamese food/drink text passes ("mùi thơm", "thơm thoảng", even "thơm ngon" if it survived the weak-word pass). Under the generic glossary the CNM trial goes 90 → **100** — better than the false fire, but the check has stopped discriminating.

### 3.6 Generic glossary — what it actually is

Diffing `GENERIC_GLOSSARY` against the shipped Mộc Brew glossary:

- **Weak words: the generic list (14) is a strict subset of the shipped list (16).** `generic_only = []`. The *only* difference in weak-word judging for another brand is that "ngon" and "thơm ngon" are not fined. Every other shipped weak word — including brand-flavoured ones like "giá rẻ bất ngờ" — still applies to every brand.
- **Sensory: genuinely different** (broad terms: "thơm", "mùi thơm", "vị chua/ngọt/đắng", "béo", "giòn", "mịn"…) — more portable, but see the "thơm" substring issue above.
- **Reachability problem (integration).** The generic path only activates when a caller passes `brand_id` (or an explicit glossary). The two automated pipelines do **not**: `gates.py` calls `craft_check_sections(sections, fmt)` and `quality.py`'s clarity axis calls `craft_check(..., fmt)` — neither passes `brand_id`. So in the actual gate and quality score, *every* brand is still judged by the Mộc Brew glossary; the F8 fix only helps direct/MCP `craft_check` calls that remember to pass `brand_id`. The calibration false fires therefore persist on the paths that block publication.

### 3.7 Too-short guard (score 0 below 10 words)

- Works as designed at the boundary: 9-word rich fragment ("Xé túi mùi cacao bốc lên ngụm đầu sáng.") → **0**; an 11-word vacuous sentence ("Cái này là một cái mà người ta vẫn thường nói.") → **100**. The cliff is intentional (fragments aren't prose), but note the asymmetry it creates with §3.3: a 9-word text is *infinitely* worse than an 11-word text, while a 3-sentence monotone text escapes rhythm judgement entirely. Short social copy lives exactly in this 8–15-word band, where the score is essentially binary (0 or ~100).
- `craft_check_sections` takes the **worst** section score: one 1-word section ("Ngắn.") drags an otherwise 100-point draft to **0** and marks the section failing. For the gate's rewrite list this is arguably right (the section must be written), but as a *quality score* a single stub section annihilates the measurement of the whole draft.

### 3.8 Format dependence

Same text scored across formats differs *only* by the sensory −10 (blog/product 75 vs social/email/comparison 85 on ladder L3 text). No other component is format-aware: email and social prose are judged by the same rhythm/opening rules as blog prose, despite very different genre norms (short paragraphs, direct address, enumerated benefits). The format parameter is nearly cosmetic outside the sensory toggle.

---

## 4. Retrieval audit (`retrieve_cards`, cap 3, ≤1 core)

### 4.1 What works

- Intent-exact + keyword hits rank sensibly in the canonical cases (`blog/sensory`, `blog/editing`, `blog/structure`, `email/closing` all return the expected cards).
- Dormancy holds for truly unrelated input (only the core card returns).
- Format gating works as documented (e.g. `logical-transitions` never leaks into email *when a real format is passed*).

### 4.2 Concrete misfires

| # | Call | Returned | Problem |
|---|---|---|---|
| R1 | `product`, intent `product`, text "giá vị hương mô tả sản phẩm" | core, `show-dont-tell-sensory`, `fact-before-claim` | **`domain-vocabulary` hidden by the cap.** It ties at top strength (intent + keyword hit) with the two returned cards and loses only on library order. The most domain-specific card for a product-description task is exactly the one dropped. The core card permanently consumes 1 of 3 slots, so only 2 triggered cards ever surface. |
| R2 | `blog`, intent `drafting`, text "nhịp câu dài câu ngắn cấu trúc đoạn dàn ý câu bị động" | core, `vary-rhythm`, `one-idea-per-paragraph` | **`active-voice` crowded out** despite an explicit keyword ("câu bị động") and matching intent. |
| R3 | `product`, intent `rewriting`, text "mô tả vị hương sửa gọn câu bị động chủ động" | core, `strong-verbs`, `cut-filler` | Same cap loss: `active-voice` (intent + 2 keyword hits) dropped. In both R2/R3 the loser is decided by list position, not relevance. |
| R4 | `blog`, intent `ending`, text "…đánh **giá vị** trí quán và giá thuê mặt bằng" | core, `concrete-ending`, **`strong-verbs`** | **Substring keyword misfire.** `strong-verbs` fires because its keyword "vị" is a substring of "**vị** trí" (position) — the text is about shop location and rent, not taste. It takes the last slot; `fact-before-claim` (fired by "giá" inside "đánh **giá**" = *evaluate*) is equally irrelevant and only loses the tie-break. Keyword matching is raw substring with no word boundaries, and Vietnamese is full of such collisions ("vị", "giá", "pha", "hạt", "hương"). |
| R5 | `blog`, intent `sensory`, text "hạt điều rang muối giòn thơm ăn vặt" (cashew snack) | core, `show-dont-tell-sensory`, `domain-vocabulary` | **Domain misfire.** Coffee cards trigger for a cashew text via the keyword "hạt" (bean/seed). `domain-vocabulary` then instructs the writer to use *coffee* glossary terms ("chua sáng, hậu cacao, body dày") for a non-coffee product. Retrieval has no notion that the library itself is coffee-specific. |
| R6 | `blog`, intent `zzz` (unrelated), text "giá" | core, `fact-before-claim` | **Keyword alone breaks dormancy under any intent.** A single common syllable in the draft text wakes a card the task never asked for. |
| R7 | intent `hook` / `conclusion` / `sales` (natural task words), any format | core only | **Intent vocabulary is a closed code list.** Synonyms a real user/LLM would naturally pass return nothing. Same for Vietnamese intents passed as the intent parameter: `mở bài`, `kết bài` → core only (they exist only as *keywords*, i.e. they must appear in `text`, not in `intent`). One code, `ket-bai`, is in the list — an inconsistent mix of English codes and one unaccented Vietnamese slug. |
| R8 | `product` or `comparison`, intent `ending`/`closing`/`ket-bai` | core only | **No ending card exists for product/comparison** (`concrete-ending` formats: blog/social/email). A product page's closing section — where the CTA lives — can never receive ending guidance. |
| R9 | `social`, intent `sensory` | core, `strong-verbs` only | Both sensory cards (`show-dont-tell-sensory`, `domain-vocabulary`) format-exclude social, although social posts are exactly where sensory hooks are used (the bank's own social exemplar is sensory-led). |
| R10 | `email`, intent `comparison`, text "so sánh đối thủ khác nhau" | core only | `logical-transitions` and `fact-before-claim` both format-exclude email, so a comparison *email* gets no comparison help at all. Similarly `structure` intent returns core-only for product and social (`one-idea-per-paragraph` excludes them). |
| R11 | `fmt=""`, intent `comparison`, text "so sánh đối thủ" | core, `logical-transitions`, `fact-before-claim` | Empty format disables format filtering (documented behaviour), so format-restricted cards leak to callers of unknown format. Any caller that forgets `fmt` silently gets cross-format advice. |
| R12 | Ranking ties generally | — | Strength is coarse (intent+keyword 4 / either 2 / format-only 1 — and the format-only tier is **dead code**: every non-core card declares intents or keywords, so strength-1 can never occur). All real contests are ties broken by library order, i.e. by the order cards happen to sit in the JSON file. There is no secondary signal (keyword count, intent specificity, format exactness). |

### 4.3 Coverage matrix (intent sweep, empty text)

Every (format × intent) cell returns the core card plus 0–2 cards. Notable dead zones (core only): sensory×{social, email, comparison}, structure×{product, social}, ending/closing/ket-bai×{product, comparison}, opening/intro×{email, comparison}, comparison/argument×{social, email}. The library's centre of gravity is blog; product — a *blocking* format in the gate — is comparatively underserved (no ending card, no structure card).

---

## 5. Exemplars & glossary data audit

### 5.1 Exemplars

- **Coverage: exactly 1 exemplar for each of 5 formats** — `blog_intro`, `product`, `social`, `email`, `comparison`. No variants (no blog body/ending anchor, no second voice per format). As few-shot anchors this is minimal but coherent; all 5 exemplars score **100** under `craft_check` (self-consistent — the bank passes its own checker).
- **The `blog` trap:** the blog exemplar is stored under format **`blog_intro`**, so `get_exemplars("blog")` returns **[]**. `assemble_context` special-cases this (`blog` → `blog_intro`), but the MCP `get_exemplars` tool and any direct caller asking for the canonical format name "blog" silently get nothing. The other four formats use their canonical names, so the inconsistency is invisible until someone asks.
- Exemplar lengths 60–120 words and 2–4 `why_it_works` notes each — consistent with the pinned tests.

### 5.2 Glossary internal consistency

- **No duplicate terms** across the 6 groups; 25 terms total (aroma 5, acidity 4, body 4, aftertaste 4, brewing 4, roast 4). No weak word appears inside another entry's `replace_with` text; no weak/sensory substring overlaps within the shipped file. Masking order (longest first) is correct for the one real overlap ("thơm ngon" ⊃ "ngon").
- **8 of 25 terms can never affect `craft_check` at all**: the `brewing` and `roast` groups are excluded from `sensory_groups`, so "pha phin", "V60", "xay vừa đủ", "bọt nở", "rang mẻ nhỏ", "ngày rang", "nâu hạt dẻ", "sổ rang" are invisible to the sensory check (they remain available as vocabulary via `glossary_group`, but a text rich in *process* detail earns no sensory credit — compounding §3.5's false negatives for process-led writing like roast diaries).
- **Sensory-term substring structure inflates metrics**: nested terms ("cacao" ⊂ "hậu cacao", "caramel" standalone) double-count in `sensory_terms_used` (§3.5). No term is *untriggerable* in the literal sense (all can appear in text), but single-syllable-adjacent terms like "crema"/"caramel"/"cacao" (loanwords) trigger on any mention, including non-sensory ones (§3.5 colour probe).
- **Generic glossary construction**: weak list ⊂ shipped list (§3.6); generic sensory list contains the high-frequency substring "thơm", making the generic sensory check close to non-discriminating for food/drink text. The generic replacement string is one boilerplate sentence for all 14 words (vs. the shipped list's per-word, genuinely instructive replacements) — the generic path gives writers far less actionable feedback.
- The shipped glossary is honestly labelled (`brand_id: moc-brew`, `simulated: true`); the craft library cards likewise carry paraphrase source notes and original examples, and the copyright rule is stated in the data. No unattributed copied text was found in the cards/exemplars (examples are Mộc Brew-specific originals).

---

## 6. Severity ranking

| Sev | Finding | Evidence |
|---|---|---|
| **S1 — High** | **Construct ceiling**: the score certifies *absence of 5 surface defects*, not prose quality. Sensory-stuffed word salad = 100; top of scale saturated (L5 ≡ L6 = 100); two of the library's own 12 techniques (active voice, one-idea-per-paragraph) are measured by *no* component (their good/bad example pairs both score 90). | §2.2–2.3 |
| **S2 — High** | **Repeated-openings artefact**: first-syllable counting fines topical Vietnamese prose near-constantly ("cà" merging coffee/tomato/aubergine; brand-name subjects; enumeration; anaphora), flat −10 from ×3 to ×14. Fired on 5/5 real published posts in calibration. | §3.4 |
| **S3 — High** | **Sensory check = glossary substring membership**: false negatives on genuinely sensory prose in other vocabulary (incl. the CNM trial about aroma itself), false positives on negated mentions and incidental strings ("màu caramel", spec-sheet "hạt rang"), metric double-counting ("hậu cacao" → 2 terms). | §3.5 |
| **S4 — Medium-High** | **Generic-glossary fix unreachable where it matters**: `gates.py` and `quality.py` never pass `brand_id`, so the blocking gate and the clarity axis still judge all brands with Mộc Brew's glossary; the generic weak list is anyway only "shipped minus ngon/thơm ngon". | §3.6 |
| **S5 — Medium** | **Retrieval cap + tie-breaking**: core card's permanent slot + cap 3 + library-order ties repeatedly hide the most relevant card (`domain-vocabulary` for product descriptions; `active-voice` for drafting/rewriting). | §4.2 R1–R3, R12 |
| **S6 — Medium** | **Substring keyword misfires** wake irrelevant cards ("vị"⊂"vị trí", "giá"⊂"đánh giá", "hạt" for cashews → coffee vocabulary advice) and can evict relevant ones from the cap. | §4.2 R4–R6 |
| **S7 — Medium** | **Intent/format dead zones**: closed English-code intent list (natural synonyms and Vietnamese intents return core-only); no ending card for product/comparison; no sensory cards for social; no comparison cards for email; no structure card for product/social. | §4.2 R7–R10, §4.3 |
| **S8 — Medium** | **Weak-word context blindness + cap**: negated/quoted/meta uses fined (a craft essay *against* "ngon" scores 57); −30 cap makes 5 hits ≡ 16 hits. | §3.1 |
| **S9 — Low-Medium** | **Monotone cliffs and genre blindness**: 3-sentence sections immune (stdev gate at ≥4); uniform instructional steps fined; flat −15 regardless of length/severity; format parameter changes almost nothing beyond the sensory toggle. | §3.3, §3.8 |
| **S10 — Low** | **Boundary/scale quirks**: 9-word fragment = 0 vs 11-word vacuity = 100; one stub section zeroes a whole draft's craft score via worst-section-wins; "ngày nay" filler false positive; `get_exemplars("blog")` = [] naming trap (mitigated inside `assemble_context` only); brewing/roast glossary groups invisible to the sensory check. | §3.7, §3.2, §5 |

---

## 7. Improvement suggestions (not applied)

1. **Openings**: count the first *two* tokens (or a segmented first word) instead of the first syllable-token; exempt a stoplist of topic/brand/enumeration openers per brand; scale the penalty with count (e.g. −2 per excess opening beyond 2, capped) instead of flat −10.
2. **Sensory**: require either ≥2 distinct sensory terms *or* one term in a depictive context; strip negated mentions (reuse the fact-checker's negation handling); de-duplicate nested terms in metrics (mask longest-first as weak words already do); consider crediting brewing/roast process terms for process-led formats.
3. **Weak words**: exempt quoted/meta mentions and negated occurrences (same negation machinery as fact_check); raise or remove the −30 cap for *ranking* purposes even if the gate keeps a cap; keep per-word replacements in the generic glossary instead of one boilerplate string.
4. **Wire `brand_id` through `gates.py` and `quality.py`** so the generic/per-brand glossary actually governs the blocking paths; until then, document that the gate is Mộc-Brew-calibrated.
5. **Retrieval**: match keywords on token boundaries (Vietnamese syllable tokens) rather than raw substrings; rank by keyword-hit count and intent specificity before library order; consider raising the cap to 4 or not counting the core card against it; add intent aliases (hook→opening, conclusion/cta→ending, sales→product, and Vietnamese intent strings); add a product/comparison ending card and a social sensory card, or widen those cards' format lists.
6. **Rhythm**: apply the monotone check proportionally (penalty scaled by how far stdev falls below threshold and by sentence count), lower the sentence-count gate to 3 with a smaller penalty, and exempt enumerated/step structures (sentences starting with step markers).
7. **Score semantics**: publish the score as a *defect index* ("craft defects found: n") alongside or instead of a 0–100 "quality" number, or add at least one positive component (concrete-detail density, sensory density) so the top of the scale can differentiate L5 from L6.
8. **Data fixes**: rename the blog exemplar's format to `blog` (keeping a `blog_intro` alias in `get_exemplars`); align card good examples with the checker (or annotate that examples are illustrative, not checker-calibrated) — especially `logical-transitions`, whose good example currently eats a weak-word fine for a negated "ngon".
9. **Too-short/sections**: in `craft_check_sections`, report the worst-section score *and* a length-weighted mean, so one stub section flags for rewrite without zeroing the draft-level quality signal.

---

## Appendix — key probe texts (for reproduction)

- **Word salad (score 100, blog):** "Cacao caramel khói nhẹ bay qua cửa sổ tím của trạm xe buýt hôm qua. Hậu cacao đậm. Một con mèo body dày ngồi đếm mười bảy đám mây chua sáng trên mái tôn nhà hàng xóm. Đất ẩm hát, crema tròn vị nhảy múa cùng sổ rang và những chiếc lá khô vô nghĩa rơi hoài không dừng lại được nữa."
- **"Cà" merging (score 75, social; openings 'cà' ×3):** "Cà phê phin cần nước nóng để bột nở đều trong phin nhôm. Cà chua chín đỏ nấu canh chua cá là món quen của bữa cơm nhà. Cà tím nướng mỡ hành ăn kèm cơm nóng cũng rất đưa cơm trưa. Ba loại cà khác nhau hoàn toàn nhưng đều có mặt trong bếp Việt."
- **Sensory false negative (score 90, `no_sensory_detail`, 0 glossary terms):** "Mở túi ra, mùi hoa nhài và mật ong rừng thoảng lên rất nhẹ nhàng trong phòng. Ngụm đầu chát nhẹ ở đầu lưỡi, rồi đắng dịu đi qua nhanh, vị mật ong ở lại lâu. Nước pha trong, màu hổ phách sáng, uống xong miệng sạch và thơm thoảng cả buổi."
- **Sensory false positive (score 100):** "Gói này tuyệt đối không có mùi cacao như mấy bài quảng cáo vẫn viết đâu. Mình mở túi ngửi kỹ ba lần, chỉ thấy mùi hạt cũ khô khốc và hơi mốc nhẹ. Ai mong chờ hương cacao thì nên cân nhắc kỹ trước khi bỏ tiền ra mua thử."
- **Weak cap equivalence:** 5-hit text and 16-hit text (full texts in `/tmp/r3_probes.py`) both score **70** (social).
- **Too-short cliff:** 9-word rich fragment → **0**; 11-word vacuous sentence → **100**.

*End of Round 3 report.*
