"""Chạy một lượt crawl trang công khai dezon.vn -> upsert vào DB.

    python -m app.ingestion.crawler.runner --max-pages 30 [--snapshot app/db/seed_data/dezon_snapshot.json]

Chỉ chạy qua job nền (app.jobs.job_crawl) hoặc CLI của đội vận hành — KHÔNG chạy khi khách hỏi.
Thứ tự: danh sách nhà thầu -> hồ sơ nhà thầu -> công trình thực tế của nhà thầu -> danh sách & trang dự án.
Mỗi trang = 1 bản ghi data_sources; trang có raw_hash không đổi -> "unchanged".
"""
from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.evidence import SourceType
from app.ingestion.crawler import parsers
from app.ingestion.crawler.fetcher import FetchBudgetExceeded, Fetcher, FetchResult, RobotsDisallowed
from app.ingestion.upsert import IngestStats, ingest_page

log = logging.getLogger(__name__)

DEFAULT_LIST_PATHS = ("/nha-thau/tiem-nang/", "/nha-thau/")


def run_crawl(db: Session, max_pages: int = 50, *, fetcher: Fetcher | None = None, site_url: str | None = None,
              list_paths: tuple[str, ...] = DEFAULT_LIST_PATHS, max_contractors: int | None = None,
              works_per_contractor: int = 3, include_project_pages: bool = True,
              max_project_list_pages: int = 1, snapshot_path: str | Path | None = None,
              commit: bool = True, on_page: Callable[[str, str], None] | None = None) -> dict[str, Any]:
    """Crawl tối đa `max_pages` trang (không tính robots.txt). Trả thống kê
    `{pages, created, updated, unchanged, skipped, errors, disallowed, changed_entities}`.

    `snapshot_path`: ghi các dict trung gian đã parse ra JSON (dùng cho seed_snapshot).
    """
    site = (site_url or get_settings().dezon_site_url).rstrip("/")
    own_fetcher = fetcher is None
    fetcher = fetcher or Fetcher()
    stats = IngestStats()
    snapshot: dict[str, list[dict[str, Any]]] = {"contractors": [], "projects": []}
    queue_profiles: list[str] = []
    seen: set[str] = set()

    def fetch(url: str) -> FetchResult | None:
        if url in seen:
            return None
        if stats.pages >= max_pages:
            raise FetchBudgetExceeded(f"Đã đạt max_pages={max_pages}")
        seen.add(url)
        try:
            res = fetcher.fetch(url)
        except RobotsDisallowed:
            log.info("robots.txt chặn %s — bỏ qua", url)
            stats.disallowed += 1
            return None
        except FetchBudgetExceeded:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("Lỗi tải %s: %s", url, exc)
            stats.errors += 1
            return None
        stats.pages += 1
        if not res.ok:
            log.warning("HTTP %s khi tải %s", res.status_code, url)
            stats.errors += 1
            return None
        if on_page:
            on_page(url, res.raw_hash)
        return res

    def store(records: list[dict[str, Any]], res: FetchResult, *, label: str | None = None,
              primary: bool = True, create_missing: bool = False) -> None:
        try:
            with db.begin_nested():  # lỗi ghi một trang chỉ hoàn tác trang đó
                outcomes = ingest_page(db, records, url=res.final_url or res.url, label=label,
                                       source_type=SourceType.crawl_public, raw_hash=res.raw_hash,
                                       fetched_at=res.fetched_at, primary=primary,
                                       create_missing_contractors=create_missing)
        except Exception as exc:  # noqa: BLE001
            log.warning("Lỗi ghi dữ liệu trang %s: %s", res.url, exc)
            stats.errors += 1
            return
        for out in outcomes:
            stats.add(out)
        if commit:
            db.commit()

    def parse(fn: Callable[..., Any], res: FetchResult) -> Any:
        """Parser lỗi với HTML lạ -> đếm lỗi, bỏ qua trang, không dừng cả lượt crawl."""
        try:
            return fn(res.html, res.final_url or res.url)
        except Exception as exc:  # noqa: BLE001
            log.warning("Không parse được %s: %s", res.url, exc)
            stats.errors += 1
            return None

    try:
        # 1) danh sách nhà thầu
        for path in list_paths:
            res = fetch(f"{site}{path}")
            if res is None:
                continue
            cards = parse(parsers.parse_contractor_list, res)
            if not cards:
                continue
            store(cards, res, label="Danh sách nhà thầu trên Dezon", primary=False)
            for c in cards:
                if c["profile_url"] not in queue_profiles:
                    queue_profiles.append(c["profile_url"])
        if max_contractors is not None:
            queue_profiles = queue_profiles[:max_contractors]
        # 2) hồ sơ + công trình thực tế
        for purl in queue_profiles:
            res = fetch(purl)
            if res is None:
                continue
            rec = parse(parsers.parse_contractor_profile, res)
            if not rec or not rec.get("slug") or not rec.get("name"):
                stats.skipped += 1
                continue
            snapshot["contractors"].append(_snap(rec, res))
            store([rec], res)
            for work in (rec.get("works") or [])[:works_per_contractor]:
                wres = fetch(work["url"])
                if wres is None:
                    continue
                prec = parse(parsers.parse_contractor_work, wres)
                if not prec:
                    continue
                snapshot["projects"].append(_snap(prec, wres))
                store([prec], wres, label=f"Công trình {prec['name']} ({rec['name']}) trên Dezon")
        # 3) danh sách dự án /du-an/
        if include_project_pages:
            page_url: str | None = f"{site}/du-an/"
            for _ in range(max_project_list_pages):
                if not page_url:
                    break
                res = fetch(page_url)
                if res is None:
                    break
                listing = parse(parsers.parse_project_list, res)
                if not listing:
                    break
                for item in listing["items"]:
                    pres = fetch(item["project_url"])
                    if pres is None:
                        continue
                    prec = parse(parsers.parse_project_page, pres)
                    if not prec:
                        continue
                    snapshot["projects"].append(_snap(prec, pres))
                    store([prec], pres)
                page_url = listing["next_page"]
    except FetchBudgetExceeded as exc:
        log.info("Dừng crawl: %s", exc)
    finally:
        if own_fetcher:
            fetcher.close()

    if snapshot_path:
        write_snapshot(snapshot, snapshot_path)
    result = stats.as_dict()
    result["changed_entities"] = stats.changed_entities
    return result


def _snap(rec: dict[str, Any], res: FetchResult) -> dict[str, Any]:
    return {**rec, "_source": {"url": res.final_url or res.url, "fetched_at": res.fetched_at.isoformat(),
                               "raw_hash": res.raw_hash}}


def write_snapshot(snapshot: dict[str, Any], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def main() -> None:  # pragma: no cover — CLI vận hành
    ap = argparse.ArgumentParser(description="Crawl dezon.vn (tôn trọng robots.txt, delay >= 2s)")
    ap.add_argument("--max-pages", type=int, default=30)
    ap.add_argument("--max-contractors", type=int, default=None)
    ap.add_argument("--works-per-contractor", type=int, default=3)
    ap.add_argument("--no-projects", action="store_true")
    ap.add_argument("--snapshot", default=None)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        stats = run_crawl(db, args.max_pages, max_contractors=args.max_contractors,
                          works_per_contractor=args.works_per_contractor,
                          include_project_pages=not args.no_projects, snapshot_path=args.snapshot)
    stats.pop("changed_entities", None)
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":  # pragma: no cover
    main()
