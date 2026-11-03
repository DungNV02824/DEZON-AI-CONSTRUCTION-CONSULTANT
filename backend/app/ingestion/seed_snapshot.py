"""Nạp snapshot dữ liệu crawl (app/db/seed_data/dezon_snapshot.json) khi seed.

Mặc định KHÔNG làm gì để fixture test (mục 11.3) giữ nguyên. Bật bằng `SEED_SNAPSHOT=1`.
Snapshot được tạo bởi `python -m app.ingestion.crawler.runner --snapshot app/db/seed_data/dezon_snapshot.json`.

Định dạng: {"contractors": [dict trung gian + "_source"], "projects": [...]} — xem docs/DATA_MAPPING.md.

Container chạy seed ở MỌI lần khởi động: snapshot không được ghi đè dữ liệu crawl mới hơn. Bản ghi có nguồn
(`data_sources` cùng URL, đã có raw_hash = dữ liệu thật) được lấy SAU `_source.fetched_at` của snapshot -> bỏ qua.
Đơn giá tổng hợp chỉ dựng lại khi snapshot thật sự thay đổi dữ liệu hoặc chưa có dòng tổng hợp nào.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ConstructionRate, DataSource
from app.evidence import SourceType
from app.ingestion.crawler.fetcher import payload_hash
from app.ingestion.upsert import IngestStats, aware, ingest_page

log = logging.getLogger(__name__)

SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "db" / "seed_data" / "dezon_snapshot.json"


def _parse_dt(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def load_snapshot(db: Session, path: str | Path | None = None) -> dict[str, Any]:
    """Nạp snapshot qua upsert (hồ sơ nhà thầu trước, dự án sau). Trả thống kê.
    `path=None` -> `SNAPSHOT_PATH` (đọc lúc gọi, để có thể đổi qua cấu hình/test)."""
    p = Path(path) if path is not None else SNAPSHOT_PATH
    if not p.exists():
        return {"status": "skipped", "reason": f"Không có file snapshot {p}"}
    data = json.loads(p.read_text(encoding="utf-8"))
    stats = IngestStats()
    newer_in_db = 0
    for key in ("contractors", "projects"):
        for rec in data.get(key) or []:
            src = rec.pop("_source", {}) or {}
            rec.setdefault("kind", "contractor" if key == "contractors" else "project")
            rec["source_updated_at"] = _parse_dt(rec.get("source_updated_at"))
            url = src.get("url") or rec.get("profile_url") or rec.get("project_url")
            fetched_at = _parse_dt(src.get("fetched_at"))
            stats.pages += 1
            if _db_has_newer(db, url, fetched_at):
                newer_in_db += 1  # đã crawl sau snapshot -> giữ dữ liệu mới hơn trong DB
                continue
            outcomes = ingest_page(db, [rec], url=url, source_type=SourceType.crawl_public,
                                   raw_hash=src.get("raw_hash") or payload_hash(rec), fetched_at=fetched_at)
            for out in outcomes:
                stats.add(out)
    db.flush()
    return {"status": "ok", **stats.as_dict(), "newer_in_db": newer_in_db}


def _db_has_newer(db: Session, url: str | None, snapshot_fetched_at: datetime | None) -> bool:
    """Nguồn cùng URL đã được nạp từ dữ liệu thật (có raw_hash) và lấy SAU thời điểm của snapshot?
    Snapshot không ghi ngày lấy -> coi như cũ hơn mọi dữ liệu thật đã có. Nguồn không có raw_hash (vd fixture dev)
    -> snapshot được ghi đè."""
    if not url:
        return False
    ds = db.scalar(select(DataSource).where(DataSource.url == url))
    if ds is None or not ds.raw_hash:
        return False
    if snapshot_fetched_at is None:
        return True
    return ds.fetched_at is not None and aware(ds.fetched_at) > aware(snapshot_fetched_at)


def _has_active_market_rates(db: Session) -> bool:
    from app.services.market_rates import MARKET_LABEL_PREFIX

    return db.scalar(
        select(ConstructionRate.id).join(DataSource, DataSource.id == ConstructionRate.source_id)
        .where(DataSource.label.startswith(MARKET_LABEL_PREFIX, autoescape=True),
               ConstructionRate.effective_to.is_(None)).limit(1)) is not None


def seed_hook(db: Session) -> None:
    if os.environ.get("SEED_SNAPSHOT") != "1":
        return
    result = load_snapshot(db)
    log.info("Seed snapshot: %s", result)
    changed = result.get("created", 0) + result.get("updated", 0)
    if not changed and _has_active_market_rates(db):
        log.info("Snapshot không đổi dữ liệu -> giữ nguyên đơn giá tổng hợp hiện có")
        return
    from app.services.market_rates import rebuild_market_rates

    rates = rebuild_market_rates(db)
    log.info("Đơn giá tổng hợp từ hồ sơ nhà thầu: created=%s updated=%s expired=%s",
             rates["created"], rates["updated"], rates["expired"])
