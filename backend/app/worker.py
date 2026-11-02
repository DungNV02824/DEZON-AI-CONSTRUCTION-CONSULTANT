"""RQ worker + bộ lập lịch nhẹ (đặc tả 11.2 bước 6: đồng bộ hằng ngày 02:00 Asia/Ho_Chi_Minh).

    python -m app.worker            # worker queue "default" + luồng lập lịch
    python -m app.worker --no-scheduler

Redis chưa sẵn sàng -> thử lại với thời gian chờ tăng dần (tối đa 60 giây), không thoát.
Lịch: mỗi ngày lúc 02:00 giờ Việt Nam xếp `daily_sync` (API nếu có DEZON_API_BASE_URL, ngược lại crawl).
Chống xếp trùng khi chạy nhiều worker: khóa Redis `dezon:daily_sync:<ngày>` (SET NX, hết hạn 23 giờ).
"""
from __future__ import annotations

import argparse
import logging
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import get_settings

log = logging.getLogger("app.worker")

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
DAILY_HOUR, DAILY_MINUTE = 2, 0


def next_run_at(now: datetime, hour: int = DAILY_HOUR, minute: int = DAILY_MINUTE) -> datetime:
    """Thời điểm 02:00 (giờ VN) kế tiếp sau `now` (now có tz bất kỳ)."""
    local = now.astimezone(TZ)
    target = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= local:
        target += timedelta(days=1)
    return target


def connect_redis(url: str | None = None, max_wait: float = 60.0, stop: threading.Event | None = None):
    """Kết nối Redis, thử lại với backoff tới khi được (hoặc `stop` được set)."""
    from redis import Redis
    from redis.exceptions import RedisError

    url = url or get_settings().redis_url
    delay = 1.0
    while True:
        try:
            conn = Redis.from_url(url)
            conn.ping()
            log.info("Đã kết nối Redis %s", url)
            return conn
        except (RedisError, OSError) as exc:
            log.warning("Redis chưa sẵn sàng (%s) — thử lại sau %.0f giây", exc, delay)
            if stop is not None and stop.wait(delay):
                return None
            if stop is None:
                time.sleep(delay)
            delay = min(delay * 2, max_wait)


def enqueue_daily_sync(conn, when: datetime) -> bool:
    """Xếp job daily_sync một lần cho ngày `when` (khóa NX). Trả True nếu đã xếp."""
    from app.jobs import enqueue

    key = f"dezon:daily_sync:{when.astimezone(TZ).date().isoformat()}"
    if not conn.set(key, "1", nx=True, ex=23 * 3600):
        return False
    job = enqueue("daily_sync", connection=conn)
    log.info("Đã xếp daily_sync (job %s) cho %s", getattr(job, "id", "?"), when.isoformat())
    return True


def scheduler_loop(stop: threading.Event, check_every: float = 30.0) -> None:
    """Luồng nền: ngủ tới 02:00 kế tiếp rồi xếp job. Mất kết nối Redis -> kết nối lại."""
    target = next_run_at(datetime.now(TZ))
    log.info("Lịch đồng bộ kế tiếp: %s", target.isoformat())
    conn = None
    while not stop.is_set():
        now = datetime.now(TZ)
        if now >= target:
            try:
                conn = conn or connect_redis(stop=stop)
                if conn is not None:
                    enqueue_daily_sync(conn, target)
                target = next_run_at(now)
                log.info("Lịch đồng bộ kế tiếp: %s", target.isoformat())
            except Exception as exc:  # noqa: BLE001 — lỗi Redis: thử lại ở vòng sau
                log.warning("Không xếp được daily_sync: %s", exc)
                conn = None
        stop.wait(min(check_every, max(1.0, (target - now).total_seconds())))


def main() -> None:  # pragma: no cover — tiến trình dài hạn
    ap = argparse.ArgumentParser(description="Dezon AI worker (RQ)")
    ap.add_argument("--no-scheduler", action="store_true")
    ap.add_argument("--burst", action="store_true", help="Chạy hết job đang chờ rồi thoát")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    import os

    from redis.exceptions import ConnectionError as RedisConnectionError
    from rq import Queue, SimpleWorker, Worker

    from app.jobs import QUEUE_NAME

    worker_cls = SimpleWorker if os.name == "nt" else Worker  # Windows không có fork (chỉ dùng khi dev)

    stop = threading.Event()
    if not args.no_scheduler and not args.burst:
        threading.Thread(target=scheduler_loop, args=(stop,), name="dezon-scheduler", daemon=True).start()
    try:
        while True:
            conn = connect_redis()
            try:
                worker = worker_cls([Queue(QUEUE_NAME, connection=conn)], connection=conn)
                worker.work(burst=args.burst, with_scheduler=False)
                if args.burst:
                    break
            except RedisConnectionError as exc:
                log.warning("Mất kết nối Redis (%s) — kết nối lại", exc)
                time.sleep(2)
    finally:
        stop.set()


if __name__ == "__main__":  # pragma: no cover
    main()
