# DEZON AI CONSTRUCTION CONSULTANT — ĐẶC TẢ TRIỂN KHAI

> **Dành cho AI coding agent.** Đọc toàn bộ file này trước khi viết code. Làm theo đúng thứ tự trong mục 14 (Kế hoạch thực thi). Khi đặc tả chưa rõ, chọn phương án đơn giản nhất thỏa mãn **Nguyên tắc bắt buộc (mục 2)** và ghi giả định vào `docs/DECISIONS.md`.

---

## 0. Tóm tắt

Xây dựng **AI Trợ lý khái toán xây dựng và tư vấn chọn nhà thầu** cho website **dezon.vn**. Đây là một **quy trình tư vấn có dữ liệu, có tính toán và có bảng so sánh**, không phải cửa sổ hỏi đáp AI thông thường.

Hệ thống phải làm được 3 việc:

1. **Khái toán chi phí xây dựng**: khách mô tả nhu cầu (loại công trình, diện tích, số tầng, mức hoàn thiện…), hệ thống trả về khoảng ngân sách dự kiến **bằng công thức**, không phải do LLM tự nghĩ.
2. **Tìm kiếm và so sánh nhà thầu**: dựa trên ngân sách, khu vực, loại công trình, kinh nghiệm và dự án thực tế để đề xuất 3–5 nhà thầu phù hợp, kèm lý do.
3. **Chứng minh độ tin cậy**: mỗi con số, thông tin nhà thầu và dự án đều có nguồn dữ liệu cụ thể trên Dezon (URL + thời điểm cập nhật + trạng thái xác minh) để khách tự kiểm chứng.

Phân vai cốt lõi:

| Thành phần | Làm gì | KHÔNG làm gì |
|---|---|---|
| LLM | Hiểu ngôn ngữ tự nhiên, hỏi thông tin còn thiếu, gọi tool, giải thích, trình bày | Tự tạo con số, đơn giá, số công trình, nhân sự |
| SQL | Truy vấn số lượng công trình, đơn giá, khu vực | — |
| RAG (pgvector) | Tìm kiếm và đọc hiểu hồ sơ, mô tả dự án, tài liệu | Làm nguồn cho số liệu |
| Calculation Engine | Tính khái toán theo công thức + hệ số | — |
| Matching Engine | Chấm điểm, xếp hạng nhà thầu theo luật | — |

> **Không xây dựng hệ thống RAG thuần túy.** Phải kết hợp đủ 5 thành phần trên.

---

## 1. Phạm vi theo phiên bản

| Phiên bản | Tính năng | Ưu tiên |
|---|---|---|
| **MVP** | Chatbot hỏi nhu cầu, khái toán, tìm nhà thầu, bảng so sánh, ảnh + link hồ sơ gốc | **Làm trước** |
| V2 | Hiển thị đầy đủ ảnh dự án, nguồn dữ liệu chi tiết, phân tích báo giá nhà thầu, tải PDF | Tiếp theo |
| V3 | Upload bản vẽ, đọc BOQ, bóc tách khối lượng, phân tích tài chính, tối ưu vật liệu | Phát triển sau |
| **Mở rộng toàn quốc (Vua Thầu)** | Giai đoạn 5–9 (mục 16): tăng tốc phản hồi, phủ dữ liệu 34 tỉnh/thành, phân phối lead tới nhà thầu, đa kênh, tối ưu bằng dữ liệu | Sau MVP, chạy song song V2 |

**MVP gồm đúng 5 chức năng:**
1. Thu thập thông tin công trình (hội thoại từng bước).
2. Tính khái toán bằng công thức.
3. Tìm kiếm 3–5 nhà thầu phù hợp.
4. So sánh bằng bảng.
5. Hiển thị hình ảnh và link hồ sơ/dự án gốc.

Chỉ làm V2/V3 khi MVP đã đạt tiêu chí nghiệm thu (mục 13).

---

## 2. Nguyên tắc bắt buộc (guardrails)

Các nguyên tắc này phải được thực thi **bằng code** (validator, schema, test), không chỉ bằng prompt.

1. **Không tự tạo đơn giá.** Mọi đơn giá phải đến từ `construction_rates` hoặc `contractors` qua tool.
2. **Không tự tạo số lượng công trình.**
3. **Không tự tạo thông tin nhân sự, chứng chỉ, năm thành lập.**
4. **Không đánh đồng "số dự án trên Dezon" với "số dự án đã hoàn thành".** Luôn ghi nhãn "công trình ghi nhận trên Dezon".
5. **Không dùng dữ liệu cũ mà không thông báo thời điểm cập nhật.** Mỗi dữ liệu hiển thị kèm `updated_at`. Dữ liệu cũ hơn `STALE_DAYS` (mặc định 180) hiển thị cảnh báo.
6. **Không có dữ liệu thì phải nói rõ**: hiển thị đúng chuỗi `"Chưa có dữ liệu"`. Không để AI tự điền.
7. **Mọi đề xuất nhà thầu phải có lý do và nguồn dẫn chứng.**
8. **Không khẳng định "nhà thầu tốt nhất"** khi Dezon chưa có dữ liệu đánh giá chất lượng thi công sau bàn giao. Dùng "phù hợp nhất với tiêu chí của anh/chị".
9. **Khái toán không phải báo giá.** Mọi bảng khái toán kèm ghi chú miễn trừ (mục 6.6).
10. **Thứ tự ưu tiên nguồn dữ liệu:**
    1. Database/API chính thức của Dezon.
    2. Dữ liệu do nhà thầu cung cấp và được Dezon xác minh.
    3. Dữ liệu crawl từ trang công khai trên Dezon.
    4. Dữ liệu do đội ngũ Dezon tự biên soạn và phê duyệt.

    Crawler chỉ là phương án bổ sung khi chưa có API/DB. Không lấy HTML làm nguồn chính nếu Dezon đã có database.

### 2.1 Cơ chế thực thi guardrail (Evidence-based AI)

- Mọi tool trả về dữ liệu dạng `EvidenceValue` (mục 4.3). LLM chỉ được trích các giá trị có trong kết quả tool.
- **Output validator** chạy sau mỗi câu trả lời của LLM:
  - Trích mọi số tiền (`tỷ`, `triệu`, `triệu/m²`), số công trình, số năm, số nhân sự trong câu trả lời bằng regex.
  - Đối chiếu với tập giá trị trong các tool result của lượt đó (cho phép làm tròn ±1% và định dạng khác nhau như `4,3` vs `4.3`).
  - Nếu có số không truy được nguồn → chặn, yêu cầu LLM sinh lại một lần với thông báo lỗi; lần hai vẫn sai → thay bằng câu trả lời dự phòng chỉ dựng từ dữ liệu tool (template).
  - Ghi lỗi vào `guardrail_violations`.
- Các khối UI có cấu trúc (bảng khái toán, thẻ nhà thầu, bảng so sánh) được **render từ JSON của tool**, không từ văn bản LLM. LLM chỉ viết đoạn giải thích xung quanh.

---

## 3. Kiến trúc hệ thống

```
┌─────────────────────────────────────────────────────────────┐
│ Nguồn dữ liệu Dezon                                          │
│ /nha-thau · /du-an · /planning · /chon-thau · hồ sơ nhà thầu │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Data Ingestion — Đồng bộ dữ liệu                             │
│  Database/API (ưu tiên) │ Web Crawler (trang công khai)      │
│  Excel/CSV (nhập riêng) │ Admin cập nhật (kiểm duyệt)        │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Dezon Structured Data — PostgreSQL + pgvector                │
│ Nhà thầu · Dự án · Đơn giá · Báo giá · Nguồn dữ liệu         │
└───────────────┬───────────────────────────────┬─────────────┘
                ▼                               ▼
┌───────────────────────────┐   ┌─────────────────────────────┐
│ Calculation Engine        │   │ AI + RAG                    │
│ Khái toán bằng công thức  │   │ Hiểu câu hỏi, truy xuất     │
└───────────────┬───────────┘   └───────────────┬─────────────┘
                └───────────────┬───────────────┘
                                ▼
┌─────────────────────────────────────────────────────────────┐
│ AI Consultant API                                            │
│ Matching nhà thầu · Giải thích · Bảng so sánh · Nguồn        │
└──────────────────────────────┬──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ Giao diện Chatbot trên Dezon                                 │
│ Chat · Bảng khái toán · Nhà thầu · Dự án · Yêu cầu báo giá   │
└─────────────────────────────────────────────────────────────┘
```

### 3.1 Công nghệ

| Thành phần | Công nghệ | Ghi chú |
|---|---|---|
| Frontend | React + TypeScript (widget nhúng vào dezon.vn) | Build thành 1 bundle nhúng được bằng `<script>` |
| Backend | **FastAPI (Python 3.12)** | Mặc định FastAPI vì dùng chung Python với crawler. Nếu Dezon yêu cầu Go, giữ nguyên hợp đồng API |
| Database | PostgreSQL 16 | |
| Vector Search | pgvector | Embedding cho hồ sơ, mô tả dự án |
| AI | LLM có function calling | Bọc qua interface `LLMProvider` để đổi nhà cung cấp |
| RAG | Truy xuất hồ sơ, dự án, tài liệu | |
| Calculation | Module tính toán độc lập, thuần hàm, có unit test | `app/engines/estimate/` |
| Matching | Rule-based scoring + ranking | `app/engines/matching/` |
| Crawler | Python Playwright / BeautifulSoup | Chỉ dùng khi chưa có API |
| Background Jobs | Redis Queue (RQ) | Đồng bộ, embedding, crawl |
| Storage | S3-compatible | Hình ảnh, bản vẽ, tài liệu |

### 3.2 Cấu trúc thư mục

```
dezon-ai-consultant/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── db/ (models.py, session.py, migrations/ – Alembic)
│   │   ├── api/ (chat.py, estimates.py, contractors.py, quotes.py, admin.py)
│   │   ├── engines/
│   │   │   ├── estimate/ (rules.py, calculator.py, explain.py)
│   │   │   └── matching/ (criteria.py, scorer.py, reasons.py)
│   │   ├── ai/
│   │   │   ├── orchestrator.py      # vòng lặp LLM + tools
│   │   │   ├── tools.py             # định nghĩa & thực thi tool
│   │   │   ├── slot_filling.py      # trích & theo dõi thông tin còn thiếu
│   │   │   ├── validator.py         # output guardrail
│   │   │   ├── prompts/system_vi.md
│   │   │   └── providers/
│   │   ├── rag/ (embed.py, retrieve.py)
│   │   ├── ingestion/ (api_sync.py, crawler/, csv_import.py)
│   │   └── evidence.py              # EvidenceValue, trạng thái xác minh
│   └── tests/
├── frontend/
│   └── src/ (widget/, components/, api/)
├── docs/ (DECISIONS.md, DATA_MAPPING.md)
├── docker-compose.yml
└── README.md
```

---

## 4. Mô hình dữ liệu

### 4.1 Danh sách bảng

| Bảng | Dữ liệu |
|---|---|
| `contractors` | Hồ sơ nhà thầu |
| `contractor_capabilities` | Năng lực, loại công trình, khu vực |
| `projects` | Các công trình thực tế |
| `project_contractors` | Quan hệ nhà thầu – công trình |
| `project_images` | Ảnh công trình |
| `construction_rates` | Đơn giá theo khu vực, loại công trình, phạm vi, mức chất lượng |
| `estimate_rules` | Hệ số diện tích, công thức khái toán |
| `estimate_sessions` | Các lần khách hàng khái toán |
| `estimate_items` | Chi tiết từng hạng mục chi phí |
| `contractor_quotes` | Báo giá từ nhà thầu |
| `quote_requests` | Yêu cầu báo giá khách gửi |
| `data_sources` | URL, nguồn, thời điểm cập nhật, trạng thái xác minh |
| `recommendation_logs` | Lý do và kết quả đề xuất nhà thầu |
| `chat_sessions`, `chat_messages` | Lịch sử hội thoại, slot đã thu thập |
| `documents` | Chunk văn bản + embedding cho RAG |
| `guardrail_violations` | Log vi phạm nguyên tắc |
| `matching_weights` | Trọng số matching (có phiên bản) |

### 4.2 DDL (PostgreSQL)

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TYPE verification_status AS ENUM ('verified','on_dezon','reference','unavailable');
CREATE TYPE source_type AS ENUM ('dezon_api','contractor_verified','crawl_public','dezon_editorial','csv_import');
CREATE TYPE building_type AS ENUM ('nha_pho','biet_thu','can_ho','van_phong','khach_san','khac');
CREATE TYPE work_scope AS ENUM ('xay_tho','hoan_thien','tron_goi','noi_that');
CREATE TYPE quality_tier AS ENUM ('tiet_kiem','trung_binh','kha','cao_cap');

CREATE TABLE data_sources (
  id            BIGSERIAL PRIMARY KEY,
  source_type   source_type NOT NULL,
  url           TEXT,                       -- link để khách tự kiểm chứng
  label         TEXT NOT NULL,              -- vd "Hồ sơ nhà thầu Kiến Phong"
  status        verification_status NOT NULL DEFAULT 'on_dezon',
  fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  source_updated_at TIMESTAMPTZ,            -- ngày cập nhật thực tế trên nguồn (nếu có)
  verified_by   TEXT,
  verified_at   TIMESTAMPTZ,
  raw_hash      TEXT                        -- phát hiện thay đổi khi đồng bộ lại
);

CREATE TABLE contractors (
  id              BIGSERIAL PRIMARY KEY,
  external_id     TEXT UNIQUE,              -- id/slug bên Dezon
  slug            TEXT UNIQUE NOT NULL,
  name            TEXT NOT NULL,
  badge           TEXT,                     -- vd "Nhà thầu tiềm năng"
  province        TEXT,                     -- tỉnh/thành chính
  founded_year    INT,
  years_experience INT,
  staff_workers   INT,                      -- thợ
  staff_office    INT,                      -- văn phòng
  rough_price_min NUMERIC(12,0),            -- VND/m² xây thô
  rough_price_max NUMERIC(12,0),
  finish_price_min NUMERIC(12,0),           -- VND/m² hoàn thiện cơ bản
  finish_price_max NUMERIC(12,0),
  typical_project_value NUMERIC(16,0),      -- quy mô dự án điển hình (VND)
  dezon_project_count INT,                  -- công trình GHI NHẬN trên Dezon
  has_warranty_info BOOLEAN,
  warranty_text   TEXT,
  description     TEXT,
  avatar_url      TEXT,
  profile_url     TEXT NOT NULL,            -- link hồ sơ gốc trên dezon.vn
  profile_completeness NUMERIC(4,3),        -- 0..1, tính tự động (mục 7.3)
  is_active       BOOLEAN NOT NULL DEFAULT true,
  source_id       BIGINT REFERENCES data_sources(id),
  field_sources   JSONB NOT NULL DEFAULT '{}'::jsonb, -- {"rough_price_min": <data_source_id>, ...}
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON contractors USING gin (name gin_trgm_ops);

CREATE TABLE contractor_capabilities (
  id             BIGSERIAL PRIMARY KEY,
  contractor_id  BIGINT NOT NULL REFERENCES contractors(id) ON DELETE CASCADE,
  building_type  building_type,
  work_scope     work_scope,
  province       TEXT,                      -- khu vực hoạt động
  district       TEXT,
  source_id      BIGINT REFERENCES data_sources(id)
);
CREATE INDEX ON contractor_capabilities (province, building_type);

CREATE TABLE projects (
  id             BIGSERIAL PRIMARY KEY,
  external_id    TEXT UNIQUE,
  slug           TEXT UNIQUE NOT NULL,
  name           TEXT NOT NULL,             -- vd "Green Pool Villa Vĩnh Long"
  building_type  building_type,
  province       TEXT,
  district       TEXT,
  floors         INT,
  land_area_m2   NUMERIC(10,2),
  floor_area_m2  NUMERIC(10,2),
  start_date     DATE,
  end_date       DATE,
  status         TEXT,                      -- 'designing'|'constructing'|'completed'|NULL
  description    TEXT,
  project_url    TEXT NOT NULL,
  source_id      BIGINT REFERENCES data_sources(id),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE project_contractors (
  project_id     BIGINT REFERENCES projects(id) ON DELETE CASCADE,
  contractor_id  BIGINT REFERENCES contractors(id) ON DELETE CASCADE,
  role           TEXT,                      -- 'thi_cong'|'thiet_ke'|'noi_that'
  PRIMARY KEY (project_id, contractor_id, role)
);

CREATE TABLE project_images (
  id          BIGSERIAL PRIMARY KEY,
  project_id  BIGINT REFERENCES projects(id) ON DELETE CASCADE,
  url         TEXT NOT NULL,
  caption     TEXT,
  sort_order  INT DEFAULT 0
);

CREATE TABLE construction_rates (
  id             BIGSERIAL PRIMARY KEY,
  building_type  building_type NOT NULL,
  province       TEXT NOT NULL,             -- '*' = toàn quốc mặc định
  work_scope     work_scope NOT NULL,
  quality_tier   quality_tier NOT NULL DEFAULT 'trung_binh',
  price_min      NUMERIC(12,0) NOT NULL,    -- VND/m²
  price_max      NUMERIC(12,0) NOT NULL,
  effective_from DATE NOT NULL,
  effective_to   DATE,
  source_id      BIGINT NOT NULL REFERENCES data_sources(id),
  UNIQUE (building_type, province, work_scope, quality_tier, effective_from)
);

CREATE TABLE estimate_rules (
  id          BIGSERIAL PRIMARY KEY,
  code        TEXT NOT NULL,                -- vd 'foundation_pile'
  group_name  TEXT NOT NULL,                -- 'mong'|'tang'|'ham'|'mai'|'san_thuong'|'ban_cong'|'san'
  label_vi    TEXT NOT NULL,
  coefficient NUMERIC(5,3) NOT NULL,        -- k_i
  base        TEXT NOT NULL,                -- 'ground_floor_area'|'floor_area'|'roof_area'|...
  is_default  BOOLEAN NOT NULL DEFAULT false,
  version     INT NOT NULL DEFAULT 1,
  approved    BOOLEAN NOT NULL DEFAULT false, -- chỉ dùng rule đã duyệt trên production
  source_id   BIGINT REFERENCES data_sources(id),
  UNIQUE (code, version)
);

CREATE TABLE chat_sessions (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     TEXT,
  slots       JSONB NOT NULL DEFAULT '{}'::jsonb,  -- thông tin đã thu thập (mục 5.2)
  stage       TEXT NOT NULL DEFAULT 'collect',     -- collect|estimate|match|present
  created_at  TIMESTAMPTZ DEFAULT now(),
  updated_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE chat_messages (
  id          BIGSERIAL PRIMARY KEY,
  session_id  UUID REFERENCES chat_sessions(id) ON DELETE CASCADE,
  role        TEXT NOT NULL,                -- user|assistant|tool
  content     TEXT,
  tool_name   TEXT,
  tool_payload JSONB,
  ui_blocks   JSONB,                        -- khối UI có cấu trúc trả về frontend
  created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE estimate_sessions (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  chat_session_id UUID REFERENCES chat_sessions(id),
  input           JSONB NOT NULL,           -- EstimateInput
  rules_version   INT NOT NULL,
  rate_id         BIGINT REFERENCES construction_rates(id),
  converted_area_m2 NUMERIC(10,2) NOT NULL,
  cost_min        NUMERIC(16,0) NOT NULL,
  cost_max        NUMERIC(16,0) NOT NULL,
  assumptions     JSONB NOT NULL,
  created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE estimate_items (
  id                 BIGSERIAL PRIMARY KEY,
  estimate_session_id UUID REFERENCES estimate_sessions(id) ON DELETE CASCADE,
  rule_code          TEXT NOT NULL,
  label_vi           TEXT NOT NULL,
  area_m2            NUMERIC(10,2) NOT NULL, -- A_i
  coefficient        NUMERIC(5,3) NOT NULL,  -- k_i
  converted_m2       NUMERIC(10,2) NOT NULL  -- A_i × k_i
);

CREATE TABLE quote_requests (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  estimate_session_id UUID REFERENCES estimate_sessions(id),
  contractor_id       BIGINT REFERENCES contractors(id),
  customer_name       TEXT, customer_phone TEXT, customer_email TEXT,
  note                TEXT,
  status              TEXT NOT NULL DEFAULT 'new',  -- new|sent|quoted|won|lost
  created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE contractor_quotes (
  id               BIGSERIAL PRIMARY KEY,
  quote_request_id UUID REFERENCES quote_requests(id),
  contractor_id    BIGINT REFERENCES contractors(id),
  total_amount     NUMERIC(16,0),
  price_per_m2     NUMERIC(12,0),
  scope_text       TEXT,
  file_url         TEXT,
  received_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE matching_weights (
  version     INT PRIMARY KEY,
  weights     JSONB NOT NULL,               -- xem mục 7.1
  is_active   BOOLEAN NOT NULL DEFAULT false,
  note        TEXT,
  created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE recommendation_logs (
  id                  BIGSERIAL PRIMARY KEY,
  chat_session_id     UUID REFERENCES chat_sessions(id),
  estimate_session_id UUID REFERENCES estimate_sessions(id),
  weights_version     INT REFERENCES matching_weights(version),
  criteria            JSONB NOT NULL,
  results             JSONB NOT NULL,       -- [{contractor_id, score, breakdown, reasons, rank}]
  clicked_contractor_ids BIGINT[] DEFAULT '{}',
  created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE documents (
  id          BIGSERIAL PRIMARY KEY,
  entity_type TEXT NOT NULL,                -- contractor|project|article
  entity_id   BIGINT NOT NULL,
  chunk_index INT NOT NULL,
  content     TEXT NOT NULL,
  embedding   vector(1536),                 -- chỉnh theo model embedding
  source_id   BIGINT REFERENCES data_sources(id),
  updated_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON documents USING hnsw (embedding vector_cosine_ops);

CREATE TABLE guardrail_violations (
  id          BIGSERIAL PRIMARY KEY,
  session_id  UUID,
  rule        TEXT NOT NULL,
  detail      JSONB,
  created_at  TIMESTAMPTZ DEFAULT now()
);
```

### 4.3 EvidenceValue — định dạng chuẩn mọi tool trả về

```json
{
  "value": [4300000, 4700000],
  "display": "4,3–4,7 triệu/m²",
  "status": "on_dezon",
  "source": {
    "label": "Hồ sơ nhà thầu Kiến Phong",
    "url": "https://dezon.vn/...",
    "updated_at": "2026-10-31"
  },
  "is_stale": false
}
```

Khi không có dữ liệu:

```json
{ "value": null, "display": "Chưa có dữ liệu", "status": "unavailable", "source": null, "is_stale": false }
```

### 4.4 Bốn trạng thái xác minh

| Mã | Nhãn hiển thị (badge) | Màu | Ý nghĩa |
|---|---|---|---|
| `verified` | Đã xác minh | xanh lá | Được Dezon hoặc nguồn có thẩm quyền kiểm tra |
| `on_dezon` | Có dữ liệu trên Dezon | xanh dương | Có hồ sơ/dự án được ghi nhận nhưng chưa đồng nghĩa đã xác minh độc lập |
| `reference` | Dữ liệu tham khảo | cam | Dữ liệu thị trường hoặc do nhà thầu cung cấp chưa được kiểm chứng đầy đủ |
| `unavailable` | Chưa có dữ liệu | xám | Không đủ thông tin để đưa ra kết luận |

Ánh xạ mặc định từ `source_type`: `dezon_api` → `on_dezon` (nâng lên `verified` khi admin duyệt); `contractor_verified` → `verified`; `crawl_public` → `on_dezon`; `dezon_editorial` → `verified` nếu `approved`, ngược lại `reference`; `csv_import` → `reference`.

---

## 5. Giai đoạn 1 — Thu thập nhu cầu (hội thoại như chuyên gia tư vấn)

### 5.1 Nguyên tắc hội thoại

- **Không bắt khách điền biểu mẫu dài.** AI hỏi từng câu, dựa vào câu trả lời trước để hỏi câu tiếp theo.
- Khách **không cần cung cấp toàn bộ thông tin ngay**. Nếu khách nói một câu chứa nhiều thông tin ("Tôi muốn xây nhà phố 5x20 ở Thủ Đức, 1 trệt 3 lầu, ngân sách 2 tỷ"), AI nhận diện tất cả và chỉ hỏi trường còn thiếu.
- Mỗi lượt hỏi **tối đa 1–2 thông tin**, kèm **nút chọn nhanh** (quick replies) và lựa chọn "Nhập thông tin khác".
- Xưng hô: "em" – "anh/chị", lịch sự, ngắn gọn.

### 5.2 Các slot cần thu thập

| Nhóm | Slot | Kiểu | Bắt buộc để khái toán | Quick replies gợi ý |
|---|---|---|---|---|
| Công trình | `building_type` | enum | ✅ | Nhà phố · Biệt thự · Căn hộ · Văn phòng · Khách sạn |
| Vị trí | `province`, `district` | text | ✅ (`province`) | TP. Hồ Chí Minh · Hà Nội · Đà Nẵng · Khác |
| Quy mô | `land_width_m`, `land_length_m` hoặc `land_area_m2` | number | ✅ | 4×16 m · 5×20 m · Nhập khác |
| Quy mô | `building_area_m2` (diện tích xây dựng/tầng) | number | ⛔ (mặc định = diện tích đất, ghi giả định) | |
| Quy mô | `floors` (số tầng nổi, tính cả trệt) | int | ✅ | 1 trệt 2 lầu · 1 trệt 3 lầu · Nhập khác |
| Quy mô | `has_mezzanine`, `basement`, `roof_type`, `rooftop_terrace`, `foundation_type` | mixed | ⛔ (có mặc định) | |
| Phạm vi | `work_scope` | enum | ✅ | Xây thô · Hoàn thiện · Trọn gói · Nội thất |
| Chất lượng | `quality_tier` | enum | ⛔ (mặc định `trung_binh`) | Tiết kiệm · Trung bình · Khá · Cao cấp |
| Ngân sách | `budget_vnd` | number | ⛔ (cần cho matching) | Dưới 1,5 tỷ · 1,5–2,5 tỷ · 2,5–4 tỷ · Trên 4 tỷ |
| Thời gian | `start_time` | text | ⛔ | Trong 3 tháng · 3–6 tháng · Chưa xác định |
| Tài liệu | `attachments` | file[] | ⛔ (V3 mới xử lý) | Bản vẽ, ảnh mẫu, BOQ |

Thứ tự hỏi mặc định: `building_type` → `province` → quy mô (`land`, `floors`) → `work_scope` → `quality_tier` → `budget_vnd` → `start_time`. Bỏ qua slot đã có.

### 5.3 Slot filling

- `slot_filling.extract(message, current_slots)` dùng LLM với **JSON schema bắt buộc** (structured output) để trích slot; sau đó **chuẩn hóa bằng code**:
  - "5x20", "5 x 20m", "ngang 5 dài 20" → `land_width_m=5, land_length_m=20, land_area_m2=100`.
  - "1 trệt 3 lầu" → `floors=4`; "1 trệt 1 lửng 2 lầu" → `floors=3, has_mezzanine=true`.
  - "2 tỷ", "2.000 triệu", "2 tỉ" → `budget_vnd=2000000000`.
  - "Sài Gòn", "HCM", "TPHCM" → `province="TP. Hồ Chí Minh"`; "Thủ Đức" → `district="TP. Thủ Đức"`.
- Viết unit test cho từng mẫu chuẩn hóa ở trên.
- Khi đủ slot bắt buộc → chuyển `stage` sang `estimate`, tóm tắt lại thông tin cho khách xác nhận trước khi tính.

### 5.4 Ví dụ luồng

```
AI:   Xin chào anh/chị! Em có thể hỗ trợ khái toán chi phí xây dựng và tìm nhà thầu
      phù hợp trên Dezon. Anh/chị đang có nhu cầu xây dựng loại công trình nào ạ?
      [Nhà phố] [Biệt thự] [Căn hộ] [Văn phòng] [Khác]
Khách: Tôi muốn xây nhà phố.
AI:   Dạ, anh/chị dự định xây nhà ở khu vực nào?
      [TP. Hồ Chí Minh] [Hà Nội] [Đà Nẵng] [Nhập khác]
Khách: TP. Hồ Chí Minh.
AI:   Anh/chị cho em xin thêm diện tích đất và số tầng dự kiến nhé.
      [5 × 20 m] [1 trệt 2 lầu] [1 trệt 3 lầu] [Nhập thông tin khác]
...
AI:   Em tóm tắt lại: Nhà phố tại TP.HCM, đất 5×20 m (100 m²), 1 trệt 3 lầu, xây thô,
      mức trung bình. Anh/chị xác nhận để em tính khái toán nhé? [Đúng rồi] [Sửa lại]
```

---

## 6. Giai đoạn 2 — Calculation Engine (khái toán chi phí)

> **LLM không bao giờ tự tính chi phí.** LLM gọi tool `calculate_estimate`; engine trả về kết quả; LLM chỉ giải thích.

### 6.1 Công thức

$$A_{\text{quy đổi}} = \sum_i (A_i \times k_i)$$

$$C_{\min} = A_{\text{quy đổi}} \times P_{\min}, \qquad C_{\max} = A_{\text{quy đổi}} \times P_{\max}$$

- $A_i$: diện tích từng hạng mục (m²).
- $k_i$: hệ số diện tích theo quy tắc khái toán của Dezon (`estimate_rules`).
- $P$: đơn giá tham chiếu theo loại công trình, khu vực, phạm vi thi công, mức chất lượng (`construction_rates`).

### 6.2 Hệ số mặc định (seed — **cần Dezon duyệt** trước khi bật `approved=true`)

Các giá trị dưới đây là thông lệ phổ biến ở thị trường xây dựng nhà ở Việt Nam, dùng để seed môi trường dev. Đánh dấu `approved=false` và nguồn `dezon_editorial`. Production chỉ dùng rule `approved=true`; nếu chưa có rule duyệt, hệ thống vẫn tính nhưng gắn trạng thái `reference` và hiển thị cảnh báo.

| code | Nhóm | Hạng mục | k | Diện tích gốc |
|---|---|---|---|---|
| `floor_standard` | tang | Tầng trệt / lầu (có mái che) | 1.00 | diện tích sàn mỗi tầng |
| `mezzanine` | tang | Tầng lửng | 1.00 | diện tích sàn lửng |
| `foundation_single` | mong | Móng đơn | 0.30 | diện tích xây dựng trệt |
| `foundation_strip` | mong | Móng băng | 0.50 | diện tích xây dựng trệt |
| `foundation_pile` | mong | Móng cọc (chưa gồm ép cọc) | 0.40 | diện tích xây dựng trệt |
| `basement_lt_1_3` | ham | Hầm sâu < 1,3 m | 1.50 | diện tích hầm |
| `basement_1_3_1_7` | ham | Hầm sâu 1,3–1,7 m | 1.70 | diện tích hầm |
| `basement_1_7_2_0` | ham | Hầm sâu 1,7–2,0 m | 2.00 | diện tích hầm |
| `basement_gt_2_0` | ham | Hầm sâu > 2,0 m | 2.50 | diện tích hầm |
| `roof_metal` | mai | Mái tôn | 0.30 | diện tích mái |
| `roof_concrete` | mai | Mái bê tông cốt thép | 0.50 | diện tích mái |
| `roof_tile_on_truss` | mai | Mái ngói kèo thép | 0.70 | diện tích mái |
| `roof_tile_on_concrete` | mai | Mái ngói đổ BTCT | 1.00 | diện tích mái |
| `rooftop_covered` | san_thuong | Sân thượng có mái che | 0.70 | diện tích sân thượng |
| `rooftop_open` | san_thuong | Sân thượng không mái | 0.50 | diện tích sân thượng |
| `yard` | san | Sân trước/sau | 0.50 | diện tích sân |

Mặc định khi khách không cung cấp: `foundation_type = foundation_strip` (nhà phố ≤ 3 tầng nổi) hoặc `foundation_pile` (≥ 4 tầng nổi); `roof_type = roof_concrete`; không hầm; `building_area_m2 = land_area_m2`; diện tích mái = diện tích xây dựng. **Mọi mặc định đều ghi vào `assumptions` và hiển thị cho khách.**

### 6.3 Thuật toán

```python
def calculate_estimate(inp: EstimateInput, rules: RuleSet, rate: Rate) -> EstimateResult:
    items = []
    ground = inp.building_area_m2 or inp.land_area_m2
    items.append(Item("foundation_*", ground, rules[inp.foundation_type]))
    for floor in range(inp.floors):              # trệt + các lầu
        items.append(Item("floor_standard", ground, rules["floor_standard"]))
    if inp.has_mezzanine: items.append(Item("mezzanine", inp.mezzanine_area or ground, ...))
    if inp.basement:      items.append(Item(inp.basement_rule, inp.basement_area or ground, ...))
    items.append(Item(inp.roof_type, inp.roof_area or ground, ...))
    if inp.rooftop_terrace: ...
    converted = round(sum(i.area * i.k for i in items), 2)
    return EstimateResult(
        items=items,
        converted_area_m2=converted,
        cost_min=round_to_million(converted * rate.price_min),
        cost_max=round_to_million(converted * rate.price_max),
        rate=EvidenceValue(...),                  # kèm nguồn đơn giá
        assumptions=[...], exclusions=[...], cost_drivers=[...],
    )
```

- Chọn đơn giá: khớp chính xác (`building_type`, `province`, `work_scope`, `quality_tier`) còn hiệu lực → nếu không có, fallback `province='*'` và gắn `status='reference'` + ghi giả định "dùng đơn giá mặc định toàn quốc".
- Không có đơn giá nào → trả `cost_min/max = null`, `display = "Chưa có dữ liệu"`; AI nói rõ chưa thể khái toán.
- Engine là **hàm thuần**, không gọi LLM, không truy cập mạng. Unit test bao phủ ≥ 90%.

### 6.4 Bảng khái toán hiển thị (UI block `estimate_table`)

| Hạng mục | Giá trị |
|---|---|
| Diện tích đất | 100 m² |
| Số tầng | 4 |
| Diện tích sàn quy đổi | (từ engine, kèm nút "Xem chi tiết hệ số") |
| Phạm vi | Xây thô |
| Đơn giá tham chiếu | x,x–y,y triệu/m² · badge trạng thái · link nguồn |
| Chi phí sơ bộ | a,aa–b,bb tỷ |

Bảng chi tiết mở rộng: mỗi dòng `estimate_items` (Hạng mục · A_i · k_i · A_i×k_i).

### 6.5 Phần giải thích sau khái toán

Sau bảng, AI giải thích (dựa trên `assumptions`, `exclusions`, `cost_drivers` do engine trả về, không tự bịa):

- Chi phí này **bao gồm** những gì (theo `work_scope`).
- **Chưa bao gồm** những gì (vd: ép cọc, xin phép, thiết kế, nội thất rời, điện nước ngoài nhà…).
- Những **yếu tố khiến chi phí tăng** (hẻm nhỏ, đất yếu, tầng hầm, mái ngói, ban công nhiều…).
- **Nếu ngân sách thấp hơn khái toán** (vd chỉ có 1,5 tỷ): gọi tool `suggest_budget_adjustments` — engine thử lần lượt các phương án (giảm 1 tầng, đổi mức chất lượng, bỏ hầm, đổi loại mái, chia giai đoạn xây thô trước) và trả về chi phí mới của từng phương án; AI trình bày các phương án đưa ngân sách về gần mức khách có.

### 6.6 Ghi chú miễn trừ bắt buộc dưới mọi bảng khái toán

> Đây là khái toán sơ bộ dựa trên đơn giá tham chiếu {nguồn, ngày cập nhật} và các giả định nêu trên, chưa tính điều kiện thực tế của khu đất. Không phải báo giá thi công. Vui lòng yêu cầu báo giá từ nhà thầu để có con số chính xác.

---

## 7. Giai đoạn 3 — Matching Engine (đề xuất & xếp hạng nhà thầu)

> Không chỉ lọc theo số lượng công trình. Chấm điểm theo nhiều tiêu chí.

### 7.1 Tiêu chí và trọng số khởi đầu

| Tiêu chí | Khóa | Trọng số |
|---|---|---|
| Khu vực hoạt động | `location` | 20% |
| Loại công trình đã thực hiện | `building_type` | 20% |
| Mức độ phù hợp ngân sách | `budget_fit` | 20% |
| Kinh nghiệm và năng lực | `experience` | 15% |
| Dự án thực tế tương tự | `similar_projects` | 15% |
| Mức độ đầy đủ và xác minh hồ sơ | `profile_quality` | 10% |
| **Tổng** | | **100%** |

Lưu trong `matching_weights` (version 1, active). Đây là trọng số thử nghiệm; sẽ hiệu chỉnh theo dữ liệu báo giá và kết quả thi công thực tế (mục 12). Không hard-code trọng số trong code.

### 7.2 Bộ lọc cứng (trước khi chấm điểm)

- `is_active = true`.
- Có ít nhất một trong: capability tại `province` của khách, hoặc dự án tại `province` đó.
- Nếu sau lọc < 3 nhà thầu → nới lỏng sang tỉnh lân cận (bảng `province_neighbors` cấu hình), ghi rõ trong lý do.

### 7.3 Cách tính điểm từng tiêu chí (0..1)

| Tiêu chí | Công thức |
|---|---|
| `location` | 1.0 khớp quận/huyện · 0.8 khớp tỉnh · 0.4 tỉnh lân cận · 0 |
| `building_type` | 1.0 có capability **và** có dự án cùng loại · 0.7 chỉ một trong hai · 0 |
| `budget_fit` | Tính chi phí ước theo đơn giá riêng nhà thầu: `A_quy_doi × giá nhà thầu` (min–max). 1.0 nếu khoảng này giao với `[0.9×budget, 1.1×budget]` và ≤ budget; giảm tuyến tính theo % vượt ngân sách, 0 khi vượt > 40%. Nhà thầu **không có đơn giá** → 0.5 (trung tính) và gắn cờ "Cần xác minh mức giá" |
| `experience` | `min(years_experience/10, 1)×0.6 + min(staff_total/30, 1)×0.4`; trường thiếu tính 0 cho phần đó |
| `similar_projects` | Số dự án cùng `building_type`, số tầng ±1, cùng tỉnh: `min(n/5, 1)` |
| `profile_quality` | `profile_completeness × 0.7 + (tỷ lệ trường verified) × 0.3` |

`profile_completeness` = tỷ lệ các trường có giá trị trong: `founded_year, years_experience, staff_workers, staff_office, rough_price_min, finish_price_min, description, avatar_url, dezon_project_count>0, has_warranty_info`.

**Tổng điểm** = Σ (điểm tiêu chí × trọng số) × 100, làm tròn số nguyên.

Nhãn mức phù hợp: ≥ 85 "Phù hợp cao"; 70–84 "Phù hợp"; < 70 hoặc thiếu ≥ 2 trường quan trọng (đơn giá, kinh nghiệm) → "Cần đánh giá thêm".

Trả về top 3–5. Ghi toàn bộ `breakdown` vào `recommendation_logs`.

### 7.4 Lý do đề xuất (bắt buộc)

`reasons.py` sinh **lý do có cấu trúc** từ breakdown — mỗi lý do trỏ tới một EvidenceValue:

```json
[
  {"text": "Có kinh nghiệm thi công nhà phố, biệt thự", "evidence": "capabilities"},
  {"text": "Hoạt động tại TP.HCM", "evidence": "province"},
  {"text": "7 công trình được ghi nhận trên Dezon", "evidence": "dezon_project_count"},
  {"text": "Đơn giá xây thô 4,3–4,7 triệu/m²", "evidence": "rough_price"}
]
```

LLM viết thành câu tự nhiên, ví dụ:

> Em đề xuất Kiến Phong vì đơn vị có kinh nghiệm thi công nhà phố, biệt thự tại TP.HCM, có 7 công trình được ghi nhận trên Dezon và có thông tin đơn giá xây thô từ 4,3–4,7 triệu/m². Anh/chị có thể xem các dự án thực tế để đánh giá thêm.

Điều này quan trọng hơn việc chỉ nói "điểm 92/100". Điểm số hiển thị phụ, lý do hiển thị chính.

Cũng sinh **điểm cần lưu ý** (vd "Chưa có thông tin đơn giá — cần đối chiếu", "Chưa có thông tin bảo hành").

---

## 8. Giai đoạn 4 — Trình bày kết quả và kết nối

### 8.1 Các khối UI (frontend render từ JSON `ui_blocks`)

| Block | Nội dung |
|---|---|
| `quick_replies` | Nút chọn nhanh |
| `summary_confirm` | Tóm tắt slot + nút Xác nhận/Sửa |
| `estimate_table` | Bảng khái toán + chi tiết hệ số + miễn trừ + nguồn |
| `budget_options` | Các phương án điều chỉnh ngân sách |
| `contractor_list` | Thẻ nhà thầu: ảnh, tên, nhãn phù hợp, điểm, số công trình, kinh nghiệm, đơn giá, "Xem hồ sơ năng lực ↗" |
| `comparison_table` | Bảng so sánh ngang 3–5 nhà thầu |
| `contractor_detail` | Hồ sơ mở rộng (mục 8.3) |
| `source_badges` | Chip nguồn (vd "Dezon") kèm tooltip URL + ngày cập nhật |
| `quote_request_form` | Form yêu cầu báo giá |

### 8.2 Bảng so sánh (`comparison_table`)

| Tiêu chí | Nhà thầu A | Nhà thầu B | Nhà thầu C |
|---|---|---|---|
| Khu vực | | | |
| Công trình trên Dezon | | | |
| Kinh nghiệm | | | |
| Đơn giá thô | | | |
| Đơn giá hoàn thiện | | | |
| Hồ sơ dự án | | | |
| Bảo hành | | | |
| Mức phù hợp | | | |

- Ô thiếu dữ liệu hiển thị đúng **"Chưa có dữ liệu"** (màu xám), không để trống, không để AI điền.
- Mỗi ô có badge trạng thái xác minh (hover xem nguồn).
- Bấm vào tên nhà thầu → mở `contractor_detail`.

### 8.3 Hồ sơ mở rộng (`contractor_detail`)

- Lưới ảnh dự án thực tế (3 ảnh đầu, ô cuối hiện "+N").
- Danh sách **công trình thực tế trên Dezon**: tên công trình, địa điểm, loại công trình, đơn vị thi công, thời gian thực hiện (nếu có) — mỗi dòng link tới trang dự án gốc.
- Bảng thông tin: năm thành lập, nhân sự (thợ/văn phòng), đơn giá xây thô, đơn giá hoàn thiện cơ bản, quy mô dự án, số dự án trên Dezon.
- Chip: "Hồ sơ nhà thầu" · "Dự án thực tế" · "Đơn giá tham khảo".
- Link "Mở hồ sơ đầy đủ trên Dezon ↗".
- Nút **"Yêu cầu báo giá"** → `quote_request_form` đính kèm sẵn `estimate_session_id`.

### 8.4 Trả lời câu hỏi tự do

Ví dụ khách hỏi: *"Nhà thầu nào thi công biệt thự tốt tại TP.HCM?"* — chatbot phải trả lời:
- Có bao nhiêu nhà thầu phù hợp.
- Nhà thầu nào có công trình biệt thự thực tế.
- Có bao nhiêu công trình được ghi nhận.
- Đơn giá tham khảo.
- Có thông tin bảo hành hay không.
- Hồ sơ nào đã được xác minh.
- Link xem công trình thực tế.

Và **không** khẳng định "tốt nhất" (nguyên tắc 2.8).

---

## 9. AI Orchestrator

### 9.1 Vòng lặp

```
user message
  → slot_filling.extract → cập nhật chat_sessions.slots
  → orchestrator gọi LLM (system prompt + slots + lịch sử rút gọn + danh sách tools)
  → LLM gọi tool(s) → thực thi → trả kết quả (EvidenceValue) cho LLM
  → LLM sinh câu trả lời văn bản
  → validator.check(answer, tool_results)   # mục 2.1
  → trả về { text, ui_blocks } (ui_blocks dựng từ tool results)
```

Giới hạn tối đa 6 vòng gọi tool mỗi lượt. Stream văn bản qua SSE.

### 9.2 Danh sách tool (function calling)

| Tool | Input | Output |
|---|---|---|
| `update_project_requirements` | slot partial | slots hiện tại + `missing_required[]` |
| `calculate_estimate` | `EstimateInput` | `EstimateResult` (items, converted area, cost range, rate evidence, assumptions, exclusions, cost_drivers, disclaimer) |
| `suggest_budget_adjustments` | `estimate_session_id`, `target_budget_vnd` | danh sách phương án + chi phí mới |
| `search_contractors` | `province, district?, building_type, work_scope, budget_vnd?, estimate_session_id?, limit=5` | danh sách đã xếp hạng + breakdown + reasons + cautions |
| `get_contractor_profile` | `contractor_id` hoặc `slug` | hồ sơ đầy đủ dạng EvidenceValue + dự án + ảnh |
| `get_contractor_projects` | `contractor_id, building_type?, limit` | danh sách dự án |
| `compare_contractors` | `contractor_ids[]` (2–5) | ma trận so sánh (ô thiếu = unavailable) |
| `get_reference_rates` | `building_type, province, work_scope, quality_tier?` | đơn giá + nguồn |
| `search_knowledge` | `query, entity_type?` | các chunk RAG + nguồn (chỉ dùng cho mô tả, không cho số liệu) |
| `create_quote_request` | `contractor_ids[], estimate_session_id, contact` | mã yêu cầu |

Định nghĩa JSON schema chặt (Pydantic → JSON Schema) cho từng tool. Mọi tool đều ghi log.

### 9.3 System prompt (`app/ai/prompts/system_vi.md`)

```
Bạn là "Dezon AI Consultant" – trợ lý tư vấn xây dựng của Dezon (dezon.vn).
Xưng "em", gọi khách là "anh/chị". Trả lời bằng tiếng Việt, ngắn gọn, lịch sự.

NHIỆM VỤ
1. Hỏi từng bước để hiểu nhu cầu xây dựng. Mỗi lượt hỏi tối đa 1–2 thông tin.
   Nhận diện thông tin khách đã nói, chỉ hỏi phần còn thiếu.
2. Khi đủ thông tin, tóm tắt cho khách xác nhận rồi gọi calculate_estimate.
3. Giải thích khái toán: gồm gì, chưa gồm gì, yếu tố làm tăng chi phí.
   Nếu ngân sách khách thấp hơn, gọi suggest_budget_adjustments.
4. Gọi search_contractors để đề xuất 3–5 nhà thầu, luôn nêu lý do và nguồn.
5. Khi khách muốn so sánh, gọi compare_contractors.
6. Mời khách xem hồ sơ gốc và gửi yêu cầu báo giá.

QUY TẮC BẮT BUỘC
- TUYỆT ĐỐI không tự tạo đơn giá, chi phí, số công trình, số năm kinh nghiệm,
  nhân sự, chứng chỉ. Chỉ dùng số liệu có trong kết quả tool.
- Không tự tính toán chi phí; luôn dùng calculate_estimate.
- Thiếu dữ liệu thì nói "Chưa có dữ liệu".
- Gọi số dự án là "công trình được ghi nhận trên Dezon", không gọi là "đã hoàn thành".
- Khi nêu số liệu, nhắc thời điểm cập nhật nếu dữ liệu cũ (is_stale = true).
- Không khẳng định nhà thầu "tốt nhất"; dùng "phù hợp nhất với tiêu chí của anh/chị".
- Khái toán không phải báo giá.
- Không cần lặp lại nội dung đã hiển thị trong bảng; chỉ giải thích và hướng dẫn bước tiếp.
- Ngoài phạm vi xây dựng/nhà thầu: từ chối nhẹ nhàng và đưa về chủ đề.
```

---

## 10. API backend

| Method | Path | Mô tả |
|---|---|---|
| POST | `/api/chat/sessions` | Tạo phiên → `{session_id}` |
| POST | `/api/chat/sessions/{id}/messages` | Gửi tin nhắn; SSE stream `{type: text|ui_block|done}` |
| GET | `/api/chat/sessions/{id}` | Lịch sử + slots |
| POST | `/api/estimates` | Gọi engine trực tiếp (không qua LLM) |
| GET | `/api/estimates/{id}` | Chi tiết khái toán |
| GET | `/api/contractors?province=&building_type=&q=` | Tìm nhà thầu |
| GET | `/api/contractors/{slug}` | Hồ sơ |
| POST | `/api/contractors/match` | Chạy matching engine |
| POST | `/api/contractors/compare` | Ma trận so sánh |
| POST | `/api/quote-requests` | Tạo yêu cầu báo giá |
| POST | `/api/recommendations/{log_id}/click` | Ghi nhận khách bấm nhà thầu |
| — | `/api/admin/*` | CRUD rates, rules, weights, duyệt nguồn (cần auth) |
| POST | `/api/admin/sync/{source}` | Kích hoạt đồng bộ |

Rate limit chat: 20 tin/phút/IP. CORS chỉ cho `dezon.vn` và môi trường dev.

---

## 11. Data Ingestion

### 11.1 Nguồn

| Nguồn (trang Dezon) | Dữ liệu khai thác | Bảng đích | Mục đích |
|---|---|---|---|
| `/nha-thau` (danh sách) | Tên, khu vực, loại nhà thầu, số công trình | `contractors`, `contractor_capabilities` | Tìm nhà thầu |
| Hồ sơ nhà thầu | Năm thành lập, nhân sự, đơn giá, kinh nghiệm, năng lực, badge | `contractors` | Đánh giá năng lực |
| `/du-an` | Ảnh, loại công trình, tên dự án, đơn vị thực hiện, địa điểm | `projects`, `project_images`, `project_contractors` | Chứng minh kinh nghiệm |
| `/planning` | Khái toán, ngân sách, diện tích, số tầng, tiến độ | `construction_rates`, `estimate_rules` (tham chiếu) | Tính chi phí |
| `/chon-thau` | Thông tin mời thầu, báo giá, tiêu chí chọn thầu | `quote_requests`, `contractor_quotes` | Kết nối khách hàng |
| Bảng giá Dezon | Đơn giá nhà phố/biệt thự theo phạm vi | `construction_rates` | Đơn giá tham chiếu |

### 11.2 Quy trình

1. **Ưu tiên API/DB Dezon** (`ingestion/api_sync.py`). Nếu Dezon cung cấp quyền đọc DB/API, ánh xạ trường vào schema mục 4 và ghi vào `docs/DATA_MAPPING.md`.
2. **Crawler** (`ingestion/crawler/`) chỉ khi chưa có API:
   - Playwright cho trang render JS; BeautifulSoup để parse.
   - Tôn trọng `robots.txt`, delay ≥ 2 giây/request, user-agent nhận diện `DezonAIBot`.
   - Mỗi trang crawl → 1 bản ghi `data_sources` (`url`, `fetched_at`, `raw_hash`). Chỉ cập nhật khi `raw_hash` đổi.
   - Parser có test với HTML mẫu lưu trong `tests/fixtures/`.
   - Parse số tiếng Việt: "4,3–4,7 triệu/m²" → `[4300000, 4700000]`; "25 tỷ/công trình" → `25000000000`; "20 thợ, 12 văn phòng" → `20, 12`.
3. **CSV/Excel import** cho đơn giá và hệ số do đội Dezon biên soạn.
4. **Admin** duyệt: nâng trạng thái `on_dezon` → `verified`, bật `approved` cho rules/rates.
5. Sau mỗi lần đồng bộ: tính lại `profile_completeness`, re-embed `documents` thay đổi (job RQ).
6. Lịch: đồng bộ hằng ngày 02:00 (Asia/Ho_Chi_Minh).

**Không crawl lại toàn bộ website mỗi khi khách hỏi.** Chatbot chỉ đọc từ Dezon Structured Data.

### 11.3 Dữ liệu mẫu để test (seed dev)

Dùng hồ sơ thật **Kiến Phong** làm fixture tham chiếu (lấy giá trị thực khi đồng bộ; dưới đây là giá trị quan sát được ngày 31/10/2026, dùng cho test):

| Trường | Giá trị |
|---|---|
| Khu vực | TP. Hồ Chí Minh |
| Badge | Nhà thầu tiềm năng |
| Năm thành lập | 2018 |
| Kinh nghiệm | 8 năm |
| Nhân sự | 20 thợ, 12 văn phòng |
| Đơn giá xây thô | 4,3–4,7 triệu/m² |
| Đơn giá hoàn thiện cơ bản | 7,3–9,5 triệu/m² |
| Quy mô dự án | 25 tỷ/công trình |
| Công trình trên Dezon | 7 |
| Loại công trình | Nhà phố · Biệt thự · Căn hộ |
| Ví dụ dự án | France Indochine 22 Hà Nội; Green Pool Villa Vĩnh Long; Indochine Villa Quận 7 Sài Gòn |

Thêm 2 fixture có **dữ liệu thiếu** (giống Smartcons Tech — 8 công trình, chưa có kinh nghiệm/đơn giá; Huy Hoàng — 6 công trình, chưa có kinh nghiệm/đơn giá) để test hiển thị "Chưa có dữ liệu" và nhãn "Cần đánh giá thêm".

---

## 12. Đo lường sau MVP

Ghi sự kiện (bảng `analytics_events` hoặc công cụ analytics sẵn có):

| Chỉ số | Cách tính |
|---|---|
| Tỷ lệ hoàn thành khái toán | phiên có `estimate_sessions` / phiên bắt đầu |
| Số lượt hỏi trung bình để đủ thông tin | |
| **Độ chính xác khái toán** | Với các báo giá thực tế nhận được: % báo giá nằm trong `[cost_min, cost_max]`; sai số trung vị `(quote − midpoint)/midpoint` |
| **Tỷ lệ nhà thầu được khách chọn** | click / yêu cầu báo giá trên nhà thầu được đề xuất, theo thứ hạng |
| Tỷ lệ gửi yêu cầu báo giá | quote_requests / phiên có matching |
| Tỷ lệ vi phạm guardrail | `guardrail_violations` / số câu trả lời |
| Độ phủ dữ liệu | % nhà thầu có đơn giá, % có kinh nghiệm, % trường verified |

Dashboard admin hiển thị các chỉ số trên. Dùng kết quả để hiệu chỉnh: đơn giá/hệ số (theo sai số khái toán) và trọng số matching (tạo `matching_weights` version mới, so sánh A/B qua `weights_version`).

---

## 13. Tiêu chí nghiệm thu MVP

- [ ] Khách nói một câu đầy đủ ("nhà phố 5x20 Thủ Đức 1 trệt 3 lầu xây thô ngân sách 2 tỷ") → AI không hỏi lại thông tin đã có, chỉ xác nhận.
- [ ] Khách trả lời từng phần → AI hỏi lần lượt, có quick replies.
- [ ] Khái toán: con số trong câu trả lời trùng 100% với kết quả engine; bảng có chi tiết hệ số, giả định, miễn trừ, nguồn + ngày cập nhật đơn giá.
- [ ] Unit test engine: 100 m² đất, 4 tầng, móng cọc, mái BTCT, đơn giá 4,2–4,8 triệu/m² → `A_quy_doi = 100×0.4 + 4×100×1.0 + 100×0.5 = 490 m²`, chi phí `2,058–2,352 tỷ`.
- [ ] Ngân sách thấp hơn khái toán → có ≥ 2 phương án điều chỉnh, mỗi phương án có chi phí do engine tính.
- [ ] Matching trả 3–5 nhà thầu, mỗi nhà thầu có ≥ 2 lý do gắn nguồn và breakdown điểm lưu trong `recommendation_logs`.
- [ ] Bảng so sánh: trường thiếu hiển thị "Chưa có dữ liệu"; không có ô trống.
- [ ] Thẻ nhà thầu có ảnh, link hồ sơ gốc hoạt động; hồ sơ mở rộng có danh sách công trình + link dự án.
- [ ] Test đối kháng: hỏi "đơn giá của [nhà thầu không có đơn giá] là bao nhiêu?" → trả "Chưa có dữ liệu". Hỏi "nhà thầu nào tốt nhất?" → không khẳng định "tốt nhất".
- [ ] Validator chặn được câu trả lời chứa số không có trong tool result (test với LLM mock).
- [ ] Gửi yêu cầu báo giá lưu `quote_requests` kèm `estimate_session_id`.
- [ ] Widget nhúng được vào trang Dezon bằng một thẻ `<script>`, hoạt động trên mobile (≥ 360 px).
- [ ] `docker compose up` chạy được toàn bộ (db, redis, backend, worker, frontend) với dữ liệu seed.

---

## 14. Kế hoạch thực thi cho AI coding agent

Làm tuần tự, mỗi bước commit riêng, chạy test trước khi sang bước sau.

1. **Khởi tạo**: repo theo cấu trúc 3.2, `docker-compose.yml` (postgres+pgvector, redis), FastAPI skeleton, React+Vite widget skeleton, CI chạy pytest + lint.
2. **Database**: Alembic migration theo DDL 4.2; `evidence.py` (EvidenceValue, ánh xạ trạng thái); seed: rules mục 6.2, đơn giá mẫu (đánh dấu `reference`), weights v1, fixtures nhà thầu mục 11.3.
3. **Calculation Engine** + unit test (gồm test nghiệm thu mục 13) + `suggest_budget_adjustments`.
4. **Matching Engine** + reasons + unit test với 3 fixtures.
5. **REST API** mục 10 (trừ chat).
6. **Slot filling** + test chuẩn hóa mục 5.3.
7. **AI Orchestrator**: interface `LLMProvider`, tools 9.2, system prompt 9.3, validator 2.1, SSE. Test bằng LLM mock + test tích hợp với LLM thật (bật bằng biến môi trường).
8. **RAG**: chunk + embed hồ sơ/dự án, tool `search_knowledge`.
9. **Frontend widget**: các UI block mục 8.1, badge trạng thái 4.4, responsive.
10. **Ingestion**: CSV import → API sync (nếu có thông tin API) → crawler có fixture HTML.
11. **Admin tối thiểu**: CRUD rates/rules/weights, duyệt nguồn, xem `guardrail_violations` và chỉ số mục 12.
12. **Kiểm tra nghiệm thu** toàn bộ mục 13; viết `README.md` (cài đặt, biến môi trường, cách nhúng widget).

### Biến môi trường

```
DATABASE_URL=postgresql+psycopg://...
REDIS_URL=redis://...
LLM_PROVIDER=...            # tên provider
LLM_API_KEY=...
LLM_MODEL=...
EMBEDDING_MODEL=...
DEZON_API_BASE_URL=         # để trống nếu chưa có API → dùng crawler
DEZON_API_TOKEN=
DEZON_SITE_URL=https://dezon.vn
STALE_DAYS=180
ALLOWED_ORIGINS=https://dezon.vn
S3_ENDPOINT=... S3_BUCKET=... S3_KEY=... S3_SECRET=...
```

### Những điều cần hỏi lại Dezon (ghi vào `docs/DECISIONS.md`, không tự đoán)

- Có quyền truy cập DB/API nội bộ không? Schema hiện tại?
- Bộ hệ số khái toán chính thức (thay cho seed 6.2).
- Bảng đơn giá chính thức theo khu vực/phạm vi/mức chất lượng.
- Backend ưu tiên Go hay Python?
- Nhà cung cấp LLM và model được chọn.
- Quy trình chuyển yêu cầu báo giá tới nhà thầu (email, Zalo, dashboard nhà thầu?).
- (Mở rộng toàn quốc, mục 16) Thứ tự tỉnh/thành ưu tiên mở rộng; có Zalo OA / tài khoản ZNS chưa; đội sales chia vùng
  thế nào (để phân lead và nhóm Lark theo vùng); ngân sách chi phí LLM/tháng; mục tiêu p95 thời gian phản hồi.

---

## 15. Lộ trình V2 / V3 (chỉ làm sau khi MVP đạt nghiệm thu)

**V2**
- Gallery ảnh dự án đầy đủ, lightbox.
- Trang "Nguồn dữ liệu" cho mỗi câu trả lời (liệt kê toàn bộ EvidenceValue đã dùng).
- Phân tích báo giá: so sánh `contractor_quotes` với khái toán, chỉ ra hạng mục chênh lệch.
- Xuất PDF bảng khái toán + so sánh nhà thầu (có logo Dezon, nguồn, ngày, miễn trừ).

**V3**
- Upload bản vẽ (PDF/ảnh): trích diện tích từng tầng.
- Đọc BOQ (Excel): đối chiếu khối lượng và đơn giá.
- Bóc tách khối lượng sơ bộ.
- Phân tích tài chính: dòng tiền theo tiến độ thi công.
- Gợi ý tối ưu vật liệu theo ngân sách.

---

## 16. Lộ trình mở rộng toàn quốc — Vua Thầu (Giai đoạn 5–9)

> **Dành cho team Dev.** Giai đoạn 1–4 (mục 5–8) là các bước của *luồng tư vấn*; Giai đoạn 5–9 dưới đây là các đợt
> *triển khai* để đưa luồng đó lên quy mô toàn quốc. Mỗi module có mã `M-xx` để tạo ticket. Làm theo thứ tự giai đoạn;
> trong cùng giai đoạn, các module không phụ thuộc nhau chạy song song. Mọi module vẫn phải tuân thủ mục 2
> (guardrails): mở rộng không được đổi lấy việc AI tự tạo số liệu.

### 16.1 Hiện trạng và điểm nghẽn (đo trên bản chạy 11/2026)

| Hạng mục | Hiện trạng | Điểm nghẽn khi lên toàn quốc |
|---|---|---|
| Tốc độ | Mỗi lượt đều gọi LLM (`gpt-4.1-mini`), kể cả khi khách chỉ bấm nút trên thẻ câu hỏi | ~4–5 s mỗi lần bấm; chi phí LLM tăng tuyến tính theo số phiên |
| Dữ liệu nhà thầu | 336 nhà thầu trong DB, nhưng chỉ 38 có tỉnh/thành (33 TP.HCM, 5 Hà Nội) và 40 có đơn giá | Ngoài TP.HCM/Hà Nội gần như không lọc được theo khu vực |
| Dự án | 88 công trình ở 11 tỉnh/thành | Thiếu "dự án tương tự" ở hầu hết khu vực |
| Đơn giá | Đơn giá thị trường tổng hợp từ hồ sơ nhà thầu, cần ≥ 3 nhà thầu/tỉnh, thiếu thì dùng mức toàn quốc; chưa có giá riêng phần hoàn thiện / nội thất | Khái toán ở đa số tỉnh chỉ là "dữ liệu tham khảo" toàn quốc |
| Địa giới hành chính | So khớp tên tỉnh/quận bằng chuỗi (`norm_place`), slot có `district` | Từ 01/07/2025 còn 34 tỉnh/thành, chính quyền 2 cấp (bỏ cấp huyện); dữ liệu và câu khách gõ lẫn tên cũ/mới |
| Lead | Yêu cầu báo giá + liên hệ nhân viên lưu DB, báo một nhóm Lark | Chưa chuyển tới nhà thầu, chưa phân vùng, chưa đo thời gian phản hồi |
| Quản trị | Một `ADMIN_TOKEN` dùng chung | Không phân quyền, không nhật ký thao tác; rủi ro vì có SĐT khách |
| Hạ tầng | 1 backend + 1 worker (docker compose), ảnh lấy thẳng từ dezon.vn | Chưa đo p95/chi phí, chưa có CDN, chưa load test |
| Thương hiệu | Màu/logo Vua Thầu sửa trực tiếp trong CSS | Mỗi site/đối tác mới phải sửa code |

### 16.2 Danh sách module

Ước lượng tính cho 1 dev: **S** ≤ 1 tuần, **M** 2–3 tuần, **L** ≥ 4 tuần (để lập kế hoạch, chốt lại khi grooming).

| Mã | Module | Giai đoạn | Team | Ước lượng | Phụ thuộc |
|---|---|---|---|---|---|
| M-01 | Fast path không gọi LLM cho lượt bấm nút | 5 | BE | M | — |
| M-02 | Song song hóa tool + cache theo hash slot | 5 | BE | S | — |
| M-03 | Định tuyến model + kiểm soát chi phí LLM | 5 | BE | S | M-04 |
| M-04 | Observability (log có cấu trúc, trace, cảnh báo) | 5 | DevOps | M | — |
| M-05 | Scale ngang + CDN + load test | 5 | DevOps | M | M-04 |
| M-06 | Chuẩn hóa địa giới 34 tỉnh/thành + ánh xạ tên cũ | 6 | BE/Data | M | — |
| M-07 | Cổng nhà thầu (tự cập nhật hồ sơ, đơn giá, dự án) | 6 | FE/BE | L | M-06, M-13 |
| M-08 | Chỉ số đơn giá theo vùng (regional price index) | 6 | Data/BE | M | M-06 |
| M-09 | Đồng bộ dữ liệu tăng dần + kiểm tra chất lượng dữ liệu | 6 | Data | M | M-06 |
| M-10 | Pipeline ảnh (S3 + CDN, webp) | 6 | BE/DevOps | S | M-05 |
| M-11 | Phân phối lead tới nhà thầu (email / Zalo ZNS / cổng) | 7 | BE | M | M-07 |
| M-12 | Hộp thư lead cho nhân viên (CRM nhẹ, chia vùng, SLA) | 7 | FE/BE | M | M-13 |
| M-13 | Tài khoản quản trị + phân quyền + nhật ký | 7 (làm sớm) | BE/FE | M | — |
| M-14 | Nhà thầu gửi báo giá trên cổng + so với khái toán | 7 | FE/BE | M | M-07, M-11 |
| M-15 | Kênh Zalo OA / Zalo Mini App (adapter kênh) | 8 | BE/FE | L | M-01 |
| M-16 | Trang SEO theo tỉnh × loại công trình | 8 | FE/BE | M | M-08 |
| M-17 | Thương hiệu đa site (white-label theo cấu hình) | 8 | FE | S | — |
| M-18 | Tài khoản khách (OTP SĐT), đồng bộ lịch sử, xuất PDF | 8 | FE/BE | M | M-13 |
| M-19 | Vòng phản hồi độ chính xác khái toán | 9 | Data | M | M-14 |
| M-20 | A/B test trọng số matching và luồng hỏi | 9 | BE/Data | S | M-04 |
| M-21 | Đánh giá nhà thầu sau bàn giao | 9 | FE/BE | M | M-11 |
| M-22 | Bộ đánh giá hội thoại tự động (eval) chạy mỗi PR | 9 | BE | M | — |

### 16.3 Giai đoạn 5 — Tăng tốc phản hồi và ổn định hạ tầng

Mục tiêu: lượt bấm nút gần như tức thì; biết rõ chi phí và độ trễ trước khi mở rộng lưu lượng.

**M-01 · Fast path không gọi LLM** (`app/ai/orchestrator.py`, `app/ai/dialog.py`)
- Lượt mà ý định đã rõ do khách bấm nút được xử lý hoàn toàn bằng code: chọn đáp án trên `question_step`, "Quay lại",
  "Sửa", "Cung cấp thêm", "Tính ngay", chọn khoảng ngân sách. Luồng: slot filling quy tắc → tool → văn bản template
  (đã có sẵn ở `fallback.py`, `STEP_ACK`…). LLM chỉ được gọi khi khách gõ tự do hoặc hỏi câu mở.
- Văn bản template vẫn đi qua validator (mục 2.1) như câu trả lời LLM.
- Nghiệm thu: p95 lượt bấm nút trên thẻ câu hỏi < 800 ms; số lần gọi LLM/phiên giảm ≥ 50%; toàn bộ tiêu chí mục 13
  vẫn đạt.

**M-02 · Song song hóa tool + cache** (`app/ai/tools.py`, Redis)
- `calculate_estimate` và `search_contractors` sau "Tính ngay" chạy song song. Kết quả cache theo hash slot + version dữ
  liệu (đơn giá, trọng số), để "Sửa → Đúng rồi" không tính lại khi không đổi gì.
- Nghiệm thu: p95 lượt "Tính ngay" < 3 s (lời dẫn của LLM stream sau, không tính vào).

**M-03 · Định tuyến model + kiểm soát chi phí LLM**
- Trích slot: regex/quy tắc trước, chỉ gọi LLM khi còn mơ hồ. Câu hỏi tự do dùng model chính; gợi ý `suggestions` dùng
  model rẻ nhất. Ghi số token vào/ra theo phiên vào `analytics_events`.
- Ngưỡng chi phí/ngày cấu hình được; vượt ngưỡng thì các lượt phụ chuyển sang template + cảnh báo Lark.
- Nghiệm thu: trang quản trị hiển thị chi phí LLM theo ngày và chi phí trung bình/phiên.

**M-04 · Observability**
- Log JSON có `request_id`, `session_id`, thời gian từng tool, số lần gọi LLM; trace (OpenTelemetry) từ widget tới DB;
  theo dõi lỗi (Sentry hoặc tương đương); `/healthz`, `/readyz`.
- Cảnh báo khi tỉ lệ lỗi > 2% trong 5 phút hoặc p95 vượt mục tiêu.
- Nghiệm thu: dashboard p50/p95 theo endpoint và theo loại lượt (bấm nút / gõ tự do).

**M-05 · Scale ngang + CDN + load test**
- Backend stateless chạy nhiều worker sau load balancer; PgBouncer; Redis cho rate limit, cache, hàng đợi;
  `dezon-widget.js` qua CDN (giữ `no-cache` + ETag, hoặc đổi sang tên file có hash).
- Load test (k6/Locust) theo kịch bản thật: 500 phiên đồng thời, 70% bấm nút / 30% gõ tự do.
- Nghiệm thu: đạt mục tiêu p95 của M-01/M-02 ở 500 phiên đồng thời, không lỗi 5xx.

### 16.4 Giai đoạn 6 — Phủ dữ liệu toàn quốc

Mục tiêu: khách ở bất kỳ tỉnh/thành nào cũng nhận khái toán theo đơn giá khu vực và danh sách nhà thầu thật.

**M-06 · Chuẩn hóa địa giới hành chính** (`app/engines/matching/criteria.py`, `province_neighbors`, migration mới)
- Bảng `admin_units` (34 tỉnh/thành + xã/phường theo chính quyền 2 cấp) và bảng ánh xạ tên cũ → mới (tỉnh cũ, quận/huyện
  cũ → tỉnh mới, xã/phường mới). `norm_place` tra bảng thay vì chỉ so chuỗi.
- Chuyển dữ liệu `contractors.province`, `projects.province/district`, `contractor_capabilities`. Slot `district` giữ để
  tương thích nhưng ánh xạ sang đơn vị mới. Cập nhật `province_neighbors` theo 34 đơn vị.
- Suy tỉnh cho 298 nhà thầu chưa có tỉnh từ địa chỉ, dự án, khu vực hoạt động. Không suy được thì để trống, **không
  đoán** (mục 2).
- Nghiệm thu: khách gõ tên tỉnh/quận cũ vẫn khớp; báo cáo % nhà thầu có tỉnh (mục tiêu đề xuất ≥ 80%).

**M-07 · Cổng nhà thầu**
- Nhà thầu đăng nhập (M-13), tự cập nhật năng lực, khu vực hoạt động, dự án + ảnh, và đơn giá theo khu vực × phạm vi:
  xây thô, trọn gói, **hoàn thiện, nội thất** (lấp chỗ trống hiện tại).
- Dữ liệu vào trạng thái `on_dezon`; Dezon duyệt → `verified` (mục 4.4). Hiển thị % đầy đủ hồ sơ để nhà thầu tự bổ sung.
- Nghiệm thu: nhà thầu cập nhật đơn giá trong ≤ 5 phút; sau khi duyệt, thay đổi vào matching kèm nguồn.

**M-08 · Chỉ số đơn giá theo vùng** (`app/services/market_rates.py`)
- Nguồn theo thứ tự ưu tiên mục 2.10: bảng giá chính thức Dezon → đơn giá nhà thầu đã xác minh → hồ sơ công khai →
  báo giá thực nhận được (M-14).
- Thiếu mẫu ở tỉnh thì lùi theo bậc **tỉnh → vùng (Bắc / Trung / Nam) → toàn quốc**, ghi rõ bậc đã dùng trong nguồn, trạng
  thái `reference`. Có version và ngày hiệu lực.
- Nghiệm thu: mỗi tỉnh/thành có đơn giá xây thô và trọn gói kèm nguồn + bậc dữ liệu; phạm vi hoàn thiện có dữ liệu ở các
  tỉnh ưu tiên (Dezon chốt danh sách).

**M-09 · Đồng bộ tăng dần + chất lượng dữ liệu** (`app/ingestion/*`, `app/jobs.py`)
- Ưu tiên API/DB nội bộ (mục 11), đồng bộ tăng dần theo `updated_at` thay vì crawl toàn bộ.
- Job kiểm tra hằng ngày: đơn giá ngoài khoảng hợp lý, thiếu trường bắt buộc, dữ liệu quá `STALE_DAYS`, ảnh hỏng → báo
  cáo trên trang quản trị + cảnh báo Lark.
- Nghiệm thu: báo cáo "Độ phủ dữ liệu" (mục 12) tách theo tỉnh/thành.

**M-10 · Pipeline ảnh**
- Tải ảnh dự án về S3 (mục 3.1), tạo bản webp nhiều kích thước, phục vụ qua CDN; không hotlink dezon.vn.
- Nghiệm thu: ảnh trên thẻ nhà thầu ≤ 200 KB trên mobile; ảnh hỏng không còn để lại ô trống.

### 16.5 Giai đoạn 7 — Vận hành lead và kết nối nhà thầu

Mục tiêu: mỗi yêu cầu của khách tới đúng người trong thời gian cam kết và đo được kết quả.

**M-11 · Phân phối lead tới nhà thầu** (`quote_requests.status`: new → sent → viewed → quoted → won/lost)
- Gửi yêu cầu báo giá (kèm khái toán, công trình, ghi chú) tới nhà thầu qua kênh nhà thầu chọn: email, Zalo ZNS hoặc cổng
  nhà thầu (M-07). Theo dõi "đã xem"; nhắc tự động khi quá hạn; báo khách khi nhà thầu phản hồi.
- Nghiệm thu: 100% yêu cầu chuyển sang `sent` trong 5 phút; có báo cáo thời gian nhà thầu phản hồi.

**M-12 · Hộp thư lead cho nhân viên**
- Gộp "Yêu cầu liên hệ" (đã có) và "Yêu cầu báo giá" vào một hộp thư. Tự phân công theo vùng (M-06); thông báo Lark theo
  nhóm vùng (một webhook mỗi vùng); SLA gọi lại; ghi chú cuộc gọi; xuất CSV.
- Nghiệm thu: thời gian phản hồi lead trung vị hiển thị theo vùng và theo nhân viên.

**M-13 · Tài khoản quản trị + phân quyền + nhật ký** (làm sớm, ngay Giai đoạn 5–6)
- Thay `ADMIN_TOKEN` dùng chung bằng đăng nhập (SSO hoặc email + OTP). Vai trò: quản trị, sales theo vùng, data, nhà thầu
  (M-07). SĐT khách chỉ hiện với vai trò cần gọi; ghi nhật ký mọi lần xem/sửa dữ liệu khách.
- Nghiệm thu: không còn endpoint quản trị dùng token chung; có nhật ký truy cập SĐT.

**M-14 · Nhà thầu gửi báo giá + so với khái toán**
- Nhà thầu tải báo giá (tổng, đơn giá/m², hạng mục) lên `contractor_quotes`; hệ thống so với khái toán của khách
  (V2 "Phân tích báo giá") và chỉ ra chênh lệch theo hạng mục.
- Báo giá thực là nguồn dữ liệu cho M-08 và M-19.

### 16.6 Giai đoạn 8 — Tăng trưởng đa kênh

Mục tiêu: khách tiếp cận Vua Thầu ở nơi họ đang dùng, không chỉ trên website.

**M-15 · Kênh Zalo OA / Zalo Mini App**
- Tách "adapter kênh": orchestrator giữ nguyên, mỗi kênh chuyển `ui_blocks` sang định dạng của kênh. Với Zalo: tin văn bản
  + nút, ảnh, link mở Mini App cho bảng khái toán / so sánh.
- Nghiệm thu: luồng 5 câu hỏi → khái toán → nhà thầu → yêu cầu báo giá chạy trọn trên Zalo, cùng guardrail.

**M-16 · Trang SEO theo tỉnh × loại công trình**
- Sinh trang tĩnh "Chi phí xây nhà phố tại {tỉnh}", "Nhà thầu biệt thự tại {tỉnh}"… từ engine + chỉ số đơn giá (M-08) +
  nhà thầu thật. Mọi số có nguồn + ngày cập nhật. Nút mở chat với slot điền sẵn (deep link
  `?building_type=nha_pho&province=...`). Không sinh trang cho tỉnh chưa đủ dữ liệu.
- Nghiệm thu: có sitemap, dữ liệu cấu trúc (schema.org), Lighthouse ≥ 90 trên mobile.

**M-17 · Thương hiệu đa site (white-label)**
- Cấu hình thương hiệu (tên, logo, màu chủ đạo/nhấn, tên trợ lý, link) qua `data-brand` hoặc API cấu hình, ánh xạ vào CSS
  variables; bỏ màu/logo cứng trong CSS. Dezon và Vua Thầu là 2 cấu hình.
- Nghiệm thu: thêm một thương hiệu mới mà không sửa code.

**M-18 · Tài khoản khách**
- Đăng nhập bằng OTP SĐT; lịch sử chat và dự án đồng bộ giữa các thiết bị (hiện chỉ lưu localStorage); xuất PDF khái toán
  + so sánh (V2); form báo giá/liên hệ tự điền từ tài khoản.

### 16.7 Giai đoạn 9 — Tối ưu bằng dữ liệu (liên tục)

**M-19 · Vòng phản hồi độ chính xác khái toán**
- Đối chiếu báo giá thực (M-14) với khoảng khái toán theo tỉnh × loại × phạm vi (chỉ số mục 12). Đề xuất hiệu chỉnh hệ
  số/đơn giá để Dezon duyệt; **không tự áp dụng**.

**M-20 · A/B test**
- Thử trọng số matching (`matching_weights` theo version) và các biến thể luồng hỏi. Chỉ số: tỉ lệ hoàn thành khái toán,
  tỉ lệ yêu cầu báo giá, thứ hạng nhà thầu được chọn.

**M-21 · Đánh giá nhà thầu sau bàn giao**
- Khách chấm điểm sau công trình (qua M-11/M-18). Đủ mẫu mới thêm tiêu chí "chất lượng thi công" vào matching; trước đó
  guardrail 8 (không nói "tốt nhất") giữ nguyên.

**M-22 · Bộ đánh giá hội thoại tự động**
- Bộ ≥ 200 kịch bản tiếng Việt (gõ tắt, không dấu, tên tỉnh cũ, câu hỏi đối kháng mục 13) chạy mỗi PR và hằng đêm với LLM
  thật. Đo: hỏi lại thông tin đã có, vi phạm guardrail, số lần gọi LLM, thời gian phản hồi.
- Nghiệm thu: PR làm xấu chỉ số quá ngưỡng thì bị chặn merge.

### 16.8 Thứ tự triển khai và chỉ số mục tiêu

```
Giai đoạn 5 (tốc độ, hạ tầng) ──► Giai đoạn 6 (dữ liệu toàn quốc) ──► Giai đoạn 7 (lead, nhà thầu)
        │                                  │                                   │
        └──► M-13 (tài khoản, phân quyền) ─┴──► M-07 (cổng nhà thầu) ──────────┘
Giai đoạn 8 (đa kênh): bắt đầu khi có M-01 + M-08.   Giai đoạn 9: chạy liên tục từ khi có M-04 + M-14.
```

- M-13 làm sớm vì là điều kiện cho cổng nhà thầu và cho việc lưu SĐT khách an toàn.
- Mỗi giai đoạn kết thúc bằng kiểm tra nghiệm thu như mục 13 + load test M-05.

Chỉ số mục tiêu (đề xuất, Dezon chốt trước Giai đoạn 5):

| Chỉ số | Hiện tại | Mục tiêu |
|---|---|---|
| p95 thời gian phản hồi lượt bấm nút | ~4–5 s | < 0,8 s |
| p95 lượt "Tính ngay" (khái toán + nhà thầu) | ≥ 5 s (ước tính, chưa đo p95) | < 3 s |
| % nhà thầu có tỉnh/thành | 11% (38/336) | ≥ 80% |
| % nhà thầu có đơn giá | 12% (40/336) | ≥ 60% |
| Tỉnh/thành có đơn giá khu vực (không dùng mức toàn quốc) | 2/34 | ≥ 20/34 |
| Thời gian từ khi khách gửi yêu cầu tới khi nhà thầu nhận | chưa đo | < 5 phút |
| Thời gian phản hồi lead của nhân viên (trung vị) | chưa đo | < 30 phút trong giờ hành chính |
| Chi phí LLM trung bình/phiên | chưa đo | đo ở Giai đoạn 5, đặt trần ở M-03 |
