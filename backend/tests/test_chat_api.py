"""Chat API: tạo phiên, lịch sử, SSE, lỗi, validate input, rate limit (bộ nhớ + Redis)."""
import json
import uuid

import pytest

from app.ai.providers.mock import MockProvider
from app.services import chat_service
from app.services.chat_service import MemoryRateLimiter, RedisRateLimiter, split_text, sse_event


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    out = []
    for chunk in body.split("\n\n"):
        if not chunk.strip():
            continue
        lines = dict(line.split(": ", 1) for line in chunk.split("\n"))
        out.append((lines["event"], json.loads(lines["data"])))
    return out


# =============================================================================================
# Thuần
# =============================================================================================
@pytest.mark.parametrize("text", ["", "Dạ.", "Dạ, em tóm tắt lại: Nhà phố tại TP. Hồ Chí Minh.\n\n- dòng 2  ",
                                  "x" * 200, "từ " * 100])
def test_split_text_roundtrip(text):
    parts = split_text(text, max_chars=20)
    assert "".join(parts) == text
    assert all(len(p) <= 20 or " " not in p.strip() for p in parts)


def test_sse_event_format_utf8():
    ev = sse_event("text", {"delta": "Chưa có dữ liệu"})
    assert ev == 'event: text\ndata: {"delta": "Chưa có dữ liệu"}\n\n'


def test_memory_rate_limiter_sliding_window():
    now = [1000.0]
    lim = MemoryRateLimiter(3, window_s=60, clock=lambda: now[0])
    assert [lim.hit("ip")[0] for _ in range(3)] == [True, True, True]
    ok, retry = lim.hit("ip")
    assert not ok and 1 <= retry <= 61
    assert lim.hit("ip-khac")[0]
    now[0] += 61
    assert lim.hit("ip")[0]


class _BrokenRedis:
    def pipeline(self):
        raise ConnectionError("redis down")


def test_redis_rate_limiter_falls_back_to_memory_on_error():
    lim = RedisRateLimiter(_BrokenRedis(), limit=2)
    assert [lim.hit("ip")[0] for _ in range(3)] == [True, True, False]


class _FakeZRedis:
    """Redis giả tối thiểu (sorted set) để kiểm tra logic cửa sổ trượt không cần Redis thật."""

    def __init__(self):
        self.z: dict[str, dict[str, float]] = {}

    def pipeline(self):
        outer = self

        class _P:
            def __init__(self):
                self.ops = []

            def __getattr__(self, name):
                return lambda *a, **k: self.ops.append((name, a, k))

            def execute(self):
                return [getattr(outer, n)(*a, **k) for n, a, k in self.ops]

        return _P()

    def zremrangebyscore(self, key, lo, hi):
        d = self.z.setdefault(key, {})
        for m in [m for m, sc in d.items() if sc <= float(hi)]:
            del d[m]

    def zadd(self, key, mapping):
        self.z.setdefault(key, {}).update(mapping)

    def zcard(self, key):
        return len(self.z.get(key, {}))

    def expire(self, key, ttl):
        return True

    def zrem(self, key, member):
        self.z.get(key, {}).pop(member, None)

    def zrange(self, key, start, end, withscores=False):
        items = sorted(self.z.get(key, {}).items(), key=lambda x: x[1])[start:end + 1]
        return items if withscores else [m for m, _ in items]


def test_redis_rate_limiter_sliding_window_no_double_burst():
    """C-F13: cửa sổ trượt — không cho burst gấp đôi quanh mốc chuyển phút; lượt bị chặn không bị tính."""
    now = [59.0]
    lim = RedisRateLimiter(_FakeZRedis(), limit=20, clock=lambda: now[0])
    assert all(lim.hit("ip")[0] for _ in range(20))
    now[0] = 61.0  # sang phút mới — cửa sổ cố định sẽ cho thêm 20 tin
    ok, retry = lim.hit("ip")
    assert not ok and 1 <= retry <= 60
    assert lim.hit("ip-khac")[0]
    now[0] = 119.5  # các lượt lúc 59.0 đã ra khỏi cửa sổ 60 giây
    assert lim.hit("ip")[0]


def test_redis_rate_limiter_with_real_redis():
    redis = pytest.importorskip("redis")
    from app.config import get_settings

    client = redis.Redis.from_url(get_settings().redis_url, socket_connect_timeout=0.3, socket_timeout=0.3)
    try:
        client.ping()
    except Exception:  # noqa: BLE001
        pytest.skip("Redis không khả dụng")
    lim = RedisRateLimiter(client, limit=20, prefix=f"dezon:test:{uuid.uuid4().hex}", clock=lambda: 120.0)
    results = [lim.hit("1.2.3.4")[0] for _ in range(21)]
    assert results == [True] * 20 + [False]
    assert lim.hit("5.6.7.8")[0]
    for key in client.scan_iter(f"{lim.prefix}:*"):
        client.delete(key)


# =============================================================================================
# HTTP
# =============================================================================================
@pytest.fixture
def api(client):
    from app.api.chat import get_chat_provider
    from app.main import app

    limiter = MemoryRateLimiter(20)
    app.dependency_overrides[chat_service.get_rate_limiter] = lambda: limiter
    app.dependency_overrides[get_chat_provider] = lambda: MockProvider()
    yield client


@pytest.mark.db
def test_create_session_with_and_without_body(api):
    r = api.post("/api/chat/sessions")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"session_id", "stage", "slots", "messages"}
    assert body["stage"] == "collect" and "_state" not in body["slots"] and body["slots"]["confirmed"] is False
    msg = body["messages"][0]
    assert set(msg) == {"id", "role", "content", "ui_blocks", "created_at"}
    assert msg["role"] == "assistant" and msg["ui_blocks"][0]["type"] == "quick_replies"
    assert api.post("/api/chat/sessions", json={"user_id": "khach-1"}).status_code == 200


@pytest.mark.db
def test_get_session_excludes_tool_messages(api):
    sid = api.post("/api/chat/sessions", json={}).json()["session_id"]
    api.post(f"/api/chat/sessions/{sid}/messages", json={"content": "nhà phố 5x20 Thủ Đức 1 trệt 3 lầu xây thô"})
    api.post(f"/api/chat/sessions/{sid}/messages", json={"content": "Đúng rồi"})
    body = api.get(f"/api/chat/sessions/{sid}").json()
    assert set(body) == {"session_id", "stage", "slots", "missing_required", "messages"}
    assert {m["role"] for m in body["messages"]} == {"user", "assistant"}
    assert body["missing_required"] == [] and body["stage"] == "match"
    assert all(isinstance(m["ui_blocks"], list) for m in body["messages"])


@pytest.mark.db
@pytest.mark.parametrize("payload", [{"content": ""}, {"content": "   "}, {"content": "x" * 2001}, {}])
def test_send_message_validation_422(api, payload):
    sid = api.post("/api/chat/sessions", json={}).json()["session_id"]
    r = api.post(f"/api/chat/sessions/{sid}/messages", json=payload)
    assert r.status_code == 422
    # C-F14: định dạng lỗi tiếng Việt gọn như các API khác, không phải lỗi pydantic thô.
    body = r.json()
    assert isinstance(body["detail"], str) and "Value error" not in r.text and "msg" not in r.text
    assert body["errors"] and {"field", "message"} <= set(body["errors"][0])


@pytest.mark.db
def test_max_length_message_accepted(api):
    sid = api.post("/api/chat/sessions", json={}).json()["session_id"]
    r = api.post(f"/api/chat/sessions/{sid}/messages", json={"content": "nhà phố " + "a" * 1990})
    assert r.status_code == 200 and _parse_sse(r.text)[-1][0] == "done"


@pytest.mark.db
def test_sse_error_event_when_orchestrator_fails(api, monkeypatch):
    sid = api.post("/api/chat/sessions", json={}).json()["session_id"]

    def boom(*a, **kw):
        raise RuntimeError("lỗi bất ngờ")

    monkeypatch.setattr(chat_service.orchestrator, "handle_message", boom)
    r = api.post(f"/api/chat/sessions/{sid}/messages", json={"content": "Nhà phố"})
    assert r.status_code == 200
    events = _parse_sse(r.text)
    assert events == [("error", {"message": chat_service.ERROR_MESSAGE})]


@pytest.mark.db
def test_sse_events_order_and_done_payload(api):
    sid = api.post("/api/chat/sessions", json={}).json()["session_id"]
    r = api.post(f"/api/chat/sessions/{sid}/messages", json={"content": "Nhà phố"})
    events = _parse_sse(r.text)
    names = [n for n, _ in events]
    assert names[0] == "text" and names[-1] == "done"
    first_block = names.index("ui_block")
    assert all(n == "text" for n in names[:first_block]) and all(n == "ui_block" for n in names[first_block:-1])
    done = events[-1][1]
    assert set(done) == {"message_id", "stage", "slots", "missing_required"}
    assert done["stage"] == "collect" and done["slots"]["building_type"] == "nha_pho"
    # C-F15: không lộ trạng thái nội bộ của dialog ra client.
    assert "asked" not in done["slots"] and "_state" not in done["slots"]
    assert "asked" not in api.get(f"/api/chat/sessions/{sid}").json()["slots"]
    assert "province" in done["missing_required"]
    assert events[first_block][1]["block"]["type"] == "quick_replies"


@pytest.mark.db
def test_rate_limit_is_per_ip(api):
    from app.main import app

    limiter = MemoryRateLimiter(1)
    app.dependency_overrides[chat_service.get_rate_limiter] = lambda: limiter
    sid = api.post("/api/chat/sessions", json={}).json()["session_id"]
    assert api.post(f"/api/chat/sessions/{sid}/messages", json={"content": "Nhà phố"}).status_code == 200
    assert api.post(f"/api/chat/sessions/{sid}/messages", json={"content": "Nhà phố"}).status_code == 429
    assert limiter.hit("10.0.0.9")[0]  # IP khác không bị ảnh hưởng


def test_default_rate_limiter_from_settings():
    chat_service.get_rate_limiter.cache_clear()
    try:
        lim = chat_service.get_rate_limiter()
        assert isinstance(lim, (MemoryRateLimiter, RedisRateLimiter)) and lim.limit == 20
    finally:
        chat_service.get_rate_limiter.cache_clear()
