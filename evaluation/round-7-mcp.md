# Round 7 — MCP Server Surface Evaluation

**Scope:** `mcp_server.py` — 29 tools, stdio + streamable HTTP (127.0.0.1), FastMCP / mcp SDK 1.30.0, at commit `8215b8c` (213 tests green).
**Mode:** READ-ONLY evaluation. No core code, tests, or data were modified (`git status` clean apart from the untracked `evaluation/` reports). All state-writing tools ran against throwaway stores via `CONTENT_CORE_DRAFTS_DIR`, `CONTENT_CORE_STYLE_PROFILES_DIR`, `CONTENT_CORE_BRAND_DRAFTS_DIR`, `CONTENT_CORE_QUALITY_PROFILES_DIR` pointed at `/tmp/r7/env-*`; the project's real `drafts/` directory was verified byte-identical (55 files) before/after.
**Method:** the official MCP SDK client over real transports (no in-process shortcuts). Four harnesses: (A) a 58-call valid-input sequence run identically over stdio and HTTP for contract + parity; (B) ~95 fuzz calls over stdio (wrong types, `None`, unknown/path-like/emoji/10k-char ids, nested structures, oversized texts); (C) state tests — two concurrent server processes sharing one drafts dir, plus mid-lifecycle file corruption; (D) raw HTTP probes (hand-rolled JSON-RPC over httpx) for the non-SDK surface. Scratch in `/tmp/r7/` (`seq.py`, `fuzz.py`, `state.py`, `http_raw.py`, results JSON alongside).

---

## 1. Contract verification — per-tool table (valid input)

Every tool was called with valid input and its output compared with the README tool table + docstring. **28/29 tools behave exactly as documented.** Compact results (sequence A, stdio):

| Tool | Verdict on valid input | Notes |
|---|---|---|
| `get_persona` | ✅ | Returns `{data, spec, spec_version}` (Card V2). |
| `get_brand` | ✅ | `brand.id = moc-brew`, banned terms present. |
| `research_facts` | ✅ | Triggered entries + canonical facts; empty query → `sources: []`, still returns facts table (undocumented but harmless). |
| `validate_brief` | ✅ with nuance | Refused-claim brief returns `refused_claims: ['100% chữa mất ngủ', 'chữa mất ngủ']` **and `valid: true`** — `valid` means "structurally validated", refusal is a separate field. Matches docstring, but a client that only reads `valid` will miss the refusal. See F7. |
| `get_craft_cards` | ✅ | ≤3 cards; unknown format `podcast` → only the always-on core card (`concrete-specifics`), as designed. |
| `get_exemplars` | ✅ | `product` → 1 exemplar; unknown format → `{count: 0, exemplars: []}` (no error — reasonable, undocumented). |
| `get_domain_glossary` | ✅ | No group → all groups + weak words; named group → that group; unknown group → in-band `{"error", "available_groups"}`. |
| `craft_check` | ✅ | Text and `draft_id` modes work; `brand_id` selects the generic glossary as documented in the calibration-fix note. |
| `analyze_style` | ✅ with nuance | 2 samples → fingerprint + "only 2 samples" warning. `samples: []` returns a zero fingerprint + warnings instead of an error — asymmetric with `save_style_profile([])`, which errors (F8). |
| `save_style_profile` | ✅ | Persists, returns generated id (`giong-r7-57ba6a`); excerpts empty by default. But see F3 (bool coercion). |
| `get_style_profile` | ✅ with caveat | By-id, list, `default`, and not-found (`{"error", "available"}`) all correct **in the shipped layout**. With `CONTENT_CORE_STYLE_PROFILES_DIR` overridden, `default` breaks — see F6. |
| `style_match` | ✅ | 0–100 score, 6 metrics, advice; unknown profile → structured error. |
| `analyze_site_brand` | ✅ | Kit with `source_url`s, fingerprint, claims, small-corpus warning. `pages: []` → zero corpus + warnings, no crash. |
| `save_brand_draft` | ✅ | Draft persisted, `banned_terms: []`, review notes present. |
| `get_brand_draft` | ✅ | By-id + list + structured not-found error. |
| `compare_brand_voice` | ✅ | Gaps computed from deltas; empty/empty → `Site A`/`Site B` zero report, no crash. |
| `assemble_context` | ✅ | Exactly 7 ordered parts; 8 with a style profile (inserted after craft cards); missing profile → `style_profile_error` field and context built without it, as documented. |
| `make_outline` | ✅ with nuance | Template + proposed paths; no-heading and `target_words: "abc"` → structured `{ok: false, error}`. Float `target_words: 12.9` is **silently truncated to 12** (F5). |
| `start_draft` | ✅ | Returns `d-<10 hex>` id, draft persisted. |
| `save_section` | ✅ | Save/overwrite + rewrite-budget bookkeeping as documented. |
| `get_draft` | ✅ | Full state incl. gate history. |
| `brand_check` | ✅ | Text mode and draft mode (worst-section score). |
| `fact_check` | ✅ | Wrong price → `wrong_price` issue, `ok: false`. |
| `seo_check` | ✅ | Title/meta/headings/keyword fields from the draft's brief, overrides work. |
| `get_quality_profile` | ✅ | Preset by format name; unknown id → structured error listing formats. |
| `save_quality_profile` | ✅ with nuance | Valid custom profile persists and round-trips; bad sum → structured error. Path-like `profile_id` accepted (F4). |
| `quality_score` | ✅ | Judged grade 0 → score 20 (documented conversion); depth floor failure reported (`floors_failed: ["depth"]`, total 86.0 yet `quality_pass: false` — anti-compensation working as designed). |
| `final_gate` | ✅ | Clean draft → `passed: true, status: approved`; craft fields, quality layer, and gate-history recording all as documented. |
| `run_golden` | ✅ | `{passed: 7, total: 7, all_passed: true}` in ~1.5 s. |

**Contract mismatches found (all minor):** F3–F8 below. No tool returned a different *shape* than documented on valid input; no documented field was missing in any of the 58 sequence calls.

---

## 2. Fuzzing — wrong types, hostile ids, oversized inputs

~95 calls. **Headline: zero stack traces.** No response contained a Python `Traceback`; nothing hung; nothing wrote outside its store directory. Errors arrive in exactly two dialects (this duality is itself finding F1):

- **Structured errors** — HTTP-200 result `{"error": "..."}` (domain-level validation): `save_style_profile` (build failures), `get_style_profile`, `style_match` (unknown profile), `get_brand_draft`, `get_quality_profile`, `save_quality_profile`, `quality_score`/`final_gate` (profile + judged-grade validation), `make_outline` (`{ok: false, error}`), `get_domain_glossary` (unknown group, from the core function).
- **Protocol errors** — MCP `isError` result, text `Error executing tool <name>: <message>`: all pydantic argument-type failures (clean, field-named messages) and all `ValueError`s raised from draft loading (`draft not found: X`, `invalid draft_id`, `corrupt JSON in draft file …`).

### Exact calls of interest

| Call | Result |
|---|---|
| `get_draft {"draft_id": "z"×10000}` — same for `get_style_profile` (10k id), `get_brand_draft` (10k id) | Protocol error leaking a **raw OS error + absolute path**: `Error executing tool get_draft: [Errno 36] File name too long: '/tmp/r7/env-fuzz/drafts/zzzz…'` (the full 10k-char path is echoed). `stored_file_stem` accepts arbitrarily long clean ids; the filesystem rejects them and the `OSError` escapes unwrapped. **F2.** |
| `save_style_profile {"name": "X", "samples": [A], "store_excerpts": "yes"}` | **Accepted — and excerpts were stored** (profile file contains 1 exemplar, note "excerpts stored from the samples"). Pydantic's lax bool parsing coerces `"yes"`→`True` (`"false"`→`False`, verified). The one flag the design treats as copyright-sensitive is the one accepting string coercion. **F3.** |
| `save_quality_profile {"profile_id": "../../evil", "weights": {…valid…}}` | Accepted; returned profile has `id: "../../evil"` verbatim and **round-trips** via `get_quality_profile("../../evil")`. The file itself stayed inside the store (`quality/evil-1361ca8c.json`, injective hash stem) — **no escape**, but a path-like identifier is now persisted in profile data. **F4.** |
| `analyze_site_brand {"pages": [{"url": 1, "text": 2, …}]}`; `save_brand_draft` with `text: 123` page | Protocol error with a **bare, context-free** Python message: `'int' object has no attribute 'split'` — no tool/field/page context. Page dict *contents* are unvalidated (`list[dict]` only). **F5.** |
| `make_outline {"proposed_outline": [{"heading": 123}]}` | **Accepted**: outline item stored with `heading: 123` (int). The "every item needs a heading" validation stringifies for the emptiness check but stores the raw value. **F5.** |
| `make_outline` `target_words: 12.9` vs `save_section` `target_words: 1.5` | Outline path silently truncates (`int()`), section path rejects (pydantic). Inconsistent numeric policy between sibling tools. **F5.** |
| `save_section {"target_words": -5}` | Accepted; negative target stored. Harmless (advisory field) but unvalidated. |
| `save_section {"section_id": "../evil"}` and `{"section_id": ""}` | Accepted as section ids (they live inside the draft JSON only — **no file-system effect**). A gate's `failing_sections` can therefore contain `"../evil"` or `""`. Cosmetic. |
| `validate_brief {"brief": {"format": 123, "topic": "x"}}` | `valid: true` — brief *contents* are entirely untyped (only the outer `dict` is enforced). |
| `get_persona {"surprise": 1}` | Extra/unknown arguments are **silently ignored** (FastMCP default) — a client typo in an argument name fails silently everywhere (e.g. `draftId` instead of `draft_id` would be dropped, then the required-arg error names the missing field, which limits the damage). |
| All traversal ids on read paths (`get_draft "../"`, `craft_check` draft `"../../data/brand_mocbrew"`, `style_match` profile `"../../etc/passwd"`, `get_brand_draft "../../etc/passwd"`, `get_quality_profile "../brand_mocbrew"`) | **Contained**: `draft not found` / `invalid draft_id` / structured `not found` / `unknown quality profile`. No file outside any store was read or written. Full escape audit: after the whole fuzz run, the env root contained only the 4 expected store files; the project's real `drafts/` was unchanged. ✅ |
| Emoji-only ids (`"💥"`, `"💥💥"`) | Clean `invalid draft_id` / `invalid id` errors (sanitiser strips to empty → ValueError). ✅ |
| Deeply nested `analyze_style {"samples": [[["deep"]]]}` | Clean pydantic validation error. ✅ |

### Oversized inputs (stdio — no size limit exists on this transport)

Repeated brand-compliant paragraph; timings are single-run, this VM:

| Input | brand_check | fact_check | craft_check | analyze_style |
|---|---|---|---|---|
| 428 KB (340k chars) | 2.8 s | 8.5 s | 0.5 s | 0.8 s |
| 1.0 MB (794k chars) | 3.5 s | **11.5 s** | 1.2 s | — |

`save_section` with the 428 KB text: 61 ms; `get_draft` round-trip of it: 24 ms; `final_gate` over the 1-section 428 KB draft: **12.0 s** (it re-runs brand + fact + craft over the text). No timeouts, no memory blow-up, no truncation — but there is **no input-size guard on stdio at all**, and fact_check is the hot spot (~11.5 s/MB, superlinear-looking growth from 428 KB→1 MB is modest; roughly linear). A pathological multi-MB section would make gate calls take minutes. HTTP is protected by the transport body cap (§5); stdio relies on the host app behaving. **F9 (low).**

---

## 3. Parity — stdio vs HTTP

The identical 58-call sequence A (tools/list incl. full input schemas, all 29 tools on valid input, a complete draft lifecycle, quality layer, `run_golden`) was run over both transports against fresh, identically-seeded throwaway stores. After normalising only the random `draft_id` (and wall-clock `ms`):

> **Diff: 0 content differences across all 58 results** — including tool schemas, gate verdicts, gate history, style/brand/quality artefacts, and the golden-set report.

The HTTP server is the same FastMCP app, so this is expected — but it is now *measured*, including the stateful paths (draft persistence is via the shared filesystem stores, not transport state).

---

## 4. State — draft lifecycle, concurrency, corruption

Lifecycle (create → outline → sections → checks → `final_gate` → reload via a new session) works exactly as documented, including rewrite-budget bookkeeping and `gate_history` growth.

**Concurrency — two server processes, one shared drafts dir (the multi-client case):**
- 2 × 15 concurrent `save_section` calls on the **same draft** (distinct section ids): all 30 calls returned OK; the stored file was **valid JSON, no torn writes, zero orphan `.tmp` files** — the atomic temp+`os.replace` write does exactly what it promises. **But 29/30 sections survived**: one section was silently lost to a whole-file read-modify-write race (both processes read the same base, each appended, last rename won).
- 2 concurrent `final_gate` calls on the same draft: both returned `passed: true`, but the stored `gate_history` contains **1 entry, not 2** — one verdict's history record was lost the same way.
- This is precisely the documented P3 deferral ("versioning/locking remains deferred"), now quantified: **atomicity ✅, serialisability ❌**. Single-process stdio use (the shipped mode) is unaffected. **F10 (medium, known-deferred).**

**Corruption mid-lifecycle** (draft file replaced under a live workflow):
- Garbage or truncated JSON → every draft tool fails with a clear, named error: `corrupt JSON in draft file <path>: <json error>` — delivered as a protocol error, not a structured one (F1), but the message is exactly right. ✅
- **Valid JSON, wrong shape** is *not* handled: `{"sections": "not-a-list"}` → `brand_check` fails with `'str' object has no attribute 'get'`; `save_section` with `string indices must be integers, not 'str'`; a top-level JSON list → `'list' object has no attribute 'get'`, and `get_draft` happily **returns the non-dict document as the "draft"**. No schema validation exists on load — only JSON *syntax* is checked. **F11.**
- **Error-but-committed write**: on a draft missing the `rewrite_count` key, `save_section` **persisted the new section and then raised** `KeyError: 'rewrite_count'` while building its return value (the store happens before the return dict is assembled). The client sees an error for a write that actually landed — verified by `final_gate` subsequently reporting violations for the "failed" section. Reachable only with hand-damaged draft files, but it breaks the error⇒no-effect assumption. **F12.**
- A section object missing `text`/`heading` keys: `brand_check` scores it **100** (missing text reads as empty; the vacuity guard lives in the gate, not in `brand_check`), while `final_gate` does catch problems downstream. Informational.

---

## 5. HTTP surface — raw protocol probes

Hand-rolled requests (no SDK), fresh server per run. The SDK/FastMCP layer is standards-conformant throughout:

| Probe | Result |
|---|---|
| `POST /mcp`, Content-Type `text/plain` | **400** `Invalid Content-Type header` |
| `POST /mcp`, malformed JSON body | **400**, JSON-RPC `-32700` Parse error with the JSON fault position |
| `POST /mcp`, initialize without the dual `Accept` header | **406**, `-32600` "Client must accept both application/json and text/event-stream" |
| `POST /wrong-path` | **404** |
| `GET`/`DELETE /mcp` without session id | **400**, `-32600` "Missing session ID" |
| Proper `initialize` | **200**, SSE-framed result, `mcp-session-id` issued |
| `tools/call` unknown tool (in session) | **200**, result `isError: true`, text `Unknown tool: nope_tool` (a result, not a protocol exception — consistent with stdio behaviour) |
| `tools/call` with `arguments` as a string | **200**, JSON-RPC error `-32602` "Invalid request parameters" |
| Non-JSON-RPC body `{"hello": "world"}` | **400**, `-32602` validation error |
| `tools/call` with a bogus session id | **404**, `-32600` "Session not found" |
| Request body size | **413 `Request body too large`** — bracketed between 4 MB (200, processed) and 8 MB (413, rejected in ~8 ms, i.e. before parsing). A 20 MB body is rejected in 72 ms. The exact cap (somewhere in 4–8 MB) is an SDK default and is **not documented** in the README. |

No probe produced a 500, an HTML error page, or a stack trace. ✅ (Reminder, already documented in the README and not re-tested as a flaw: HTTP mode has **no auth** and is localhost-only by design.)

---

## 6. Severity ranking

| # | Severity | Finding |
|---|---|---|
| F10 | **Medium** (known, deferred to P3) | Concurrent writers on one draft lose updates silently: 29/30 sections, 1/2 gate-history entries in testing. Atomic rename prevents corruption but there is no locking/merge. Quantified here; single-client stdio unaffected. |
| F3 | **Medium** | `store_excerpts` accepts string coercion: `"yes"` → `True` **stores excerpts**. The copyright-sensitive flag should require a real JSON boolean. (Same lax-bool surface likely applies to any future bool arg.) |
| F1 | **Medium-low** (API design) | Two incompatible error dialects — structured `{"error"}` results vs protocol `Error executing tool …` — split roughly along "domain validation" vs "draft loading / type validation" lines, and nowhere documented. Every client must handle both; several near-identical situations (unknown profile id vs unknown draft id) land in *different* dialects. |
| F12 | **Low-medium** | `save_section` can report an error *after* committing the write (return-value assembly reads `draft["rewrite_count"]` after `_store_draft`). Error ⇒ no-effect is violated on damaged drafts. |
| F2 | **Low-medium** | ≥~255-char clean ids escape as raw `OSError: [Errno 36]` with the **absolute store path** echoed in the error. No traversal, but unclean and path-disclosing. |
| F11 | **Low-medium** | Stored-draft loading validates JSON syntax only, not shape: wrong-shape files produce bare `AttributeError`/`TypeError` messages, and `get_draft` returns non-dict JSON as a "draft". |
| F6 | **Low** (deployment trap) | Overriding `CONTENT_CORE_STYLE_PROFILES_DIR` orphans the shipped default profile: `get_style_profile("default")` → not-found error and `get_style_profile()` returns `default: null`, because the default lives in the data dir the env var replaces. Embedders (and this project's own test client) who redirect the dir for isolation silently lose `mocbrew-blog-voice`. |
| F4 | **Low** | Path-like ids accepted and persisted verbatim (`save_quality_profile` id `"../../evil"`). Writes stay inside the store (hash-suffixed stem), so this is identifier hygiene, not traversal. |
| F5 | **Low** | Unvalidated dict *contents*: page fields with wrong types → bare `'int' object has no attribute 'split'`; `make_outline` stores an int `heading`; float `target_words` truncated in `make_outline` but rejected in `save_section`; negative `target_words` accepted. |
| F7 | **Low** (docs/UX) | `validate_brief` returns `valid: true` alongside non-empty `refused_claims`. Documented in prose, but the field name invites a client to gate on `valid` alone and write the refused claim. |
| F9 | **Low** | No size guard on stdio inputs; `fact_check` ≈ 11.5 s/MB and `final_gate` ≈ 12 s on a 428 KB draft make multi-MB sections a minutes-long stall. HTTP is capped (~4–8 MB body → 413); stdio is not. |
| F8 | **Informational** | `analyze_style([])` returns a zero fingerprint + warnings while `save_style_profile([])` errors; unknown-format `get_exemplars`/`get_craft_cards` succeed with empty/core-only results; unknown extra arguments are silently ignored everywhere. All defensible — none documented. |

**Not found (positives worth recording):** zero stack traces in ~150 total calls; zero file-system escapes across every path-like id tried on every read/write path; traversal ids uniformly contained by `stored_file_stem`; corrupt-JSON drafts produce named, actionable errors; atomic writes leave no `.tmp` orphans even under a 30-call concurrent storm; stdio↔HTTP parity is exact (58/58); the HTTP error surface is fully JSON-RPC-conformant with no 500s.

---

## 7. Improvement suggestions (not applied — evaluation only)

1. **Unify the error contract (F1):** wrap draft loading in the tools so *all* domain failures return the structured `{"error": …}` shape (or document the two dialects in the README tool table). Cheapest high-value fix for client authors.
2. **Strict booleans for rights-sensitive flags (F3):** make `store_excerpts` reject non-bool input (e.g. validate `isinstance(x, bool)` in the tool, or stricter pydantic config) — a string `"yes"` must never store someone else's passages.
3. **Validate ids at the boundary (F2, F4):** length-cap and charset-check ids in `stored_file_stem` callers (draft/profile/brand/quality) so over-long or path-like ids return the existing clean `invalid id` error instead of `Errno 36` or a persisted `"../../evil"`.
4. **Schema-check stored drafts on load (F11, F12):** a minimal shape check (`dict` with list `sections`, dict `brief`, int `rewrite_count` defaulted) in `_load_draft` would convert the bare `AttributeError`s into the named corrupt-file error, and assembling tool returns *before* storing (or using `.get` with defaults) removes the error-but-committed path.
5. **Validate page/section field types (F5):** coerce-or-reject non-string `heading`/`text`/page fields with field-named errors; pick one float policy for `target_words` and use it in both tools.
6. **Ship the default style profile through the env override (F6):** fall back to the packaged `data/style_profiles/` for reads when the override dir lacks the requested default, or document the trap next to the env var.
7. **Document + bound the big-input behaviour (F9):** state the HTTP body cap (≈4–8 MB, SDK default) in the README, and consider a soft text-size warning/limit for check tools on stdio (fact_check is ~11.5 s/MB).
8. **Concurrency (F10):** already scheduled as P3 locking/versioning — this round's numbers (1/30 sections, 1/2 history entries lost under a mild 2-client race) are the acceptance test for that work.
9. **Rename or annotate `valid` (F7):** e.g. add `"safe_to_write": not refused_claims` to `validate_brief` output so no client has to infer the refusal semantics.

*End of round 7 report.*
