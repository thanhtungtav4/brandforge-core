# Spec: Lớp GEO — tối ưu bài viết cho máy tìm kiếm AI (Generative Engine Optimization)

Ngày: 2026-11-27. Trạng thái: Smith yêu cầu trực tiếp trong chat ("Thêm GEO vào brandforge (kiểm tra + tối ưu trong core)"); đã ghi quyết định trong `pmdecisions.md` (mục 2026-11-27). Vòng build v1 triển khai cùng ngày.

## 1. Mục đích

SEO truyền thống (trục `seo` trong Quality Profile) lo việc bài được Google xếp hạng và hiển thị đúng. GEO lo một việc khác: khi người dùng hỏi ChatGPT, Gemini, Perplexity hay Google AI Overview, bài viết có được các máy đó **trích xuất và trích dẫn** hay không. Máy trả lời AI không đọc cả bài như người; nó nhặt từng câu/khối tự chứa (self-contained) có thể nhấc nguyên văn vào câu trả lời. Bài viết muốn được trích dẫn phải:

- trả lời thẳng ngay ở mở đầu (answer-first), nêu rõ thực thể/chủ đề chính sớm;
- có các câu sự thật tự chứa, trích nguyên văn được (quotable blocks);
- có số liệu kèm nguồn (theo ai, tổ chức nào, đường link);
- có định nghĩa rõ của chủ đề ("X là …");
- dùng tiêu đề dạng câu hỏi khi tự nhiên (khớp cách người dùng hỏi AI);
- cấu trúc dễ trích xuất: đoạn vừa phải, có danh sách, mục rõ ràng;
- có mục Hỏi đáp (FAQ) cho các câu hỏi thường gặp.

## 2. Kiến trúc: lớp tư vấn (advisory), theo tiền lệ lớp ảnh

GEO v1 đi đúng đường của `content_core/images.py`:

- **Không đổi verdict của `final_gate`**, không đổi 7 trục chất lượng, không đổi trọng số/sàn/PASS_TOTAL trong `quality.py`, không đổi bất kỳ ngưỡng hiện có nào. Kết quả gate/quality của mọi bản nháp hiện hữu giữ nguyên byte-for-byte, ngoại trừ một trường cộng thêm `geo` ở những nơi bản nháp đã có báo cáo GEO (chứng minh bằng test vi phân trong `tests/test_geo.py`).
- Hai tool MCP mới: `check_geo(draft_id)` chạy bộ kiểm tra xác định (deterministic) và lưu báo cáo vào bản nháp; `record_geo_judgement(draft_id, grades, issues)` ghi lớp phán đoán của model chủ (mirror `record_vision_check` của lớp ảnh).
- `check_draft_all` cộng thêm tóm tắt `geo` khi bản nháp đã có báo cáo — chỉ cộng thêm, không thay trường nào khác.
- Phía người viết: `assemble_context` có thêm part `geo_guidance` khi người gọi bật (`geo_guidance=True` hoặc brief có `"geo": true`) — hướng dẫn viết advisory, không phải luật. Bản nháp/cuộc gọi không bật thì context giữ nguyên như trước (cùng kỷ luật với part `depth_material`).

## 3. Bộ kiểm tra xác định (content_core/geo.py)

Tám kiểm tra, mỗi kiểm tra trả `{label, status, score, weight, evidence, suggestion}`; status ∈ `pass` (≥70) / `warn` (40–69) / `fail` (<40) / `not_applicable`. Kiểm tra không áp dụng được thì **loại ra và chuẩn hoá lại trọng số** trên các kiểm tra còn lại (cùng mẫu với trục judged bị thiếu trong `quality.py`) — không bao giờ chấm 0 mặc định.

| Kiểm tra | Đo gì | Khi nào không áp dụng |
|---|---|---|
| `answer_first` | 1–2 câu đầu của section mở đầu nêu thực thể/chủ đề chính và cho câu trả lời trực tiếp (số liệu hoặc định nghĩa) | bản nháp không có chữ |
| `question_headings` | tỉ lệ tiêu đề section dạng câu hỏi (dấu `?` hoặc mẫu "là gì / như thế nào / bao nhiêu / vì sao / có nên / khi nào / ở đâu / thế nào / làm sao" — khớp trên dạng đã bỏ dấu) | ít hơn 2 section có tiêu đề |
| `fact_density` | số câu chứa số liệu trên 1.000 từ (đích ≥6 → 100 điểm, tỉ lệ tuyến tính) | dưới 50 từ |
| `attribution` | tỉ lệ câu số liệu có manh mối nguồn (link, "theo …", tổ chức được nêu tên, "nguồn:") — ≥50% → 100 | không có câu số liệu nào |
| `quotable_blocks` | số câu sự thật tự chứa trích nguyên văn được: 8–45 từ, có số hoặc mẫu định nghĩa, không mở đầu bằng từ nối/đại từ tham chiếu (đích ≥4 → 100) | dưới 50 từ |
| `definition_present` | có câu định nghĩa chủ đề chính ("X là …") trong 2 section đầu | brief không cho biết chủ đề/từ khoá/tiêu đề |
| `structure` | có danh sách; tỉ lệ đoạn nằm trong khoảng trích xuất được (20–120 từ); tỉ lệ section có tiêu đề | dưới 50 từ |
| `faq_present` | có section Hỏi đáp/FAQ, hoặc ≥2 cặp hỏi–đáp trong bài (1 cặp → 50) | format social/email |

Trọng số v1 (**TẠM THỜI — PROVISIONAL**, xem mục 5): answer_first 20, quotable_blocks 20, fact_density 15, attribution 10, definition_present 10, question_headings 10, structure 10, faq_present 5 (tổng 100). `geo_score` là trung bình có trọng số trên các kiểm tra áp dụng được, làm tròn 2 chữ số; không có kiểm tra nào áp dụng được thì `geo_score = None`.

Báo cáo còn có `fixes`: danh sách sửa theo từng section — đầu ra "tối ưu" để người viết/model chủ hành động được (mở đầu chưa trả lời thẳng → đưa câu trả lời lên đầu; section không có số liệu → thêm một sự thật cụ thể có nguồn; đoạn quá dài → tách đoạn; thiếu FAQ → thêm mục hỏi đáp ở section cuối…).

## 4. Lớp phán đoán (judged layer)

Core không đọc hiểu như người; ba khía cạnh sau do model chủ chấm sau khi đọc bài, thang 0/1/2 như mọi rubric trong hệ thống:

- `citability` — bài có câu nào đáng được AI trích nguyên văn không;
- `answer_clarity` — câu trả lời chính có rõ, không vòng vo không;
- `self_contained` — các khối thông tin có đứng độc lập được không (khỏi cần đọc phần trước).

`issues` chọn từ từ vựng cố định: `buried_answer`, `vague_claims`, `missing_context`, `jargon_unexplained`, `not_self_contained`, `no_citable_fact`, `structure_hard_to_extract`. Grade 0 ở bất kỳ khía cạnh nào là **hard flag trong báo cáo GEO** (`hard_flags`) — chỉ trong lớp GEO, không chặn gate. Phán đoán lưu riêng cạnh báo cáo xác định, không trộn vào `geo_score` (điểm xác định phải thuần xác định, tái lập được).

## 5. Điều chưa chốt — để Smith quyết sau khi có số liệu thử

- Trọng số ở mục 3 là **giá trị v1 tạm thời do Ember đặt**, không phải giá trị Smith đã duyệt như trọng số/sàn của Quality Profile. Chúng chỉ sống trong lớp GEO advisory nên không ảnh hưởng verdict nào.
- Câu hỏi trục: GEO sau này (a) gộp vào trục `seo` hiện có, (b) thành trục thứ 8, hay (c) ở yên dạng lớp advisory riêng — **hoãn**, chờ số liệu từ các bài thật (mục 6) rồi Smith quyết. V1 không đụng vào lựa chọn nào trong ba lựa chọn này.

## 6. Thử nghiệm v1

Chạy `check_geo` trên 4 bản nháp shop thật (bài giới thiệu v4 `d-44bf2dc3c8`, A1 `d-c9368a2a16`, A2 `d-2a0202e3c7`, A3 `d-3a01142628`), ghi điểm, kiểm tra yếu nhất và gợi ý hàng đầu vào `evaluation/geo-trial-2026-11-27.md`, kèm ghi chú trung thực về giới hạn của heuristic xác định: nó đếm được *dấu hiệu bề mặt* của tính trích dẫn được (số liệu, định nghĩa, câu hỏi, cấu trúc) chứ không phán đoán được câu trả lời có *đúng và hay* không — phần đó là của lớp phán đoán và của người duyệt.

## 7. Vòng bổ sung 2026-11-28 — 2 đề xuất daily được Smith duyệt

Bằng chứng: baseline GEO đầu tiên trên 4 bài đã xuất (mục 6) — mean 36.72; `answer_first` fail 3/4, `definition_present` fail 4/4, `question_headings` fail 4/4, `faq_present` fail 4/4, `structure` warn 4/4. Hai thay đổi, cả hai đều advisory và chỉ tác động bài viết TƯƠNG LAI (điểm GEO của 4 bài hiện hữu không đổi — chúng không được viết lại):

1. **Lớp mở đầu trả lời + định nghĩa (thẻ `answer-first-geo`).** Instruction của thẻ được mở rộng: bài bắt đầu bằng một *lớp mở đầu trả lời* riêng, 1–2 câu — câu đầu nêu thẳng định nghĩa chủ đề theo mẫu "X là …" hoặc trả lời trực tiếp câu hỏi chính, câu sau kèm một sự thật cụ thể — rồi mới đến phần mở bài bằng cảnh/giọng văn của persona như thường. Lớp trả lời chỉ đứng trước, **không thay giọng văn**, không lặp lại ở các mục sau. Kích hoạt của thẻ giữ nguyên (dormant cho tới khi intent/keyword geo/faq khớp) — bài viết thường không bị thẻ này chạm vào.
2. **Pattern "tiêu đề dạng câu hỏi + FAQ cuối bài" — chỉ bài mua sắm/công thức.** Part `geo_guidance` của `assemble_context` thêm một khối pattern khi brief thuộc phạm vi mua sắm/công thức, và metadata `geo_guidance.patterns` ghi `["question_headings", "faq_closing"]` (rỗng nếu không thuộc phạm vi). **Quy tắc phạm vi** — dùng đúng hai tín hiệu core đã có: (a) `format` thuộc {`product`, `comparison`} — hai format thương mại của core; hoặc (b) `intent` của brief thuộc {`recipe`, `buying-guide`, `shopping`, `how-to`} (chuẩn hoá "_" và khoảng trắng thành "-"; đây là cùng tín hiệu tự do mà cơ chế truy hồi thẻ kỹ thuật khớp intent). Bài tản văn/kể chuyện (format `blog`, không intent mua sắm — hình dạng của bài mùa nước nổi) chỉ nhận guidance gốc: ép tiêu đề câu hỏi + FAQ lên tản văn văn học sẽ đánh nhau với giọng đã duyệt mà không thêm khả năng trích dẫn. Không bật GEO (`geo_guidance`/`"geo": true`) thì không có part nào xuất hiện — context mặc định giữ nguyên byte-for-byte như trước.
