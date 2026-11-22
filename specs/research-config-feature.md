# Spec: Trợ lý nghiên cứu tư liệu và cấu hình viết

Ngày: 2026-11-22. Trạng thái: đã nhận vào phạm vi P3 (xem mục tương ứng trong `pmdecisions.md`). Tài liệu này là đầu vào cho scaffold P3, chưa phải cam kết xây ngay.

## 1. Vấn đề

Mỗi lần nhận một brand mới, Smith phải dựng cấu hình viết từ đầu: luật giọng brand, hồ sơ fact, profile văn phong, thẻ persona. Ngày 22/11/2026, việc này được làm thủ công qua nhiều vòng agent trong một ngày cho hai brand (Cao Nguyên Mộc ở vòng hiệu chuẩn, Mắm Bà Giáo Khỏe ở vòng chạy thử). Mỗi vòng đều cần Smith hoặc người vận hành ra quyết định tại chỗ:

- Trang sản phẩm của mambagiaokhoe.com ghi giá listing 100.000đ và ghi 90.000đ trong bảng giá của chính trang đó. Người vận hành phải chọn giá listing làm giá chính và ghi lại mâu thuẫn bằng tay.
- Corpus fetch của cùng site chứa 845/919 khối là boilerplate lặp lại, làm dải độ dài câu gợi ý lệch thành [28,30]. Người vận hành phải sửa tay về [16,30] trước khi sửa lỗi ở vòng sau.
- Hai vòng học văn phong (32 bài báo chí từ 7 nguồn, corpus văn học từ 5 EPUB cộng 12 nguồn bổ sung) đều cần người loại bỏ nguồn rác, ghi căn cứ hợp lệ cho từng nguồn và chốt luật giọng.
- Thẻ persona An được viết tay hoàn toàn dưới dạng JSON.

Quy trình thủ công này có ba hậu quả cụ thể. Nó tốn vài giờ cho mỗi brand. Các quyết định nằm rải rác trong chat và file trial, không có một nơi duy nhất ghi lại mục nào đã được duyệt, mục nào chưa. Và nó phụ thuộc vào phán đoán của Smith, nên ở phase 2 (bán tool cho writer/agency khác) người mua không có cách nào lặp lại.

Job của người dùng: khi bắt đầu phục vụ một brand mới, Smith cần một bộ cấu hình khởi điểm đáng tin để bài viết đầu tiên ra được trong cùng ngày, và mọi con số trong cấu hình đều truy được về nguồn mà anh đã duyệt.

## 2. Bối cảnh và bằng chứng

Các lần chạy ngày 22/11 là bản mẫu của tính năng này. Chúng cho ra bốn tiền lệ mà spec phải giữ nguyên thành luật:

1. Mâu thuẫn nguồn được nêu ra cho người dùng quyết, không ai tự chọn im lặng. Tiền lệ là giá mắm cá lóc ở trên, và trước đó là giá hạt điều của Cao Nguyên Mộc (blog và shop ghi khác nhau).
2. Tài liệu của đối thủ không bao giờ tự thành brand đã duyệt. Brand Builder hiện tại đã đóng dấu `reference_only` lên ví dụ của draft đối thủ và `draft_to_brand_yaml` từ chối chuyển đổi khi còn mục reference-only.
3. Nguồn rác bị loại kèm lý do ghi lại. Vòng thu corpus báo chí loại các bài listicle SEO và tin ngắn dưới chuẩn; vòng bổ sung corpus văn học loại toàn bộ các trang tổng hợp ebook lậu và ghi rõ trường hợp Trang Thế Hy (không tìm được kênh hợp lệ nên không lấy).
4. Corpus văn phong chỉ lưu thống kê và đặc tính diễn đạt lại. Cả hai profile đã dựng đều có `store_excerpts=False`, 0 exemplar, và mỗi nguồn có căn cứ hợp lệ (public domain, kênh của chủ sở hữu quyền, hoặc user cung cấp).

Phần lõi đã có sẵn phần lớn linh kiện: `fetch_site.py` (có sitemap fallback và phát hiện trang JS shell từ vòng hiệu chuẩn Cao Nguyên Mộc), `analyze_site_brand` và `save_brand_draft` trong Brand Builder, `build_profile`/`save_style_profile` của Style Extraction, `save_facts` kèm `validate_facts`, và `compare_brand_voice`. Ba lỗi làm lộ quy trình thủ công (tool phía người viết phục vụ sai brand, dải câu bị boilerplate làm lệch, chuyển đổi draft làm rơi section `visual`) đã sửa ở commit `64a67a5`.

Phần còn thiếu gồm bốn thứ: một luồng gom tư liệu từ nhiều loại nguồn (hiện mỗi tool chỉ xử lý một site hoặc một danh sách mẫu rời), một nơi lưu các đề xuất trước khi chúng được duyệt, một báo cáo mâu thuẫn, và một bề mặt duyệt từng mục.

## 3. Giải pháp đề xuất

Luồng bốn giai đoạn: Thu thập, Soạn nháp, Duyệt, Áp dụng. AI làm hai giai đoạn đầu. Smith quyết ở giai đoạn Duyệt. Hệ thống chỉ ghi vào cấu hình thật ở giai đoạn Áp dụng.

### 3.1 Giai đoạn Thu thập

Đầu vào gồm URL site của brand (không bắt buộc), danh sách URL đối thủ, từ khóa thể loại cho corpus văn phong, và URL nguồn fact bổ sung. AI được đề xuất ứng viên đối thủ và ứng viên corpus, nhưng chỉ fetch những nguồn Smith đã xác nhận trong danh sách.

Mỗi nguồn fetch qua `fetch_site.py` và qua một cổng chất lượng. Cổng ghi phán quyết accept hoặc reject kèm lý do cho từng nguồn. Tiêu chí reject lấy từ tiền lệ: bài listicle SEO, tin ngắn dưới 450 từ, trang tổng hợp ebook không có quyền, nội dung ngoài vùng chủ đề. Kết quả của giai đoạn này là một research pack (mô tả ở 3.5) chứa bản ghi từng nguồn: URL, thời điểm fetch, loại nguồn (own, competitor, genre, factual), căn cứ hợp lệ nếu là nguồn corpus, phán quyết chất lượng.

### 3.2 Giai đoạn Soạn nháp

Từ research pack, AI soạn các mục đề xuất. Mỗi mục là một đơn vị duyệt độc lập, thuộc một trong bốn loại:

- Brand: bộ luật giọng và brand kit, soạn qua `analyze_site_brand` trên các trang own. Trang competitor chỉ sinh mục so sánh qua `compare_brand_voice`, không sinh luật brand.
- Fact: từng fact một mục riêng (giá, trọng lượng, nguồn gốc, quy trình đã công bố). Mỗi mục mang giá trị, URL nguồn, và đoạn ngữ cảnh quanh giá trị trong nguồn.
- Style: một hoặc nhiều ứng viên profile văn phong, dựng qua `build_profile` từ corpus đã qua cổng chất lượng, kèm manifest corpus.
- Persona: bộ khung thẻ Card V2 suy từ chất liệu brand (tên gọi, xưng hô, vùng miền). Phần thế đứng và tính cách để trống cho Smith điền ở giai đoạn Duyệt, vì thẻ An hiện tại bắt nguồn từ brief của Smith (tuổi 25, người Châu Đốc, xưng em) chứ không suy ra được từ site.

Bộ phát hiện mâu thuẫn chạy trên toàn bộ mục nháp và giữa các nguồn. Hai giá trị khác nhau cho cùng một trường fact tạo một bản ghi mâu thuẫn gồm các giá trị cạnh tranh và nguồn của từng giá trị. Mục fact dính mâu thuẫn bị khóa duyệt cho tới khi Smith chọn một giá trị ở giai đoạn Duyệt.

### 3.3 Giai đoạn Duyệt

Smith đi từng mục và chọn một trong ba hành động: duyệt, sửa rồi duyệt, hoặc bỏ. Mỗi quyết định ghi kèm thời gian. Mâu thuẫn mở được trình bày trước các mục thường. Giai đoạn này không ghi bất cứ thứ gì vào các store đang sống (brand, facts, style, persona). Research pack hiển thị số đếm theo trạng thái để Smith biết còn bao nhiêu mục chưa xử lý.

### 3.4 Giai đoạn Áp dụng

Các mục đã duyệt được ghi qua đúng các hàm lưu hiện có: brand qua `save_brand_draft` rồi chuyển đổi theo luật `draft_to_brand_yaml`, fact qua `save_facts`, style qua `save_style_profile`, persona ghi thành file persona trong store persona. Áp dụng chạy theo từng mục và chạy lại không sinh trùng lặp. Cuối buổi, một báo cáo áp dụng liệt kê id các mục đã áp dụng, đã sửa, đã bỏ. Danh sách id đã áp dụng phải khớp đúng danh sách id đã duyệt; đây là tiêu chí kiểm chứng ở mục 8.

### 3.5 Artefact mới ở phía core

Chỉ có một artefact mới cần xây. Ba thứ còn lại là bề mặt hiển thị của nó.

| Artefact | Nội dung | Trạng thái |
|---|---|---|
| research pack | Một file JSON cho mỗi brand trong store mới, theo đúng khuôn store của loaders (ghi nguyên tử, kiểm tra schema khi lưu). Chứa `sources[]`, `items[]`, `contradictions[]`, `quality_rejections[]` | Là artefact mới duy nhất ở core |
| item trong pack | `id`, `kind` (brand, fact, style, persona), `payload`, `provenance` (URL nguồn, ghi chú lấy gì từ nguồn), `status`, `contradiction_id` (có thể null) | `status` đi theo: proposed, approved, edited, rejected, applied. Mục edited giữ provenance gốc và thêm dấu đã sửa bởi user |
| contradiction record | `id`, `field`, `values[]` (mỗi giá trị kèm URL nguồn), `status`, `resolution` | `status`: open hoặc resolved. Chỉ resolved mới mở khóa mục fact liên quan |
| báo cáo mâu thuẫn | Phần hiển thị các contradiction record đang open, sinh từ pack khi đọc | Không phải store riêng |
| bề mặt checklist duyệt | Màn hình P3 đọc pack qua một tool duy nhất và hiển thị từng mục để duyệt | Thuộc UI Astryx ở P3, xem 3.7 |

Các tool MCP mới phía core (tên gọi dự kiến, scaffold P3 chốt cuối): `create_research_pack`, `add_pack_source`, `build_pack_proposals`, `get_pack`, `decide_pack_item`, `resolve_contradiction`, `apply_pack`. `get_pack` phải trả đủ dữ liệu cho checklist trong một lần gọi, gồm số đếm theo trạng thái và danh sách mâu thuẫn đang open.

### 3.6 Ánh xạ sang linh kiện hiện có

| Bước trong luồng | Linh kiện hiện có | Phần phải xây mới |
|---|---|---|
| Fetch site brand và đối thủ | `fetch_site.py` | Cổng chất lượng nguồn và bản ghi nguồn trong pack |
| Soạn luật brand | `analyze_site_brand`, `save_brand_draft` | Bọc kết quả thành item có provenance |
| So sánh đối thủ | `compare_brand_voice` | Không |
| Dựng profile văn phong | `build_profile`, `save_style_profile` | Gom corpus theo thể loại, manifest kèm căn cứ hợp lệ |
| Soạn fact | `find_site_claims` trong Brand Builder, `validate_facts` | Tách claim thành item duyệt riêng lẻ, phát hiện mâu thuẫn |
| Áp dụng | `save_brand_draft`, `draft_to_brand_yaml`, `save_facts`, `save_style_profile` | Điều phối apply theo danh sách đã duyệt và báo cáo áp dụng |
| Persona | File Card V2 trong store persona | Sinh khung thẻ từ chất liệu brand |

### 3.7 Vị trí trong P3

Spec này là một đầu vào của scaffold P3, ngang hàng với WordPress draft exporter đã chốt ngày 2026-11-22. P3 cần gồm, theo thứ tự: (1) research pack store và trạng thái duyệt ở core, (2) các tool MCP ở 3.5, (3) màn hình checklist trong UI Astryx tiêu thụ `get_pack`. Exporter WordPress là hạng mục P3 riêng và nằm ngoài spec này. Hợp đồng dữ liệu của `get_pack` phải chốt sau lần chạy thử đầu tiên (mục 10) rồi mới xây UI, để UI không phải làm lại khi luồng duyệt thay đổi.

### 3.8 Đánh đổi

Phương án bị loại là giữ nguyên cách thủ công trong chat. Cách đó cho kết quả tốt ở hai brand đầu vì Smith ngồi cạnh từng quyết định, nhưng nó không để lại artefact duyệt nào và không chuyển giao được cho người dùng phase 2. Phương án trong spec đánh đổi như sau: phải xây pack store và các tool trước khi có UI, và mỗi brand mới tốn một buổi duyệt từng mục thay vì vài câu lệnh rời trong chat.

## 4. User stories và tiêu chí chấp nhận

**US-1.** Smith bắt đầu phục vụ một shop mới và đưa URL site của shop. Anh cần pack chứa sẵn brand draft và danh sách fact có nguồn để buổi duyệt bắt đầu ngay, thay vì bắt đầu từ trang trắng.
- Fetch xong, pack chứa các trang đã lấy được, hoặc một bản ghi fetch thất bại kèm lý do cho từng URL.
- Mọi mục fact trong pack đều có URL nguồn. Một mục fact thiếu URL nguồn không được tồn tại trong pack; test tự động kiểm tra điều này trên fixture.
- Brand draft trong pack do `analyze_site_brand` sinh ra và giữ nguyên kỷ luật `reference_only` hiện có.

**US-2.** Site của khách tự mâu thuẫn về giá. Smith cần thấy mâu thuẫn đó ở đầu buổi duyệt và chọn một giá trị, để bài viết sau này không mang giá chưa được anh quyết.
- Fixture dựng lại tiền lệ mambagiaokhoe.com (listing 100.000đ, bảng giá 90.000đ) phải sinh đúng một contradiction record cho trường giá đó, với cả hai giá trị và hai vị trí nguồn.
- `apply_pack` từ chối ghi mục fact đang dính mâu thuẫn open và báo id mâu thuẫn trong kết quả.
- Sau khi Smith resolve bằng một giá trị, apply chỉ ghi giá trị đã chọn. Giá trị còn lại chỉ còn trong pack dưới dạng lịch sử, không xuất hiện trong facts profile.

**US-3.** Smith thêm URL của một đối thủ để tham khảo vị thế. Anh cần chắc chắn chất liệu đối thủ không lọt vào brand và fact của khách.
- Mọi item có nguồn từ site competitor đều mang dấu reference.
- Test áp dụng một item brand example có dấu reference phải thất bại theo đúng cách `draft_to_brand_yaml` từ chối draft còn reference-only hiện nay.

**US-4.** Smith cần profile văn phong cho một thể loại mới. Anh cần biết từng nguồn trong corpus có quyền dùng hay không trước khi profile được lưu.
- Profile dựng từ pack liệt kê căn cứ hợp lệ cho từng nguồn trong manifest. Nguồn thiếu căn cứ không được đưa vào corpus.
- Profile được lưu có `store_excerpts=False`. Test tự động khẳng định điều này trên profile sinh ra từ pack.

**US-5.** Smith hủy buổi duyệt giữa chừng. Anh cần chắc chắn cấu hình đang sống của các brand khác và của chính brand này không thay đổi gì.
- Test bất biến: seed một pack có item ở trạng thái proposed, chạy apply, khẳng định các store đang sống không đổi (so sánh danh sách và nội dung qua các hàm `list_*` trước và sau).
- Test tương tự với item ở trạng thái rejected và item dính mâu thuẫn open.

**US-6.** AI đề xuất một nguồn corpus là bài listicle SEO. Smith cần nguồn đó bị loại kèm lý do, để corpus không nhiễm văn phong rác.
- Fixture dùng các mẫu nguồn đã bị loại trong các vòng thu corpus ngày 22/11. Cổng chất lượng phải reject kèm lý do ghi trong `quality_rejections[]`, và nguồn bị loại đóng góp 0 từ vào corpus.

## 5. Trường hợp biên và xử lý lỗi

- Site tự mâu thuẫn (tiền lệ ở mục 2): contradiction record, khóa duyệt mục liên quan, chỉ Smith resolve.
- Site là JS shell và fetch chỉ lấy được khung trang: `fetch_site.py` đã có sitemap fallback. Nếu vẫn rỗng, pack ghi trạng thái site không đọc được, luồng tiếp tục bằng tài liệu Smith cung cấp, và brand draft được đánh dấu ít bằng chứng để buổi duyệt biết mà thận trọng.
- Brand không có website: pack bắt đầu từ tài liệu Smith đưa (ảnh bảng giá, file giới thiệu). Fact được đề xuất từ các tài liệu đó với provenance ghi user-provided. Brand kit (logo, font, màu) để trống, không suy đoán.
- Không tìm được nguồn corpus hợp lệ cho một thể loại (tiền lệ Trang Thế Hy): giai đoạn Style báo thiếu hụt kèm danh sách nguồn đã loại và lý do. Smith chọn một trong ba: dùng corpus nhỏ hơn, đổi thể loại, hoặc bỏ qua style profile và chỉ dùng luật brand.
- Một nguồn fetch lỗi hoặc timeout: ghi lỗi theo từng nguồn, pack vẫn tiếp tục với các nguồn còn lại. Nếu mọi nguồn đều lỗi, pack không được chuyển sang giai đoạn Soạn nháp.
- Hai URL trỏ cùng một trang: `fetch_site.py` đã canonical hóa URL. Pack khử trùng theo URL đã canonical.
- Smith sửa một mục tới mức giá trị không còn khớp nguồn đã dẫn: được phép. Mục chuyển trạng thái edited, giá trị đã sửa là giá trị được áp dụng, provenance gốc vẫn giữ để truy vết.
- Chạy lại Thu thập cho brand đã có cấu hình: tạo phiên bản pack mới. Cấu hình đã áp dụng trước đó không bị ghi đè nếu chưa qua một buổi duyệt mới.
- Site rất lớn: giới hạn `max_pages` hiện có của `fetch_site.py` áp dụng nguyên trạng, và pack ghi lại việc cắt bớt để buổi duyệt biết corpus site chưa đầy đủ.

## 6. Ngoài phạm vi

1. WordPress draft exporter. Hạng mục P3 riêng, đã chốt ở quyết định ngày 2026-11-22 trong `pmdecisions.md`. Spec này dừng ở cấu hình đã áp dụng và không đăng bài.
2. Tự động khám phá nguồn mà không qua xác nhận của Smith. AI chỉ đề xuất ứng viên; danh sách fetch phải được Smith xác nhận. Tự động hoàn toàn chờ dữ liệu từ các lần chạy thử cho thấy cổng chất lượng đủ tin cậy. Câu hỏi mở số 2 ở mục 11 theo dõi việc này.
3. Duyệt phân tán nhiều người, gồm việc khách tự duyệt cấu hình của mình. Người duyệt duy nhất ở phase 1 là Smith. Bề mặt cho khách thuộc về đóng gói phase 2.
4. Nạp thư viện ảnh từ tư liệu thu thập. Tính năng ảnh đã có luồng thư viện riêng theo brand. Research pack không fetch và không lưu ảnh.
5. Tự sinh persona hoàn chỉnh không qua phỏng vấn. Pack chỉ sinh khung thẻ. Thế đứng, tuổi, quê quán và cách xưng hô của persona cần câu trả lời của Smith, như thẻ An đã cần.
6. Brand và thể loại ngoài tiếng Việt. Cổng chất lượng nguồn và thống kê văn phong hiện được hiệu chuẩn trên nguồn tiếng Việt. Ngôn ngữ khác cần một vòng hiệu chuẩn riêng trước khi dùng.

## 7. Phụ thuộc

| Phụ thuộc | Chủ sở hữu | Trạng thái |
|---|---|---|
| Scaffold P3 (core store, tool MCP, UI) | Smith duyệt phạm vi từng vòng, Ember xây | Spec này là đầu vào; chưa bắt đầu |
| Các tool hiện có ở 3.6 giữ nguyên hợp đồng | Ember | Đã ổn định ở HEAD `64a67a5`, 446 test |
| Hợp đồng dữ liệu của `get_pack` cho UI Astryx | Ember đề xuất, Smith duyệt sau lần thử đầu | Chưa chốt, cố ý để sau trial |
| Thời gian của Smith cho một buổi thiết lập khách thật và một buổi duyệt | Smith | Chưa lên lịch |
| Job của khách hàng thuê dịch vụ viết bài theo tháng | Smith | Chưa nắm bắt (ghi trong `.pmcontext.md`); không chặn phase 1, chặn thiết kế bề mặt cho khách ở phase 2 |

## 8. Tiêu chí thành công

Lần chạy thử đầu tiên là thước đo. Đối tượng thử: một brand thật Smith sắp phục vụ, hoặc brand shop mắm của chính anh. Báo cáo lần thử ghi lại mọi con số dưới đây vào thư mục `trials/` theo đúng định dạng các báo cáo trial hiện có.

- Chỉ số chính: số lần Smith phải can thiệp (mỗi lần duyệt, sửa, bỏ, hoặc resolve tính là một) để đi từ URL đầu tiên tới cấu hình đã áp dụng. Mục tiêu: tối đa 8 lần can thiệp cho một brand shop tiêu chuẩn, và tổng thời gian buổi thiết lập tối đa 45 phút. Buổi thủ công ngày 22/11 cho Mắm Bà Giáo Khỏe cần các quyết định rải rác qua nhiều vòng agent trong khoảng hai giờ, gồm cả hai vòng học văn phong riêng lẻ.
- Chỉ số phụ: tỉ lệ mục được duyệt nguyên văn trên tổng số mục đã xử lý. Dải lành mạnh dự kiến là 50 tới 85 phần trăm. Dưới 50 phần trăm nghĩa là khâu soạn nháp yếu. Trên 85 phần trăm kèm gần như không có lần sửa nào là dấu hiệu duyệt qua loa, và báo cáo lần thử phải nêu ra thay vì mừng.
- Rào chắn cứng, kiểm bằng test tự động: số mục chưa được duyệt hoặc đang dính mâu thuẫn open xuất hiện trong cấu hình đang sống sau bất kỳ lần apply nào phải bằng 0. Sau apply, báo cáo đối chiếu danh sách id đã áp dụng với danh sách id đã duyệt; hai danh sách phải khớp đúng.
- Rào chắn cứng thứ hai: số profile văn phong lưu từ pack có `store_excerpts=True` phải bằng 0.
- Đánh giá sau đúng một lần thử. Nếu chỉ số chính vượt mục tiêu gấp đôi, cắt bớt giai đoạn thay vì thêm UI, rồi mới tính lần thử thứ hai.

## 9. Rủi ro và giảm thiểu

- Rủi ro giá trị, mức trung bình: Smith thấy luồng pack nặng hơn cách làm trong chat và quay lại làm thủ công. Giảm thiểu bằng lần thử có đo thời gian và số lần can thiệp ở mục 8. Nếu số liệu thua cách thủ công, spec này bị sửa hoặc bỏ trước khi xây UI.
- Rủi ro sử dụng, mức trung bình: một pack có vài chục mục làm buổi duyệt mệt, và mệt thì duyệt qua loa. Giảm thiểu: mâu thuẫn và các claim luôn xếp trước, mục fact không bao giờ được duyệt hàng loạt, các loại mục còn lại gom nhóm khi hiển thị.
- Rủi ro khả thi, mức trung bình: cổng chất lượng nguồn là phán đoán của model chủ, không phải code xác định. Giảm thiểu: mọi phán quyết đều ghi lý do trong pack, và các nguồn đã bị loại ngày 22/11 trở thành fixture hồi quy cho cổng.
- Rủi ro khả thi thứ hai, mức thấp: pack trở thành bản sao thứ hai của các store hiện có và hai bên lệch nhau. Giảm thiểu: pack là store duy nhất ở trạng thái trước duyệt; apply ghi qua đúng các hàm lưu hiện có và không giữ bản sao cấu hình đang sống.
- Rủi ro vận hành, mức thấp: dùng nhầm tư liệu chưa có quyền cho một dịch vụ thương mại. Giảm thiểu: căn cứ hợp lệ là trường bắt buộc của nguồn corpus, các tên miền tổng hợp lậu bị loại theo danh sách rút ra từ vòng bổ sung corpus, và profile không lưu đoạn văn.

## 10. Kế hoạch triển khai

Ba bước, mỗi bước cần Smith duyệt phạm vi trước như cách làm việc hiện tại. Bước một: xây research pack store và các tool MCP ở core, kèm test fail-first cho các tiêu chí ở mục 4 và hai rào chắn ở mục 8. Bước hai: một lần chạy thử không UI, vận hành trong chat như các trial hiện tại, có báo cáo đo lường. Bước ba: chốt hợp đồng `get_pack` dựa trên báo cáo lần thử, rồi xây checklist trong UI Astryx. Tính năng này cộng thêm hoàn toàn; tắt nó nghĩa là không tạo pack, và đường thủ công hiện tại vẫn dùng được ở mọi thời điểm.

## 11. Pre-mortem

Giả định ba tháng sau khi tính năng chạy và nó đã thất bại. Năm lý do có khả năng nhất:

1. Smith duyệt hàng loạt mà không đọc, vì một pack có quá nhiều mục nhỏ. Khả năng 40%. Tín hiệu sớm: tỉ lệ duyệt nguyên văn trên 90% và gần như không có lần sửa nào ở lần thử đầu. Giảm thiểu ở mục 9 (xếp mâu thuẫn lên trước, cấm duyệt hàng loạt với fact).
2. Phát hiện mâu thuẫn bỏ sót các xung đột diễn đạt khác nhau, ví dụ giá ghi bằng chữ hoặc giá theo ký so với giá theo gói. Khả năng 35%. Tín hiệu sớm: một bài đã đăng mang con số mà Smith phải sửa tay. Giảm thiểu: `validate_facts` đã chuẩn hóa trọng lượng và giá; các cặp giá trị gần khớp nhưng không bằng nhau được liệt kê thành mục nghi mâu thuẫn cho buổi duyệt xem.
3. Cổng chất lượng cho qua nguồn SEO ở một thể loại mới mà nó chưa gặp. Khả năng 30%. Tín hiệu sớm: corpus phình nhanh với rất ít nguồn bị loại. Giảm thiểu: fixture hồi quy từ các nguồn đã loại, và phán quyết từng nguồn luôn hiển thị trong pack.
4. Thời gian tiết kiệm được rất ít vì phần persona và văn phong vẫn cần gu của Smith. Khả năng 30%. Tín hiệu sớm: thời gian lần thử gần bằng buổi thủ công. Giảm thiểu: đo số lần can thiệp bên cạnh thời gian. Tính năng vẫn đạt mục đích nếu nó gom được các quyết định vào một buổi và ghi lại chúng, kể cả khi thời gian chưa giảm.
5. Pack và các store hiện có lệch nhau sau vài lần chạy lại Thu thập. Khả năng 25%. Tín hiệu sớm: một brand có hai bản luật giọng khác nhau ở hai nơi. Giảm thiểu ở mục 9 (pack không giữ bản sao cấu hình đang sống).

## 12. Câu hỏi mở

1. Research pack nên là store mới hay mở rộng của brand draft store hiện có? Spec này đề xuất store mới vì pack chứa cả fact, style và persona. Scaffold P3 quyết cuối cùng. Không chặn lần chạy thử.
2. ~~Có loại mục nào được duyệt hàng loạt không?~~ **ĐÃ CHỐT (Smith, 2026-11-22): không duyệt hàng loạt — mọi mục đều duyệt thủ công, từng mục một, trong admin.** Không có đường duyệt tắt ở phase 1.
3. ~~Corpus thể loại dùng chung đăng ký một lần rồi tham chiếu, hay sao vào từng pack?~~ **ĐÃ CHỐT (Smith, 2026-11-22): đăng ký một lần ở cấp hệ thống; từng brand CHỌN corpus (và phiên bản) để gắn vào mình, và AI dựa trên corpus đã chọn đó để cải tiến việc viết cho brand.** Pack ghi lại lựa chọn corpus của brand; không sao corpus vào pack.
4. Khi khách ở phase 2 tự duyệt cấu hình của họ, buổi duyệt cần hiển thị khác gì so với buổi duyệt của Smith? Chưa trả lời được vì job của khách chưa được nắm bắt. Không chặn phase 1.

---

## Phụ lục — Trạng thái triển khai ở core (2026-11-22, vòng hoàn thiện core 2)

**Đã triển khai ở core** (store + tools + bất biến, có test fail-first ghim):

- Research pack store mới (`data/research_packs/<brand-id>.json`, loaders house pattern) — trả lời câu hỏi mở 1 theo đúng đề xuất của spec: store mới. Logic ở `content_core/research.py`.
- 8 tool MCP: `create_research_pack`, `add_pack_source`, `build_pack_proposals`, `propose_pack_item`, `get_pack`, `decide_pack_item`, `resolve_contradiction`, `apply_pack` — đúng hợp đồng mục 3.5, cộng thêm `propose_pack_item` để operator đề xuất fact giá/trọng lượng/nguồn gốc từng mục (phần trích xuất xác định của `build_pack_proposals` chỉ gồm brand_rule + claim needs_evidence).
- Các bất biến của mục 4 và hai rào chắn của mục 8 đều có test: fact không `source_url` không tồn tại được (US-1); mâu thuẫn khóa cả quyết định lẫn apply, chọn một phía thì phía còn lại bị reject và chỉ còn trong lịch sử pack (US-2); item brand còn `reference_only` fail đúng tại cổng `draft_to_brand_yaml` (US-3); style item từ pack luôn lưu `store_excerpts=False` (US-4); apply chỉ nhận approved/edited, idempotent, và tập id đã apply đối chiếu bằng đúng tập đã duyệt.
- Corpus registry cấp hệ thống (`register_corpus` / `list_corpora` / `get_corpus`) + lựa chọn corpus theo brand (`set_corpus_choice` / `get_corpus_choice`, hoặc qua item `corpus_choice` trong pack) — đúng quyết định đã chốt ở câu hỏi mở 3; `assemble_context` lấy style profile mặc định từ lựa chọn này, lựa chọn tường minh trong brief luôn thắng.
- Không có tool duyệt hàng loạt — đúng quyết định đã chốt ở câu hỏi mở 2.

**Chưa làm (ngoài phạm vi vòng core này):**

- Lần chạy thử đo lường của mục 8 (≤45 phút, ≤8 lần can thiệp, tỉ lệ duyệt nguyên văn 50–85%) — chưa đo; cần một brand thật chạy qua luồng pack.
- UI checklist Astryx (bước ba của mục 10) — thuộc P3, chờ hợp đồng `get_pack` chốt sau lần thử.
- Câu hỏi mở 4 (buổi duyệt của khách phase 2) — vẫn mở, phụ thuộc job của khách chưa nắm bắt.
