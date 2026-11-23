# Kế hoạch triển khai: Nghiên cứu 5 bài cho mỗi từ khóa

Ngày: 2026-11-23. Trạng thái: đề xuất; chưa triển khai. Đặc tả và tiêu chí A1–A10 nằm trong [seo-five-source-feature.md](seo-five-source-feature.md). Kế hoạch chia thành các vòng có thể review, từ thử nghiệm thủ công đến công cụ dùng thường xuyên.

## 1. Phạm vi và phụ thuộc

- Repo core chuẩn đối chiếu: `6f39a89`; workspace app hiện có code core ở `20d3280` và các file app chưa được Git theo dõi. Trước khi code phải xác nhận checkout/remote, lấy HEAD mới, kiểm tra diff và giữ nguyên thay đổi người dùng; không sao chép đè app hoặc dữ liệu local.
- Trial đầu dùng brand và facts có sẵn, 3–5 từ khóa thật. Chủ động chọn 5 URL Google thủ công; host model phải có khả năng đọc web hoặc người dùng cung cấp nội dung được phép. Không thêm nhà cung cấp LLM hoặc scrape Google.
- Chuẩn chất lượng hiện tại không được hạ để qua trial; các mục research pack tiếp tục duyệt từng mục, WordPress chỉ tạo draft.
- Các dự án tạo brand đầy đủ, search ảnh, import bài WordPress cũ, Search Console và xuất DOCX là công việc riêng; chỉ làm phần tối thiểu cần để trial được lặp lại.

## 2. Vòng 0 — Baseline và chốt hợp đồng

1. Chọn 3–5 từ khóa thuộc brand thật, ít nhất một truy vấn có kết quả pha trộn ý định. Ghi thời gian làm theo cách hiện tại nếu có, dữ liệu brand sẵn có và mục tiêu chuyển đổi.
2. Với từng từ khóa, ghi thiết bị, thị trường, ngôn ngữ, địa điểm, thời điểm, 5 URL organic, vị trí và các URL không đọc được. Xác định người review và rubric chấm tính hữu ích, đúng brand, khớp ý định và CTA trước khi xem bài mới.
3. Lập ví dụ hồ sơ SEO và các tình huống thiếu nguồn, đổi outline, sửa bài sau review, thay facts, export bị ngắt. Đối chiếu A1–A10 để chốt schema, chính sách lưu nguồn và export draft cũ.
4. Ghi baseline, hạn chế dữ liệu và quyết định kỹ thuật trong tài liệu ngắn. Nếu thiếu baseline, ghi thiếu thay vì ước lượng.

**Xong khi:** có bộ brief, nguồn, rubric, các điều kiện dừng và ví dụ dữ liệu được review. Không sửa production data.

## 3. Vòng 1 — Core: hồ sơ SEO và nguồn

1. Thiết kế store riêng gắn `draft_id`, schema version, validation, ghi nguyên tử và khóa cho thao tác đọc-sửa-ghi. Xác định quyền sở hữu, migration và hành vi khi bản ghi hỏng.
2. Thêm các thao tác MCP dự kiến: tạo/đọc hồ sơ, thêm kết quả gốc, ghi trạng thái đọc, thêm nguồn thay thế và lưu phân tích có provenance. Tên cụ thể chốt trong thiết kế; kết quả lỗi theo dialect core.
3. Phân biệt rank quan sát, nguồn gốc và nguồn thay thế; không ghi lại nội dung đã duyệt khi thử lại. Kiểm tra tối thiểu một nguồn đọc được và ngoại lệ thiếu 5 nguồn có lý do, xác nhận rõ.
4. Mở rộng backup/restore cho store, migration và kiểm tra dữ liệu hỏng. Không lưu bí mật hoặc toàn văn đối thủ mặc định.

**Kiểm tra:** test có ý nghĩa cho A1–A4, A7, A9; các tình huống đọc lỗi, nguồn thiếu, URL thay thế, sửa đồng thời, migration và backup/restore. Chạy lại các suite/probe phù hợp với thay đổi.

**Xong khi:** có thể tạo và đọc hồ sơ qua MCP, các nguồn và nhận định truy về được, không tác động facts/config.

## 4. Vòng 2 — Hướng bài và duyệt outline

1. Thêm dữ liệu ý định/phương án, provenance và quyết định người dùng; model gửi đề xuất còn core chỉ xác thực cấu trúc và trạng thái.
2. Thiết kế outline có version, chỉnh sửa, duyệt và supersede; liên kết với draft hiện có và `make_outline`, tránh hai nguồn chân lý về outline.
3. Xây invalidation có thể dự đoán: thay đổi nguồn, brief, hướng hay outline làm hết hiệu lực phê duyệt phụ thuộc; thay bài sau review/cổng làm hết hiệu lực sẵn sàng xuất. Giữ lịch sử thay vì ghi đè.
4. Kiểm tra các trường hợp lặp lệnh, cạnh tranh cập nhật, brief/facts thay đổi, restart và draft cũ; tài liệu hóa quy tắc áp dụng cho draft cũ trước khi triển khai.

**Kiểm tra:** A5–A6; so sánh kết quả các đường draft hiện có, đảm bảo không đổi ngưỡng/gate ngoài phạm vi. Kiểm tra các trạng thái không thể chuyển trực tiếp sang viết/xuất.

**Xong khi:** model và người dùng hoàn thành từ phân tích đến outline đã duyệt qua MCP, có lịch sử và invalidation đúng.

## 5. Vòng 3 — Review và xuất WordPress an toàn

1. Gắn các bản review với hash/phiên bản nội dung, outline và facts liên quan; tách checker xác định, nhận xét model và quyết định người dùng. Danh sách claim cần chuyên môn phải thể hiện người và thời điểm duyệt.
2. Định nghĩa một quyết định sẵn sàng xuất trong core; enforce cho draft SEO mới theo đặc tả. Xử lý tương thích draft cũ theo quyết định đã chốt; không tin trạng thái nút UI.
3. Thiết kế giao dịch export: key ổn định, bản ghi trước khi gửi, trạng thái đang xử lý / thành công / kết quả chưa rõ; đối soát với WordPress trước khi retry. Xử lý ảnh đã upload, timeout, lỗi sau khi post tạo và sau khi lưu. Tránh tự retry mọi thao tác ghi của MCP client.
4. Luôn xuất `draft`, không cập nhật bài đã đăng. Thông báo bước phục hồi và link nháp khi có thể xác định.

**Kiểm tra:** mock WP mô phỏng mất phản hồi trước/sau khi tạo post, lỗi lưu local, chạy lặp và chạy đồng thời; A6–A8; xác minh draft chưa duyệt bị từ chối và không có request tạo post. Chạy suite WordPress/gates và các probe có liên quan.

**Xong khi:** review có dấu vết, điều kiện xuất được enforce ở core và lỗi không chắc chắn không gây retry mù.

## 6. Vòng 4 — App: tạo brief, nguồn, outline, review

1. Đọc hướng dẫn Next.js tương ứng với phiên bản cài trong workspace trước khi viết code; kiểm tra các quy tắc app hiện hành. Khảo sát UI hiện có và giữ dữ liệu/điều khiển người dùng.
2. Thêm luồng tạo brief cho brand đã có; trạng thái thiếu brand/facts/đích chuyển đổi phải rõ và có cách xử lý phù hợp. Không giả định model chạy trong app.
3. Hiển thị hồ sơ nguồn, rank quan sát, tình trạng đọc, nguồn thay thế và ngoại lệ; cung cấp chỉ dẫn trao đổi qua MCP hoặc nhập phân tích thủ công nếu hợp đồng vòng 0 chọn phương án đó.
4. Thêm màn duyệt hướng bài, sửa/duyệt outline, review từng bản và trạng thái xuất. Hiện lỗi core, tiến trình và thông báo khi bản duyệt đã cũ.
5. Nếu thử nghiệm cho thấy cần tạo brand trong app, làm bước tạo brand tối thiểu gọi `prepare_brand_setup` thành hạng mục riêng; facts/persona/full CRUD không mặc định nằm trong vòng này.

**Kiểm tra:** các hành trình A1–A8, A10, trạng thái rỗng/lỗi và mobile; dùng dữ liệu fixture hoặc bản sao, không ghi vào dữ liệu khách. Chạy lint/build và kiểm tra phù hợp với phiên bản app; ghi rõ lỗi còn lại.

**Xong khi:** người dùng có thể thực hiện vai trò của mình trong app và biết chính xác những bước cần model chủ thực hiện.

## 7. Vòng 5 — Trial thực tế và quyết định

1. Chạy 3–5 từ khóa ở vòng 0; tìm 5 URL thủ công, ghi các thay thế, phân tích, duyệt outline, viết và review. Chỉ xuất WordPress khi điều kiện và quyền truy cập đã sẵn sàng; nếu không có thì kết thúc ở bản review.
2. Đo thời gian từng bước, tỷ lệ nguồn đọc được, số lần sửa, claim cần xác minh và kết quả rubric; so sánh baseline có cùng cách đo. Ghi lại cả các case thất bại và sự khác nhau giữa host model.
3. Đối chiếu bất biến: 0 nguồn đối thủ tự vào facts, 0 xuất trái điều kiện, 100% lỗi đọc được báo; những chỉ tiêu khác chỉ đặt ngưỡng sau baseline. Không hứa thứ hạng hay chuyển đổi.
4. Tổng hợp vấn đề theo mức tác động, quyết định sửa tiếp, thay đổi quy trình hay dừng. Chỉ sau trial mới xét chọn nguồn SERP tự động có quyền, chi phí, giới hạn và phương án fallback.

**Xong khi:** có báo cáo số liệu, ví dụ nguồn và bản review, hạn chế, đề xuất vòng tiếp theo. Dữ liệu khách hoặc site thật chỉ thay đổi theo đúng thao tác được ủy quyền.

## 8. Rủi ro và quyết định trước khi xây

| Vấn đề | Cách giảm thiểu / quyết định |
|---|---|
| Host model không đọc được web hoặc trang trả thiếu nội dung | Ghi trạng thái thật, thay nguồn hoặc cho nhập thủ công; không giả bộ đã đọc |
| Search không phản ánh đúng khu vực hoặc ý định pha trộn | Ghi điều kiện; người dùng chọn hướng; không coi top 5 là chân lý |
| AI tạo thông tin sai hoặc copy cấu trúc đối thủ | Provenance, facts đã duyệt, review claim, đóng góp riêng của brand và người review |
| Model review bỏ sót claim y tế | Danh sách cần chuyên gia và xác nhận có người duyệt; không dựa hoàn toàn vào điểm |
| Hai bản outline hoặc review lỗi thời | Một nguồn dữ liệu rõ ràng, version/invalidation và test thay đổi sau duyệt |
| WordPress tạo post nhưng mất phản hồi | Không retry mù; đối soát, trạng thái chưa rõ và mock lỗi đúng thời điểm |
| Backup bỏ store SEO hoặc dữ liệu app đang khác core | Migration/backup/restore và kiểm tra checkout trước thay đổi |

Quyết định còn mở và yêu cầu dữ liệu nằm ở mục 9 của đặc tả. Không coi tài liệu này là phê duyệt triển khai hay cam kết lịch hoàn thành.
