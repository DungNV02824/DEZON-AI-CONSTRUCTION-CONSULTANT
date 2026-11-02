"""Fixtures dùng chung.

- Test thuần (engine, slot filling, validator) KHÔNG cần DB.
- Test đánh dấu/nhận fixture `db` cần PostgreSQL+pgvector (docker compose up db).
  Mỗi phiên pytest tạo một database riêng `dezon_test_<hex>` từ DATABASE_URL rồi xóa khi xong,
  nên nhiều tiến trình pytest có thể chạy song song. Không kết nối được -> test DB tự skip.
- Mỗi test chạy trong một transaction và rollback khi kết thúc.
"""
import importlib.util
import os
import uuid
from pathlib import Path

import pytest

# Test luôn dùng LLM giả lập + embedding cục bộ, kể cả khi máy dev có OPENAI_API_KEY trong .env.
# Test gọi LLM thật đánh dấu live_llm và tự bật provider khi RUN_LIVE_LLM=1.
os.environ["LLM_PROVIDER"] = "mock"
os.environ["EMBEDDING_PROVIDER"] = "hash"
# Fixture nhà thầu mục 11.3 phải giữ nguyên: không nạp snapshot crawl kể cả khi shell dev đặt SEED_SNAPSHOT=1.
os.environ["SEED_SNAPSHOT"] = "0"

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import get_settings

BACKEND = Path(__file__).resolve().parent.parent


def _load_ddl() -> str:
    path = BACKEND / "app/db/migrations/versions/0001_initial.py"
    spec = importlib.util.spec_from_file_location("mig0001", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ddls = [mod.DDL]
    # Migration bổ sung (nếu có) khai báo biến DDL tương tự, chạy theo thứ tự tên file.
    for extra in sorted((BACKEND / "app/db/migrations/versions").glob("0*.py")):
        if extra.name.startswith("0001"):
            continue
        s = importlib.util.spec_from_file_location(extra.stem, extra)
        m = importlib.util.module_from_spec(s)
        s.loader.exec_module(m)
        if hasattr(m, "DDL"):
            ddls.append(m.DDL)
    return "\n".join(ddls)


@pytest.fixture(scope="session")
def db_engine():
    base_url = make_url(os.environ.get("TEST_DATABASE_URL") or get_settings().database_url)
    admin_url = base_url.set(database="postgres")
    test_db = f"dezon_test_{uuid.uuid4().hex[:10]}"
    try:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{test_db}"'))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL không khả dụng ({exc.__class__.__name__}); bỏ qua test cần DB")
    engine = create_engine(base_url.set(database=test_db), future=True)
    with engine.begin() as conn:
        conn.exec_driver_sql(_load_ddl())
    # Seed dữ liệu dev vào DB test
    from app.db import seed

    try:
        import app.db.seed_hooks  # noqa: F401
    except ImportError:
        pass
    with Session(engine) as s:
        seed.run(s)
    yield engine
    engine.dispose()
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{test_db}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture
def db(db_engine):
    """Session trong transaction, rollback sau mỗi test (commit() bên trong chỉ tạo savepoint)."""
    conn = db_engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        conn.close()


@pytest.fixture
def client(db):
    """TestClient FastAPI dùng chung session `db` của test."""
    from fastapi.testclient import TestClient

    from app.db.session import get_db
    from app.main import app

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
