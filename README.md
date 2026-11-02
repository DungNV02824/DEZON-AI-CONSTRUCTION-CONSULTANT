# Dezon AI Consultant — MVP

Trợ lý **khái toán chi phí xây dựng và tư vấn chọn nhà thầu** cho dezon.vn. Đặc tả gốc:
[DEZON_AI_CONSULTANT_SPEC.md](DEZON_AI_CONSULTANT_SPEC.md).

Nguyên tắc cốt lõi: **LLM không tự tạo con số.** Chi phí do Calculation Engine tính bằng công thức;
nhà thầu do Matching Engine chấm điểm theo luật. Mọi số liệu hiển thị kèm nguồn trên Dezon, ngày cập nhật
và trạng thái xác minh. Output validator chặn mọi con số trong câu trả lời không truy được về kết quả tool.

| Thành phần | Vị trí |
|---|---|
| Backend FastAPI (Python 3.12) | [backend/app](backend/app) |
| Calculation Engine (hàm thuần) | [backend/app/engines/estimate](backend/app/engines/estimate) |
| Matching Engine (hàm thuần) | [backend/app/engines/matching](backend/app/engines/matching) |
| AI Orchestrator, tools, validator | [backend/app/ai](backend/app/ai) |
| RAG (pgvector) | [backend/app/rag](backend/app/rag) |
| Đồng bộ dữ liệu (CSV, API, crawler) | [backend/app/ingestion](backend/app/ingestion), [backend/app/jobs.py](backend/app/jobs.py) |
| Widget chat (React + TS) | [frontend](frontend) |
| Hợp đồng API / SSE | [docs/API_CONTRACT.md](docs/API_CONTRACT.md) |
| Giả định & câu hỏi chờ Dezon | [docs/DECISIONS.md](docs/DECISIONS.md) |
| Ánh xạ dữ liệu dezon.vn → DB | [docs/DATA_MAPPING.md](docs/DATA_MAPPING.md) |

## Chạy nhanh (Docker)

Yêu cầu: Docker Desktop / Docker Engine có Compose v2.

```bash
cp .env.example .env          # rồi điền OPENAI_API_KEY (tùy chọn, xem dưới)
docker compose up -d --build
```

| Dịch vụ | Địa chỉ |
|---|---|
| Trang demo nhúng widget | http://localhost:8080 |
| Backend API + Swagger | http://localhost:8000/docs |
| Trang quản trị | http://localhost:8000/admin (nhập `ADMIN_TOKEN`) |
| PostgreSQL + pgvector | localhost:5433 (user/pass/db: `dezon`) |
| Redis | localhost:6379 |

Khi khởi động, backend tự chạy migration (`alembic upgrade head`) và seed dữ liệu mẫu (idempotent).
Worker RQ xử lý đồng bộ dữ liệu, re-embed và lịch đồng bộ hằng ngày 02:00 (Asia/Ho_Chi_Minh).

### Chế độ AI

| `OPENAI_API_KEY` | Hành vi |
|---|---|
| Để trống | **Chế độ giả lập (mock):** hội thoại theo kịch bản, không gọi mạng. Dùng để demo toàn bộ luồng, mọi con số vẫn lấy từ engine/DB thật. |
| Có giá trị | Dùng OpenAI `MODEL_NAME` (mặc định `gpt-4.1-mini`) cho hội thoại + function calling, `EMBEDDING_MODEL` (mặc định `text-embedding-3-small`) cho RAG. |

Sau khi điền key: `docker compose up -d` (tạo lại container backend/worker), rồi chạy lại embedding bằng
trang quản trị → "Đồng bộ" → `reindex` (vì vector cũ được tạo bằng embedding cục bộ).

Có thể ép provider bằng `LLM_PROVIDER=mock|openai|anthropic`, `EMBEDDING_PROVIDER=hash|openai`.

## Biến môi trường

Xem [.env.example](.env.example). Quan trọng:

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `OPENAI_API_KEY` | — | Key OpenAI (không commit file `.env`) |
| `MODEL_NAME` | `gpt-4.1-mini` | Model hội thoại |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Model embedding (1536 chiều) |
| `APP_ENV` | `development` | `production`: trang quản trị bị khóa nếu `ADMIN_TOKEN` còn mặc định |
| `ADMIN_TOKEN` | `change-me` | Token cho `/api/admin/*` — **bắt buộc đổi trên production** |
| `IMPORT_DIR` | `data/imports` | Thư mục duy nhất admin được nhập CSV/XLSX theo đường dẫn (ngoài ra có thể tải file lên) |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | IP reverse proxy được tin cho `X-Forwarded-For` (rate limit theo IP thật) |
| `ALLOWED_ORIGINS` | `https://dezon.vn,...` | CORS (chỉ dezon.vn + môi trường dev) |
| `STALE_DAYS` | `180` | Dữ liệu cũ hơn số ngày này hiển thị cảnh báo |
| `CHAT_RATE_LIMIT_PER_MIN` | `20` | Giới hạn tin nhắn / phút / IP |
| `DEZON_API_BASE_URL`, `DEZON_API_TOKEN` | — | API nội bộ Dezon (để trống → dùng crawler trang công khai) |

## Nhúng widget vào dezon.vn

```html
<script src="https://<host-widget>/dezon-widget.js" data-api-base="https://<host-api>" defer></script>
```

- Một thẻ `<script>` duy nhất; widget tự gắn nút chat góc phải dưới, style cô lập trong Shadow DOM.
- `data-open="true"`: mở sẵn khung chat. `data-mock="true"`: dữ liệu giả lập (chỉ để xem giao diện).
- Hoạt động trên mobile từ 360 px (toàn màn hình khi màn hình < 640 px).
- Điều khiển từ trang: `window.DezonWidget.open() / close() / toggle()`.
- Thêm domain trang nhúng vào `ALLOWED_ORIGINS` của backend.

## Phát triển

```bash
docker compose up -d db redis

cd backend
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"     # Linux/macOS: .venv/bin/...
.venv/Scripts/alembic upgrade head && .venv/Scripts/python -m app.db.seed
.venv/Scripts/uvicorn app.main:app --reload --port 8000
.venv/Scripts/python -m pytest -q          # test tự tạo DB tạm, luôn dùng LLM giả lập (không tốn phí)
.venv/Scripts/ruff check app tests

cd frontend
npm install && npm run dev                 # http://localhost:5173 (proxy /api -> :8000)
npm test && npm run typecheck && npm run build
```

Test gọi LLM thật (tốn phí): `RUN_LIVE_LLM=1 OPENAI_API_KEY=... pytest -m live_llm`.

## Dữ liệu

- **Seed dev** ([backend/app/db/seed.py](backend/app/db/seed.py)): hệ số khái toán mục 6.2 (chưa duyệt),
  **đơn giá mẫu gắn nhãn "Dữ liệu tham khảo"** (không phải bảng giá chính thức), trọng số matching v1,
  3 nhà thầu tham chiếu (Kiến Phong, Smartcons Tech, Huy Hoàng) với URL hồ sơ thật trên dezon.vn.
- **Nhập đơn giá / hệ số chính thức**: trang quản trị → Import CSV/XLSX (mẫu tại [docs/samples](docs/samples)),
  sau đó bật `approved` cho hệ số đã duyệt.
- **Đồng bộ dữ liệu nhà thầu/dự án**: ưu tiên API Dezon (`DEZON_API_BASE_URL`); nếu chưa có, crawler đọc trang
  công khai (tôn trọng robots.txt, ≥ 2 giây/request, user-agent `DezonAIBot`). Chatbot **không** crawl khi khách hỏi.

## Việc cần Dezon xác nhận trước khi lên production

Xem mục "Cần hỏi lại Dezon" trong [docs/DECISIONS.md](docs/DECISIONS.md): quyền truy cập DB/API, bộ hệ số và
bảng đơn giá chính thức, quy trình chuyển yêu cầu báo giá tới nhà thầu, danh mục tỉnh sau sáp nhập 2025,
quyền sử dụng dữ liệu crawl.
