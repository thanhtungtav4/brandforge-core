## Product Context

last_updated: 2026-11-22

### Product
brandforge-core is a local MCP tool that runs inside the AI apps a writer already uses (Muse, ChatGPT, Gemini). The host app's model does the writing; the core is the deterministic referee: separate Persona, Brand Voice, Facts, Craft and Style layers, one shared gate rule, and a 7-axis Quality Profile. Form factor is local-first; API providers attach later and must behave identically (same core, same gates, only the writer changes). Phased B2B: phase 1 is an internal production tool for the owner's own client work; phase 2 sells the tool to writers/agencies. Business model is service-led: a monthly content-writing service for clients, with the tool as the production engine behind it. The tool itself is not sold yet.

### Lifecycle and Strategy
Pre-product-market-fit. Current strategic focus: use the tool first on the owner's own sites to grow them, then serve paying clients' content needs with it, then package it for sale. Explicitly NOT doing right now: selling the tool to outside users, integrating external LLM providers, building the product UI (planned later, Astryx design system), multi-user/auth work.

### Users
Primary user today is the owner himself: a solo web/content freelancer serving Vietnamese SME clients (shops, clinics, local brands) who reach him via Zalo. Buyers and beneficiaries are those clients: they pay a monthly fee and receive articles for their sites; they never touch the tool directly. Later users (phase 2) are writers and small agencies buying the tool itself. What clients did before: wrote articles by hand, used raw ChatGPT/Claude with no brand or fact control, hired freelance writers, or published nothing. The specific job clients hire the monthly service for is [Not captured - ask me and update this section].

### Competitive Landscape
Real alternatives: raw LLM chat apps used without guardrails; generic AI writing products (Jasper-style SaaS); freelance writers; the client's own staff writing ad hoc; doing nothing and letting the site sit stale.

### Team and Process
Team of one. The owner decides — brief, direct instructions, scope approved per round before building. Ember (the AI agent) builds, tests and verifies. Definition of done, established in practice: fail-first tests, all suites green, results re-measured with the same probes used to find the problems, an independent review pass for large rounds, and the work pushed to GitHub (repo thanhtungtav4/brandforge-core).

### Technical Constraints
Local Python MCP server (stdio plus localhost-only HTTP, no auth in v1). Output quality depends on the host app's model, which the product does not control. ChatGPT access needs the user's own HTTPS tunnel. Per-brand data (brand, facts, style, quality profiles) lives in local files/stores. Easy to change: all core checking logic, heavily tested (377 tests). Hard to change: host-model variance, anything multi-user or hosted, provider integration (deferred by design).

### Current Challenges
Biggest risk, named by the owner on 2026-11-22: feasibility — whether the host app's model writes stably enough in real client work. The core-as-referee architecture is the mitigation: deterministic gates catch what an uncontrolled writer gets wrong. Open questions: the client job-to-be-done (not yet captured); which host app/model becomes the default for the service. Settled 2026-11-22: client articles are delivered as WordPress drafts on the client's own site, never auto-published (see pmdecisions.md).

### Ways of Working
Decisions by measurement: options are scoped, the owner approves the scope, work runs fail-first, and results are reported as before/after numbers rather than claims. Documents short, in Vietnamese for working docs, direct in tone. Quality bars are explicit and pinned (brand pass 85, craft bar 70, quality floors per axis) and are not traded away silently. Checklists before greenlighting a round: repro cases first, all suites green, probes re-run, review for large changes.
