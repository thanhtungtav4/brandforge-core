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
