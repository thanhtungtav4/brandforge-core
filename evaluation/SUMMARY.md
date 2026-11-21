# Tổng hợp 8 vòng đánh giá core — 22/11/2026

Code được đánh giá: commit `8215b8c` (213 test). Mọi vòng đều read-only, có bằng chứng tái hiện trong từng báo cáo `round-*.md`.

## Điểm số từng phần

| Phần | Kết quả headline | Độ tin cậy |
|---|---|---|
| 1. Fact-check | 205 case biến thể: **recall 68,1%**, **bắt oan 27,4%**. Giao hàng chỉ bắt 46,7%; claim bắt oan 50% | ⚠️ Yếu nhất |
| 2. Brand linter | 14 cách né từ cấm thành công (ký tự vô hình, homoglyph, fullwidth…); miễn trừ "bảng" né 100% rule độ dài; văn lành bị gắn cờ 12/16 | ⚠️ |
| 3. Craft | Thang chất lượng đơn điệu ✅ nhưng "word salad nhồi từ cảm quan" vẫn 100 điểm; glossary theo brand **không tới được đường chặn** của gate | ⚠️ |
| 4. Style | Vân tay đo chính xác, split-half 92–100 ✅; nhưng văn ngắn mất khả năng phân biệt (hype đạt 100 trước profile Mộc Brew), matcher bỏ qua tỉ lệ câu hỏi/chấm than | Trộn |
| 5. Gates + Quality | Sàn 107/107, cổng cứng 8/8, renormalise chính xác tuyệt đối, parity MCP↔Graph 18/18 ✅✅. 1 lỗi: phi đơn điệu do câu quá dài vẫn tính vào median/nhịp | Mạnh nhất |
| 6. Builder + Fetch | Kit 22/24, claim precision/recall 7/7 trên text render ✅; sitemap index bị cắt ở 3 file, POV hụt brand dạng tiền tố, mù song ngữ | Trộn |
| 7. MCP server | Contract 28/29, parity stdio↔HTTP 0 khác biệt, ~95 call fuzz: 0 crash/0 thoát sandbox ✅. Ghi đồng thời mất dữ liệu (29/30 section); `store_excerpts` nhận chuỗi "yes" thành True | Mạnh |
| 8. Flow end-to-end | Hội tụ rewrite chuẩn ✅, tất định byte-identical ✅. Nhưng: danh sách section phải viết lại "nổ" vì craft tính theo vi phạm chứ không theo điểm; brief chứa claim cấm vẫn chạy tới approved; context phình ~3× do dữ liệu trả trùng 2 dạng | Trộn |

## Backlog cải tiến (xếp theo tác động)

### Tier 1 — đúng/sai & niềm tin (nên làm trước)
1. **Fact-check hiểu ngữ cảnh tiền**: phí ship, tổng tiền, giảm giá, giá cũ, giá/kg đang bị xử như "giá gói sai" → nguồn bắt oan lớn nhất. Kèm: mở rộng mẫu câu giao hàng (vòng/sau/làm việc/hỏa tốc/24 giờ), bỏ miễn trừ thống kê theo số fact (185/250/1000), bắt cặp origin hoán đổi toàn phần. (R1)
2. **Đấu `brand_id` vào gates/quality**: glossary theo brand đã làm ở vòng hiệu chuẩn nhưng đường chặn (final_gate, trục clarity) chưa truyền brand_id → vẫn chấm mọi brand bằng từ điển Mộc Brew. (R3-S4)
3. **Craft failing_sections theo điểm** như brand linter, thay vì "có bất kỳ vi phạm nào là fail" → hết nổ danh sách viết lại. (R8)
4. **Gia cố normalisation của linter**: loại ký tự zero-width/format, gấp NFKC + confusable cho thuật ngữ Latin; thu hẹp miễn trừ bảng (chỉ hàng bảng thật, không phải câu chứa 2 dấu `|`). (R2)
5. **Style matcher dùng thêm tỉ lệ câu hỏi/chấm than**; khử trùng mẫu trong analyze_style; sửa mất phân biệt ở văn ngắn. (R4)
6. **Loại câu vượt trần khỏi thống kê median/nhịp** — sửa gốc lỗi phi đơn điệu, thay đổi nhỏ. (R5)

### Tier 2 — bền vững & hoàn thiện
7. Refusal ở brief thành quyết định routing trong graph (dừng, hoặc đòi override rõ ràng). (R8)
8. assemble_context: một dạng biểu diễn duy nhất + ngân sách mềm theo từng phần. (R8)
9. Fetch: luôn đọc sitemap (không chỉ fallback), nâng/ghi rõ trần sitemap index, canonical URL + provenance theo URL cuối, cờ truncated cho fetch_site. (R6)
10. POV cho brand tự xưng bằng tiền tố tên ("Ong Rừng …"). (R6)
11. MCP: bool nghiêm ngặt cho `store_excerpts`, hợp nhất một dialect lỗi, validate độ dài id, kiểm tra shape khi load draft. (R7)
12. save_section giữ intent/target_words; final_gate trên draft rỗng trả hướng dẫn rõ; draft đã approved thì sửa là mất trạng thái approved. (R8)
13. Linter: nhận biết phủ định/trích dẫn cho từ cấm; cân lại phạt (chấm than không trần vs câu dài phạt phẳng −2). (R2)
14. Craft retrieval: khớp keyword theo biên từ, thêm intent hook/conclusion/sales + intent tiếng Việt, xem lại cap 3/slot core. (R3)
15. Sensory check: tối thiểu nhận biết phủ định ("không có mùi cacao" đang được tính là có chi tiết cảm quan). (R3)

## Điểm mạnh đã kiểm chứng (giữ nguyên, đừng phá)
- Lõi số học của gates/quality: sàn, cổng cứng, renormalise, parity hai đường — không lỗi nào qua ~5.000 property check.
- Bề mặt MCP: fuzz sạch, parity hoàn hảo, ghi atomic không hỏng file.
- Đo vân tay style: số liệu khớp đếm tay; builder: precision claim trên text render tuyệt đối.
- Flow tất định hoàn toàn (trừ uuid draft_id theo thiết kế).

---

## Post-fix re-measurement — vòng sửa Tier 1 + Tier 2 (22/11/2026)

Cả 15 mục backlog đã được sửa theo quy trình test-fail-first (file test mới
`tests/test_backlog_fixes.py`, 79 test). Probe đo lại được dựng lại từ chính
các báo cáo round-*.md và lưu bền trong `evaluation/probes/` để chạy lại được
ở mọi commit; mỗi probe được chạy trên cả code trước sửa (worktree `8215b8c`)
và sau sửa với **cùng một bộ case** — cột "trước" dưới đây là số đo trên bộ
case dựng lại (số gốc của vòng đánh giá ghi trong ngoặc khi khác).

| Probe | Trước | Sau | Ghi chú |
|---|---|---|---|
| Fact mutation 205 case (`probes/fact_probe.py`) — recall | 63,8% (gốc: 68,1%) | **81,9%** (95/116) | Giao hàng 6/15 → **15/15**; thống kê số 8/12 → **12/12**; weight 7/10 → **10/10**; origin 8/14 → 12/14; money 9/15 → 10/15 |
| Fact mutation — bắt oan (false-flag) | 32,1% (gốc: 27,4%) | **15,5%** (13/84) | Money 5 → **0**; thống kê 6 → **0**; giao hàng còn 1 (câu "dùng trong 5-7 ngày" hết bị bắt oan giao hàng, chỉ còn cờ claim "ngon nhất" ngoài phạm vi vòng này) |
| Fact mutation — case `[NEEDS SOURCE]` xử lý đúng | 3/5 (gốc: 4/5) | **4/5** | Percent có đánh dấu giờ được ghi vào `flagged_claims` (mục 1e); case còn lại là claim không thuộc danh mục nào của checker ở cả hai phiên bản |
| Linter né từ cấm (`probes/linter_probe.py`) | 14/14 né được | **5/14** | Đã bắt: zero-width ×4, homoglyph Cyrillic ×2, fullwidth, digit-guard ×2. Còn né: letter-spacing, letter-dotting, tách bằng thẻ HTML, HTML entity, chèn từ giữa cụm — đều là các mục LOW của R2, ngoài phạm vi 15 mục đã duyệt |
| Pipe-disguise (câu 60 từ chứa 2 dấu `\|`) | né hoàn toàn, điểm 100 | **bị bắt** `sentence_too_long`, điểm 86 | Miễn trừ bảng đã thu hẹp đúng hàng bảng thật |
| Từ cấm trong phủ định / trích dẫn | hard `banned_term`, −15 | **`banned_term_context` (warning, không trừ điểm, không tính vào cổng cứng)**; khẳng định vẫn hard | Mục 13a |
| Sweep đơn điệu 420 case (`probes/sweep_probe.py`) — tổng nghịch đảo | 63 (gốc: 64) | **23** | Riêng injector "thêm câu 26 từ" (cơ chế R5 §1b/§1c): **40 → 0** — đã diệt gốc (mục 6). 23 case còn lại đến từ injector từ cấm/từ yếu làm thay đổi thống kê độ dài câu (median/nhịp) — số đếm y hệt trước/sau, không phải hồi quy của vòng này, và bằng 0 trong sweep gốc của R5 với phân bố base khác |
| Style: Đỉnh Cao hype vs profile Mộc Brew (`probes/cnm_style_probe.py`) | 100,0 | **88,0** | `exclamation_rate` fail + trần văn ngắn 88 (mục 5) |
| CNM 5 bài thật — craft | 90 ×5 | **90 ×5** ✅ | Không hồi quy |
| CNM 5 bài thật — brand violations | 10 | **10** ✅ | Số vi phạm giữ nguyên; điểm brand giảm nhẹ 1–4 điểm/bài (96/96/98/96/94 → 95/93/97/94/90) do mục 13b: phạt `sentence_too_long` nay tỉ lệ với độ vượt trần thay vì phẳng −2 — thay đổi phạt đã được duyệt, mọi bài vẫn ≥ 90 |
| CNM 5 bài thật — style_match | 92/100/92/100/92 | **92/100/92/100/92** ✅ | |
| CNM writer trial — style_match | 85,0 | **85,0** ✅ | Ban đầu tụt 77 vì metric `question_rate` mới phạt một section 8 câu/0 câu hỏi trước profile 22,92 — đã sửa bằng hạng tử nhiễu lấy mẫu (200 / số câu) trong `_within`, có test hồi quy riêng |
| MCP parity stdio↔HTTP (`probes/parity_probe.py`) | 0 khác biệt (R7) | **16/16 call giống hệt** ✅ | |

Phần fact-check còn hụt sau vòng này tập trung ở các defect **ngoài phạm vi
15 mục đã duyệt**: ngày rang theo mệnh đề (R1-D5), phủ định claim diện rộng
(D6), paraphrase claim (D11), ngữ pháp số-từ chuỗi (D10), tiền viết bằng chữ /
ngoại tệ / số không kèm đơn vị (D8), origin ngoài danh sách + xuyên câu, và
case 500g gây tranh cãi do brief lệch facts file (D13). Recall theo nhóm còn
yếu nhất: claims 11/18, word-stats 7/12, roast 11/12 (bắt oan 4/9 không đổi).

Bộ suite sau vòng sửa: pytest **292 passed** (213 gốc + 79 test backlog),
run_golden 7/7, run_model_golden 6/6, test_mcp_client 22/22, test_mcp_http
PASS, 3 demo OK. Bốn pin cũ được cập nhật có chủ đích (không nới cổng nào):
(1) `test_validate_brief_refuses_health_claim` — `valid` nay là False khi có
refused claim, đúng ngữ nghĩa mục 7; (2) golden g6 trong `runner.py` — brief
bị từ chối nay kết thúc ở status `refused` trước khi soạn thảo, tiêu chí kiểm
tra chặt hơn (không có draft nào được tạo); (3+4) số metric của style_match
6 → 8 ở `test_style.py` và `test_mcp_client.py` do mục 5a; (5) sanity count
trong test divergence của calibration 5 → 2 do khử trùng mẫu (mục 5b); (6)
hai test `assemble_context` đọc shape cũ chuyển sang `counts`/`context_text`
theo shape mới của mục 8 (đã ghi trong README).

---

## Core completion — vòng đóng core (22 Nov 2026)

Vòng cuối của core trước khi scaffold product (chỉ đạo của Smith: hoàn
thiện core trước, UI sau). Sáu workstream A–F, kỷ luật fail-first như
các vòng trước; test mới ở `tests/test_core_closing.py` (47 test).
Không thay đổi bất kỳ giá trị thiết kế nào Smith đã duyệt (craft bar
70, brand pass 85, quality presets/floors/pass_total).

### A — Fact base theo từng brand (defect cấu trúc của calibration): ĐÓNG

- Store mới `data/facts_profiles/<brand-id>.json` (đúng pattern của
  style/quality profiles; env `CONTENT_CORE_FACTS_PROFILES_DIR`).
  `loaders.load_facts(brand_id="moc-brew")`: profile đã lưu → dùng;
  moc-brew chưa lưu → file shipped `data/facts_lorebook.json` (hành vi
  byte-equivalent); brand lạ → **ValueError, không bao giờ fallback
  sang facts của brand khác**.
- `save_facts`/`get_facts` là 2 MCP tool mới (29 → **31 tool**),
  validate schema trước khi ghi + atomic write như `save_brand_draft`.
  Schema facts mở rộng: `known_prices_vnd` (giá đã xác minh ngoài gói
  chính), `pack_weights_g` (quy cách thật của brand); facts không có
  `delivery_days`/`roast_weekday` thì check tương ứng được BỎ QUA thay
  vì phán theo khoảng rỗng.
- Mọi đường check đều resolve theo brand đang active: `fact_check`
  (param `brand_id` → `brief.brand_id` → server brand; brand lạ cần
  brand draft đã lưu + facts profile, thiếu là structured error),
  `final_gate`/`quality_score` (theo `brief.brand_id` của draft),
  `graph.Core` (facts theo `brand["brand"]["id"]`).
  `brand_check`/`assemble_context` vẫn là server-brand trong v1 —
  chúng không dùng fact base và không nhận brand_id; ghi nhận rõ ở đây.
- **Proof (case fail của calibration, nay là test):** facts profile
  `cao-nguyen-moc` dựng chỉ từ số đã xác minh trong
  `calibration/caonguyenmoc-report.md` (250g 89.000đ, 500g 179.000đ,
  1kg 359.000đ, điều shop 99/199/379k, Robusta Đức Cơ). Draft
  writer-trial của CNM: **0 fact issue dưới facts CNM**, vẫn
  `wrong_price` dưới facts Mộc Brew.
- Bài story của chính CNM dưới facts CNM: **11 issue** (so với 23 dưới
  facts Mộc Brew) — phần còn lại là *đúng*: giá điều cũ trên blog
  94/169/339k (shop: 99/199/379k), giá 119.000đ không nằm trong tập đã
  xác minh, và một mention 12g ngoài quy cách. Không ép về 0.

### B — Fact-check defects còn lại: ĐÓNG (trừ các non-goal ghi rõ)

- **D5** roast theo mệnh đề + phủ định cục bộ: câu tách theo `,`/`;`,
  chỉ mệnh đề chứa "rang" mới bị check; "không rang" bị vô hiệu khi
  "rang" khẳng định lại sau nó trong cùng mệnh đề; phủ định hậu tố
  ("thứ Tư không phải ngày rang") được tôn trọng.
- **D6** phủ định claim diện rộng: ngữ pháp phủ định nới rộng (bare
  "không", "không khẳng định", "chưa từng nhận", "chưa hề"...) dùng
  chung linter/fact_check; thêm luật phủ định speech-act trong cửa sổ
  24 ký tự không qua dấu phẩy ("không khẳng định hạt ngon nhất");
  "không chỉ" vẫn là khẳng định. Claim gán cho đối thủ / dạng câu hỏi
  tường thuật → `flagged_claim`, không block (ca ngợi của khách vẫn
  block như R1 đã chốt).
- **D11** paraphrase claim: chèn 1 từ ("chữa được mất ngủ"), số bằng
  chữ ("số một"), markup/chèn khoảng trắng qua matcher dùng chung với
  linter; thêm cụm "đứng đầu việt nam", "thần dược", "hết mất ngủ".
- **D10** số-từ chuỗi: "hàng trăm nghìn", "hàng chục nghìn", lượng từ
  + thang ("cả nghìn"), thang trần ("Nghìn khách hàng").
- **D8** tiền trần trong ngữ cảnh giá ("giá 200.000") + số bị ngắt
  dòng ("200.\n000đ"); lookbehind chặn "đánh giá/định giá/danh giá",
  lookahead loại đơn vị thống kê và gram.
- Origin: cặp bean↔region derive từ `origins` của facts doc đang
  active; pairing xuyên câu qua đại từ ("Hạt này...") trong cửa sổ 2
  câu; region quốc tế (Ethiopia, Colombia...) + marker "nhập từ";
  `unknown_origin` scope theo mệnh đề + loại mệnh đề về đối thủ.
- **D13 (case 500g gây tranh cãi): document-only** theo đúng khuyến
  nghị của round report — facts file mơ hồ (một giá/250g + gói 500g
  thật của brand khác), không đoán. Probe vẫn ghi 1 false-flag ở nhóm
  weight; đó là D13, không phải hồi quy.

### C — Linter: ĐÓNG

- 5 evasion cuối bị bắt: letter-spacing ("s e a m l e s s"),
  letter-dotting ("s.e.a.m.l.e.s.s"), tách bằng thẻ HTML, HTML entity,
  chèn từ giữa cụm ("seam less" / split-token). Cơ chế: strip markup
  (unescape + tag→space), collapse chuỗi ký tự đơn ≥5 token,
  fuzzy span (exact + chèn 1 token + split-token nối liền) dùng chung
  cho banned terms và claims; scan thêm một lượt toàn văn vì bộ tách
  câu xé nát dạng dotting. Guard false-positive: term <5 ký tự không
  split-match; câu chứa banned-term dạng KHẲNG ĐỊNH bị loại khỏi pool
  median (dạng phủ định/trích dẫn thì không).
- **Probe linter: 5/14 → 0/14 evasion.**

### Sweep đơn điệu (R5 residual): 23 → 9

- Cơ chế gốc: một câu vi phạm được append làm thay đổi thống kê độ
  dài (median của linter / stdev nhịp của craft) đủ để xoá phạt
  monotone, và trong tổng quality còn nặng hơn cả phạt của chính nó.
- Linter: câu mang banned-term khẳng định bị loại khỏi pool median
  (cùng nguyên tắc loại câu over-max của R5).
- Craft: thử loại khỏi pool nhịp trước — **đo được là tệ hơn (23→24)**
  vì bỏ một câu gần trung bình làm stdev của phần còn lại nhảy khó
  lường và pool rớt dưới ngưỡng 4 câu. Cách sửa cuối: **median
  imputation** — câu vi phạm (weak/filler/banned khi caller cấp) đóng
  góp độ dài bằng median của pool: không giúp cũng không hại verdict
  nhịp, và bị phạt tại chỗ nó đứng.
- Còn lại **9 case, tất cả cùng một cơ chế**: injector từ yếu append
  "tuyệt vời" làm câu cuối dài hơn → median câu của *brand layer* vào
  band (90→100). Brand layer theo thiết kế không phạt từ yếu (đó là
  việc của craft, và craft có phạt −6); sửa nó đòi linter nhập
  glossary của craft — phá nguyên tắc tách lớp Persona ≠ Brand ≠
  Facts ≠ Craft. Ghi nhận là residual đã hiểu rõ, không phải ẩn số.
  Không injector nào tệ đi: banned 4→0, weak 19→9, exclamation 0,
  too_long 0.

### D — Craft: ĐÓNG

- `get_exemplars("blog")` hết trả []: một mapping format duy nhất
  (`EXEMPLAR_FORMAT_ALIASES`, blog → blog_intro) trong craft.py;
  `assemble_context` dùng chung thay vì bản copy riêng.
- `repeated_openings` key bằng **2 token đầu**: "Cà phê/Cà chua/Cà
  tím" hết gộp; chủ ngữ brand + động từ khác nhau và bước liệt kê
  không bị phạt oan. CNM 5 bài: craft **90 ×5 trước và sau** (artefact
  −10 cũ đến từ key 1-token trên các mở đầu khác nhau — sau sửa các
  bài vẫn 90 vì artefact đó không nằm trong 5 bài này; không bài nào
  tụt). Một pin cũ trong `test_craft.py` (MONOTONE_TEXT) kỳ vọng
  repeated_openings cho 4 câu "Mình + 4 động từ khác nhau" — đó chính
  là artefact R3 mô tả; pin đã cập nhật có ghi lý do.
- Word-salad-100: **document-only, có đo đạc.** TTR của salad = 0.95
  (62 từ) nhưng exemplar social của chính project = 0.91 (64 từ) và
  section email golden tới 0.92–0.95 (20–49 từ) — không ngưỡng nào
  tách salad khỏi copy ngắn hợp lệ. craft.py ghi rõ: điểm craft chứng
  nhận *không có* các defect bề mặt đã đo, không chứng nhận mạch lạc;
  coherence thuộc về model viết + người duyệt.

### E — Style: ĐÓNG

- `ALGORITHM_VERSION = 2`: profile mới mang stamp +
  `consistency_detail` (per-sample key metrics). `refresh_profile`
  chạy ở mọi đường load phục vụ (MCP `_resolve_style_profile`,
  `get_style_profile`, trong `style_match`): profile cũ được tính lại
  warnings từ bằng chứng đã lưu; không có per-sample metrics thì
  divergence warning bị **bỏ** kèm ghi chú "could not be re-verified"
  thay vì phục vụ đông cứng.
- Profile CNM shipped được rebuild bằng code hiện tại: warning
  "samples diverge on first_person_rate (CV 2.0) — possibly mixed
  authors" **biến mất** (warnings: []), giữ nguyên id/name/qualitative,
  brand_id cao-nguyen-moc, store_excerpts=False. Profile Mộc Brew
  rebuild qua `build_default_style_profile.py`.
- Fingerprint: hàng bảng markdown bị loại khỏi thống kê câu (dùng
  `is_table_like` của linter); TTR tính trên cửa sổ 500 token đầu
  (hết confound độ dài). Style CNM không hồi quy: posts
  92/100/92/100/92, trial 85, Đỉnh Cao hype 88 — y hệt trước vòng.

### F — MCP concurrency: ĐÓNG

- `_draft_lock`: lockfile O_EXCL theo draft, retry bounded 10s, lock
  cũ hơn 30s coi là stale và steal, release trong finally; timeout →
  structured `{"error": ...}`. Bọc cả 3 đường read-modify-write:
  `save_section`, `final_gate`, block store của `make_outline`.
- Repro R7 nay pass: 2 process × 15 `save_section` → **30/30 section**
  (trước 29/30); 2 `final_gate` đồng thời → **đủ 2 gate_history**
  (trước 1/2). Single-writer không đổi hành vi.

### Số đo trước/sau (baseline = sau Tier 1+2)

| Probe | Trước | Sau |
|---|---|---|
| Fact recall (`fact_probe.py`) | 81.9% (95/116) | **96.6% (112/116)** |
| Fact false-flag | 15.5% (13/84) | **2.4% (2/84)** |
| Fact theo nhóm | claims 11/18 · roast 11/12 FF 4/9 · origin 12/14 · wordstats 7/12 | claims 17/18 · roast **12/12 FF 0** · origin **14/14 FF 0** · wordstats 11/12 · stats 12/12 · money 13/15 FF 0 |
| Linter evasions | 5/14 | **0/14** |
| Sweep inversions | 23 (banned 4 / weak 19) | **9 (banned 0 / weak 9)** |
| CNM craft / brand violations / style | 90 ×5 / 10 / 92·100·92·100·92 | **90 ×5 / 10 / 92·100·92·100·92** ✅ |
| CNM writer trial — fact layer | FAIL wrong_price (facts Mộc Brew) | **PASS dưới facts CNM**; vẫn fail dưới facts Mộc Brew ✅ |
| MCP parity stdio↔HTTP | 16/16 | **16/16** ✅ |

2 false-flag còn lại của fact probe: case 500g của D13 (document-only,
ở trên) và một câu khuyên bảo quản chứa "ngon nhất" bị bắt như claim
tươi ngon (nhóm F10 của calibration — ngoài phạm vi vòng này). 3 case
recall còn hụt đều là non-goal đã ghi: tiền viết bằng chữ, claim tách
xuyên 2 câu, số-từ dưới thang ("sáu mươi").

### Còn mở sau vòng này (và vì sao)

- **D13** — facts file mơ hồ theo chính round report; document-only.
- **Tiền viết bằng chữ / ngoại tệ, claim tách xuyên câu, số-từ dưới
  thang** — non-goal đã tuyên bố từ R1/Tier 1+2: cần parser ngữ nghĩa,
  rủi ro false-flag cao hơn giá trị bắt được ở tầng deterministic.
- **9 sweep inversions** — cơ chế median-xuyên-lớp đã phân tích ở mục
  C; sửa đòi phá tách lớp hoặc đổi trọng số Smith đã duyệt.
- **Word-salad / coherence** — không tồn tại signal deterministic
  tách được (đo ở mục D); thuộc về model + người duyệt.
- Parity probe giữ nguyên 16 call (chưa thêm get_facts/save_facts
  vào bộ parity) — hành vi 2 tool mới được test trực tiếp ở
  test_core_closing + test_mcp_server/client/http.

### Suite sau vòng

pytest **339 passed** (292 + 47 test core-closing), run_golden 7/7,
run_model_golden 6/6, test_mcp_client 22/22, test_mcp_http PASS
(31 tools), 3 demo OK. Pin cập nhật có chủ đích: (1) tool count
29 → 31 ở test_mcp_server / test_mcp_client / test_mcp_http /
test_craft / test_brand_extract + README; (2) kỳ vọng
repeated_openings trong `test_craft.py::test_craft_check_flags_monotone_and_repeated_openings`
(mục D). Không pin nào thuộc nhóm giá trị Smith duyệt bị đụng tới.
