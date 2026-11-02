"""Nạp snapshot dữ liệu crawl (app/db/seed_data/dezon_snapshot.json) khi seed.

Mặc định KHÔNG làm gì để fixture test (mục 11.3) giữ nguyên. Bật bằng `SEED_SNAPSHOT=1`.
Snapshot được tạo bởi `python -m app.ingestion.crawler.runner --snapshot app/db/seed_data/dezon_snapshot.json`.

Định dạng: {"contractors": [dict trung gian + "_source"], "projects": [...]} — xem docs/DATA_MAPPING.md.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.evidence import SourceType
from app.ingestion.crawler.fetcher import payload_hash
from app.ingestion.upsert import IngestStats, ingest_page

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
    for key in ("contractors", "projects"):
        for rec in data.get(key) or []:
            src = rec.pop("_source", {}) or {}
            rec.setdefault("kind", "contractor" if key == "contractors" else "project")
            rec["source_updated_at"] = _parse_dt(rec.get("source_updated_at"))
            url = src.get("url") or rec.get("profile_url") or rec.get("project_url")
            outcomes = ingest_page(db, [rec], url=url, source_type=SourceType.crawl_public,
                                   raw_hash=src.get("raw_hash") or payload_hash(rec),
                                   fetched_at=_parse_dt(src.get("fetched_at")))
            stats.pages += 1
            for out in outcomes:
                stats.add(out)
    db.flush()
    return {"status": "ok", **stats.as_dict()}


def seed_hook(db: Session) -> None:
    if os.environ.get("SEED_SNAPSHOT") != "1":
        return
    result = load_snapshot(db)
    log.info("Seed snapshot: %s", result)
