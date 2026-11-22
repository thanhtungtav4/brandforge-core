# Spec: Tìm ảnh thật, nhận diện và tinh chỉnh bằng AI cho bài viết

Ngày: 2026-11-22. Trạng thái: đã ghi quyết định trong `pmdecisions.md` (mục "Article images: search real photos, vet, then AI-edit"), xây dựng để sau. Tài liệu này là đầu vào cho vòng build của lớp ảnh, chưa phải cam kết xây ngay.

## 1. Vấn đề

Lớp ảnh hiện tại có hai nguồn cho mỗi ô ảnh (slot) trong bài: thư viện ảnh của brand trước, sinh ảnh bằng AI sau (`content_core/images.py`, ngưỡng khớp thư viện 0.35). Thư viện của các brand mới bắt đầu gần như trống, nên phần lớn slot rơi vào nguồn thứ hai. Với brand Mắm Bà Giáo Khỏe, cả 6 slot của bài giới thiệu v4 đều do AI sinh.

Ngày 22/11/2026, Smith xem bộ ảnh đã sinh và kết luận ảnh sinh từ đầu nhìn quá rõ là ảnh AI. Nguyên văn của anh: nên làm luồng "tìm kiếm ảnh, về nhận diện ảnh rồi dùng AI tinh chỉnh lại để bám sát hơn" thay vì "tự tạo nó, vì nhìn AI quá". Vấn đề cụ thể nằm ở loại nội dung brandforge phục vụ: ảnh thực phẩm và ảnh shop bán hàng. Khách mua mắm, khô cá đọc ảnh để phán đoán đồ thật hay không. Ảnh sinh có bề mặt quá sạch, nhãn hũ và kết cấu món ăn sai lệch ở chi tiết nhỏ, và người xem nhận ra. Một bộ ảnh làm mất niềm tin vào sản phẩm thì cổng chất lượng chữ tốt đến đâu cũng không cứu được bài.

Job của người dùng: khi viết bài cho một shop, Smith cần ảnh trông như ảnh chụp thật của shop đó, bám chủ đề bài và bám visual style của brand, mà không phải tự đi chụp hay tự lục kho ảnh cho từng bài.

## 2. Bối cảnh và bằng chứng

Ba tiền lệ trong chính dự án này định hình spec:

1. Kỷ luật nguồn của research pack áp dụng được cho ảnh. Mọi tư liệu vào cấu hình đều có provenance, nguồn rác bị loại kèm lý do, và tư liệu chưa rõ quyền thì không vào được. Ảnh lấy từ mạng là tư liệu thương mại trên site khách hàng, rủi ro pháp lý cao hơn corpus văn phong, nên kỷ luật phải chặt hơn, không lỏng hơn.
2. Lớp ảnh hiện tại đã có sẵn khung cho luồng mới. `plan_images` sinh slot kèm prompt và style block theo visual profile của brand. `record_vision_check` đã là lớp phán đoán (judged layer) cho ảnh: model chủ nhìn ảnh và chấm content/style theo thang 0/1/2, core giữ bản ghi và chặn grade 0. `build_manifest` đóng gói ảnh cho WordPress exporter, và exporter đã chạy thật (4 bài lên kho.nttung.dev ngày 22/11, post 777 tới 780).
3. Phân chia việc của kiến trúc hiện tại giữ nguyên: model chủ hành động ở ngoài (viết chữ, sinh ảnh), core kiểm tra và ghi lại ở trong. Tìm kiếm và tinh chỉnh ảnh cũng là hành động ở ngoài do model chủ làm bằng công cụ của nó. Core không tự gọi API tìm ảnh và không tự sửa pixel, giống như core không tự sinh ảnh hiện nay.

Phần thiếu gồm bốn thứ: một bậc tìm ảnh thật trong chuỗi lấp slot, một bản ghi nhận diện cho ứng viên trước khi chọn, một bản ghi các thao tác tinh chỉnh kèm luật trung thực sản phẩm, và các trường provenance/giấy phép trong manifest.

## 3. Giải pháp đề xuất

Chuỗi lấp một slot có sáu bậc. Bậc 1 và bậc 5 dùng lại nguyên trạng. Bậc 2, 3, 4 là mới. Bậc 6 là bậc sinh ảnh hiện tại, hạ xuống cuối.

### 3.1 Chuỗi lấp slot

1. **Thư viện của brand.** `fulfil_images_from_library` chạy như hiện tại: xếp hạng theo token overlap, ngưỡng 0.35, một entry không lấp hai slot.
2. **Tìm ảnh thật.** Slot không có ảnh thư viện phù hợp chuyển sang trạng thái chờ tìm. Model chủ tìm ảnh bằng công cụ tìm kiếm của nó, chỉ trong các nguồn thuộc allowlist của brand (mục 3.3), rồi đăng ký ứng viên vào core. Mỗi slot giữ tối đa 5 ứng viên.
3. **Nhận diện ứng viên.** Model chủ nhìn từng ứng viên và ghi phán đoán vào core qua bản ghi vetting (mục 3.4): đúng chủ thể của slot hay không, bố cục dùng được không, có watermark hay chữ nhúng trong ảnh không, độ phân giải đủ cho kích thước hiển thị của slot không. Ứng viên sai chủ thể hoặc có watermark bị loại kèm lý do và không đi tiếp.
4. **Tinh chỉnh bằng AI.** Model chủ sửa ảnh đã chọn ở ngoài core: cắt theo tỉ lệ slot (16:9 featured, 4:3 in-content như hiện tại), cân màu và ánh sáng về phía visual style profile của brand, dọn chi tiết gây nhiễu ở hậu cảnh. Các thao tác được ghi vào core theo từ vựng cố định ở mục 3.5. Tinh chỉnh không vẽ lại ảnh và không thay đổi sản phẩm trong ảnh.
5. **Vision check trên kết quả.** `record_vision_check` chấm ảnh sau tinh chỉnh như hiện tại. Grade 0 ở một trong hai trục làm slot mở lại: thử bản gốc chưa tinh chỉnh của cùng ứng viên (nếu bản gốc qua được vision check), rồi ứng viên tiếp theo, rồi mới xuống bậc 6.
6. **Sinh ảnh từ đầu, phương án cuối.** Chỉ chạy khi bậc 2 tới 4 không lấp được slot và người gọi xác nhận tường minh đã hết đường tìm (cờ `allow_generation`). Manifest ghi rõ ảnh này là ảnh sinh (mục 3.6). Prompt và style lock giữ nguyên cơ chế hiện tại.

Slot gắn cờ `product_specific` (ảnh phải là sản phẩm thật của shop, ví dụ hũ mắm đúng nhãn của shop) bỏ qua bậc 2 tới 4: kho stock không thể có ảnh sản phẩm của shop. Các slot này đi thẳng từ thư viện xuống bậc 6. Cờ do người viết đặt lúc lập kế hoạch, dựa trên intent của section. Câu hỏi mở số 2 ở mục 12 hỏi Smith có muốn các slot này chỉ dùng thư viện hay không.

### 3.2 Việc của core và việc của model chủ

Core làm bốn việc: giữ allowlist theo brand và kiểm tra ứng viên có thuộc nguồn được phép; giữ candidate pool và bản ghi vetting; kiểm tra trường giấy phép và từ vựng thao tác sửa khi một ảnh searched được lấp vào slot; và chặn manifest khi một ảnh searched thiếu provenance. Model chủ làm ba việc ở ngoài: tìm, nhìn, sửa. Ranh giới này khớp với cách sinh ảnh vận hành hiện nay, nên vòng build không cần thêm hạ tầng mạng nào trong core.

### 3.3 Giấy phép: luật cứng

- Chỉ bốn loại nguồn được vào allowlist: kho stock có giấy phép dùng thương mại rõ ràng, nguồn brand sở hữu hoặc có quyền dùng, file user cung cấp, và ảnh public domain. Câu hỏi mở số 1 ở mục 12 chốt danh sách kho stock cụ thể.
- Allowlist lưu trong brand YAML ở field `image_sources`, cạnh section `visual`, validate cùng khuôn với `visual` trong loaders. Brand không có field này, hoặc field rỗng, thì bậc tìm ảnh tắt. Chuỗi của brand đó quay về đúng hành vi hiện tại: thư viện rồi sinh.
- Mỗi ứng viên đăng ký phải có `source_url` và `license_type`. `license_type` thuộc tập cố định: `stock_commercial` (giấy phép của kho cho phép dùng thương mại), `stock_paid` (đã mua quyền), `brand_owned`, `user_supplied`, `public_domain`, `cc_attribution` (dùng được với điều kiện ghi nguồn). Giá trị ngoài tập này, gồm mọi dạng "không rõ", bị core từ chối ngay lúc đăng ký. Ứng viên không có giấy phép hợp lệ không tồn tại được trong pool, giống luật fact không có `source_url` không tồn tại được trong research pack.
- Giấy phép dạng `cc_attribution` bắt buộc manifest sinh `attribution_text`, và caption WordPress của ảnh đó phải chứa attribution. Manifest chưa sẵn sàng khi một ảnh cần attribution mà `attribution_text` rỗng.
- Mua stock trả phí tự động nằm ngoài phạm vi (mục 6). Stock trả phí chỉ vào được khi Smith tự mua và cung cấp file, đăng ký dưới `stock_paid` kèm ghi chú số đơn hoặc nơi mua trong provenance.

### 3.4 Bản ghi nhận diện (vetting)

Mỗi ứng viên có một bản ghi vetting do model chủ ghi sau khi nhìn ảnh, cùng khuôn với vision check hiện tại (core giữ bản ghi, không giữ việc nhìn):

| Trường | Giá trị |
|---|---|
| `content_grade` | 0/1/2: mức khớp chủ thể và bối cảnh với intent của slot, cùng thang của vision check |
| `issues` | Tập con của từ vựng cố định: `watermark`, `embedded_text`, `low_resolution`, `wrong_subject`, `off_brand_scene` |
| `notes` | Ghi chú tự do của người nhìn |
| `checked_at` | Thời điểm ghi |

Ứng viên có `content_grade` bằng 0, hoặc `issues` chứa `watermark` hay `wrong_subject`, bị loại và không được chọn để sửa. Lý do loại ghi trong pool để buổi duyệt ảnh (nếu có) và báo cáo trial đọc lại được. Xóa watermark bằng tinh chỉnh bị cấm tuyệt đối: watermark là dấu của ảnh chưa có quyền, và vetting loại ảnh đó thay vì rửa nó.

### 3.5 Tinh chỉnh và luật trung thực sản phẩm

Từ vựng thao tác sửa cố định, core kiểm tra khi ảnh searched được lấp vào slot:

- Được phép: `crop`, `resize`, `color_grade`, `exposure`, `white_balance`, `sharpen`, `background_cleanup` (dọn vật gây nhiễu ở hậu cảnh, không chạm vào sản phẩm).
- Bị cấm, ghi vào là hard issue `edit_op_not_allowed` và chặn manifest: mọi thao tác ngoài từ vựng trên. Cấm cụ thể: đổi màu, hình dạng, nhãn, số lượng của sản phẩm; thêm hoặc bớt sản phẩm; thay nền bằng cảnh khác; xóa watermark.

Luật này là kỷ luật fact của core áp lên ảnh. Bài viết không được bịa giá hay bịa quy trình, và ảnh của bài cũng không được bịa hình dạng sản phẩm. Ảnh sau sửa vẫn phải là ảnh của đúng món đồ trong ảnh gốc.

Ảnh searched được tải về thành file local trước khi lấp slot. `ref` trong draft là đường dẫn local như mọi nguồn ảnh khác, để các kiểm tra file hiện tại (định dạng, ngân sách dung lượng) và WordPress exporter chạy nguyên trạng. `source_url` sống trong provenance, không thay `ref`.

### 3.6 Provenance trong manifest

`build_manifest` cộng thêm các trường sau cho mọi ảnh, không chỉ ảnh searched:

| Trường | Ý nghĩa |
|---|---|
| `origin` | `library`, `searched_edited`, `searched_unedited`, hoặc `generated` |
| `source_url` | URL nguồn gốc của ảnh searched; rỗng với library và generated |
| `license_type` | Một giá trị trong tập ở 3.3; rỗng với library và generated |
| `attribution_text` | Bắt buộc có nội dung khi `license_type` là `cc_attribution` |
| `edit_ops` | Danh sách thao tác đã áp lên ảnh, theo từ vựng 3.5; rỗng khi không sửa |
| `generated` | `true` chỉ khi `origin` là `generated` |

Bản ghi ảnh trong draft (đầu vào của manifest) mang các trường tương ứng qua một khối `origin` trên entry đã lấp: `{kind, source_url, license_type, candidate_id, edit_ops}`. `image_check` cộng ba hard issue mới: `image_license_missing` (ảnh searched thiếu `source_url` hoặc `license_type`), `edit_op_not_allowed` (thao tác ngoài từ vựng), `attribution_missing` (giấy phép cần ghi nguồn mà chưa có `attribution_text`). Cả ba chặn `ready` của manifest như các hard issue hiện tại. Lớp ảnh giữ ranh giới advisory hiện tại: không hard issue nào trong số này vào verdict của `final_gate`.

### 3.7 Ánh xạ sang linh kiện hiện có

| Bước trong luồng | Linh kiện hiện có | Phần phải xây mới |
|---|---|---|
| Lập kế hoạch slot | `plan_images` trong `images.py` | Cờ `product_specific` trên slot |
| Bậc thư viện | `fulfil_images_from_library` (tool MCP), `best_library_match`, ngưỡng 0.35 | Không |
| Bậc tìm ảnh | Chưa có | Candidate pool trong images state của draft; tool đăng ký ứng viên (tên dự kiến `add_image_candidate`); kiểm tra allowlist và giấy phép khi đăng ký |
| Bậc nhận diện | Khuôn bản ghi của `record_vision_check` | Tool ghi vetting (tên dự kiến `record_candidate_vetting`) và luật loại ứng viên |
| Bậc tinh chỉnh | Việc sửa nằm ngoài core như việc sinh ảnh hiện nay | Lấp slot từ ứng viên qua `set_image` mở rộng: `source="searched"` kèm khối `origin` bắt buộc; kiểm tra từ vựng `edit_ops` trong `check_images` |
| Vision check kết quả | `record_vision_check`, `check_images` | Logic mở lại slot và rơi xuống bậc tiếp theo khi grade 0 (điều phối ở tool lấp slot, tên dự kiến `fulfil_images_v2` hoặc mở rộng tool hiện tại; vòng build chốt) |
| Bậc sinh ảnh | `needs_generation` trong kết quả fulfil hiện tại, style lock, `prompt_used` | Đổi trạng thái báo cáo: sinh ảnh chỉ xuất hiện khi cờ `allow_generation` bật; manifest đánh dấu `generated` |
| Manifest | `build_manifest`, `get_image_manifest`, WordPress exporter | Các trường ở 3.6; exporter cộng attribution vào caption khi manifest có `attribution_text` |
| Allowlist nguồn | Section `visual` trong brand YAML và `validate_visual` trong loaders là khuôn gần nhất | Field `image_sources` trong brand YAML + hàm validate riêng trong loaders |

Không có store mới. Candidate pool sống trong images state của draft, cùng nơi plan và ảnh đã lấp đang sống. Cache kết quả tìm kiếm dùng chung giữa các draft bị hoãn vô thời hạn: pool theo draft đủ cho lần thử đầu và tránh một store nữa phải đồng bộ.

### 3.8 Đánh đổi

Phương án bị loại thứ nhất là giữ nguyên sinh ảnh sau thư viện. Phương án đó đã cho ra bộ ảnh mà Smith chê nhìn rõ là AI, và chi phí của nó tính bằng niềm tin của người đọc trên site khách. Phương án bị loại thứ hai là tìm ảnh thật rồi dùng nguyên bản, không tinh chỉnh. Ảnh stock nguyên bản lệch visual style của brand theo từng ảnh, và bộ ảnh trong một bài trông như gom từ nhiều nguồn khác nhau; đó chính là vấn đề style lock đã giải quyết cho ảnh sinh. Phương án trong spec đánh đổi như sau: mỗi bài tốn thêm các bước tìm, nhìn, sửa cho từng slot không có ảnh thư viện, và chất lượng bước sửa phụ thuộc vào khả năng sửa ảnh của ứng dụng chủ, thứ brandforge không kiểm soát được.

## 4. User stories và tiêu chí chấp nhận

**US-1.** Smith viết bài cho shop có thư viện trống. Một slot cảnh chợ buổi sáng không có ảnh thư viện. Anh cần slot đó nhận ứng viên ảnh thật để duyệt, thay vì nhận ngay một prompt sinh ảnh.
- Sau khi fulfil chạy, slot không có ảnh thư viện và brand có allowlist thì ở trạng thái chờ ứng viên, không xuất hiện trong danh sách `needs_generation`.
- Đăng ký 5 ứng viên thì pool giữ đúng 5; đăng ký ứng viên thứ 6 bị từ chối kèm lý do vượt trần.

**US-2.** Một ứng viên đẹp nhưng nguồn không rõ giấy phép. Smith cần ứng viên đó không vào được hệ thống, để site khách không mang rủi ro pháp lý vì một tấm ảnh.
- Test: đăng ký ứng viên thiếu `license_type`, hoặc `license_type` ngoài tập ở 3.3, đều bị từ chối và pool không đổi.
- Test: brand không có `image_sources` thì tool đăng ký ứng viên báo lỗi rõ ràng và không tạo pool.

**US-3.** Ứng viên có watermark của kho stock. Smith cần nó bị loại ở bước nhận diện, không ai rửa watermark rồi dùng.
- Test: ghi vetting với `issues` chứa `watermark` làm ứng viên chuyển trạng thái bị loại; chọn ứng viên đó để lấp slot bị từ chối.
- Từ vựng thao tác sửa không chứa thao tác xóa watermark, và ghi `edit_ops` chứa giá trị ngoài từ vựng làm `image_check` sinh hard issue `edit_op_not_allowed`.

**US-4.** Ảnh sau tinh chỉnh bị vision grade 0 ở trục content. Smith cần slot tự mở lại và thử đường khác, thay vì mang ảnh sai vào manifest.
- Test: grade 0 trên ảnh `searched_edited` làm fulfil rơi xuống bản gốc chưa sửa của cùng ứng viên; bản gốc cũng grade 0 thì rơi xuống ứng viên tiếp theo còn sống; hết ứng viên thì slot báo `needs_generation` chỉ khi cờ `allow_generation` bật.

**US-5.** Một bài dùng cả ba nguồn ảnh. Smith cần đọc manifest và biết chính xác ảnh nào từ đâu, ảnh nào là ảnh sinh.
- Test: manifest của draft có ảnh library, ảnh searched đã sửa và ảnh sinh mang `origin` đúng từng loại, và chỉ ảnh sinh có `generated` bằng `true`.
- Test: ảnh searched thiếu `source_url` trong bản ghi làm `image_check` sinh `image_license_missing` và `ready` của manifest bằng `false`.

**US-6.** Slot ảnh hũ mắm đúng nhãn của shop. Smith cần slot này không bao giờ nhận ảnh stock của một hãng mắm khác.
- Test: slot có cờ `product_specific` không nhận ứng viên ở bất kỳ bước nào; fulfil báo slot thuộc diện thư viện hoặc sinh.

## 5. Trường hợp biên và xử lý lỗi

- Không có kết quả tìm nào có giấy phép rõ ràng cho một slot: slot rơi xuống bậc sinh với cờ xác nhận, hoặc để trống nếu người gọi không bật cờ. Luật giấy phép không nới trong mọi trường hợp.
- Mọi ứng viên của một slot đều bị loại ở nhận diện: pool giữ lý do loại của từng ứng viên, slot rơi xuống bậc sinh như trên. Lý do loại là dữ kiện cho báo cáo trial, không bị xóa.
- Tinh chỉnh làm ảnh tệ hơn bản gốc (màu lệch, chi tiết nhãn bị méo): vision grade trên kết quả bắt được. Grade 0 mở lại slot theo thứ tự ở 3.1 bậc 5. Bản gốc chưa sửa được dùng khi nó qua vision check, kể cả khi style grade chỉ đạt 1.
- URL nguồn chết sau khi đăng ký: không ảnh hưởng, vì ảnh đã tải về local lúc chọn. `source_url` chỉ còn vai trò provenance và attribution.
- Hai slot chọn cùng một ảnh searched: luật `duplicate_ref` hiện tại áp nguyên trạng, vì `ref` local là đơn vị duy nhất.
- Ứng viên là ảnh sản phẩm của đối thủ có nhãn rõ ràng: vetting loại bằng `wrong_subject` hoặc `off_brand_scene`. Nếu lọt qua vetting, vision check trên kết quả là chốt chặn thứ hai, và luật trung thực ở 3.5 cấm sửa nhãn thành nhãn của shop.
- Brand có allowlist nhưng kho nguồn đổi điều khoản giấy phép giữa chừng: provenance trong manifest giữ `license_type` tại thời điểm lấy ảnh. Việc rà soát lại giấy phép của ảnh đã đăng nằm ngoài hệ thống; Smith quyết khi có thông báo đổi điều khoản.
- Ứng dụng chủ không có khả năng sửa ảnh: bậc 4 không chạy được. Chuỗi suy biến thành thư viện, searched dùng nguyên bản (origin `searched_unedited`, vẫn qua vetting và vision check), rồi sinh. Spec ghi nhận đây là chế độ suy biến hợp lệ, không phải lỗi.
- Ảnh searched dung lượng vượt ngân sách slot (200KB featured, 150KB in-content): kiểm tra file hiện tại gắn cờ như với mọi ảnh local. Bước `resize` trong tinh chỉnh là nơi xử lý trước khi lấp.

## 6. Ngoài phạm vi

1. Xây tích hợp tìm kiếm và mọi code khác. Tài liệu này là spec cộng quyết định. Vòng build là một vòng riêng cần Smith duyệt phạm vi, như mọi vòng trước.
2. Video, GIF và mọi định dạng động. Slot giữ nguyên ảnh tĩnh webp/jpg/png như hiện tại. Kho stock video không được động vào.
3. Mua stock trả phí tự động, gồm tạo tài khoản kho, thanh toán và checkout giấy phép. Không có luồng tiền nào trong tính năng này. Stock trả phí chỉ vào qua file Smith tự mua và cung cấp.
4. Thay đổi chính sách về người và khuôn mặt trong ảnh. Tính năng này không sinh mặt người mới và không đặt máy móc chính sách mới cho ảnh người trong stock; ảnh stock có người đi theo giấy phép của kho và phán đoán của Smith theo từng bài, như ảnh client hiện tại.
5. Cào ảnh từ web nói chung, ví dụ kết quả Google Images. Nguồn bị giới hạn ở allowlist tại 3.3 vì lý do giấy phép. Mở rộng sang cào web cần một quyết định riêng kèm rà soát pháp lý.
6. Đưa lớp ảnh vào verdict của `final_gate`. Lớp ảnh giữ ranh giới advisory hiện tại. Thay đổi ranh giới đó là quyết định riêng, không mượn vòng này.

## 7. Phụ thuộc

| Phụ thuộc | Chủ sở hữu | Trạng thái |
|---|---|---|
| Danh sách kho stock trong allowlist mặc định | Smith duyệt | Chưa chốt, là câu hỏi mở số 1 |
| Khả năng tìm ảnh, nhìn ảnh và sửa ảnh của ứng dụng chủ | Bên ngoài brandforge | Đã có tiền lệ với sinh ảnh và vision check; khả năng sửa ảnh chưa chạy thử trong dự án này |
| WordPress exporter cộng attribution vào caption | Ember, trong vòng build | Exporter đã chạy thật; thay đổi nhỏ ở bước đóng gói caption |
| Field `image_sources` trong brand YAML và validate ở loaders | Ember, trong vòng build | Chưa có; theo khuôn `visual` hiện tại |
| Thời gian của Smith cho buổi chấm mù ở lần thử đầu | Smith | Chưa lên lịch |

## 8. Tiêu chí thành công

Lần thử đầu chạy trên batch bài tiếp theo của shop Khô Mắm An (brand `mambagiaokhoe`), cùng brand đã có 4 bài và 6 ảnh sinh ở vòng trước để đối chiếu. Báo cáo trial ghi mọi con số dưới đây vào `trials/` như các báo cáo trước.

- Chỉ số chính thứ nhất: tỉ lệ slot được lấp mà không cần sinh ảnh, tính trên mọi slot của các bài trong batch. Mục tiêu: ít nhất 2/3 tổng số slot có `origin` thuộc `library`, `searched_edited` hoặc `searched_unedited`. Đếm trực tiếp từ manifest của từng bài.
- Chỉ số chính thứ hai: chấm mù của Smith. Báo cáo trial trộn ảnh searched đã tinh chỉnh của batch với 6 ảnh đã sinh của bài giới thiệu v4, bỏ nhãn nguồn, và Smith chấm từng ảnh theo thang 1 tới 5 cho câu hỏi "ảnh này trông như ảnh chụp thật của một shop bán mắm hay không". Mục tiêu: điểm trung bình của nhóm tinh chỉnh cao hơn nhóm sinh ít nhất 1 điểm, hoặc ít nhất 80% ảnh tinh chỉnh đạt từ 4 điểm trở lên. Đây là thước đo khớp với lời chê gốc của Smith, nên nó nặng ký hơn mọi chỉ số máy.
- Chỉ số phụ: tỉ lệ ảnh tinh chỉnh qua vision check ngay lần chấm đầu (grade từ 1 trở lên ở cả hai trục). Mục tiêu: ít nhất 90%. Con số này cho biết bước sửa có ổn định hay không, tách khỏi câu hỏi gu.
- Chỉ số phụ thứ hai: thời gian khâu ảnh cho một bài, từ lúc fulfil bắt đầu tới lúc manifest ready. Mục tiêu: không chậm hơn khâu sinh ảnh của vòng trước quá 15 phút mỗi bài. Smith là người vận hành duy nhất; một luồng đúng mà chậm quá sẽ bị bỏ khi có deadline khách.
- Rào chắn cứng, kiểm bằng test tự động: số ảnh trong bất kỳ manifest nào của trial thiếu `license_type` hợp lệ hoặc thiếu `source_url` khi là ảnh searched phải bằng 0. Số ảnh có `edit_ops` ngoài từ vựng ở 3.5 phải bằng 0.
- Đánh giá sau đúng một batch. Nếu chấm mù thua mục tiêu, giữ lại bậc tìm và nhận diện (chúng vẫn cho ảnh thật nguyên bản) và xem lại bậc tinh chỉnh, thay vì bỏ cả luồng.

## 9. Rủi ro và giảm thiểu

- Rủi ro giá trị, mức trung bình: ảnh stock đã sửa vẫn trông xa lạ với shop thật, vì kho stock ít cảnh mắm và chợ miền Tây đúng chất. Giảm thiểu: chấm mù ở mục 8 quyết định bằng số trước khi tính năng thành mặc định; slot `product_specific` giữ sản phẩm thật ngoài stock từ đầu.
- Rủi ro sử dụng, mức trung bình: chuỗi sáu bậc làm khâu ảnh dài hơn khâu sinh ảnh, và Smith bỏ bước khi bận. Giảm thiểu: trần 5 ứng viên mỗi slot, các bậc tự rơi xuống bậc tiếp theo khi cạn ứng viên, và đo thời gian ở mục 8 với ngưỡng 15 phút.
- Rủi ro khả thi, mức cao: khả năng sửa ảnh của ứng dụng chủ chưa được kiểm chứng trong dự án này. Nó có thể không có, hoặc sửa làm méo nhãn và chữ trên bao bì. Giảm thiểu: chế độ suy biến dùng ảnh nguyên bản ở mục 5 là đường lui chính thức; vision check trên kết quả chặn ảnh méo trước manifest; lần thử đầu chạy trên shop của chính Smith, không phải site khách.
- Rủi ro pháp lý, mức trung bình: người viết đăng ký sai `license_type` cho một ảnh, và ảnh chưa có quyền lên site khách. Giảm thiểu: allowlist giới hạn nguồn từ đầu, core từ chối giấy phép ngoài tập cố định, provenance nằm trong manifest để rà soát lại được, và ảnh của khách hàng thật luôn có đường thư viện an toàn hơn để ưu tiên.

## 10. Kế hoạch triển khai

Ba bước, mỗi bước cần Smith duyệt phạm vi trước. Bước một: xây ở core gồm field `image_sources`, candidate pool, hai tool mới, mở rộng `set_image` và `check_images`, và các trường manifest ở 3.6, kèm test fail-first cho mọi tiêu chí ở mục 4 và hai rào chắn ở mục 8. Bước hai: lần thử trên batch của shop Khô Mắm An, có chấm mù theo mục 8. Bước ba: nếu số liệu đạt, đưa trạng thái chuỗi lấp slot lên UI app (app brandforge đã có panel ảnh ở trang draft) và xét cache tìm kiếm dùng chung. Tính năng cộng thêm hoàn toàn: brand để trống `image_sources` là tắt bậc tìm, và hành vi hiện tại giữ nguyên cho mọi brand chưa bật.

## 11. Pre-mortem

Giả định ba tháng sau khi tính năng chạy và nó đã thất bại. Năm lý do có khả năng nhất:

1. Kho stock không có cảnh đúng chất miền Tây, pool ứng viên quay về rỗng hoặc toàn ảnh generic, và bậc tìm thành công việc thừa. Khả năng 40%. Tín hiệu sớm: tỉ lệ slot có ít nhất một ứng viên qua vetting dưới 50% ngay batch đầu. Giảm thiểu: đo tỉ lệ này trong báo cáo trial đầu tiên; nếu thấp, thu hẹp kỳ vọng của bậc tìm vào các slot cảnh quan và bối cảnh chung, nơi stock mạnh.
2. Bước tinh chỉnh làm méo chữ trên nhãn hũ và một ảnh méo lọt qua vision grade 1 vào bản nháp khách. Khả năng 25%. Tín hiệu sớm: notes của vision check nhắc tới chữ hoặc nhãn nhiều lần trong batch đầu. Giảm thiểu: luật ở 3.5 cấm sửa vùng sản phẩm; giảm thiểu mạnh hơn, nếu tín hiệu xuất hiện, là giới hạn `background_cleanup` và cân màu toàn ảnh, bỏ mọi sửa cục bộ.
3. Khâu ảnh chậm hơn khâu sinh khoảng nửa giờ mỗi bài và Smith quay lại sinh ảnh khi có việc gấp. Khả năng 30%. Tín hiệu sớm: chỉ số thời gian ở mục 8 vượt ngưỡng ngay batch đầu. Giảm thiểu: trần ứng viên và rơi bậc tự động đã có trong spec; nếu vẫn chậm, cắt bậc tinh chỉnh cho slot in-content và giữ nó cho featured.
4. Một ảnh đăng ký sai giấy phép bị phát hiện sau khi bài đã đăng trên site khách. Khả năng 15%. Tín hiệu sớm: một lần rà soát provenance cho thấy `source_url` không khớp ảnh. Giảm thiểu: manifest giữ provenance để gỡ đúng ảnh trong vài phút; quy trình gỡ ảnh là việc vận hành của Smith, và spec này giữ bằng chứng để việc đó làm được.
5. Ứng dụng chủ đổi hoặc rút khả năng sửa ảnh, và bậc 4 chết lặng lẽ trong khi báo cáo fulfil vẫn trông bình thường. Khả năng 20%. Tín hiệu sớm: tỉ lệ `searched_unedited` tăng đột ngột giữa các batch. Giảm thiểu: báo cáo trial đếm origin theo từng batch, nên sự thay đổi này lộ ra ở số liệu thay vì im lặng.

## 12. Câu hỏi mở

1. Kho stock nào vào allowlist mặc định? Đề xuất: Pexels, Pixabay và Unsplash, ba kho có giấy phép cho dùng thương mại không cần mua. Smith duyệt danh sách và điều khoản từng kho. Người quyết: Smith. Chặn bước một của vòng build, vì validate của `image_sources` cần tập nguồn khởi điểm.
2. Slot `product_specific` chỉ dùng thư viện, hay thư viện rồi sinh? Đề xuất mặc định: thư viện trước, sinh sau, tìm stock bị cấm tuyệt đối cho các slot này. Người quyết: Smith. Chặn hành vi của cờ ở bước một.
3. Ảnh khách hàng của shop gửi qua Zalo (ảnh khách chụp sản phẩm) có được tính là nguồn `user_supplied` không, và ai xác nhận quyền dùng cho từng brand? Đề xuất: được tính, với Smith xác nhận theo brand khi cấu hình allowlist. Người quyết: Smith. Không chặn trial trên shop của chính Smith, chặn khi dùng cho site khách.
4. Giấy phép `cc_attribution` được dùng với attribution trong caption, hay loại khỏi tập giấy phép cho gọn? Đề xuất: giữ, kèm luật attribution ở 3.3 và 3.6. Người quyết: Smith. Không chặn bước một; validate chỉ cần biết tập giấy phép có `cc_attribution` hay không.
