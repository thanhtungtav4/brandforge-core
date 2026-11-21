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
