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
contractor_list | comparison_table | contractor_detail | source_badges | quote_request_form | suggestions` — xem
`app/schemas/ui_blocks.py`.

### Nguồn của lượt `source_badges` (V2 — sources)

`{type: "source_badges", badges: [{label, status, source: SourceRef}], items: [EvidenceItem]}`.

- `badges` (không đổi): nguồn đã khử trùng lặp theo (label, url, ngày) của mọi EvidenceValue có giá trị trong kết quả
  tool của lượt (trừ `search_knowledge`, trừ nguồn "Ngân sách do anh/chị cung cấp", bỏ nguồn không có url lẫn ngày,
  tối đa 8). Frontend KHÔNG hiển thị chip này.
- `items` (V2, mặc định `[]`; block cũ đã lưu không có trường này):
  `EvidenceItem = {group: string|null, label: string, evidence: EvidenceValue}` — giá trị đã dùng trong lượt nhưng
  KHÔNG nằm trong bảng/thẻ nào: `get_reference_rates` → 1 mục "Đơn giá tham chiếu (VND/m²)" (group "Đơn giá tham
  chiếu — <Loại>, <phạm vi>, <tỉnh>"); `get_contractor_overview` → 8 số tổng hợp (Số nhà thầu phù hợp, Nhà thầu có công
  trình <loại> thực tế, Công trình <loại> có trang dự án, Công trình ghi nhận trên Dezon (tổng các nhà thầu), Khoảng
  đơn giá xây thô, Khoảng đơn giá trọn gói (hoàn thiện cơ bản), Nhà thầu có thông tin bảo hành, Hồ sơ đã xác minh),
  group "Tổng quan nhà thầu — <Loại> tại <tỉnh>". Mục unavailable vẫn được gửi (`display` = "Chưa có dữ liệu").
- Block được phát khi có `badges` HOẶC `items` (trước V2: chỉ khi có `badges`), nên `badges` có thể là `[]`.
  Ngoại lệ: khi lượt không có `badges` và không có block dữ liệu nào (bảng/thẻ/form), `items` mà mọi mục đều
  unavailable thì KHÔNG tạo block (vd đơn giá tham chiếu không có + câu hỏi bằng `quick_replies` → blocks chỉ là
  `[quick_replies]`, như trước V2). Có `badges` hoặc có block dữ liệu thì mục unavailable vẫn được gửi.
- Frontend gom `items` cùng mọi EvidenceValue trong các block khác của tin nhắn
  (`frontend/src/utils/answerSources.ts`) để hiện link "Nguồn dữ liệu (N)" (N = số nguồn) cuối tin nhắn trợ lý và hộp
  thoại liệt kê theo nguồn. Không có endpoint mới.

### Gợi ý câu hỏi tiếp theo `suggestions`

`{type: "suggestions", title: "Gợi ý cho anh/chị", options: [{label, value}]}` — tối đa 5 câu hỏi theo ngữ cảnh hội
thoại, luôn là block CUỐI của tin nhắn. Chỉ có khi luồng chính đã xong (đã xác nhận, có khái toán và nhà thầu đề xuất — kể cả
lượt ngay sau "Tính ngay" đang hỏi ngân sách, khi đó gợi ý nằm sau nút ngân sách) và lượt đó không chờ khách trả lời
câu khác (thẻ câu hỏi, bản xác nhận, form báo giá). Bấm = gửi `value` như
tin nhắn mới. Frontend chỉ hiện ở tin nhắn mới nhất (như `quick_replies`).

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
| GET | `/api/estimates/{id}/pdf?contractor_ids=&contractor_ids=` | — | `application/pdf` (V2 — PDF, xem dưới) |
| GET | `/api/rates?building_type=&province=&work_scope=&quality_tier=` | — | `{rate: EvidenceValue, rate_data: RateData \| null}` |

### Xuất PDF `GET /api/estimates/{id}/pdf` (V2 — PDF)

- Query `contractor_ids` lặp lại 0 lần (chỉ khái toán) hoặc 2–5 lần (khái toán + bảng so sánh), mỗi id 1..2⁶³−1,
  không trùng. Ví dụ `/api/estimates/<uuid>/pdf?contractor_ids=3&contractor_ids=7`.
- 200: thân là file PDF A4 (`Content-Type: application/pdf`, `Content-Disposition: attachment;
  filename="khai-toan-vuathau-<yyyymmdd>-<8 ký tự đầu id>[-so-sanh].pdf"`, `Cache-Control: no-store`). Trang so sánh
  nằm ngang khi có hơn 3 nhà thầu. Ghi sự kiện `estimate_pdf_exported {estimate_session_id, contractor_count}`.
- Nội dung: đầu trang có logo, "Vua thầu khái toán", pro.vuathau.com.vn, "Ngày xuất dd/mm/yyyy HH:mm" (giờ Việt Nam)
  và mã khái toán. Phần khái toán lấy nguyên snapshot đã hiển thị (`GET /api/estimates/{id}`): thông tin công trình,
  diện tích quy đổi, đơn giá + nguồn, chi phí, bảng hệ số (luôn mở), lưu ý/giả định/chưa bao gồm/yếu tố tăng chi phí
  và ghi chú miễn trừ. Bảng so sánh được tính lại lúc xuất (như `POST /api/contractors/compare` với
  `estimate_session_id` = id), có dòng "Dữ liệu tại thời điểm xuất …". Mục "Nguồn dữ liệu" liệt kê các nguồn khác
  nhau (nhãn, link, ngày cập nhật, cờ dữ liệu cũ). File ẩn danh: không có tên/SĐT khách. Ô thiếu = "Chưa có dữ liệu".
- Lỗi: 404 `Không tìm thấy phiên khái toán.` (id không tồn tại/không phải UUID); 422 `Cần 0 hoặc từ 2 đến 5 mã nhà
  thầu hợp lệ…` (1 id, > 5 id, id ngoài khoảng) hoặc thông báo của compare (trùng, không tìm thấy nhà thầu); 429
  (quá `PDF_RATE_LIMIT_PER_MIN`, mặc định 20 lượt/phút/IP, kèm `Retry-After`); 503 chưa có bộ trọng số matching;
  500 `Em chưa tạo được file PDF cho bảng khái toán này…` (ReportLab không dàn trang được).
- Dạng lỗi theo header `Accept`: client API (không có `text/html`) nhận JSON `{detail}` như mọi endpoint; trình duyệt
  điều hướng tới link (Accept có `text/html`) nhận trang lỗi HTML tiếng Việt (`text/html; charset=utf-8`,
  `Content-Disposition: inline`, `Cache-Control: no-store`, CSP `default-src 'none'`) cùng mã trạng thái, hiện
  thông báo trên + link "Thử lại" (429/500/503) và "Về Vua thầu khái toán". Áp dụng cho cả lỗi validation 422 và lỗi
  500 không lường trước.
- Ô bảng so sánh dài quá 300 ký tự (vd `warranty_text` thô) và tên nhà thầu quá 120 ký tự được rút gọn + "…" trong
  PDF (một dòng bảng không tách qua trang), kèm chú thích "nội dung đầy đủ xem trên hồ sơ nhà thầu tại Dezon".
- `ComparisonTableBlock.estimate_session_id` (UUID, tùy chọn, mặc định `null`): phiên khái toán gắn với bảng so sánh
  từ chat (tool `compare_contractors`) — frontend hiện nút "Tải PDF (khái toán + so sánh)" khi có. Bảng tổng quan
  (`get_contractor_overview`) không có trường này.
- Frontend: nút là thẻ `<a href target="_blank">` — không có `download`, để trang lỗi HTML hiện được ("Tải PDF khái toán" cuối `estimate_table`, "Tải PDF (khái
  toán + so sánh)" cuối bảng so sánh 2–5 nhà thầu không phải bảng tổng quan); ẩn ở chế độ mock và khi không có
  `estimate_session_id`.

## Nhà thầu

| Method | Path | Body | Trả về |
|---|---|---|---|
| GET | `/api/contractors?province=&building_type=&q=&limit=` | — | `{items: ContractorCard[], total}` |
| GET | `/api/contractors/{slug}` | — | `ContractorProfile` |
| POST | `/api/contractors/match` | `MatchCriteria & {chat_session_id?}` | `MatchResult` (ghi `recommendation_logs`) |
| POST | `/api/contractors/compare` | `{contractor_ids: int[2..5], estimate_session_id?, budget_vnd?}` | `ComparisonMatrix` — hàng "Mức phù hợp" chấm cùng tiêu chí với thẻ `contractor_list`: tỉnh/quận/loại/hạng mục/số tầng/ngân sách lấy từ slot phiên chat gắn với khái toán (`budget_vnd` trong body ưu tiên hơn) |
| POST | `/api/recommendations/{log_id}/click` | `{contractor_id}` | `{ok: true}` |

**Ảnh (slideshow)** — `ImageRef = {url, caption?, project_slug?, project_name?, project_url?, thumb_url?}`;
`project_name` là tên công trình chứa ảnh, `project_url` là trang công trình trên dezon.vn (tùy chọn, mặc định `null`).
`caption` rỗng thì backend điền tên công trình.
`thumb_url` (V2 — gallery, tùy chọn, mặc định `null`): ảnh thu nhỏ của dezon.vn cho ô ảnh. Nguồn: cột
`project_images.thumb_url` (crawler đọc `data-thumb` của `a.dznt-shot`; migration 0004), thiếu thì backend suy từ `url`
dạng `https://dezon.vn/wp-content/uploads/dz-data/nt/<slug>/o/<id>.<jpg|jpeg|png|webp>` bằng cách bỏ đoạn `/o/`
(đúng mẫu đó, không khớp -> `null`). Frontend: ô ảnh dùng `thumb_url ?? url`, lỗi tải thì lùi về `url`; lightbox luôn
dùng `url` (ảnh gốc).

- `ContractorCard.cover_images`: tối đa 80 ảnh (đủ toàn bộ ảnh thật hiện có), xoay vòng giữa các dự án của nhà thầu (ảnh 1 của mỗi dự án theo id
  dự án, rồi ảnh 2, …; trong một dự án theo `sort_order`) — thứ tự cố định. Frontend (V2) hiển thị 3 ô là ảnh đầu của
  3 công trình khác nhau, sắp lại slideshow theo công trình (hết ảnh công trình A rồi sang B, nhóm theo `project_slug`)
  và nhóm theo `project_slug` ở hộp thoại "Xem tất cả ảnh".
- `ContractorProfile.projects[].images`: ảnh của từng dự án theo `sort_order`, tối đa 20 ảnh/dự án;
  `ProjectSummary.total_images` (V2, mặc định `0`) = số ảnh thực có của công trình (không bị cắt; `0` ở payload cũ ->
  frontend dùng `images.length`); `ContractorProfile.images` = nối các danh sách đó; `ContractorProfile.total_images` =
  tổng số ảnh dự án thực có (không bị cắt, có thể lớn hơn `images.length`).
- Không có endpoint mới: hộp thoại "Xem tất cả ảnh" của frontend dùng dữ liệu sẵn có (`ContractorProfile.projects` ở hồ
  sơ mở rộng, `cover_images` ở thẻ nhà thầu).
- Ảnh không được gửi cho LLM (`compact_for_llm` bỏ `cover_images`/`images`/`total_images`).

## Yêu cầu báo giá

| Method | Path | Body | Trả về |
|---|---|---|---|
| POST | `/api/quote-requests` | `{contractor_ids: int[1..5], estimate_session_id?, customer_name, customer_phone, customer_email?, note?, chat_session_id?}` | `{ids: uuid[], code: string}` |

Ghi chú gửi nhà thầu (`quote_requests.note`) = ghi chú của khách + mong muốn đã ghi nhận trong hội thoại, mỗi dòng
một mục: `Phong cách thiết kế: <nhãn>`, `Mức đầu tư mong muốn: <nhãn>`. Phiên chat lấy theo `chat_session_id` (tùy
chọn) hoặc phiên chat gắn với `estimate_session_id`.

## Yêu cầu liên hệ nhân viên

Nút "Liên hệ nhân viên" (ô chat, thẻ nhà thầu, hồ sơ nhà thầu): khách để lại họ tên + số điện thoại để nhân viên Dezon
gọi lại. Không gửi cho nhà thầu, không cần khái toán. Công khai (không cần token).

| Method | Path | Body | Trả về |
|---|---|---|---|
| POST | `/api/contact-requests` | `{customer_name, customer_phone, note?, chat_session_id?, contractor_id?, source?, page_url?}` | `{id: uuid, code: string}` (201) |

- `customer_name`: bắt buộc, 1..200 ký tự sau khi bỏ khoảng trắng hai đầu. Rỗng → 422 "Vui lòng nhập họ tên người
  liên hệ."
- `customer_phone`: số Việt Nam, chuẩn hóa như yêu cầu báo giá (`+84 912.345.678` → `0912345678`); sai → 422
  "Số điện thoại không hợp lệ (cần dạng 0xxxxxxxxx)".
- `note` ≤ 1000 ký tự; `page_url` ≤ 500 ký tự (địa chỉ trang khách đang xem); chuỗi rỗng/chỉ khoảng trắng → `null`.
- `source`: `"composer"` (ô chat) | `"contractor_card"` (thẻ nhà thầu) | `"contractor_detail"` (hồ sơ nhà thầu) |
  `null`; giá trị khác → 422 "Nguồn yêu cầu liên hệ không hợp lệ."
- `contractor_id`: 1..2^63−1 (ngoài khoảng → 422 "Mã nhà thầu không hợp lệ."). `chat_session_id` / `contractor_id`
  không tồn tại → bỏ qua (lưu `null`), không lỗi.
- `code`: `LH-` + 6 ký tự chữ in hoa/số, duy nhất — hiển thị cho khách để đối chiếu khi nhân viên gọi.
- Chống gửi trùng: cùng SĐT (sau chuẩn hóa) + cùng `chat_session_id` + cùng `contractor_id` (kể cả cùng `null`) trong
  10 phút → trả lại `{id, code}` của yêu cầu cũ (vẫn 201), không tạo bản ghi mới.
- Ghi `analytics_events` `contact_requested` `{code, source, has_contractor}` (chỉ khi tạo mới).

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
| GET | `/api/admin/contact-requests?status=new\|contacted\|done&limit=1..500 (50)&offset=` | — | `{items: ContactRequestOut[], total, new_count}` — mới nhất trước; `total` theo bộ lọc; `new_count` = số yêu cầu trạng thái `new` (không phụ thuộc bộ lọc) |
| PATCH | `/api/admin/contact-requests/{id}` | `{status?: new\|contacted\|done, handled_note?: string\|null (≤ 1000)}` | `ContactRequestOut`; trường không gửi giữ nguyên, `handled_note: null`/rỗng → xóa; không có → 404 |
| GET | `/api/admin/chat-sessions/{session_id}` | — | `SessionStateOut` (như `GET /api/chat/sessions/{id}`): toàn bộ tin `user`/`assistant` của phiên theo thứ tự (bỏ tin `tool`), kèm `ui_blocks`, `created_at`; không có → 404, mã sai dạng → 422 |
| GET | `/api/admin/quote-requests?status=new\|sent\|quoted\|won\|lost&code=&limit=1..500 (50)&offset=` | — | `{items: QuoteRequestOut[], total, new_count}` (V2 — quotes) — mới nhất trước; `code` = mã khách nhận `DZ-XXXXXX` (hoặc 6 ký tự hex, không phân biệt hoa thường) → mọi yêu cầu của lượt gửi đó; mã sai dạng/không có → rỗng |
| GET | `/api/admin/quote-requests/{quote_request_id}` | — | `QuoteRequestDetail` `{request: QuoteRequestOut, estimate: EstimateSummary \| null, quotes: ContractorQuoteOut[], line_template: QuoteLine[]}`; không có → 404, mã sai dạng → 422 |
| POST | `/api/admin/quote-requests/{quote_request_id}/quotes` | `ContractorQuoteIn` | `ContractorQuoteOut` (201). Nhà thầu = nhà thầu của yêu cầu. Yêu cầu `new`/`sent` → `quoted` (`won`/`lost` giữ nguyên). Ghi `analytics_events` `contractor_quote_recorded` `{quote_request_id, contractor_quote_id, verified, lines}` |
| POST | `/api/admin/quote-requests/{quote_request_id}/analysis-preview` | `ContractorQuoteIn` | `QuoteAnalysisResult` — xem trước, không lưu; yêu cầu không gắn khái toán → 409 |
| PUT | `/api/admin/contractor-quotes/{id}` | `ContractorQuoteIn` (thay toàn bộ; không gửi `received_at` → giữ ngày nhận cũ) | `ContractorQuoteOut`; không có → 404 |
| DELETE | `/api/admin/contractor-quotes/{id}` | — | `{ok: true}`; yêu cầu đang `quoted` mà không còn báo giá nào → về `new` |
| GET | `/api/admin/contractor-quotes/{id}/analysis` | — | `QuoteAnalysisResult`; yêu cầu không gắn khái toán (hoặc khái toán đã bị xóa) → 409 "Yêu cầu báo giá này chưa gắn khái toán nên chưa phân tích được." |
| POST | `/api/admin/import/{rates\|rules}` | multipart `file` (.csv/.xlsx) hoặc thân thô + `?filename=`, hoặc JSON `{path}` (file trong `IMPORT_DIR`); tham số `dry_run`, `force` | `ImportReport` `{kind, filename, total_rows, inserted, updated, unchanged, skipped, error_count, errors[{row, field, message}], source_id, status: ok\|partial\|failed\|unchanged, dry_run}`. `dry_run=true`: không ghi DB nhưng báo đúng số dòng **sẽ** thêm/cập nhật/giữ nguyên/bị chặn (`skipped` = bị nguồn ưu tiên cao hơn chặn). File Excel hỏng → `status: failed` |
| POST | `/api/admin/sync/csv` | như `import`, thêm `kind=rates\|rules` | `{status: "done", source: "csv", report: ImportReport}` |
| POST | `/api/admin/sync/{api\|crawl\|reindex\|after_sync}` | `?max_pages=1..1000` (api, crawl) | `{status: "queued", source, job, job_id, params, warning?}`; Redis chưa sẵn sàng → 503 |
| GET | `/api/admin/sync/jobs/{job_id}` | — | `{job_id, job, status: queued\|started\|finished\|failed\|…, status_vi, enqueued_at, started_at, ended_at, result, error}` — `result` là thống kê job (vd `{reindexed_chunks}`, `{profile_completeness, reindexed_chunks}`, `{status: "skipped", reason}`); `error` là dòng cuối traceback. Kết quả giữ 7 ngày; không có/hết hạn → 404; Redis lỗi → 503 |

`after_sync` = tính lại `profile_completeness` + re-embed toàn bộ; `reindex` = chỉ re-embed.

`ContactRequestOut` = `{id, code, customer_name, customer_phone, note, source, page_url, status, handled_note,
created_at, contractor: {id, name} | null, project: string | null, chat_session_id: UUID | null}`; `project` = tóm tắt công trình khách đã nêu trong
phiên chat, vd `"Nhà phố · TP. Hồ Chí Minh · 100 m² · 4 tầng · Xây thô · Ngân sách 2–4 tỷ"` (chỉ gồm mục đã có;
không có phiên/chưa có thông tin → `null`). Trang `/admin` có tab "Yêu cầu liên hệ" (ngay sau "Chỉ số", huy hiệu số
yêu cầu mới) để lọc theo trạng thái, gọi khách (link `tel:`), xem lại hội thoại của khách (nút "Xem hội thoại" khi có `chat_session_id`) và cập nhật trạng thái + ghi chú xử lý.

### Admin — yêu cầu báo giá & phân tích báo giá (V2 — quotes)

Chỉ cho nhân viên (cần `X-Admin-Token`); **không có endpoint, trang hay block nào cho khách** ở vòng này. Nhân viên
nhập báo giá nhà thầu gửi về (Zalo, PDF, Excel — chỉ lưu link file gốc, không tải file lên) ở tab "Yêu cầu báo giá"
của `/admin` (ngay sau "Yêu cầu liên hệ") và xem phân tích so với khái toán.

- `QuoteRequestOut` = `{id, code, created_at, status, customer_name, customer_phone, customer_email, note,
  contractor: {id, name} | null, estimate_session_id, estimate_cost: string | null, project: string | null,
  chat_session_id: UUID | null, quote_count}`. `code` = mã `DZ-XXXXXX` khách nhận (chung cho các nhà thầu gửi cùng
  lượt). `estimate_cost` = `"1,740–2,058 tỷ"` / `"Chưa có dữ liệu"`; `null` = không gắn khái toán. `project` = tóm tắt
  công trình từ phiên chat của khái toán, không có thì từ input khái toán (vd `"Nhà phố · TP. Hồ Chí Minh · 100 m² đất ·
  4 tầng · Xây thô"`). `chat_session_id` = phiên chat của khái toán (nút "Xem hội thoại").
- `EstimateSummary` = `{estimate_session_id, project, cost, rate, converted_area (EvidenceValue), items: EstimateItem[],
  exclusions, warnings, disclaimer}` — snapshot khái toán đã hiển thị cho khách (bản ghi cũ không có snapshot được tính
  lại và có cảnh báo).
- `QuoteLine` = `{group: mong|tang|ham|mai|san_thuong|ban_cong|san|khac (mặc định khac), label (≤ 200), rule_code?,
  area_m2?, coefficient? (0..10), converted_m2? (bỏ trống = area_m2 × coefficient), amount_vnd? (VND)}`.
  `line_template` = các dòng điền sẵn từ hạng mục khái toán (nhóm, diễn giải, mã hệ số, diện tích, hệ số).
- `ContractorQuoteIn` = `{total_amount? (VND, > 0), price_per_m2? (VND/m², > 0), converted_area_m2? (diện tích quy đổi
  theo nhà thầu), scope_text? (≤ 4000), file_url? (http/https, ≤ 1000), received_at? (YYYY-MM-DD, mặc định hôm nay),
  verified (mặc định false), entered_by? (≤ 200), note? (≤ 2000), lines: QuoteLine[] (≤ 100)}`. Bắt buộc có
  `total_amount` hoặc `price_per_m2` → 422 "Cần nhập tổng tiền hoặc đơn giá/m² của báo giá.". Chuỗi rỗng → `null`.
  Dòng không có số nào của nhà thầu (diện tích, hệ số, DT quy đổi, thành tiền) bị bỏ.
- `ContractorQuoteOut` = `{id, quote_request_id, contractor: {id, name} | null, total_amount, price_per_m2,
  converted_area_m2, scope_text, file_url, received_at, received_date (ngày theo giờ Việt Nam), verified,
  status: verified|reference, entered_by, note, lines: QuoteLine[]}`. `verified=true` (nhân viên đã đối chiếu file gốc)
  → "Đã xác minh", ngược lại "Dữ liệu tham khảo".
- `QuoteAnalysisResult` = `{estimate_session_id, quote_id (null khi xem trước), contractor_name, quote_status,
  quote_source: SourceRef ("Báo giá của <nhà thầu> nhận ngày dd/mm/yyyy", url = link file gốc nếu http/https),
  total, price_per_m2, area: QuoteComparison, breakdown: GapBreakdown | null, lines: QuoteLineAnalysis[],
  lines_compared, scope_text, exclusions, notable, summary: string[], disclaimer, warnings: string[]}`.
  - `QuoteComparison` = `{label_vi, estimate, quote, position: below|within|above|not_comparable, gap_pct, outside_pct,
    notable, note_vi}`. `gap_pct` = (báo giá − điểm giữa khoảng khái toán) / điểm giữa (`"+13,4%"`), luôn có khi hai
    bên đủ số; `outside_pct` = phần vượt ra ngoài khoảng ((báo giá − cận trên)/cận trên hoặc (cận dưới − báo giá)/cận
    dưới), trong khoảng → "Chưa có dữ liệu". `notable` = ngoài khoảng [min, max]. Ví dụ `note_vi`: "Cao hơn khoảng khái
    toán 6,3% (so với mức cao của khoảng); lệch +13,4% so với điểm giữa khoảng khái toán."
  - Đơn giá/m² của báo giá thiếu → suy ra tổng tiền / diện tích quy đổi của nhà thầu; diện tích thiếu → cộng từ các
    dòng hạng mục hoặc tổng tiền / đơn giá (mỗi lần suy ra có cảnh báo). Chỉ cộng từ các dòng khi các dòng phủ đủ mọi
    hạng mục của khái toán (không nhóm nào "Báo giá chưa có", không nhóm nào thiếu hạng mục) và mọi dòng thuộc nhóm của
    khái toán có diện tích quy đổi; ngược lại diện tích (và đơn giá suy ra) là "Chưa so được", có cảnh báo "Dòng hạng
    mục chưa phủ đủ mọi hạng mục của khái toán…".
  - `GapBreakdown` = `{total_gap, area_effect, price_effect, other_effect, note_vi}`: ΔC = báo giá − điểm giữa =
    (A_q − A_e)·P_mid + A_q·(P_q − P_mid) + phần còn lại (VND, làm tròn triệu, `"+135 triệu"`). Chỉ có khi đủ tổng tiền,
    chi phí khái toán, đơn giá và diện tích của báo giá.
  - `QuoteLineAnalysis` = `{group, label_vi, estimate_area, quote_area, estimate_cost, quote_amount, position:
    below|within|above|missing_in_quote|extra_in_quote|not_comparable, gap_pct, notable, notes: string[],
    missing_items: string[] (mặc định [])}` — gộp theo
    nhóm hạng mục; chi phí nhóm = Σ diện tích quy đổi × [P_min, P_max] (làm tròn triệu). Có thành tiền → so tiền; chỉ
    có diện tích/hệ số → so diện tích quy đổi; khác hệ số → ghi chú "nhà thầu tính hệ số 0,5, khái toán tính 0,4".
    Báo giá không có dòng nào → mọi nhóm `not_comparable`. Dòng ghép với hạng mục khái toán theo `rule_code` + diễn
    giải (rồi chỉ `rule_code`); khi mọi dòng của nhóm đều ghép được mà còn hạng mục chưa có dòng → `missing_items` =
    nhãn các hạng mục đó, `estimate_area`/`estimate_cost` và kết quả so chỉ tính phần của các hạng mục đã có dòng (ghi
    chú "Báo giá chỉ có dòng cho 1/4 hạng mục của nhóm…"). Dòng không có `rule_code` (nhân viên tự thêm) coi như phủ
    cả nhóm. Dòng `extra_in_quote` khớp từ khóa của một câu "chưa bao gồm" → ghi chú "Khái toán không có hạng mục này;
    có thể thuộc phần khái toán chưa bao gồm: <câu>."
  - Trạng thái kết luận chênh lệch = trạng thái yếu nhất (khái toán, báo giá). Thiếu số → `EvidenceValue.unavailable()`.
  - Câu chữ trung tính, không dùng "đắt/rẻ/tốt nhất", không xếp hạng nhà thầu; `disclaimer` luôn nêu khái toán không
    phải báo giá thi công.

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
