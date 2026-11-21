# Calibration Report — Cao Nguyên Mộc (real site) vs the Content Core

- **Site:** https://caonguyenmoc.vn — family coffee roastery, Đức Cơ, Gia Lai (Robusta rang mộc + hạt điều; 55-post blog)
- **Date:** 2026-11-22 · **Run by:** Ember, authorised by Smith ("tự tìm 1 site rồi tự mô phỏng brand thử")
- **Scope:** analysis/testing only. Nothing published, nobody contacted, **no core code, threshold or Mộc Brew data changed** — recommendations only.
- **Method:** the project's own CLIs (`fetch_site.py`, `fetch_sample.py`) plus direct in-process calls of the exact functions the MCP tools wrap (`mcp_server.analyze_site_brand / save_brand_draft / analyze_style / save_style_profile`, `content_core.linter.lint_draft`, `craft.craft_check`, `style.style_match`, `fact_check.check_draft`, `quality.evaluate_quality`, `gates.evaluate_gate`, `brand_extract.draft_to_brand_yaml`). Core test suite re-run at the end: **188 passed** (unchanged).
- **Ground truth used for judging the tools:** the site's own embedded product data (`const PRODUCTS=[…]`, 65 products, extracted from the homepage script) and the brand-story post `/blog/cao-nguyen-moc-la-ai-xuong-rang-gia-dinh-duc-co/`.

---

## 0. The headline: the fetch layer is blind to this site's shop, and the fact layer is a single-brand trap

Two structural facts dominate everything below:

1. **The homepage/shop is a single-file JS app.** The homepage HTML is 2,893,056 bytes, of which **2,804,277 bytes (97%) sit inside 3 `<script>` blocks** — the entire catalogue (`PRODUCTS`, 65 items with prices) is a JS constant, and all visible copy is rendered client-side. The project's fetchers deliberately skip `<script>`, so **`fetch_site.py` returned exactly 1 page with 0 words of text and discovered 0 internal links**. The blog, by contrast, is server-rendered static HTML, and `fetch_sample.py` works on it perfectly (616–979 words/post). Every Brand Builder number below therefore comes from a **sitemap-assisted corpus** (URLs from the site's own `/sitemap.xml`, parsed with the project's own `fetch_site.parse_page`) — i.e. from the blog, not from the shop pages the tool was designed to read.
2. **The fact base is Mộc Brew's, so correct real-world content cannot pass.** One real post produced **32 fact issues** (23× `wrong_price`), and a writer-trial section written in Cao Nguyên Mộc's voice with Cao Nguyên Mộc's *true* price **failed the final gate** on `wrong_price: 89.000đ != fact 185.000đ`. This is the known single-brand hardcoding (P3), now quantified: gate pass rate for truthful content of any second brand = **0%**.

---

## 1. Brand Builder on the real site

### 1a. As shipped (`fetch_site.py https://caonguyenmoc.vn --max-pages 8`)

| Item | Result |
|---|---|
| Pages fetched | **1** (homepage only; 0 links discovered — all navigation is JS-rendered) |
| Corpus | **0 pages / 0 words** (`site_corpus` silently drops the empty-text page) |
| Fingerprint | all zeros |
| Brand kit | brand_name "Cao Nguyên Mộc" ✓ (og:site_name), og_image `https://caonguyenmoc.vn/og.jpg` ✓, theme_color `null` ✓ (the site genuinely has no theme-color meta), logo_url `null`, colors topped by `#ffffff`/`#000000` (real brand green `#1d6a43` only 4th), fonts include the junk family **`var(--f-head)`** |

The tool gives no hint *why* the corpus is empty (JS shell) — a user would conclude the site has no content.

### 1b. Sitemap-assisted corpus (9 pages fetched, 8 with text, **8,663 words**)

Homepage (kit evidence) + `/blog/` index + brand-story post + 6 blog posts, all parsed by `fetch_site.parse_page`.

**Brand kit assessment**

| Field | Extracted | Verdict |
|---|---|---|
| brand_name | "Cao Nguyên Mộc" | ✓ correct |
| og_image | `https://caonguyenmoc.vn/og.jpg` | ✓ correct |
| theme_color | `null` | ✓ correct (site has none) |
| logo_url | `https://caonguyenmoc.vn/img/b/cao-nguyen-moc-la-ai-xuong-rang-gia-dinh-duc-co-t.jpg` (from `/blog/`) | ✗ **wrong** — a blog-card thumbnail. The heuristic matched its alt text, which literally reads *"Logo Cao Nguyên Mộc với dòng chữ Mang Tây Nguyên Về Nhà"* — an article image *describing* a logo, not the logo asset |
| colors (top) | `#efe2c8` ×32, `#ffffff` ×26, `#f2b48f` ×24, `#141a10`/`#1d2418`/`#222b1c` ×16 | ✗ misleading — blog badge/template colours by raw frequency. The real palette is in CSS variables on the homepage (`--brand:#1d6a43`, `--gold:#d8962b`); `#1d6a43` appeared only in the set-A run, ranked 4th |
| fonts | `var(--display)` ×16, Lora ×10, Be Vietnam Pro ×9 | ~ half right — Lora + Be Vietnam Pro are the real fonts (Google Fonts link), but the top "family" is an unresolved CSS variable, also seen as `var(--f-head)`/`var(--f-ui)` in set A |

**Voice suggestions assessment**

| Suggestion | Value | Verdict |
|---|---|---|
| median_words_default | **[11, 17]** (measured median 14.0) | ✓ plausible — all 5 measured posts have medians 12–16 (see §3) |
| max_sentence_words | 25 (hard-coded default) | ✗ unrealistic for this brand — see §3c (28 violations on 5 clean posts) |
| pov | "third-person" (first-person rate 0.01–0.02/100w) | ✗ wrong in spirit — the brand speaks **first-person-by-name**: it calls itself **"Mộc"** and narrates as a person ("Mộc bán…", "Mộc muốn…", "nhắn Mộc xay mịn hơn"). Pronoun counting cannot see name-as-pronoun |
| reader_address | `null` | ✗ missed — the reader is addressed as **"bạn"** throughout the story post and product copy |

**Vocabulary assessment**

- `use` list is polluted by **bigram fragments** from unsegmented Vietnamese tokenisation: "phê rang" ×47, "nguyên mộc" ×28, "cao nguyên" ×28 (slices of "cà phê rang mộc" / "Cao Nguyên Mộc"). Real terms present: "cà phê" ×303, "hạt điều" ×70, "rang mộc" ×47, "gia lai" ×57.
- `exact` (casing) list is mostly good — "Cao Nguyên Mộc", "Gia Lai", "Tây Nguyên", "Chư Đang Ya", "Biển Hồ", "Minh Thành" — plus one artifact: **"W180 W240 W320"** (three cashew grades merged into one "term").
- **The actual product names are absent** ("Cà phê rang mộc nguyên hạt", "Hạt điều rang muối", …) because they live in the JS shop data the fetcher cannot see.

**Claims-for-review list: 5 claims, all with source URLs — and 5/5 are benign.**

| # | Claim (phrase matched) | Source | Reviewer verdict |
|---|---|---|---|
| 1 | "…ngon nhất trong [3–6 tuần]…" — storage table, text mangled by table flattening ("LoạiSau khi mở túi, ngon nhất trong------Cà phê nguyên hạtKhoảng 3–6 tuần…") | /blog/bao-quan-ca-phe-sau-khi-mo-tui/ | freshness window advice, not a product claim — **allow** |
| 2 | "Tốt nhất là dùng bã để khử mùi" | same post | usage tip — **allow** |
| 3 | "Cà phê xay ngon nhất trong vài tuần đầu sau khi mở" | /blog/cach-pha-ca-phe-phin-rang-moc/ | freshness advice — **allow** |
| 4 | "Cà phê rang xong bao lâu thì uống ngon nhất" | /blog/cac-muc-rang-ca-phe-rang-sang-vua-dam/ | a *question heading* — **allow** |
| 5 | "Phần lớn cà phê ngon nhất trong khoảng vài ngày đến vài tuần sau ngày rang" | same post | freshness statement — **allow** |

Precision on this site: **0/5**. The detector matches the *word* "nhất", not claim risk. Meanwhile the site's real risk surface is invisible to it: health-adjacent posts (người tiểu đường ăn hạt được không, giảm cân, táo bón…) make no superlative claims, so nothing is flagged; and the shop's marketing claims (e.g. "Không chất bảo quản (theo nhãn hàng công bố)") sit in JS data and were never fetched.

**Cross-brand contamination in the draft:** `build_brand_draft` silently copied **Mộc Brew's** `prohibited_claims` into Cao Nguyên Mộc's draft ("100% chữa mất ngủ", "tốt nhất việt nam", "đẳng cấp thế giới", "chữa mất ngủ", "trị bệnh"). A new brand's draft should start from its own evidence, not the house brand's prohibitions.

**Bonus real-world finding (the site's own data disagrees with itself):** the brand-story post lists hạt điều rang muối at **94.000đ / 169.000đ / 339.000đ** (250g/500g/1kg), while the live `PRODUCTS` data says **99.000đ / 199.000đ / 379.000đ**. Coffee prices agree everywhere (89/179/359k). A brand profile built from blog text alone — the only text this toolchain can reach — would encode **stale cashew prices as brand facts**.

**Draft saved:** id **`cao-nguyen-moc`** → `data/brand_drafts/cao-nguyen-moc.json` (review_status `draft`), with model-filled qualitative traits (xưng hô "Mộc"/"bạn", low-hype, price transparency, "Trả lời nhanh" structure).

**Human-fixing estimate for the draft:** substantive edits needed in 6 of 8 areas — pov (wrong), reader_address (missing), 1 of 3 examples (blog-index boilerplate, replaced in §4), brand_kit logo + colours + fonts (wrong/junk), prohibited_claims (imported from another brand), vocabulary artifacts. Roughly **30–45 minutes of reviewer work**; the deterministic skeleton (name, median band, claims list with URLs, review notes) is a genuine head start, but nothing except brand_name/og_image can be trusted without checking.

---

## 2. Style extraction on their real writing

Samples (via `fetch_sample.py`): 5 coffee blog posts, **4,081 words, 253 sentences** total.

**Aggregate fingerprint**

| Metric | Value | Per-post range |
|---|---|---|
| sentence median | **14.0** words | 12–16 |
| sentence stdev | **10.44** | 8.8–12.25 |
| short-sentence share | 0.05 | 0.04–0.06 |
| long-sentence share | 0.24 | 0.14–0.38 |
| first-person rate | 0.02 /100w | 0.0–0.1 |
| reader rate | 0.20 /100w | 0.0–0.32 |
| number density | 1.67 /100w | 0.51–3.73 |
| question rate | **22.92 /100 sentences** | 10.3–30.9 (question-form headings are a house device) |
| exclamation rate | **0.0** | 0.0 everywhere |
| type-token ratio | 0.16 (aggregate) | 0.32–0.42 per post |
| top transitions | nhưng ×8, nhất là ×4, vì vậy ×2 | sparse — prose is paratactic |

**Warning issued — and it's a false positive:** *"samples diverge on first_person_rate (CV 2.0 > 0.35) — possibly mixed authors"*. The five rates are **[0.0, 0.0, 0.0, 0.0, 0.1]** — near-identical; the coefficient of variation explodes because the mean (≈0.02) is near zero. CV-based divergence is meaningless for low-rate metrics and would train users to ignore the warning that actually matters (mixed authors).

**Profile saved:** id **`cao-nguyen-moc-blog-voice-dd5678`** → `data/style_profiles/cao-nguyen-moc-blog-voice-dd5678.json`, `store_excerpts=False` (0 exemplars stored, fingerprint + distilled traits only), brand_id `cao-nguyen-moc`.

---

## 3. Self-consistency checks (each post vs the core's own bars)

### 3a. craft_check — does real published human copy clear the craft bar of 70?

| Post | Craft score | Verdict vs 70 | Points lost to |
|---|---|---|---|
| ca-phe-rang-moc-la-gi | **84** | pass | weak_word "ngon" ×1, repeated_openings |
| bao-quan-ca-phe-sau-khi-mo-tui | **74** | pass | "ngon" ×1, repeated_openings, no_sensory_detail |
| cach-pha-ca-phe-phin-rang-moc | **72** | pass | "ngon" ×3, repeated_openings |
| cac-muc-rang-ca-phe-rang-sang-vua-dam | **66** | **FAIL** | "ngon" ×4, repeated_openings |
| ca-phe-robusta-va-arabica-khac-nhau-the-nao | **78** | pass | "ngon" ×2, repeated_openings |

Mean **74.8**; **1 of 5 real, published, perfectly serviceable posts fails the craft bar.** The deductions are dominated by two Mộc Brew artefacts, not by writing quality:

- **weak_word "ngon"** (11 hits across 5 posts): the craft glossary is literally Mộc Brew's file — `data/domain_glossary.json` carries `"brand_id": "moc-brew"`, `"simulated": true`, and lists "ngon" as weak word #1 ("say a specific flavour instead"). For Cao Nguyên Mộc, "ngon" is ordinary vocabulary. The craft layer applies one brand's glossary to every brand.
- **repeated_openings**: counts sentence-initial tokens; because Vietnamese "cà phê" tokenises as "cà"+"phê", every sentence starting "Cà phê…" counts as opening "cà" (×14 in one post). Single-topic how-to prose naturally starts sentences with its topic noun — the checker cries wolf.
- **no_sensory_detail** fired on the storage post, a text entirely about preserving *mùi thơm* (aroma) — the glossary's sensory term list (Arabica-oriented: "chua sáng, hậu cacao…") doesn't match this brand's sensory vocabulary.

### 3b. style_match — each post vs the profile extracted from the same 5 posts

Scores: **92, 100, 92, 100, 92** (mean 95.2). Self-consistency is high, as it should be; tolerances on median/stdev/shares behave sensibly at post length (~600–1,000 words). This part of the core works on real data.

### 3c. brand_check (linter) with the converted Cao Nguyên Mộc brand (§4)

| Post | Brand score | Violations | Types |
|---|---|---|---|
| ca-phe-rang-moc-la-gi | 92 | 4 | all `sentence_too_long` |
| bao-quan-ca-phe-sau-khi-mo-tui | 88 | 6 | all `sentence_too_long` |
| cach-pha-ca-phe-phin-rang-moc | 90 | 5 | all `sentence_too_long` |
| cac-muc-rang-ca-phe-rang-sang-vua-dam | 88 | 6 | all `sentence_too_long` |
| ca-phe-robusta-va-arabica-khac-nhau-the-nao | 86 | 7 | all `sentence_too_long` |

**28 violations, 100% one type.** Zero banned terms, zero median violations — the derived median band [11,17] fits the brand (post medians 12–16). But `max_sentence_words = 25` is violated by real copy constantly. Inspecting the longest "sentences" shows why — they are a three-way mix:

1. **Page furniture**: in 4 of 5 posts the single longest "sentence" (49–69 words) is the unpunctuated header block (site name + breadcrumb + title + byline) glued to the first paragraph by the extractor.
2. **Flattened tables**: e.g. the Robusta/Arabica comparison table becomes one 69-word run ("RobustaArabica---------Tên khoa họcCoffea canephora…"); the storage table a 42-word run.
3. **Genuine prose**: after stripping the header block, **26 long sentences remain** across 5 posts; removing tables (~3) and footer furniture (~4) leaves **~19 real sentences of 26–39 words** — comma-chained explanatory Vietnamese that this brand writes routinely (long-sentence share 0.14–0.38). A 25-word hard cap is simply stricter than this brand's real style.

Sensitivity check (header stripped): brand scores 92/88/92/90/86 — the furniture inflates counts but the conclusion stands on genuine prose alone.

---

## 4. Converting the draft (acting as the human reviewer)

Edits made to draft `cao-nguyen-moc` before `draft_to_brand_yaml`:

1. **Example 1 replaced** — the extractor's first "example" was blog-index category boilerplate; replaced with the bao-quan post's own "Trả lời nhanh" passage (own text, source URL kept in the draft).
2. **pov corrected** → "brand-as-person: tự xưng 'Mộc', gọi người đọc là 'bạn'"; **reader_address** `null` → "bạn"; formatting_rules filled ("Trả lời nhanh" opening, question-form headings, no exclamation in blog).
3. **Claims decided item by item** (table in §1b): all 5 are freshness/usage superlatives → **none prohibited**. Bare "tốt nhất/ngon nhất" deliberately **not** banned — they are this brand's freshness vocabulary, and banning them would flag its own published posts (§3c confirms the posts use them benignly).
4. **prohibited_claims replaced** — the inherited Mộc Brew list removed; set from this brand's own risk surface (food + health-adjacent content): `chữa mất ngủ, trị bệnh, chữa tiểu đường, đẳng cấp thế giới, tốt nhất việt nam`.
5. **banned_terms set conservatively**: `ngon nhất việt nam, tốt nhất việt nam, số 1, đẳng cấp thế giới, thần dược` (product-superiority compounds only).
6. **Vocabulary artifact fixed**: "W180 W240 W320" → "W240", "W320".

**Conversion result:** succeeded, no refusal (own-site examples carry no `reference_only` flag). Output saved to **`calibration/caonguyenmoc-brand.yaml`** (brand id `cao-nguyen-moc`, `simulated: false`).

**Conversion friction (calibration data):**
- `draft_to_brand_yaml` **strips every example's `source_url`** — the approved brand YAML loses the provenance of its own approved examples.
- It passes `brand_kit` through **unvalidated** — the wrong logo_url and `var(--display)` "font" are now baked into the "approved" brand file. A reviewer who only fixes voice/claims ships a kit that is wrong.
- The claims decisions exist nowhere in the output (review list and notes are dropped by design) — the *reasoning* of the review evaporates unless recorded externally (as done in this report).

---

## 5. Fact layer reality check (Mộc Brew facts vs a real Cao Nguyên Mộc post)

Input: the brand-story post (the site's most price-dense page: full price table, discount code, shipping policy). Checker: `fact_check.check_draft` with Mộc Brew's facts (185.000đ/250g, rang thứ Ba, Cầu Đất/Đắk Lắk, giao 2–3 ngày) and Mộc Brew's brand.

**Result: 32 unflagged issues, 0 flagged, section failing.**

| Type | Count | Examples |
|---|---|---|
| `wrong_price` | **23** | 89.000đ ×4, 94.000đ ×4, 119.000đ ×3, 179.000đ ×2, 359.000đ ×2, 169.000đ ×2, 339.000đ ×2 — every real price on the page, plus policy amounts (20.000đ discount, 299.000đ threshold, 99.000đ/15.000đ/500.000đ shipping tiers) — all "wrong" because ≠ 185.000đ |
| `unknown_weight` | 5 | 500g ×4, 12g ×1 (Mộc Brew only sells 250g/1kg) |
| `unsupported_number` | 4 | **"1kg"** — a pack size read as an unsupported statistic |

Also instructive — what did **not** fire: "Robusta Đức Cơ" produced no `wrong_origin` (Đức Cơ is not in the pairing table at all; unknown regions pass silently), and "đổi trả 7 ngày" produced no delivery issue. The checker is simultaneously maximally noisy on prices (it even re-reports the same amount once per occurrence — 89.000đ four times) and blind to origin statements outside its two hard-coded pairings.

**Interpretation:** the money/weight *normalisation* machinery is excellent — it recognised every Vietnamese price format on the page (89.000đ, 179.000đ, comma/dot variants) with zero misses. What fails is the *reference data*: a single global fact base. Per-brand fact bases (P3) turn this from 32 false alarms into a genuinely useful check — with CNM's own facts, this same run would have caught the blog-vs-shop cashew price discrepancy in §1b.

---

## 6. Writer trial (one section, Cao Nguyên Mộc voice, their facts only)

**Topic:** cách bảo quản cà phê rang mộc tại nhà. **Text:** 149 words, 8 sentences (lengths 14–23, median 18) — saved at `calibration/work/writer_trial.txt`. Facts used, all from their bao-quan post: van một chiều/hũ kín, nơi khô mát tối, không cất tủ lạnh, nguyên hạt 3–6 tuần / rang xay 2–4 tuần sau mở túi, túi 250g = 89.000đ. One inference ("đủ pha phin mỗi sáng trong khoảng hai tuần") marked `[NEEDS SOURCE]`.

| Check | Result | Reading |
|---|---|---|
| brand_check (CNM brand) | **90**, 1 violation: median 18.0 vs target 11–17 | **Fair catch.** My sentences were uniformly mid-length; real posts mix short fragments/headings with long explanations (medians 12–16). The derived band did its job |
| craft_check | **90**, 1 violation: `no_sensory_detail` | **Cried wolf.** The section is *about* aroma preservation ("mùi thơm giữ được lâu hơn") but contains no term from Mộc Brew's sensory glossary |
| style_match (CNM profile) | **69** | Mixed. Fair: sentence **stdev 3.2 vs profile 10.44** — my prose lacks their burstiness; that is a real style miss. Unfair/unstable at 149 words: reader_rate 1.34 vs 0.20 ("bạn" ×2 in a short section — density metrics swing wildly on short texts) and number_density 2.68 vs 1.67 (four numbers in 149 words). Note the inconsistency: style_match's median tolerance *passed* 18 vs 14 while the brand linter *failed* the same median against [11,17] |
| quality_score (blog preset; judged: coherence 2, persuasion 1, depth 1 — honest grades for one short practical section) | **total 82.43, quality_pass = True**, no floor failed | Facts axis landed **exactly on its floor: 70.0** — one unflagged issue (the *true* 89.000đ price vs Mộc Brew's fact) cost 30 points. The `[NEEDS SOURCE]` sentence registered **0 flagged claims**: the sentence contains no digit/claim-phrase the checker recognises, so the marker had nothing to attach to |
| final_gate (CNM brand + Mộc Brew facts) | **FAIL** | Hard layer blocks on the single `wrong_price`. Brand 90 ✓, craft 90 ✓ — irrelevant. A truthful, on-voice section for a real brand **cannot pass the shipped gate** until fact bases are per-brand |

**Where the checkers helped:** the median catch and the stdev/burstiness signal are real, actionable writing feedback — exactly what the core is for. **Where they cried wolf:** sensory glossary (wrong brand's vocabulary), wrong_price (wrong brand's facts), density metrics on a 149-word sample.

---

## 7. Core findings (bugs / design issues — documented with repro, **not fixed**)

| # | Finding | Repro / evidence |
|---|---|---|
| F1 | **Fetch layer is blind to JS-data-driven sites, silently.** No JS-shell detection, no warning, no sitemap fallback; link discovery returns 0 links | `fetch_site.py https://caonguyenmoc.vn` → 1 page, 0 words, 0 links, from a 2.9 MB HTML whose content lives in `<script>` data |
| F2 | **Style divergence warning false-positives near zero.** CV on first_person_rate [0,0,0,0,0.1] = 2.0 → "possibly mixed authors" | `analyze_style` on the 5 posts in §2 |
| F3 | **Brand drafts inherit the house brand's prohibited_claims** (`build_brand_draft` copies `load_brand()` defaults) | CNM draft arrived with Mộc Brew's "100% chữa mất ngủ" etc. |
| F4 | **Logo heuristic traps on alt text** — article images whose alt *mentions* "Logo…" win over any real logo | §1b logo_url = blog thumbnail |
| F5 | **Font extraction returns CSS variables as families** (`var(--display)`, `var(--f-head)`) and ranks them first | §1a/§1b brand kits |
| F6 | **Page furniture and flattened tables contaminate sentence statistics** — header blocks and tables become 42–69-word "sentences", polluting median/max checks and fingerprints for *every* server-rendered page with tables | §3c longest-sentence inspection |
| F7 | **fact_check noise amplification**: same amount re-reported per occurrence (89.000đ ×4 in one post); pack size "1kg" classified as `unsupported_number` statistic | §5 |
| F8 | **craft_check's glossary is brand-bound but applied universally** (`domain_glossary.json`: brand_id moc-brew) — weak words and sensory terms of one brand judge all brands | §3a ("ngon" ×11), §6 (sensory false negative) |
| F9 | **Conversion loses provenance & validates nothing**: `draft_to_brand_yaml` strips example `source_url`s and passes a wrong brand_kit into the approved YAML | §4 |
| F10 | **Claim detection = superlative-word matching**: 5/5 false alarms on freshness prose; health/marketing claims without "nhất" invisible; shop claims unreachable (compounds F1) | §1b claims table |

---

## 8. Recalibration recommendations (Smith approves any change — none applied)

| # | Current rule | Recommendation | Evidence |
|---|---|---|---|
| R1 | Craft bar **70** (blocking for blog/product) | **KEEP the bar; ADJUST its inputs first** — make weak-word/sensory lists per-brand (or brand-neutral) before trusting the bar on a second brand | Real posts score 66–84 (mean 74.8): the bar sits *inside* the real distribution, so 70 is a sane line — but 11 of the deductions were one Mộc Brew glossary word ("ngon") and the single sub-70 post (66) got there on glossary + tokenisation artefacts, not on bad writing |
| R2 | Sentence-median approach (per-format band) | **KEEP — and prefer the Brand Builder's derived band over fixed defaults.** Mộc Brew's fixed blog band [11,14] would have failed 3 of 5 real posts (medians 15, 14, 16…); CNM's derived [11,17] failed **0 of 5** on median | §1b, §3c |
| R3 | Max sentence **25 words** (hard default in every brand) | **ADJUST** — derive per brand like the median (this brand's real tail runs to ~39 words of genuine prose; long-share 0.14–0.38), and exclude tables/furniture from sentence stats (F6). At 25 the rule produced 28/28 of all brand violations on clean published copy → alarm fatigue teaches writers to ignore the linter | §3c |
| R4 | No exclamation marks in blog | **KEEP** — matches reality exactly: 0.0 exclamations per 100 sentences across 8,663 words of this brand's copy | §1b, §2 |
| R5 | Quality floors (facts 70 / voice 60 / clarity 60 / coherence 60 / seo 55 / persuasion 55 / depth 60) | **KEEP the values; the binding constraint is elsewhere.** With a wrong fact base, the facts axis pins at exactly 70 for one "issue" and the hard gate fails outright — no floor tuning fixes a wrong reference base. Priority is per-brand fact bases (already-known P3), then re-measure floors on real drafts | §5, §6 |
| R6 | Style fingerprint tolerances | **ADJUST three things:** (a) divergence check — use absolute spread, not CV, for metrics whose mean is near zero (F2); (b) density metrics (reader/number per 100w) need a minimum text length or wider tolerance — at 149 words they swung 3–7× off profile on honest writing; (c) keep the stdev tolerance as-is — it caught a real burstiness miss (3.2 vs 10.44) | §2, §6 |
| R7 | Fetch layer (new) | **ADJUST (highest leverage after per-brand facts):** detect JS-shell pages (HTML large, extracted text ≈ 0, content in script data) and say so; fall back to `/sitemap.xml` discovery; strip header/byline/footer furniture and mark table runs as non-sentences before statistics | F1, F6; the blog corpus was only reachable via the sitemap |
| R8 | Brand Builder details | **ADJUST:** stop inheriting house prohibited_claims (F3); drop or resolve `var(--…)` font entries (F5); rank colours by CSS-variable definitions (`--brand:`) before raw frequency; tighten the logo heuristic (F4); Vietnamese word segmentation before bigram vocabulary (kills "phê rang"-type fragments); keep example `source_url`s through conversion (F9) | §1, §4 |

**Net assessment:** the core's *measurement machinery* (money normalisation, median derivation, style self-consistency 92–100, exclamation rule, gate plumbing) held up well on real data. Everything that failed traces to two root causes — **single-brand reference data** (facts, glossary) and **fetch/extraction assumptions** (server-rendered, furniture-free HTML). Both are known-direction fixes (P3 multi-brand; fetch hardening), and neither requires touching the Smith-approved thresholds themselves, with one exception: the 25-word max-sentence default (R3) should become derived, not fixed.

---

## Appendix — artefacts saved by this run

| Artefact | Path / id |
|---|---|
| Brand draft (status: draft) | id `cao-nguyen-moc` → `data/brand_drafts/cao-nguyen-moc.json` |
| Style profile (excerpts NOT stored) | id `cao-nguyen-moc-blog-voice-dd5678` → `data/style_profiles/cao-nguyen-moc-blog-voice-dd5678.json` |
| Converted brand YAML | `calibration/caonguyenmoc-brand.yaml` |
| This report | `calibration/caonguyenmoc-report.md` |
| Working evidence (fetched pages, PRODUCTS ground truth, per-check JSON) | `calibration/work/` (`site.json`, `pages_setB.json`, `products.json`, `samples.json`, `analysis_setB.json`, `self_consistency*.json`, `factcheck_mocbrew_on_cnm.json`, `writer_trial.txt`, `writer_trial_results.json`) |

Mộc Brew data files were not modified; the core test suite still passes 188/188 after the run.

---

## Post-fix re-check (calibration fix round, 2026-11-22)

Smith approved the ADJUST recommendations; the fixes were implemented with
fail-first tests (`tests/test_calibration_fixes.py`, 25 tests; suite now
213). The per-brand FACT separation stayed in P3 and was NOT done — every
fact-layer number below is therefore unchanged, by design. Re-run artefacts:
`calibration/work/recheck_after_fixes.py` → `recheck_after_fixes.json`
(same saved corpus as the original run).

### Fetch (F1) — live re-run of the exact command that failed before

| | Before | After |
|---|---|---|
| `fetch_site.py https://caonguyenmoc.vn --max-pages 8` | 1 page, **0 words**, 0 links, no warning | **8 pages**, homepage flagged `js_shell_suspected: true`, `sitemap_fallback_used: true`; blog index (3,662 words) + 6 posts (719–809 words each) crawled via the sitemap |

JSON-LD text extraction is in; plain `<script>` data is still skipped.

### Craft (F8) — generic glossary (`brand_id="cao-nguyen-moc"`), same 5 posts

| Post | Before (Mộc Brew glossary) | After (generic glossary) |
|---|---|---|
| ca-phe-rang-moc-la-gi | 84 | **90** |
| bao-quan-ca-phe-sau-khi-mo-tui | 74 | **90** |
| cach-pha-ca-phe-phin-rang-moc | 72 | **90** |
| cac-muc-rang-ca-phe-rang-sang-vua-dam | **66 (fail)** | **90** |
| ca-phe-robusta-va-arabica-khac-nhau-the-nao | 78 | **90** |

Mean 74.8 → **90.0**; posts failing the 70 bar: 1 → **0**. All "ngon"
weak-word hits (11) and the `no_sensory_detail` false fire are gone. The
one remaining deduction on every post is `repeated_openings` (−10),
dominated by the known Vietnamese tokenisation artefact ("cà phê" →
sentence-initial "cà" ×6–14): real, still unfixed — word segmentation
was out of this round's scope, and it now stands alone as the craft
layer's biggest remaining false-positive source on Vietnamese prose.

### Brand lint (F6/R3) — derived max + table-segment exclusion

`suggest_voice_rules` derived **max_sentence_words = 39** for this corpus
(p90 of observed lengths, clamped [25, 45] — exactly the ~39-word genuine
prose tail measured in §3c). Linting the same 5 posts with that cap (the
saved brand YAML itself was not modified):

| Post | Score before (cap 25) | Score after (cap 39) | Violations before | Violations after |
|---|---|---|---|---|
| ca-phe-rang-moc-la-gi | 92 | 96 | 4 | 2 |
| bao-quan-ca-phe-sau-khi-mo-tui | 88 | 96 | 6 | 2 |
| cach-pha-ca-phe-phin-rang-moc | 90 | 98 | 5 | 1 |
| cac-muc-rang-ca-phe-rang-sang-vua-dam | 88 | 96 | 6 | 2 |
| ca-phe-robusta-va-arabica-khac-nhau-the-nao | 86 | 94 | 7 | 3 |

Total `sentence_too_long`: **28 → 10**. The remaining 10 are all > 39-word
runs — mostly the furniture/flattened-table runs whose separators are
dashes, not `|` (so the table exclusion cannot see them), plus a few
genuine 40+ word sentences. Median violations: 0 before, 0 after.

### Style (F2/R6)

- Divergence warning on the 5 samples: **fired** ("first_person_rate CV
  2.0 — possibly mixed authors") → **gone** (near-zero-mean guard;
  warnings list is now empty).
- style_match self-scores on the 5 posts: **unchanged** (92/100/92/100/92)
  — long texts are unaffected by design.
- Writer trial (149 words): style_match **69 → 85**. reader_rate and
  number_density are now marked `reliability: "low"`, judged with widened
  ranges, and pass; sentence_stdev (3.2 vs 10.44) still fails — the one
  real signal in that score, preserved. Trial craft **90 → 100** (the
  `no_sensory_detail` wolf-cry is gone under the generic glossary).
  Trial brand score unchanged at 90 (median 18 vs band [11,17] — the
  fair catch from §6 still catches).

### What did NOT improve (honest list)

- **Fact layer (P3, out of scope):** the story post still draws 32
  Mộc-Brew-fact issues and the writer trial still fails the final gate
  on the single `wrong_price` (89.000đ ≠ 185.000đ). Nothing in this round
  touches that; per-brand fact bases remain the highest-leverage fix.
- **POV suggestion on this corpus:** the brand-name-as-speaker rule is
  implemented and unit-tested (synthetic "Mộc Nhỏ" corpus →
  "first-person (as brand name)"), but re-running it on Cao Nguyên Mộc's
  full 8,663-word corpus still yields "third-person": their aggregate
  reader_rate is 0.17/100 words, below the 0.8 gate the bounded rule
  requires (their "bạn" address concentrates in the story/product copy,
  diluted by policy and index pages). The heuristic helps brands that
  address readers densely; it does not rescue this one.
- **repeated_openings tokenisation artefact** (above) and **dash-flattened
  tables** (above) remain open — both need Vietnamese word segmentation /
  richer extraction, not threshold changes.
- **Claim detection (F10)** and **fact-noise dedup (F7)** were not in this
  round's approved scope and are unchanged.
