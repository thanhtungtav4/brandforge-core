# Spec: Viết lại bài đã đăng trên WordPress (import, audit, viết lại, xuất nháp liên kết)

Ngày: 2026-11-22. Trạng thái: đã ghi quyết định trong `pmdecisions.md` (mục "Existing WordPress posts get a rewrite path"); ngày 2026-11-23 các câu hỏi mở ở mục 12 đã được chốt (xem cuối tài liệu) và bước một (xây ở core) tiến hành trên các quyết định đó.

## 1. Vấn đề

brandforge viết bài mới tốt, nhưng site của khách và của chính Smith đã có sẵn bài cũ viết trước khi có hệ thống này. Ngày 22/11/2026, Smith hỏi hệ thống xử lý được các bài cũ trên WordPress hay không. Câu trả lời sau khi kiểm tra: các bộ kiểm tra (fact, brand, craft, văn phong) chấm được mọi văn bản đưa vào, nhưng hai mắt xích còn thiếu. Chưa có đường nào kéo một bài đã đăng từ WordPress vào core thành draft, và exporter hiện tại chỉ tạo nháp mới, không có khái niệm "bản viết lại của bài X".

Site thử nghiệm là kho.nttung.dev (shop Khô Mắm An của chính Smith, brand `mambagiaokhoe`). Site có đúng 9 bài đã đăng, toàn bộ là nội dung SEO viết tháng 11/2026 theo cách cũ: tiêu đề câu click, nhiều bài lệch chủ đề shop, và giá cùng claim trong bài không có gì đối chiếu với hồ sơ fact hiện tại. Nếu không có luồng viết lại, 9 bài này nằm ngoài hệ thống mãi mãi: Smith hoặc giữ nguyên chất lượng cũ, hoặc ngồi viết lại bằng tay từng bài và tự đăng lại.

Job của người dùng: khi tiếp quản một site đã có nội dung, Smith cần đưa từng bài cũ vào cùng một kỷ luật như bài mới (nguyên bản được giữ, fact được audit, bản viết lại qua cổng, xuất thành nháp để anh duyệt) mà bài gốc trên site không bị đụng vào ở bất kỳ bước nào.

## 2. Bối cảnh và bằng chứng

### 2.1 Tập thử thật: 9 bài trên kho.nttung.dev

Đã kiểm chứng trực tiếp qua REST API ngày 22/11/2026. Target WordPress của brand `mambagiaokhoe` đã cấu hình trỏ vào site này và exporter đã chạy thật trên nó (4 bài mới lên nháp 777 tới 780 cùng ngày).

| WP id | Ngày đăng | Tiêu đề |
|---|---|---|
| 240 | 2026-11-02 | Khô cá lóc chiên giòn chấm mắm me: Món ngon đưa cơm đúng điệu miền Tây |
| 241 | 2026-11-02 | Bí quyết chọn khô cá ngon: 5 điều người miền Tây luôn để ý |
| 717 | 2026-11-15 | Cam Sành Vĩnh Long |
| 718 | 2026-11-15 | Bạn biết cách chọn khô cá 1 nắng và 2 nắng ngon chưa? |
| 719 | 2026-11-15 | Top 42 đặc sản An Giang mà bạn không nên bỏ lỡ |
| 720 | 2026-11-15 | LẠP XƯỞNG TUNG LÒ MÒ |
| 721 | 2026-11-15 | KHÔ CÁ SẶC LÀM MÓN GÌ ??? |
| 722 | 2026-11-15 | CÁCH BẢO QUẢN CÁ KHÔ |
| 723 | 2026-11-15 | CÁCH LÀM KHÔ CÁ LÓC CHUẨN NHÀ LÀM |

Tập này chứa sẵn các tình huống spec phải xử lý: bài đúng chủ đề shop (723, 240), bài listicle (719), bài lệch hẳn chủ đề (717, cam sành trên shop khô cá và mắm), và bài có khả năng mang giá hoặc claim cũ cần audit (241, 718).

### 2.2 Phần đã có sẵn

- Exporter (`content_core/wordpress.py`): tạo nháp với status `draft` ghim cứng trong `build_post_payload`, không có đường publish; luật trùng lặp là có bản ghi xuất thì từ chối, `force=True` mới tạo bài mới; target theo brand ở `data/wordpress_targets/` chỉ giữ field phi bí mật; mật khẩu ứng dụng đọc từ biến môi trường hoặc file secrets local và mọi lỗi đều qua `redact`.
- Draft store: mỗi draft là một JSON trong `drafts/` với `brief`, `outline`, `sections[]` (mỗi section có `id`, `heading`, `intent`, `target_words`, `text`), `gate_history`, `status`, và bản ghi `wordpress_export` khi đã xuất. Luồng viết (`assemble_context`, `save_section`, `check_draft_all`, `final_gate`) chạy trên đúng shape này.
- Hồ sơ fact của brand `mambagiaokhoe` đã có và đã dùng cho 4 bài mới. Đây là chuẩn để audit bài cũ.

Phần thiếu gồm ba thứ: importer (liệt kê và kéo bài), bản audit của nguyên bản gắn trên draft, và nhánh xuất ngược nhận diện draft có nguồn gốc import.

## 3. Giải pháp đề xuất

Luồng một bài cũ: Liệt kê, Import, Audit, Phán quyết, Viết lại (nếu phán quyết là viết lại), Xuất nháp liên kết. Importer chỉ đọc WordPress. Exporter chỉ tạo bài mới. Bài gốc không nhận bất kỳ request ghi nào trong toàn bộ luồng, và luật này là bất biến có test ở mục 8.

### 3.1 Liệt kê và import

Tool mới dự kiến:

| Tool | Hành vi |
|---|---|
| `list_wordpress_posts(brand_id, status?, per_page?)` | Đọc target của brand, gọi GET `posts` trên REST với mật khẩu đã cấu hình (cần auth để thấy cả bài draft). Trả id, tiêu đề, status, ngày đăng, ngày sửa, link cho từng bài. Chỉ đọc. |
| `import_wordpress_post(brand_id, wp_post_id, force?)` | Kéo một bài, chuyển đổi (3.2), tạo draft trong store kèm snapshot và provenance (3.3), chạy audit mặc định (3.4). Trả draft_id và báo cáo import: số section, tỉ lệ bao phủ, số raw block, số heading bị hạ cấp, số link đã trích. |
| `audit_imported_post(draft_id)` | Chạy lại audit trên snapshot nguyên bản, ví dụ sau khi hồ sơ fact được cập nhật. Ghi đè `original_audit`, không đụng `gate_history`. |
| `set_rewrite_verdict(draft_id, verdict)` | Ghi phán quyết của Smith cho draft import: `rewrite`, `audit_only`, hoặc `drop`. Xuất ngược chỉ mở khi verdict là `rewrite`. |

Luật trùng lặp của import, theo đúng khuôn luật trùng lặp của exporter: đã tồn tại draft cho cặp (brand, wp_post_id) mà chưa bị supersede thì import lại bị từ chối và trả draft_id hiện có. `force=True` tạo draft mới với snapshot chụp lại từ đầu, ghi `source.import_round` tăng dần và `supersedes` trỏ draft cũ; draft cũ nhận `superseded_by`, chuyển status sang `superseded` và bị khóa xuất. Lý do chọn từ chối cộng version thay vì ghi đè im lặng: draft cũ có thể đang chứa công viết lại và lịch sử cổng; ghi đè sẽ xóa dấu vết audit mà tính năng này tồn tại để tạo ra.

Bài ở status draft trên WordPress và bài ở status publish được import bằng cùng một đường. Importer không phân biệt vì nó không ghi gì lên WordPress trong cả hai trường hợp.

### 3.2 Chuyển đổi HTML sang section

Nguồn là `content.rendered` của bài. Luật chuyển đổi cố định:

| Thành phần trong HTML | Kết quả trong draft |
|---|---|
| `h2` | Ranh giới section mới; text của h2 thành `heading` |
| Nội dung trước `h2` đầu tiên | Section mở đầu, `heading` rỗng |
| `h3`, `h4` | Hạ xuống thành đoạn text trong section hiện tại; đếm số lượng vào cảnh báo của báo cáo import |
| `p` | Đoạn text, các đoạn nối nhau bằng dòng trống (đúng định dạng `build_content_html` đọc ngược được) |
| `ul`, `ol` | Mỗi `li` thành một dòng text riêng trong section |
| `img`, `figure` | Trích khỏi text, ghi vào `source_media` của section hiện tại: `wp_media_id` (từ class `wp-image-<id>` nếu có), `source_url`, `alt`, `caption` |
| `a` | Giữ anchor text trong text; cặp (anchor, href) ghi vào `links[]` của section để người viết chèn lại khi viết lại |
| `blockquote` | Thành đoạn text thường; đếm vào cảnh báo |
| Bảng `table`, shortcode, block comment không map được, `iframe`/embed | Giữ nguyên HTML trong `raw_blocks[]` của section hiện tại kèm ghi chú loại; không vào text viết lại |
| `featured_media` của bài | Ghi ở cấp draft trong snapshot, kèm `wp_media_id` để xuất ngược dùng thẳng (3.6) |

Bài có `content.protected` (đặt mật khẩu) bị từ chối import kèm lý do, vì `content.rendered` rỗng. Entity HTML được giải mã, class và thuộc tính trình bày của WordPress bị bỏ, khoảng trắng được chuẩn hóa. Báo cáo import tính tỉ lệ bao phủ: (số ký tự text trong các section cộng số ký tự text bên trong raw block) chia cho tổng số ký tự text của nguồn. Bài dưới 0,97 vẫn import được nhưng bị gắn cờ bao phủ thấp, và phán quyết mặc định gợi ý cho các bài đó là `audit_only` cho tới khi Smith xem tay.

### 3.3 Draft import trong store

Draft import là một draft thường trong `drafts/`, cộng bốn khối:

```json
"source":   {"kind": "wp_import", "brand_id": "...", "site_url": "...",
             "wp_post_id": 719, "wp_url": "...", "wp_status": "publish",
             "imported_at": "...", "import_round": 1, "supersedes": null},
"original": {"title": "...", "slug": "...", "excerpt": "...",
             "content_html": "<nguyên văn content.rendered>",
             "featured_media": 123, "categories": [4], "tags": [9],
             "media": [{"wp_media_id": 124, "source_url": "...", "alt": "...", "section_id": "s2"}],
             "raw_blocks": [{"section_id": "s3", "html": "...", "note": "shortcode"}]},
"original_audit": {"checked_at": "...", "fact_issues": [], "prohibited_claims": [],
                   "brand_score": 0.0, "craft_score": 0.0},
"verdict": null
```

Ba luật gắn với các khối này:

- `original` là snapshot bất biến. Tool import là nơi duy nhất ghi nó. Các bước viết lại, cổng và xuất chỉ đọc. Test ở mục 4 khẳng định `original` bằng đúng bản chụp lúc import sau khi chạy hết luồng.
- `sections` khởi tạo từ kết quả chuyển đổi ở 3.2, nên người viết bắt đầu từ văn bản thật của bài cũ. Viết lại sửa trên `sections` như draft thường.
- Ảnh của bài cũ vào images state của draft dưới dạng đã lấp, mang `wp_media_id` và dấu nguồn gốc `existing_wp`. Exporter gặp entry có `wp_media_id` thì dùng thẳng id đó, không upload lại.

### 3.4 Audit nguyên bản

Ngay sau import, hệ thống chạy kiểm tra trên văn bản gốc đã chuyển đổi: fact check đối chiếu hồ sơ fact của brand, quét claim bị cấm theo luật brand, cộng điểm brand và craft của bản gốc để có ngữ cảnh. Kết quả vào `original_audit`. Danh sách Smith đọc là hai mục đầu: fact issue (claim cũ lệch fact hiện tại, ví dụ giá cũ) và claim bị cấm. Audit tách hẳn khỏi `gate_history`: điểm kém của bài cũ không bao giờ làm bẩn lịch sử cổng của bản viết lại, và audit không chặn bước nào. Nó là đầu vào cho phán quyết ở 3.5.

### 3.5 Phán quyết và viết lại

Sau khi đọc audit, Smith đặt một trong ba phán quyết cho từng bài, theo đúng kỷ luật từng mục đã chốt cho research pack:

- `rewrite`: bài đi vào luồng viết thường. `assemble_context` theo brand, persona và style profile của brand; người viết sửa từng section; `check_draft_all` và `final_gate` như bài mới.
- `audit_only`: dừng ở audit. Draft giữ snapshot và audit làm hồ sơ; không viết lại, không xuất. Đây là kết quả hạng nhất của luồng, dành cho bài cần quyết định nội dung trước hoặc bài có bao phủ chuyển đổi thấp.
- `drop`: ghi nhận bài không nên tồn tại. Hệ thống chỉ ghi phán quyết và lý do; việc gỡ bài trên WordPress là tay Smith. Không có thao tác WordPress nào đi kèm phán quyết này.

### 3.6 Xuất ngược thành nháp liên kết

Xuất ngược dùng lại `export_draft_to_wordpress` hiện có. Khi draft có `source.kind` là `wp_import`, exporter rẽ nhánh:

- Điều kiện mở: `verdict` phải là `rewrite` và `final_gate` của bản viết lại phải đạt. Draft `audit_only` hoặc `drop` gọi xuất sẽ bị từ chối kèm lý do.
- Bài tạo ra vẫn là một post mới ở status `draft`, qua đúng `build_post_payload` hiện tại. Không có request nào trỏ vào id của bài gốc.
- Tiêu đề mang tiền tố `[Viết lại] ` cộng tiêu đề đang dùng của draft, để cặp bài gốc và bản viết lại nhìn thấy nhau ngay trong danh sách wp-admin. Câu hỏi mở số 1 hỏi Smith giữ hay bỏ tiền tố này.
- `featured_media`: nếu bản viết lại giữ ảnh đại diện gốc, payload dùng `wp_media_id` đã ghi, không upload. Ảnh mới thêm trong quá trình viết lại đi đường manifest như bài mới.
- Category lấy theo bài gốc; bài gốc không có category thì dùng `default_category_id` của target như hiện tại.
- Bản ghi `wordpress_export` trên draft cộng trường `rewrite_of`: `{wp_post_id, wp_url, site_url}`. Báo cáo xuất liệt kê các raw block và link của bài gốc không được tự khôi phục vào bản viết lại, để Smith bổ sung tay trong wp-admin nếu cần.
- Luật trùng lặp giữ nguyên: đã có bản ghi xuất thì từ chối, `force=True` mới tạo thêm một nháp nữa và thay bản ghi.

Smith tự thay bài: đọc nháp viết lại trong wp-admin, chuyển nội dung sang bài gốc hoặc đăng bài mới và xử lý slug, rồi tự quyết với bài cũ. Hệ thống dừng ở nháp liên kết.

### 3.7 Ánh xạ sang linh kiện hiện có

| Bước trong luồng | Linh kiện hiện có | Phần phải xây mới |
|---|---|---|
| Đọc bài từ WordPress | `_request` trong `wordpress.py` (đã hỗ trợ GET có auth), target store, `load_app_password`, `redact` | Hai hàm đọc: list posts, get post; hai tool `list_wordpress_posts`, `import_wordpress_post` |
| Tạo draft | Shape draft và các hàm lưu trong `mcp_server.py` | Khối `source`, `original`, `original_audit`, `verdict`; bộ chuyển đổi HTML sang section |
| Audit | fact check, quét claim bị cấm, brand check, craft check hiện có | Tool `audit_imported_post` và luật ghi vào `original_audit` thay vì `gate_history` |
| Phán quyết | Khuôn trạng thái theo mục của research pack | Tool `set_rewrite_verdict` |
| Viết lại | `assemble_context`, `save_section`, `check_draft_all`, `final_gate` | Không |
| Xuất ngược | `export_draft_to_wordpress`, `build_post_payload`, luật trùng lặp | Nhánh `wp_import`: kiểm verdict, tiền tố tiêu đề, dùng thẳng `wp_media_id`, trường `rewrite_of` |
| Ảnh | images state, manifest, exporter upload | Entry mang `wp_media_id` được exporter bỏ qua bước upload |

Không có store mới. Draft import sống trong `drafts/` như mọi draft khác, nên backup, list và delete hiện có áp dụng nguyên trạng.

### 3.8 Đánh đổi

Phương án bị loại thứ nhất là sửa bài gốc tại chỗ qua REST (gửi bản viết lại đè lên post id gốc). Cách đó nhanh hơn một bước cho Smith nhưng phá kỷ luật giao bài đã chốt ngày 2026-11-22: bài đang sống của khách bị thay nội dung trước khi ai duyệt, và một bản viết lại tệ hơn bản gốc sẽ nằm thẳng trên site. Phương án bị loại thứ hai là dán text bài cũ vào chat bằng tay như hiện tại. Cách đó không giữ được snapshot, không có audit gắn với draft, và mỗi bài là một lần chép tay dễ sót section hoặc ảnh. Phương án trong spec đánh đổi như sau: Smith phải tự tay hoàn tất bước thay bài trong wp-admin, và đổi lại bài gốc an toàn tuyệt đối cùng một hồ sơ audit truy được cho từng bài.

## 4. User stories và tiêu chí chấp nhận

**US-1.** Smith mở danh sách bài của kho.nttung.dev và import bài 723. Anh cần draft trong core chứa đúng bài đó, nguyên bản giữ nguyên văn để đối chiếu về sau.
- `list_wordpress_posts` trả đủ 9 bài của site với id và status đúng như REST.
- Sau import, `original.content_html` bằng đúng từng byte với `content.rendered` mà API trả.
- Tỉ lệ bao phủ của báo cáo import đạt từ 0,97 trở lên, hoặc draft mang cờ bao phủ thấp kèm số đo cụ thể.

**US-2.** Bài 241 chứa một con số giá không còn khớp hồ sơ fact. Smith cần con số đó xuất hiện trong audit của bài, tách khỏi kết quả cổng của bản viết lại sau này.
- Fixture dựng bài có giá lệch fact: audit ghi fact issue đó vào `original_audit.fact_issues`, và `gate_history` của draft vẫn rỗng sau audit.
- Chạy lại `audit_imported_post` sau khi sửa hồ sơ fact làm audit cập nhật theo fact mới.

**US-3.** Smith viết lại bài 723 tới khi qua cổng và xuất ngược. Anh cần một nháp mới trên WordPress nhìn thấy là bản viết lại của bài 723, và bài 723 không đổi một ký tự.
- Mock WP server ghi log mọi request trong suốt vòng import, viết lại, xuất: số request ghi (POST, PUT, PATCH, DELETE) trỏ vào route của id gốc bằng 0; request tạo bài mang status `draft` và tiêu đề có tiền tố liên kết.
- Bản ghi `wordpress_export.rewrite_of.wp_post_id` bằng 723.
- Sau xuất, `original` trong draft vẫn bằng bản chụp lúc import (so sánh sâu toàn bộ khối).

**US-4.** Bài 717 (Cam Sành Vĩnh Long) lệch chủ đề shop. Smith cần kết thúc bài này bằng phán quyết, không bị ép viết lại.
- Import và audit chạy bình thường; audit gắn cờ các claim không có trong hồ sơ fact.
- `set_rewrite_verdict` với `drop` (hoặc `audit_only`) ghi được phán quyết kèm lý do, và gọi xuất trên draft đó bị từ chối kèm lý do verdict, không tạo gì trên WordPress.

**US-5.** Smith import nhầm lại bài đã import. Anh cần hệ thống chặn trùng như exporter đang chặn xuất trùng.
- Import lần hai bị từ chối và trả draft_id của draft đã có.
- `force=True` tạo draft mới có `supersedes` trỏ draft cũ; draft cũ ở status `superseded` và bị từ chối khi gọi xuất.

**US-6.** Một bài chứa shortcode và bảng giá dạng table. Smith cần phần không chuyển được không biến mất lặng lẽ.
- Shortcode và table nằm trong `original.raw_blocks` với ghi chú loại, báo cáo import liệt kê chúng, và tỉ lệ bao phủ tính cả phần text trong raw block.
- Báo cáo xuất của bản viết lại liệt kê lại các raw block này là phần không tự khôi phục.

## 5. Trường hợp biên và xử lý lỗi

- Bài chỉ có ảnh, không có text: import thành công với 0 section có text, draft gắn cờ `no_text`. Phán quyết khả thi là `audit_only` hoặc `drop`; gọi viết lại trên draft không có section bị từ chối kèm lý do.
- Bài rất dài (trên 20.000 từ): import bình thường, báo cáo import cảnh báo kích thước. Đo hiệu năng vòng perf cho thấy cổng xử lý 42.000 từ trong khoảng 0,6 giây, nên không cần chia nhỏ.
- Bài bị sửa trên WordPress giữa lúc liệt kê và lúc import: snapshot là trạng thái tại thời điểm import, `imported_at` và mốc sửa của bài được ghi trong provenance. WordPress không có khóa; spec chấp nhận cửa sổ này và dựa vào snapshot để đối chiếu.
- Bài bị xóa giữa lúc liệt kê và import: lỗi 404 trả về qua `redact` như mọi lỗi exporter, import dừng, không tạo draft dở.
- Bài đặt mật khẩu: từ chối import theo luật ở 3.2.
- Brand chưa cấu hình mật khẩu WordPress: liệt kê và import chỉ thấy bài publish qua đường công khai; import bài draft báo rõ thiếu credential theo đúng thông điệp `not_configured_message` hiện có.
- Featured media trỏ tới media đã bị xóa trên WordPress: snapshot ghi id như API trả; xuất ngược bỏ qua id đó kèm cảnh báo trong báo cáo xuất và để trống `featured_media`.
- Hai bài trùng tiêu đề: không ảnh hưởng, mọi khóa đều theo wp_post_id.
- Site của khách (không phải của Smith) mà tài khoản ứng dụng chỉ có quyền đọc: import và audit chạy được; xuất ngược sẽ lỗi quyền ở bước tạo bài và lỗi đó hiện rõ. Tính năng không yêu cầu nới quyền để đọc.

## 6. Ngoài phạm vi

1. Cập nhật, ghi đè hoặc đổi trạng thái bài gốc trên WordPress. Đây là luật cứng của tính năng, cùng hạng với luật không bao giờ publish của exporter. Mọi vòng build đều bị cấm mở đường này.
2. Tự đăng bản thay thế, gỡ bài cũ, redirect 301, giữ hoặc chuyển slug. Việc thay bài hoàn tất trong wp-admin bởi Smith.
3. Import hàng loạt không qua phán quyết từng bài. `list_wordpress_posts` chỉ liệt kê; import chạy từng bài một, theo kỷ luật từng mục đã chốt cho research pack.
4. Mô tả sản phẩm WooCommerce và pages. Sản phẩm giữ giá, thuộc tính và tồn kho trong field thương mại riêng, nằm ngoài shape section của bài viết; sửa mô tả sản phẩm qua pipeline bài viết có nguy cơ lệch dữ liệu catalogue. Pages thường là trang cấu trúc do builder dựng, chuyển đổi section không trung thực. Cả hai là ứng viên cho một spec riêng sau khi luồng posts chạy ổn.
5. Đồng bộ hai chiều sau import. Đã import rồi mà bài gốc được sửa tiếp trên WordPress, core không tự kéo thay đổi về. Muốn bản mới nhất thì import lại bằng `force` theo luật version ở 3.1.
6. Tạo, sửa hoặc xóa media trên WordPress ở bước import. Importer chỉ ghi lại media id và URL đã có.

## 7. Phụ thuộc

| Phụ thuộc | Chủ sở hữu | Trạng thái |
|---|---|---|
| Exporter, target store, cơ chế secrets và `redact` | Ember | Đã chạy thật trên kho.nttung.dev ở HEAD `ea49db9`; 64 tool, 498 test |
| Mật khẩu ứng dụng cho brand `mambagiaokhoe` | Smith đã cấp | Đã cấu hình local, đã xuất thật 4 bài |
| Hồ sơ fact của `mambagiaokhoe` | Ember và Smith | Đã có; độ phủ ngoài mắm cá lóc còn mỏng, ảnh hưởng độ nhiễu của audit (mục 9) |
| Khả năng viết của model chủ cho khâu viết lại | Bên ngoài brandforge | Cùng rủi ro khả thi đã ghi trong `.pmcontext.md`; cổng xác định là lớp đỡ |
| Thời gian của Smith cho phán quyết và buổi trial 9 bài | Smith | Chưa lên lịch |

## 8. Tiêu chí thành công

Lần thử đầu chạy trên đúng 9 bài ở mục 2.1. Báo cáo trial ghi mọi con số dưới đây vào `trials/` theo định dạng các báo cáo trước.

- Độ trung thực của snapshot: 9/9 bài có `original.content_html` khớp từng byte với dữ liệu API tại thời điểm import.
- Độ bao phủ chuyển đổi: tỉ lệ bao phủ ở 3.2 đạt từ 0,97 trở lên cho ít nhất 8/9 bài; bài nào dưới ngưỡng phải có raw block hoặc cảnh báo giải thích được phần thiếu.
- Audit: 9/9 bài có `original_audit` đầy đủ hai danh sách (fact issue, claim bị cấm), kể cả khi danh sách rỗng.
- Đầu ra: ít nhất 5/9 bài được viết lại tới mức qua `final_gate` và xuất thành nháp liên kết. Mọi bài không xuất phải có phán quyết `audit_only` hoặc `drop` do Smith ghi, kèm lý do. Bài 717 là ứng viên drop đã biết trước; nó tính vào nhóm có phán quyết, không tính là thất bại của luồng.
- Thời gian: trung vị thời gian cho một bài, từ import tới nháp liên kết (hoặc tới phán quyết), tối đa 30 phút. Đối chứng là các bài batch 1 viết từ đầu, mỗi bài tốn một buổi làm việc nhiều vòng.
- Rào chắn cứng thứ nhất, kiểm bằng test tự động và bằng đối chiếu ở trial: số lần bài gốc bị sửa bằng 0. Ở trial, sau khi chạy xong, đọc lại cả 9 bài và so mốc sửa cùng hàm băm nội dung với snapshot lúc import; 9/9 phải không đổi. Ở test, log của mock server khẳng định không request ghi nào trỏ vào id gốc trong toàn bộ vòng đời.
- Rào chắn cứng thứ hai: số lần xuất ngược thành công trên draft có verdict khác `rewrite` bằng 0, và số draft import bị mất hoặc lệch khối `original` sau bất kỳ thao tác nào bằng 0.
- Đánh giá sau đúng một trial. Nếu trung vị thời gian không tốt hơn viết từ đầu, giữ lại import và audit (hai phần này độc lập có giá trị) và xem lại khâu viết lại, thay vì bỏ cả tính năng.

## 9. Rủi ro và giảm thiểu

- Rủi ro giá trị, mức trung bình: phần lớn 9 bài đáng bỏ hơn đáng viết lại, và khâu viết lại ít được dùng. Giảm thiểu: `audit_only` và `drop` là kết quả hạng nhất có hồ sơ, nên trial vẫn cho ra một bản audit nội dung đầy đủ của site dù tỉ lệ viết lại thấp.
- Rủi ro sử dụng, mức thấp tới trung bình: draft import cộng thêm các trạng thái verdict và superseded mà Smith phải theo dõi. Giảm thiểu: verdict chỉ có ba giá trị, draft bị supersede bị khóa xuất, và bề mặt UI (bước ba ở mục 10) hiển thị nguồn gốc import ngay trên thẻ draft.
- Rủi ro khả thi, mức trung bình: độ trung thực của chuyển đổi HTML là ẩn số chính. Bài dựng bằng block builder hoặc shortcode nặng sẽ rớt bao phủ. Giảm thiểu: raw block giữ nguyên văn, tỉ lệ bao phủ đo từng bài ngay ở import, và bài bao phủ thấp bị gợi ý `audit_only` thay vì đi tiếp lặng lẽ.
- Rủi ro khả thi thứ hai, mức trung bình: hồ sơ fact của brand phủ hẹp (hiện mạnh ở mắm cá lóc) làm audit các bài khô cá gắn cờ nhiễu. Giảm thiểu: audit là danh sách tư vấn, không phải cổng; nếu trial cho thấy nhiễu cao, mở rộng fact qua research pack trước khi viết lại hàng loạt.
- Rủi ro vận hành, mức thấp: tài khoản ứng dụng của site khách có quyền sửa bài, và một lỗi code có thể ghi nhầm vào bài gốc. Giảm thiểu: bất biến không ghi vào id gốc có test trên mock server ở mục 4 và đối chiếu hàm băm ở trial mục 8; importer chỉ dùng GET.

## 10. Kế hoạch triển khai

Ba bước, mỗi bước cần Smith duyệt phạm vi trước như cách làm việc hiện tại. Bước một: xây ở core gồm hai hàm đọc WordPress, bộ chuyển đổi, bốn tool mới và nhánh xuất `wp_import`, kèm test fail-first cho mọi tiêu chí ở mục 4 và hai rào chắn ở mục 8, chạy trên mock WP server như bộ test exporter hiện có. Bước hai: trial trên 9 bài thật của kho.nttung.dev, vận hành trong chat, báo cáo đo lường gồm cả đối chiếu hàm băm bài gốc. Bước ba: đưa danh sách bài và trạng thái import lên UI app (trang draft đã có panel WordPress), sau khi hợp đồng dữ liệu qua trial không đổi nữa. Tính năng cộng thêm hoàn toàn: brand không có target WordPress thì không có gì thay đổi, và luồng viết bài mới giữ nguyên.

## 11. Pre-mortem

Giả định ba tháng sau khi tính năng chạy và nó đã thất bại. Năm lý do có khả năng nhất:

1. Chuyển đổi làm nát các bài dựng bằng block và shortcode, section không dùng để viết lại được. Khả năng 35%. Tín hiệu sớm: tỉ lệ bao phủ dưới 0,97 ở quá nửa số bài ngay trial đầu. Giảm thiểu: raw block và cờ bao phủ thấp có sẵn trong spec; các bài đó dừng ở audit, và vòng build sau chỉ đầu tư vào chuyển đổi nếu trial cho thấy đáng.
2. Audit gắn cờ quá nhiều vì hồ sơ fact mỏng, Smith mất niềm tin vào danh sách và bỏ qua cả các cờ đúng. Khả năng 30%. Tín hiệu sớm: Smith bác phần lớn finding ở 2 bài audit đầu tiên. Giảm thiểu: audit tách khỏi cổng và ghi rõ nguồn fact cho từng finding; mở rộng fact bằng research pack là việc chuẩn bị trước khi audit hàng loạt.
3. Smith đăng cả bài gốc lẫn nháp viết lại vì quên mất quan hệ liên kết, tạo nội dung trùng trên site. Khả năng 20%. Tín hiệu sớm: một bài trùng xuất hiện trên site sau trial. Giảm thiểu: tiền tố tiêu đề, trường `rewrite_of`, và báo cáo trial liệt kê cặp gốc và nháp để Smith xử lý dứt điểm từng cặp.
4. Bài cũ cần viết lại gần như toàn bộ, thời gian mỗi bài ngang viết từ đầu, và luồng import thành việc thừa. Khả năng 30%. Tín hiệu sớm: trung vị thời gian ở trial chạm ngưỡng 30 phút mà tỉ lệ section giữ nguyên văn dưới 20%. Giảm thiểu: phán quyết có trước khi viết; bài nào phải viết lại toàn bộ thì verdict vẫn ghi nhận công audit, và Smith có số liệu để quyết định các site sau đi đường viết mới luôn.
5. Người viết lại chèn lại link và raw block sai vị trí vì chúng nằm ngoài text section. Khả năng 25%. Tín hiệu sớm: báo cáo xuất liệt kê nhiều link chưa khôi phục ở các bài đầu. Giảm thiểu: danh sách link theo section đi kèm ngữ cảnh assemble, và báo cáo xuất nhắc lại phần chưa khôi phục để Smith kiểm tra trong wp-admin.

## 12. Câu hỏi mở

1. Tiền tố `[Viết lại] ` trên tiêu đề nháp liên kết: giữ, đổi chữ khác, hay bỏ? Đề xuất: giữ, vì wp-admin là nơi cặp bài được nhận diện nhanh nhất. Người quyết: Smith. Không chặn bước một; tiền tố là một hằng số dễ đổi.
2. Phán quyết `drop` chỉ ghi nhận trong core, hay cần một danh sách tổng hợp "bài nên gỡ" xuất ra cho Smith xử lý theo đợt? Đề xuất: ghi trong core ở v1, danh sách tổng hợp để bước ba (UI) lo. Người quyết: Smith. Không chặn trial.
3. Bài viết lại có nên kế thừa tag của bài gốc cùng với category không? Spec đã chốt category kế thừa theo mặc định. Tag để mở vì tag của site cũ thường lộn xộn. Đề xuất: không kế thừa tag ở v1. Người quyết: Smith. Không chặn bước một.
4. Sau trial 9 bài, mô tả sản phẩm WooCommerce có được đưa vào một spec tiếp theo không? Spec này để sản phẩm ngoài phạm vi (mục 6). Người quyết: Smith, sau khi có số liệu trial. Không chặn tính năng này.

## 12b. Quyết định đã chốt (2026-11-23, Ember theo ủy quyền Brandforge của Smith)

Smith ủy quyền cho Ember tự quyết các phần Brandforge ("phần nào về Brandforge bạn tự quyết luôn giúp mình", 2026-11-23). Bốn câu hỏi ở mục 12 được chốt như sau — cả bốn đều theo đúng đề xuất đã ghi trong spec, nên hướng thiết kế không đổi; việc ủy quyền chỉ gỡ điểm chờ để bước một tiến hành:

1. **Tiền tố `[Viết lại] ` được GIỮ** trên tiêu đề nháp liên kết (hằng số `REWRITE_TITLE_PREFIX` trong `content_core/wp_convert.py`, dễ đổi một chỗ).
2. **Tag KHÔNG kế thừa; category kế thừa** theo bài gốc (bài gốc không có category thì dùng `default_category_id` của target như exporter hiện tại).
3. **Danh sách tổng hợp "bài nên gỡ" là việc của tầng UI** (bước ba). Core chỉ ghi phán quyết `drop` kèm lý do theo từng bài; không có tool tổng hợp ở v1.
4. **Mô tả sản phẩm WooCommerce nằm NGOÀI vòng build này** — ứng viên cho một spec riêng sau khi luồng posts chạy ổn, đúng mục 6.

Ghi kèm trong `pmdecisions.md` (mục "WordPress rewrite: §12 open questions resolved under Smith's Brandforge delegation").
