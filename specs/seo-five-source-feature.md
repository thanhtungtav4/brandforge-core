# Đặc tả: Nghiên cứu 5 bài và viết nội dung SEO theo brand

Ngày: 2026-11-23. Trạng thái: **đề xuất để review; chưa triển khai**. Tài liệu này mô tả luồng cho bài mới. Tên tool, schema và các ngưỡng thử nghiệm là đề xuất cần chốt khi triển khai; không mặc định là quyết định sản phẩm đã ban hành.

## 1. Mục tiêu

Với một từ khóa và một brand, người dùng nghiên cứu 5 bài trong kết quả Google, phân tích nhu cầu tìm kiếm, duyệt dàn ý, viết bài có giá trị riêng của brand, review và giao thành nháp WordPress. Hệ thống lưu đủ nguồn và quyết định để lần sau mở lại biết bài được xây từ đâu.

Người dùng phase 1 là chủ dịch vụ nội dung, thao tác trên máy local. Model của ứng dụng chủ tìm, đọc, phân tích và viết; core lưu dữ liệu, kiểm tra các luật xác định, giữ trạng thái duyệt và kết quả. Model có thể đưa nhận định có giải thích, nhưng hệ thống không gọi nhận định đó là kết luận xác định hay bằng chứng rằng bài sẽ lên top.

## 2. Hiện trạng và phạm vi

- Core đã có `start_draft`, `make_outline`, `save_section`, `check_draft_all`, `final_gate`, facts theo brand và xuất WordPress draft. `seo_check` hiện kiểm tra title, heading, keyword và meta cơ bản; chưa nghiên cứu SERP.
- Research pack hiện phục vụ cấu hình **brand**, không phải nghiên cứu **một bài**. Hồ sơ SEO ở spec này là store riêng theo draft; nguồn đối thủ trong đó không tự trở thành fact, cấu hình brand hay corpus.
- App hiện chủ yếu xem draft và duyệt pack; chưa có luồng tạo brief, hồ sơ SEO và duyệt outline hoàn chỉnh.
- V1 thử nghiệm dùng kết quả Google do người dùng tìm và nhập URL; model chủ đọc và phân tích. Không mặc định có công cụ tìm kiếm, khả năng đọc web hoặc tích hợp LLM trong core.
- Bài cũ trên WordPress, ảnh tìm kiếm và xuất file không qua WordPress thuộc spec hoặc vòng riêng. Có thể dùng dữ liệu brand đã tồn tại cho trial; không chặn V1 bằng việc xây toàn bộ màn hình quản trị brand.

## 3. Luồng người dùng

1. **Tạo brief:** chọn brand, nhập từ khóa chính, chủ đề, mục tiêu bài, mục tiêu chuyển đổi; ghi thị trường, ngôn ngữ, địa điểm và ngày tìm kiếm. Chọn trang đích / thông tin liên hệ đã xác minh của brand hoặc đánh dấu chưa có.
2. **Ghi kết quả:** người dùng tìm Google, nhập 5 URL bài organic đầu tiên quan sát được, tiêu đề và vị trí 1–5. Ghi rõ nguồn thủ công và ngày giờ; không coi quảng cáo hay AI overview là bài organic. Kết quả tùy thời điểm và bối cảnh, không coi là bảng xếp hạng vĩnh viễn.
3. **Đọc nguồn:** model chủ đọc từng URL nếu có khả năng truy cập; người dùng có thể cung cấp nội dung được phép dùng. Ghi trạng thái đọc, lý do thất bại và độ đầy đủ. Nếu nguồn không phù hợp/không đọc được, giữ bản ghi ban đầu và ghi thêm URL thay thế cùng thứ hạng quan sát, lý do thay thế; không tự nhận là top 5 ban đầu.
4. **Phân tích:** model ghi cho từng bài loại trang, ý định ước đoán, headings/chủ đề, câu hỏi được trả lời, nguồn dẫn và gợi ý chuyển đổi. Sau đó tổng hợp chủ đề chung, khác biệt, ý định chính/phụ, bất đồng, phần còn thiếu và cơ hội brand có thể bổ sung. Mỗi nhận định có nguồn hoặc đánh dấu là suy luận; phân tích chưa được duyệt không thành dữ liệu cấu hình sống.
5. **Duyệt hướng bài:** người dùng xác nhận ý định, đối tượng, giá trị riêng và mục tiêu chuyển đổi. Nếu các kết quả phục vụ các ý định khác nhau, hiển thị các phương án và lý do trước khi chọn.
6. **Đề xuất và duyệt outline:** model đề xuất thứ tự các mục, mục đích, câu hỏi cần trả lời, nguồn tham khảo và mục dự kiến dẫn CTA. Người dùng sửa và duyệt; lưu phiên bản đã duyệt trước khi viết.
7. **Viết và review:** model viết theo outline, fact đã duyệt, voice và giá trị riêng của brand. Core chạy checker hiện có; model có thể bổ sung nhận xét về đáp ứng ý định và CTA, có dẫn chứng vị trí trong bài. Người dùng xem lỗi, nguồn, mục thiếu và quyết định sửa hoặc duyệt. Claim cần chuyên môn phải được người có năng lực phù hợp kiểm tra.
8. **Giao:** khi điều kiện ở mục 7 đạt, xuất thành bản nháp WordPress. Người dùng/khách xem và tự đăng. Ghi kết quả xuất và liên kết; lỗi phải cho phép đối soát, tránh tạo bản trùng khi kết quả không chắc chắn.

### Thiếu nguồn

Mặc định cần 5 nguồn phù hợp đã đọc đủ để đề xuất outline. Nếu không đạt, hiển thị số lượng và lý do. Người dùng có thể bổ sung nguồn hoặc **chủ động tiếp tục với ít hơn 5**, ghi lý do và xác nhận giới hạn; không gắn nhãn “đã phân tích đủ 5 bài”. Không cho phép tiếp tục với 0 nguồn đã đọc. Đây là ngoại lệ có dấu vết, không làm thay đổi bản ghi thứ hạng ban đầu.

## 4. Dữ liệu và trạng thái đề xuất

Một hồ sơ SEO gắn một `draft_id` và `brand_id`, có `schema_version`, `created_at`, `updated_at` và phiên bản. Mỗi nguồn có `source_id`, URL, tiêu đề nếu có, `observed_rank`, `observed_at`, ngữ cảnh tìm kiếm, nguồn nhập `manual`, loại trang, `read_status` (`pending`, `read`, `partial`, `failed`, `excluded`), lý do, thời điểm đọc, dấu hiệu nội dung bị cắt và `replaces_source_id` nếu là nguồn thay thế. Không suy đoán trạng thái đọc từ việc có URL.

Phân tích có danh sách nhận định `{id, text, source_ids, kind, confidence}`; `kind` phân biệt thông tin được dẫn nguồn và suy luận. Intent có thể chưa rõ hoặc có nhiều phương án. Outline có `version`, các mục, provenance và trạng thái `proposed`/`approved`/`superseded`; lưu nội dung và thời điểm phê duyệt, người duyệt là chủ local. Nội dung viết và review phải tham chiếu phiên bản outline được duyệt.

Trạng thái quy trình dự kiến: `collecting → analyzing → direction_review → outline_review → writing → reviewing → ready_to_export → exported`. Các lỗi đọc, thiếu nguồn và cần sửa là điều kiện/chi tiết tại bước hiện tại, không tự biến thành trạng thái thành công. Thao tác phải kiểm tra điều kiện chuyển trạng thái ở core; UI chỉ hiển thị và gửi yêu cầu.

Nếu nguồn, hướng bài, brief hoặc outline đã duyệt thay đổi, tạo phiên bản mới và đánh dấu các phê duyệt và review phụ thuộc là hết hiệu lực; bài phải được xem lại trước khi xuất. Nếu nội dung draft sửa sau cổng hoặc review, điều kiện sẵn sàng xuất mất hiệu lực. Lưu lịch sử; không âm thầm đổi phiên bản đã duyệt.

Nguồn đối thủ chỉ lưu metadata và phân tích vừa đủ để kiểm chứng; không mặc định lưu toàn bộ bài hoặc biến đoạn văn đối thủ thành mẫu văn phong. Quy định truy cập, bản quyền và nguồn được phép cần được tôn trọng. Sao lưu/phục hồi phải bao gồm store SEO mới, không chứa bí mật; chỉ triển khai khi đã thiết kế migration và tương thích backup hiện tại.

## 5. Giao diện V1

- **Danh sách bài:** nút tạo brief; danh sách trạng thái và mục cần xử lý tiếp.
- **Tạo brief:** trường ở mục 3.1; tạo draft qua core và hiển thị lỗi nếu brand/facts chưa sẵn sàng.
- **Nguồn và phân tích:** 5 vị trí ban đầu, thêm nguồn thay thế, trạng thái đọc, bằng chứng, mâu thuẫn ý định và cảnh báo thiếu nguồn.
- **Outline:** xem nguồn/lý do theo mục, sửa thứ tự và nội dung, duyệt phiên bản.
- **Review:** kết quả core, nhận xét model, checklist claim/CTA và các mục cần người dùng quyết định. Báo rõ thiếu dữ liệu hoặc lỗi core; không hiển thị lỗi như trạng thái rỗng.
- **WordPress:** trạng thái đủ điều kiện, lý do bị chặn và link nháp sau xuất. Không nhập Application Password qua UI trong V1.

App hiện là lớp vỏ; với model chủ có khả năng web, thao tác phân tích/viết có thể diễn ra qua MCP. V1 không giả định có model tích hợp trong app. Giao diện phải chỉ rõ bước nào thực hiện trong app và bước nào do model chủ thực hiện; có đường nhập nội dung phân tích thủ công cho trial.

## 6. Quy tắc dữ liệu và an toàn nghiệp vụ

1. Không tự thêm nội dung đối thủ vào facts, profile giọng viết hay cấu hình brand; chỉ fact đã được chủ duyệt mới dùng làm fact của brand.
2. Phân tích, outline và nhận định review do model tạo là đề xuất; người dùng xác nhận ý định và duyệt outline. Research pack tiếp tục duyệt từng mục một, không có duyệt hàng loạt.
3. Không tự phát minh giá, công dụng, cam kết, năng lực chuyên môn, ưu đãi, địa chỉ hoặc URL. Thiếu thông tin phải nêu rõ và yêu cầu kiểm chứng.
4. Không copy / ghép lại nội dung đối thủ làm bài hoàn chỉnh. Bài cần thông tin và giá trị riêng có nguồn hợp lệ của brand; review tương đồng và đọc thủ công là hỗ trợ, không chứng minh tuyệt đối không vi phạm bản quyền.
5. Nội dung y tế và claim cần chuyên môn có trạng thái cần người phù hợp duyệt. Không coi điểm model hay qua cổng tự động là thay thế chuyên gia.
6. Luồng SEO mới chỉ xuất khi outline và review hợp lệ cho phiên bản nội dung hiện tại, cổng cuối đạt, không còn mục kiểm chứng bắt buộc. Core cưỡng chế; WordPress luôn `draft`. Phạm vi xử lý draft cũ và cơ chế kiểm tra tính hợp lệ khi fact thay đổi phải thiết kế rõ trước khi bật, không làm mất tương thích âm thầm.
7. Export là thao tác có tác dụng bên ngoài: không tự retry mù khi có khả năng WordPress đã tạo post. Thiết kế idempotency/đối soát và trạng thái `outcome_unknown` trước khi cho retry; giữ log đủ để tìm bài, không ghi credential.

## 7. Tiêu chí nghiệm thu

| Mã | Tiêu chí |
|---|---|
| A1 | Tạo brief gắn đúng brand và một draft; mở lại còn đủ từ khóa, khu vực, mục tiêu và trạng thái. |
| A2 | Lưu 5 kết quả gốc với vị trí/thời điểm; thay thế hoặc đọc thất bại có lịch sử và lý do. |
| A3 | Chưa đủ 5 nguồn đọc được thì báo; chỉ tiếp tục khi người dùng xác nhận ngoại lệ có lý do, tối thiểu 1 nguồn đọc được. |
| A4 | Mỗi nhận định có nguồn hoặc nhãn suy luận; không coi nguồn chưa đọc là đã phân tích đầy đủ. |
| A5 | Ý định chưa thống nhất được đưa ra để người dùng chọn; outline đã duyệt có phiên bản và có thể đọc lại. |
| A6 | Thay đổi nguồn/hướng/outline hoặc sửa bài sau duyệt làm mất hiệu lực những duyệt và review phụ thuộc; không xuất khi thiếu điều kiện. |
| A7 | Đối thủ không làm thay đổi facts/cấu hình brand; claim không xác minh được xuất hiện trong danh sách review. |
| A8 | Với bài SEO mới, export chỉ tạo WP draft khi core xác nhận điều kiện; lỗi kết quả không chắc chắn không tự tạo post mới do retry. |
| A9 | SEO store có trong backup/restore, không chứa credential; bản ghi cũ và draft cũ vẫn đọc được. |
| A10 | UI phân biệt dữ liệu rỗng, lỗi kết nối và lỗi tool; khả năng model chủ thiếu web được hiển thị rõ. |

## 8. Đo trial và giới hạn

Chọn 3–5 từ khóa thật, cùng brand và quy trình đang dùng; ghi rõ truy vấn, điều kiện Google và các URL. Thu thập baseline theo cách hiện tại nếu có: thời gian, số lần sửa outline và bài, số claim cần kiểm chứng, chất lượng theo người duyệt. Dùng cùng người duyệt và rubric, ghi cả khi không có baseline. Không dùng thứ hạng/lượt click ngắn hạn làm tiêu chí duy nhất.

Các chỉ số: tỷ lệ 5 nguồn đọc đủ; số nguồn phải thay; thời gian nghiên cứu, outline, viết, review; số lần sửa; claim thiếu nguồn trước/sau review; tỷ lệ bài đạt các điều kiện A1–A8; đánh giá tính hữu ích/đúng brand/CTA theo thang được định nghĩa trước. Thử nghiệm ban đầu đề xuất: 0 fact đối thủ tự vào brand, 0 export không đủ điều kiện, 100% nguồn lỗi được báo. Mục tiêu giảm thời gian và ngưỡng chất lượng cụ thể chốt sau baseline; đây không phải cam kết tăng thứ hạng.

## 9. Quyết định còn mở

- Địa điểm/thiết bị/ngôn ngữ mặc định; định nghĩa chọn kết quả organic đối với từng loại SERP.
- Giới hạn lưu metadata, trích dẫn và thời gian giữ; nguồn nào được phép lưu.
- Có cho nhập phân tích thủ công trong UI hay chỉ qua MCP trong trial đầu.
- Chính sách bắt buộc duyệt chuyên môn theo ngành và người duyệt tương ứng.
- Export đối với draft cũ và chiến lược đối soát khi WordPress có kết quả không chắc chắn.
- Khi trial đạt: cách lấy SERP có quyền, chi phí và quyền truy cập; không mặc định scraping Google hay tích hợp nhà cung cấp.

Tham khảo: [Google — nội dung hữu ích](https://developers.google.com/search/docs/fundamentals/creating-helpful-content), [Google — chính sách truy cập tự động](https://developers.google.com/search/docs/essentials/spam-policies#machine-generated-traffic), [Google — Search Console](https://developers.google.com/search/docs/monitor-debug/search-console-start/).
