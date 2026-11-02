# Dezon AI Consultant

Đặc tả gốc: `DEZON_AI_CONSULTANT_SPEC.md` (đọc mục liên quan trước khi sửa module). Hợp đồng API/SSE:
`docs/API_CONTRACT.md`. Giả định: `docs/DECISIONS.md` (ghi thêm khi tự quyết điểm chưa rõ).

## Lệnh

```bash
docker compose up -d db redis                     # Postgres+pgvector ở localhost:5433, Redis 6379
cd backend
.venv/Scripts/python -m pytest -q                 # Windows venv (Python 3.13); test DB tự tạo DB tạm
.venv/Scripts/python -m pytest tests/test_x.py -q
.venv/Scripts/ruff check app tests
.venv/Scripts/alembic upgrade head && .venv/Scripts/python -m app.db.seed
```

Frontend: `cd frontend && npm install && npm run build` (Vite + React + TS).

## Quy ước

- Kiểu dữ liệu dùng chung nằm ở `app/schemas/*` và `app/evidence.py`; định dạng số tiếng Việt ở
  `app/formatting.py` (`format_vnd_range`, `format_price_per_m2_range`, ...). Dùng lại, đừng viết bản riêng.
  Nếu buộc phải mở rộng schema dùng chung: chỉ THÊM trường có giá trị mặc định, không đổi/xóa trường cũ.
- Engine (`app/engines/*`) là hàm thuần: không DB, không mạng, không LLM. Truy cập DB ở `app/services/*`.
- Mọi giá trị hiển thị cho khách là `EvidenceValue`; thiếu dữ liệu → `EvidenceValue.unavailable()`
  (chuỗi đúng `"Chưa có dữ liệu"`). Số dự án luôn gọi là "công trình ghi nhận trên Dezon".
  Không bao giờ dùng "tốt nhất" — dùng "phù hợp nhất với tiêu chí của anh/chị".
- Chuỗi hiển thị cho khách bằng tiếng Việt có dấu; xưng "em", gọi "anh/chị".
- Test thuần không cần DB; test cần DB dùng fixture `db` / `client` trong `tests/conftest.py`.
- Migration mới: file `app/db/migrations/versions/000N_*.py` khai báo biến `DDL` (SQL thuần) và
  `upgrade()` gọi `op.execute(DDL)` — conftest tự nạp biến `DDL` này cho DB test.
