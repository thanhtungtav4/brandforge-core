# Evaluation Round 6 — Brand Builder & Fetch Layer: Site-Archetype Evaluation

**Scope:** `fetch_site.py`, `fetch_sample.py`, `content_core/brand_extract.py`
**Method date:** 2026-11-22 · **Codebase:** commit `8215b8c` (213 tests green) · **Mode:** READ-ONLY — no core code, tests, or data were modified. Fixtures live in `/tmp/r6/sites/`, the runner in `/tmp/r6/build_and_run.py`, raw output in `/tmp/r6/results.json`, ground truth in `/tmp/r6/ground_truth.json`.

## Method

Six fixture sites were authored in `/tmp` with **known ground truth** (brand name, logo, colors, fonts, product names, claim sentences, voice category, and per-sentence word counts, from which prose median/p90 were computed independently of the code under test). Each site was run through the real pipeline — `fetch_site()` over `file://` → `extract_brand_kit` / `suggest_voice_rules` / `extract_vocabulary` / `find_site_claims` / `build_brand_draft` — and every extracted field was scored against ground truth.

| # | Archetype | Fixture |
|---|-----------|---------|
| A1 | Classic WordPress-like blog+shop, server-rendered, `sitemap.xml` + `robots.txt`, one sitemap-only orphan page | `/tmp/r6/sites/a1/` |
| A2 | Plain static HTML brochure, **no sitemap**, name only in repeated title segments, no `theme-color`, no OG tags | `/tmp/r6/sites/a2/` |
| A3 | JS-shell SPA: products in a plain `<script>` blob, one product + one claim **only in ld+json**, prerendered blog page, sitemap **index** (2 children) via `robots.txt` | `/tmp/r6/sites/a3/` |
| A4 | Messy traps: 3 brand-name variants (og / title / legal footer), header vs footer vs alt-text logos, `theme-color` absent, fonts via `@import` + `font-family` + `var(--font-main)`, brand color defined once as CSS var vs sale red `#ff0000` repeated ×6 as literals | `/tmp/r6/sites/a4/` |
| A5 | Heavy claims: 4 asserted phrase-claims, 1 negated claim, 2 substantiated-looking non-phrase claims (ISO cert, 10.000-customer statistic); brand speaks first-person **by name prefix** ("Ong Rừng …") | `/tmp/r6/sites/a5/` |
| A6 | Bilingual: mostly-English copy (first-person *we/you*), Vietnamese product names, one English superlative, one Vietnamese claim | `/tmp/r6/sites/a6/` |

Fetch robustness was probed separately against local HTTP servers (`/tmp/r6/build_and_run.py`): redirect chains + 404 links + duplicated sitemap URLs (Probe A), a sitemap index with **4 children** (Probe B), root `/` vs `/index.html` both listed in a sitemap (Probe C), and a 5.6 MB page with its only product heading at the very end (Probe D). Two supplementary probes prove root causes: A5 subject-hit counts under different short forms, and A1b — A1 rebuilt with a word-rich homepage (>100 words) so the sitemap fallback does not trigger.

## Per-archetype accuracy table

Scoring: ✓ = matches ground truth, ◐ = partial, ✗ = wrong/missed.

| Field | A1 WP-like | A2 Static | A3 SPA | A4 Traps | A5 Claims | A6 Bilingual |
|---|---|---|---|---|---|---|
| Pages fetched | 6/6 (incl. orphan) | 4/4 | 2/2 (blog via sitemap index) | 2/2 | 1/1 | 1/1 |
| Brand name | ✓ | ✓ (title segment) | ✓ | ✗ og variant "TayNguyen Herbs" ≠ legal "Thảo Dược Tây Nguyên" | ✓ | ✓ |
| Logo | ✓ | ✓ | ✓ | ✓ (all 3 traps avoided) | ✓ | ✓ |
| theme-color | ✓ | ✓ (null, correct) | ✓ | ✓ (null, correct) | ✓ | ✓ |
| Colors #1 | ✓ `#2e5339` | ✓ `#5b3a29` | ✓ | ✗ `#ff0000` (sale) beats brand `#7a4a21` | ✓ | ✓ |
| Fonts #1 | ✓ | ✓ | ✓ | ✓ Inter (Playfair #2 ✓; `var(--…)` filtered ✓) | ✓ | ✓ |
| POV suggestion | ✓ first-person | ✓ third-person | ✓ third-person | ✓ first-person | ✗ third-person (GT: first-person as brand name) | ✗ third-person (GT: first-person English) |
| Median band ∋ GT prose median | ✓ 11∈[9,13] | ✓ 15∈[12,18] | ✓ 12∈[10,14] | ✓ 15∈[12,18] | ✓ 13∈[11,15] | ✓ 11∈[9,14] |
| Derived max ≥ GT p90 | ✓ 25≥11 | ✓ 25≥15 | ✓ 25≥13 | ✓ 25≥16 | ✓ 25≥16 | ✓ 25≥12 |
| Vocab `exact`: product recall | ◐ 1 full + 2 truncated (weight suffix dropped) of 3 | ✓ 2/2 | ◐ ld+json full name 0/1; JS-only 0/2 (by design) | ✓ 1/1 | n/a | ✓ 2/2 |
| Vocab `exact`: junk entries | 3–4 of 8 (heading fragments, title-concatenation) | 2 borderline headings | 1 title artefact | 2–3 (fragments + duplicate name variants) | 1 title-concatenation artefact | 2 English heading phrases |
| Claims (in-scope phrase claims) | ✓ 1/1 | ✓ 0, none flagged | ✓ ld+json 1/1 (evidence mangled, see S9); JS-only 0/1 missed | ✓ 0 | ✓ **4/4 asserted; negated correctly excluded** | ✓ VI 1/1; EN 0/1 missed |
| Claims (non-phrase: cert/statistic) | n/a | n/a | n/a | n/a | ✗ 0/2 (ISO cert, "10.000 khách hàng") | n/a |

**Headline aggregates**

- Brand kit scalar fields (name/logo/theme/font-#1): 22/24 correct; color-#1: 5/6.
- Voice: POV 4/6; median band 6/6; derived max 6/6 (all corpora were short-sentence; the long-sentence derivation path remains covered only by the earlier Cao Nguyên Mộc calibration).
- Claims on server-rendered/ld+json text: **precision 7/7, recall 7/7**, including correct rejection of the negated claim. All misses are out-of-scope channels (plain-JS data, English phrases, non-phrase claims) — see severity list for which of those matter.

## Findings (severity-ranked)

### S1 — HIGH — POV "first-person (as brand name)" fails for prefix short forms (A5)
`suggest_voice_rules` returned `third-person` for a brand whose name opens 8 sentences as the speaking subject. Root cause proven directly: `_brand_name_subject_hits(corpus, "Ong Rừng U Minh")` = **0**, because the heuristic's short form is the name's **last** token ("Minh") and the full-name prefix check needs all 4 tokens; with short form "Ong Rừng" the same call returns **8**. The calibration fix assumed self-reference = last word (true for "Cao Nguyên Mộc" → "Mộc"), but a large class of Vietnamese brands self-refers by **prefix** (descriptor words: "Ong Rừng", "Khải Hoàn", "Shan Tuyết"…). The draft for such brands gets the wrong `pov` and a misleading `pov_note` ("the brand speaks about itself in third person").

### S2 — HIGH — Sitemap-index reading silently truncates at 3 documents (Probe B)
With `robots.txt` → sitemap index of 4 children, discovery returned only **p1, p2** (index + 2 children consume the `_MAX_SITEMAP_DOCS = 3` budget); p3/p4 were never fetched and **no warning** appears in the fetch result or draft notes. Real WordPress SEO plugins (Yoast/RankMath) emit indexes with many children (post/page/product/taxonomy splits), so on exactly the A1 archetype at production scale, whole page classes can silently vanish from the corpus.

### S3 — HIGH — Sitemap is fallback-only; sitemap-only pages are missed on healthy homepages (A1b)
The sitemap is consulted only when the homepage yields <3 links or <100 words. A1's orphan page was fetched *only because* its homepage happened to be 95 words. The A1b variant (same site, homepage padded past 100 words, ≥3 links) fetched 5 pages with `sitemap_fallback_used: False` and the orphan — listed in `sitemap.xml` — was **never fetched**. Any page reachable only via the sitemap (orphans, landing pages, unlinked product pages) is invisible on normal sites.

### S4 — MEDIUM — `fetch_site` pages carry no truncation signal (Probe D)
On the 5.6 MB page, the tail product heading was lost beyond the 5 MB read cap, and the returned page dict has **no `truncated` field** (keys: headings/html_meta/js_shell_suspected/text/title/url/word_count) — silent data loss. `fetch_sample.fetch_url` on the same file correctly reports `truncated: true`; the two fetchers are inconsistent.

### S5 — MEDIUM — Color ranking by raw frequency crowns repeated accent colors (A4)
Colors ranked `#ff0000` (count 6, sale badges) above the true brand color `#7a4a21` (count 3). A brand color defined once in `:root{--brand:#7a4a21}` and referenced via `var(--brand)` accrues almost no literal occurrences, while any repeated literal (sale red, link blue, star gold) wins by repetition. The kit's "top colors" — and any downstream theme suggestion — can be actively wrong on real e-commerce pages.

### S6 — MEDIUM — No URL canonicalization: `/` and `/index.html` are different pages (Probe C)
Both were fetched and stored as separate pages with identical content. On a real site whose sitemap lists both forms, the homepage's prose is double-counted in the corpus, skewing the fingerprint (median, rates), vocabulary counts, and example selection.

### S7 — MEDIUM — Redirected pages are recorded under the pre-redirect URL (Probe A)
The chain `/old` →301→ `/newer` →302→ `/final` was followed correctly, but the page is stored as `url = …/old` with `/final`'s title/content. Every `source_url` in the kit, claims list, and examples therefore cites a URL that does not serve that content — provenance evidence rots after any site migration, which is precisely when old URLs redirect.

### S8 — MEDIUM — Bilingual/English sites get silently wrong voice + claims results (A6)
POV and `reader_address` count only Vietnamese pronouns (*mình/tôi/bạn*), so a first-person English site ("We formulate… You can layer…") is labelled `third-person` with `reader_address: None`, and no warning says the heuristics are Vietnamese-only. Likewise the English superlative ("the best moisturizer they have ever used") is invisible to the Vietnamese phrase lists — the draft's claims review comes back clean for a site making an unsubstantiated #1-style claim in English. The Vietnamese claim on the same page *was* caught, so the failure is partial and therefore easy to trust by mistake.

### S9 — LOW–MEDIUM — ld+json text is concatenated into fake "sentences" (A3)
JSON-LD fields are appended as separate text chunks, but `split_sentences` does not treat the chunk boundary as a sentence boundary: the flagged claim's quoted "sentence" is `Nước Mắm Truyền Thống 40 Độ Đạm\n\nNước mắm nhĩ cá cơm, được n…` — product name glued to the description, truncated at 200 chars. The same glue inflates sentence-length stats (A3 corpus stdev 4.95 vs ≈0.6 for the blog prose alone), which feeds the median band and derived max.

### S10 — LOW — Vocabulary `exact` noise and product-name truncation (A1, A4, A5)
- Title-concatenation artefacts: `"Cách Pha Trà Shan Shan Tuyết Cổ Thụ"` (A1 blog title + repeated brand segment), `"Mật Ong Rừng U Minh Ong Rừng U Minh"` (A5) — titles are split on `|` for brand-name detection but **not** before capitalized-run extraction, so both halves fuse into one fake name.
- Heading fragments presented as names: `"Với Mình"` (from "Liên Hệ Với Mình"), `"Thảo Dược"` (run broken by lowercase "Từ").
- Product names lose their digit suffixes: GT `Trà Shan Tuyết Cổ Thụ 100g` → extracted `Trà Shan Tuyết Cổ Thụ`; `Trà Ô Long Shan 250g` → `Trà Ô Long Shan`. The weight — often the SKU-defining part — is dropped because digit tokens break capitalized runs. With the `exact` list capped at 8, junk entries also crowd out real ones (A1).

### S11 — LOW — Vocabulary `use` bigrams cross sentence boundaries and include claim phrases (A5)
Top bigrams include `"ong ong"` (from "…mật ong. Ong Rừng…" — tokens are joined across the whole corpus with no sentence segmentation) and `"ngon nhất"` — a superlative claim phrase suggested as a vocabulary term to *use*, in the same draft whose claims section flags that phrase for evidence review.

### S12 — LOW — `file://` root-relative asset resolution (A1, A3, A5)
Logo URLs from root-relative `src` resolve against the filesystem root under `file://` (`file:///wp-content/uploads/logo-shan.png` instead of the site directory). Harmless over HTTP (origin-root is correct there) and only affects the fixture/test channel, but it means kit evidence gathered in `file://` test runs points at non-existent paths.

### Verified strengths (no action needed)
- A1/A2 archetypes are near-perfect end-to-end (kit, voice, vocab, claims).
- All three A4 logo traps avoided: header strong-signal logo won; footer decoy and the alt-text blog thumbnail were correctly ignored; absent `theme-color` correctly reported as null (A2, A4).
- CSS-var font filtering works (A4): `var(--font-main)` never appears as a font; `@import`-only Inter still surfaced via `font-family` declarations.
- Claim negation handling is exact (A5): "không nhận là ngon nhất, cũng không phải rẻ nhất" produced **no** claim entry, while all 4 asserted claims were caught with correct phrases.
- ld+json extraction works as designed (A3): the ld+json-only claim was found; JS-shell detection + sitemap-index fallback fired correctly.
- Fetch robustness basics hold: redirect chains followed, 404s (link and sitemap) recorded in `errors` without aborting the crawl, duplicate sitemap URLs de-duplicated (Probe A).

## Improvement suggestions (no fixes applied)

1. **POV short forms (S1):** try several self-reference candidates — full name, last token, and the first 1–2 tokens — and take the maximum hit count; report which form matched in `pov_note`.
2. **Sitemap discovery (S2, S3):** always union sitemap URLs into the crawl queue (de-duplicated, priority-sorted, still bounded by `max_pages`) instead of fallback-only; raise or parameterize the sitemap-document budget and surface a `sitemap_truncated` warning (fetch result + draft `review_notes`) when the budget cuts children off.
3. **Truncation parity (S4):** add a per-page `truncated` flag in `fetch_site` (the read already knows the cap) and a review note when any page was truncated.
4. **Color evidence weighting (S5):** score colors by context, not raw count — boost colors defined in `:root` custom properties (especially `--brand`/`--primary`-named), colors matching `theme-color`, and colors used in header/logo-adjacent elements; discount near-identical repeats within repeated components (badges, stars, buttons).
5. **URL canonicalization (S6):** normalize `index.html`/`index.php` and trailing-slash variants to one canonical page key before dedup.
6. **Redirect provenance (S7):** store the final response URL (`response.geturl()`) as the page's `url`, keeping the requested URL as `requested_url` for traceability.
7. **Language awareness (S8):** detect dominant corpus language; when it isn't Vietnamese, add a `review_notes` warning that POV/reader/claims heuristics are Vietnamese-only, and optionally add English pronoun counting + an English superlative phrase list.
8. **Sentence boundaries (S9):** treat blank-line chunk boundaries as sentence boundaries in the claims scan (and ideally in `split_sentences`), so ld+json fields and paragraphs are quoted as themselves.
9. **Vocabulary hygiene (S10, S11):** split titles on separators before capitalized-run extraction; drop runs that are prefixes/suffixes of another extracted name; allow a trailing digit/weight token (`100g`, `250g`, `1kg`) to attach to a capitalized run; compute bigrams per sentence; exclude claim phrases from `use` suggestions.
10. **file:// resolution (S12):** resolve root-relative asset paths against the root page's directory for `file://` corpora, matching how the crawl filter already scopes them.

## Artefacts

- Fixtures: `/tmp/r6/sites/a1…a6/`, `/tmp/r6/sites/a1b/` (supplementary), `/tmp/r6/big/` (Probe D page)
- Runner: `/tmp/r6/build_and_run.py` · Raw results: `/tmp/r6/results.json` · Ground truth: `/tmp/r6/ground_truth.json`
