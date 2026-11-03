# DECISIONS — Giả định & quyết định triển khai

Ghi lại các điểm đặc tả chưa rõ và phương án đã chọn (đơn giản nhất, thỏa Nguyên tắc mục 2).
Mỗi module bổ sung mục riêng khi có quyết định mới.

## Nền tảng

- **Thư mục gốc repo** là `Dezon-chat/` (tương đương `dezon-ai-consultant/` trong mục 3.2).
- **Backend Python**: Docker dùng Python 3.12 theo đặc tả; máy dev hiện có 3.13 — mã tương thích cả hai.
- **Migration** dùng DDL thuần (op.execute) chép từ mục 4.2 để bám sát đặc tả. Bổ sung so với DDL gốc:
  - `estimate_sessions.cost_min/cost_max` cho phép NULL — vì mục 6.3 yêu cầu trả `null` khi không có đơn giá.
  - `data_sources`: unique index trên `url` (khi không NULL) — để đồng bộ lại không tạo bản ghi trùng.
  - `documents`: unique `(entity_type, entity_id, chunk_index)` — để re-embed dạng upsert.
  - Bảng `province_neighbors` (mục 7.2) và `analytics_events` (mục 12).
  - Index `chat_messages (session_id, id)`.
- **Cổng DB dev**: compose map Postgres ra `localhost:5433` (máy dev đã có Postgres ở 5432).
- **Test DB**: mỗi phiên pytest tạo database tạm `dezon_test_<hex>`, chạy DDL + seed, xóa khi xong.

## Dữ liệu seed

- Hệ số khái toán mục 6.2 seed với `approved=false`, nguồn `dezon_editorial` → trạng thái `reference`.
- **Đơn giá mẫu là dữ liệu tham khảo cho dev**, không phải bảng giá Dezon. Nhãn nguồn ghi rõ
  "seed dev — chờ Dezon cung cấp bảng giá". Hệ số tỉnh: HCM/HN ×1,05; hệ số mức chất lượng
  tiết kiệm 0,9 · trung bình 1 · khá 1,15 · cao cấp 1,35.
- Fixture nhà thầu: Kiến Phong (giá trị quan sát 31/10/2026 trong đặc tả), Smartcons Tech, Huy Hoàng
  (thiếu kinh nghiệm/đơn giá). URL hồ sơ và công trình theo mẫu **thật** đã khảo sát trên dezon.vn
  (`/nha-thau/<slug>/`, `/nha-thau/<slug>/<công-trình>/` — xem docs/DATA_MAPPING.md); slug dự án theo quy ước crawler
  `<slug-nhà-thầu>--<công-trình>` để lần crawl đầu cập nhật đúng bản ghi thay vì tạo trùng. Ảnh dự án dev là ảnh giữ chỗ (placehold.co), caption ghi "ảnh minh họa dev".
- Tỉnh lân cận dùng tên tỉnh trước sáp nhập 2025; cần cập nhật khi Dezon chuẩn hóa danh mục tỉnh mới.

## AI

- **Nhà cung cấp LLM: OpenAI `gpt-4.1-mini`** (theo yêu cầu khách hàng 02/11/2026), embedding `text-embedding-3-small`
  (1536 chiều, khớp cột `documents.embedding vector(1536)`). Biến môi trường: `OPENAI_API_KEY`, `MODEL_NAME`,
  `EMBEDDING_MODEL` (cũng nhận tên chung `LLM_API_KEY`, `LLM_MODEL`, `EMBEDDING_API_KEY`).
- `LLM_PROVIDER=auto` (mặc định): có `OPENAI_API_KEY` -> `openai`; không có -> `mock` (provider giả lập có kịch bản,
  demo chạy được không cần key). Vẫn giữ `anthropic` làm tùy chọn qua interface `LLMProvider`.
- `EMBEDDING_PROVIDER=auto`: có key -> `openai`; không có -> `hash` (băm đặc trưng cục bộ, xác định).
  Đổi provider embedding phải chạy lại reindex (`POST /api/admin/sync/reindex`). RAG chỉ dùng cho mô tả, không cho số liệu.
- Test luôn ép `mock` + `hash` (tests/conftest.py) để không gọi API thật và không tốn phí.

## Cần hỏi lại Dezon (không tự đoán)

- Có quyền truy cập DB/API nội bộ không? Schema hiện tại?
- Bộ hệ số khái toán chính thức (thay cho seed 6.2).
- Bảng đơn giá chính thức theo khu vực/phạm vi/mức chất lượng.
- Backend ưu tiên Go hay Python?
- Nhà cung cấp LLM và model được chọn; nhà cung cấp embedding.
- Quy trình chuyển yêu cầu báo giá tới nhà thầu (email, Zalo, dashboard nhà thầu?).
- Danh mục tỉnh/thành sau sáp nhập 2025 mà Dezon dùng.

## Calculation Engine (khái toán)

- **Engine thuần** `app/engines/estimate/` (không DB/mạng/LLM). Service `app/services/estimate_service.py` nạp hệ số/đơn giá rồi gọi engine.
- **Hầm thiếu độ sâu** → tạm tính nhóm `basement_1_7_2_0` (k theo bộ hệ số) và ghi assumption. Ngưỡng: `< 1,3` · `1,3–1,7` (gồm 1,7) · `> 1,7–2,0` (gồm 2,0) · `> 2,0`.
  Khách không bật `basement` nhưng có `basement_depth_m`/`basement_area_m2` → vẫn tính hầm, ghi assumption. Tương tự lửng có `mezzanine_area_m2`.
- **Diện tích lửng / hầm mặc định** = diện tích xây dựng (theo mã giả mục 6.3), ghi assumption.
- **Sân thượng**: diện tích mặc định = diện tích xây dựng. Sân thượng nằm trên sàn mái nên **thay cho phần mái ở cùng diện tích**:
  khi khách không cho `roof_area_m2`, diện tích mái = diện tích xây dựng − diện tích sân thượng (≤ 0 → không tính mái). Tránh tính trùng mái BTCT + sân thượng trên cùng mặt bằng.
- `roof_type='none'` → không có hạng mục mái. **Sân (yard)** chỉ tính khi khách cho `yard_area_m2`; nếu diện tích xây dựng < diện tích đất thì ghi assumption "Chưa tính sân trước/sau".
- **Căn hộ (`can_ho`)**: chỉ tính sàn (các tầng của chính căn hộ + lửng), không tính móng/mái/hầm/sân thượng/sân (phần chung tòa nhà). `floors > 3` với căn hộ → warning nhắc kiểm tra (dễ nhầm với vị trí tầng).
- **Phạm vi Nội thất (`noi_that`)**: chỉ tính diện tích sàn sử dụng (tầng + lửng) với hệ số tầng, không tính móng/mái/hầm/sân thượng/sân.
- Phạm vi `xay_tho`/`hoan_thien`/`tron_goi`: dùng cùng bộ hệ số quy đổi (thông lệ thị trường tính đơn giá theo m² sàn quy đổi).
- Mô tả "chi phí bao gồm gì" (mục 6.5) đặt ở **dòng đầu của `assumptions`** (không thêm trường mới vào schema).
- **rules_status** xét các rule **thực sự dùng** trong phép tính: tất cả approved → `verified`, ngược lại `reference` + warning.
  `cost.status` = trạng thái yếu nhất giữa đơn giá và hệ số. Đơn giá fallback `'*'` luôn `reference`.
- Thiếu rule cho một hạng mục → bỏ hạng mục đó + warning; diện tích quy đổi = 0 → không khái toán chi phí (unavailable).
- Đơn giá cũ hơn `STALE_DAYS` → `is_stale=true` + warning "Đơn giá cập nhật ngày dd/mm/yyyy, có thể đã cũ".
- Disclaimer 6.6: `{nguồn, ngày cập nhật}` = `(<nhãn nguồn đơn giá>, cập nhật dd/mm/yyyy)` lấy từ `data_sources.source_updated_at` (fallback `fetched_at`); không có ngày → "ngày cập nhật: Chưa có dữ liệu"; không có đơn giá → "(Chưa có dữ liệu)".
- **Gợi ý ngân sách**: phương án `reduce_floor`, `lower_quality`, `remove_basement`, `change_roof` (loại mái có hệ số thấp hơn liền kề), `open_rooftop`, `remove_mezzanine`, `phase_rough_first`.
  Mỗi phương án dựng lại input từ **input gốc của khách** (trường khách chưa cho được engine áp mặc định lại — vd giảm 4→3 tầng thì móng mặc định chuyển cọc→băng, ghi trong `description`).
  `phase_rough_first` chỉ áp cho `tron_goi` → `xay_tho` (phạm vi `hoan_thien` là chỉ phần hoàn thiện, chuyển sang xây thô không phải "chia giai đoạn").
  Bỏ phương án không có đơn giá hoặc không tiết kiệm. Nếu không phương án đơn lẻ nào vừa ngân sách → thêm phương án `combined` (kết hợp 2 phương án tiết kiệm nhất, khác trường). Sắp xếp theo `cost_min` tăng dần.
- **Lưu trữ**: `estimate_sessions.input` lưu **input gốc** (exclude_unset) để tính lại phương án ngân sách; cột `assumptions` (JSONB) lưu `{assumptions, exclusions, cost_drivers, warnings, snapshot}` — `snapshot` là `EstimateResult` đầy đủ để `get_estimate` trả đúng con số đã hiển thị. Bản ghi không có snapshot → tính lại với hệ số hiện hành + đơn giá theo `rate_id`.
- So khớp tỉnh khi chọn đơn giá (`normalize_province`): không phân biệt hoa thường, dấu tiếng Việt, khoảng trắng/dấu chấm thừa, tiền tố "TP."/"Thành phố"/"Tỉnh" và bí danh phổ biến ("HCM", "TPHCM", "Sài Gòn", "SG", "HN"). Vd "HCM", "ho chi minh", "Thành phố Hồ Chí Minh" đều khớp đơn giá "TP. Hồ Chí Minh". Slot filling vẫn nên chuẩn hóa tên tỉnh trước.
- (Review) `select_rate` kiểm tra enum `building_type`/`work_scope`/`quality_tier` bằng Python trước khi truy vấn → giá trị sai ném `ValueError` (API trả 422), tránh lỗi enum của PostgreSQL làm hỏng transaction; `quality_tier=None` → `trung_binh`.
- (Review) Đơn giá truyền vào engine không khớp `building_type`/`work_scope`/`quality_tier` của input → warning "không khớp thông tin công trình" và trạng thái đơn giá/chi phí hạ về `reference`.
- (Review) Căn hộ không nhắc "ép cọc" trong exclusions; phạm vi Hoàn thiện không nêu yếu tố chi phí của phần thô (đào hầm, ép cọc, gia cố móng). Tỉnh trống → văn bản dùng "khu vực của anh/chị".
- (Review) `get_estimate` giữ nguyên con số trong snapshot nhưng tính lại cờ `is_stale` của đơn giá/chi phí theo ngày đọc (nguyên tắc 2.5) và thêm warning nếu đơn giá đã cũ. `suggest_budget_adjustments` với ngân sách ≤ 0 → `ValueError`.

## Matching Engine & service nhà thầu

- **Kiến trúc**: engine thuần trong `app/engines/matching/` (`criteria.py` dữ kiện + `profile_completeness`,
  `scorer.py` 6 tiêu chí + bộ lọc cứng + xếp hạng, `reasons.py` evidence/lý do/lưu ý); service
  `app/services/contractor_service.py` chỉ nạp DB và dựng model. Trọng số đọc từ `matching_weights` active
  (version lớn nhất) và truyền vào engine; nếu tổng ≠ 1 thì chuẩn hóa về 1, thiếu khóa = 0.
- **profile_completeness**: `has_warranty_info` chỉ tính khi `True` (False = không có thông tin bảo hành);
  `dezon_project_count` chỉ tính khi > 0; chuỗi rỗng coi như thiếu. Tính lại trong `seed_hook` (chỉ flush).
- **Tỷ lệ trường verified** (profile_quality): số trường trong 10 trường hồ sơ có giá trị **và** nguồn
  (`field_sources` → `data_sources.status`, fallback `source_id`) là `verified`, chia cho **10** (trường trống
  tính chưa xác minh — tránh hồ sơ 1 trường verified đạt 100%).
- **Trường quan trọng** cho nhãn "Cần đánh giá thêm": 2 nhóm — *đơn giá* (không có bất kỳ đơn giá thô/hoàn thiện
  nào) và *kinh nghiệm* (`years_experience`). Thiếu cả hai → "Cần đánh giá thêm" bất kể điểm.
- **Khu vực / bộ lọc cứng**: "hoạt động tại tỉnh" = capability **hoặc** dự án tại tỉnh **hoặc** tỉnh chính trên hồ sơ
  (`contractors.province` — là trường "Khu vực" của hồ sơ Dezon). So khớp địa danh bỏ hoa thường, khoảng trắng và
  tiền tố "TP./Thành phố/Tỉnh/Quận/Huyện/Thị xã" ("TP. Thủ Đức" ~ "Thủ Đức"). Khách không cho quận/huyện →
  khớp tỉnh tối đa 0,8 (đúng bảng 7.3). Nới lỏng sang tỉnh lân cận khi < 3 ứng viên tại tỉnh; chỉ thêm nhà thầu ở
  tỉnh lân cận (điểm location 0,4, `relaxed_location=true`, có note + caution).
- **Dự án** của nhà thầu: tính mọi vai trò trong `project_contractors` (thi_cong/thiet_ke/noi_that) cho tiêu chí
  loại công trình và dự án tương tự (MVP; có thể giới hạn `thi_cong` khi dữ liệu đủ).
- **budget_fit**:
  - Đơn giá theo phạm vi: `xay_tho` → đơn giá thô; `hoan_thien`/`tron_goi` → đơn giá hoàn thiện;
    **`noi_that` → không dùng đơn giá hồ sơ** (đơn giá thô/hoàn thiện không áp dụng cho nội thất) → trung tính 0,5,
    không ước chi phí, kèm lưu ý "cần yêu cầu báo giá riêng". Chưa rõ phạm vi → trung tính 0,5.
  - Thiếu một đầu min/max → dùng đầu còn lại cho cả hai. Chi phí ước làm tròn triệu.
  - Quy tắc 1,0: khoảng chi phí giao `[0,9B; 1,1B]` và **đầu thấp ≤ B** (tức `lo ≤ B` và `hi ≥ 0,9B`).
    Vượt (`lo > B`): `1 − (lo − B)/B / 0,4`, chặn dưới 0 (vượt ≥ 40% → 0) — tính trên đầu thấp để liên tục tại `lo = B`.
  - **Thấp hơn nhiều so với ngân sách** (`hi < 0,9B`): 0,8 cố định + lưu ý "cần đối chiếu phạm vi và vật liệu"
    (rẻ hơn không xấu nhưng có thể khác phạm vi/chất lượng).
  - Không có ngân sách hoặc không có diện tích quy đổi → 0,5 + note; vẫn trả `estimated_cost` nếu có diện tích và
    đơn giá. Không có đơn giá → 0,5 + cờ "Cần xác minh mức giá".
- **similar_projects**: khách chưa cho số tầng → bỏ điều kiện tầng; dự án **không có số tầng tính 0,5 dự án**
  (không loại hẳn vì dữ liệu crawl hay thiếu số tầng, không tính đủ vì chưa chắc tương tự).
- **Tổng điểm** làm tròn nửa lên (sau khi làm tròn 6 chữ số để tránh sai số dấu phẩy động). Xếp hạng hòa điểm:
  số công trình ghi nhận trên Dezon giảm dần, rồi tên.
- **estimated_cost**: `EvidenceValue` value `[min, max]`, status = trạng thái nguồn đơn giá nhà thầu, nhãn nguồn ghi
  rõ "Ước tính = diện tích quy đổi … × đơn giá … trên hồ sơ …", URL = nguồn đơn giá/hồ sơ.
- **Lý do**: thứ tự loại công trình → khu vực → số công trình ghi nhận → đơn giá theo phạm vi → kinh nghiệm → nhân sự,
  tối đa 5. Nhận định về loại công trình dùng "Nhận thi công …" khi chỉ có capability (không khẳng định "có kinh
  nghiệm"). Hồ sơ quá thiếu (< 2 lý do) → thêm lý do dự phòng "Hồ sơ đầy đủ N%" (evidence `profile_completeness`,
  status `reference`, tính tự động).
- **Evidence `warranty`**: chỉ có dữ liệu khi `has_warranty_info = true` (display = `warranty_text` hoặc
  "Có thông tin bảo hành"); ngược lại `unavailable`. `province` fallback danh sách tỉnh trong capability khi hồ sơ
  không ghi tỉnh chính.
- **So sánh**: hàng "Hồ sơ dự án" = số dự án có trang trên Dezon ("3 dự án có hồ sơ trên Dezon (3 có ảnh)"), 0 dự án →
  "Chưa có dữ liệu". "Mức phù hợp" chấm trực tiếp bằng scorer (không áp bộ lọc cứng), status `reference`. Có thể truyền
  `estimate_session_id` thay cho `criteria` (dựng tiêu chí từ input khái toán). Id trùng/không tồn tại → `ValueError`.
- **Tổng quan câu hỏi tự do (8.4)**: nhà thầu phù hợp = hoạt động tại tỉnh **và** có capability hoặc dự án loại đó.
  Sắp xếp: có công trình loại đó thực tế trước, rồi số công trình ghi nhận — không xếp hạng "tốt nhất". Không có nhà
  thầu → mọi số `unavailable` + note. "Hồ sơ đã xác minh" = nguồn hồ sơ chính (`source_id`) có status `verified`.
  Khoảng đơn giá tổng hợp = min các đầu thấp – max các đầu cao, status = trạng thái yếu nhất trong các nguồn.
- **record_click** chỉ nhận nhà thầu có trong `results` của log; bấm lại không ghi trùng.
- `chat_session_id` không tồn tại trong `chat_sessions` → log ghi NULL (tránh lỗi FK); `estimate_session_id`
  không tồn tại → bỏ qua + note.
- **Bổ sung sau review (Matching)**:
  - So khớp địa danh (`norm_place`) thêm: chuẩn hóa NFC, **bỏ dấu** (đ→d), bỏ dấu câu ("Bà Rịa - Vũng Tàu" ~
    "Ba Ria Vung Tau"), bí danh HCM/TPHCM/TP.HCM/Sài Gòn/SG → Hồ Chí Minh, HN → Hà Nội, "Q7"/"Q.7"/"quan 7" ~ "Quận 7".
    Tiền tố có dấu cắt trên chuỗi còn dấu; tiền tố không dấu "quan"/"q" chỉ cắt khi theo sau là số (tránh "Quan Hóa").
  - Ngân sách ≤ 0 coi như không có ngân sách (trung tính 0,5); diện tích quy đổi ≤ 0 → không ước chi phí.
    Câu "chi phí ước phù hợp ngân sách" chỉ thêm khi điểm `budget_fit` = 1,0.
  - Phạm vi `noi_that`: không nêu đơn giá thô/hoàn thiện làm lý do đề xuất (tránh mâu thuẫn với lưu ý "không áp dụng").
  - Evidence `province` fallback: tỉnh chính → tỉnh trong capability → tỉnh có dự án, để mọi nhà thầu qua bộ lọc cứng
    luôn có ≥ 2 lý do (khu vực + dự phòng). Khi tỉnh chính khác tỉnh khách nhưng có năng lực/dự án tại tỉnh khách,
    lý do ghi rõ "có ghi nhận năng lực hoặc công trình tại …". `years_experience = 0` không dùng làm lý do.
  - `search_contractors_simple`: `q` so khớp **không dấu** bằng SQL `translate()` (không cần extension `unaccent`),
    cộng ILIKE có dấu, slug và `pg_trgm similarity > 0.3`. `building_type` không thuộc enum → `ValueError`
    (tránh gửi giá trị ENUM lạ làm hỏng transaction).
  - `ContractorOverview.total_dezon_projects` là **tổng mọi loại công trình** (display ghi rõ); thêm
    `projects_of_type_count` = số trang dự án đúng loại được hỏi (không trùng giữa các nhà thầu, mọi tỉnh).

## Output guardrail validator & fallback (`app/ai/validator.py`, `app/ai/fallback.py`)

- **Chỉ trích số có đơn vị** (tiền, đơn giá, công trình/dự án, năm, nhân sự, m², nhà thầu/đơn vị, điểm, %),
  cộng "năm thành lập/từ năm YYYY" và số lớn có ≥ 2 dấu phân cách nghìn (`2.058.000.000`). Số đứng một mình
  (số thứ tự `1.`, `bước 2`, `tầng 1`, `4 tầng`, `Quận 7`) không bị trích để tránh báo nhầm. URL, email, ngày
  (`31/10/2026`, ISO), giờ được che trước khi trích. "tháng 6 năm 2027" không bị coi là "6 năm".
- **Định dạng**: `4,3`/`4.3`; một dấu phân cách + đúng 3 chữ số (`2.058`, `2,058`) là mơ hồ → thử cả hai cách
  hiểu; khoảng `a–b`, `a-b`, `a ~ b`, `từ a đến b` (số đầu thừa hưởng đơn vị số sau); `1 tỷ 8` = 1,8 tỷ;
  `1 tỷ rưỡi`; `2 tỷ 58 triệu`; `4,3 tr/m2`, `4.300.000 đ/m²`. Sai lệch cho phép ±1% so với giá trị nguồn.
- **Tập số được phép**: duyệt đệ quy JSON tool (pydantic → `model_dump(mode="json")`). Bỏ qua khóa nội bộ
  (`id`, `*_id`, `ids`, `rank`, `*version*`, `limit`, `sort_order`, `chunk_index`) và chuỗi URL/slug/code; số
  dưới khóa `score/weight/coefficient` chỉ dùng cho claim điểm/phần trăm. Số khách tự nói (`user_texts`):
  số CÓ đơn vị ("2 tỷ", "100 m²", "5 người") được gom theo đúng loại; số trần và tích `5x20` → 100 chỉ dùng cho
  tiền/đơn giá/diện tích/số lớn/năm (xem mục review bên dưới).
- **Loại số đếm đối chiếu theo loại** (`project_count`, `contractor_count`, `staff`, `years`, `area`): chỉ khớp
  với giá trị cùng loại — suy ra từ đơn vị trong chuỗi (`"7 công trình"`), từ đường dẫn khóa JSON
  (`dezon_project_count`, `staff*`, `years*/experience/warranty`, `*area*/*m2*`, `total_candidates`) hoặc độ dài
  danh sách (`results`, `contractors`, `projects`, `ids`). Lý do: nếu so với mọi số trong JSON, "4 nhà thầu" sẽ
  khớp nhầm `floors=4`. Hệ quả: tool của module khác nên đặt tên khóa/chuỗi display có đơn vị như trên; nếu
  không, câu trả lời đúng có thể bị chặn (an toàn: rơi về fallback).
- **`search_knowledge` không phải nguồn số liệu** (mục 9.2): khi `tool_results` truyền dạng cặp
  `(tool_name, result)`, số trong kết quả `search_knowledge` không được tính là được phép. Fallback của
  `search_knowledge` bỏ mọi câu có số liệu trong đoạn trích.
- **Cụm cấm**: `best_claim` ("tốt nhất", "uy tín nhất", "giỏi nhất", "chất lượng nhất", "đáng tin cậy nhất",
  "là/nhà thầu… số 1", "top 1") — bỏ qua khi câu có phủ định trước cụm ("không thể khẳng định", "chưa có dữ
  liệu để kết luận", "không phải"…) và thành ngữ "tốt nhất anh/chị nên…"; "phù hợp nhất" luôn hợp lệ.
  `project_count_label`: số + công trình/dự án kèm "đã hoàn thành/thi công xong/bàn giao/hoàn tất" (trong 5 từ),
  trừ khi có "không/chưa". `estimate_as_quote`: "đây/khái toán… là báo giá", "chi phí chính xác là…", "khái
  toán này chính xác", "cam kết chi phí/giá" — trừ ngữ cảnh phủ định hoặc "yêu cầu báo giá… để có con số chính xác".
- **Vòng sinh lại**: `run_guarded` gọi `generate(None)`, nếu vi phạm gọi `generate(retry_message)` (tối đa 2 lần);
  vẫn sai → `build_fallback_text`; nếu chính fallback không qua validator (không nên xảy ra) → câu xin lỗi
  chung không có số. `record_violations` chỉ `add` + `flush`, caller commit.
- **Fallback** chỉ chép chuỗi `display`/văn bản engine, không tự định dạng số mới (kể cả diện tích quy đổi —
  đã có trong bảng UI). Ngày cập nhật nguồn hiển thị dd/mm/yyyy, kèm "dữ liệu có thể đã cũ" khi `is_stale`.
  Giả định hình dạng dict: `update_project_requirements` → `{slots, missing_required}`; `get_reference_rates`
  → `{rate, rate_data}`; `search_knowledge` → `{chunks: [{content, source: {label, url}}]}`;
  `create_quote_request` → `{ids, code}`; `get_contractor_projects` → list `ProjectSummary` hoặc `{projects}`.
- **Bổ sung sau review**:
  - Số trần khách nói ("5x20", "1 trệt 3 lầu") KHÔNG làm hợp lệ số đếm/số năm/điểm/% ("5 năm kinh nghiệm",
    "3 kỹ sư", "1 nhà thầu" bị chặn). Điểm và % là loại chặt: chỉ khớp khóa score/weight/coefficient, chuỗi có
    đơn vị điểm/%, khóa `*percent*/*pct*` hoặc phân số 0–1 × 100 — không khớp số bất kỳ trong JSON.
  - Nhận đơn vị **không dấu** (ty, trieu, nghin, dong, tr/m2, cong trinh, du an, nha thau, tho, ky su, nguoi,
    diem, phan tram, "nam" viết thường) và **số bằng chữ** hai…mười trước công trình/dự án/nhân sự/năm (không gồm
    "một" và "tư"; không áp cho số nhà thầu vì "hai đơn vị này", "cả ba nhà thầu" là cách nói tham chiếu; số ghép
    "mười lăm", "hai mươi" chưa hỗ trợ).
  - Phân số số đếm "2/3 nhà thầu", "0/3 hồ sơ" không bị che như ngày: tử số và mẫu số đều được đối chiếu
    (khóa `contractors_with_projects`, `with_warranty_info`, `verified_profiles`, `total_dezon_projects` của
    `ContractorOverview` được gắn loại số đếm).
  - Phủ định chỉ có hiệu lực trong cùng mệnh đề (cắt tại `:`, `;`, ngoặc, "nhưng", "tuy nhiên", ", em/thì/còn/và");
    "không phải" chỉ tính là phủ định khi đứng ngay trước cụm (≤ 3 từ chen giữa) — "Không phải ngẫu nhiên mà X là
    nhà thầu tốt nhất" bị chặn. Ngữ cảnh hợp lệ "yêu cầu/gửi/để có …" chỉ tính khi cách cụm ≤ 2 từ, không qua dấu phẩy.
  - Thêm cụm cấm: "nhà thầu/đơn vị/công ty … hàng đầu", "xuất sắc/chuyên nghiệp/đáng chọn nhất"; "ưu tiên hàng
    đầu" vẫn hợp lệ. Thêm `estimate_as_quote` khi gắn nhãn "báo giá" cho con số ("Báo giá cho công trình … là 2 tỷ",
    "Báo giá sơ bộ: …"); tối đa một vi phạm `estimate_as_quote` mỗi câu.
  - `collect_allowed` tự suy tên tool khi không truyền cặp (name, result): `list[KnowledgeChunk]` → bỏ khỏi nguồn số.
  - Fallback: nhận tuple `(EvidenceValue, RateData|None)` của `estimate_service.get_reference_rates`,
    `list[KnowledgeChunk]` của `knowledge_service.search_knowledge`, thêm template `get_contractor_overview`
    (`ContractorOverview`, mục 8.4); tên tool lạ → suy theo hình dạng kết quả; lỗi template bất kỳ → bỏ qua
    (log warning), không làm hỏng lượt chat; ngày nguồn không phải ISO → bỏ phần "cập nhật".

## Slot filling + chuẩn hóa + hỏi tiếp (`app/ai/normalize.py`, `slot_filling.py`, `dialog.py`)

- **So khớp không dấu**: mọi quy tắc chạy trên chuỗi "gập" (bỏ dấu, đ→d, ×→x, ²→2, gạch dài→"-"), giữ nguyên độ dài
  để cắt lại văn bản gốc. Alias dễ trùng cụm từ thường ("Gia Lai" ~ "giá lại", "Nhà Bè" ~ "nhà bé", "Huế", "Long An",
  "Sơn La", "Hà Nam", "Thủ đô") chỉ nhận khi văn bản gốc viết hoa chữ đầu.
- **Kích thước**: "a x b" = ngang a × dài b; diện tích đất = a × b do code tính. "N m²" mặc định là đất, trừ khi
  từ khóa gần nhất phía trước là "xây dựng/sàn/mỗi tầng" (→ `building_area_m2`). Đơn giá "triệu/m²" không bị hiểu là diện tích.
- **Số tầng**: "1 trệt N lầu" = N+1; **"N lầu" (không nói trệt) = N+1** (cách nói phía Nam); "N tầng" = N; "1 trệt"/
  "nhà cấp 4" = 1; tầng lửng/hầm không tính vào tầng nổi. Chữ số bằng chữ ("một trệt hai lầu") chỉ đổi khi đứng ngay
  trước từ chỉ tầng.
- **Kết cấu**: "sân thượng" không nói rõ → `open` (không mái); "mái ngói" không nói rõ → `roof_tile_on_concrete`
  (mái ngói kèo thép phải nói "kèo thép/sắt"). Đều hiện trong tóm tắt để khách sửa.
- **Ngân sách** (giá trị = ngân sách tối đa khách nêu): "Dưới X" → X; khoảng "X–Y" → **Y (cận trên)**; "Trên X" → X
  (cận dưới). "1 tỷ 8" → 1,8 tỷ; "1 tỷ 25" → 1,25 tỷ; "1 tỷ 800 (triệu)" → 1,8 tỷ; "1 tỷ rưỡi" → 1,5 tỷ. Chỉ nhận số tiền
  có đơn vị tỷ/tỉ/triệu/tr/đồng — trừ khi câu có "ngân sách" hoặc đang hỏi ngân sách: số trần < 100 → tỷ, 100–99.999
  → triệu. Giới hạn hợp lệ 10 triệu – 1.000 tỷ. Tóm tắt hiển thị đúng giá trị đã lưu (vd "2,5 tỷ" cho "1,5–2,5 tỷ").
- **Vị trí**: tỉnh dùng tên trước sáp nhập 2025 (khớp seed); quận nội thành HCM/HN/ĐN và một số TP trực thuộc tỉnh
  (Biên Hòa, Thủ Dầu Một, Dĩ An, Thuận An, Nha Trang, Đà Lạt, Hạ Long, Quy Nhơn, Buôn Ma Thuột, Phú Quốc) suy ra tỉnh.
  "Quận 1–12"/"Q7" → TP.HCM. Tên quận lưu dạng ngắn ("Bình Thạnh", "Quận 7"), riêng "TP. Thủ Đức". Đổi tỉnh mà không
  nói quận → xóa quận cũ nếu không thuộc tỉnh mới. Khi đang hỏi tỉnh và khách gõ tên lạ (≤ 5 từ, không số) → lưu
  nguyên văn.
- **Ý định**: thứ tự edit (gồm "không đúng/chưa đúng") > confirm > out_of_scope (từ khóa ngoài chủ đề và không có từ
  khóa xây dựng) > question. Tin nhắn có thông tin mới → `provide_info`, hoặc `edit` nếu ghi đè giá trị đã có / có
  từ "sửa/đổi". Câu hỏi kèm thông tin ("nhà phố 5x20 hết bao nhiêu?") → `provide_info` (thông tin vẫn được lưu;
  orchestrator dựa vào `missing_required`). "sửa nhà/sửa chữa" không phải ý định sửa thông tin.
- **Xác nhận**: `confirmed=True` chỉ khi intent confirm và đủ slot bắt buộc; bất kỳ slot nào đổi hoặc intent edit
  → `confirmed=False`. Lặp lại giá trị cũ không bỏ xác nhận.
- **LLM**: chỉ gọi khi provider khác `mock`. Schema chặt sinh từ Pydantic (`additionalProperties: false`, mọi trường
  required, nullable). LLM trả giá trị THÔ (`floors_text`, `budget_text` nguyên văn) → code chuẩn hóa; ngân sách chỉ
  nhận qua `budget_text` parse được. Chống bịa: số (ngang/dài/diện tích/tầng) phải xuất hiện trong tin nhắn; chuỗi tự
  do (tỉnh/quận lạ, thời gian) phải là chuỗi con của tin nhắn. Quy tắc luôn thắng LLM; LLM chỉ bổ sung trường thiếu.
  Intent LLM chỉ dùng khi quy tắc không nhận ra.
- **Câu trả lời ngắn** (theo `expected_slots`): "4" khi hỏi số tầng → floors; khi hỏi chung đất + tầng: số ≤ 10 → tầng,
  ≥ 10 → m² (số 10 hiểu là 10 tầng); "5 20" → 5×20; "Khác" khi hỏi loại công trình → `khac`, khi hỏi tỉnh → không
  điền (chờ khách gõ); "thô" → xây thô.
- **Slots.asked** (trường mới, mặc định `[]`): `extract` ghi các `expected_slots` đã hỏi để `budget_question` không
  hỏi lại ngân sách khi khách chưa trả lời con số. Không ảnh hưởng `to_estimate_input` (EstimateInput bỏ trường thừa).
- **Hỏi tiếp**: chỉ hỏi slot bắt buộc theo thứ tự 5.2; diện tích đất + số tầng hỏi chung một lượt nếu cùng thiếu.
  Không hỏi `quality_tier` trước khái toán (mặc định "Trung bình (mặc định)" trong tóm tắt); ngân sách hỏi sau khái
  toán qua `budget_question`. Quick replies loại công trình có thêm "Khác" (5.4) bên cạnh 5 loại của 5.2; "Nhập thông
  tin khác" = `allow_free_text=True`. Tóm tắt luôn liệt kê giả định mặc định mục 6.2 (diện tích xây dựng = đất, móng
  băng/cọc theo số tầng, mái BTCT, không hầm, không lửng, mức trung bình).
- (Review) **Chống LLM bịa, siết thêm**: `budget_text`/`floors_text` chỉ nhận khi mọi chữ số trong đó có trong tin nhắn
  (số viết bằng chữ có dấu như "hai", "hai mươi" được tính; so khớp trên chuỗi có dấu để "tầm"/"sau"/"tư" không thành
  số). Tỉnh/quận LLM trả về phải xuất hiện trong tin nhắn (bỏ tiền tố TP./Tỉnh/Quận, không phân biệt dấu). Hầm, lửng,
  mái, sân thượng, móng chỉ nhận khi tin nhắn có từ khóa tương ứng (hầm / lửng, gác / mái, ngói, tôn / sân thượng, tum /
  móng, cọc). `building_type="khac"` từ LLM chỉ nhận khi đang hỏi loại công trình hoặc khách nói "khác".
  `work_scope`/`quality_tier` từ LLM chưa có kiểm tra từ khóa (giá trị ngữ nghĩa, vd "xịn" → cao cấp).
- (Review) Nút "Khác" của loại công trình gửi value **"Công trình khác"** (hiểu được không cần `expected_slots`);
  `dialog.GREETING_EXPECTED_SLOTS = ["building_type"]` để orchestrator truyền vào `extract` ở lượt ngay sau lời chào.
- (Review) Trả lời trống "không"/"chưa"/"sai" → intent `edit` (thường là đáp câu hỏi xác nhận tóm tắt). "Đổi ..." đầu
  câu chỉ là edit khi viết "đổi"/"doi" (không nhầm "đợi"). Đơn vị tỷ dính số ("1ty5", "1tỷ5") được hiểu là 1,5 tỷ.
- (Review) Câu trả lời ngắn giữ dấu thập phân ("1,5" khi hỏi ngân sách → 1,5 tỷ; "62,5" khi hỏi đất → 62,5 m², không
  thành 62×5). Khi hỏi tỉnh/thời gian, câu có từ như "tôi/anh/chưa/để/biết/nhà..." không bị lưu nguyên văn làm địa danh
  hay thời gian ("tôi chưa chọn được", "để tôi suy nghĩ").

## Ingestion, RAG & background jobs (`app/ingestion/`, `app/rag/`, `app/jobs.py`, `app/worker.py`)

- **Khảo sát trang thật 02/11/2026** (9 request, DezonAIBot, delay ≥ 2 giây): dezon.vn render phía server → parser
  BeautifulSoup trên HTML tĩnh, Playwright chỉ là tùy chọn (import lười, chưa cài). Fixture HTML THẬT (đã lược
  script/style/header/footer) ở `backend/tests/fixtures/dezon/`. Mẫu URL thật và ánh xạ trường: `docs/DATA_MAPPING.md`.
  Slug thật `kien-phong`, `smartcons-tech`, `huy-hoang` trùng seed; URL thật có `/` cuối; dự án mẫu của Kiến Phong
  nằm ở `/nha-thau/kien-phong/<công-trình>/` (vd `indochine-villa-quan-7-sai-gon`), không phải `/du-an/<slug>`.
- **robots.txt**: `urllib.robotparser` là "khớp đầu tiên" và không hiểu wildcard → với robots.txt của dezon.vn
  (`Allow:/` trước `Disallow: /planning/`) nó cho phép `/planning/`. Fetcher đánh giá thêm theo RFC 9309 (khớp dài nhất,
  `*`, `$`, hòa → Allow) và chỉ tải khi CẢ HAI cho phép; tôn trọng `Crawl-delay` nếu > 2 giây; robots.txt lỗi mạng →
  coi như chặn, 401/403 → chặn, 404 → cho phép. `/planning/` (khái toán) không được crawl → đơn giá/hệ số đi qua CSV.
- Trang danh sách có hộp "Đăng nhập để xem tiếp" hiển thị bằng JS; nội dung hồ sơ vẫn có trong HTML công khai và
  robots.txt cho phép. **Cần Dezon xác nhận** được dùng dữ liệu này (hoặc cấp API) trước khi bật crawl định kỳ trên production.
- **raw_hash** = sha256 HTML đã chuẩn hóa (bỏ script/style/noscript/svg/comment/header/footer/bộ đếm lượt xem/nonce,
  ưu tiên vùng `<main>`), để lượt xem hay menu đổi không gây "updated" giả.
- **data_sources**: một bản ghi cho mỗi URL (unique). Hash không đổi → chỉ cập nhật `fetched_at` (= lần kiểm tra lại
  gần nhất, dữ liệu vẫn đúng tới thời điểm đó). Hash đổi → trạng thái về mặc định theo `source_type` và xóa
  `verified_by/at` (xác minh cũ không còn áp dụng cho nội dung mới). Cùng URL chỉ nâng `source_type` lên ưu tiên cao hơn.
  `source_updated_at` lấy từ `article:modified_time` của trang.
- **Ưu tiên nguồn khi upsert**: theo `contractors.field_sources`; nguồn ưu tiên thấp hơn chỉ điền trường đang trống.
  Cùng mức ưu tiên → nguồn mới ghi đè (dữ liệu crawl thật thay seed dev cùng loại `crawl_public`). Giá trị `None` chỉ
  xóa trường khi chính nguồn đó trước đây đã cung cấp. `projects` không có field_sources → xét theo `projects.source_id`;
  `None` không bao giờ xóa trường dự án. Năng lực (`contractor_capabilities`) của nguồn ưu tiên cao hơn được giữ.
  Thẻ trong trang danh sách (`primary=False`) không thành `contractors.source_id` và chỉ tạo năng lực khi chưa có
  (tránh giành qua lại với trang hồ sơ).
- **Không suy diễn số liệu khi parse**: khoảng giá trị cho cột một giá trị ("Quy mô dự án 7 – 15 tỷ", "100 - 300 người")
  → `None` (khoảng lưu ở `extra`, không ghi DB); số không có đơn vị tiền → `None`; "0 năm" trên thẻ danh sách = chưa
  khai báo → không ghi. Dấu phẩy là thập phân ("2,058 tỷ" = 2,058 tỷ); dấu chấm là nghìn khi theo sau đúng nhóm 3 số.
- `projects.building_type` của công trình nằm dưới hồ sơ nhà thầu suy từ **tên** khi có từ khóa rõ (Villa/Biệt thự →
  biệt thự, Duplex/Penthouse/Căn hộ → căn hộ, Nhà phố/House → nhà phố); không có từ khóa → `None`. Đây là thuộc tính
  phân loại, không phải số liệu. "DIỆN TÍCH" trên trang `/du-an/` lưu vào `floor_area_m2` (**giả định**, trang không
  nói rõ đất hay sàn — cần Dezon xác nhận). "NĂM HOÀN THIỆN" → `status='completed'`, không đặt `end_date` (chỉ có năm).
- Slug dự án dưới hồ sơ nhà thầu = `<slug-nhà-thầu>--<slug-công-trình>`, `external_id` = đường dẫn URL — tránh trùng với
  `/du-an/<slug>`. Nhà thầu trong trang dự án chưa có trong DB: crawler bỏ qua liên kết (lần sau sẽ nối);
  API sync được phép tạo bản ghi tối thiểu (tên + URL hồ sơ, không có số liệu).
- `contractor_capabilities.work_scope` để trống khi crawl (trang không có trường phạm vi chuẩn hóa).
- **CSV/XLSX**: nhập từng dòng hợp lệ, dòng lỗi liệt kê trong báo cáo (số dòng, cột, thông báo tiếng Việt). Mỗi lần nhập
  1 `data_sources` (`csv_import` → `reference`, url NULL, hash file; nhập lại cùng file → `unchanged`). Không ghi đè bản ghi
  cùng khóa từ nguồn ưu tiên cao hơn (vd đơn giá seed `dezon_editorial`) trừ khi `force=True` (admin) — quy trình
  chuẩn là nhập bảng giá mới với `effective_from` mới. Hệ số nhập vào luôn `approved=false` (cột approved bị bỏ qua).
  Tỉnh không nhận diện được → lỗi dòng (dùng `*` cho toàn quốc). Số liệu trong file mẫu chép từ seed dev.
- **API sync** là skeleton với ánh xạ trường giả định (DATA_MAPPING mục 3). `data_sources.url` của bản ghi API = URL tài
  nguyên API (URL hồ sơ công khai đã thuộc bản ghi crawl). Cờ verified từ API không tự nâng `status`.
- **seed_snapshot** mặc định không làm gì; `SEED_SNAPSHOT=1` nạp `app/db/seed_data/dezon_snapshot.json` (chưa tạo file
  này trong repo — chạy runner `--snapshot` khi Dezon đồng ý dùng dữ liệu crawl).
- **RAG**: document gồm tên, nhãn, khu vực, loại công trình, phạm vi, mô tả, tên các công trình ghi nhận (và với dự án:
  loại, địa điểm, nhà thầu, trạng thái, mô tả). **Không đưa trường số liệu có cấu trúc** (đơn giá, số công trình,
  nhân sự, năm thành lập/kinh nghiệm, quy mô, diện tích, số tầng) và **che cụm "số + đơn vị"** trong mô tả tự do
  ("12 nhân sự" → "…", "năm 2018" → "năm …"); số trong tên riêng không kèm đơn vị ("Quận 7") giữ nguyên. Mỗi
  `KnowledgeChunk` có `usage_note` "Chỉ dùng cho mô tả, không dùng làm nguồn số liệu".
- `HashEmbedder`: feature hashing có dấu (blake2b) trên từ, cặp từ, 3-gram ký tự (đã gấp dấu), bỏ từ dừng, mở rộng đồng
  nghĩa (villa ↔ biệt thự, Indochine ↔ Đông Dương, Sài Gòn/HCM ↔ Hồ Chí Minh, Q7 → quận 7…), chuẩn hóa L2. Văn bản rỗng →
  vector đơn vị cố định (tránh NaN với cosine). `OpenAIEmbedder` gửi `dimensions=1536`; `EMBEDDING_API_BASE` tùy chọn.
  `EMBEDDING_PROVIDER=auto` dùng `Settings.resolved_embedding_provider`.
- Chunk ~800 ký tự theo đoạn/câu, overlap ~150 ký tự theo ranh giới câu; chunk thứ 2 trở đi có tiền tố
  "Nhà thầu X:" / "Dự án Y:". Re-index chỉ embed lại chunk có nội dung đổi; nhà thầu ngừng hoạt động → xóa document.
- **Retrieve hybrid**: điểm = 0,6 × cosine (pgvector `<=>`) + 0,4 × max(`word_similarity` pg_trgm, tỉ lệ từ khóa không dấu
  xuất hiện). Ứng viên = hợp top vector và top từ khóa (ILIKE / `<%`); mỗi thực thể giữ 1 chunk tốt nhất; bỏ điểm < 0,05.
  `url` = profile_url/project_url hiện tại; `source` = data_sources của document (fallback nguồn của thực thể).
- **Jobs**: `job_crawl`, `job_api_sync`, `job_import_csv(path, kind)`, `job_reindex_all`, `job_after_sync`,
  `job_daily_sync`; sau đồng bộ chỉ re-embed thực thể thay đổi (kèm nhà thầu/dự án liên quan) và gọi
  `contractor_service.recompute_profile_completeness(db)` (import lười). `enqueue(name, **kwargs)` dùng queue `default`.
- **Worker**: `python -m app.worker` = RQ worker + luồng lập lịch 02:00 Asia/Ho_Chi_Minh (zoneinfo) xếp `daily_sync`
  (API nếu có `DEZON_API_BASE_URL`, ngược lại crawl); khóa Redis `dezon:daily_sync:<ngày>` (SET NX) chống xếp trùng khi
  nhiều worker. Redis chưa sẵn sàng → thử lại backoff 1→60 giây. Windows dùng `SimpleWorker` (không có fork) chỉ để dev.
- Quan sát lệch đặc tả: hồ sơ thật Huy Hoàng (02/11/2026) đã có kinh nghiệm/đơn giá và 7 công trình; seed vẫn giữ theo
  bảng 11.3 vì là fixture cho trường hợp thiếu dữ liệu.
- **Sau review (02/11/2026)**:
  - Parse tiền: viết tắt "3tr5" = 3,5 triệu, "1ty2" = 1,2 tỷ (chữ số dính liền sau đơn vị là phần thập phân). Hai số
    chỉ được ghép thành khoảng khi nối bằng `-`, `~`, "đến", "tới"; không có dấu nối thì lấy số đầu, và số không đơn vị
    không mượn đơn vị của số sau. Giá trị 0 ("0 đ") coi là chưa khai báo → `None`.
  - Thẻ trong trang danh sách (`primary=False`) chỉ điền trường đang trống hoặc trường do chính trang danh sách cung cấp.
    Nó không ghi đè trường đang thuộc nguồn khác cùng mức ưu tiên, ví dụ trang hồ sơ. Lý do: khi trang danh sách đổi
    còn trang hồ sơ thì không, giá trị của hồ sơ sẽ bị thay và `field_sources` bị đổi nguồn, mà không có lần cập nhật
    nào sửa lại.
  - CSV: đơn giá không có đơn vị và nhỏ hơn 100.000 ("4,3", "4.3", ô số 4.3 trong XLSX) bị báo lỗi dòng, không đoán
    là triệu. Trước đây "4.3" bị đọc thành 43 đồng và "4300000.0" thành 43 triệu. `force=True` áp lại file kể cả khi
    hash không đổi. Nhập lại hệ số có nội dung giống hệt thì không bỏ cờ `approved`; chỉ bỏ khi hệ số, nhãn, nhóm
    hoặc cơ sở thay đổi.
  - robots.txt trả 5xx thì coi như chặn toàn bộ (RFC 9309, "unreachable"). Trước đây trường hợp này bị coi như cho phép.
  - Runner: parser hoặc ghi DB lỗi ở một trang chỉ làm tăng `errors` và hoàn tác trang đó (savepoint), không dừng
    cả lượt crawl.
  - RAG còn che thêm: số có phân cách hàng nghìn ("4.300.000"), "…/m²", năm đứng riêng ("thành lập 2018"), và các
    đơn vị tháng/ngày/căn/phòng/kỹ sư/sao… ("bảo hành 12 tháng").
  - `job_daily_sync`: khi có `DEZON_API_BASE_URL` nhưng api_sync trả `skipped` (thiếu token) thì chuyển sang crawl.
    `enqueue` dùng `enqueue_call(kwargs=...)` để tham số job không bị RQ hiểu nhầm là tùy chọn của RQ.
    `recompute_profile_completeness` lỗi không còn chặn bước re-embed.
  - API sync bỏ qua bản ghi không có `id`/`slug` (trước đây tạo nhà thầu slug "None"). Bản ghi ánh xạ lỗi chỉ tăng
    `errors`, không làm hỏng cả lượt đồng bộ.

## Frontend

- **Bundle**: Vite chế độ thư viện, định dạng IIFE → một file `frontend/dist/dezon-widget.js` (~74 kB gzip, gồm React).
  CSS import `?inline`, chèn vào **Shadow DOM** (`mode: open`) gắn trên `<div id="dezon-ai-widget">` ở cuối `body`.
  Chỉ dùng đơn vị px (rem sẽ theo cỡ chữ gốc của dezon.vn). `useDefineForClassFields=false` để esbuild không sinh helper
  nằm ngoài IIFE (rò biến toàn cục lên trang chủ).
- **Cấu hình**: `document.currentScript` được lưu ngay khi script chạy → `data-api-base` → fallback
  `VITE_API_BASE_URL` lúc build → rỗng = **cùng origin** (`/api/...`). **Không tự bật mock khi thiếu apiBase**, để không
  bao giờ hiển thị dữ liệu giả như thật; mock chỉ bật bằng `data-mock="true"` hoặc `?dezon_mock=1`, và header hiện nhãn
  "Chế độ giả lập". Thêm `data-open="true"` / `?dezon_open=1`; API toàn cục `window.DezonWidget.open/close/toggle()`.
- **Trang demo**: dùng chung `frontend/index.html` cho dev server và nginx: khi build, plugin `demoPagePlugin` thay đoạn
  giữa `<!-- dezon-widget:start/end -->` bằng thẻ nhúng thật (`data-api-base` = `VITE_API_BASE_URL`, hoặc
  `data-mock="true"` khi biến rỗng). Trang demo cố tình có CSS "gây nhiễu" để kiểm tra cô lập style.
- **Phiên**: `session_id` lưu `localStorage["dezon_ai_session_id"]` (mock: `dezon_ai_mock_session_id`); phiên chỉ được
  tạo/khôi phục khi khách **mở** khung chat (không gọi API trên mọi trang). `getSession` lỗi (hết hạn/404) → tạo phiên mới.
  Trạng thái đóng/mở giữ trong `sessionStorage` của tab.
- **Tin nhắn cục bộ**: "Xem chi tiết", "So sánh", "Yêu cầu báo giá" gọi REST (`getContractor`, `compare`) hoặc mở form và
  thêm khối `contractor_detail` / `comparison_table` / `quote_request_form` như tin nhắn trợ lý **chỉ ở trình duyệt**
  (không lưu server) → mất khi tải lại trang. Nếu cần lưu, backend có thể thêm endpoint ghi tin nhắn hệ thống.
- **Nút tương tác**: quick replies chỉ hiện ở tin nhắn trợ lý (từ server) mới nhất; nút của `summary_confirm` cũ bị vô
  hiệu hóa. Lỗi SSE/mạng → bong bóng lỗi + nút "Thử lại" (gửi lại đúng nội dung). Luồng đóng mà không có `done`/`error`
  → coi là lỗi "Kết nối bị gián đoạn".
- **recordClick**: gọi khi bấm tên nhà thầu, "Xem chi tiết", "Xem hồ sơ năng lực ↗", "Yêu cầu báo giá" trên thẻ. Từ bảng
  so sánh: dùng `recommendation_log_id` của `contractor_list` gần nhất nếu nhà thầu nằm trong kết quả đó; không có
  log thì bỏ qua (không bịa log id). Lỗi recordClick bị nuốt (không ảnh hưởng UI).
- **Định dạng số**: EvidenceValue luôn hiển thị `display`. Riêng các trường số thuần **không có display** trong schema —
  diện tích (`land_area_m2`, `A_i`, `converted_area_m2`), hệ số `k_i`, `saving_min` của phương án ngân sách, trọng số/điểm
  breakdown — được định dạng bằng bản chuyển ngữ 1-1 của `app/formatting.py` (`viNumber`, `formatM2`, `formatVnd`) trong
  `src/utils/format.ts`. Nếu muốn bỏ hẳn định dạng phía client, backend có thể THÊM `BudgetOption.saving: EvidenceValue`.
- **"Chưa có dữ liệu"**: EvidenceCell coi `status=unavailable`, `null/undefined` hoặc `display` rỗng là thiếu dữ liệu và
  hiển thị chữ xám đúng chuỗi này (không badge). Bảng so sánh tự điền ô thiếu nếu `cells` ngắn hơn số nhà thầu; hồ sơ mở
  rộng luôn hiện đủ 8 dòng thông tin mục 8.3 (khóa thiếu → "Chưa có dữ liệu"). Không có công trình → "Chưa có dữ liệu".
- **Badge**: dạng nhãn chữ trong bảng khái toán/thẻ; dạng **chấm màu** (vùng chạm 24 px) trong bảng so sánh + chú thích
  màu bên dưới, để vừa màn hình 360 px. Tooltip nguồn: một popover `position: fixed` dùng chung (không bị cắt trong vùng
  cuộn ngang), hover/focus hiện tạm, bấm (tap) để ghim, Esc / bấm ra ngoài để đóng. `is_stale` → "Dữ liệu cập nhật ngày
  dd/mm/yyyy, có thể đã cũ" (ngày định dạng từ chuỗi ISO, không qua `Date` để tránh lệch múi giờ).
- **Miễn trừ**: nếu `EstimateResult.disclaimer` rỗng, frontend dựng câu mục 6.6 kèm nhãn + ngày nguồn đơn giá — bảng
  khái toán không bao giờ thiếu miễn trừ.
- **Mức phù hợp**: màu theo nhãn backend (`Phù hợp cao` xanh lá, `Phù hợp` xanh dương, `Cần đánh giá thêm` cam); điểm
  hiển thị phụ (chữ nhỏ "Điểm N/100"), lý do là nội dung chính; mỗi lý do có chấm trạng thái của evidence nó trỏ tới.
  Tiêu đề danh sách: "N nhà thầu phù hợp nhất với tiêu chí của anh/chị".
- **Form báo giá**: SĐT Việt Nam hợp lệ = di động `0[35789]xxxxxxxx` hoặc cố định `02xxxxxxxxx`, chấp nhận `+84`/`84`,
  bỏ khoảng trắng/chấm/gạch; gửi lên dạng đã chuẩn hóa. Email tùy chọn. Khách có thể bỏ chọn nhà thầu (tối thiểu 1).
- **Mock**: fixture bám seed (Kiến Phong đủ dữ liệu; Smartcons Tech, Huy Hoàng thiếu kinh nghiệm/đơn giá) và ca nghiệm
  thu 490 m² · 2,058–2,352 tỷ; kịch bản hội thoại 5.4 dựa trên từ khóa. Bảng khái toán mock **luôn là ca 490 m²** dù khách
  nhập số khác; điểm/breakdown mock là số minh họa. Hồ sơ Huy Hoàng trong mock có ngày cập nhật 15/03/2026 (`is_stale`)
  chỉ để minh họa cảnh báo dữ liệu cũ — khác seed. Ảnh dùng placehold.co như seed.
- **Lỗi đã sửa khi kiểm tra trực quan**: popover nguồn dùng `setState(prev => ({...}))` bị React áp lại updater khi rebase
  lane (mouseenter/focus khác độ ưu tiên) → object mới mỗi lần render → vòng lặp layout effect (React #185) làm sập
  widget. Đã chuyển sang tính state từ ref + `setState(giá_trị)`, có test hồi quy; thêm `BlockBoundary` cô lập lỗi từng block.
- **Docker**: `node:22-alpine` build (ARG `VITE_API_BASE_URL`) → `nginx:alpine` cổng 80; `dezon-widget.js` cache 5 phút
  (tên file cố định) + `Access-Control-Allow-Origin: *`; trang demo `no-cache`.

### Frontend — bổ sung sau review
- **Khôi phục phiên**: chỉ bỏ `session_id` đã lưu khi `GET /api/chat/sessions/{id}` trả 4xx (phiên không còn). Lỗi
  mạng/5xx → giữ `session_id`, hiện "Thử lại" (trước đây mọi lỗi đều xóa phiên và tạo phiên mới → mất hội thoại khi mạng chập chờn).
- **Chống gửi trùng**: khóa đồng bộ bằng ref trong `useChat.send` (bấm quick reply/Enter 2 lần trước khi `busy` kịp render).
- **`data-api-base` rỗng** → fallback `VITE_API_BASE_URL` (trước đây chuỗi rỗng chặn fallback). Nếu `document.currentScript`
  null (script chèn qua trình quản lý thẻ) → tìm thẻ `script[src*="dezon-widget"]`.
- **SĐT báo giá** gửi lên ở dạng trong nước `0xxxxxxxxx` (`+84 912 345 678` → `0912345678`) để lưu thống nhất.
- **Form báo giá**: tối đa 5 nhà thầu (hợp đồng API); form không có nhà thầu nào → hiện lỗi rõ ràng thay vì im lặng.
- **Bảng khái toán**: dòng "Đơn giá tham chiếu" hiện nguồn kèm "cập nhật dd/mm/yyyy" ngay trong bảng (mục 13), không chỉ trong tooltip.
- **Phương án ngân sách**: `saving_min` = `current.cost_min − option.cost_min` → nhãn "Tiết kiệm khoảng X so với khái toán hiện tại".
- `types.ts` thêm `Slots.asked`, `OverviewContractor`, `ContractorOverview` cho khớp `schemas/slots.py`, `schemas/matching.py`.

## REST API (trừ chat), Admin tối thiểu & đo lường (`app/api/{estimates,contractors,quotes,admin,deps}.py`, `app/services/{admin_service,metrics_service}.py`)

- **Lỗi validation 422 tiếng Việt**: FastAPI không cho đăng ký exception handler ở cấp router và không được sửa `main.py`,
  nên các router dùng `route_class=VietnameseRoute` (deps.py) để bắt `RequestValidationError` → `{"detail": "<chuỗi tiếng
  Việt>", "errors": [{loc, field, message, type}]}`. `detail` là **chuỗi** để widget (client.ts) hiển thị thẳng. Lỗi nghiệp vụ
  (ValueError, QuoteRequestError…) → 422/404/409 với thông báo tiếng Việt; không trả stack trace.
- **Wrapper response** (`{items,total}`, `{rate,rate_data}`, `{ids,code}`, `{ok}`) khai báo ngay trong module API (chỉ gói lại
  kiểu của `app/schemas`), không thêm file schema dùng chung.
- **`chat_session_id` không tồn tại** ở `POST /api/estimates` → bỏ qua (lưu NULL) thay vì lỗi khóa ngoại — giống
  `match_contractors`. Khóa id lớn hơn BIGINT trong path/body → 404/422 thay vì lỗi DB.
- **Compare với `estimate_session_id` không tồn tại** → vẫn trả ma trận, hàng "Mức phù hợp" = "Chưa có dữ liệu" (không chặn
  khách so sánh vì một phiên khái toán cũ). Không có bộ trọng số active → 503 (match/compare).
- **`POST /api/quote-requests` trả 201** `{ids, code}` (đúng hợp đồng; không thêm trường).
- **Token admin**: `X-Admin-Token` so với `ADMIN_TOKEN` bằng `hmac.compare_digest`; token rỗng luôn bị từ chối. Mặc định
  `change-me` trong config — **phải đổi trên production**. Chưa có danh tính từng quản trị viên: `verified_by` lấy từ body
  (mặc định "admin"). `GET /admin` (trang tĩnh `app/static/admin.html`) không cần token; trang hỏi token, lưu ở
  `sessionStorage` và gửi qua header; mọi nội dung động render bằng `textContent` (chống XSS từ chi tiết vi phạm/nhãn nguồn).
- **"Duyệt" đơn giá = xác minh nguồn**: `construction_rates` không có cột `approved`; trạng thái hiển thị lấy từ
  `data_sources.status` (mục 4.4). `POST /api/admin/rates/{id}/approve` đặt nguồn của đơn giá thành `verified` → mọi đơn giá
  cùng nguồn (vd cả một file CSV) được duyệt theo; response trả `affected_rates`. Đơn giá tạo qua admin (không truyền
  `source_id`) có nguồn `dezon_editorial` riêng (`reference`, hoặc `verified` nếu `approved=true`).
- **Đơn giá đã dùng trong khái toán** (`estimate_sessions.rate_id`): không xóa, không sửa giá/tỉnh/mức/ngày hiệu lực (409) —
  chỉ đặt `effective_to` rồi tạo đơn giá mới, để giữ dấu vết.
- **Hệ số**: `POST /api/admin/rules` tạo phiên bản mới (version = max + 1 theo `code`; nhóm/nhãn/cơ sở kế thừa bản mới nhất
  nếu bỏ trống; mã mới phải đủ). `PUT` chỉ đổi `label_vi`, `is_default`, `approved`; đổi hệ số/nhóm/cơ sở → 422 "hãy tạo phiên
  bản mới". Không xóa hệ số đang duyệt (409). Khái toán tự dùng bản đã duyệt mới nhất (estimate_service.load_ruleset).
- **Trọng số**: đúng 6 khóa `CRITERIA_KEYS`, mỗi giá trị 0..1, tổng = 1 ± 0,001. Tạo/kích hoạt chạy dưới
  `pg_advisory_xact_lock` để luôn đúng 1 bản active; không cho tắt bản active duy nhất (422), không xóa bản active hoặc bản đã
  có `recommendation_logs` (409, giữ để so sánh A/B). Nội dung trọng số của một version là bất biến.
- **Đồng bộ** `POST /api/admin/sync/{source}`: `api|crawl|reindex|after_sync` → `app.jobs.enqueue` (`api_sync`, `crawl`,
  `reindex_all`, `after_sync`), kết nối Redis có timeout 2 s + `ping()`; lỗi → 503 kèm hướng dẫn (REDIS_URL, `python -m
  app.worker`). `max_pages` (1..1000) cho api/crawl. `csv` chạy **đồng bộ** `import_table` (file multipart trường `file`, thân
  request thô kèm `?filename=`, hoặc `path` trên máy chủ) với `kind=rates|rules`, trả báo cáo. `path` chỉ nhận .csv/.xlsx/.xlsm
  và chỉ admin có token gọi được. `POST /api/admin/import/{kind}` tương tự (tham số `dry_run`, `force`); giới hạn 5 MB (413).
- **python-multipart**: thêm vào `pyproject.toml` (cần cho upload). Endpoint upload đọc form bằng `request.form()` lúc chạy
  (không khai báo `File()`), nên app vẫn khởi động được nếu môi trường thiếu thư viện này — chỉ upload multipart báo 422.
- **Analytics**: `estimate_created` (POST /api/estimates, props estimate_session_id/converted_area/cost), `contractor_clicked`
  (props recommendation_log_id, contractor_id, rank, weights_version; session_id = phiên chat của log). `quote_request_created`
  do quote_service ghi. `slots_completed` (props.turns) do orchestrator ghi — chưa có thì chỉ số tương ứng = null.
- **Chỉ số mục 12** (`GET /api/admin/metrics`, mỗi chỉ số `{key, label_vi, value, unit, numerator, denominator, sample_size,
  note, breakdown}`; thiếu dữ liệu → `value=null` + ghi chú "Chưa có dữ liệu để tính: …"):
  - Hoàn thành khái toán = số phiên chat có ≥ 1 `estimate_sessions` / số phiên chat (khái toán gọi thẳng API không có phiên
    chat không tính vào tử số).
  - Số lượt hỏi trung bình = trung bình `props.turns` của sự kiện `slots_completed`.
  - Độ chính xác khái toán: chỉ dùng `contractor_quotes.total_amount` có gắn khái toán qua `quote_requests` (báo giá chỉ có
    đơn giá/m² bị bỏ qua và ghi số lượng trong note); tách 2 chỉ số: % nằm trong `[cost_min, cost_max]` và sai số trung vị
    `(quote − midpoint)/midpoint` (dương = báo giá cao hơn).
  - Chọn nhà thầu theo thứ hạng: value = lượt bấm / lượt hiển thị; breakdown theo rank gồm `clicked`, `quoted`. Yêu cầu báo
    giá được gắn với **một** lượt đề xuất (gần nhất trước lúc gửi, chứa nhà thầu đó) qua `estimate_session_id` chung hoặc sự kiện
    `quote_request_created` cùng phiên chat.
  - Tỷ lệ gửi yêu cầu báo giá = số "phiên có matching" đã gửi ≥ 1 yêu cầu / số phiên có matching (phiên = phiên chat, nếu
    không có thì phiên khái toán, nếu không có nữa thì từng log) — đúng định nghĩa mục 12; breakdown kèm tỷ lệ thô
    `quote_requests / recommendation_logs`.
  - Vi phạm guardrail = số dòng `guardrail_violations` / số `chat_messages` role=assistant (một câu có thể nhiều vi phạm,
    kể cả lần sinh lại) + breakdown theo rule.
  - Độ phủ: nhà thầu đang hoạt động; "có đơn giá" = có `rough_price_min` hoặc `finish_price_min`; "trường verified" = trường
    hồ sơ có giá trị mà nguồn (`field_sources[field]`, mặc định `source_id`) có `data_sources.status = verified`.

## AI Orchestrator, tools, providers & Chat API/SSE (`app/ai/{orchestrator,tools,blocks,state}.py`, `app/ai/providers/*`, `app/services/chat_service.py`, `app/api/chat.py`)

- **Trạng thái phiên**: lưu trong `chat_sessions.slots` dưới khóa nội bộ `_state` (`app/ai/state.py` `SessionState`:
  `expected_slots` của câu hỏi trước, `user_turns`, cờ đã ghi `slots_completed`, khái toán hiện tại `{id, cost_min,
  cost_max, cost_display, slots_hash}`, `budget_checked_for`, `matched_for`, `recommendation_log_id`, danh sách nhà thầu
  vừa đề xuất `{id, name, slug}`, cờ `presented`). Không cần migration; `_state` luôn bị loại khỏi slots trả frontend
  (POST/GET session, event `done`). Lượt đầu dùng `GREETING_EXPECTED_SLOTS` (phiên tạo qua API đã lưu sẵn).
- **Luồng chính do code bảo đảm, không phụ thuộc LLM nhớ gọi tool**: thiếu slot bắt buộc → câu hỏi `dialog.next_question`
  kèm quick replies; đủ slot mà chưa xác nhận → `build_summary` + `summary_confirm`; đã xác nhận mà chưa có khái toán cho
  đúng bộ slot (so `slots_hash`) → orchestrator TỰ gọi `calculate_estimate`; ngân sách < `cost_min` → tự gọi
  `suggest_budget_adjustments`; có khái toán mà chưa matching cho cặp (khái toán, ngân sách) → tự gọi `search_contractors`
  (kèm `estimate_session_id`), rồi hỏi ngân sách nếu chưa có (`budget_question`; câu chữ riêng vì nhà thầu đã được đề xuất
  ngay lượt đó). Khi có ngân sách mới, matching chạy lại với ngân sách. Tool tự gọi chạy TRƯỚC khi gọi LLM và được đưa vào
  hội thoại dưới dạng lượt assistant tool_calls (id `auto_*`) + tool result, để LLM chỉ cần giải thích.
- **Câu hỏi/tóm tắt do code nối vào cuối văn bản**: LLM được báo qua khối trạng thái (`he_thong_se_tu_them`) để không hỏi
  lại. Nhờ vậy việc hỏi đúng slot thiếu được bảo đảm bằng code. Nếu LLM gọi `update_project_requirements` làm đổi slot giữa
  lượt, câu hỏi/tóm tắt được dựng lại theo slot mới.
- **Giữ xác nhận khi chỉ đổi ngân sách/thời gian khởi công** (`NON_ESTIMATE_SLOTS`): `slot_filling.extract` đặt
  `confirmed=False` khi có slot đổi; orchestrator khôi phục `confirmed=True` nếu mọi slot đổi đều không ảnh hưởng con số
  khái toán. Đổi slot khái toán → hủy khái toán/matching hiện tại và tóm tắt lại để xác nhận. "Tôi muốn sửa lại" (không đổi
  slot) → hiện lại tóm tắt.
- **Guardrail trong tool (bằng code)**: `calculate_estimate` do LLM gọi bị từ chối khi khách chưa xác nhận tóm tắt. Tham số
  truyền vào ghi đè slots (phương án "nếu… thì sao"); chỉ khái toán tính đúng theo slots mới thành "khái toán hiện tại".
  Khái toán trùng trong một lượt được khử trùng lặp (không tạo thêm `estimate_sessions`). `create_quote_request` chỉ nhận
  SĐT/tên có xuất hiện trong tin nhắn của khách (chống LLM bịa thông tin liên hệ); bỏ trống `contractor_ids` /
  `estimate_session_id` = nhà thầu vừa đề xuất (tối đa 5) / khái toán hiện tại. `get_contractor_profile` và
  `get_contractor_projects` nhận id, slug hoặc **tên** (ưu tiên nhà thầu vừa đề xuất, sau đó lấy kết quả đầu của
  `search_contractors_simple(q=tên)`). Có thêm tool `get_contractor_overview` (mục 8.4).
- **Validator**: `tool_results` = mọi kết quả tool thành công trong lượt (kể cả tool tự gọi) + `("session_context",
  {"slots": ...})` + kết quả gần nhất của từng tool ở các lượt trước, bọc thành `("context:<tool>", {"previous": ...})`
  (để LLM nhắc lại được con số đã hiển thị, nhưng câu dự phòng không lặp lại kết quả cũ). `user_texts` = mọi tin nhắn
  khách trong phiên. Validator kiểm tra phần văn bản do LLM viết. Phần code nối thêm (câu hỏi, tóm tắt, "Chi phí sơ bộ dự
  kiến: <display>", ghi chú "không phải báo giá", "chưa có phương án nào… trong ngân sách") dựng từ chuỗi `display`/dialog
  nên được tin cậy. Mỗi lần bị chặn đều gọi `record_violations` + commit. Câu dự phòng chung (GENERIC_APOLOGY) bị bỏ nếu
  lượt đã có câu hỏi/tóm tắt do code dựng.
- **Bảo đảm nội dung bắt buộc**: lượt có khái toán mà văn bản LLM không chứa nguyên `cost.display` → code thêm câu "Chi
  phí sơ bộ dự kiến: …"; không nhắc "báo giá" → thêm "Đây là khái toán sơ bộ, không phải báo giá thi công."
- **Kết quả tool gửi LLM**: JSON `{"ok": true, "result": <rút gọn>}` hoặc `{"is_error": true, "error": "<tiếng Việt>"}`.
  Rút gọn: bỏ ảnh/avatar/breakdown, cắt danh sách dự án còn 5, cắt chuỗi > 500 ký tự; GIỮ `value/display/status/source/
  is_stale`. Validator, UI block và DB dùng kết quả ĐẦY ĐỦ (`chat_messages.tool_payload = {arguments, result, is_error,
  error, forced}`).
- **UI block** (`blocks.py`), theo thứ tự: estimate_table → budget_options → contractor_list → comparison_table →
  contractor_detail (kèm `estimate_session_id`) → quote_request_form → source_badges → summary_confirm → quick_replies.
  `source_badges`: nguồn của mọi EvidenceValue có giá trị trong kết quả tool của lượt, khử trùng lặp theo (label, url,
  ngày); bỏ nguồn "Ngân sách do anh/chị cung cấp" và nguồn không có cả url lẫn ngày; tối đa 8 chip. `quote_request_form`
  hiện khi tin nhắn khách có "báo giá" (code phát hiện) và lượt đó chưa tạo yêu cầu; danh sách nhà thầu = nhà thầu vừa đề
  xuất (hoặc nhà thầu vừa xem hồ sơ). Sau `contractor_list`, nếu không cần hỏi ngân sách, có quick replies "So sánh các
  nhà thầu" / "Yêu cầu báo giá".
- **Stage**: `collect` (thiếu slot) → `estimate` (đủ slot: đang tóm tắt hoặc đã khái toán) → `match` (đã đề xuất nhà
  thầu) → `present` (đã so sánh / xem hồ sơ / gửi báo giá). Đổi slot khái toán đưa về bước tóm tắt (`estimate`).
- **Analytics**: `session_started` (tạo phiên); `slots_completed {turns}` (lần đầu đủ slot bắt buộc; turns = số tin nhắn
  khách tới lúc đó); `estimate_created {estimate_session_id, from_slots, has_cost}`; `quote_request_created` do
  quote_service ghi.
- **Lỗi LLM**: provider ném `LLMError` (hoặc lỗi bất ngờ) thì không trả 500: tool tự gọi vẫn chạy, văn bản = câu dự phòng
  từ kết quả tool + câu hỏi/tóm tắt do code; không có gì thì trả "hệ thống tư vấn đang bận…". Lỗi khi sinh lại → câu dự
  phòng. Giới hạn `settings.max_tool_rounds` (6) vòng tool do LLM gọi mỗi lượt (tool tự gọi không tính); hết vòng thì lấy
  văn bản của lần gọi kế tiếp và bỏ qua tool call thừa.
- **SSE**: cả lượt (gồm validator và sinh lại) được tính xong rồi mới phát sự kiện, vì hợp đồng chỉ cho stream văn bản đã
  duyệt. Văn bản chia thành mảnh ≤ 48 ký tự theo từ; thứ tự text… ui_block… done. Lỗi bất ngờ trong lượt → rollback + một
  event `error` (HTTP 200). Phiên không tồn tại hoặc id sai định dạng → 404 JSON trước khi stream. Nội dung 1..2000 ký tự
  sau khi strip, rỗng → 422. Tin nhắn khách được commit ngay đầu lượt.
- **Rate limit** 20 tin/phút/IP (`chat_rate_limit_per_min`): Redis cửa sổ cố định theo phút (`INCR` + `EXPIRE`, khóa
  `dezon:rl:chat:<ip>:<phút>`). Không kết nối được Redis lúc khởi tạo, hoặc Redis lỗi khi đếm → cửa sổ trượt 60 giây trong
  bộ nhớ tiến trình. Vượt → 429 JSON `{"detail": "<tiếng Việt>"}` + `Retry-After`. Rate limit được kiểm tra trước khi tra
  phiên. IP = `request.client.host`; sau proxy cần chạy uvicorn `--proxy-headers --forwarded-allow-ips` để không bị giả
  `X-Forwarded-For`.
- **Providers**: `get_provider()` chọn theo `resolved_llm_provider`. OpenAI (chính, `gpt-4.1-mini`): Chat Completions,
  `tool_choice="auto"`, temperature 0.2 (extract: 0), timeout 60 giây, `max_retries=2`; extract bằng `response_format`
  json_schema `strict: true`; lỗi SDK → `LLMError(kind=rate_limit|timeout|connection|auth|bad_request|server|unknown)`.
  Anthropic (tùy chọn): `max_tokens=16000`, `output_config={"effort": "low"}`, không gửi temperature/top_p/thinking/
  tool_choice; nếu `LLM_MODEL` vẫn là model OpenAI (`gpt-*`) thì dùng `claude-opus-5-5`. `response.content` được giữ ở
  `raw` và gửi lại nguyên vẹn trong các vòng tool; nhiều tool_result gộp vào MỘT user message; cờ `is_error` của
  tool_result suy từ JSON `{"is_error": true}`. Chưa bật tham số `fallbacks` phía máy chủ (beta) cho `refusal`: refusal
  hiện trả văn bản rỗng và orchestrator dùng câu dự phòng.
- **MockProvider** (demo không cần key): chọn tool theo từ khóa trong tin nhắn cuối. "so sánh" → compare (tìm nhà thầu
  trước nếu chưa có); "đơn giá/hồ sơ của <Tên>" → get_contractor_profile; "<Tên> … dự án" → get_contractor_projects;
  "tốt nhất/uy tín/nhà thầu nào/thi công … tốt" → get_contractor_overview; "báo giá" + SĐT + "tên tôi là …" →
  create_quote_request; "tìm/đề xuất nhà thầu" → search_contractors; "đơn giá" → get_reference_rates; câu hỏi khác ngoài
  giai đoạn thu thập → search_knowledge. Văn bản chỉ ghép chuỗi `display`/văn bản engine. `MockProvider(lie_text=…)` dùng
  cho test guardrail. Tên nhà thầu được nhận diện bằng cụm Viết Hoa sau "của/nhà thầu/hồ sơ/đơn vị/công ty/về"; khách gõ
  tên không viết hoa chỉ được nhận nếu nhà thầu đó vừa được đề xuất trong phiên.

## REST API — kết quả review (giới hạn đầu vào, bảo mật trang admin)

- `POST /api/estimates` (`EstimateCreateIn`, chỉ nằm trong router, KHÔNG sửa `EstimateInput` dùng chung): tỉnh/thành
  không được rỗng hoặc chỉ có khoảng trắng; chiều rộng/dài ≤ 5.000 m; các diện tích ≤ 200.000 m²; độ sâu hầm ≤ 100 m.
  Diện tích suy ra từ rộng × dài mà vượt giới hạn → 422. Lưu khái toán bị tràn cột số (`DataError`, ví dụ đơn giá admin
  nhập cực lớn × diện tích lớn) → rollback + 422 "Quy mô công trình vượt giới hạn tính toán…" thay vì 500.
- `budget-adjustments`: `target_budget_vnd` ≤ 10^15 VND (1 triệu tỷ). Trước khi sửa, 10^30 vẫn trả 200 và hiển thị
  "1.000.000.000.000.000.000.000 tỷ".
- `POST /api/contractors/match` (`MatchIn`): tỉnh không rỗng, `budget_vnd` trong (0, 10^15], `floors` trong 1..50,
  `converted_area_m2` trong (0, 10^8]. Trước khi sửa, số âm hoặc tỉnh rỗng vẫn chạy matching và ghi `recommendation_logs`.
- `GET /api/rates`: tỉnh chỉ có khoảng trắng → 422.
- `POST /api/quote-requests`: họ tên chỉ có khoảng trắng → 422 (trước khi sửa: lưu họ tên rỗng).
- Lỗi 422 có thêm nhãn tiếng Việt cho tham số đường dẫn/admin (log_id, slug, source, kind, version…). Lỗi
  `finite_number` (NaN/Infinity) cũng có thông báo tiếng Việt.
- Trang `/admin`: URL nguồn chỉ thành link khi bắt đầu bằng http(s). Mục đích: chặn `javascript:` từ dữ liệu crawl/API.
  Đã kiểm tra trên trình duyệt thật (Playwright, uvicorn tạm trên DB dev): nhập mã, cả 7 tab đều tải được dữ liệu, console
  không có lỗi JS.

## AI Orchestrator & Chat API/SSE — kết quả review

- **Câu hỏi không ghi đè thông tin công trình.** Khi khách đã cung cấp đủ slot bắt buộc, câu hỏi (`detect_intent` =
  question) không được đổi các slot ảnh hưởng khái toán. Ví dụ "Nhà thầu nào thi công biệt thự tốt tại TP.HCM?" trước
  đây đổi `building_type` sang biệt thự; "đơn giá hoàn thiện ở HCM?" đổi `work_scope`. Cả hai làm mất khái toán đã xác
  nhận và hiện lại tóm tắt. Giờ chỉ nhận ngân sách và thời gian từ câu hỏi. Khách muốn đổi thì nói rõ ("đổi sang
  biệt thự", "sửa lại thành 3 tầng"), khi đó intent là edit. Câu "nếu ... thì sao" để LLM gọi `calculate_estimate` với
  tham số giả định.
- **Lượt thu thập: chỉ code được hỏi.** Khi code nối câu hỏi slot hoặc bản tóm tắt vào cuối, mọi câu hỏi (câu kết thúc
  bằng "?") trong phần LLM viết đều bị bỏ, trừ khi khách đang hỏi. Như vậy quy tắc "không hỏi lại slot đã có / không
  hỏi trùng" được code bảo đảm, không chỉ dựa vào prompt. Chữ viết tắt "TP." không bị coi là hết câu.
- **"Sửa lại" mà chưa có thông tin mới:** trả lời bằng câu "anh/chị muốn sửa thông tin nào ạ?…", rồi mới hiện bản tóm
  tắt và summary_confirm. Trước đây chỉ lặp lại bản tóm tắt.
- **Ghi chú "không phải báo giá" cho khái toán:** chỉ coi là đã có khi văn bản chứa cụm "không phải (là) báo giá". Trước
  đây chỉ cần chữ "báo giá" xuất hiện là đủ, nên câu "gửi yêu cầu báo giá" cũng làm mất ghi chú.
- Sự kiện `slots_completed` được commit ngay, để tool lỗi hệ thống (`db.rollback()`) không làm mất sự kiện.
- MockProvider:
  - Chưa có nhà thầu đề xuất thì không nhắc tới "form bên dưới", vì khi đó hệ thống không hiện form.
  - `get_reference_rates` lấy phạm vi, loại công trình và khu vực trong câu hỏi.
  - Không gọi tổng quan nhà thầu khi hệ thống vừa chạy `search_contractors` trong cùng lượt.
- OpenAIProvider: timeout 30 giây mỗi lần thử, tối đa 2 lần thử lại. Khách đang chờ SSE nên không để 60 giây × 3.
- AnthropicProvider: bỏ các tin assistant chỉ có văn bản ở đầu lịch sử (lời chào), để hội thoại gửi API bắt đầu bằng
  lượt user.
- Còn mở:
  - Tham số `update_project_requirements` của LLM không bị đối chiếu với tin nhắn khách. Lý do giữ nguyên: cần để hiểu
    số viết bằng chữ, và bản tóm tắt vẫn bắt khách xác nhận.
  - Validator coi slot là nguồn hợp lệ. Vì vậy nếu LLM tự điền một con số vào slot, con số đó sẽ qua validator, nhưng
    vẫn hiện trong bản tóm tắt để khách sửa.

## Nghiệm thu MVP — xử lý vấn đề đã biết (K1–K3, K5)

- K1 (guardrail): tool `update_project_requirements` đối chiếu giá trị LLM đưa vào với toàn bộ tin nhắn khách của phiên
  (`update_slots(..., message=...)` → bộ lọc chống bịa của `normalize_raw_slots`). Số/địa danh không có trong lời khách
  bị loại, vì slot là nguồn hợp lệ của validator.
- K2: thêm `APP_ENV`. Khi `production` mà `ADMIN_TOKEN` rỗng hoặc `change-me` → mọi `/api/admin/*` trả 503.
- K3: nhập file theo đường dẫn chỉ trong `IMPORT_DIR` (mặc định `data/imports`), chặn path traversal; thông báo lỗi giống
  nhau dù file có tồn tại hay không.
- K5: thông báo lỗi thiếu diện tích bằng tiếng Việt, không lộ tên trường.
- docker-compose: healthcheck Postgres qua TCP (sửa lỗi backend sập ở lần khởi động đầu), `restart: unless-stopped`,
  uvicorn `--proxy-headers` với `FORWARDED_ALLOW_IPS` mặc định `127.0.0.1` (không tin `X-Forwarded-For` giả).

## Nghiệm thu MVP — fixer frontend

- W-F2: reset `font/color: inherit` cho button/input/textarea đặt trong `:where(...)` (specificity 0), nên mọi class
  (`.dz-btn--primary`, `.dz-btn--sm`, `.dz-composer__input`...) đều thắng. Test hồi quy đọc văn bản `styles.css` vì
  jsdom không tính cascade theo specificity.
- W-F3: nhãn thông tin nhà thầu dùng `infoLabel()`: map đủ khóa backend (`years_experience`, `staff`, `province`), còn
  khóa lạ thì hiện "Thông tin khác", không bao giờ in tên khóa thô.
- W-F6: chip trong `source_badges` xuống dòng (`white-space: normal`, `overflow-wrap: anywhere`, `max-width: 100%`).
  Không rút gọn bằng ellipsis vì khách cần đọc hết nguồn.
- W-F7 (phần frontend): khi có block mới, nếu tin nhắn cao hơn khung nhìn thì cuộn tới đầu tin nhắn. Vị trí tính bằng
  `getBoundingClientRect` so với khung cuộn. Nguyên nhân gốc: trước đây dùng `offsetTop`, mà `.dz-messages` không được định
  vị nên `offsetTop` tính theo `.dz-panel` (cộng thêm chiều cao header), làm khung chat cuộn vào giữa đoạn văn. Việc rút
  gọn lời dẫn do fixer chat làm.
- W-F8: tin nhắn cục bộ đã xong (hồ sơ, so sánh, form báo giá) được lưu vào localStorage, khóa
  `<khóa phiên>:local:<session_id>`, tối đa 20 tin. Mỗi tin ghi `afterId` = id của tin server đứng ngay trước nó; khi tải
  lại trang thì ghép vào đúng vị trí đó, mốc không còn thì xếp cuối. Gửi báo giá thành công thì ghi
  `submitted_code` vào block `quote_request_form` (trường chỉ có ở client), nên sau khi tải lại vẫn thấy thẻ thành công
  kèm mã DZ-…. Bắt đầu phiên mới hoặc phiên hết hạn (4xx) thì xóa bản lưu của phiên cũ. Nếu vượt hạn mức thì bỏ dần tin
  cũ nhất. Không đổi backend.
- W-F10: chỉ áp dụng ở viewport ≤ 640px: nút, chip, link, `summary` cao ≥ 44px; icon header 44×44; chấm nguồn trong lý do
  có vùng chạm 32px (dùng margin âm để dòng chữ không giãn); chữ nội dung ≥ 13px (nhắc lại các selector có specificity
  cao như `.dz-srcbadges .dz-badge--full`); ô nhập 16px. Trên desktop, badge tăng từ 11px lên 12px.
- W-F11 (phần frontend): pill mức phù hợp có viền và bảng màu riêng (teal, indigo, tím), không trùng 4 màu trạng thái
  mục 4.4. `contractor_list` hiện sẵn chú giải 4 trạng thái (`StatusLegend`) khi lý do có chấm, nên đọc được mà không
  cần hover. Badge cho ô "Hồ sơ dự án" trong bảng so sánh do fixer ops làm ở backend.

## Nghiệm thu MVP — fixer ops

- O-F1: `python -m app.db.seed` gọi `main()` của module `app.db.seed` (commit 3e802c5), nên các hook (snapshot →
  profile_completeness → RAG) chạy đúng danh sách đã đăng ký. Test hồi quy là
  `test_acceptance_fixes.py::test_seed_as_main_runs_hooks` (chạy qua `runpy` với `run_name="__main__"`).
- O-F2: chặn ký tự điều khiển ở một chỗ chung: `RejectControlCharsMiddleware` (ASGI thuần, không dùng
  BaseHTTPMiddleware để không ảnh hưởng SSE) trong `app/main.py`. Middleware soi path, query và body JSON; ký tự C0 (trừ
  tab, CR, LF) và DEL, ở dạng byte thô hay escape `\u0000`, đều bị trả 422 với `detail` tiếng Việt trước khi truy vấn
  DB. Không soi body multipart hay octet-stream (file import của admin). Chuỗi `\u0000` khách tự gõ không bị chặn. CORS
  bọc ngoài middleware này nên phản hồi 422 vẫn có header CORS.
- O-F5: thêm handler `RequestValidationError` ở cấp app, dùng chung `validation_error_response`, làm lưới an toàn cho
  mọi router. Chat router cũng đã dùng `VietnameseRoute` do fixer chat thêm. Map thêm `int_parsing_size` → "số quá lớn"
  và nhãn trường `content` và `user_id`.
- O-F3: `read_table` bọc lỗi openpyxl (BadZipFile, XML hỏng…) thành `ValueError("File Excel không hợp lệ…")`, nên
  báo cáo trả `status: failed` thay vì lỗi 500.
- O-F7: `dry_run` chạy đúng logic so khớp của lần nhập thật nhưng không ghi gì. Nó tìm nguồn csv_import theo nhãn giống
  `upsert_data_source`: cùng hash thì báo `unchanged`, khác thì đếm từng dòng `inserted/updated/unchanged/skipped`
  (`skipped` = bị nguồn ưu tiên cao hơn chặn). Toast ở admin.html ghi rõ "Chạy thử (chưa ghi): sẽ thêm…".
- O-F6: thêm `GET /api/admin/sync/jobs/{job_id}`, trả `status`, `status_vi`, `result` và `error` (dòng cuối
  traceback). Job được xếp với `result_ttl` và `failure_ttl` là 7 ngày (mặc định của RQ là 500 s). Trang admin tự hỏi
  trạng thái mỗi 2 s sau khi kích hoạt (tối đa khoảng 3 phút) và có ô "Mã job" để xem lại. `job_reindex_all` và
  `job_after_sync` giờ có log thống kê.
- O-F4/O-F8/O-F9: README có thêm mục "Triển khai sau reverse proxy" (`FORWARDED_ALLOW_IPS`) và bảng biến môi trường
  đầy đủ (LLM_API_KEY cho Claude, LLM_MODEL, LLM_BASE_URL, MAX_TOOL_ROUNDS, EMBEDDING_*, SEED_SNAPSHOT). `.env.example`
  có đủ các biến này. `EMBEDDING_API_BASE` giờ đi qua `Settings.embedding_api_base`, không còn đọc thẳng
  `os.environ`. API_CONTRACT.md liệt kê đủ endpoint admin, kèm mục lỗi chung.
- W-F4: `_criteria_for_estimate_id` dựng tiêu chí giống `tools._slot_criteria`. Nếu phiên chat gắn với khái toán có
  `slots.province` và `slots.building_type` thì lấy tỉnh/quận/loại/hạng mục/số tầng từ slot; ngân sách lấy theo thứ tự
  body `budget_vnd` → `slots.budget_vnd` → `input.budget_vnd`. Nếu slot thiếu thì dùng `estimate_sessions.input`.
  `CompareIn` nhận thêm `budget_vnd` (tùy chọn). Đã kiểm trên dữ liệu e2e: thẻ cho 52/42/41, code cũ ra 62/42/41,
  code mới ra 52/42/41.
- W-F11 (backend): ô "Hồ sơ dự án" đã có sẵn `status` (nguồn yếu nhất trong các dự án, thường là `on_dezon`) và
  `source` (link hồ sơ nhà thầu, ngày cập nhật mới nhất). Đã kiểm trên stack 8001 và thêm test để mọi ô khác
  `unavailable` của bảng so sánh luôn có status và source. Backend không cần sửa gì thêm.
- W-F5: KHÔNG bịa dữ liệu. Dùng crawler của dự án (`Fetcher`: robots.txt, delay ≥ 2 s, UA DezonAIBot,
  `max_requests=25`; thực tế chạy 22 request ngày 02/11/2026) để lấy hồ sơ và 6 công trình thực tế cho mỗi nhà thầu
  kien-phong, smartcons-tech, huy-hoang. Kết quả lưu ở `app/db/seed_data/dezon_snapshot.json`: 3 hồ sơ, 18 công trình
  có `project_url`, mỗi công trình 3–8 URL ảnh gốc trên dezon.vn, avatar thật. docker-compose đặt
  `SEED_SNAPSHOT=${SEED_SNAPSHOT:-1}` cho backend. Upsert dùng cùng URL nguồn nên ảnh placehold.co của fixture
  Kiến Phong được thay bằng ảnh thật. Test luôn đặt `SEED_SNAPSHOT=0` trong conftest để fixture mục 11.3 giữ nguyên.
  Bản thân snapshot được test riêng: dữ liệu thật, idempotent, mỗi nhà thầu có công trình và ảnh, có nhà thầu > 3 ảnh.

## Nghiệm thu MVP — fixer chat

Test hồi quy chính: `tests/test_chat_fixes.py` (luồng thật qua orchestrator + MockProvider).

- C-F1/C-F2: `create_quote_request` không có `contractor_ids` thì chỉ gửi tới nhà thầu đã đề xuất mà khách NÊU TÊN
  trong tin nhắn hiện tại. Khách không nêu tên ai thì mới gửi tới các nhà thầu vừa đề xuất (luồng nút "Yêu cầu báo
  giá"). Mock tự truyền `contractor_ids` theo tên. Văn bản xác nhận nêu tên nhà thầu nhận yêu cầu
  (`contractor_names`). Bộ nhận tên khách hiểu cả "tên X", "họ tên X", "tên tôi là X", "tôi là X".
- C-F3: câu giả định ("nếu…/giả sử…", `normalize.is_hypothetical`) không ghi đè slot (`_protect_profile`). Mock gọi
  `calculate_estimate` với tham số giả định. Tool không cập nhật `state.estimate` khi tham số khác slot, nên khái toán
  chính giữ nguyên, kèm `estimate_table` của phương án thử. Chi phí ngoài phạm vi engine (ép/khoan cọc, giấy phép,
  thiết kế, tháo dỡ, khảo sát địa chất, đấu nối) trả "Chưa có dữ liệu" và nói rõ khoản đó chưa gồm trong khái toán,
  không tra RAG. `search_knowledge` chỉ trả chunk có điểm ≥ `RELEVANT_MIN_SCORE = 0,25`. Đo trên dữ liệu e2e: câu
  lạc đề ≤ 0,12, câu đúng thực thể ≥ 0,29.
- C-F4: câu hỏi tổng quan (mục 8.4) trả block `comparison_table`, vì frontend đã render được block này. Dòng đầu là
  "Công trình <loại> có trang dự án" (số đúng loại, nguồn là trang dự án), sau đó là công trình ghi nhận, đơn giá
  thô/hoàn thiện, bảo hành. Văn bản nêu số nhà thầu và TÊN nhà thầu có công trình đúng loại. Không lặp nguồn/URL
  trong câu: link hồ sơ nằm ở tiêu đề cột, link công trình nằm ở chấm nguồn. Không xếp hạng.
- C-F5: `get_contractor_projects` trả thêm tổng `dezon_project_count` và hồ sơ (`contractor_detail`). Câu trả lời có
  dạng "7 công trình ghi nhận trên Dezon (không đồng nghĩa với số công trình đã hoàn thành); trong đó 3 công trình có
  trang dự án".
- C-F6: câu thiếu dữ liệu viết thành câu tự nhiên, không dùng "…: Chưa có dữ liệu.". Khi đang thu thập, câu hỏi
  "đơn giá …" không ngầm điền `work_scope`/`quality_tier`. Khu vực khách nói vẫn được nhận.
- C-F7: câu hỏi hồ sơ được trả lời đúng mục (kinh nghiệm/bảo hành/nhân sự/quy mô/đơn giá), phần hồ sơ còn lại ở thẻ.
  Văn bản ngân sách chỉ nêu phương án vừa ngân sách (hoặc rẻ nhất). "Tìm nhà thầu khác" mà không có nhà thầu mới thì
  nói rõ Dezon chỉ có N nhà thầu phù hợp. Để phân biệt lần đề xuất đầu với lần cập nhật, khối trạng thái có thêm
  `nha_thau_luot_truoc`.
- C-F8: ở lượt thu thập slot, câu hỏi do code dựng là đủ, mock không thêm câu "đã ghi nhận". Nhiều đoạn trong một
  tin thì chỉ đoạn đầu mở bằng "Dạ,". Lời chào thì đáp "Dạ, em chào anh/chị ạ."
- C-F9: "Sửa lại" mà chưa nói sửa gì thì CHỈ hỏi sửa mục nào, kèm nút theo từng mục (`dialog.EDIT_FIELD_REPLIES`,
  value "Tôi muốn sửa <mục>"). Nút "Giữ nguyên" gửi "Thông tin đúng rồi". Không gắn lại bản tóm tắt. Khách nêu mục
  ("Tôi muốn sửa số tầng") thì hỏi đúng mục đó, kèm quick replies và `expected_slots`.
- K4: khách xác nhận lại (chưa xác nhận → xác nhận) mà slot khái toán không đổi, trong khi phiên đã có khái toán: tính
  lại và hiển thị lại khái toán, ngân sách, nhà thầu (reset `estimate/matched_for/budget_checked_for`). Không trả câu
  chung chung.
- C-F10: hai đầu khoảng tiền cùng đơn vị hiển thị cùng số chữ số thập phân ("1,740–2,058 tỷ", "1,065–1,260 tỷ"). Hai
  đầu cùng là số nguyên thì vẫn gọn ("2–3 tỷ").
- C-F11: văn bản khái toán nói rõ khi dùng đơn giá mặc định toàn quốc. Không có nhà thầu thì không hỏi ngân sách mà
  gợi ý bằng quick replies ("Tìm nhà thầu tại <TP khác>", "Sửa thông tin công trình"). "Tìm nhà thầu tại X" chỉ tìm
  ở X, không đổi khu vực công trình.
- C-F12 (quyết định nghiệp vụ, chờ Dezon xác nhận): phạm vi **Hoàn thiện** KHÔNG cộng hệ số móng vào diện tích quy
  đổi, vì móng thuộc phần xây thô và không có hạng mục hoàn thiện. Assumptions ghi rõ điều này. Bản tóm tắt cũng không
  nêu giả định móng cho Hoàn thiện, Nội thất hay căn hộ. Mái, hầm, sân thượng vẫn tính vì có phần hoàn thiện (chống
  thấm, lát, sơn). Ví dụ nhà phố 5×20, 4 tầng, hoàn thiện: 490 m² → 450 m².
- C-F13: rate limit chuyển sang cửa sổ TRƯỢT 60 s cho cả Redis (sorted set) và bộ nhớ, không còn burst gấp đôi quanh
  mốc phút. Lượt bị chặn không được tính. Lấy IP thật sau proxy là việc cấu hình triển khai (`FORWARDED_ALLOW_IPS`,
  README mục reverse proxy).
- C-F14: chat router dùng `route_class=VietnameseRoute`. Tin nhắn rỗng trả 422 `{detail, errors:[{field, message}]}`.
- C-F15: `public_slots` bỏ trường nội bộ `asked` (GET phiên, event `done`, TurnResult).
- W-F1: orchestrator nhận diện "Tôi chọn phương án: <label>" bằng cách so khớp tên với `options` của lần
  `suggest_budget_adjustments` gần nhất: khớp đúng tên trước, rồi khớp tên dài nhất. Sau đó áp đúng `changes` của
  phương án do engine tính vào slot, không suy lại từ chữ. Khách đã chủ động chọn nên giữ trạng thái xác nhận, tính
  lại ngay (khái toán, ngân sách, nhà thầu) và báo "đã áp dụng phương án …". Dự phòng: `parse_floors` ưu tiên "còn N
  tầng", bỏ qua "giảm/bớt/thêm N tầng".
- W-F7: văn bản khi trả estimate/contractor_list chỉ có lời dẫn ngắn và giải thích gồm, chưa gồm, 1–2 yếu tố tăng
  (mục 6.5). Không chép bảng, phương án hay lý do. Câu hỏi ngân sách là đoạn cuối, tách riêng. Hướng dẫn
  `buoc_tiep_theo` cho LLM thật cũng yêu cầu như vậy.
- W-F9: phương án "Kết hợp" đứng sau các phương án thành phần. Mô tả nêu đúng tên hai phương án. Tên mức chất lượng
  viết thường khi đứng giữa câu ("hạ mức chất lượng xuống tiết kiệm").

## Nghiệm thu MVP — tích hợp sau 3 fixer

- Sau khi rebuild stack e2e với snapshot dữ liệu thật, câu hỏi tổng quan ("Nhà thầu nào thi công biệt thự tốt tại
  TP.HCM?") rơi về câu xin lỗi chung dù bảng vẫn hiện. Nguyên nhân: câu "Kiến Phong (4 công trình biệt thự có trang
  dự án)" không truy được, vì tool chỉ trả danh sách `projects_of_type`. Validator không đếm danh sách này, và khi
  rút gọn cho LLM danh sách còn bị cắt còn 3 mục. Fixture test chỉ có ít công trình nên không lộ lỗi. Cách sửa: THÊM
  `OverviewContractor.projects_of_type_count: int = 0` (đếm đủ). Validator coi trường này và độ dài
  `projects_of_type` là số công trình. Câu dự phòng và ô "Công trình <loại> có trang dự án" dùng số đếm này. Có
  test hồi quy `tests/test_integration_fixes.py`.
- Frontend `ComparisonTable`: khi hàng đầu có key `projects_of_type` (bảng tổng quan C-F4), tiêu đề là
  "Tổng quan N nhà thầu" thay vì "So sánh N nhà thầu".
- Rate limit theo IP thật (C-F13): `docker-compose.yml` đã có `FORWARDED_ALLOW_IPS` (mặc định 127.0.0.1). Stack e2e
  không có reverse proxy, nên mọi khách đi qua cổng Docker chung một IP gateway. Đây là cấu hình triển khai, không
  phải lỗi mã.
- Mã báo giá DZ-… chỉ lưu localStorage (W-F8). Chưa ghi tin nhắn báo giá vào lịch sử chat phía server: để sau MVP.


## Nghiệm thu MVP — sửa sau nghiệm thu cuối

- `BudgetOption.fits_budget` ("Vừa ngân sách") chỉ đúng khi **cận trên** chi phí ≤ ngân sách khách nêu. Trước đây so
  cận dưới nên phương án 1,28–1,52 tỷ bị báo "nằm trong ngân sách 1,3 tỷ".
- Crawler: tỉnh/quận của công trình ưu tiên địa danh trong tên công trình (`apply_location_from_name`), vì dòng meta
  trang công trình trên dezon.vn ghi khu vực của nhà thầu. `extra.location_source` = `project_name` | `page_meta`.
  Snapshot `dezon_snapshot.json` đã chạy lại bước này (8 công trình được sửa tỉnh/quận).
- Đơn giá/m² trên hồ sơ nhà thầu ngoài khoảng 0,5–40 triệu/m², hoặc cận trên lớn hơn 10 lần cận dưới, coi là gõ nhầm
  (vd Huy Hoàng "hoàn thiện cơ bản: Từ 2 đến 80 tr/m²"). Crawler không đưa vào `*_price_min/max` (khách thấy "Chưa có
  dữ liệu", matching tính trung tính + "cần đối chiếu đơn giá"); chuỗi gốc lưu ở `extra.implausible_prices`.
- Snapshot crawl lại toàn bộ 41 nhà thầu của `/nha-thau/tiem-nang/` (trang 1 đã chứa đủ, phân trang làm phía trình
  duyệt) cùng **mọi** công trình thực tế của từng nhà thầu (`--works-per-contractor 50`). Nhà thầu không ghi tỉnh trên
  hồ sơ (vd Châu Phương Thịnh, Minh Gia Phát) vẫn được lọc theo tỉnh nhờ tỉnh của công trình.
- Cận dưới "2 tr/m²" trên hồ sơ nhà thầu dezon.vn là giá trị mặc định của form (20/41 hồ sơ ghi "Từ 2 đến X tr/m²",
  kể cả xây thô — thấp hơn nhiều mặt bằng 3,8–5 tr/m²). Crawler bỏ cận dưới này: chỉ lưu cận trên
  (`*_price_min = None`), hiển thị "tối đa X triệu/m²", chi phí ước theo cận trên (thận trọng) — tránh việc khoảng
  2–X luôn "vừa ngân sách" và vượt mặt nhà thầu khai giá cụ thể. Chuỗi gốc ở `extra.default_min_prices` của snapshot.
  Khai đúng "Từ 2 tr/m²" (không có cận trên) vẫn giữ. **Cần Dezon xác nhận** hoặc sửa form/hồ sơ.
- Matching — tiêu chí khu vực: khớp tỉnh qua tỉnh trên hồ sơ / năng lực đăng ký = 0,8 (như đặc tả); khớp **chỉ qua
  công trình** = 0,6 (`location_kind = "project"`). Lý do: Kiến Phong (TP HCM) có 1 công trình ở Hà Nội từng đứng đầu
  đề xuất nhà phố Hà Nội, trên các nhà thầu sở tại. Bộ lọc cứng 7.2 giữ nguyên (vẫn là ứng viên).

## Đơn giá khái toán lấy từ đơn giá nhà thầu công bố trên Dezon

- Dezon không công bố bảng giá. Bảng giá tự đặt trong `seed.py` (`BASE_RATES`/`TIER_FACTOR`/`PROVINCE_FACTOR`) chỉ
  còn là fixture test (`tests/conftest.py` gọi `seed.seed_rates`); `seed.run()` không nạp nữa.
- Đơn giá/m² = tổng hợp đơn giá các nhà thầu **tự công bố** trên hồ sơ dezon.vn
  (`app/services/market_rates.py::rebuild_market_rates`, chạy sau nạp snapshot và sau mỗi lần đồng bộ `_after_sync`):
  - `xay_tho` <- `rough_price_*`; `tron_goi` <- `finish_price_*` (mục "Đơn giá hoàn thiện cơ bản" trên hồ sơ = trọn
    gói thô + hoàn thiện). `hoan_thien`, `noi_that`: không có dữ liệu -> không có đơn giá ("Chưa có dữ liệu"), engine
    gợi ý chọn Xây thô/Trọn gói hoặc xin báo giá.
  - Mỗi nhà thầu: cận dưới = min (thiếu thì max), cận trên = max (thiếu thì min). Khoảng tổng hợp = (P25 các cận
    dưới, P75 các cận trên), nội suy tuyến tính, làm tròn 50.000 đ (`app/engines/estimate/market.py`).
  - Nhà thầu thuộc loại công trình X nếu có năng lực loại X (bất kỳ tỉnh). Tỉnh nhà thầu = tỉnh hồ sơ + tỉnh năng
    lực; không có thì tỉnh các công trình. Mỗi (loại, tỉnh, phạm vi) cần ≥ 3 nhà thầu có giá; thêm dòng toàn quốc
    `province='*'` tổng hợp mọi nhà thầu của loại đó (cũng cần ≥ 3).
  - Mỗi dòng có `data_sources` riêng: `crawl_public`/`on_dezon`, nhãn "Tổng hợp đơn giá … do N nhà thầu … công bố
    trên Dezon", URL = trang `/nha-thau/tiem-nang/` + fragment riêng (`#don-gia-<loại>-<phạm vi>-<tỉnh>-<yyyymmdd>`) vì
    `data_sources.url` là UNIQUE; `source_updated_at` = ngày cập nhật hồ sơ mới nhất trong nhóm.
  - Dựng lại: dòng tổng hợp cũ và dòng seed dev được đặt `effective_to = hôm qua` (không xóa — `estimate_sessions`
    tham chiếu). Chạy lại trong ngày cập nhật tại chỗ (ràng buộc UNIQUE theo `effective_from`).
- Bỏ mức chất lượng (tiết kiệm/trung bình/khá/cao cấp): không có dữ liệu thật. `select_rate` luôn dùng dòng
  `trung_binh`; engine không ghi giả định/so khớp mức, không còn phương án ngân sách "Hạ mức chất lượng" và yếu tố chi
  phí theo mức. Trường `quality_tier` của schema giữ nguyên (chỉ thêm, không xóa).
- Ưu tiên: trong cùng tỉnh (và cùng `'*'`), đơn giá chính thức (Dezon nhập qua CSV/admin, nhãn không bắt đầu bằng
  "Tổng hợp đơn giá ") đứng trước đơn giá tổng hợp. Fallback `'*'` vẫn mang trạng thái `reference`; câu giả định vẫn
  bắt đầu "Dùng đơn giá mặc định toàn quốc" (lớp AI nhận diện tiền tố này) và nói rõ chưa đủ dữ liệu đơn giá nhà thầu
  tại tỉnh (cần ≥ 3 hồ sơ).

## Hội thoại: hỏi chi tiết công trình trước khi khái toán, không tự giả định

- Sau 5 slot cốt lõi (`REQUIRED_SLOTS`), trước bản tóm tắt, trợ lý hỏi thêm các slot chi tiết (`DETAIL_SLOTS`, theo
  thứ tự): diện tích xây dựng mỗi tầng -> có hầm/lửng (một câu) -> độ sâu hầm (chỉ khi có hầm) -> loại mái. Không áp
  dụng cho căn hộ hoặc phạm vi Nội thất. `missing_required()` trả slot cốt lõi còn thiếu, đủ cốt lõi thì trả slot chi
  tiết còn trống và CHƯA hỏi (`slots.asked`); `missing_core()` chỉ slot cốt lõi (tool `calculate_estimate` dùng để tính
  thử).
- Khách trả lời "chưa biết"/không liên quan -> slot đã hỏi, không hỏi lại; tóm tắt nêu "Anh/chị chưa có thông tin …,
  em tạm tính …" (mặc định engine: bằng diện tích đất, không hầm, không lửng, hầm sâu 1,7–2 m, mái BTCT). Móng không
  hỏi, nêu là quy ước "Móng tính theo thông lệ: …".
- Đang hỏi diện tích xây dựng mà khách nhắn "80 m2"/"80" -> là diện tích xây dựng (không đổi diện tích đất, trừ khi
  nói rõ "đất …" hoặc ngang × dài). "Xây kín đất" -> bằng diện tích đất. "Không" trả lời câu hỏi hầm/lửng = không có
  (không phải ý muốn sửa). "ok" trả lời câu hỏi chi tiết không phải xác nhận tóm tắt.
- Bỏ mức hoàn thiện khỏi hội thoại: không hỏi, không trích (`parse_message`, LLM extract, tham số tool), không hiển
  thị trong tóm tắt; phương án ngân sách cũ có `quality_tier` bị bỏ qua khi áp dụng. `Slots.quality_tier` giữ để tương
  thích schema nhưng không bao giờ được đặt.

## Review sửa lỗi — matching

- **budget_fit theo phạm vi (thay mục budget_fit cũ "`hoan_thien`/`tron_goi` → đơn giá hoàn thiện")**: `xay_tho` →
  đơn giá thô; `tron_goi` → `finish_price_*` ("Đơn giá hoàn thiện cơ bản" trên hồ sơ = giá trọn gói);
  **`hoan_thien` và `noi_that` → không có đơn giá riêng** → trung tính 0,5, cờ `scope_no_contractor_price`, chi phí
  ước "Chưa có dữ liệu", lưu ý "Nhà thầu chưa công bố đơn giá riêng cho phạm vi … trên Dezon — cần yêu cầu báo giá riêng".
- Nhãn hiển thị cho `finish_price` (thẻ/so sánh/bảng tổng quan, lý do đề xuất, nguồn chi phí ước):
  "Đơn giá trọn gói (hoàn thiện cơ bản)" / "đơn giá trọn gói (hoàn thiện cơ bản)".
- **Khớp quận/huyện** (location 1,0) chỉ khi capability ở đúng quận, hoặc dự án ở đúng quận **và** nhà thầu đã đăng ký
  tỉnh đó (tỉnh hồ sơ/capability). Dự án tại quận của nhà thầu ngoại tỉnh vẫn là "project" 0,6 (< sở tại 0,8).
- **Đơn giá chỉ có cận trên** (cận dưới mặc định của form bị bỏ): chi phí ước = diện tích × cận trên, giá trị số giữ
  `[hi, hi]` nhưng hiển thị "tối đa X" (thẻ, ghi chú budget_fit, nguồn ghi "đơn giá … tối đa"). Chấm budget_fit: mức tối
  đa ≤ ngân sách → như quy tắc cũ (1,0 hoặc 0,8 "thấp hơn nhiều"); mức tối đa > ngân sách → trung tính 0,5, **không**
  gắn cờ vượt ngân sách (cận dưới chưa rõ), kèm lưu ý "Hồ sơ chỉ công bố mức đơn giá tối đa … cần đối chiếu báo giá".

## Review sửa lỗi — crawl

- Sàn đơn giá/m² theo loại trên hồ sơ nhà thầu (`PRICE_PER_M2_FLOOR_BY_KIND` trong `app/ingestion/parse_vi.py`):
  xây thô 2,5 tr/m², "hoàn thiện cơ bản" (= trọn gói) 4 tr/m² — thận trọng, dưới mức thấp nhất thực tế trên dezon.vn
  (xây thô 3,8 tr; trọn gói 6 tr, 02/11/2026). Cận dưới thấp hơn sàn -> bỏ cận dưới như trường hợp mặc định của form
  (lưu "tối đa X", chuỗi gốc ở `extra.implausible_min_prices`); cận trên cũng dưới sàn -> bỏ cả khoảng ("Chưa có
  dữ liệu", chuỗi gốc ở `extra.implausible_prices`). Vd An Phú xây thô "0,5–4,2 tr" -> tối đa 4,2 tr; KHP "hoàn thiện
  cơ bản 0,7–2 tr" -> Chưa có dữ liệu. Không truyền loại (`kind=None`) thì chỉ kiểm tra chung 0,5–40 tr như trước.
- Quy tắc chéo: đơn giá trọn gói gồm phần thô, nên cận trên trọn gói < cận dưới xây thô -> trọn gói vô lý, bỏ
  (`parsers.apply_spec_prices`).
- Tỉnh của công trình suy từ tên (`parsers.province_from_name`): tách tên theo dấu phẩy/`;`/`|`/` - `, xét các đoạn
  từ CUỐI lên (địa chỉ tiếng Việt ghi tỉnh sau cùng), trong mỗi đoạn lấy lần nhắc tỉnh cuối cùng; bỏ qua địa danh
  trùng tên tỉnh khi là tên đường/phường/xã ("Điện Biên Phủ"; đứng sau "đường", "xã", "ấp", "phường", "P", "thôn"…).
  Quận/huyện chỉ nhận khi thuộc đúng tỉnh tìm được. Không thấy tỉnh -> giữ tỉnh dòng meta (khu vực nhà thầu).
- Snapshot làm sạch lại KHÔNG crawl: `python -m app.ingestion.crawler.runner --reclean-snapshot <file>` tính lại đơn
  giá từ `extra.spec` và tỉnh/quận công trình nhà thầu từ `extra.meta_line` + tên, giữ `_source`. Lần chạy 02/11/2026
  đổi: An Phú (xây thô min 0,5 tr -> bỏ), KHP (trọn gói 0,7–2 tr -> bỏ), 2 công trình về Tây Ninh (TTPCons "Nhà phố
  số 30, Điện Biên Phủ…", Smartcons "Biệt thự ấp Lập Khánh, Khánh Hòa…"). `_source.raw_hash` không đổi nên DB đã nạp
  snapshot cũ cần cơ chế cập nhật của #4 (upsert theo parser version / backfill) rồi `rebuild_market_rates`.

## Review sửa lỗi — dialog

- Bảo vệ hồ sơ (`orchestrator._protect_profile`) áp dụng ngay khi đủ 5 slot cốt lõi (`missing_core`), kể cả lúc đang
  hỏi chi tiết: câu hỏi / câu giả định ("nếu…") / "tìm nhà thầu tại X" chỉ nhận ngân sách, thời gian; không đổi số tầng,
  khu vực… Lượt được bảo vệ không đánh dấu câu hỏi chi tiết đang chờ là "đã hỏi" -> lượt sau hỏi lại. Ngoại lệ: câu
  trả lời dạng hỏi cho đúng mục đang hỏi ("mái tôn được không?") vẫn được nhận.
- Slot chi tiết chỉ vào `asked` (dùng mặc định, nêu rõ trong tóm tắt) khi khách trả lời được, nói "chưa biết"/"không"/
  "tùy em"/"ok"… Câu trả lời không hiểu được -> hỏi lại, không âm thầm dùng mặc định.
- "Có" chung chung cho câu hỏi "có tầng hầm hoặc tầng lửng không" -> hỏi rõ "Dạ, nhà có tầng hầm, tầng lửng hay cả
  hai ạ?" (bỏ nút "Không hầm, không lửng"); áp dụng cả trong luồng sửa.
- "Không" trả lời câu hỏi chi tiết (vd mái) là "không có ý kiến" -> dùng mặc định và hiện bản tóm tắt, không phải ý
  muốn sửa.
- Câu trả lời ngắn cho câu hỏi mái: "tôn" -> mái tôn; "ngói kèo thép", "mái Thái", "mái Nhật" -> ngói kèo thép;
  "ngói" / "ngói đổ bê tông" -> ngói đổ BTCT (như `parse_structure`); "bê tông", "BTCT", "mái bằng" -> BTCT.
- Sửa diện tích đất mà không nói lại diện tích xây dựng: diện tích xây dựng cũ = đất cũ ("xây kín đất") -> theo đất mới;
  lớn hơn đất mới -> bỏ, hỏi lại. Tóm tắt (không phải căn hộ) cảnh báo khi diện tích xây dựng > diện tích đất.
- Đang hỏi diện tích xây dựng, tin nhắn không có từ chỉ đất / ngang × dài -> bỏ diện tích đất do LLM trích.
- "đất 100m2, xây 80m2" / "xây 80m2 trên đất 100m2" -> số m² ngay sau "xây" là diện tích xây dựng (chỉ khi có từ chỉ
  đất, có ngang × dài, hoặc đang hỏi diện tích xây dựng; "muốn xây 100m2" đứng một mình vẫn là diện tích đất).
- Phiên đã xác nhận TRƯỚC khi có câu hỏi chi tiết (confirmed=True nhưng còn chi tiết chưa hỏi): lượt kế tiếp bỏ xác
  nhận và bỏ khái toán/kết quả tìm nhà thầu cũ -> hỏi chi tiết -> tóm tắt -> khách xác nhận lại rồi mới tính.

## Review sửa lỗi — frontend

- Nhãn giao diện cho `finish_price` (thẻ nhà thầu, hồ sơ, bảng so sánh mẫu): "Đơn giá trọn gói (hoàn thiện cơ bản)"
  (`utils/labels.FINISH_PRICE_LABEL_VI`). Thẻ nhà thầu nhận `workScope` từ `MatchResult.criteria.work_scope`; phạm vi
  Hoàn thiện / Nội thất -> ô "Chi phí ước tính theo đơn giá nhà thầu" luôn "Chưa có dữ liệu" (kể cả payload cũ có số).
- Miễn trừ khi không có đơn giá (frontend `fallbackDisclaimer`, fixture mock) dùng đúng câu no-rate của backend
  (`utils/labels.NO_RATE_DISCLAIMER`): "Đây là khái toán sơ bộ: Dezon chưa có đủ đơn giá do nhà thầu công bố cho phạm
  vi này nên em chỉ tính diện tích sàn quy đổi, chưa tính được chi phí. …". Backend `explain.build_disclaimer` cần trả
  nguyên văn câu này khi không có đơn giá.
- Chế độ mock bám backend: chỉ có đơn giá mẫu cho nhà phố tại TP. Hồ Chí Minh, phạm vi Xây thô (4,2–4,7 tr/m², 34 nhà
  thầu) và Trọn gói (6,8–9 tr/m², 29 nhà thầu) — đúng số backend trả về ngày 02/11/2026 (P25/P75 đơn giá nhà thầu
  công bố trên Dezon); loại công trình / tỉnh khác -> "Chưa có dữ liệu" kèm cảnh báo như `calculate_estimate`.
  Không hỏi chi tiết cho căn hộ / Nội thất; không nêu quy ước móng cho Hoàn thiện / Nội thất / căn hộ; Hoàn thiện bỏ
  móng (450 m²), Nội thất / căn hộ chỉ tính sàn (400 m²). Giả định trong tóm tắt chép nguyên văn
  `dialog.default_assumptions` (hầm chưa rõ độ sâu -> 1,7–2 m). Phương án ngân sách mẫu là đầu ra thật của
  `suggest_adjustments` (Giảm 1 tầng, Đổi sang mái tôn, Kết hợp) — với 1,5 tỷ không phương án nào vừa ngân sách.

## Review sửa lỗi — rates

- Nhận diện nguồn đơn giá ở engine: `MARKET_LABEL_PREFIX`, `SEED_DEV_LABEL_PREFIX`, `is_market_label` chuyển về
  `app/engines/estimate/market.py` (thuần; `services/market_rates` re-export).
- Câu giả định khi dùng dòng toàn quốc nêu đúng nguồn của dòng đó (giữ tiền tố "Dùng đơn giá mặc định toàn quốc"):
  dòng tổng hợp -> giữ câu "tổng hợp đơn giá do các nhà thầu trên Dezon công bố … cần ít nhất 3 hồ sơ"; dòng khác
  (CSV/admin) -> "Dùng đơn giá mặc định toàn quốc (nguồn: <nhãn nguồn>) vì chưa có đơn giá riêng tại <tỉnh>".
- Không có đơn giá (vd Hoàn thiện, Nội thất) -> ghi chú miễn trừ `explain.NO_RATE_DISCLAIMER`, không nói "dựa trên
  đơn giá tham chiếu": "Đây là khái toán sơ bộ: Dezon chưa có đủ đơn giá do nhà thầu công bố cho phạm vi này nên em chỉ
  tính diện tích sàn quy đổi, chưa tính được chi phí. Không phải báo giá thi công. Vui lòng yêu cầu báo giá từ nhà
  thầu để có con số chính xác." (`build_disclaimer(source, has_rate=False)`; frontend dùng đúng chuỗi này).
- `select_rate` so khớp tỉnh qua `parse_vi.normalize_province` trước (cùng bộ chuẩn hóa gắn tỉnh cho dòng tổng hợp):
  quận/huyện ("Thủ Đức", "Quận 7", "Cầu Giấy") khớp dòng của tỉnh/thành, không rơi về toàn quốc.
- Đơn giá mẫu seed dev (nhãn "Đơn giá tham khảo (seed dev …") không bao giờ được `select_rate` dùng; chỉ test bật
  `ALLOW_SEED_DEV_RATES=1` (conftest). Migration dữ liệu `0002_retire_seed_dev_rates` hết hiệu lực các dòng này trên DB
  cũ, đặt lại `building_type = NULL` cho công trình fixture "France Indochine 22 Hà Nội" (dezon.vn không ghi loại) và
  gỡ ảnh/ảnh đại diện placeholder (placehold.co).
- `seed.run()` không nạp fixture (nhà thầu/công trình mẫu + bảng giá tự đặt) nữa; `run(db, fixtures=True)` (conftest)
  hoặc `SEED_FIXTURES=1` (máy dev không nạp snapshot) mới nạp (`seed.seed_fixtures`).
- Đơn giá chính thức (CSV/admin) luôn đứng trước đơn giá tổng hợp, kể cả cùng ngày: admin tạo/sửa đơn giá trùng khóa
  UNIQUE với dòng tổng hợp -> dòng tổng hợp nhường khóa (`market_rates.displace_market_rate`: chưa có khái toán dùng
  -> xóa; đã dùng -> giữ nguyên giá/nguồn, lùi ngày hiệu lực về ngày trống gần nhất trước đó và hết hiệu lực ngay ngày
  đó). Dựng lại trong ngày gặp dòng chính thức -> bỏ qua (`skipped_official`).
- Không bao giờ sửa giá/nhãn của dòng đơn giá đã có khái toán tham chiếu: dựng lại trong ngày mà số liệu đổi -> giữ
  nguyên dòng (`kept_in_use`), số mới áp dụng từ lần dựng lại ngày sau. Dòng chưa dùng vẫn cập nhật tại chỗ.
- Duyệt dòng tổng hợp được giữ khi giá không đổi: dựng lại trong ngày giữ `verified`/`verified_by`/`verified_at`;
  dòng ngày mới cùng giá với dòng trước đã duyệt thì kế thừa duyệt. Giá đổi -> về `on_dezon`, xóa thông tin duyệt.
- Phát hiện thay đổi khi nạp trang: `data_sources.raw_hash` = "<hash trang>:<hash bản ghi đã parse>". Trang không đổi
  nhưng bộ parse/làm sạch cho kết quả khác (hoặc khóa kiểu cũ chỉ có hash trang) -> vẫn ghi lại thực thể (đây cũng là
  backfill làm sạch đơn giá cho DB đã nạp snapshot/crawl cũ); xác minh của nguồn chỉ bị bỏ khi thực thể thật sự đổi.
  `fetched_at` khi nội dung không đổi không bị lùi về trước.
- Snapshot không ghi đè dữ liệu crawl mới hơn: bản ghi có nguồn cùng URL đã nạp từ dữ liệu thật (có raw_hash) với
  `fetched_at` SAU `_source.fetched_at` của snapshot -> bỏ qua (`newer_in_db`). Cùng thời điểm (DB nạp từ snapshot
  cũ) -> vẫn nạp để áp dụng bản làm sạch mới. `seed_hook` chỉ dựng lại đơn giá tổng hợp khi snapshot tạo/cập nhật dữ
  liệu hoặc chưa có dòng tổng hợp còn hiệu lực.
- Đơn giá chính thức (CSV/admin) cùng khóa + cùng ngày với dòng tổng hợp từ hồ sơ nhà thầu: dòng tổng hợp nhường khóa
  (`market_rates.displace_market_rate`). Chưa có khái toán dùng -> xóa. Đã có khái toán dùng -> KHÔNG đổi giá, nguồn,
  `effective_from`/`effective_to`; chỉ chuyển `quality_tier` sang một mức không dùng (`RETIRED_TIERS`: tiet_kiem/kha/
  cao_cap) để nhả ràng buộc UNIQUE — hệ thống không phân mức, select_rate/rebuild chỉ đọc 'trung_binh' nên dòng này
  không bao giờ được chọn lại. (Cách sạch hơn cần migration đổi UNIQUE; chưa làm.) Nhập CSV: dòng tổng hợp cùng khóa
  không bao giờ bị ghi giá đè (kể cả `force=True`) — nhường khóa rồi THÊM dòng chính thức (`replaced_market` trong
  báo cáo). Dòng đơn giá đã có khái toán dùng không bị CSV đổi giá (kể cả `force=True`), báo lỗi dòng. `_expire_old`
  không đặt `effective_to` trước `effective_from` cho dòng đã có khái toán dùng (dòng hôm nay -> hết hiệu lực hôm nay).

## Chạy với LLM thật — trùng lặp văn bản, diện tích tầng lửng

- Lượt thu thập/tóm tắt: câu hỏi và bản tóm tắt do code dựng là bản DUY NHẤT. `orchestrator._drop_questions` bỏ trong
  văn bản LLM: câu hỏi (kể cả ghi chú trong ngoặc đi sau câu hỏi, ghi chú trong ngoặc / câu "Ví dụ ..." đứng riêng),
  câu hoặc ghi chú trong ngoặc chép lại câu hỏi/tóm tắt của code (nguyên văn hoặc ≥ 80% từ trùng, câu ≥ 6 từ); lượt
  tóm tắt bỏ thêm câu có "tóm tắt"/"xác nhận" và dòng liệt kê ("- ...", "Loại công trình: ..."). Khách hỏi (intent
  question) thì giữ câu trả lời, chỉ bỏ phần chép lại. Prompt/khối trạng thái cũng dặn LLM không tự tóm tắt.
- Tầng lửng: thêm slot `mezzanine_area_m2` (mặc định None) vào DETAIL_SLOTS, chỉ hỏi khi `has_mezzanine = True` (như
  độ sâu hầm chỉ hỏi khi có hầm): "Tầng lửng rộng khoảng bao nhiêu m² ạ?" kèm "Khoảng 1/2 sàn (<nửa> m²)", "Bằng cả
  sàn (<sàn> m²)", "Chưa biết" (sàn = diện tích xây dựng mỗi tầng, chưa có thì diện tích đất — như engine). Khách
  "chưa biết" -> giữ mặc định của engine (lửng = diện tích xây dựng mỗi tầng) và nêu rõ trong giả định của bản tóm
  tắt. Khách đổi sang không có lửng -> bỏ diện tích lửng và lần sau có lửng thì hỏi lại; `to_estimate_input` chỉ
  truyền diện tích lửng khi có lửng; `calculate_estimate` với `has_mezzanine=false` bỏ diện tích lửng của slot.
- "Tầng lửng 40 m2"/"lửng 50m2"/"diện tích lửng 35" là diện tích lửng, không phải diện tích đất/xây dựng; đang hỏi
  diện tích lửng thì số trần ("40", "40 m2") là diện tích lửng và giá trị đất/xây dựng LLM đọc ra bị bỏ qua.

## Thẻ câu hỏi từng bước, câu hỏi tùy chọn (phong cách, ngân sách, mức đầu tư)

- Lượt hỏi slot trả thẻ `question_step` thay `quick_replies` (kể cả tin chào). Văn bản không lặp câu hỏi: câu hỏi
  văn xuôi (`NextQuestion.text`) chỉ dùng cho khối trạng thái gửi LLM (`he_thong_se_tu_them`) và để bỏ câu LLM chép
  lại; LLM không viết gì -> lời dẫn cố định `STEP_ACK` (Quay lại: `BACK_ACK`, Cung cấp thêm: `MORE_ACK`, Sửa:
  `EDIT_ACK`). Không dùng câu "em đã ghi nhận" khuôn mẫu (C-F8).
- Hành vi chọn: backend coi MỌI tin nhắn là câu trả lời ngay (bấm dòng/chip gửi `value` ngay, "Tiếp" gửi lựa chọn
  đang chọn hoặc chữ đã gõ) — frontend chọn một cách nhất quán; backend không cần trạng thái "đang chọn".
- `step`/`total` tính từ danh sách câu hỏi dự kiến (`dialog.planned_keys`) cho slot hiện tại, không lưu lịch sử. "Quay
  lại câu trước" = câu đứng trước câu đang hỏi trong danh sách (`state.current_question`), giữ nguyên giá trị; đang ở
  bản tóm tắt -> câu cuối (mức đầu tư). Chỉ xử lý khi chưa xác nhận.
- Diện tích đất + số tầng vẫn là MỘT thẻ khi cả hai còn thiếu (chip = kích thước đất, dòng = số tầng) để giữ tối đa
  2 thông tin/lượt như trước; trong danh sách bước là hai câu (bước nhảy 2 khi khách trả lời cả hai). Căn hộ: hỏi diện
  tích căn hộ (50/80/120/200/300 m²) và số tầng của căn hộ (1 tầng / 2 tầng duplex) riêng.
- Câu hỏi tùy chọn `OPTIONAL_SLOTS = [design_style, budget_vnd, investment_level]` KHÔNG thuộc `missing_required`
  (API `missing_required` giữ nghĩa "bắt buộc để khái toán"); `stage` = "collect" khi còn câu tùy chọn chưa hỏi trước
  lần xác nhận đầu. Đánh dấu "đã hỏi" khi khách trả lời / bỏ qua / nói "chưa biết"; khách HỎI (intent question) hoặc
  muốn sửa mục khác khi thẻ tùy chọn đang hiện -> chưa tính đã hỏi, thẻ hiện lại.
- "ok"/"đúng rồi" khi thẻ tùy chọn đang hiện = bỏ qua câu đó, KHÔNG phải xác nhận tính khái toán (bản tóm tắt chưa
  hiển thị). Riêng đúng tin nhắn của nút "Tính ngay" (`Đúng rồi, tính khái toán giúp tôi`) vẫn là xác nhận: các câu
  tùy chọn còn lại bỏ qua, ngân sách chưa hỏi thì hỏi sau khái toán bằng nút chọn nhanh như cũ.
- Sau lần xác nhận đầu (`state.optional_done`, phiên cũ đã xác nhận cũng tính), sửa thông tin đi thẳng tới bản xác
  nhận — không hỏi lại câu tùy chọn. "Cung cấp thêm thông tin" hỏi lại câu tùy chọn đã bỏ qua VÀ câu chi tiết khách trả
  lời "Chưa biết" (cả hai đều giúp báo giá sát hơn); không còn câu nào -> `more_label/more_value = null`.
- Ngân sách: hỏi trước bản xác nhận (5 mức <2, 2–4, 4–6, 6–10, >10 tỷ; lưu cận trên như quy ước cũ của khoảng giá,
  "dưới 2 tỷ" -> 2 tỷ, "trên 10 tỷ" -> 10 tỷ). Sau khái toán chỉ hỏi khi CHƯA từng hỏi (`budget_vnd` không có trong
  `asked`).
- Phong cách / mức đầu tư chỉ ghi nhận (quyết định của chủ sản phẩm): không vào `EstimateInput`, không đổi đơn giá,
  không tái lập mức hoàn thiện. Mã chuẩn hoặc chữ khách tự nhập (≤ 60 ký tự) khi đang hỏi đúng câu đó; ngoài thẻ chỉ
  nhận khi có ngữ cảnh rõ ("phong cách hiện đại", "mức đầu tư phổ thông") — "đầu tư khá nhiều", "nhà hiện đại giá bao
  nhiêu" không được hiểu thành giá trị. Có mức đầu tư -> `estimate_service.create_estimate(notes=...)` thêm vào giả
  định "Mức đầu tư <x> chưa được tính vào khái toán vì Dezon chưa có đơn giá theo mức đầu tư." (cũng hiện trong giả
  định của bản xác nhận và văn bản mock/dự phòng). Mức đầu tư nằm trong hash khái toán (chỉ khi có giá trị — phiên cũ
  giữ nguyên hash) để đổi mức đầu tư sau khái toán dựng lại bảng kèm ghi chú; con số không đổi. Cả hai thuộc
  `NON_ESTIMATE_SLOTS` (đổi không bắt xác nhận lại).
- Yêu cầu báo giá: ghi chú gửi nhà thầu nối thêm "Phong cách thiết kế: ...", "Mức đầu tư mong muốn: ..." (tool trong
  hội thoại dùng slot hiện tại; REST lấy theo `chat_session_id` tùy chọn hoặc phiên chat của `estimate_session_id`).
  Không thêm cột DB (ghi chú là kênh đã có tới nhà thầu).
- Gợi ý ngân sách trong thẻ: "Có ngân sách giúp em tư vấn sát hơn và tự gợi ý phân khúc phù hợp ạ." (mockup ghi "báo
  giá sát hơn" — đổi thành "tư vấn" vì khái toán không phải báo giá).

## Phần hỏi rút còn ĐÚNG 5 câu (quyết định của chủ sản phẩm, thay quy tắc "hỏi mọi chi tiết")

- Trước bản xác nhận chỉ hỏi 5 câu cốt lõi, mỗi câu một thẻ `question_step`, bước luôn "i/5" theo vị trí cố định:
  1 Loại công trình · 2 Khu vực · 3 Diện tích đất (căn hộ: diện tích căn hộ) · 4 Số tầng (căn hộ: số tầng của căn
  hộ, 1 / 2 "duplex") · 5 Phạm vi thi công. Diện tích đất và số tầng là HAI câu riêng (bỏ thẻ gộp cũ). Câu đã có thông
  tin thì bỏ qua, không đánh số lại. `missing_required` = 5 slot cốt lõi (chi tiết không còn chặn bản tóm tắt).
- Không hỏi trước bản xác nhận: diện tích xây dựng, hầm/lửng, độ sâu hầm, diện tích lửng, mái, phong cách, mức đầu
  tư, ngân sách. Chi tiết dùng mặc định CÓ CÔNG BỐ của engine (xây kín đất, không hầm, không lửng, mái BTCT, móng theo
  thông lệ; có hầm chưa rõ độ sâu -> 1,7–2 m; có lửng chưa rõ diện tích -> bằng diện tích sàn). Bản xác nhận gom các
  mặc định thành một dòng "Tạm tính: ... — anh/chị bấm Sửa hoặc Cung cấp thêm thông tin để thay đổi." và luôn có 4 dòng
  chi tiết (giá trị hoặc "(tạm tính)") kèm nút Sửa, để khách đổi được từng mặc định.
- "Cung cấp thêm thông tin" (`state.more_mode`): hỏi lần lượt diện tích xây dựng → hầm/lửng → mái → độ sâu hầm (nếu
  có hầm) → diện tích lửng (nếu có lửng), chỉ mục chưa có giá trị; bước "i/N" của nhóm này — N chỉ đếm các mục còn
  thiếu lúc bấm nút (`state.more_keys`, `dialog.more_plan`; N tăng khi khách trả lời có hầm/lửng), như mock frontend. Hết nhóm -> bản xác nhận, tắt `more_mode`. Nút hiện khi còn mục chi tiết chưa có giá trị (kể cả "Chưa
  biết"); căn hộ / phạm vi nội thất không có nhóm này.
- "Sửa" một dòng: hỏi lại đúng mục đó (mục chi tiết: bước 1/1, không có "Quay lại câu trước") rồi quay lại bản xác nhận (không chạy tiếp nhóm câu hỏi thêm — vd sửa hầm thành
  "có" thì độ sâu dùng mặc định có nêu rõ; muốn khai độ sâu thì bấm Sửa / Cung cấp thêm).
- "ok"/"được" khi đang ở thẻ câu hỏi chi tiết không phải xác nhận tính khái toán (chỉ đúng tin nhắn nút "Tính ngay").
- Ngân sách chỉ hỏi SAU khái toán (`budget_question`, như trước). Phong cách / mức đầu tư không hỏi; slot, trích xuất,
  dòng trên bản xác nhận, nút Sửa và ghi chú gửi nhà thầu giữ nguyên khi khách tự nói. `state.optional_done` giữ trong
  schema cho phiên cũ nhưng không còn dùng.
- Phiên cũ đã xác nhận khi chưa có chi tiết: giữ xác nhận (chi tiết không còn bắt buộc); khái toán tính lại nếu hash
  slot khác.

## Hồ sơ và form báo giá mở ngay dưới thẻ nhà thầu

- Theo yêu cầu chủ sản phẩm: trong `contractor_list`, nút "Xem chi tiết" (và tên nhà thầu) mở hồ sơ ngay dưới thẻ;
  nút "Yêu cầu báo giá" (trên thẻ, trong hồ sơ, trên thanh "Đã chọn") mở form ngay dưới thẻ / dưới thanh chọn. Bấm lại
  ("Thu gọn chi tiết" / "Thu gọn báo giá") để đóng. Không còn thêm tin nhắn ở cuối hội thoại. Hồ sơ mở từ chỗ khác (bảng
  so sánh, block `contractor_detail` backend gửi) vẫn là tin nhắn, nhưng nút "Yêu cầu báo giá" trong hồ sơ đó cũng mở
  form ngay dưới hồ sơ.
- Đây là trạng thái giao diện, không phải tin nhắn cục bộ: tải lại trang thì hồ sơ đóng lại (thay phần "hồ sơ" của
  W-F8). Mã DZ-… của các form này lưu riêng ở localStorage, khóa `<khóa phiên>:quotes:<session_id>`, dạng
  `{listKey: {khóa form: mã}}` (`state/quoteCodes.ts`; listKey = `recommendation_log_id`, hồ sơ = `detail-<id>`).
  Khóa form = id nhà thầu sắp xếp tăng dần; form gửi nhiều nhà thầu có tiền tố `b:`. Sau khi tải lại vẫn thấy thẻ
  "Đã gửi yêu cầu báo giá" ngay dưới thẻ nhà thầu / dưới danh sách; mở lại form đã gửi chỉ hiện thẻ thành công (không
  gửi trùng). Khóa bị xóa cùng `:local:` khi xóa lịch sử, xóa một cuộc trò chuyện hoặc phiên hết hạn (4xx).
- Thanh "Đã chọn": chọn 1 nhà thầu thì "Yêu cầu báo giá" mở form dưới thẻ của nhà thầu đó; chọn ≥ 2 thì mở form chung
  dưới danh sách. Đổi lựa chọn (tích/bỏ tích trên thẻ, "Bỏ chọn") thì form chung đóng, tránh gửi nhầm nhà thầu.
- Đổi phiên (New chat, Recent chats) thì danh sách tin nhắn mount lại (`key = session_id`), nên trạng thái mở/đóng và mã
  báo giá của phiên trước không mang sang phiên sau.
- `dezon-widget.js` (tên file cố định) chuyển từ `max-age=300` sang `no-cache` (trình duyệt hỏi lại bằng ETag): bản build
  mới tới khách ngay, không còn tình trạng 5 phút đầu vẫn chạy bản cũ.
- Bảng so sánh cũng mở tại chỗ: nút "So sánh N nhà thầu trong bảng" mở bảng ngay dưới nút (trên thẻ đầu tiên), nút
  "So sánh" của thanh "Đã chọn" mở bảng ngay dưới thanh; bấm lại ("Thu gọn bảng so sánh" / "Thu gọn so sánh") để đóng.
  Đổi lựa chọn thì bảng của thanh "Đã chọn" đóng. Bấm tên nhà thầu trong bảng này mở hồ sơ dưới thẻ của nhà thầu đó và
  cuộn tới thẻ. Bảng là trạng thái giao diện (tải lại trang thì đóng). Khách gõ "so sánh ..." trong chat thì vẫn trả
  bảng thành tin nhắn như cũ.
- Thứ tự hiển thị trong một tin nhắn trợ lý (`utils/messageLayout.ts`, chỉ frontend, áp dụng cả tin cũ): mỗi đoạn chữ
  đứng ngay trên block nó nói tới. Đoạn giới thiệu nhà thầu (có "nhà thầu" + "đề xuất"/"giới thiệu", không phải đoạn
  đầu) -> ngay trên `contractor_list`; đoạn cuối là câu hỏi (kết thúc "?" hoặc có "cho em biết", vd `BUDGET_TAIL`) ->
  ngay trên `quick_replies` cuối tin. Không tách được thì giữ thứ tự cũ (chữ trước, block sau). Tin đang stream vẫn
  chữ trước block sau; block key theo chỉ số gốc nên đổi thứ tự khi nhận xong không mount lại block.
