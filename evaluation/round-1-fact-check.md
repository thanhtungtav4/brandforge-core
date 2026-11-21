# Evaluation Round 1 — Fact Checker Deep Evaluation (Mutation Testing)

- **Scope:** `content_core/fact_check.py` + shared matcher in `content_core/linter.py`
  (`normalize_for_match`, `_phrase_regex`, `split_sentences`), against the real
  loaders (`load_facts()` → `data/facts_lorebook.json`, `load_brand()` →
  `data/brand_mocbrew.yaml`).
- **Mode:** READ-ONLY. No core code, tests, or data were modified. Corpus and
  harness lived in `/tmp/fact_eval.py`; raw results in `/tmp/fact_eval_results.json`.
- **Facts under test (Mộc Brew, simulated):** price 185.000đ per 250g pack;
  roast every Tuesday (thứ Ba); delivery 2-3 days; Arabica Cầu Đất +
  Robusta Đắk Lắk; pack weight 250g (checker also allows 1000g).
- **Commit evaluated:** 8215b8c (213 tests green at start).

## Method

Mutation testing with a hand-built labelled corpus of **205 cases**:

- **TRUE statements** (84): correct facts in every format the checker claims to
  support, plus true statements that merely *contain* fact-shaped tokens
  (shipping fees, bundle totals, serving counts, other weekdays for other
  activities, denied claims, competitor attributions). A true case is a
  **false flag** if `check_section` returns any blocking issue.
- **FALSE statements** (116): one fact mutated per case (wrong value, wrong
  pairing, wrong day…), then systematic surface mutations: format changes
  (185k, 185.000 VND, 185,000đ, ₫ prefix, spacing), diacritics dropped,
  punctuation attached, negation (true-negated vs false-negated), clause
  reordering, claims in quotes, multi-fact sentences with exactly one wrong
  fact, facts split across sentences. A false case is **caught** if an issue
  of the expected type fires, **caught-other** if only a different issue type
  fires, **missed** if no issue fires.
- **MARKED statements** (5): unsupported content carrying `[NEEDS SOURCE]` —
  correct behaviour is *flagged, not blocking*.

Every case was executed through the real `check_section(text, facts, brand)`.

## Headline numbers

| Metric | Value |
|---|---|
| Total cases | 205 (116 false / 84 true / 5 marked) |
| Recall, expected issue type | **79/116 = 68.1%** |
| Recall, any issue at all | 81/116 = 69.8% |
| Missed entirely | **35/116 = 30.2%** |
| False-flag rate on true statements | **23/84 = 27.4%** |
| Marked `[NEEDS SOURCE]` handled correctly | 4/5 (1 silently passed, see §9) |

The checker is strong on *canonical* forms (roast day 91.7%, multi-fact
sentences 87.5%) and weak exactly where real copy deviates from the canonical
template: natural delivery phrasings (46.7% recall), context around money
amounts, negation/attribution scope, and statistics that reuse fact numbers.

## Per-category results

| Category | Cases | False | Caught (exp. type) | Caught-other | Missed | Recall | True | False flags | FF rate |
|---|---|---|---|---|---|---|---|---|---|
| Money | 33 | 15 | 10 | 1 | 4 | 66.7% | 18 | 4 | 22.2% |
| Weight | 19 | 10 | 7 | 1 | 2 | 70.0% | 9 | 2 | 22.2% |
| Delivery | 26 | 15 | 7 | 0 | 8 | **46.7%** | 11 | 2 | 18.2% |
| Roast day | 21 | 12 | 11 | 0 | 1 | **91.7%** | 9 | 4 | **44.4%** |
| Origin pairing | 22 | 14 | 9 | 0 | 5 | 64.3% | 8 | 2 | 25.0% |
| Prohibited / evidence claims | 30 | 18 | 13 | 0 | 5 | 72.2% | 10 | 5 | **50.0%** |
| Unsupported statistics (digits/%) | 24 | 12 | 8 | 0 | 4 | 66.7% | 10 | 4 | 40.0% |
| Word-number statistics | 19 | 12 | 7 | 0 | 5 | 58.3% | 6 | 0 | 0.0% |
| Multi-fact / boundary | 11 | 8 | 7 | 0 | 1 | 87.5% | 3 | 0 | 0.0% |
| **Total** | **205** | **116** | **79** | **2** | **35** | **68.1%** | **84** | **23** | **27.4%** |

---

## 1. Money — recall 66.7%, FF 22.2%

All canonical formats pass clean (185.000đ, 185.000 đ, 185.000đ/gói,
185.000 VND, 185.000 vnđ, 185.000 đồng, 185000đ, 185k, 185 nghìn, 185 ngàn,
185,000đ, 185.000₫, parenthesised) and plain wrong prices in those formats
are caught. The failures are contextual and format-edge failures.

**Missed (false, no issue):**

- `Giá một gói là ₫200.000.` — prefix ₫: `find_money_amounts` returns `[]`;
  every money pattern requires the currency marker *after* the digits.
- `Một gói giá 200.000.` — bare grouped number with no currency marker is
  invisible.
- `Giá gói hạt là hai trăm nghìn đồng.` — spelled-out money (documented
  limitation; confirmed still open).
- `Một gói giá 200.000 USD.` — foreign-currency amounts are ignored entirely.

**Caught-other:**

- `Giá gói là 185.000đ cho 500g, rẻ hơn hẳn khi tính theo gram.` — the false
  per-weight price claim is not caught as `wrong_price` (185.000 equals the
  fact); it only trips `unknown_weight` on 500g.

**False flags (true, flagged `wrong_price`):** the checker requires *every*
amount in the text to equal the single-pack price, so any other legitimate
amount blocks the draft:

- `Phí ship cố định 30.000đ mỗi đơn.` → `30.000đ = 30000đ != fact 185.000đ`
- `Hai gói có tổng 370.000đ.` → `370.000đ … != fact`
- `Giảm 20.000đ cho đơn đầu tiên.` → `20.000đ … != fact`
- `Giá cũ 220.000đ đã được hạ xuống 185.000đ.` → `220.000đ … != fact`
- Probe: `Một cân hạt giá 740.000đ.` (true 1kg bulk price = 4 × 185.000đ)
  → `wrong_price`.

## 2. Weight — recall 70.0%, FF 22.2%

**Missed:**

- `Gói 300gr rang sẵn.` — `gr` unit not recognised (`\b` after `g` fails).
- `Một gói nửa ký cho cả tuần.` — word/idiom weight (500g) not parsed.

**Caught-other:**

- `Gói hạt 0,3kg tiện lợi.` — decimal-kg weights are not parsed as weight;
  the text only trips `unsupported_number` on `0,3kg` (the statistics regex
  treats `kg` as a countable unit).

**False flags:**

- `Một gói 1kg cho người uống hằng ngày.` → `unsupported_number` on `1kg`:
  the weight checker ignores kg forms, but the *statistics* checker flags
  `1kg` because `kg` is in `_STAT_UNITS` and token `1` is not exempt.
- `Có gói 500g cho người uống nhiều.` → `unknown_weight` on `500g`.
  ⚠️ Corpus-labelling note: the evaluation brief listed 500g among the
  brand's weights, but `facts_lorebook.json` defines only the 250g pack and
  the checker allows {250, 1000}. Under the facts file as shipped this flag
  is "correct"; under the brief it is a false flag. The discrepancy between
  brief and facts file is itself worth resolving.

**Boundary probe:** `Gói 250,5g đóng sẵn.` → `unknown_weight` with detail
`5g` — the regex matches the decimal tail `5g`, misreporting the weight.

## 3. Delivery — recall 46.7% (weakest category), FF 18.2%

Canonical ranges (`2-3`, `2–3`, `2—3`, `2 - 3 ngày`) and verb-anchored single
values (`giao/nhận hàng/ship/vận chuyển … trong N ngày`) work. Almost every
other natural Vietnamese phrasing of a delivery promise evades all three
mechanisms (range regex, single-value regex, same-day string match):

**Missed (all false, no issue):**

- `Ship trong ngày là tới.` — same-day check only matches the strings
  `giao ngay trong ngay` / `giao trong ngay`; `ship` variant slips.
- `Giao hàng sau 5 ngày làm việc.` — `sau N ngày` (no `trong`) unmatched.
- `Hàng tới nơi trong vòng 5 ngày.` — no listed verb + `vòng` intervenes.
- `Giao trong vòng 5 ngày.` — `trong vòng N` breaks `\btrong\s+(\d+)`.
- `Bạn sẽ nhận được hàng sau 5 ngày.` — `nhận được hàng` ≠ `nhận hàng`, and
  `sau` phrasing.
- `Giao hỏa tốc trong 24 giờ.` — hour-based promises never checked.
- `Thời gian giao là 5 đến 7 ngày.` — `N đến M ngày` range form unmatched
  (also means the *true* `2 đến 3 ngày` form passes only vacuously).
- `Giao chậm nhất 6 ngày.` — `chậm nhất N ngày` unmatched.

**False flags:**

- `Mộc Brew không giao trong 5 ngày; thời gian giao luôn là 2-3 ngày.` →
  `wrong_delivery` on `giao trong 5 ngày`: `SINGLE_DELIVERY` has no negation
  handling, so an explicit denial of the 5-day figure is treated as a claim.
- `Hạt sau khi mở túi nên dùng trong 5-7 ngày để ngon nhất.` →
  `wrong_delivery` on `5-7 ngày` (the range regex is context-free: any
  `N-M ngày` anywhere is a delivery claim — this one is a use-by window),
  plus a bonus `unsupported_claim` on `ngon nhất`.

## 4. Roast day — recall 91.7% (best), FF 44.4% (worst precision)

Wrong days are caught in all formats, including diacritics-free
(`Rang vao thu tu hang tuan.`) and numeric (`thứ 5`). Precision is the
problem: the rule is "any weekday mentioned in any sentence containing the
substring `rang` is a roast-day claim", and the negation guard searches the
*entire prefix* before the weekday.

**Missed:**

- `Mộc Brew không rang vào thứ Ba mà rang vào thứ Năm.` — the false claim
  (thứ Năm) is *not* flagged: `_ROAST_NEGATED_RE` matches `khong rang`
  anywhere in the prefix, so the negation bleeds forward and suppresses the
  genuinely asserted second weekday.

**False flags (all true statements, all flagged `wrong_roast_day`):**

- `Rang vào thứ Ba, không phải thứ Tư.` → flags `thu tu`: post-posed negation
  (`không phải` *after* the weekday) is not honoured.
- `Vào thứ Tư, Mộc Brew không rang; ngày rang là thứ Ba.` → flags `thu tu`:
  negation after the weekday again.
- `Thứ Sáu là ngày giao hàng; hạt rang vào thứ Ba.` → flags `thu sau`: a
  weekday for a *different activity* in a sentence that also mentions
  roasting.
- `Bài viết đăng vào thứ Năm, còn hạt rang thứ Ba.` → flags `thu nam`: same
  activity-conflation.

**Boundary:** a roast day split across sentences evades by design of the
per-sentence scan — in the corpus this pattern was caught only because the
first sentence also contained `rang` (`Ngày rang là thứ Tư, đóng gói thứ
Sáu.`); a fully split claim (`Lịch rang cố định của Mộc Brew rất rõ. Ngày
rang đó là thứ Tư.` was in the roast corpus as a false case and *was*
caught via `rang` in sentence 2 — but the origin split in §5 shows the
general cross-sentence hole).

## 5. Origin pairing — recall 64.3%, FF 25.0%

Direct swaps in canonical order are caught, including diacritics-free, and
the negation-aware denial cases from the core-completion round pass clean
(`Robusta không lấy từ Cầu Đất…`, `Không phải Robusta Cầu Đất đâu…`), while
the affirmed `Robusta không chỉ lấy từ Cầu Đất mà còn là hạt chính.` is
correctly flagged.

**Missed:**

- `Arabica Dak Lak, Robusta Cau Dat.` — **both pairings swapped, neither
  flagged.** The "home region present in the same sentence" guard suppresses
  each bean's check because each bean's true home appears as the *other*
  bean's (wrong) partner. The guard cannot distinguish "both pairings
  listed correctly" from "both pairings swapped".
- `Hạt Arabica của Mộc Brew. Hạt này được trồng ở Đắk Lắk.` — pairing split
  across sentences; per-sentence checking never sees bean + region together.
- `Đắk Lắk là quê hương của hạt Arabica nhà Mộc Brew.` — region *before*
  bean: `_asserted_wrong_origin` only looks at regions following the bean.
- `Cầu Đất là vùng trồng Robusta của mình.` — same word-order hole.
- `Hạt Arabica nhập từ Ethiopia cho dòng giới hạn.` — origins outside the
  two pair regions and the 7-entry `_OTHER_REGIONS_NORM` list (all domestic)
  are invisible: no pairing match, and `nhập từ` is not a sourcing marker.

**False flags (both `unknown_origin`):** the sourcing-marker test is
substring-based and the brand's own name (`moc brew`) is itself a marker, so
*comparisons* and *competitor* origins are treated as the brand's source:

- `Khác với hạt Bảo Lộc trên thị trường, Mộc Brew dùng Arabica Cầu Đất.`
- `Hạt của đối thủ trồng tại Bảo Lộc, còn hạt của mình là Arabica Cầu Đất.`
  (marker `hạt của` fires on the competitor clause)

## 6. Prohibited / evidence claims — recall 72.2%, FF 50.0%

All list phrases are caught in canonical, diacritics-free
(`Ca phe chua mat ngu, tot nhat Viet Nam.`), punctuation-variant
(`tốt-nhất`, `100 % …`) and affirmed-negation (`Không chỉ tốt nhất…`) forms.
Quoted claims are also caught (`Khách khen đây là hạt ngon nhất họ từng
uống.` — counted as a correct catch: the claim still appears in the copy).

**Missed:**

- `Hạt này chữa được mất ngủ sau vài ngày.` — one inserted word (`được`)
  breaks the contiguous phrase `chữa mất ngủ`; no fuzzy matching.
- `Uống vào là hết mất ngủ, trị tận gốc chứng khó ngủ.` — full paraphrase of
  the health claim; no phrase matches.
- `Cà phê ngon số một hiện nay.` — `số một` (word form) is not `số 1`.
- `Đứng đầu Việt Nam về cà phê rang mộc.` — superlative synonym not in lists.
- `Hạt tốt. Nhất Việt Nam về độ tươi.` — phrase split across a sentence
  boundary (`tốt` / `Nhất Việt Nam` in different sentences).

**False flags (all `unsupported_claim`/`prohibited_claim` on true text):**
the negation whitelist (`không phải`, `chẳng phải`, `đâu phải`, `không
nhận`, `không dám nhận`) is much narrower than real denial/attribution
language:

- `Cà phê không tốt nhất đâu, uống thử rồi tự cảm nhận.` — bare `không`.
- `Bài này không khẳng định hạt ngon nhất, chỉ ghi giá và ngày rang.` —
  `không khẳng định` not a recognised negation.
- `Đối thủ tự nhận là tốt nhất Việt Nam, Mộc Brew không bình luận.` —
  competitor attribution flagged as the brand's own prohibited claim.
- `Khách hỏi có phải ngon nhất không, mình trả lời là không.` — question /
  reported speech flagged.
- `Mộc Brew chưa từng nhận là số 1.` — `chưa từng nhận` not recognised.

Correctly *not* flagged: `không phải là tốt nhất`, `không nhận là chữa mất
ngủ`, `chẳng phải đẳng cấp thế giới`, `đâu phải số 1`.

## 7. Unsupported statistics (digits / %) — recall 66.7%, FF 40.0%

**Missed — the fact-token exemption swallows real statistics.** Any
digit+unit whose number is in `_FACT_NUMBER_TOKENS = {"185", "250", "1000"}`
is skipped as "probably a fact", so the exact round numbers marketing copy
uses are exempt:

- `Đã có 250 khách hàng thân thiết.`
- `Hơn 185 khách hàng quay lại mỗi tháng.`
- `1000 khách hàng đầu tiên được giảm giá.`
- `Mỗi ngày có 5-7 khách hàng mới.` — the hyphen-range guard (meant for the
  delivery fact) exempts statistic ranges too.

**False flags:**

- `Một phin pha cho 2 người uống tại văn phòng.` → `unsupported_number` on
  `2 người` — bare `người` (and `khách`, `năm`) as stat units makes ordinary
  serving/recipe prose a "statistic".
- `Công thức này đủ cho 4 người.` → `4 người`
- `Nhóm 5 người chia nhau một gói vẫn đủ tuần.` → `5 người`
- `100% hạt Arabica nguyên chất trong gói.` → `unsupported_statistic`: the
  bare-percent rule flags *composition* percentages (blend %, ingredient %),
  not just performance statistics.

**Marked inconsistency:** `Tăng 20% doanh số [NEEDS SOURCE].` passes
*silently* — neither issue nor flagged entry. Marked digit statistics and
marked claims are recorded in `flagged_claims`; a marked percent is recorded
nowhere (the percent branch is skipped entirely when marked).

## 8. Word-number statistics — recall 58.3%, FF 0.0%

Precision is perfect (no ordinary prose flagged; lone word numbers like
`Một khách hàng hỏi…` correctly ignored), but the bounded scale grammar
misses common inflated forms:

**Missed:**

- `Hàng trăm nghìn khách hàng đã mua.` — chained scales (`trăm nghìn`) not
  in the grammar.
- `Cả nghìn khách hàng đang dùng.` — quantifier `cả` not recognised.
- `Hàng chục nghìn người dùng.` — `hàng chục` not recognised.
- `Sáu mươi khách hàng mỗi ngày.` — sub-scale word numbers (documented
  bound, confirmed).
- `Nghìn khách hàng tin tưởng.` — bare scale word with no leading
  digit/quantifier.

Caught correctly: `năm trăm`, `hai nghìn`, `mười nghìn`, `ba mươi nghìn`,
`một trăm`, `hai trăm năm mươi` + audience noun, and the marked variant is
flagged properly.

## 9. Multi-fact / boundary probes

- One-wrong-fact sentences are handled well: roast error inside a 4-fact
  sentence caught; two simultaneous errors (price + origin) both caught;
  a wrong price at the end of a ~90-word sentence caught.
- Mixed true formats in one sentence (`185.000đ, tức 185k, hay 185 nghìn
  đồng`) pass clean; dates (`21/11`, `30/11`) do not false-trigger.
- **Missed:** `Giá một gói là 200.\n000đ.` — a line break inside the grouped
  number defeats every money pattern (and the number-protection in the
  sentence splitter does not apply here since money matching is raw-text).
  By contrast `200.000\nđ` (break before the currency marker) and
  `Giao trong\n5 ngày` are caught — `\s` spans the break in those patterns.
- `Blend 70% Robusta Đắk Lắk và 30% Arabica Cầu Đất.` → one
  `unsupported_statistic` (percent rule, §7) on a true composition statement.
- `Giảm 50% hôm nay: giá còn 185.000đ.` → percent flagged while the price
  itself is correct; defensible as "needs source" but shows the percent rule
  cannot distinguish a promotion from a statistic.

## Severity ranking

| # | Severity | Defect | Evidence |
|---|---|---|---|
| D1 | **High** | Delivery recall 46.7%: `sau N ngày`, `trong vòng N ngày`, `N đến M ngày`, `chậm nhất N ngày`, hour-based and `ship trong ngày` promises all evade | 8/15 false missed (§3) |
| D2 | **High** | Money checker is context-blind: every amount must equal the single-pack price → fees, totals, discounts, old prices, bulk prices block true copy | 4/18 true flagged + bulk probe (§1) |
| D3 | **High** | Fact-token exemption (`185`/`250`/`1000`) exempts real statistics using those numbers; range guard also exempts stat ranges | 4/12 stats missed (§7) |
| D4 | **Medium** | Origin guard defeated by fully-swapped pairing in one sentence; region-before-bean order and cross-sentence pairings invisible | 4/14 origin missed (§5) |
| D5 | **Medium** | Roast-day rule conflates activities (any weekday in a `rang` sentence) and negation scope is the whole prefix: bleeds forward (miss) yet misses post-posed negation (false flags) | FF 44.4% + 1 miss (§4) |
| D6 | **Medium** | Claim negation/attribution whitelist too narrow: bare `không`, `không khẳng định`, `chưa từng nhận`, questions, competitor attribution all flag | FF 50.0% (§6) |
| D7 | **Medium** | Bare `N người` serving prose and `1kg` flagged as statistics; bare `%` flags composition percentages | 4/10 stats true flagged (§7, §2) |
| D8 | **Low** | Money format blind spots: prefix `₫…`, bare number without currency marker, foreign currency, newline inside a grouped number | 4 misses (§1, §9) |
| D9 | **Low** | Weight formats: decimal-kg unparsed (only caught as stat noise), `gr` and `nửa ký` missed, `250,5g` misreported as `5g` | §2 |
| D10 | **Low** | Word-number grammar gaps: chained scales, `cả nghìn`, `hàng chục nghìn`, bare `nghìn` | 5/12 missed (§8) |
| D11 | **Low** | Claim paraphrase/insertion gap: `chữa được mất ngủ`, `số một`, `đứng đầu`, cross-sentence splits | 5/18 missed (§6) |
| D12 | **Low** | Marked percent is silently dropped (not recorded as flagged), unlike marked stats/claims | §7 |
| D13 | **Info** | Brief vs facts-file discrepancy on 500g (brief lists it as a fact weight; facts file/checker allow only 250/1000) | §2 |

## Improvement suggestions (not applied — evaluation only)

1. **Delivery:** add pattern families for `sau N ngày`, `trong vòng N ngày`,
   `N đến M ngày`, `chậm nhất/trễ nhất N ngày`, hour-based promises
   (`trong 24 giờ` → 1 day), and verb-generalised same-day detection; add
   negation handling to `SINGLE_DELIVERY`; require a delivery-context verb
   for bare ranges so use-by windows (`dùng trong 5-7 ngày`) stop flagging.
2. **Money:** classify amounts by local context before comparing — amounts
   near fee/total/discount/old-price markers (`phí`, `tổng`, `giảm`, `giá
   cũ`, `tổng cộng`) or attached to a different weight (370.000đ/500g,
   740.000đ/1kg, i.e. price × pack multiple) should be checked for
   *consistency* (multiple of unit price) or skipped, not equality-checked.
   Add prefix-₫ and bare-number-with-price-context patterns.
3. **Statistics:** drop the blanket token exemption; instead exempt a
   number only when it appears in its fact *form* (e.g. `250` followed by
   `g`, `185` inside a money amount). Restrict the range guard to
   day-ranges. Require a "claim-ish" context for bare `người/khách` (e.g.
   preceded by quantifiers like `hơn`, `khoảng`, `đã có`, or followed by a
   verb of use/trust) so serving prose passes. Exclude composition
   percentages (percent directly modifying an ingredient/bean noun) from
   the bare-percent rule, or route them to flagged instead of blocking.
4. **Origin:** replace the home-present guard with per-bean clause analysis:
   for each bean mention, take the *nearest* region in the same clause; flag
   if it is the wrong one, regardless of other regions elsewhere in the
   sentence. Handle region-before-bean order symmetrically. Consider a
   cross-sentence pass for pronoun-linked continuations (`Hạt này…`).
   Extend the other-origin list beyond domestic regions (Ethiopia, Colombia,
   Brazil…) and treat `nhập từ`/`nguồn gốc từ` as sourcing markers; exclude
   comparison clauses (`khác với`, `đối thủ`) from `unknown_origin`.
5. **Roast day:** scope the check to the clause around each weekday (split
   on `;`, `,`, `còn`, `và` boundaries) and require the roasting verb in the
   same clause; make negation *local* (a window of a few tokens either side
   of the weekday, both pre- and post-posed) instead of a whole-prefix
   search.
6. **Claims:** widen the denial grammar (bare `không` + claim, `không
   khẳng định/công nhận`, `chưa từng/chưa bao giờ … nhận`, question form)
   and add attribution guards (`đối thủ`, `khách hỏi`, quoted speech
   attributed to others) routing to flagged rather than blocking. Add fuzzy
   phrase matching allowing 1 inserted word, word-form numerals (`số một`),
   and a small synonym set (`đứng đầu`, `vượt trội nhất`…) for the
   superlative family.
7. **Weight:** parse decimal-kg (`0,3kg` → 300g) and `gr`/`ký` forms;
   anchor the weight regex so decimal tails (`250,5g`) are reported whole.
   Remove `kg` from `_STAT_UNITS` or exempt weight-shaped tokens from the
   statistics scan.
8. **Word numbers:** extend the grammar with chained scales
   (`trăm nghìn`, `chục nghìn`), quantifiers (`cả`, `hơn`, `gần`,
   `khoảng`) and bare scale + unit; keep the sub-scale bound documented.
9. **Marked handling:** record marked percents in `flagged_claims` like
   every other marked category, for a consistent audit trail.
10. **Facts data:** resolve the 500g discrepancy — either add the 500g pack
    (with its price) to `facts_lorebook.json` or correct the brief; the
    checker can only be as right as the facts file.

## Caveats

- Corpus is hand-built by the evaluator around the documented fact set;
  frequencies are not a natural distribution of real drafts. Rates measure
  *behaviour on these mutations*, not production accuracy.
- "True" labels follow the facts file; the one contested case (500g) is
  flagged explicitly in §2/D13.
- Quoted third-party claims in copy were labelled FALSE (the claim still
  appears in the draft); if product policy later decides attributed quotes
  are acceptable, claims recall rises and §6's attribution false-flag count
  should be re-read in that light.
