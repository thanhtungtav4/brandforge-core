# Product Decisions — brandforge-core

## 2026-11-22 — Output channel for client articles: WordPress drafts on the client's site

**Decision:** Finished articles are delivered by posting them directly as **drafts** into the client's own WordPress site. Never auto-published — the client reviews and publishes.

**Context:** The product's phase-1 business is a monthly content-writing service for the owner's existing web clients (see .pmcontext.md). Most client sites are WordPress sites the owner built or maintains, so write access via WordPress Application Passwords (REST API) is realistic to obtain per client.

**Options considered:**
1. Text export only (client copies, formats and posts everything themselves) — zero integration work, but all friction lands on the client every month.
2. WordPress draft on the client's site (chosen) — the client only reviews and hits publish; delivery feels like a finished service, not homework.
3. Both, per client preference — deferred; can be added later without changing the decision, since export is the fallback when a client has no WordPress or declines access.

**Trade-offs accepted:** per-client credential handling (Application Passwords, stored securely, revocable by the client) and WordPress integration work in the product phase, in exchange for much lower client friction — which is the thing a monthly service is actually selling. Draft-only status keeps editorial control and liability with the client.

**Consequences / next steps:**
- The product scaffold (P3) spec must include a WordPress draft exporter: per-brand target site config, REST draft creation, credentials via secure storage only (never in brand files or git).
- Text export remains the fallback path and the phase-2 (tool sale) default.
- Revisit if a major client is not on WordPress.

## 2026-11-22 — Research & configuration assistant accepted into P3 scope

**Decision:** Add a research-and-configuration assistant to the P3 scope, specified in `specs/research-config-feature.md`. The assistant gathers source material for a new brand (the brand's own site, competitors, genre/style corpora, factual sources) and drafts the writing configuration (brand, facts, style, persona skeleton) as individually approvable items with per-item provenance. Only items the owner approves are applied to the live configuration.

**Context:** Every quality jump on 2026-11-22 came from better configuration inputs, with the writing model unchanged: brand rules extracted from the real site, facts taken from published pages, style learned from real corpora. That setup work was done manually across many agent rounds in one day for two brands, with the owner resolving a same-page price contradiction (100.000đ listing vs 90.000đ in the site's own price table), rejecting low-quality corpus sources, and recording legitimacy bases by hand. Smith proposed productising this: an AI that finds the material and co-configures with the user.

**Options considered:**
1. Keep setup manual in chat (status quo) — good results while the owner sits next to every decision, but leaves no approval artefact and cannot transfer to phase-2 tool buyers.
2. Research assistant with item-by-item approval (chosen) — reuses the existing Brand Builder, Style Extraction and facts stores; adds a research pack with proposal states, a contradiction report, and an approval step before anything touches live configuration.
3. Fully automatic configuration from a single URL — rejected. It breaks the discipline the trials established: contradictions surfaced for the owner to decide, competitor material never becoming an approved brand by default, claims entering facts only through approval.

**Trade-offs accepted:** build time for the pack store and MCP tools before any UI exists, plus a per-brand review session instead of scattered chat commands, in exchange for a setup process that leaves an auditable approval record and can later be handed to other users.

**Consequences / next steps:**
- The P3 scaffold must include: the research-pack store with approval states in the core, the pack MCP tools named in the spec, and an Astryx checklist UI consuming the pack contract. The WordPress draft exporter stays a separate P3 item.
- The pack data contract is frozen only after a first measured trial run (spec section 8 defines the metrics); UI work starts after that.
- Open items the spec leaves for Smith: whether any item kind may be bulk-approved, and whether shared genre corpora are registered once or copied per pack.

## 2026-11-22 — Research pack approvals are manual per item; genre corpora are system-level, chosen per brand

**Decision:** Two open questions from the research & configuration spec, answered by Smith the same day:
1. **No bulk approval.** Every configuration item in a research pack is approved manually, one by one, in the admin. There is no approve-all or per-kind bulk shortcut in phase 1.
2. **Shared genre corpora are registered once at system level.** Each brand then *chooses* which corpus (and version) to attach, and the AI uses that chosen corpus as the reference sample set to improve the brand's writing. Corpora are never copied into individual packs; a pack records the brand's corpus choice.

**Context:** The spec had proposed "no bulk approval for the first trial" and "register corpora once" as defaults and left both to Smith. He confirmed the corpus default and hardened the approval default into a standing rule (manual, in admin — not just a trial setting).

**Trade-offs accepted:** Slower setup sessions (every item gets individual attention) in exchange for the approval step staying a real review rather than a rubber stamp — consistent with the spec's warning that a >85% unchanged-approval rate signals careless review. System-level corpora mean a corpus update can shift many brands' reference at once; the per-brand version pin is the control for that.

**Consequences / next steps:**
- Spec section 12, questions 2 and 3 are marked resolved with these answers.
- The P3 data contract must include: a system-level corpus registry (id + version), and a per-brand corpus choice recorded in the research pack.
- The Astryx checklist UI is a per-item review surface; no bulk-approve control is to be built.

## 2026-11-22 — Article images: search real photos, vet, then AI-edit; generation becomes the last resort

**Decision:** The image fulfilment chain gains a search path between the brand library and AI generation, specified in `specs/image-search-edit-feature.md`. Per slot, the order becomes: brand library, then search real photos from license-clear sources on a per-brand allowlist, then vision vetting of candidates, then AI editing of the chosen photo (crop, colour and light grading toward the brand's visual style, background cleanup; never regenerating the photo and never altering the product in it), then the existing vision check on the result. Generation from scratch drops to last resort, runs only on an explicit exhausted-search flag, and is marked as generated in the manifest. Build is deferred; the current image layer is unchanged until Smith greenlights a build round.

**Context:** Smith reviewed the generated image set for the Mắm Bà Giáo Khỏe intro article (all 6 slots generated, because the brand library was empty) and judged the output in his own words: the flow should be "tìm kiếm ảnh, về nhận diện ảnh rồi dùng AI tinh chỉnh lại để bám sát hơn" instead of generating, because generated images "nhìn AI quá" (look too AI). The brands brandforge serves sell food and physical products to readers who judge authenticity from photos, so a synthetic look costs reader trust in the product itself. Asked to choose between building immediately and recording the decision plus spec first, Smith chose spec first.

**Options considered:**
1. Keep library-then-generation (status quo) — rejected by Smith on the evidence of the v4 image set: the output reads as AI-generated on a food shop's article.
2. Search, vet, then AI-edit real photos; generation last (chosen) — keeps the photo real, uses editing only to pull it toward the slot's ratio and the brand's visual style, and reuses the existing vision-check layer as the gate on the result.
3. Search and use photos unedited — rejected: unedited stock clashes with the brand's visual style from image to image, which is the same set-consistency problem the style lock already solves for generated images.

**Trade-offs accepted:** more steps per article for slots the library cannot fill (search, vetting, editing, each capped and measured in the spec's first trial, with a 15-minute-per-article time guardrail), dependence on the host app's image-editing ability, which the project has not tested yet, and license bookkeeping per searched image, in exchange for photos that read as real. Legal discipline is part of the decision, not an add-on: allowlisted sources only, a license type from a fixed set recorded per image, unknown-license images rejected at registration, watermarked candidates rejected at vetting, and editing a watermark away is banned outright.

**Consequences / next steps:**
- Spec at `specs/image-search-edit-feature.md` defines the chain, the vetting record, the fixed edit-operation vocabulary with the product-truthfulness rule, and the manifest provenance fields (`origin`, `source_url`, `license_type`, `attribution_text`, `edit_ops`, `generated`). No code changes in this round.
- First trial, when built, runs on Smith's own shop (brand `mambagiaokhoe`) and is decided by his blind scoring of edited photos against the existing generated set, per spec section 8.
- Open items the spec leaves for Smith: the default stock-source allowlist, whether product-specific slots are library-only or library-then-generation, whether customer-sent photos count as user-supplied sources, and whether attribution-required licenses stay in the allowed set.

## 2026-11-22 — Existing WordPress posts get a rewrite path: import, audit, rewrite, export as a linked draft; the original is never touched

**Decision:** Add a rewrite path for posts already published on a brand's WordPress site, specified in `specs/wp-rewrite-feature.md`. The flow is: list and import a post into a core draft that preserves the original verbatim as an immutable snapshot with provenance; run a fact and claims audit of the original against the brand's current facts profile, stored separately from the draft's gate history; Smith records a per-post verdict (rewrite, audit-only, or drop); a rewrite runs the normal writing flow and exports as a NEW WordPress draft linked to the original (title prefix plus a `rewrite_of` record). The system never updates, overwrites, or changes the status of the original post — a hard rule in the same class as the never-publish rule, pinned by an automated test. Smith completes the replacement manually in wp-admin. Build is deferred; spec first, as with the research-pack and image features.

**Context:** The WordPress exporter shipped on 2026-11-22 and posted 4 new articles as drafts to kho.nttung.dev (posts 777 to 780). Smith then asked whether the system can also handle the site's existing articles. The checkers score any text, but two links were missing: no importer from WordPress into core drafts, and no export path that means "a rewrite of post X" — the exporter only creates new drafts. The trial set is real and already verified live: kho.nttung.dev, Smith's own shop (brand `mambagiaokhoe`), has exactly 9 published posts (ids 240, 241, 717 to 723, November 2026 SEO content written before brandforge existed), including on-topic how-tos, a 42-item listicle, and one post entirely off the shop's topic ("Cam Sành Vĩnh Long" on a dried-fish and mắm shop).

**Options considered:**
1. Paste old post text into chat by hand (status quo) — rejected: no preserved original, no audit artefact attached to a draft, and every post is a manual copy step that can drop sections or images.
2. Update the original post in place via REST once the rewrite passes the gate — rejected: it replaces live client content before anyone reviews the replacement, and a worse rewrite would sit directly on the client's site. It also breaks the delivery discipline settled earlier the same day (drafts only, the owner reviews and publishes).
3. Import, audit, rewrite in core, export as a new linked draft; Smith replaces manually (chosen) — the original stays provably untouched (trial verifies all 9 originals by re-reading them and comparing modification marks and content hashes), and every post leaves an audit record even when the verdict is drop.

**Trade-offs accepted:** Smith keeps one manual step in wp-admin per replaced post (moving the rewrite over, handling slug and redirects), and the importer adds an HTML-to-sections conversion whose fidelity has to be measured per post (a coverage ratio at import, low-coverage posts steered to audit-only), in exchange for originals that are safe by construction and an auditable trail per post. WooCommerce product descriptions and pages are explicitly out of v1: product data lives in commerce fields outside the article section shape, and pages are usually builder-built structure that section conversion would misrepresent.

**Consequences / next steps:**
- Spec at `specs/wp-rewrite-feature.md` defines the importer tools (`list_wordpress_posts`, `import_wordpress_post`, `audit_imported_post`, `set_rewrite_verdict`), the snapshot and provenance blocks on the draft, the conversion table with the coverage metric, the linked-draft export branch on the existing exporter, and the trial: all 9 posts of kho.nttung.dev imported and audited, at least 5 rewritten to gate-pass as linked drafts, every other post closed by a recorded verdict, median 30 minutes per post, and 0 modifications to any original.
- The never-overwrite invariant joins never-publish as a structural rule of the WordPress layer: importer issues only GET requests, and the export branch creates a new post; a mock-server test asserts no mutating request ever targets the original post id.
- Open items the spec leaves for Smith: the "[Viết lại] " title prefix (proposed keep), whether drop verdicts need an aggregated removal list before the UI step, tag inheritance from the original (proposed no), and whether product descriptions get a follow-up spec after the trial.

## 2026-11-23 — Image search-edit: §12 open questions resolved under Smith's Brandforge delegation

**Decision:** The four open questions in `specs/image-search-edit-feature.md` §12 are resolved as follows, and build step one of the feature proceeds on them:
1. **Default stock allowlist for NEW brands: Pexels, Pixabay, Unsplash.** This is a starting list only — the operative allowlist stays per-brand in the brand's `image_sources` field; a brand without the field (or with it empty) has the search step OFF and keeps today's library-then-generation chain exactly.
2. **product_specific slots: library first, then generation; stock search is FORBIDDEN for them at every step** — no candidate may be registered for, or fulfil, a product_specific slot.
3. **Photos from Smith's own supplier Zalo group (Vựa khô Quốc Dũng — Nhóm sỉ 1) count as `user_supplied`**, under his standing direction to import that group's photos into his shop's library. Photos customers send through any other channel are NOT `user_supplied` until Smith confirms usage rights per brand.
4. **`cc_attribution` STAYS in the license set**, with the spec's hard rule: the manifest must carry `attribution_text` and the WordPress caption must contain the attribution; missing attribution is the hard issue `attribution_missing` and blocks manifest readiness.

**Context:** Smith wrote the spec on 2026-11-22 and chose spec-first/build-later, leaving §12's four questions for himself. On 2026-11-23 he delegated Brandforge decisions to Ember ("phần nào về Brandforge bạn tự quyết luôn giúp mình"), the same delegation under which the SEO partial-read counting question was resolved earlier that day. All four resolutions adopt the spec's own proposals, so no design direction changes — the delegation unblocks the build rather than redirecting it.

**Options considered:**
1. Leave §12 open and keep the build blocked (status quo) — rejected: the questions were the only blocker for build step one, and Smith's delegation was given precisely to stop Brandforge items waiting on him.
2. Resolve differently from the spec's proposals (e.g. drop `cc_attribution` for simplicity, or make product_specific slots library-only with no generation fallback) — rejected: the spec's proposals were already reasoned from the trial evidence, and a library-only product slot with an empty library would leave articles imageless; the generation fallback stays, marked `generated` in the manifest like every generated image.

**Trade-offs accepted:** Ember, not Smith, now owns these four calls; if the first trial's blind test or a license incident contradicts one, the decision is revisited with that evidence. The `user_supplied` resolution is deliberately narrow — one named supplier group under an existing standing direction — so no general "customer photos are fine" precedent is set for client sites.

**Consequences / next steps:**
- Spec §12 is marked resolved (2026-11-23) with these decisions and their basis.
- Build step one implements: the `image_sources` brand field + loader validation, the per-draft candidate pool with allowlist/license checks at registration, the vetting record with the rejection rules (watermark / wrong_subject / grade 0), `set_image` extended with `source="searched"` plus the fixed edit-op vocabulary (watermark removal is not in the vocabulary and no tool path records it), the grade-0 fallback chain in the fulfilment orchestration, and the manifest provenance fields (`origin`, `source_url`, `license_type`, `attribution_text`, `edit_ops`, `generated`).
- Still Smith's: the first trial on the next Khô Mắm An batch, including his blind scoring of edited vs generated photos (spec §8) — that judgement is the feature's main success metric and is not delegable to a metric the core computes.

## 2026-11-23 — WordPress rewrite: §12 open questions resolved under Smith's Brandforge delegation

**Decision:** The four open questions in `specs/wp-rewrite-feature.md` §12 are resolved as follows, and build step one of the feature (core importer, audit, verdict, linked-draft export branch) proceeds on them:
1. **The `[Viết lại] ` title prefix is KEPT** on linked rewrite drafts — one named constant, changeable in one place.
2. **Tags are NOT inherited from the original post; categories ARE inherited** (falling back to the target's `default_category_id` when the original has none, as the exporter already does).
3. **Drop-list aggregation stays a UI concern.** The core records the per-post `drop` verdict with its reason and nothing more; an aggregated "posts to remove" list belongs to the app step, not v1 core.
4. **WooCommerce product descriptions are OUT of this build** — a candidate for a follow-up spec once the posts flow is proven, per the spec's out-of-scope list.

**Context:** Smith wrote the spec on 2026-11-22 and chose spec-first/build-later, leaving §12's questions for himself. On 2026-11-23 he delegated Brandforge decisions to Ember ("phần nào về Brandforge bạn tự quyết luôn giúp mình"), the same delegation under which the SEO partial-read question and the image search-edit §12 questions were resolved earlier that day. All four resolutions adopt the spec's own proposals, so no design direction changes — the delegation unblocks the build rather than redirecting it.

**Options considered:**
1. Leave §12 open and keep the build blocked (status quo) — rejected: the questions were the only blocker for build step one, and the delegation was given precisely to stop Brandforge items waiting on Smith.
2. Resolve differently from the spec's proposals (e.g. drop the title prefix, or inherit tags for completeness) — rejected: the prefix is the fastest way to spot an original/rewrite pair in wp-admin (the spec's pre-mortem names double-publishing as a real failure mode), and the old site's tags are exactly the kind of legacy mess the rewrite flow exists to clean up.

**Trade-offs accepted:** Ember, not Smith, now owns these four calls; if the 9-post trial contradicts one (e.g. the prefix annoys in wp-admin practice), it is revisited with that evidence. The prefix is a single constant and tag inheritance is a single branch, so any reversal is cheap.

**Consequences / next steps:**
- Spec §12b records the resolutions with their basis; build step one implements the four tools (`list_wordpress_posts`, `import_wordpress_post`, `audit_imported_post`, `set_rewrite_verdict`) and the `wp_import` export branch on the existing exporter, so the F1 gate and F2 idempotency govern rewrite exports unchanged.
- The hard invariants stand as specified: the importer issues GETs only, the imported draft's `original` block is byte-verbatim and writable only by the import tool, and no code path edits, overwrites, or changes the status of an existing WordPress post.
- Still Smith's: the trial itself — his per-post verdicts on the 9 real posts and the rewriting work — and any export against the real site (build step one verifies import + audit read-only against kho.nttung.dev; no real-site export happens in the build round).
