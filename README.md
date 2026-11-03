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
| Giao diện đầy đủ AI for Planning (trang chủ) | http://localhost:8080 |
| Trang demo nhúng widget nổi | http://localhost:8080/widget-demo |
| Backend API + Swagger | http://localhost:8000/docs |
| Trang quản trị | http://localhost:8000/admin (nhập `ADMIN_TOKEN`) |
| PostgreSQL + pgvector | localhost:5433 (user/pass/db: `dezon`) |
| Redis | localhost:6379 |

Khi khởi động, backend tự chạy migration (`alembic upgrade head`) và seed dữ liệu mẫu (idempotent): hệ số,
đơn giá tham khảo, trọng số, 3 nhà thầu demo, rồi tính `profile_completeness` và tạo embedding RAG. Trong Docker
`SEED_SNAPSHOT=1` (mặc định) nạp thêm snapshot dữ liệu **thật** crawl từ dezon.vn (hồ sơ, công trình thực tế,
URL ảnh) cho 3 nhà thầu demo — xem mục "Dữ liệu".
Worker RQ xử lý đồng bộ dữ liệu, re-embed và lịch đồng bộ hằng ngày 02:00 (Asia/Ho_Chi_Minh).

### Chế độ AI

| `OPENAI_API_KEY` | Hành vi |
|---|---|
| Để trống | **Chế độ giả lập (mock):** hội thoại theo kịch bản, không gọi mạng. Dùng để demo toàn bộ luồng, mọi con số vẫn lấy từ engine/DB thật. |
| Có giá trị | Dùng OpenAI `MODEL_NAME` (mặc định `gpt-4.1-mini`) cho hội thoại + function calling, `EMBEDDING_MODEL` (mặc định `text-embedding-3-small`) cho RAG. |

Sau khi điền key: `docker compose up -d` (tạo lại container backend/worker), rồi chạy lại embedding bằng
trang quản trị → "Đồng bộ" → `reindex` (vì vector cũ được tạo bằng embedding cục bộ).

Có thể ép provider bằng `LLM_PROVIDER=mock|openai|anthropic`, `EMBEDDING_PROVIDER=hash|openai`.

- **OpenAI hoặc endpoint tương thích OpenAI**: `OPENAI_API_KEY` (hoặc `LLM_API_KEY`), `LLM_MODEL`/`MODEL_NAME`,
  tùy chọn `LLM_BASE_URL`.
- **Claude (`LLM_PROVIDER=anthropic`)**: đặt key Anthropic vào `LLM_API_KEY` (hoặc để trống và đặt
  `ANTHROPIC_API_KEY` — SDK tự đọc), `LLM_MODEL` là tên model Claude. Lưu ý `OPENAI_API_KEY` cũng là bí danh của
  `LLM_API_KEY`, nên khi dùng Claude đừng để `OPENAI_API_KEY` chứa key OpenAI mà không đặt `LLM_API_KEY`.
  Embedding vẫn đi theo `EMBEDDING_PROVIDER`/`EMBEDDING_API_KEY` (Anthropic không có API embedding).
- **Embedding qua endpoint khác** (Azure/proxy nội bộ…): `EMBEDDING_PROVIDER=openai`, `EMBEDDING_API_KEY`,
  `EMBEDDING_API_BASE=https://.../v1` (đi qua Settings), `EMBEDDING_MODEL`, `EMBEDDING_DIM` (mặc định 1536 — đổi
  thì phải đổi cả cột vector trong migration).

## Biến môi trường

Xem [.env.example](.env.example). Quan trọng:

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `OPENAI_API_KEY` | — | Key OpenAI (không commit file `.env`); bí danh của `LLM_API_KEY` và `EMBEDDING_API_KEY` |
| `LLM_PROVIDER` | `auto` | `auto` (có key → openai, không → mock) \| `mock` \| `openai` \| `anthropic` |
| `LLM_API_KEY` | — | Key cho provider LLM đang chọn (bắt buộc với `anthropic` nếu không có `ANTHROPIC_API_KEY`) |
| `LLM_MODEL` / `MODEL_NAME` | `gpt-4.1-mini` | Model hội thoại |
| `LLM_BASE_URL` | — | Endpoint LLM tương thích khác (tùy chọn) |
| `MAX_TOOL_ROUNDS` | `6` | Số vòng gọi tool tối đa mỗi lượt chat |
| `EMBEDDING_PROVIDER` | `auto` | `auto` (có key → openai, không → hash cục bộ) \| `hash` \| `openai` |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Model embedding |
| `EMBEDDING_API_KEY` | — | Key embedding (mặc định lấy `OPENAI_API_KEY`) |
| `EMBEDDING_API_BASE` | `https://api.openai.com/v1` | Endpoint `/embeddings` tương thích OpenAI khác |
| `EMBEDDING_DIM` | `1536` | Số chiều vector (phải khớp cột `vector(1536)` của bảng `documents`) |
| `SEED_SNAPSHOT` | `1` trong Docker, `0` khi chạy tay | `1`: seed nạp `backend/app/db/seed_data/dezon_snapshot.json`. Đọc từ biến môi trường thật (không từ `.env` khi chạy ngoài Docker); test luôn tắt |
| `APP_ENV` | `development` | `production`: trang quản trị bị khóa nếu `ADMIN_TOKEN` còn mặc định |
| `ADMIN_TOKEN` | `change-me` | Token cho `/api/admin/*` — **bắt buộc đổi trên production** |
| `IMPORT_DIR` | `data/imports` | Thư mục duy nhất admin được nhập CSV/XLSX theo đường dẫn (ngoài ra có thể tải file lên) |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | IP/subnet reverse proxy được tin cho `X-Forwarded-For` — **phải đặt khi chạy sau proxy**, xem "Triển khai sau reverse proxy" |
| `ALLOWED_ORIGINS` | `https://dezon.vn,...` | CORS (chỉ dezon.vn + môi trường dev) |
| `STALE_DAYS` | `180` | Dữ liệu cũ hơn số ngày này hiển thị cảnh báo |
| `CHAT_RATE_LIMIT_PER_MIN` | `20` | Giới hạn tin nhắn / phút / IP |
| `DEZON_API_BASE_URL`, `DEZON_API_TOKEN` | — | API nội bộ Dezon (để trống → dùng crawler trang công khai) |

### Triển khai sau reverse proxy (rate limit theo IP khách)

Rate limit chat (`CHAT_RATE_LIMIT_PER_MIN` tin/phút) tính theo IP mà uvicorn thấy. Backend chạy với
`--proxy-headers`, nhưng chỉ tin `X-Forwarded-For` từ các IP trong `FORWARDED_ALLOW_IPS` (mặc định `127.0.0.1`).
Khi đặt nginx/Caddy/load balancer ở **container hoặc máy khác**, nếu không đặt biến này thì mọi khách bị tính là IP
của proxy (hoặc gateway Docker, vd `172.x.0.1`) và **chung một bucket** → chặn hàng loạt khi đông khách.

- Đặt `FORWARDED_ALLOW_IPS` = IP hoặc subnet của proxy thật, vd `FORWARDED_ALLOW_IPS=10.0.0.5` hoặc
  `FORWARDED_ALLOW_IPS=172.16.0.0/12` (mạng Docker), nhiều giá trị cách nhau bằng dấu phẩy.
- Proxy phải ghi đè (không nối thêm) header: nginx `proxy_set_header X-Forwarded-For $remote_addr;`.
- **Không** đặt `*` nếu cổng backend còn mở trực tiếp ra Internet: khách sẽ tự giả mạo `X-Forwarded-For` để né giới hạn.
- Kiểm tra: log truy cập uvicorn phải hiện IP khách thật, không phải IP proxy.

## Nhúng widget vào dezon.vn

```html
<script src="https://<host-widget>/dezon-widget.js" data-api-base="https://<host-api>" defer></script>
```

- Một thẻ `<script>` duy nhất; widget tự gắn nút chat góc phải dưới, style cô lập trong Shadow DOM.
- `data-open="true"`: mở sẵn khung chat. `data-mock="true"`: dữ liệu giả lập (chỉ để xem giao diện).
- Hoạt động trên mobile từ 360 px (toàn màn hình khi màn hình < 640 px).
- Điều khiển từ trang: `window.DezonWidget.open() / close() / toggle()`.
- Thêm domain trang nhúng vào `ALLOWED_ORIGINS` của backend.

### Giao diện mở rộng "AI for Planning" (trang đầy đủ)

Dùng cho trang như `dezon.vn/planning/`: thanh bên (New chat, Recent chats, Đăng nhập / Đăng ký) + khung chat rộng,
câu hỏi kèm lựa chọn nhanh hiển thị dạng thẻ lưới 2 cột.

```html
<div id="dezon-planning" style="height: 100vh"></div>
<script src="https://<host-widget>/dezon-widget.js"
        data-mode="page"
        data-api-base="https://<host-api>"
        data-login-url="https://dezon.vn/<trang-đăng-nhập>/"
        defer></script>
```

- Trang có sẵn phần tử `#dezon-planning` thì widget tự chạy chế độ trang (không cần `data-mode`);
  `data-target="<selector>"` để gắn vào phần tử khác. Giao diện lấp đầy chiều cao phần tử đó.
- `data-login-url` rỗng → ẩn nút "Đăng nhập / Đăng ký".
- "Recent chats" lưu trong trình duyệt của khách (localStorage), tiêu đề là câu hỏi đầu tiên; nội dung hội thoại
  lấy lại từ server khi mở. "Xóa tất cả" chỉ xóa lịch sử trên thiết bị đó.
- Widget nổi có nút **Mở rộng** (góc trên khung chat) chuyển sang giao diện này toàn màn hình, nút **Thu nhỏ** để quay
  lại; từ trang chủ gọi được `window.DezonWidget.expand()`.
- Demo: http://localhost:8080/planning

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
  Fixture tối thiểu này (mục 11.3) là thứ test dùng.
- **Snapshot dữ liệu thật** ([backend/app/db/seed_data/dezon_snapshot.json](backend/app/db/seed_data/dezon_snapshot.json)):
  hồ sơ + 6 công trình thực tế mỗi nhà thầu (kèm `project_url` và URL ảnh gốc trên dezon.vn) của 3 nhà thầu demo,
  crawl ngày 02/11/2026 bằng crawler của dự án (robots.txt, ≥ 2 giây/request, 22 request). Nạp khi
  `SEED_SNAPSHOT=1` (mặc định trong docker-compose). Làm mới: chạy crawler với `--snapshot <file>` hoặc
  trang quản trị → Đồng bộ → crawl.
- **Nhập đơn giá / hệ số chính thức**: trang quản trị → Import CSV/XLSX (mẫu tại [docs/samples](docs/samples)),
  sau đó bật `approved` cho hệ số đã duyệt.
- **Đồng bộ dữ liệu nhà thầu/dự án**: ưu tiên API Dezon (`DEZON_API_BASE_URL`); nếu chưa có, crawler đọc trang
  công khai (tôn trọng robots.txt, ≥ 2 giây/request, user-agent `DezonAIBot`). Chatbot **không** crawl khi khách hỏi.

## Việc cần Dezon xác nhận trước khi lên production

Xem mục "Cần hỏi lại Dezon" trong [docs/DECISIONS.md](docs/DECISIONS.md): quyền truy cập DB/API, bộ hệ số và
bảng đơn giá chính thức, quy trình chuyển yêu cầu báo giá tới nhà thầu, danh mục tỉnh sau sáp nhập 2025,
quyền sử dụng dữ liệu crawl.
