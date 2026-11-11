"""contractor_quotes: báo giá nhà thầu do nhân viên nhập ở trang admin + dòng hạng mục (V2 — quotes)

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-11

Chỉ THÊM cột (có mặc định / cho phép NULL) nên không cần backfill:
- converted_area_m2: diện tích sàn quy đổi theo cách tính của nhà thầu (để so với khái toán).
- lines: dòng hạng mục JSONB (kiểm tra bằng pydantic QuoteLine ở app/schemas/quote_analysis.py), mặc định [].
- verified: nhân viên đã đối chiếu file gốc -> trạng thái "Đã xác minh", ngược lại "Dữ liệu tham khảo".
- entered_by, note: người nhập, ghi chú nội bộ.
Thêm index quote_request_id (trang admin đọc báo giá theo yêu cầu báo giá). Không dùng ký tự phần trăm trong DDL:
conftest chạy bằng exec_driver_sql (psycopg coi nó là placeholder).
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

DDL = r"""
ALTER TABLE contractor_quotes ADD COLUMN IF NOT EXISTS converted_area_m2 NUMERIC(10,2);
ALTER TABLE contractor_quotes ADD COLUMN IF NOT EXISTS lines JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE contractor_quotes ADD COLUMN IF NOT EXISTS verified BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE contractor_quotes ADD COLUMN IF NOT EXISTS entered_by TEXT;
ALTER TABLE contractor_quotes ADD COLUMN IF NOT EXISTS note TEXT;
CREATE INDEX IF NOT EXISTS ix_contractor_quotes_quote_request ON contractor_quotes (quote_request_id);
"""

DROP = r"""
DROP INDEX IF EXISTS ix_contractor_quotes_quote_request;
ALTER TABLE contractor_quotes DROP COLUMN IF EXISTS note;
ALTER TABLE contractor_quotes DROP COLUMN IF EXISTS entered_by;
ALTER TABLE contractor_quotes DROP COLUMN IF EXISTS verified;
ALTER TABLE contractor_quotes DROP COLUMN IF EXISTS lines;
ALTER TABLE contractor_quotes DROP COLUMN IF EXISTS converted_area_m2;
"""


def upgrade() -> None:
    op.execute(DDL)


def downgrade() -> None:
    op.execute(DROP)
