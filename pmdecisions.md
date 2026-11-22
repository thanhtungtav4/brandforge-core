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
