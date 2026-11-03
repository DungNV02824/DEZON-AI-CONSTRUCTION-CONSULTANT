"""bảng contact_requests: khách để lại họ tên + số điện thoại để nhân viên Dezon gọi lại (nút "Liên hệ nhân viên")

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-03

Yêu cầu liên hệ độc lập với yêu cầu báo giá (quote_requests): không bắt buộc nhà thầu hay khái toán; có thể gắn phiên
chat (để nhân viên xem tóm tắt công trình) và/hoặc nhà thầu khách đang xem. Không dùng ký tự phần trăm trong DDL:
conftest chạy bằng exec_driver_sql (psycopg coi nó là placeholder).
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

DDL = r"""
CREATE TABLE IF NOT EXISTS contact_requests (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  code             TEXT UNIQUE NOT NULL,
  customer_name    TEXT NOT NULL,
  customer_phone   TEXT NOT NULL,
  note             TEXT,
  chat_session_id  UUID REFERENCES chat_sessions(id) ON DELETE SET NULL,
  contractor_id    BIGINT REFERENCES contractors(id) ON DELETE SET NULL,
  source           TEXT,
  page_url         TEXT,
  status           TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'contacted', 'done')),
  handled_note     TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_contact_requests_status_created ON contact_requests (status, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_contact_requests_phone_created ON contact_requests (customer_phone, created_at DESC);
"""

DROP = "DROP TABLE IF EXISTS contact_requests;"


def upgrade() -> None:
    op.execute(DDL)


def downgrade() -> None:
    op.execute(DROP)
