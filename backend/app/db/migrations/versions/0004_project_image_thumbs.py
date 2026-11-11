"""project_images.thumb_url: ảnh thu nhỏ của dezon.vn cho ô ảnh gallery (V2 — gallery)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-11

Crawler đọc `data-thumb` của `a.dznt-shot`; dữ liệu cũ (snapshot, chưa crawl lại) để NULL và service suy ra
từ URL ảnh gốc (app/images.py derive_thumb_url). Cột cho phép NULL -> không cần backfill.
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

DDL = r"""
ALTER TABLE project_images ADD COLUMN IF NOT EXISTS thumb_url TEXT;
"""

DROP = "ALTER TABLE project_images DROP COLUMN IF EXISTS thumb_url;"


def upgrade() -> None:
    op.execute(DDL)


def downgrade() -> None:
    op.execute(DROP)
