# Round 8 — End-to-End Flow Evaluation

Scope: the writing FLOW as a system — the LangGraph pipeline
(`content_core/graph.py`), context assembly (`mcp_server.assemble_context`),
and the MCP tool sequence a host model follows. Read-only evaluation;
probes were run from `/tmp` against the shipped code at commit `8215b8c`
(213 tests green). No core code, tests, or data were modified.

## Method

- **Graph traces**: `run_flow` driven by the stock `MockWriter` plus five
  purpose-built writer doubles (persistent brand failure, fixable fact
  failure, oscillating rewrite, persistent craft failure, empty-section
  writer, claim-writing writer). Gate history, failing lists, rewrite
  counts and final section texts recorded per pass.
- **Outline/refusal probes**: `Core.validate_brief` and `run_flow` with
  malformed, edge-value, and adversarial briefs; `make_outline` (MCP,
  in-process) with edge-case proposed outlines.
- **Context measurement**: `assemble_context` called directly; per-part
  chars/words/token-estimate (chars/4) for the standard brand, then
  stressed via monkeypatched brand/persona/style-profile data (200 banned
  terms, 50 examples, ~22k-char post-history, 40-trait style profile).
- **MCP loop**: a full brief → checks → rewrite → gate run over real stdio
  (subprocess server, drafts redirected to `/tmp`), timing every call;
  budget/state quirks probed in-process against the same functions.
- **Determinism**: the golden brief and the full golden set run twice
  through the graph and compared as canonical JSON; `assemble_context`
  likewise.

## 1. Rewrite convergence

### What works (verified)

| Case | Trace |
|---|---|
| Stock bad draft (g7) | Pass 1: brand 15, 30 brand violations, 10 fact issues, craft 84 → 1 rewrite → pass 2 all clean, craft 75 → `approved`, rewrite_count 1 |
| Fact-only failure (wrong price in s1) | Pass 1: 2 fact issues → rewrite → `approved` in 1 cycle |
| Empty section (s2 = "") | Pass 1 fails (vacuity + craft 0) → rewrite → `approved` in 1 cycle |
| Persistent failure (any kind) | Exactly 3 gate passes, rewrite_count caps at 2, status `failed_gates` |

Passing sections are **never touched** by graph rewrites: in the
persistent-brand-failure trace, the four clean sections were byte-identical
to fresh good output after 2 rewrite cycles. At the cap, the caller gets a
normal state dict — no exception — with `status="failed_gates"`, a 3-entry
`gate_history`, and `sections` still containing the failing text (verified:
final s3 still contains the banned term). **The caller must check
`status`; nothing else distinguishes the failed draft from an approved
one, and the bad text ships in the same field either way.**

### Defects

**F8-1 (HIGH) — Craft failure explodes the rewrite list to nearly every
section.** `craft_check_sections` builds `failing_sections` as *every
section carrying any craft violation*, regardless of that section's score
— unlike the brand linter, whose failing list is score-aware
(violations or score < 85). Every stock "good" MockWriter section carries
craft violations (`monotone_rhythm`, `repeated_openings`) while scoring
75, i.e. individually *passing* the 70 bar. Repro: one blog section of
filler/weak-word prose (craft 48) among four good sections → aggregate
craft 48 < 70 → gate failing list = **all 5 sections** `[s1..s5]`. The
graph (and the MCP instruction) then rewrites four sections that
individually pass craft. Consequences: wasted rewrite cycles, and each
unnecessary rewrite is a fresh chance to break a passing section (the
oscillation trace below shows rewrites can trade one failure for
another). The two checkers also disagree semantically about what
"failing section" means, which leaks into `gates.evaluate_gate`'s union.

**F8-2 (MEDIUM) — `rewrite_section` receives the draft-wide, unfiltered
issue list.** `graph.rewrite_sections` passes
`brand_violations + fact_issues` for the *whole draft* to every failing
section's rewrite. Repro: banned term in s2 + wrong price in s4 → the
rewrite call for s2 receives both issues (including s4's `wrong_price`),
and vice versa. MockWriter ignores the list, so the golden set cannot see
this; a real LLM writer is told to fix a price error in a section that
contains no price, inviting cross-section edits and hallucinated "fixes".

**F8-3 (MEDIUM) — Oscillation burns the whole budget with no progress
signal.** A writer that fixes the flagged fact error but introduces a
banned term + exclamation oscillates: pass 1 fact-fail (brand 100) →
pass 2 brand-fail (brand 80, facts clean) → pass 3 identical →
`failed_gates`. The gate history records the whack-a-mole faithfully, but
nothing in the flow detects non-convergence (e.g. same failing section,
changing failure class) or escalates differently — the third pass is a
foregone conclusion once pass 2 repeats a section. Acceptable for a
2-cycle design, but the caller gets no "not converging" hint beyond
reading the history themselves.

**F8-4 (LOW) — Score-at-threshold masking.** In the persistent-brand
trace the draft sits at brand score **85.0** (exactly the pass mark) for
all 3 passes; it fails only on the banned-term list. `gate_history`
records `brand_score: 85` beside `passed: false` with no field naming the
banned-term veto as the reason (a reader must cross-check
`brand_violations`). Minor reporting friction, correct behaviour.

## 2. Outline gate

**F8-5 (MEDIUM) — In the graph, the "outline gate" is a boolean flag, not
a gate.** `outline_gate` checks only `state["outline_approved"]`; the
outline itself is always the format template and is never validated.
Concretely, all silently ignored / accepted:

- A brief carrying its own `outline`, `sections`, or `target_words: 50`
  produces the stock blog outline (5 sections × 320 words = 1600) — the
  brief's wishes vanish without a note.
- An unknown format (`"whitepaper"`) silently falls back to the **blog**
  template, is linted against default median ranges, gets advisory-only
  craft, and can end `approved` — a whitepaper was never written, and
  nothing says so.
- `run_flow(brief, outline_approved=False)` ends at
  `awaiting_outline_approval` with the template outline attached — fine —
  but there is no path in the graph to submit a *modified* outline; the
  human can only approve the template as-is.

**F8-6 (MEDIUM) — MCP `make_outline` validation is heading-only.**
Verified against the tool directly:

| Proposed outline | Result |
|---|---|
| `[]` (empty) | `ok: True`, 0 sections — failure deferred to the final gate's vacuity guard |
| Duplicate headings ×3 | accepted |
| Missing/blank heading | rejected with a clear error ✓ |
| `target_words: 0` / `-50` / `10**9` | accepted verbatim |
| `target_words: 120.9` | silently truncated to 120 |
| `target_words: "150"` | silently coerced to 150 |
| `target_words: "abc"` / `None` | rejected with a clear error ✓ |

Zero/negative/absurd targets flow into the draft state and into
`save_section` records; nothing downstream sanity-checks them (the graph
writer would compute `max(3, ceil(target/12.5))` — a 1e9 target is a
runaway-generation instruction to a real writer).

### Prohibited claims in the brief

Refusal detection (`find_refused_claims`, normalized matching) is strong
against disguise: exact, unaccented (`100% chua mat ngu`), spaced
(`100 % …`), multi-space, and ALL-CAPS forms are all refused, as is the
bare sub-phrase (`chữa mất ngủ`, `trị bệnh` inside `trị bệnh mất ngủ`).

**F8-7 (HIGH) — But refusal never stops the flow.** `validate_brief`
records `refused_claims` and returns `status="validated"`; routing only
stops on `invalid_brief`. A g6-style brief (requested claim
`100% chữa mất ngủ`) runs the full flow and ends **`approved`**, protected
only by (a) the writer choosing not to write the claim and (b) the fact
checker catching it if written (verified: a writer that writes the claim
fails the gate on `prohibited_claim` and ends `failed_gates` — the claim
cannot *ship*, but the system drafts around a refused brief and spends
the full rewrite budget instead of refusing at intake). In the MCP flow
the only barrier is the advisory `instruction` string returned by
`validate_brief`. "Refusal" is a label, not a control.

**F8-8 (MEDIUM) — Refusal has both false positives and clean evasions.**

- *False positive (by design, still friction)*: negation is deliberately
  not honoured, so a brief that **disclaims** the claim — "cam kết không
  chữa mất ngủ, chỉ hỗ trợ thư giãn" — is flagged `refused=['chữa mất
  ngủ']` identically to a brief demanding it. A host model cannot
  distinguish "user asked for the claim" from "user forswore it" from
  the tool output.
- *Evasions*: a claim split across brief fields
  (`topic="chữa"`, `title="mất ngủ ngon giấc"`) is not refused — matching
  runs over `str(brief)`, so the phrase must be contiguous inside one
  value's repr; and a claim with an inserted word
  (`chữa dứt điểm mất ngủ`) is not refused. Both then rely entirely on
  the draft-side checker.

**F8-9 (LOW) — Brief validation is shallow.** Missing/empty `topic` or
`format` → `invalid_brief` ✓, but a whitespace-only topic (`"   "`)
validates and approves end-to-end; any non-empty format string is
accepted (see F8-5); the refused list echoes the *lowercased* brand
strings (`tốt nhất việt nam`) rather than the brand file's casing.

## 3. Context assembly

Standard brand, measured per part (chars / words / token-est ≈ chars/4):

| Part | Standard blog | + style profile |
|---|---:|---:|
| persona | 921 / 182 / ~230 | 921 |
| brand_rules | 982 / 164 / ~245 | 982 |
| craft_cards | 1,194 / 245 / ~298 | 1,194 |
| style_profile | — | 1,945 / 381 / ~486 |
| facts_triggered | 261 / 58 / ~65 | 261 |
| exemplar | 782 / 169 / ~195 | 782 |
| task | 256 / 47 / ~64 | 256 |
| post_history_reminder | 121 / 25 / ~30 | 121 |
| **context_text total** | **4,648 / 904 / ~1,160** | **6,614 / 1,287 / ~1,650** |
| **full tool JSON response** | **13,812** | **17,873** |

**F8-10 (MEDIUM) — The tool response is ~3× the context it delivers.**
`sources`, `craft_cards`, and `exemplars` are returned as full structured
objects *and* rendered into `ordered_context` *and* into `context_text`
(4,098 chars of duplicated payload in the standard case; the craft-card
JSON alone includes fields the rendered text omits, e.g. bad examples and
trigger metadata). A host model pays ~13.8k chars (~3.5k tok) to receive
~1.2k tokens of actual prompt context — on every brief, before writing a
word.

**F8-11 (MEDIUM) — No token budget anywhere; only craft cards are
capped.** Measured stress growth (each applied separately):

| Stress | Effect |
|---|---|
| Brief stuffed with every lorebook key | facts part unchanged at 261 chars — the lorebook has only 5 entries and all trigger; retrieval returns *all* triggered entries with no cap, so a grown lorebook (100+ entries, broad keys) scales this part linearly and unboundedly |
| Brand: 200 banned terms, 60 prohibited claims, 30 traits, 50 examples | brand_rules 982 → **26,857 chars** (~6,700 tok); examples alone dominate — *all* examples are dumped regardless of the brief's format |
| Persona: ~22k-char post-history + ~9k mes_example | persona 13,529; reminder 22,010; total context **39,145 chars** (~9,800 tok) |
| Style profile: 40 traits + 4×40 qualitative lists | style part 1,945 → **13,022 chars** |

Combined realistically (a mature brand + rich persona + full profile),
`context_text` reaches **~85–100k chars (~21–25k tok)** and the JSON
response well past 150k chars, with **no truncation, no warning, and no
prioritisation**: the parts are simply concatenated. What gets crowded
out is proportion, not presence — the `task` part (the actual assignment)
shrinks to ~0.3% of the context, sandwiched between a giant exemplar/
style block and the reminder; the facts part (the only numbers the
writer may use) shrinks below 1%. The one part that *is* capped —
craft cards at 3 — is capped by retrieval policy, not by any size
accounting. `assemble_context` is, however, fully deterministic
(identical output across calls) and degrades gracefully on a missing
style profile (part omitted + `style_profile_error` field) ✓.

## 4. MCP writer loop (stdio, timed)

Full documented flow — validate → research → assemble_context →
start_draft → make_outline → 5× save_section → brand/fact/seo checks →
final_gate (fail on s3) → save_section → final_gate (pass) → get_draft:

| Step | ms | Response chars |
|---|---:|---:|
| server spawn + initialize | **8,059** | — |
| validate_brief | 31 | 149 |
| research_facts | 4 | 1,088 |
| assemble_context | 54 | 17,873 |
| start_draft | 4 | 222 |
| make_outline | 4 | 587 |
| save_section ×5 | ~4 each | ~100 each |
| brand_check | 20 | 513 |
| fact_check | 58 | 156 |
| seo_check | 4 | 395 |
| final_gate (fail) | 41 | 2,145 |
| save_section (rewrite) | 40 | 113 |
| final_gate (pass) | 39 | 1,424 |
| get_draft | 4 | 2,782 |
| **total wall** | **8,391** | |

Per-call latency is a non-issue (4–58 ms); the 8 s cold start (interpreter
+ LangGraph imports in this VM) is paid once per stdio launch — hosts
that spawn per session will feel it, long-lived sessions won't.

### Friction and state defects

**F8-12 (MEDIUM) — Rewriting a section silently wipes its metadata.**
`save_section` replaces the stored record wholesale; `intent` and
`target_words` default to `""`/`0`. The gate's own instruction says
"save them with save_section" without mentioning the other fields, so the
natural rewrite call — `(draft_id, section_id, heading, text)` — stores
`intent=""`, `target_words=0` (verified). Any later logic keyed on intent
(craft-card retrieval per section, word-count expectations) silently
loses its inputs on exactly the sections that were rewritten.

**F8-13 (MEDIUM) — Empty-draft gate is a dead-end instruction.** Calling
`final_gate` on a draft with no sections returns `passed: false`,
`status: "rewriting"`, `failing_sections: []`, and the instruction
*"Rewrite ONLY sections []"* (plus a craft paragraph about a score of
0.0). The graph path cannot produce this (template outline ≥ 1 section),
but the MCP path can (start_draft → final_gate, or make_outline with
`[]` — see F8-6). A literal-minded host model is told to rewrite nothing
and call again; recovery requires ignoring the instruction and writing
sections anyway. Root cause is in the shared gate: for zero sections,
`evaluate_gate` reports `empty_sections: ["<no sections>"]` in the hard
layer but the failing-list builder only folds empty sections in `if
sections:` — so the verdict and the rewrite list contradict each other.

**F8-14 (MEDIUM) — The gate can be polled for free; history grows
unboundedly.** Repeated `final_gate` calls with no intervening saves each
append a `gate_history` entry to the stored draft (verified: 3 calls →
history 3, rewrite_count still 0, `rewrites_remaining` still 2). The
budget only moves when `save_section` completes an `awaiting_rewrite`
set. A confused or looping host can grow the draft JSON and re-run every
checker indefinitely at no budget cost — and the history stops meaning
"one entry per check pass over a *changed* draft".

**F8-15 (LOW) — Budget accounting doesn't match the "2 cycles" story.**
A save of *still-bad* text counts as completing a rewrite cycle (the
counter tracks saves, not improvements); a partial fix across two gate
calls redistributes the `awaiting_rewrite` set so a later single save
can consume a second cycle for what the writer experienced as one fix
round (traced: fix s1 → gate → save s2-bad → gate → fix both →
rewrite_count 2 on the passing call). Had that last gate failed, the
draft would be `failed_gates` despite the writer having made only two
*substantive* fix attempts, one of which was mid-flight when the counter
moved.

**F8-16 (LOW) — Approved drafts stay mutable, silently.** After
`status="approved"`, `save_section` still accepts writes — including a
brand-new section id never in the outline (`s99`) — and the stored status
remains `"approved"` (verified). The deliverable a user reads via
`get_draft` can therefore differ from the text the gate approved, with
no dirty flag; only a fresh `final_gate` call would notice. Symmetrically,
`save_section` accepts arbitrary section ids at any time (no check
against the stored outline).

**F8-17 (LOW) — State the writer must hold that the server could.**
The host model must carry: the `draft_id`; every section's id *and*
heading (required on each save) *and* intent/target_words (to avoid
F8-12); the quality arguments (`quality_profile`, `judged_scores`) to
repeat verbatim on every `final_gate` call for a consistent verdict;
and the style profile id inside the brief (it is read from the brief at
gate time, so a brief saved without it silently loses style reporting).
Sequencing is also duplicated effort: `brand_check` + `fact_check` +
`seo_check` before `final_gate` re-run the same checkers the gate runs
again (~120 ms and 3 extra round-trips per loop in the trace) — useful
as pre-flight, but the documented flow presents them as steps rather
than optional previews. There is no single "write my outline sections
from these texts" batch call: a 5-section blog is 5 round-trips, a
20-section piece is 20.

## 5. Determinism

Clean. The golden brief run twice through the graph produces
byte-identical canonical state (9,505 chars), as does the full golden
set; `assemble_context` is byte-identical across calls. No wall-clock,
random, or set-iteration leakage was found in any flow output (failing
lists are sorted in `gates.evaluate_gate`; lorebook/card retrieval
preserves file order). The single by-design nondeterminism in the system
is the MCP `draft_id` (uuid4 hex in `start_draft`).

## Severity ranking

| # | Severity | Defect |
|---|---|---|
| F8-1 | **High** | Craft failing list is violation-based, not score-based: one sub-70 section puts every violation-carrying section (in practice, all of them) on the rewrite list |
| F8-7 | **High** | Refused claims in a brief never stop the graph/MCP flow; refusal is advisory, protection is downstream-only |
| F8-11 | Medium | No token budget in context assembly; all parts but craft cards unbounded; task/facts proportionally buried under stress (~100k chars worst realistic) |
| F8-10 | Medium | assemble_context response ~3× the delivered context (structured duplicates of sources/cards/exemplars) |
| F8-13 | Medium | MCP final_gate on an empty draft returns an empty rewrite list and a dead-end instruction |
| F8-12 | Medium | save_section rewrite silently wipes section intent/target_words |
| F8-5 | Medium | Graph outline gate validates nothing; brief-supplied outline/targets ignored; unknown format silently becomes a blog |
| F8-6 | Medium | make_outline accepts empty/duplicate outlines and target_words of 0 / −50 / 1e9 |
| F8-8 | Medium | Refusal false-positives on disclaimers; evasions via split-across-fields and inserted words |
| F8-2 | Medium | rewrite_section receives all sections' issues, unfiltered |
| F8-14 | Medium | final_gate polling is free and appends unbounded gate_history |
| F8-3 | Medium | Oscillation consumes the budget with no non-convergence signal |
| F8-15 | Low | Rewrite-cycle counting tracks saves, not fix rounds |
| F8-16 | Low | Approved drafts remain mutable; arbitrary section ids accepted |
| F8-17 | Low | Host must hand-carry ids/headings/intent/quality args; no batch section save; pre-flight checks duplicate gate work |
| F8-4 | Low | gate_history shows a passing brand score (85) beside `passed: false` with no veto reason field |
| F8-9 | Low | Whitespace-only topic validates; refused claims echoed lowercased |

## Improvement suggestions (no fixes applied)

1. **Align craft failing semantics with the linter**: a section is
   craft-failing when its *score* is below the pass mark, not when it
   carries any violation. This single change defuses F8-1 and makes the
   gate's union of failing lists mean one thing.
2. **Filter rewrite issues per section** in `graph.rewrite_sections`
   (issues whose `section_id` matches), and include the craft violations
   for that section — currently craft is a gate input but never part of
   the rewrite guidance in the graph path.
3. **Make refusal a routing decision**: `validate_brief` gains a
   `refused` status that ends the graph (END) unless the caller passes an
   explicit override; in MCP, return the disclaimer case distinctly
   (e.g. `mentioned_but_negated`) so hosts can tell demands from
   disclaimers. Match claims per-field *and* across concatenated field
   values to close the split evasion.
4. **Give `evaluate_gate` a coherent empty-draft verdict**: failing list
   `["<no sections>"]` should surface as an actionable MCP instruction
   ("no sections saved yet — call save_section first"), and
   `make_outline` should reject empty outlines and out-of-range
   target_words (suggest 10–5000) and duplicate headings.
5. **Budget the context**: cap lorebook entries returned (top-N by key
   specificity), cap brand examples to the brief's format (+1 generic),
   summarise or elide style qualitative lists past a size threshold, and
   add a soft total budget with an explicit `truncated_parts` report.
   Separately, drop the structured duplicates from the
   `assemble_context` response (or drop `context_text` and let the host
   render) — one representation, not three.
6. **save_section merge semantics**: omitted `intent`/`target_words`
   keep the stored values; only explicit values overwrite. Add a batch
   `save_sections` tool. Validate section ids against the stored outline
   (warn or reject unknown ids).
7. **Gate-call hygiene**: if the draft's sections are unchanged since
   the last gate call, return the previous verdict without appending
   history (or mark the entry `repeat: true`); surface a
   `non_convergence` hint when the same section fails 2 passes with
   changing failure classes.
8. **State machine enforcement**: saving to an `approved` draft flips it
   back to `drafting` (dirty) so a stored approval always describes the
   stored text.
9. **Graph outline honesty**: validate the brief's format against the
   known template set and fail loudly (or record `format_fallback:
   "blog"`) instead of silently templating; honour a brief-supplied
   outline through the same validation `make_outline` applies.
