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
  (thiếu kinh nghiệm/đơn giá). Slug/URL hồ sơ (`/nha-thau/<slug>`, `/du-an/<slug>`) là **giả định**,
  cần thay bằng URL thật khi đồng bộ. Ảnh dự án dev là ảnh giữ chỗ (placehold.co), caption ghi "ảnh minh họa dev".
- Tỉnh lân cận dùng tên tỉnh trước sáp nhập 2025; cần cập nhật khi Dezon chuẩn hóa danh mục tỉnh mới.

## AI

- `LLM_PROVIDER=mock` (mặc định): provider giả lập có kịch bản, chạy được không cần API key — dùng cho test và demo.
  `LLM_PROVIDER=anthropic`: Claude qua Anthropic SDK (model mặc định `claude-sonnet-5-5`).
- Embedding: `EMBEDDING_PROVIDER=hash` (băm đặc trưng cục bộ, 1536 chiều, xác định) cho dev/test;
  `openai` khi có key. RAG chỉ dùng cho mô tả, không cho số liệu.

## Cần hỏi lại Dezon (không tự đoán)

- Có quyền truy cập DB/API nội bộ không? Schema hiện tại?
- Bộ hệ số khái toán chính thức (thay cho seed 6.2).
- Bảng đơn giá chính thức theo khu vực/phạm vi/mức chất lượng.
- Backend ưu tiên Go hay Python?
- Nhà cung cấp LLM và model được chọn; nhà cung cấp embedding.
- Quy trình chuyển yêu cầu báo giá tới nhà thầu (email, Zalo, dashboard nhà thầu?).
- Danh mục tỉnh/thành sau sáp nhập 2025 mà Dezon dùng.
