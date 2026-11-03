# Hợp đồng API — Dezon AI Consultant (MVP)

Nguồn sự thật cho kiểu dữ liệu: Pydantic trong `backend/app/schemas/` và `backend/app/evidence.py`.
Frontend sinh/viết tay type TypeScript tương ứng trong `frontend/src/api/types.ts`.

Mọi số liệu hiển thị cho khách nằm trong `EvidenceValue`:

```json
{ "value": [4300000, 4700000], "display": "4,3–4,7 triệu/m²", "status": "on_dezon",
  "source": { "label": "Hồ sơ nhà thầu Kiến Phong", "url": "https://dezon.vn/...", "updated_at": "2026-10-31" },
  "is_stale": false }
```

Frontend **luôn hiển thị `display`** (không tự định dạng lại `value`), kèm badge `status` (mục 4.4) và
tooltip nguồn (`source.label`, `source.url`, `source.updated_at`). `is_stale=true` → hiển thị cảnh báo
"Dữ liệu cập nhật ngày dd/mm/yyyy, có thể đã cũ". `status="unavailable"` → chữ xám "Chưa có dữ liệu".

## Chat

| Method | Path | Body | Trả về |
|---|---|---|---|
| POST | `/api/chat/sessions` | `{user_id?}` | `{session_id, stage, slots, messages: ChatMessageOut[]}` — có sẵn tin chào + thẻ `question_step` (bước 1: loại công trình) |
| POST | `/api/chat/sessions/{id}/messages` | `{content: string}` | **SSE** (xem dưới) |
| GET | `/api/chat/sessions/{id}` | — | `{session_id, stage, slots, missing_required, messages: ChatMessageOut[]}` |

`ChatMessageOut = {id, role: "user"|"assistant", content, ui_blocks: UIBlock[], created_at}`

### SSE `POST /api/chat/sessions/{id}/messages`

Content-Type `text/event-stream`. Các event theo thứ tự:

```
event: text        data: {"delta": "Dạ, "}             # nhiều lần; nối lại thành câu trả lời
event: ui_block    data: {"block": UIBlock}            # 0..n lần, dựng từ kết quả tool
event: done        data: {"message_id": 123, "stage": "collect", "slots": {...}, "missing_required": [...]}
event: error       data: {"message": "..."}            # thay cho done khi lỗi
```

Văn bản chỉ được stream **sau khi** validator (mục 2.1) duyệt, nên `text` là câu trả lời đã an toàn.
Bấm nút `quick_replies` / `question_step` / `summary_confirm` = gửi `value` của nút như một tin nhắn mới.

`UIBlock.type` ∈ `quick_replies | question_step | summary_confirm | estimate_table | budget_options |
contractor_list | comparison_table | contractor_detail | source_badges | quote_request_form` — xem
`app/schemas/ui_blocks.py`.

### Thẻ câu hỏi từng bước `question_step`

Lượt thu thập thông tin (kể cả tin chào) trả thẻ `question_step` THAY cho `quick_replies` khi hỏi slot. Văn bản
`text` của lượt KHÔNG lặp lại câu hỏi (thẻ mang câu hỏi) — chỉ là lời dẫn ngắn, vd "Dạ, anh/chị chọn hoặc nhập thông
tin bên dưới giúp em nhé.". `quick_replies` vẫn dùng ở chỗ khác (bước tiếp sau khái toán, chọn mục cần sửa, hỏi
ngân sách sau khái toán khi khách chưa từng được hỏi).

```
{ type: "question_step",
  question: string,                 // tiêu đề in đậm, vd "Diện tích đất?"
  hint: string | null,              // dòng gợi ý; frontend tự thêm biểu tượng 💡 phía trước (chuỗi KHÔNG chứa 💡)
  options: {label, value}[],        // các dòng đánh số 1..n
  chips: {label, value}[],          // chip nhỏ (vd kích thước đất "4 × 10"), có thể rỗng
  input_placeholder: string | null, // null = không có dòng nhập tự do (bút chì)
  step: int, total: int,            // "step/total" ở đầu thẻ; step bắt đầu từ 1
  skippable: bool,                  // true -> hiện link "Bỏ qua"
  skip_value: string | null,        // tin nhắn gửi khi bấm "Bỏ qua" ("Bỏ qua câu này")
  back_value: string | null,        // tin nhắn gửi khi bấm "Trước"/‹ ("Quay lại câu trước"); null = bước đầu, tắt nút
  slots: string[] }                 // slot đang hỏi (tham khảo)
```

- Chọn một dòng `options` / một `chips` -> gửi `value` của lựa chọn; ô nhập tự do -> gửi đúng chữ khách gõ.
- `back_value`: server hiện lại câu hỏi trước theo danh sách câu hỏi dự kiến (giữ nguyên giá trị đã trả lời; trả lời
  lại thì ghi đè, rồi đi tiếp tới câu còn thiếu). Đang ở câu đầu -> hiện lại câu đầu.
- Phần hỏi trước bản xác nhận gồm ĐÚNG 5 câu, mỗi câu một thẻ, `total` luôn = 5 và `step` = vị trí CỐ ĐỊNH của câu:
  1/5 Loại công trình · 2/5 Khu vực · 3/5 Diện tích đất (căn hộ: diện tích căn hộ) · 4/5 Số tầng (căn hộ: số tầng của
  căn hộ) · 5/5 Phạm vi thi công. Câu khách đã trả lời sẵn (vd trong tin đầu) bị bỏ qua nhưng KHÔNG đánh số lại (vd
  "Nhà phố ở Hà Nội" -> thẻ kế tiếp là 3/5). Đủ 5 câu -> bản xác nhận ngay.
- KHÔNG hỏi trước bản xác nhận: diện tích xây dựng mỗi tầng, hầm/lửng, độ sâu hầm, diện tích lửng, loại mái, phong
  cách, mức đầu tư, ngân sách. Chi tiết dùng mặc định của engine (xây kín đất, không hầm, không lửng, mái bê tông cốt
  thép, móng theo thông lệ), nêu rõ trong `assumptions` của bản xác nhận.
- Nhóm câu hỏi thêm (khách bấm `more_value` "Cung cấp thêm thông tin"): lần lượt Diện tích xây dựng mỗi tầng →
  Hầm/lửng → Loại mái → Độ sâu tầng hầm (nếu có hầm) → Diện tích tầng lửng (nếu có lửng), chỉ các mục chưa có giá trị;
  `step/total` = vị trí trong nhóm này — N chỉ đếm các mục còn thiếu lúc bấm nút (vd đã có diện tích xây dựng -> 1/2;
  trả lời có hầm -> total tăng thêm câu độ sâu hầm). Hết nhóm -> bản xác nhận. Câu đầu nhóm `back_value = null`.
- "Sửa" một dòng trên bản xác nhận -> thẻ hỏi đúng mục đó (câu cốt lõi: i/5 cố định; mục chi tiết, phong cách, ngân
  sách, mức đầu tư: 1/1, `back_value = null`; phong cách / ngân sách / mức đầu tư `skippable: true`), trả lời xong quay
  lại bản xác nhận.
- Ngân sách chỉ hỏi SAU khái toán (`quick_replies` 5 mức, kèm câu "Anh/chị cho em biết ngân sách dự kiến ...").
  Phong cách / mức đầu tư không hỏi; khách tự nói thì vẫn ghi nhận (hiện trên bản xác nhận, gửi kèm yêu cầu báo giá),
  KHÔNG làm thay đổi khái toán; có mức đầu tư thì khái toán và bản xác nhận ghi "Mức đầu tư <x> chưa được tính vào
  khái toán vì Dezon chưa có đơn giá theo mức đầu tư.".

Chuỗi cố định (frontend mock dùng đúng các chuỗi này):

| Mục | Giá trị |
|---|---|
| `back_value` | `Quay lại câu trước` |
| `skip_value` | `Bỏ qua câu này` |
| Placeholder chung | `Nhập câu trả lời của anh/chị`; diện tích: `Nhập diện tích (m²)` |
| Phong cách | tiêu đề `Phong cách thiết kế mong muốn?`; Hiện đại / Tối giản / Tân cổ điển / Indochine -> `Phong cách hiện đại` / `Phong cách tối giản` / `Phong cách tân cổ điển` / `Phong cách Indochine` |
| Ngân sách | tiêu đề `Ngân sách dự kiến?`, gợi ý `Có ngân sách giúp em tư vấn sát hơn và tự gợi ý phân khúc phù hợp ạ.`; `<2 tỷ` / `2–4 tỷ` / `4–6 tỷ` / `6–10 tỷ` / `>10 tỷ` -> `Ngân sách dưới 2 tỷ` / `Ngân sách 2–4 tỷ` / `Ngân sách 4–6 tỷ` / `Ngân sách 6–10 tỷ` / `Ngân sách trên 10 tỷ`; placeholder `Nhập ngân sách (vd 3,5 tỷ)` |
| Mức đầu tư | tiêu đề `Mức đầu tư mong muốn?`; Tiết kiệm / Phổ thông / Trung cấp / Siêu cao cấp -> `Mức đầu tư tiết kiệm` / `Mức đầu tư phổ thông` / `Mức đầu tư trung cấp` / `Mức đầu tư siêu cao cấp` |
| 1/5 Loại công trình | tiêu đề `Loại công trình anh/chị dự định xây dựng?`; Nhà phố / Biệt thự / Căn hộ / Văn phòng / Khách sạn / Khác -> `Nhà phố` / `Biệt thự` / `Căn hộ` / `Văn phòng` / `Khách sạn` / `Công trình khác` |
| 2/5 Khu vực | tiêu đề `Khu vực xây dựng?`, gợi ý `Tỉnh/thành phố, kèm quận/huyện nếu có — đơn giá khác nhau theo khu vực.`, placeholder `Nhập tỉnh/thành phố, quận/huyện`; TP. Hồ Chí Minh / Hà Nội / Đà Nẵng (value = label) |
| 3/5 Diện tích đất | tiêu đề `Diện tích đất?`, gợi ý `Chọn kích thước đất ngang × dài (m), hoặc nhập tổng diện tích đất (m²).`, placeholder `Nhập ngang × dài (m) hoặc tổng m²`; `options` rỗng; `chips` `4 × 10`, `5 × 10`, `6 × 10`, `6 × 12`, `9 × 12`, `12 × 10` -> `Đất 4×10 m` ... `Đất 12×10 m` |
| 4/5 Số tầng | tiêu đề `Số tầng dự kiến?`, gợi ý `Tầng trệt tính là 1 tầng; hầm, lửng (nếu có) anh/chị bổ sung ở bản xác nhận.`, placeholder `Nhập số tầng (vd 1 trệt 3 lầu)`; `1 trệt` / `1 trệt 1 lầu` / `1 trệt 2 lầu` / `1 trệt 3 lầu` / `1 trệt 4 lầu` (value = label) |
| 5/5 Phạm vi | tiêu đề `Phạm vi thi công cần khái toán?`, gợi ý `Trọn gói gồm phần thô và hoàn thiện.`; Xây thô / Hoàn thiện / Trọn gói / Nội thất (value = label) |
| Căn hộ 3/5 | tiêu đề `Diện tích căn hộ (m²)?`, gợi ý `Diện tích sử dụng của căn hộ (theo sổ hồng hoặc hợp đồng mua bán).`, placeholder `Nhập diện tích (m²)`; `50 m²` ... `300 m²` -> `Diện tích căn hộ 50 m2` ... (50, 80, 120, 200, 300) |
| Căn hộ 4/5 | tiêu đề `Căn hộ có mấy tầng?`, gợi ý `Số tầng của chính căn hộ — căn duplex là 2 tầng.`, placeholder `Nhập số tầng`; `1 tầng` / `2 tầng (duplex)` -> `Căn hộ 1 tầng` / `Căn hộ 2 tầng (duplex)` |
| Thêm: Diện tích xây dựng | tiêu đề `Diện tích xây dựng mỗi tầng (m²)?`, gợi ý `Phần sàn xây, không tính sân.`; `Xây kín đất (<đất> m²)` -> `Diện tích xây dựng mỗi tầng <đất> m2`, `Chưa biết` -> `Tôi chưa biết diện tích xây dựng` |
| Thêm: Hầm/lửng | tiêu đề `Nhà có tầng hầm hoặc tầng lửng không?`; `Không hầm, không lửng` / `Có tầng lửng` / `Có tầng hầm` / `Có cả hầm và lửng` -> `Không hầm, không lửng` / `Có tầng lửng, không hầm` / `Có tầng hầm, không lửng` / `Có tầng hầm và tầng lửng` (đã biết một mục: `Nhà có tầng hầm không?` / `Nhà có tầng lửng không?`) |
| Thêm: Mái | tiêu đề `Loại mái dự kiến?`; `Mái bằng bê tông` / `Mái tôn` / `Mái ngói kèo thép` / `Mái ngói đổ bê tông` (value = label), `Chưa biết` -> `Tôi chưa biết loại mái` |
| Thêm: Độ sâu hầm | tiêu đề `Độ sâu tầng hầm?`; `Dưới 1,3 m` / `1,3–1,7 m` / `1,7–2 m` / `Trên 2 m` / `Chưa biết` -> `Hầm sâu 1,2 m` / `Hầm sâu 1,5 m` / `Hầm sâu 1,9 m` / `Hầm sâu 2,5 m` / `Tôi chưa biết độ sâu hầm` |
| Thêm: Diện tích lửng | tiêu đề `Diện tích tầng lửng (m²)?`; `Khoảng 1/2 sàn (<x> m²)` / `Bằng cả sàn (<y> m²)` -> `Tầng lửng <x> m2` / `Tầng lửng <y> m2`, `Chưa biết` -> `Tôi chưa biết diện tích tầng lửng` |

Slot mới trong `slots` (mặc định null): `design_style` (`hien_dai | toi_gian | tan_co_dien | indochine` hoặc chữ khách
tự nhập), `investment_level` (`tiet_kiem | pho_thong | trung_cap | sieu_cao_cap` hoặc chữ khách tự nhập).

### Bản xác nhận `summary_confirm`

Trường cũ giữ nguyên (`items`, `assumptions`, `confirm`, `edit`). Thêm (tùy chọn, mặc định null):

- `title`: `Xác nhận thông tin — <Loại công trình>` (chưa có loại: `Xác nhận thông tin`).
- `confirm_label` = `Tính ngay → xem bảng khái toán`, `confirm_value` = `Đúng rồi, tính khái toán giúp tôi` (nút đen
  lớn; tin nhắn này luôn được hiểu là xác nhận, kể cả khi thẻ câu hỏi tùy chọn đang hiển thị).
- `more_label` = `Cung cấp thêm thông tin để báo giá chính xác hơn`, `more_value` = `Tôi muốn cung cấp thêm thông tin`
  (nút viền): server hỏi lần lượt các mục chi tiết còn dùng mặc định (chưa có giá trị, kể cả mục khách đã trả lời
  "Chưa biết") — xem "Nhóm câu hỏi thêm" ở trên — rồi hiện lại bản xác nhận. Không còn mục nào (đã đủ chi tiết, hoặc
  căn hộ / phạm vi nội thất) -> cả hai là null (ẩn nút).
- `items[].edit_value`: tin nhắn gửi khi bấm "Sửa" ở dòng đó (null = dòng không sửa riêng được, vd Móng, Sân thượng);
  server hỏi lại đúng mục bằng thẻ `question_step`, trả lời xong hiện lại bản xác nhận. Giá trị: `Tôi muốn sửa loại
  công trình`, `Tôi muốn sửa khu vực`, `Tôi muốn sửa diện tích đất`, `Tôi muốn sửa số tầng`, `Tôi muốn sửa phạm vi
  công việc`, `Tôi muốn sửa diện tích xây dựng`, `Tôi muốn sửa thông tin hầm, lửng của nhà` (Tầng hầm; Tầng lửng khi
  không có lửng), `Tôi muốn sửa diện tích lửng của nhà` (Tầng lửng khi có lửng), `Tôi muốn sửa loại mái`, `Tôi muốn
  sửa phong cách thiết kế`, `Tôi muốn sửa ngân sách`, `Tôi muốn sửa mức đầu tư`.
- Dòng mới khi có giá trị: `Phong cách`, `Ngân sách`, `Mức đầu tư`. `assumptions` ("Giả định") vẫn hiển thị dưới bảng.
- Công trình xây mới (không phải căn hộ / phạm vi nội thất) luôn có 4 dòng chi tiết kèm nút Sửa: `Diện tích xây dựng
  mỗi tầng`, `Tầng hầm`, `Tầng lửng`, `Mái`; mục chưa có thông tin hiện giá trị mặc định kèm hậu tố ` (tạm tính)` (vd
  `100 m² (tạm tính)`, `Không (tạm tính)`, `Mái bê tông cốt thép (tạm tính)`).
- `assumptions`: mỗi giả định một dòng ngắn. Mục chi tiết dùng mặc định gom thành MỘT dòng, thứ tự diện tích xây dựng
  → hầm → lửng → mái: `Tạm tính: xây kín đất (100 m²/tầng), không hầm, không lửng, mái bê tông cốt thép — anh/chị bấm
  Sửa hoặc Cung cấp thêm thông tin để thay đổi.` (có hầm chưa rõ độ sâu: `hầm sâu 1,7–2 m`; có lửng chưa rõ diện tích:
  `lửng bằng diện tích sàn (<sàn> m²)`); tiếp theo là quy ước móng (`Móng tính theo thông lệ: ...`) và ghi chú mức đầu
  tư nếu có.

## Khái toán

| Method | Path | Body | Trả về |
|---|---|---|---|
| POST | `/api/estimates` | `EstimateInput & {chat_session_id?}` | `EstimateResult` (đã lưu, có `estimate_session_id`) |
| GET | `/api/estimates/{id}` | — | `EstimateResult` |
| POST | `/api/estimates/{id}/budget-adjustments` | `{target_budget_vnd}` | `BudgetAdjustmentResult` |
| GET | `/api/rates?building_type=&province=&work_scope=&quality_tier=` | — | `{rate: EvidenceValue, rate_data: RateData \| null}` |

## Nhà thầu

| Method | Path | Body | Trả về |
|---|---|---|---|
| GET | `/api/contractors?province=&building_type=&q=&limit=` | — | `{items: ContractorCard[], total}` |
| GET | `/api/contractors/{slug}` | — | `ContractorProfile` |
| POST | `/api/contractors/match` | `MatchCriteria & {chat_session_id?}` | `MatchResult` (ghi `recommendation_logs`) |
| POST | `/api/contractors/compare` | `{contractor_ids: int[2..5], estimate_session_id?, budget_vnd?}` | `ComparisonMatrix` — hàng "Mức phù hợp" chấm cùng tiêu chí với thẻ `contractor_list`: tỉnh/quận/loại/hạng mục/số tầng/ngân sách lấy từ slot phiên chat gắn với khái toán (`budget_vnd` trong body ưu tiên hơn) |
| POST | `/api/recommendations/{log_id}/click` | `{contractor_id}` | `{ok: true}` |

**Ảnh (slideshow)** — `ImageRef = {url, caption?, project_slug?, project_name?, project_url?}`; `project_name` là tên
công trình chứa ảnh, `project_url` là trang công trình trên dezon.vn (hai trường mới, tùy chọn, mặc định `null`).
`caption` rỗng thì backend điền tên công trình.

- `ContractorCard.cover_images`: tối đa 80 ảnh (đủ toàn bộ ảnh thật hiện có), xoay vòng giữa các dự án của nhà thầu (ảnh 1 của mỗi dự án theo id
  dự án, rồi ảnh 2, …; trong một dự án theo `sort_order`) — thứ tự cố định. Frontend hiển thị vài ô đầu, bấm mở
  slideshow toàn bộ danh sách.
- `ContractorProfile.projects[].images`: ảnh của từng dự án theo `sort_order`, tối đa 20 ảnh/dự án;
  `ContractorProfile.images` = nối các danh sách đó; `total_images` = tổng số ảnh dự án thực có (không bị cắt, có thể
  lớn hơn `images.length`).
- Ảnh không được gửi cho LLM (`compact_for_llm` bỏ `cover_images`/`images`/`total_images`).

## Yêu cầu báo giá

| Method | Path | Body | Trả về |
|---|---|---|---|
| POST | `/api/quote-requests` | `{contractor_ids: int[1..5], estimate_session_id?, customer_name, customer_phone, customer_email?, note?, chat_session_id?}` | `{ids: uuid[], code: string}` |

Ghi chú gửi nhà thầu (`quote_requests.note`) = ghi chú của khách + mong muốn đã ghi nhận trong hội thoại, mỗi dòng
một mục: `Phong cách thiết kế: <nhãn>`, `Mức đầu tư mong muốn: <nhãn>`. Phiên chat lấy theo `chat_session_id` (tùy
chọn) hoặc phiên chat gắn với `estimate_session_id`.

## Admin (header `X-Admin-Token: $ADMIN_TOKEN`)

Thiếu/sai token → 401. `APP_ENV=production` mà `ADMIN_TOKEN` còn mặc định → 503 (trang quản trị bị khóa).
Lỗi trả `{detail: string}` tiếng Việt. Trang HTML: `GET /admin` (không cần token để tải trang; mọi API cần token).

| Method | Path | Body / tham số | Trả về |
|---|---|---|---|
| GET | `/api/admin/rates?building_type=&province=&work_scope=&quality_tier=&limit=&offset=` | — | `{items: RateOut[], total}` |
| GET | `/api/admin/rates/{id}` | — | `RateOut` |
| POST | `/api/admin/rates` | `{building_type, province ('*' = toàn quốc), work_scope, quality_tier?, price_min, price_max, effective_from, effective_to?}` | `RateOut` (201) |
| PUT | `/api/admin/rates/{id}` | `{province?, quality_tier?, price_min?, price_max?, effective_from?, effective_to?, clear_effective_to?, source_id?}` | `RateOut` |
| DELETE | `/api/admin/rates/{id}` | — | `{ok: true}` (409 nếu đơn giá đang được khái toán tham chiếu) |
| POST | `/api/admin/rates/{id}/approve` | `{verified_by?}` | Đánh dấu đơn giá là dữ liệu Dezon đã xác minh |
| GET | `/api/admin/rules?code=&approved=&limit=&offset=` | — | `{items: RuleOut[], total}` |
| POST | `/api/admin/rules` | `{code, coefficient, group_name?, label_vi?, base?, is_default?, approved?}` | `RuleOut` (201) — tạo phiên bản mới |
| PUT | `/api/admin/rules/{id}` | `{label_vi?, is_default?, approved?, coefficient?, group_name?, base?}` | `RuleOut` |
| POST | `/api/admin/rules/{id}/approve` | — | `RuleOut` (bật `approved`) |
| DELETE | `/api/admin/rules/{id}` | — | `{ok: true}` |
| GET | `/api/admin/weights` | — | `{items: WeightsOut[]}` |
| POST | `/api/admin/weights` | `{weights: {location, building_type, budget_fit, experience, similar_projects, profile_quality}, note?, activate?}` | `WeightsOut` (201) — tổng trọng số = 1 |
| PUT | `/api/admin/weights/{version}` | `{is_active?, note?}` | `WeightsOut` |
| POST | `/api/admin/weights/{version}/activate` | — | `WeightsOut` (chỉ một phiên bản hoạt động) |
| DELETE | `/api/admin/weights/{version}` | — | `{ok: true}` |
| GET | `/api/admin/sources?status=&source_type=&limit=&offset=` | — | `{items: SourceOut[], total}` |
| POST | `/api/admin/sources/{id}/verify` | `{verified_by?}` | `SourceOut` (status `verified`) |
| GET | `/api/admin/guardrail-violations?limit=&offset=&rule=` | — | `{items, total}` |
| GET | `/api/admin/metrics` | — | `MetricsReport` (mục 12) |
| POST | `/api/admin/import/{rates\|rules}` | multipart `file` (.csv/.xlsx) hoặc thân thô + `?filename=`, hoặc JSON `{path}` (file trong `IMPORT_DIR`); tham số `dry_run`, `force` | `ImportReport` `{kind, filename, total_rows, inserted, updated, unchanged, skipped, error_count, errors[{row, field, message}], source_id, status: ok\|partial\|failed\|unchanged, dry_run}`. `dry_run=true`: không ghi DB nhưng báo đúng số dòng **sẽ** thêm/cập nhật/giữ nguyên/bị chặn (`skipped` = bị nguồn ưu tiên cao hơn chặn). File Excel hỏng → `status: failed` |
| POST | `/api/admin/sync/csv` | như `import`, thêm `kind=rates\|rules` | `{status: "done", source: "csv", report: ImportReport}` |
| POST | `/api/admin/sync/{api\|crawl\|reindex\|after_sync}` | `?max_pages=1..1000` (api, crawl) | `{status: "queued", source, job, job_id, params, warning?}`; Redis chưa sẵn sàng → 503 |
| GET | `/api/admin/sync/jobs/{job_id}` | — | `{job_id, job, status: queued\|started\|finished\|failed\|…, status_vi, enqueued_at, started_at, ended_at, result, error}` — `result` là thống kê job (vd `{reindexed_chunks}`, `{profile_completeness, reindexed_chunks}`, `{status: "skipped", reason}`); `error` là dòng cuối traceback. Kết quả giữ 7 ngày; không có/hết hạn → 404; Redis lỗi → 503 |

`after_sync` = tính lại `profile_completeness` + re-embed toàn bộ; `reindex` = chỉ re-embed.

## Lỗi chung (mọi endpoint)

- 422 `{detail: "Dữ liệu không hợp lệ — <trường>: <lý do>", errors?: [...]}` — `detail` luôn là chuỗi tiếng Việt,
  không lặp lại dữ liệu khách gửi (kể cả router chat).
- Path, query hoặc body JSON chứa ký tự điều khiển (byte NUL `\u0000`, C0 trừ tab/xuống dòng, DEL) → 422
  "nội dung chứa ký tự điều khiển không được hỗ trợ", chặn trước khi chạm DB.

## Nhúng widget

```html
<script src="https://<host>/dezon-widget.js" data-api-base="https://<api-host>" defer></script>
```

Widget tự gắn nút chat nổi góc phải dưới; mở ra khung chat (toàn màn hình khi viewport < 640 px,
hỗ trợ tối thiểu 360 px). CSS được cô lập (Shadow DOM) để không ảnh hưởng trang dezon.vn.

### Giao diện mở rộng (trang đầy đủ)

```html
<div id="dezon-planning" style="height: 100vh"></div>
<script src="https://<host>/dezon-widget.js" data-mode="page" data-api-base="https://<api-host>"
        data-login-url="https://dezon.vn/<trang-đăng-nhập>/" defer></script>
```

Dùng cùng các endpoint chat ở trên; "Recent chats" là danh sách `session_id` lưu ở localStorage của trình duyệt,
mở lại bằng `GET /api/chat/sessions/{id}` (404 → tự xóa khỏi danh sách).
