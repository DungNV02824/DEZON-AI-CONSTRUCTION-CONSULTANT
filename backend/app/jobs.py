"""Job nền RQ (đặc tả 3.1, 11.2 bước 5–6): đồng bộ, nhập CSV, embedding.

Mỗi job tự mở Session riêng, commit khi xong và trả dict thống kê (lưu trong kết quả job RQ).
Dùng `enqueue("crawl", max_pages=30)` để xếp job vào queue "default" (REDIS_URL).
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings

log = logging.getLogger(__name__)

QUEUE_NAME = "default"


@contextmanager
def _session() -> Iterator[Session]:
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _after_sync(db: Session, changed: list[tuple[str, int]] | None) -> dict[str, Any]:
    """Sau đồng bộ: tính lại profile_completeness (module contractor_service) rồi re-embed."""
    from app.rag.embed import reindex_all, reindex_changed

    completeness = "skipped"
    try:
        from app.services import contractor_service  # do module khác cung cấp — import lười

        fn = getattr(contractor_service, "recompute_profile_completeness", None)
        if callable(fn):
            with db.begin_nested():  # lỗi tính điểm hồ sơ không được chặn bước re-embed
                fn(db)
            completeness = "ok"
    except ImportError as exc:
        log.warning("Chưa có contractor_service.recompute_profile_completeness: %s", exc)
    except Exception as exc:  # noqa: BLE001
        log.exception("Lỗi tính lại profile_completeness: %s", exc)
        completeness = "error"
    chunks = reindex_changed(db, changed) if changed is not None else reindex_all(db)
    return {"profile_completeness": completeness, "reindexed_chunks": chunks}


# ------------------------------------------------------------------ các job
def job_crawl(max_pages: int = 50, **kwargs: Any) -> dict[str, Any]:
    from app.ingestion.crawler.runner import run_crawl

    with _session() as db:
        stats = run_crawl(db, max_pages=max_pages, **kwargs)
        changed = stats.pop("changed_entities", [])
        stats["after_sync"] = _after_sync(db, changed) if changed else {"reindexed_chunks": 0}
    log.info("job_crawl: %s", stats)
    return stats


def job_api_sync(max_pages: int = 100) -> dict[str, Any]:
    from app.ingestion.api_sync import run_api_sync

    with _session() as db:
        stats = run_api_sync(db, max_pages=max_pages)
        changed = stats.pop("changed_entities", [])
        if stats.get("status") == "ok" and changed:
            stats["after_sync"] = _after_sync(db, changed)
    log.info("job_api_sync: %s", stats)
    return stats


def job_import_csv(path: str, kind: str, force: bool = False) -> dict[str, Any]:
    """Nhập đơn giá (`kind="rates"`) / hệ số (`kind="rules"`) từ file CSV/XLSX."""
    from app.ingestion.csv_import import import_file

    with _session() as db:
        report = import_file(db, path, kind, force=force)  # type: ignore[arg-type]
    return report.as_dict()


def job_reindex_all() -> dict[str, Any]:
    from app.rag.embed import reindex_all

    with _session() as db:
        n = reindex_all(db)
    return {"reindexed_chunks": n}


def job_after_sync() -> dict[str, Any]:
    """Tính lại profile_completeness cho toàn bộ nhà thầu rồi re-index toàn bộ documents."""
    with _session() as db:
        return _after_sync(db, None)


def job_daily_sync() -> dict[str, Any]:
    """Đồng bộ hằng ngày: API nếu đã cấu hình DEZON_API_BASE_URL, ngược lại crawl trang công khai."""
    if get_settings().dezon_api_base_url:
        result = job_api_sync()
        if result.get("status") != "skipped":
            return {"source": "api", **result}
        # Có URL nhưng thiếu token -> api_sync bỏ qua; vẫn đồng bộ bằng crawler để dữ liệu không đứng yên.
        log.warning("job_daily_sync: api_sync bị bỏ qua (%s) — chuyển sang crawl", result.get("reason"))
    return {"source": "crawl", **job_crawl()}


JOBS: dict[str, Callable[..., dict[str, Any]]] = {
    "crawl": job_crawl,
    "api_sync": job_api_sync,
    "import_csv": job_import_csv,
    "reindex_all": job_reindex_all,
    "reindex": job_reindex_all,
    "after_sync": job_after_sync,
    "daily_sync": job_daily_sync,
}


def get_queue(connection: Any | None = None):
    from redis import Redis
    from rq import Queue

    conn = connection or Redis.from_url(get_settings().redis_url)
    return Queue(QUEUE_NAME, connection=conn, default_timeout=3600)


def enqueue(name: str, *, connection: Any | None = None, **kwargs: Any):
    """Xếp job theo tên (`crawl`, `api_sync`, `import_csv`, `reindex_all`, `after_sync`, `daily_sync`).
    Trả rq.job.Job (có `.id`)."""
    if name not in JOBS:
        raise ValueError(f"Job không tồn tại: {name}. Hợp lệ: {sorted(JOBS)}")
    # enqueue_call(kwargs=...) để tham số của job (vd `timeout`, `description`) không bị RQ hiểu là tùy chọn
    # của chính RQ như khi truyền thẳng **kwargs vào Queue.enqueue.
    return get_queue(connection).enqueue_call(func=JOBS[name], kwargs=kwargs)
