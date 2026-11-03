"""dữ liệu: hết hiệu lực đơn giá mẫu seed dev, gỡ dữ liệu fixture dev khỏi DB đã seed trước đây

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-02

Chỉ sửa dữ liệu (không đổi schema), chạy một lần:
- Đơn giá mẫu seed dev (bảng giá tự đặt, nhãn nguồn "Đơn giá tham khảo (seed dev …") còn hiệu lực -> effective_to =
  hôm qua (không xóa: estimate_sessions.rate_id có thể tham chiếu). Không phụ thuộc việc rebuild_market_rates có chạy.
- Công trình fixture "France Indochine 22 Hà Nội" bị fixture gán 'biet_thu'; trang dezon.vn không ghi loại công trình
  và upsert không xóa trường dự án khi nguồn trả None -> đặt lại NULL (crawl sau sẽ ghi lại nếu dezon.vn có loại).
- Ảnh/ảnh đại diện placeholder của fixture dev (placehold.co) không phải dữ liệu thật -> gỡ.
Trên DB test (conftest nạp DDL trước khi seed) các lệnh này không ảnh hưởng gì. Không dùng ký tự phần trăm trong
DDL: conftest chạy bằng exec_driver_sql (psycopg coi nó là placeholder).
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

DDL = r"""
UPDATE construction_rates SET effective_to = CURRENT_DATE - 1
WHERE (effective_to IS NULL OR effective_to >= CURRENT_DATE)
  AND source_id IN (SELECT id FROM data_sources WHERE position('Đơn giá tham khảo (seed dev' in label) = 1);

UPDATE projects SET building_type = NULL, updated_at = now()
WHERE slug = 'kien-phong--france-indochine-22-ha-noi' AND building_type = 'biet_thu';

DELETE FROM project_images WHERE position('https://placehold.co/' in url) = 1;

UPDATE contractors SET avatar_url = NULL WHERE position('https://placehold.co/' in avatar_url) = 1;
"""


def upgrade() -> None:
    op.execute(DDL)


def downgrade() -> None:
    pass  # sửa dữ liệu một chiều
