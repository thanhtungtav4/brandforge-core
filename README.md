# Content Core — the engine behind brandforge (P3 adds the app shell
# in ../brandforge-app; this package stays the referee)

The testable core of Smith's AI content-writing product. Product direction for
later phases: Next.js + LangGraph + Postgres/pgvector (3–6 weeks part-time).
**This phase deliberately builds none of that** — the principle is that the
content-writing core must be solid before any infrastructure wraps it.

Testbed brand is **simulated**: "Mộc Brew", small-batch coffee for Vietnamese
office workers. The repo ALSO contains one documented calibration run against
a real public site (Cao Nguyên Mộc, `calibration/`), including that site's
verified public prices in `data/facts_profiles/cao-nguyen-moc.json` — see the
calibration section below. Raw scrape captures from that run are kept as
local-only evidence and are not tracked (see `calibration/work/README.md`).

## Run it

```bash
cd ~/workspace/projects/content-core
python3 -m venv .venv            # already created
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q          # full test suite
.venv/bin/python run_golden.py         # MockWriter golden set, exit code non-zero on any FAIL
.venv/bin/python run_model_golden.py   # model-written golden set (MCP mode regression)
```

No API keys needed. Nothing calls an external LLM.

## Architecture

Real **LangGraph 1.2.24** `StateGraph` (installed in the local `.venv`, not a
fake/adapter). Typed state: `content_core/state.py` (`ContentState`).

```
validate_brief → research → outline → outline_gate ─(not approved)→ END
                                            │ approved
draft_sections → brand_check → fact_check → seo_pass → final_gate ─(pass)→ END
                                            │ fail & rewrite_count < 2
                                     rewrite_sections ─→ brand_check (loop)
                                            │ fail & rewrites exhausted → END (failed_gates)
```

- **research**: keyword-triggered lorebook retrieval from the local facts file
  only — entries stay dormant (zero cost) until a key matches the brief.
- **outline_gate**: human approval simulated by the `outline_approved` state
  flag. In LangGraph product code this becomes an `interrupt()`.
- **draft_sections**: writes section by section (never one long generation),
  via the pluggable `Writer` interface.
- **brand_check**: deterministic linter in code (`linter.py`) — banned terms,
  no exclamation marks in blog, median sentence length per format, max sentence
  length. Score = worst section score. Sentence splitting protects Vietnamese
  thousand separators (`185.000đ` is one token, never a sentence boundary —
  fixed in P1a; the unprotected splitter tore prices in half and corrupted
  the median metric).
- **fact_check**: deterministic (`fact_check.py`) — coverage in the Fact
  check section below; unsupported claims block the gate unless marked
  `[NEEDS SOURCE]` (then they are recorded as `flagged_claims`).
- **final_gate**: pass only if brand score ≥ 85 **and** 0 banned terms **and**
  0 unflagged fact issues **and** the draft is not vacuous (every section has
  words) **and** (blog/product) craft ≥ 70 — the rule lives in
  `content_core/gates.py` and is shared with the MCP `final_gate` tool,
  which can additionally apply the optional Quality Profile layer. On failure
  it rewrites **only the failing sections**, max 2 rewrites, and records
  every pass in `gate_history`.

## Gate rules (from data/brand_mocbrew.yaml — checkable, not adjectives)

- Median sentence 11–14 words (blog/product/comparison), 5–14 social, 8–14 email; max 25 words/sentence.
- No exclamation marks in blog.
- Banned: elevate, curated, game-changing, leverage, delve, seamless,
  "tốt nhất Việt Nam", "100% chữa mất ngủ", "đẳng cấp thế giới".
- Facts (facts file only): 185.000đ/250g, rang thứ Ba hàng tuần, giao 2–3 ngày,
  Arabica Cầu Đất + Robusta Đắk Lắk.
- A brief asking for a prohibited claim (golden brief 6: "100% chữa mất ngủ")
  is **refused**: the claim is recorded in `refused_claims` and never written.

## Fact check coverage (P1b)

`fact_check.py` is deterministic and compares the draft against
`data/facts_lorebook.json` only:

- **Money, normalized**: `185.000đ`, `185.000 đ`, `185.000đ/gói`,
  `185.000 VND` / `vnđ`, `185000đ`, `185k`, `185 nghìn`, `185 ngàn`,
  comma-grouped `185,000đ` and the `₫` sign (`185.000₫`) are all
  recognized, converted to a VND integer and compared with the fact price
  (185000). Any other amount — in any of those shapes — is a `wrong_price`
  issue, reported once per amount (span de-duplication).
- **Weight / delivery**: pack weights other than 250g/1000g (including the
  `gam`/`gram` spellings, `300 gam`), delivery ranges other than 2-3 ngày —
  typographic dashes and spaced ranges (`5–7 ngày`, `5 - 7 ngày`) are
  checked too. A **single-value** delivery is checked against the fact
  range: `giao trong 3 ngày` passes (inside 2–3), `giao trong 5 ngày`
  flags, and a same-day claim (`giao ngay trong ngày`) contradicts the
  range outright.
- **Roast weekday**: a sentence about roasting ("rang") that names a weekday
  other than the fact's thứ Ba is a `wrong_roast_day` issue. Weekdays in
  non-roast sentences are ignored, and a negated mention ("không rang vào
  thứ Tư") is not a claim.
- **Origins**: Arabica pairs only with Cầu Đất, Robusta only with Đắk Lắk
  (`wrong_origin`); a sentence naming a bean with the other region is
  flagged only when the bean's true home is not named in the same sentence,
  so one sentence may list both correct pairings. The pairing check is
  negation-aware: `Robusta không lấy từ Cầu Đất` and `không phải Robusta
  Cầu Đất` are denials and pass, while `không chỉ lấy từ Cầu Đất` affirms
  and still flags. Another coffee region
  (Bảo Lộc, Di Linh, Gia Lai, Kon Tum, Sơn La, Điện Biên, Buôn Ma Thuột)
  presented as the source of the brand's beans (sourcing markers like
  "hạt của", "trồng tại", "nguồn hạt", "Mộc Brew") is an `unknown_origin`
  issue; merely mentioning such a region (e.g. in a comparison) is not flagged.
- **Unsupported numbers**: a digit + countable unit (`500 khách hàng`,
  `3 cửa hàng`, `10 năm kinh nghiệm`, …) that no fact supports is an
  `unsupported_number` issue — unless the sentence is marked
  `[NEEDS SOURCE]`, in which case it is recorded as a flagged claim instead.
  Fact numbers (185/250/1000, the 2-3 delivery range) are whitelisted.
  The same check catches numbers written as Vietnamese **words** in
  scale shapes (`năm trăm khách hàng`, `hàng nghìn người dùng`).
  **Limitation:** money fully spelled out in words (`một trăm tám mươi
  lăm nghìn đồng`) is NOT parsed — the price check covers digit formats
  only; a spelled-out price passes unchecked (pinned by a boundary test
  in `tests/test_core_completion.py`).
- **Claims**: prohibited phrases block outright; evidence-requiring phrases
  and bare percentages block unless marked `[NEEDS SOURCE]`.

## Matching is normalized + negation-aware (audit round 2)

Banned terms (linter), claim phrases, roast weekdays and origin pairing are
matched on a NORMALIZED form of the text (lowercase, Vietnamese diacritics
stripped, punctuation collapsed — `linter.normalize_for_match`), because raw
substring matching had three evasion classes and one false-positive class:

- diacritics-free copy evaded everything: `dang cap the gioi`,
  `100% chua mat ngu`, `Arabica Dak Lak` now match their banned/claim forms;
- punctuation variants evaded: `game changing` matches `game-changing`,
  `100 % …` matches `100% …`; fullwidth `！` counts as an exclamation mark
  in blog and as a sentence boundary;
- `số 1` no longer matches inside ordinary numbers (`số 12`, `số 185`) —
  digit-boundary guards on phrase matching;
- a NEGATED claim is not an assertion: `không phải là tốt nhất`,
  `không nhận là chữa mất ngủ` are not flagged (the narrow negation list
  does not include `không chỉ …`, which affirms and still flags).

The gate also rejects a VACUOUS draft (audit round 2): no sections, or any
section with zero words, is a hard failure — an empty draft used to pass
every checker trivially (no text = no violations = brand 100).

Sentence splitting protects a documented set of Vietnamese abbreviations
(`TP. Hồ Chí Minh`, `ThS. Nguyễn`, `Q. 1`, PGS/GS/TS/BS/KS) as well as
numbers, so abbreviations no longer tear sentences and corrupt the
median/rhythm metrics.

## Storage durability (core-completion round)

Drafts and all stored profiles (style / brand drafts / quality) are
written **atomically** — temp file in the same directory + `os.replace`
(`loaders.atomic_write_text`) — so a crash mid-write leaves the previous
JSON intact instead of a corrupt file. Stored filenames are injective:
when an id contains characters the filename sanitiser must drop, a short
stable hash of the original id is appended, so `a/b` and `ab` no longer
overwrite each other; already-clean ids keep their exact legacy
filenames. (Versioning/locking remains deferred to the product phase.)

## Four data layers — kept separate on purpose

| Layer | File | Holds |
|---|---|---|
| Persona | `data/persona_linh.json` | Character Card V2 for Linh, barista-writer (who writes) |
| Brand | `data/brand_mocbrew.yaml` | Voice rules, banned terms, examples (how the brand sounds) |
| Facts | `data/facts_lorebook.json` | Prices, schedule, origins + keyword lorebook entries (what is true) |
| Craft | `data/craft_library.json`, `data/exemplars.json`, `data/domain_glossary.json` | Technique cards, exemplar passages, domain vocabulary (how to write WELL — see Layer 4 section below) |

Mixing these layers is the most common cause of voice/fact drift, so the core
never merges them into one prompt blob.

## Layer 4 — Craft & Domain Library

The first three layers make output **correct** (right persona, right voice
rules, right facts) but the prose was still plain. Layer 4 exists to raise
prose **quality**. It is a separate layer on purpose: craft is not brand
(a brand rule says what Mộc Brew never does; a craft card says how anyone
writes a good sentence), and it is not facts.

### The three data files

| File | What it is |
|---|---|
| `data/craft_library.json` | **12 technique cards** distilled from classic writing principles: concrete specifics (the one always-on core card), concrete opening, strong verbs, show-don't-tell sensory detail, deliberate rhythm variation, cut filler, one idea per paragraph, active voice, concrete ending, logical transitions, fact-before-claim, domain vocabulary. Each card: checkable `instruction`, an ORIGINAL `good_example` and `bad_example` written for Mộc Brew, and a `source_note`. |
| `data/exemplars.json` | **Exemplar bank**: one strong ORIGINAL passage (60–120 words) per format — `blog_intro`, `product`, `social`, `email`, `comparison` — each with 2–4 `why_it_works` notes. These are few-shot style anchors in `assemble_context`. |
| `data/domain_glossary.json` | **Coffee vocabulary**: 6 groups (`aroma`, `acidity`, `body`, `aftertaste`, `brewing`, `roast`) of precise terms with one-line usage notes, plus **16 weak words** (`ngon`, `thơm ngon`, `tuyệt vời`, `hoàn hảo`, `đặc biệt`, `chất lượng cao`, …) each with a precise suggested replacement. |

### Copyright rule: distillation, not copying

No book text is stored anywhere in this repo. Only the **principles** are
kept, paraphrased into checkable instructions in our own words (every card's
`source_note` says e.g. "Orwell's rules — paraphrased", "Strunk & White
principles — paraphrased", "Zinsser, On Writing Well — principle
paraphrased"). Every example sentence is original writing for Mộc Brew.
Ideas and principles are reusable; an author's sentences are not.

### How cards trigger (lorebook-style)

`content_core/craft.py::retrieve_cards(format, intent, text)` — a card stays
**dormant** until it triggers: its `formats` must include the task format,
and when it declares `intents`/`keywords`, the intent must match or a keyword
must appear in the topic/text. A card declaring neither triggers on format
alone. At most **3 cards** per call, at most **1 always-on core card**
(`concrete-specifics`). An unrelated task (e.g. a social post about the
weather) gets only the core card — craft costs nothing when it is not
relevant. Passing an **empty format** (`""`) disables format filtering
entirely (dormancy still applies); callers that want filtering must pass
a real format.

### craft_check — a quality score, separate from the brand score

`craft_check(text, format)` (in `craft.py`, exposed as an MCP tool) is
deterministic and scores **writing quality only**, 0–100:

- weak/vague word: −6 per occurrence, capped −30 (each hit comes with the
  glossary's suggested replacement)
- filler phrase (`có thể nói rằng`, `thật sự là`, …): −4 each, capped −12
- monotone rhythm: −15 (≥4 sentences and stdev of sentence lengths < 2.0;
  sentence splitting protects Vietnamese thousand separators, so `185.000đ`
  is not mistaken for a sentence boundary)
- repeated sentence openings (same first word ≥3 times): −10
- no glossary sensory term in a product/blog text: −10

**Too-short guard:** text under **10 words** (`MIN_WORDS_TO_JUDGE`) is
not judged at all — it returns score **0** with a `too_short_to_judge`
violation. (The penalty-only scoring used to return ~90 for an empty
string: nothing to penalise read as good prose.) Legitimate short
formats sit far above the floor — the golden social post is ~35 words.

It returns `{score, violations, metrics{word_count, sentence_count, rhythm_stdev,
weak_words_found, sensory_terms_used}}`. Measured on real fixtures: the
pre-Craft demo product text (correct but plain, uniform 13–14-word
sentences) scores **85** (monotone rhythm is its only craft fault); a
sensory-rich rewrite of the same facts scores **100** with 7 glossary
sensory terms.

### Gate status: BLOCKING for blog/product (P2b) — one gate, two call sites

Craft is a blocking rule for long-form formats: for `blog` and `product`
drafts, a craft score below **70** (`CRAFT_PASS_SCORE`, formats in
`CRAFT_BLOCKING_FORMATS`, both in `content_core/craft.py`) **fails the
gate** — the craft-failing sections join `failing_sections`,
`gate_history` records `craft_score` and `craft_blocking`, and the gate
instruction tells the writer to rewrite those sections for craft (weak
words, rhythm, sensory detail). For `social`, `email` and `comparison`
craft stays **advisory** (reported, never blocking; `craft_advisory` is
true exactly for those formats).

**The gate rule now lives in exactly one place: `content_core/gates.py`
(`evaluate_gate`)**, and both call sites — the MCP `final_gate` tool and
the LangGraph graph's `final_gate` node — call it. The graph previously
skipped the craft layer (MockWriter was assumed to fail craft); measured
with `craft_check_sections` over the golden set, MockWriter output
scores craft **75** (worst section) for blog/product good drafts and
**84** on the intentionally bad draft's first pass (its badness is
brand/fact, which craft does not measure) — all ≥ 70, so craft blocking
is enabled in the graph too: **full unification, no behavioural
difference between the paths**. `evaluate_gate` keeps an explicit
`craft_mode` parameter (`"blocking"` default; `"advisory"` is the single
escape hatch for a future fixture writer) — both production call sites
use `"blocking"`. The gate constants (`BRAND_PASS_SCORE`,
`MAX_REWRITES`, and the craft constants re-exported) live in `gates.py`;
`graph.py` and `mcp_server.py` import them from there.

## Quality Profile — the weighted quality layer

On top of the hard + craft layers, the gate has an optional **quality
layer** (`content_core/quality.py`), implementing the design Smith
approved from the artifact's quality tab: a draft is scored on **7 axes**
and the user distributes a **100-point budget** across them at setup.

| Axis | Label | How it is scored |
|---|---|---|
| `facts` | Sự thật & bằng chứng | MEASURED: fact_check — 100 − 30 per unflagged issue − 10 per flagged `[NEEDS SOURCE]` item |
| `voice` | Giọng brand & văn phong | MEASURED: brand linter score; with a Style Profile, the mean of brand score and `style_match` score |
| `clarity` | Dễ hiểu | MEASURED: `craft_check` over the full text (v1 mapping — craft blends clarity + rhythm + concreteness; splitting it is future work) |
| `coherence` | Mạch lạc & cấu trúc | JUDGED: the writing model grades 0/1/2 |
| `seo` | SEO / được tìm thấy | MEASURED: `seo_pass` returns fields, not a score — the axis maps its 4 boolean outcomes (keyword_in_title, headings_ok, meta_ok, title present) at 25 points each |
| `persuasion` | Thuyết phục & CTA | JUDGED: grade 0/1/2 |
| `depth` | Độ gốc & chiều sâu | JUDGED: grade 0/1/2 |

Judged grades convert **{0: 20, 1: 60, 2: 95}** (mirroring the artifact's
0/1/2 rubrics). A judged axis the writer does not grade is **excluded**
and the weights renormalise over the present axes — the report lists
`measured_axes` / `judged_axes` / `missing_axes` explicitly; a judged
score is never invented.

**Presets** (weights sum to exactly 100; the artifact demo's numbers):
blog {18/14/14/14/22/8/10}, product {24/12/12/10/10/22/10},
social {14/26/14/10/8/20/8}, email {16/12/22/14/6/22/8},
comparison {30/10/12/14/12/10/12} (axis order as in the table).
**Floors** (same for all formats in v1): facts 70, voice 60, clarity 60,
coherence 60, seo 55, persuasion 55, depth 60. **PASS_TOTAL = 70.**

Two guardrails are structural:

1. **Hard criteria stay OUTSIDE the weighting.** Banned terms, wrong
   facts and illegal claims fail in the hard layer no matter what the
   quality layer says — a high weighted total cannot rescue them
   (pinned by a test).
2. **Every axis has a floor (anti-compensation).** `quality_pass`
   requires weighted_total ≥ pass_total **and** zero axes below floor —
   a draft with depth graded 0 fails on the depth floor even at a
   weighted total of 88 (pinned by a test).

Custom profiles (weights each within [2, 45], sum exactly 100; floors
overridable within [0, 90]) persist via `loaders.py` to
`data/quality_profiles/<id>.json` (env
`CONTENT_CORE_QUALITY_PROFILES_DIR`), and are managed through the MCP
tools `get_quality_profile` / `save_quality_profile`; `quality_score`
scores a draft without running the gate, and `final_gate` activates the
layer when given `quality_profile` and/or `judged_scores` (with judged
scores alone, the draft format's preset is used). With neither argument,
`final_gate` returns exactly the legacy hard+craft verdict.

### Adding a real book later

1. Read the book (a human, or a model in-session).
2. Distill each usable principle into a card **in your own words**: a
   checkable instruction, trigger conditions, and original good/bad examples
   written for the brand at hand. Never paste the book's sentences in.
3. Add or extend exemplar passages (original writing) and glossary terms
   the book's domain suggests.
4. Re-run the golden set + full pytest. A card that makes the golden set
   worse does not ship.

## Style Extraction — văn phong từ bài mẫu

Correct + well-crafted is still not the same as *sounding like a specific
human*. Style Extraction closes that gap: the user pastes links or texts
whose voice they like, and the system distills them into a **Style
Profile** — measured, stored, and reusable — instead of re-pasting the
samples into every prompt.

### The workflow (the MCP server stays offline)

```
paste links ──► fetch_sample.py (or Muse fetches in-session)
            ──► analyze_style(samples)     deterministic fingerprint + warnings
            ──► the MODEL reads the samples and fills the qualitative schema
            ──► save_style_profile(...)    persisted to data/style_profiles/<id>.json
            ──► a HUMAN approves the profile
            ──► assemble_context(brief, style_profile_id=...) injects it
            ──► final_gate reports style_match (advisory) for styled drafts
```

`fetch_sample.py <url> [more urls...]` fetches with urllib (browser-ish UA,
20s timeout) and strips HTML to plain text with the stdlib `html.parser`
(script/style/nav/footer dropped), printing `{url, title, text,
word_count}` per URL. All profile file IO lives in `loaders.py`
(`save_style_profile` / `load_style_profile` / `list_style_profiles`,
dir overridable via `CONTENT_CORE_STYLE_PROFILES_DIR`);
`content_core/style.py` is pure computation.

### What gets measured — the fingerprint

Code measures, never guesses (`style.fingerprint`):

| Metric | Meaning |
|---|---|
| `sentence_len {median, mean, stdev}` | Sentence-length centre AND spread. The stdev is **burstiness** — the strongest human-vs-AI signal: people mix 3-word fragments with 30-word runs, models default to uniform lengths |
| `short_sentence_share` / `long_sentence_share` | Fraction of sentences ≤6 / ≥20 words |
| `paragraph_len_median` | Median words per paragraph (blank-line split; sentence groups of 3 when the text has no blank lines) |
| `question_rate` / `exclamation_rate` | Per 100 sentences |
| `first_person_rate` | `mình/tôi/ta/chúng mình/chúng tôi` per 100 words (`người ta` is generic third person and is NOT counted) |
| `reader_rate` | `bạn` per 100 words |
| `number_density` | Digit-bearing tokens per 100 words — concreteness |
| `transition_freq` | Counts for a VN transition list (`tuy nhiên`, `thực ra`, `hóa ra`, `sau đó`, `tóm lại`, …) |
| `type_token_ratio`, `top_words` | Vocabulary richness; most frequent content words after a built-in VN stopword list |

`analyze_samples` also returns a **consistency report**: for the key
metrics (sentence median, stdev, first-person rate) it computes the
cross-sample coefficient of variation; CV > 0.35 warns "possibly mixed
authors". It also warns when there are fewer than 3 samples, or fewer
than 800 total words.

### What the model fills in — the qualitative schema

`QUALITATIVE_FIELDS` in `style.py`: `tone_traits` (list of
`{trait, do, dont}`), `opening_patterns`, `closing_patterns`,
`signature_devices`, `stance`, `never_do`. Code only validates and stores
these (unknown keys are ignored, missing keys default to empty); a model
that actually read the samples writes them, and a human approves them.

### The 3 usage rules

1. **3–5 samples from ONE author/source.** Mixing authors yields the
   average internet voice — exactly the "AI voice" this feature exists to
   avoid. The consistency warnings enforce this mechanically.
2. **The brand's own published posts are the best samples.** They are
   also the only samples whose passages may be stored.
3. **Distill traits, don't copy passages.** `store_excerpts` defaults to
   **False**: exemplar passages (≤120 words, ≤3) are stored only for the
   user's own or licensed texts. For other people's writing the profile
   keeps the fingerprint + distilled traits, never their sentences.

### style_match — is this draft in the sample voice?

`style_match(text, profile)` compares a draft's fingerprint to the
profile on 6 metrics (sentence median, sentence stdev, short-sentence
share, first-person rate, reader rate, number density), each with a
documented range rule (e.g. median within ±35% or ±3 words; stdev ratio
0.5–2.0). Score: 100 − 15 per out-of-range key metric (median, stdev)
− 8 per other out-of-range metric, with concrete fix advice per miss
("draft sentences too uniform: mix in short 3-6 word sentences…").
In `final_gate` the style report is **advisory in v1** — included when
the draft's brief carries `style_profile_id`, recorded in `gate_history`
as `style_score`, and it never changes `passed`.

### Built-in example: `mocbrew-blog-voice`

Shipped at `data/style_profiles/mocbrew-blog-voice.json`, built by
`build_default_style_profile.py` from 3 ORIGINAL Mộc Brew blog samples
(`data/style_samples_mocbrew.json`, one consistent conversational voice;
excerpts stored because they are our own simulated brand's texts).
Measured fingerprint: 829 words, 61 sentences — sentence median **13.0**,
stdev **8.72** (high burstiness), short-sentence share **0.28**,
first-person **3.5**/100w, reader **1.93**/100w, number density
**0.36**/100w, type-token ratio 0.37, no consistency warnings.
Discrimination check (pinned in `tests/test_style.py`): a same-voice
text scores **100** against it; a monotone uniform-sentence text scores
**61** with burstiness flagged out of range.

## Brand Builder — xây Brand từ site đích / đối thủ

Writing a Brand Voice profile from scratch is slow, so most users never
finish one. The Brand Builder inverts the work: **analyze the site that
already exists** (the client's own site, or a competitor's), generate a
draft that is 80% filled, and let the user edit/approve it.

### The workflow

```
dán link ──► fetch_site.py <root_url> [--max-pages 8]
             (stdlib CLI helper; homepage + prioritized internal pages —
             gioi-thieu/about, san-pham/product, dich-vu/service, blog,
             tin-tuc, lien-he/contact first; per page: clean text,
             headings, and html_meta kit evidence)
         ──► analyze_site_brand(pages)     deterministic analysis to review
         ──► the MODEL reads the pages and fills the qualitative schema
             (tone_traits[{dimension, position, do, dont}], audience_guess,
             positioning, differentiators)
         ──► save_brand_draft(pages, qualitative, source_type)
             persisted to data/brand_drafts/<brand-id>.json (loaders.py,
             env CONTENT_CORE_BRAND_DRAFTS_DIR)
         ──► the USER edits the draft — most fields are already there
         ──► draft_to_brand_yaml(draft)    brand chính thức (official brand)
```

`fetch_site.py` is a CLI helper like `fetch_sample.py` — the MCP server
stays offline; every Brand Builder function takes the fetched
`pages = [{url, title, text, headings, html_meta}]` shape
(`content_core/brand_extract.py`, pure computation).

### What is inferred vs what the user MUST decide

**Inferred by code** (deterministic, each kit value carries the
`source_url` it came from): brand kit (name from og:site_name → repeated
title segment → domain; logo, theme color, og image, top-6 colors, top-3
fonts merged across pages); voice suggestions from the measured
fingerprint (sentence-median range around the site's real median, POV from
the first-person rate, reader address from the `bạn` rate); vocabulary
candidates (frequent domain bigrams; `exact` = brand + product-like names
from titles/headings, casing preserved); example passages from the site's
own pages.

**The user MUST decide** — the draft says so in `review_notes`:
- **banned_terms starts EMPTY, always.** A site never reveals what it
  forbids itself; bans cannot be inferred and are never invented.
- **traits are model-proposed.** The qualitative schema is a draft for
  approval, not a measurement.
- **claims need evidence.** The site's own superlative/claim sentences are
  collected into `claims_and_guardrails.site_claims_to_review`, each with
  its source_url and `risk: needs_evidence`. A site asserting "tốt nhất
  Việt Nam" does not make it an approved brand claim.
- **hype punctuation is flagged, not copied.** A high exclamation rate
  becomes a review note ("decide deliberately"), never a formatting rule.

### Calibration fixes (from the caonguyenmoc.vn run — report in `calibration/`)

The first real-site calibration (findings F1–F9) produced these changes;
the Smith-approved thresholds (craft bar 70, quality floors, exclamation
rule) did not move:

- **Fetch hardening (F1).** Pages carry `js_shell_suspected`
  (fetch_sample.detect_js_shell: tiny extracted text + ≥50% of bytes in
  `<script>`, or a large page with no text at all). When a homepage
  yields < 3 links or < 100 words, `fetch_site` falls back to sitemap
  discovery (robots.txt `Sitemap:` lines, then `/sitemap.xml`, sitemap
  indexes one level deep). Text inside `<script type="application/ld+json">`
  blocks (headline/description/articleBody fields) is extracted; plain
  script data is still skipped.
- **Per-brand max sentence (F6/R3).** `suggest_voice_rules` DERIVES
  `max_sentence_words` from the corpus (90th percentile of observed
  sentence lengths, clamped [25, 45]) instead of always writing 25;
  the linter already read the cap from the brand file, and now also
  excludes table-like segments (≥ 2 `|`) from its median/max statistics.
- **Craft glossary per brand (F8).** `craft_check`/`craft_check_sections`
  (and the MCP `craft_check` tool) accept `brand_id`: the shipped
  glossary is Mộc Brew's, so any other brand is judged with a generic
  brand-neutral Vietnamese weak-word/sensory set (`GENERIC_GLOSSARY`
  in craft.py). The default path is unchanged.
- **Style tolerances (F2/R6).** The mixed-authors divergence warning no
  longer uses CV when a metric's mean is near zero (absolute spread
  instead). `style_match` on texts < 300 words marks the per-100-word
  density metrics `reliability: "low"`, widens their ranges, and adds a
  `reliability_note`; sentence-shape metrics are unchanged.
- **Builder trust leaks (F3/F4/F5/F9).** Drafts no longer inherit the
  house brand's `prohibited_claims` (they start empty, with a review
  note). Logo detection requires a positive signal ('logo' in src or
  class/id, or in alt inside a `<header>`). CSS `var(--…)` values and
  generic families are filtered from font rankings. Conversion keeps
  each example's `source_url` and sanitizes the brand kit (var fonts
  dropped; a logo URL with no 'logo' in its path is nulled). POV
  suggestion detects brand-name-as-speaker ("first-person (as brand
  name)").

### Competitor rules

A competitor's site is for **analysis only** — their traits, positioning
and gaps, never their copy. Drafts built with
`source_type="competitor"` carry a top-level `competitor_warning` and mark
**every example `reference_only: true`**. `draft_to_brand_yaml` **refuses**
(ValueError) to convert any draft that still contains reference_only
examples: the user must replace/paraphrase them with the brand's own words
first. Competitor text can never become an approved brand by accident.

### Compare mode — positioning gaps

`compare_brand_voice(pages_a, pages_b)` contrasts two sites (mine vs a
competitor): the 6 style-match fingerprint metrics side by side, sentence
median/stdev, vocabulary symmetric difference, claim counts, both brand
kits, and `gaps` — observations generated in code from the measured
deltas with documented thresholds (first-person ratio ≥2×, claims
difference ≥3, median difference ≥3 words, burstiness ratio ≥1.5×,
exclamation difference ≥5/100 sentences). Measured on the fixture sites
(Sớm Coffee vs Đỉnh Cao Coffee): first-person 5.84 vs 0.0 per 100 words,
7 superlative claims vs 0 ("more restrained"), exclamation 100 vs 0 per
100 sentences, disjoint vocabularies and kits (green/Be Vietnam Pro vs
red-gold/Montserrat).

Demo: `.venv/bin/python demo_brand_builder.py` runs the whole flow on the
two fictional fixture sites in `tests/fixtures/` (file://, no network).

## Golden set (`content_core/golden.py`, runner: `run_golden.py`)

6 briefs, each with its own criteria: (1) 1500+ word SEO blog drift test,
(2) product description fact accuracy, (3) social tone-flex, (4) care email
clarity, (5) comparison with `[NEEDS SOURCE]` marking, (6) false health claim
refusal. Plus case `g7-demo`: an intentionally bad draft (`writer_mode="bad"` —
banned words, exclamations, wrong price 200.000đ) that the linter catches on
pass 1 and the rewrite loop recovers. Run the golden set after **every**
prompt/flow change; a regression there blocks merging.

## Model golden set (`data/model_golden.json`, runner: `run_model_golden.py`)

The golden set above tests the gates with MockWriter fixtures. It cannot
test the thing that actually breaks in MCP mode: **real model prose**. The
model golden set pins six model-written drafts (written by Muse as the
writer) with expected brand/craft scores and gate verdicts, evaluated by
`content_core/model_golden.py` with the MCP final_gate's exact functions
and rule (including craft blocking at 70 for blog/product):

| Entry | What it protects |
|---|---|
| mg1 | The demo v2 product draft (sensory-rich): brand 100, craft 90, gate passes |
| mg2 | The demo v1 product draft (correct but plain): craft 75 worst-section (85 whole-text) — passes, since 75 ≥ 70 |
| mg3 | A good blog intro (both origin pairings in one sentence): brand 100, craft 100, passes |
| mg4 | A mediocre generic blog draft: brand/fact clean, craft 35 → gate **fails on craft only** |
| mg5 | A drift draft: sections 1–2 clean, section 3 has a banned term + a `190k` wrong price → gate fails, `failing_sections` is exactly `[s3]` |
| mg6 | A social post: social is not craft-blocking, clean brand/fact → passes |

Run it together with the golden set after every prompt/craft/core change:
`.venv/bin/python run_model_golden.py` (exit non-zero on any mismatch).

## What is mock vs real

- **Real**: the LangGraph graph, typed state, gates, linter, fact checker, SEO
  pass, rewrite loop, golden runner, tests.
- **Mock**: `MockWriter` (`content_core/writer.py`) — a deterministic writer
  built from a pool of brand-compliant sentences, so tests are reproducible
  without an LLM. Its drafts are structurally valid test fixtures, not
  publishable prose.

## Plugging in a real LLM writer (next step)

Implement the `Writer` interface in `content_core/writer.py`:

```python
class Writer(Protocol):
    def write_section(self, section: dict, ctx: dict) -> str: ...
    def rewrite_section(self, section: dict, ctx: dict, issues: list[dict]) -> str: ...
```

Then either pass it in code — `run_flow(brief, writer=MyLLMWriter())` /
`build_graph(writer=...)` — or select it by environment variable:
`CONTENT_WRITER=my_module:MyLLMWriter` (default `mock`). `ctx` already carries
brief, retrieved sources, facts, brand and persona, assembled in the
context order used by the MCP `assemble_context` tool (persona → brand rules
→ craft cards → optional style profile → triggered facts → exemplar → task
→ post-history reminder goes in the writer's prompt template). All gates/checkers stay unchanged —
that is the point of the core: swapping the writer cannot weaken the gates.

## MCP mode — Muse is the writer

Smith's decision (21 Nov 2026, superseding "connect an external LLM provider"):
**no external LLM provider for now.** Muse's own main model is the Writer.
The core is exposed as an **MCP server** (stdio by default, streamable
HTTP on localhost with `--transport http`; official Python `mcp` SDK
1.30.0, FastMCP — real SDK, pinned in `requirements.txt`), so the agent uses
the core tools as its hands: Muse writes the prose in-session, the tools do
everything deterministic. The packaging section below ("Chạy như tool
trong app AI local") generalizes this to Gemini / ChatGPT / Muse:
any host app's model can be the writer; the core stays the referee.

```
┌─────────────────────────────────────────────────────────────┐
│  Muse (main model)  =  BRAIN + WRITER                      │
│  reads assembled context, writes/repairs prose in-session   │
└──────────────┬──────────────────────────────────────────────┘
               │ MCP tools (stdio)
┌──────────────▼──────────────────────────────────────────────┐
│  mcp_server.py  =  HANDS + GATES (deterministic, no LLM)    │
│  research/facts · context assembly · brand lint · fact      │
│  check · SEO check · final gate · draft state · golden set  │
│  — every tool wraps an existing content_core function,      │
│    no logic is duplicated in the server                     │
└──────────────┬──────────────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────────────────┐
│  Data layers: Persona (Card V2) · Brand (YAML) · Facts      │
│               Craft (cards / exemplars / glossary)           │
│  Draft state: drafts/<draft_id>.json (local JSON files)     │
└─────────────────────────────────────────────────────────────┘
```

### Tools (68)

| Tool | What it does |
|---|---|
| `get_persona` | Linh, Character Card V2 |
| `get_brand` | Mộc Brew voice rules (parsed YAML): banned terms, sentence rules, examples |
| `research_facts(query)` | Keyword-triggered lorebook retrieval (reuses the graph's research node) + canonical facts |
| `get_craft_cards(format, intent?, query?)` | Layer 4: triggered technique cards (lorebook-style, ≤3, ≤1 core) |
| `get_exemplars(format)` | Layer 4: exemplar passage(s) for the format (few-shot style anchors) |
| `get_domain_glossary(group?)` | Layer 4: coffee glossary groups + weak words with replacements |
| `craft_check(text?, format?, draft_id?)` | Layer 4: deterministic prose-QUALITY score (weak words, rhythm, sensory detail) — reports only; the blocking decision lives in `final_gate` |
| `assemble_context(brief, format?, style_profile_id?)` | Ordered prompt context the writer must follow: 1 persona → 2 brand rules → 3 craft cards (triggered) → 4 triggered facts → 5 exemplar (matching format) → 6 task → 7 post-history reminder. With `style_profile_id`, a `style_profile` part (fingerprint targets + qualitative do/don't + never_do) is inserted after craft cards — 8 parts total. When the brand's research pack has APPLIED items, a `depth_material` part (applied facts with provenance + cited source excerpts, labelled SOURCE MATERIAL — never merged into the fact base) is inserted after the facts; `quality_score` / `check_draft_all` report the same availability as `depth_context` | **Response shape (backlog item 8):** the rendered `context_text` is the single representation of the context; alongside it the tool returns only lightweight metadata — `counts` (sources / craft_cards / exemplars), `style_profile` as `{id, name}`, `ordered_context` as `[{order, part, chars}]` (no content), and `truncated_parts`. A documented soft budget caps each part (persona 8000, brand_rules 6000, craft_cards 4000, style_profile 4000, facts_triggered 3000, exemplar 3000, task 1000, post_history_reminder 4000 chars; brand examples ≤ 8 entries, fact entries ≤ 12, exemplars ≤ 2); an over-budget part is truncated with an explicit `[truncated: …]` marker and named in `truncated_parts`, never silently |
| `analyze_style(samples)` | Style Extraction: per-sample + aggregate deterministic fingerprint and consistency warnings (too few samples/words, mixed-authors divergence) |
| `save_style_profile(name, samples, qualitative, store_excerpts=false, brand_id?)` | Style Extraction: build + persist a Style Profile from samples and the model-filled qualitative schema. Excerpts stored only when `store_excerpts=True` (user's own/licensed samples) |
| `get_style_profile(profile_id?)` | Style Extraction: one stored profile by id (`default` → `mocbrew-blog-voice`); with no id, the profile list + the default |
| `style_match(text? / draft_id?, profile_id?)` | Style Extraction: closeness of a text/draft to a profile's fingerprint (0-100, per-metric ranges, fix advice). Advisory only |
| `validate_brief(brief)` | Brief validation + prohibited-claim detection → `refused_claims` (e.g. "100% chữa mất ngủ" is refused) |
| `make_outline(brief, proposed_outline?, draft_id?)` | Deterministic template outline, or validates/stores a model-proposed outline |
| `start_draft(brief)` | New draft, persisted to `drafts/<draft_id>.json`; returns `draft_id` |
| `save_section(draft_id, section_id, heading, text, …)` | Save/overwrite ONE section; rewriting gate-flagged sections counts toward the max-2 rewrite budget |
| `get_draft(draft_id)` | Full stored draft state incl. gate history |
| `brand_check(text?, format?, draft_id?)` | Deterministic brand linter + score on a text or a whole draft |
| `fact_check(text?, draft_id?, brand_id?)` | Prices/numbers vs the ACTIVE brand's fact base; `[NEEDS SOURCE]` claims flagged, unmarked unsupported claims block. Brand resolution: `brand_id` → draft's `brief.brand_id` → server brand; an unknown brand is a structured error, never a check against another brand's facts |
| `get_facts(brand_id?)` | The per-brand fact base (default moc-brew; `''` lists the stored facts profiles) |
| `save_facts(brand_id, facts)` | Validate + persist a brand's fact base (schema-checked, atomic write — same discipline as `save_brand_draft`) |
| `seo_check(draft_id?, title?, keyword?, topic?)` | Title / meta / headings / keyword coverage |
| `final_gate(draft_id, quality_profile?, judged_scores?)` | THE shared gate (`gates.evaluate_gate`, same as the graph): PASS only if brand ≥85 AND 0 banned AND 0 unflagged fact issues AND no empty sections AND (blog/product only) craft ≥70 AND, when activated, the Quality Profile passes; returns the Layer 4 `craft` report and records `craft_score`/`craft_blocking` in `gate_history` — craft **blocks** for blog/product, advisory for social/email/comparison; when the brief carries `style_profile_id`, also returns an advisory `style_match` report (recorded as `style_score`, never blocking); returns exactly which sections to rewrite |
| `get_quality_profile(format_or_id)` | Quality Profile: a format name → the shipped preset; a saved custom id → that profile ({weights sum 100, floors, pass_total}) |
| `save_quality_profile(format, weights, floors?, pass_total?)` | Quality Profile: validate (7 axes, each weight in [2, 45], sum exactly 100) + persist a custom profile to `data/quality_profiles/` |
| `quality_score(draft_id, judged_scores?, quality_profile?)` | Quality Profile: score a draft on the 7 axes WITHOUT running the gate — dumbbell-ready per-axis score vs floor, weighted total, quality_pass; judged axes (coherence/persuasion/depth) are the writer's 0/1/2 grades, missing ones are excluded, never invented |
| `check_draft_all(draft_id, judged_scores?, quality_profile?)` | Combined check (perf P3): brand + fact + craft + style + quality for a stored draft in ONE call, each checker run once, the lint/fact results fed into the quality layer via the gate's own `_precomputed` reuse — every sub-report mirrors the individual tool's shape and numbers; changes no state, `final_gate` remains the only verdict |
| `score_section_variants(section_role, variants, draft_id? / brand_id?)` | Best-of-N (quality uplift): rank 2–5 candidate texts for a headline / opening / cta / closing with the gate's own checkers, scored in isolation. A variant with a banned term or an unflagged fact issue is **ineligible** and ranks last however fluent (hard criteria stay outside the weighting); seo/coherence are reported `not_scored` — they need the full draft and are never faked |
| `run_golden()` | Runs the golden set through the LangGraph core, per-brief PASS/FAIL |
| `analyze_site_brand(pages)` | Brand Builder: deterministic analysis of a fetched site (pages from `fetch_site.py`) — corpus stats, style fingerprint, visual brand kit with source_url evidence, voice-rule suggestions, vocabulary candidates, site claims flagged needs_evidence |
| `save_brand_draft(pages, qualitative?, source_type?, brand_name?)` | Brand Builder: build + persist a DRAFT Brand Voice profile in the brand YAML structure. `qualitative` is the model-filled schema (tone traits, audience, positioning, differentiators). Drafts carry review_status="draft" + review_notes; competitor drafts mark every example reference_only |
| `get_brand_draft(draft_id?)` | Brand Builder: one stored brand draft by id, or the list of drafts |
| `compare_brand_voice(pages_a, pages_b)` | Brand Builder compare mode: two sites' fingerprints side by side, vocabulary difference, claim counts, both kits, and `gaps` — positioning observations computed from the measured deltas |
| `prepare_brand_setup(site_url, brand_id, display_name?)` | Setup Assist: prepare a NEW brand's whole setup in one call → the SETUP DOSSIER. Fetches the site, saves the brand DRAFT via the Brand Builder (visual palette inferred from the site kit, stamped `setup_assist`), extracts a FACTS DRAFT into the setup record (never the live profile), creates the research pack if absent + builds its proposals, PROPOSES a corpus + persona candidates. Nothing goes live; site self-contradictions block the facts draft with the contested values withheld |
| `get_brand_setup(brand_id)` | Setup Assist: the setup checklist — per-step status (done / proposed / blocked / needs_human) DERIVED from the live stores, so steps completed through the existing tools flip automatically; plus facts-draft summary, pack counts, corpus proposal, contradictions and the exact `needs_human` list |
| `plan_images(draft_id)` | Images: plan a draft's image slots — 1 featured + 1 per section (max 5 in-content), each with a deterministic generation prompt = scene + the brand's visual style block. Explicit, never auto-planned; stored in the draft under the per-draft lock |
| `set_image(draft_id, image_id, source, ref, …)` | Images: fulfil one slot with an image the writer generated (`source="ai"`, pass `prompt_used`) or a client photo (`source="client"`). Filename/alt default to the SEO suggestions (alt marked `alt_suggested`); the core stores refs + metadata only, never image bytes |
| `image_check(draft_id, complete?)` | Images: deterministic checker — alt presence/duplication/stuffing/length, filename slug rules, featured keyword + no featured reuse, AI style lock, vision grades. `complete=True` also counts unfulfilled slots as issues. Advisory only: never part of `final_gate` |
| `record_vision_check(draft_id, image_id, content_grade, style_grade, notes?)` | Images: record the WRITER's vision grades (0/1/2 per axis, strict int validation) for a fulfilled image — the judged layer for images, mirroring the Quality Profile's judged axes. A grade of 0 fails that slot until the image is replaced |
| `get_image_manifest(draft_id)` | Images: the WordPress-export-ready media manifest (fulfilled images, plan order, WP field names) + `ready` (all slots fulfilled AND no hard issues; unchecked images appear under `warnings` without blocking) |
| `add_library_image(brand_id, ref, description, tags?, source?, ai_tags?, dimensions?)` | Images: add one image to a brand's own image library (`data/image_library/<brand-id>/`, refs only). The description is what ranking and alt suggestions read. Import flows may pass `ai_tags` + `dimensions` in the same call (validated on the tag tool's path); a re-add passing neither preserves the recorded tags |
| `tag_library_image(brand_id, ref, ai_tags, dimensions?)` | Images: record the HOST MODEL's AI tags for one library image — the fixed shape {subjects (≤8 short phrases), scene (≤12 words), objects (≤10), colours (≤6), text_in_image}; an empty tag set is refused. The core validates + stamps (tagged_by="host_model", tag_count) and the tag tokens join the ranking token set, so a thinly described photo becomes findable. Re-tagging replaces the record; the ref must match exactly one entry of that brand |
| `list_library_images(brand_id)` | Images: list a brand's library entries (including `ai_tags` + `dimensions` when recorded) |
| `fulfil_images_from_library(draft_id)` | Images: library-first fulfilment — rank the draft brand's library per slot by token overlap (entry tags + description + AI tags), fulfil above the match threshold (one entry never serves two slots), report the rest as `needs_generation` with style-locked prompts |
| `list_drafts()` | Draft admin: every stored draft (draft_id, brand, status, updated); corrupt files listed as corrupt, never skipped |
| `delete_draft(draft_id, confirm?)` | Draft admin: delete one draft under the per-draft lock; an APPROVED draft requires `confirm=True` |
| `create_research_pack(brand_id)` | Research pack: create (or version-bump) a brand's pre-approval setup store — sources, proposed items, contradictions, quality rejections. Never touches live config |
| `add_pack_source(brand_id, url, kind, …)` | Research pack: record one gathered source with its legitimacy basis and quality verdict; a reject verdict also lands in `quality_rejections[]`. Fetched `pages` ride on own sources (the server never fetches) |
| `build_pack_proposals(brand_id)` | Research pack: deterministic proposals from accepted own sources — one brand_rule item + one fact item per needs-evidence site claim |
| `propose_pack_item(brand_id, kind, payload, provenance, reference?)` | Research pack: propose one item (brand_rule / fact / style_candidate / persona_frame / corpus_choice) with provenance; a fact without `source_url` is refused; competing facts spawn a contradiction |
| `get_pack(brand_id)` | Research pack: the full pack + counts by status/kind + open-contradiction count (the review checklist in one call) |
| `decide_pack_item(brand_id, item_id, decision, edited_payload?, note?)` | Research pack: the owner's MANUAL per-item decision — approve / edit (edited payload stored, original kept for audit) / reject. No bulk approval exists, by design. Items locked by an open contradiction cannot be decided |
| `resolve_contradiction(brand_id, contradiction_id, chosen_item_id, note?)` | Research pack: close a contradiction by choosing one item — chosen becomes approved, the rest are rejected and survive only as pack history |
| `apply_pack(brand_id)` | Research pack: apply approved/edited items into live stores through the existing save paths. Invariants: proposed/rejected never apply; open-contradiction items blocked; reference material refused (brand drafts fail at the `draft_to_brand_yaml` gate); style items forced `store_excerpts=False`; idempotent; returns exactly which ids landed |
| `create_corpus_profile(name, samples)` | Corpus registry: build + persist a Style Profile for a NEW corpus from pasted samples (≥3 distinct, non-empty). Excerpts are forced OFF server-side — the tool takes no `store_excerpts` parameter; corpus profiles hold statistics + traits only. Returns the measured `word_count`/`sample_count` to hand to `register_corpus` |
| `register_corpus(corpus_id, version, title, kind, profile_id, legitimacy_basis, word_count, sample_count?)` | Corpus registry: register a genre corpus ONCE at system level; a corpus without a recorded legitimacy basis cannot register; re-registering an id is an error (a revision is a new version decision) |
| `list_corpora()` / `get_corpus(corpus_id)` | Corpus registry: list / read registered corpora |
| `set_corpus_choice(brand_id, corpus_id)` / `get_corpus_choice(brand_id)` / `clear_corpus_choice(brand_id)` | Corpus registry: a brand's corpus choice (corpus id + pinned version + resolved profile). `assemble_context` defaults a brand's style profile from its choice when the brief names the brand without an explicit `style_profile_id` — explicit choice always wins. Clear removes BOTH the choice record and the stamp a pack apply leaves on the brand draft; clearing an unset brand is a no-op report, not an error |
| `import_taste_articles(brand_id, file_refs)` | Taste layer: import finished article files as PENDING entries — candidates only, never learned from until the owner marks them |
| `mark_article_verdict(brand_id, verdict, draft_id? / file_ref?, edited_text?, note?)` | Taste layer: the OWNER's verdict on one article — `approved_unchanged` or `edited` (edited requires the edited text; BOTH versions are kept, the diff is the signal). Only the owner gives verdicts |
| `build_taste_profile(brand_id)` | Taste layer: with ≥3 verdicted articles, distil them into the brand's taste profile (`<brand-id>-taste-voice`). `store_excerpts=True` is legitimate here and only here — the samples are the owner's own articles |
| `get_taste_status(brand_id)` | Taste layer: verdict counts + whether a taste profile exists and what it was built from |
| `create_seo_profile(draft_id, keyword, search_context, brand_id?)` | SEO five-source (Round 1): create ONE research profile per draft. Brand is pinned from the draft's own brief (a brandless brief is refused; a mismatching `brand_id` is a structured error). `search_context` = {market, language, location, device, searched_at} — all five required, no defaults invented. Starts in state `collecting`; nothing here writes to facts / brand / corpus / taste stores |
| `get_seo_profile(draft_id)` | SEO five-source: the full profile (keyword, context, state, sources with read states + read_events, analysis claims with provenance flags, shortfall exception) plus the `sufficiency` report: read_count (read + partial) / full_read_count vs the required 5, shortfall standing, exception recorded, proceed authorised |
| `add_seo_source(draft_id, url, observed_rank, title?, observed_at?)` | SEO five-source: record one ORIGINAL observed result. Ranks 1–5 unique among originals, never overwritten; entry is always `manual`; the source starts `pending` — a URL alone never counts as read |
| `record_source_read(draft_id, source_id, read_status, reason?, content_truncated?, page_type?)` | SEO five-source: record an explicit read outcome (read / partial / failed / excluded) with reason + truncation flag. Every recording appends to the source's `read_events` — a failed-then-read history is kept whole. `page_type` (article / product / category / video / tool / other) is assigned at read time (Round 2); omitting it leaves a set value alone |
| `add_replacement_source(draft_id, replaces_source_id, url, observed_rank, reason, title?, observed_at?)` | SEO five-source: record a replacement for an unusable source. The ORIGINAL record is never rewritten; the replacement is a new record linked via `replaces_source_id`, with the rank it was observed at and the reason it exists |
| `save_seo_analysis(draft_id, claims)` | SEO five-source: save the analysis as provenance-checked claims — `sourced` claims must cite ≥1 recorded source; unknown ids refused; a claim citing an unread (not read/partial) source is stored WITH a flag and reported in `flagged_claim_ids`, never silently treated as analysed. Saving moves the profile to `analyzing`; re-saves version the analysis and keep the previous one in `analysis_history` |
| `record_shortfall_exception(draft_id, reason)` | SEO five-source: record the owner's deliberate decision to proceed with fewer than 5 read sources. Reason required; refused with 0 read sources. The exception stores the read count at confirmation and does NOT clear the reported shortfall. Recordable up to the outline gate (states collecting → outline_review); once writing begins it is refused |
| `save_seo_direction(draft_id, primary_intent, secondary_intents?, rationale_claim_ids?)` | SEO five-source (Round 2): propose the article's direction from the saved analysis. ONE primary intent is required — competing intents are the user's choice, not an average (A5); rationale claims must resolve to the current analysis. Moves analyzing → direction_review; a proposal only, until the user decides |
| `record_direction_decision(draft_id, decision, note?)` | SEO five-source (Round 2): record the USER's decision on the direction — `approved` (→ outline_review, decision stays readable) or `rejected` (direction kept in history with its decision, profile back to analyzing). The core never approves its own proposal |
| `propose_seo_outline(draft_id, sections)` | SEO five-source (Round 2): propose a versioned outline. Gate: direction approved AND `sufficiency.proceed_ready` (5 counted reads — the partial-counts rule is the `PARTIAL_READS_COUNT_TOWARD_REQUIRED` policy constant — or a recorded exception). Sections go through `make_outline` into the DRAFT (the writing artefact; no second outline store); the profile mirrors the normalised sections per version. A pending proposal is superseded by a re-proposal; an approved version stays current until a newer one is approved |
| `approve_seo_outline(draft_id, version)` | SEO five-source (Round 2): approve ONE proposed outline version (user's act, timestamped). The previously approved version becomes superseded — exactly one approved-current exists at any time; superseded/invalidated versions can never be approved. Moves the profile to `writing` and stamps the version with the draft's current `gates.content_sha` for Round 3's export check |
| `reopen_seo_analysis(draft_id, reason)` | SEO five-source (Round 2): the ONLY way back once approvals exist (A6) — direct source/analysis mutation in direction_review / outline_review / writing stays refused. The direction and every live outline version are marked `invalidated` and kept whole in history with reason + timestamp; the profile returns to `analyzing`. Round 3: reopening from `reviewing` also invalidates the review records; from `exported` it stays refused |
| `record_seo_review(draft_id, items, verdict, expert_review?)` | SEO five-source (Round 3): record ONE review of the written article — checklist items {key, label, status ok/issue/na, mandatory, note?}, verdict approved / changes_requested, and an optional expert_review {required, status, reviewer?} for specialist claims (`done` must name the reviewer; who + when are recorded). The review BINDS to the approved outline version + the current `gates.content_sha` fingerprint: a later edit or a newer approved outline makes it stale, computably. First review moves writing → reviewing |
| `get_seo_export_readiness(draft_id)` | SEO five-source (Round 3): THE export-readiness decision for a draft with an SEO profile — {ready, reasons[]}, every unmet condition named (state / outline / fingerprint / review currency / open mandatory items / expert pending / verdict / the F1 gate codes, reused from the exporter's own check). Readiness is computed, never a stored state. `export_draft_to_wordpress` enforces exactly this decision for SEO drafts (refusal: reason `seo_not_ready` + the reasons list, before any WP write) and stamps the profile `exported` on success |
| `export_core_data()` | Backup: snapshot ALL local stores into one timestamped zip under `backups/` with a manifest (per-store counts, per-file sha256); credential-pattern files are excluded and recorded in the manifest |
| `list_backups()` | Backup: list archives (newest first) with manifest summaries; an unreadable archive is listed as corrupt, never skipped |
| `restore_core_data(archive, confirm=False, dry_run=False)` | Backup: restore an archive into the live stores — missing files restored, identical skipped, differing only with `confirm=True`; `dry_run=True` reports the full plan and writes nothing; any traversal entry refuses the whole restore, credential-pattern/sha-mismatched/invalid-JSON entries are refused per file and named; the current stores are snapshotted as `pre-restore-*` before the first write |
| `set_wordpress_target(brand_id, site_url, username, post_type?, default_category_id?)` | WordPress exporter (P3): record a brand's NON-SECRET delivery target (site base URL, WP username, REST post type, optional category). The Application Password is never accepted or stored here — see the WordPress section below |
| `get_wordpress_target(brand_id)` | WordPress exporter: the stored target + `credentials_configured` (boolean only; the password is never returned) |
| `export_draft_to_wordpress(brand_id, draft_id, force=false)` | WordPress exporter: upload the draft's images per its media manifest (featured → `featured_media`, alt/caption/description set), create the post with status **"draft" — always; there is no publish path**, record the export on the draft (post id + edit link). A second export is refused unless `force=True` |
| `sync_wordpress_edits(brand_id? / draft_id?)` | Edit-feedback loop (quality uplift): READ-ONLY (GET only) — fetch each exported post's current content, diff it against the exported draft, and record a changed pair in the taste layer as an "edited" verdict (both versions kept; provenance = WP post id + site). Idempotent per post + content hash; a further edit supersedes the entry |

### Setup Assist — AI-assisted new-brand setup

Smith's direction (22 Nov 2026): the AI does most of the new-brand
setup work; the human only decides. `prepare_brand_setup` is the
orchestration over pieces that already existed (Brand Builder,
facts profiles, research packs, corpus registry, persona/visual
schema, WP targets): fetch → brand draft → facts draft → pack +
proposals → corpus proposal → persona candidates → dossier, in one
call. The hard rules are the point:

- **Nothing goes live without the existing approval paths.** The
  brand stays a draft; the facts stay a draft inside the setup
  record (`data/brand_setups/<brand-id>.json`) until a human saves
  them via `save_facts`; pack items are all `proposed` (per-item
  manual approval only — no bulk approve, no auto-apply); the
  corpus is proposed, never chosen; persona candidates are never
  saved as a persona.
- **Contradictions are blockers, never silently resolved.** Two
  prices for one pack size (or two delivery promises) land in the
  dossier's `contradictions[]`, the facts step reads `blocked`,
  and the contested values are withheld from the facts draft.
- **The checklist derives from live state.** `get_brand_setup`
  recomputes every step from the stores, so finishing a step
  through the existing tools (`save_facts`, `set_corpus_choice`,
  `decide_pack_item`, `set_wordpress_target`…) flips it to `done`
  with no bookkeeping in the setup record.
- Re-running prepare is idempotent-ish: the pack is reused (never
  version-bumped), proposals are built only when the pack has no
  brand_rule item yet, and drafts are refreshed from the new fetch.
- **A re-prepare never silently overwrites the human's review**
  (review round 3, S2). Generation is deterministic, so any field
  in which the stored brand draft differs from the fresh
  generation counts as a human edit: it is preserved (banned_terms
  above all), and the dossier's `preserved_fields` lists every
  preserved field path.
  Inferred fields (e.g. the visual palette from the site's CSS)
  carry provenance `setup_assist`.

### Article images (hybrid library-first + style lock + vision grades)

Smith's decisions (22 Nov 2026): articles get a featured image plus
in-content images with SEO-standard alt text and descriptions; the
source model is **hybrid** — the client's real photos first, AI
generation for what no photo fits; generated images must share **one
consistent per-brand look**; and delivery is a WordPress draft, so
the feature ends in a media manifest the future WP exporter uploads.

The split mirrors text exactly: **generation happens outside the
core** (the host/writer makes or selects the image files); the core
plans, checks and packages (`content_core/images.py`, pure):

- **Plan.** `plan_images` creates one featured slot (16:9, 200KB
  advisory budget) + one in-content slot per section (4:3, 150KB),
  capped at 5 in-content. Prompts are deterministic templates — no
  LLM — assembled as scene + style block.
- **Per-brand image library.** Each brand/client has its own
  refs-only library at `data/image_library/<brand-id>/index.json`
  (same stored-profile discipline as the other stores: validated
  ids, schema-checked entries, atomic writes; the directory is
  gitignored live data). Entries: `{id, ref, source: client|ai,
  description, tags[], added_at}` plus optional `ai_tags` and
  `dimensions` (below). Entries without them work exactly as
  before.
- **AI image tags.** `tag_library_image` records the host model's
  structured read of a photo — `{subjects, scene, objects,
  colours, text_in_image}` under a fixed discipline (short noun
  phrases, capped counts, empty tag sets refused), stamped by the
  core (`tagged_by="host_model"`, `tagged_at`, `tag_count`;
  re-tagging replaces the record, only the count survives). The
  split mirrors vision checks: the model sees, the core referees
  the record. `add_library_image` accepts the same payload so an
  import flow tags in one call.
- **Library-first fulfilment.** `fulfil_images_from_library` ranks
  entries by normalised token overlap (entry token set = tags +
  description + AI-tag tokens, unioned at equal weight, vs the
  slot's heading/intent + brief keyword) and fulfils slots scoring
  ≥ 0.35, client entries winning ties; one entry never fulfils two
  slots in a draft. Alt text for a library fulfilment is suggested
  from the entry description and marked `alt_suggested`. Slots with
  no fit come back as `needs_generation` with their generation
  prompt.
- **Brand visual style.** An optional `visual` section in the brand
  YAML / brand draft — `{style_summary, palette[], medium
  (photo|illustration|mixed), mood, composition, avoid[]}` — renders
  as one style block appended **verbatim** to every generation
  prompt; that identical block is what makes a generated set look
  like one set. Mộc Brew's shipped YAML carries a seeded value;
  brands without a section simply get no style block (loader
  tolerates absence). A fulfilled AI image records the full prompt
  actually used (`prompt_used`); `image_check` raises
  `style_not_locked` when it is missing or does not carry the
  brand's current block, and `style_block_mismatch` when two AI
  images in one draft were locked to different blocks. Library and
  client images need no prompt.
- **SEO metadata checks.** Hard issues: missing/duplicate
  (diacritics-insensitive) alt, keyword-stuffed alt (the brief
  keyword repeated, or an alt made only of the keyword), alt
  > 200 chars, non-slug filename, featured filename without the
  keyword, the featured ref reused in-content, fulfilments outside
  the plan. Flagged: alt 125–200 chars, featured without caption,
  missing media description, local files over budget or not
  webp/jpg/png.
- **Vision check (judged layer).** The core cannot see images; the
  host model can — the Quality Profile's judged-axes pattern.
  `record_vision_check` stores the writer's grades
  `{content_grade, style_grade, notes, checked_at}` (0/1/2: 2 =
  matches intent and brand style, 1 = usable with reservations,
  0 = wrong content / off-style) on the fulfilled image — client and
  library photos included (for a real photo, the style grade judges
  fit with the brand look, not the photo's fault). In a complete
  check, an ungraded image is flagged `vision_unchecked`; a grade
  of 0 is a hard issue (`vision_content_mismatch` /
  `vision_style_mismatch`) whose remedy is regenerating or
  replacing that one image; grade 1 passes with its notes surfaced
  as a `vision_reservations` flag. The core enforces the record,
  not the seeing.
- **Manifest + boundary.** `get_image_manifest` returns fulfilled
  images in plan order as `{role, section_id, ref, filename,
  alt_text, caption, description}`; `ready` requires all slots
  fulfilled and no hard issues (vision-unchecked appears under
  `warnings` only). The image layer is **advisory in this round**:
  it is NOT wired into `final_gate`'s verdict — a draft's gate
  result is exactly what its text earns. The LangGraph flow is
  unchanged. Demo: `.venv/bin/python demo_images.py`.

### Research pack — setup with approval (core side)

The setup-phase feature from `specs/research-config-feature.md`,
built on the core side (the Astryx checklist UI is P3): AI-assisted
research gathers sources and drafts a brand's configuration, and
the owner approves **item by item** before anything goes live.
The only new artefact is the **research pack** — one JSON per brand
at `data/research_packs/<brand-id>.json` (loaders house pattern:
validated ids, atomic writes, schema-checked on load; gitignored
live data):

- `sources[]` — every gathered source with kind (own / competitor /
  genre / factual), its legitimacy basis and the quality gate's
  verdict; rejections carry their reason and are mirrored into
  `quality_rejections[]`.
- `items[]` — proposed configuration (brand_rule / fact /
  style_candidate / persona_frame / corpus_choice), each with
  payload + provenance `{source_url, note}` and status
  `proposed → approved | edited | rejected → applied`. A fact item
  without a `source_url` cannot exist (US-1). An edit stores the
  edited payload and keeps the original for audit.
- `contradictions[]` — two fact items competing for the same field
  with different values spawn a contradiction; both items lock
  (no decide, no apply) until `resolve_contradiction` picks one —
  the loser is rejected and survives only as pack history (US-2).

`apply_pack` is the only door into live configuration, and it goes
through the EXISTING save paths (brand draft, facts profile, style
profile, persona file, corpus choice). Its invariants are pinned
by tests: only approved/edited items land; the applied-id set
cross-checks equal to the approved set; re-applying is idempotent;
reference material is refused (brand drafts fail at the
`draft_to_brand_yaml` conversion gate, US-3); style items are
saved with `store_excerpts=False` whatever the payload asked
(US-4). There is deliberately **no bulk-approve tool** — Smith's
standing rule is manual per-item approval in admin.

### Corpus registry — genre corpora at system level

Genre corpora are registered **once, at system level**
(`data/corpus_registry/`), never copied per brand: id, version,
title, kind, the style profile the corpus feeds, its legitimacy
basis (registration refuses a corpus without one) and measured
size. The two trial corpora are the first entries:
`mien-tay-van-hoa` v1 (press, 39,619 words) and `mien-tay-van-hoc`
v2 (literature, 95,507 words). Each brand **chooses** a corpus and
pins its version (a pack `corpus_choice` item, or
`set_corpus_choice` directly); `assemble_context` then defaults
the brand's style profile from the choice when a brief names the
brand without an explicit `style_profile_id`. The pin is the
control: a shared-corpus update never silently shifts a brand —
the explicit brief choice always wins over the default.

### Taste layer — the learning loop, core side

The system gets smarter only through the owner's verdicts, never
silently. Per brand, `data/taste/<brand-id>.json` holds finished
articles the owner has marked: `approved_unchanged`, or `edited`
with BOTH versions kept (the owner's edit is the highest-value
signal there is). Imported-but-unmarked articles sit at `pending`
and teach nothing. With ≥3 verdicted articles,
`build_taste_profile` distils them (via `style.build_profile`)
into `<brand-id>-taste-voice`, attachable through
`assemble_context` like any style profile. Excerpts are stored
for taste profiles — legitimately, and only here: the samples are
the owner's OWN articles, not a third-party corpus (every other
profile path forces `store_excerpts=False`).

### Local data backup

`export_core_data` (MCP tool, or
`.venv/bin/python scripts/export_core_data.py`) snapshots ALL
local stores — personas, brand files/drafts, facts, style/quality
profiles, image libraries, research packs, corpus registry +
choices, taste stores, drafts — into ONE timestamped zip under
`backups/`, with a `manifest.json` inside (per-store counts,
per-file sha256) so an archive can be verified without trusting
it. Files matching credential patterns are excluded and recorded
in the manifest: the stores hold no credentials by design, and a
planted one is reported, never silently archived. `list_backups`
reads the manifests back. `restore_core_data` is the return path:
the archive is validated before anything is planned (a traversal
or absolute entry path anywhere refuses the whole restore; a
credential-pattern entry, a sha256 mismatch against the manifest,
or JSON that does not parse is refused per file and named), then
planned per file — missing files are restored, identical files
skipped, differing files restored only with `confirm=True`
(`dry_run=True` returns the plan and writes nothing) — and the
current live stores are first snapshotted as a `pre-restore-*`
archive, so a restore is itself reversible.

### WordPress draft exporter — delivery as drafts (P3)

The delivery channel Smith settled (pmdecisions 2026-11-22):
finished articles are posted as **drafts** into the client's own
WordPress site; the client reviews and publishes. The exporter
(`content_core/wordpress.py`, stdlib urllib) makes "never publish"
structural, not a setting:

- `build_post_payload` hard-codes status `"draft"` and ignores any
  status passed to it; there is no publish code path.
- The per-brand **target store** (`data/wordpress_targets/<brand-id>.json`,
  gitignored) holds ONLY non-secret config — site base URL, WP
  username, REST post type, optional default category — and its
  validator keeps only those fields, so a hand-edited target file
  cannot smuggle in a status or a credential.
- The **Application Password** is read at export time only, from
  the environment variable `BRANDFORGE_WP_PASSWORD_<BRAND>`
  (brand id upper-cased, non-alphanumerics as `_`) or the local
  secrets file `data/wordpress_secrets.json` (JSON
  `{brand_id: "app password"}`; path overridable via
  `CONTENT_CORE_WP_SECRETS_FILE`; gitignored, and its name matches
  the backup exporter's credential-pattern exclusion). It is never
  stored in any store, never logged, and every error is scrubbed
  through `wordpress.redact` — password, wire form, base64 token
  and full Authorization header — before it can surface.
- Export flow: media manifest → upload each image (bytes from the
  manifest's local ref; URL refs are refused) → set alt/caption/
  description on each media item → featured image becomes
  `featured_media` → post body built from the draft's sections
  with in-content images at their sections → create post → record
  `wordpress_export` {site, post id, edit link, media map} on the
  draft. **Duplicate rule:** a draft with an export record is
  refused; `force=True` deliberately creates a fresh post and
  replaces the record. Tests run against a local mock WP REST
  server (`tests/test_wordpress.py`): auth header shape, media +
  alt, draft-only payload, 401/404 handling, redaction.

### Start the server / test it

```bash
cd ~/workspace/projects/content-core
.venv/bin/python mcp_server.py                    # stdio (default) — an MCP client launches this
.venv/bin/python mcp_server.py --transport http   # streamable HTTP on 127.0.0.1:8765 (localhost only)
.venv/bin/python test_mcp_client.py     # stdio: initialize + tools/list + 24 end-to-end checks
.venv/bin/python test_mcp_http.py       # http smoke test: start server, list tools, call get_quality_profile
.venv/bin/python run_model_golden.py    # model-written golden set (MCP mode regression)
.venv/bin/python -m pytest -q           # full test suite (core + MCP + craft + fact/linter + model golden + quality)
```

Register it with an MCP client as a stdio server with command
`.venv/bin/python` and args `["mcp_server.py"]` (cwd = this project).

## Chạy như tool trong app AI local (Gemini / ChatGPT / Muse)

Smith's packaging decision (21 Nov 2026): ship the core **first as a
local tool that runs inside the AI apps the user already has** — Gemini,
ChatGPT (local/desktop apps) and Muse. The concept is the MCP mode
above, generalized:

```
┌─────────────────────────────────────────────────────────────┐
│  Host app (Gemini / ChatGPT / Muse)  =  the WRITER        │
│  its model reads the assembled context and writes prose     │
└──────────────┬──────────────────────────────────────────────┘
               │ MCP (stdio, or streamable HTTP on localhost)
┌──────────────▼──────────────────────────────────────────────┐
│  content-core MCP server  =  HANDS + REFEREE                │
│  retrieval · context assembly · brand/fact/craft/SEO checks │
│  · THE gate (gates.py) · Quality Profile · draft state      │
└─────────────────────────────────────────────────────────────┘
```

**Swapping the writer changes nothing else.** Later, an API provider
(OpenAI/Claude/Gemini API) can replace the host app's model — via the
`Writer` interface in-process, or by calling the same MCP tools — and
the gates, checkers and Quality Profiles behave **identically**, because
none of them know or care who wrote the prose. That is the point of the
core: the referee is writer-agnostic.

### stdio config (Gemini CLI / Muse-style `mcpServers` JSON)

Most local apps take the same shape — a named server with a command and
args (paths absolute, pointing at this project):

```json
{
  "mcpServers": {
    "content-core": {
      "command": "/home/<you>/workspace/projects/content-core/.venv/bin/python",
      "args": ["/home/<you>/workspace/projects/content-core/mcp_server.py"],
      "cwd": "/home/<you>/workspace/projects/content-core"
    }
  }
}
```

(Gemini CLI uses the same `mcpServers` block in its settings file;
Claude-style desktop apps use the same JSON in their MCP config. The
per-app menu steps to paste it are the apps' own UI — see "tested vs
documented" below.)

### HTTP mode (apps that connect over HTTP instead of stdio)

```bash
.venv/bin/python mcp_server.py --transport http
# streamable HTTP at http://127.0.0.1:8765/mcp
# host/port overridable: CONTENT_CORE_HTTP_HOST / CONTENT_CORE_HTTP_PORT
```

**There is NO auth in v1 — the HTTP server binds loopback only, and
that is now enforced, not just documented:** a non-loopback
`CONTENT_CORE_HTTP_HOST` makes the server refuse to start unless
`CONTENT_CORE_ALLOW_NONLOCALHOST=1` is set explicitly. Do not bind it
to a LAN/public interface. **ChatGPT** connects
to MCP servers as *remote* servers over HTTPS, so using this local
server from ChatGPT requires exposing it through a tunnel **the user
controls** (e.g. Cloudflare Tunnel) and pointing ChatGPT at the
tunnel's HTTPS URL + `/mcp` path:

```bash
cloudflared tunnel --url http://127.0.0.1:8765
# -> https://<random>.trycloudflare.com/mcp   (register this URL in ChatGPT)
```

Treat a tunnel URL as a credential while it is live: anyone who has it
can call the tools (drafts are local files, but the tools can read and
write them). Prefer stdio apps (Gemini CLI / Muse) for local use;
use a tunnel only when the app leaves no other route.

### Tested vs documented-from-spec

- **TESTED in this repo**: the stdio transport end-to-end
  (`test_mcp_client.py`, 24 checks, official SDK client) and the HTTP
  transport smoke test (`test_mcp_http.py`: real server on a test port,
  tools/list + `get_quality_profile` over streamable HTTP).
- **Documented from the apps' public MCP specs, NOT clicked through**:
  the exact per-app UI steps for registering the server in Gemini /
  ChatGPT / Muse, and the ChatGPT-over-tunnel flow. The config shapes
  above follow those specs; the first real registration in each app
  should be verified by hand once.

### The workflow an agent follows for ONE brief

1. `validate_brief` — if `refused_claims` is non-empty, those claims are never written.
2. `research_facts` — pull only the facts this brief triggers.
3. `assemble_context` — read the ordered context; write under those rules.
4. `start_draft` → `make_outline` (propose an outline or take the template; store it in the draft).
5. For each section: **Muse writes the prose** → `save_section`.
6. `brand_check` + `fact_check` (+ `seo_check`) on the draft.
7. `final_gate` — on FAIL, rewrite **only** the listed failing sections, `save_section` them, call `final_gate` again (max 2 rewrite cycles, tracked in the draft).
8. On PASS (`status="approved"`): deliver the draft from `get_draft`.

### Honest limitation of MCP mode

This mode writes **inside a Muse session** — it is not an autonomous
backend: no session, no writer. That is the point for now (the best available
model writes, the deterministic core keeps it honest). The later product
swaps in an API Writer implementing the same `Writer` interface / tool
contracts; **the gates do not change** when that happens.

## Layout

```
content_core/  state.py loaders.py writer.py linter.py fact_check.py seo.py
               graph.py golden.py runner.py craft.py model_golden.py
               gates.py   (THE final gate: hard + craft + quality layers —
                           the single rule both the graph and MCP call)
               quality.py   (Quality Profile: 7 axes, presets, floors,
                             measured/judged axis scoring)
               style.py   (Style Extraction: fingerprint / build_profile / style_match)
               brand_extract.py   (Brand Builder: kit / voice suggestions /
                                   vocabulary / site claims / draft / compare)
               images.py   (article images: plan / SEO metadata checks /
                            library ranking / style lock / vision grades /
                            WP manifest — pure, no generation inside)
               research.py   (research pack: sources / items / contradiction
                              detection + resolution / apply invariants)
               taste.py   (taste layer: owner verdicts / taste profile build)
               backup.py   (local data backup: store snapshot zip + manifest)
               wordpress.py   (WordPress draft exporter: secrets handling
                             with redaction, REST client, draft-only
                             payload + content builders)
data/          persona_linh.json brand_mocbrew.yaml facts_lorebook.json
               craft_library.json exemplars.json domain_glossary.json   (Layer 4)
               model_golden.json   (model-written drafts + expected scores, P2a)
               style_samples_mocbrew.json   (3 original samples for the default profile)
               style_profiles/mocbrew-blog-voice.json   (built-in Style Profile)
               style_profiles/cao-nguyen-moc-blog-voice-dd5678.json   (calibration site's profile)
               facts_profiles/cao-nguyen-moc.json   (per-brand fact base, calibration brand)
               brand_drafts/   (Brand Builder drafts, gitignored)
               quality_profiles/   (custom Quality Profiles, gitignored)
               image_library/   (per-brand image libraries, gitignored)
               research_packs/   (research packs, gitignored)
               corpus_registry/   (system-level genre corpora, tracked)
               corpus_choices/   (per-brand corpus choices, gitignored)
               taste/   (per-brand taste stores, gitignored)
               wordpress_targets/   (per-brand WP delivery config —
                             non-secret only, gitignored)
               wordpress_secrets.json   (WP Application Passwords,
                             local only, gitignored — NEVER tracked)
               persona_an.json   (trial persona: An, 25, Châu Đốc)
tests/         test_linter.py test_fact_check.py test_gates.py test_golden.py
               test_mcp_server.py test_craft.py test_model_golden.py test_style.py
               test_brand_extract.py   (Brand Builder: kit/voice/claims/drafts/compare/MCP)
               test_quality.py   (Quality Profile + unified gate parity)
               test_audit_fixes.py test_backlog_fixes.py   (audit/backlog rounds)
               test_calibration_fixes.py   (calibration-round fixes)
               test_core_closing.py test_core_completion.py   (core-completion round)
               test_review_fixes.py   (2026-11-22 code-review round)
               test_images.py   (article images: plan/check/library/vision/MCP)
               test_core_completion2.py   (core-completion round 2: research
               pack invariants / corpus registry / taste layer / draft
               admin / backup)
               test_wordpress.py   (WP draft exporter: mock WP REST
               server — auth shape / media+alt / draft-only / redaction)
               fixtures/dinhcao/ fixtures/som/   (two fictional coffee sites, HTML)
               fixtures/jsshell/ fixtures/jsshell2/   (JS-shell site fixtures)
calibration/   caonguyenmoc-report.md + work/   (the Cao Nguyên Mộc calibration
               run: corpus, samples, writer trial; work/README.md says which
               files are fixtures and which are local-only raw evidence)
evaluation/    round-1..8 reports + SUMMARY.md + probes/   (the 8-round
               evaluation and its regression probes, all with exit codes)
mcp_server.py        MCP server (stdio + streamable-http, mcp SDK, 68 tools) — the host app's model is the writer, tools are the hands + referee
test_mcp_client.py   end-to-end MCP client test over stdio (24 checks)
test_mcp_http.py     MCP HTTP smoke test (server on a test port, tools/list + get_quality_profile)
fetch_sample.py      CLI helper: URL -> plain text for Style Extraction (server stays offline)
fetch_site.py        CLI helper: site URL -> pages + html_meta for the Brand Builder (server stays offline)
build_default_style_profile.py   rebuilds data/style_profiles/mocbrew-blog-voice.json
demo_mcp_e2e.py      end-to-end demo: Muse-written product draft through all gates
demo_mcp_e2e_v2.py   same brief rewritten with Layer 4 craft (craft 85 -> 100)
demo_brand_builder.py   Brand Builder demo on the fixture sites (draft + refusal + compare)
demo_images.py       article-images demo: library-first fulfilment + style lock + vision grades + WP manifest (isolated temp stores)
scripts/export_core_data.py   CLI for the local data backup (same code path as the export_core_data tool)
backups/             timestamped core-data archives from export_core_data (gitignored)
drafts/              draft state JSON files created by the MCP tools (gitignored)
run_golden.py  run_model_golden.py  requirements.txt
```
