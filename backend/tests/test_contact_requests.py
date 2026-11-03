"""Yêu cầu liên hệ nhân viên: POST /api/contact-requests (công khai) + admin danh sách/cập nhật + tab trang /admin."""
import re
import uuid

import pytest
from sqlalchemy import func, select, update

from app.config import get_settings
from app.db.models import AnalyticsEvent, ChatSession, ContactRequest, Contractor
from app.main import app
from app.services import chat_service
from app.services.chat_service import MemoryRateLimiter
from app.services.contact_service import project_summary

TOKEN = "test-admin-token"
H = {"X-Admin-Token": TOKEN}
HCM = "TP. Hồ Chí Minh"
SLOTS = {"building_type": "nha_pho", "province": HCM, "land_area_m2": 100, "floors": 4, "work_scope": "xay_tho"}


@pytest.fixture(autouse=True)
def _admin_token(monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_token", TOKEN)


@pytest.fixture(autouse=True)
def _no_contact_rate_limit():
    # Các test gửi nhiều yêu cầu từ cùng IP "testclient": tắt giới hạn (test riêng kiểm tra 429 bên dưới).
    app.dependency_overrides[chat_service.get_contact_rate_limiter] = lambda: MemoryRateLimiter(10_000)
    yield
    app.dependency_overrides.pop(chat_service.get_contact_rate_limiter, None)


def _body(**extra):
    return {"customer_name": "  Nguyễn Văn A  ", "customer_phone": "+84 912.345.678", **extra}


def _contractor(db) -> Contractor:
    return db.scalars(select(Contractor).where(Contractor.is_active.is_(True)).order_by(Contractor.id)).first()


def _count(db) -> int:
    return db.scalar(select(func.count()).select_from(ContactRequest))


# ------------------------------------------------------------------ tạo (công khai)
def test_create_contact_request_normalizes_and_logs(client, db):
    chat = ChatSession(id=uuid.uuid4(), slots=SLOTS)
    db.add(chat)
    db.commit()
    c = _contractor(db)
    r = client.post("/api/contact-requests", json=_body(
        note="  Gọi sau 17h  ", chat_session_id=str(chat.id), contractor_id=c.id, source="contractor_card",
        page_url="https://dezon.vn/nha-thau/abc"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == {"id", "code"}
    assert re.fullmatch(r"LH-[0-9A-Z]{6}", body["code"])
    row = db.get(ContactRequest, uuid.UUID(body["id"]))
    assert row.customer_name == "Nguyễn Văn A" and row.customer_phone == "0912345678"
    assert row.note == "Gọi sau 17h" and row.status == "new" and row.code == body["code"]
    assert row.chat_session_id == chat.id and row.contractor_id == c.id
    assert row.source == "contractor_card" and row.page_url == "https://dezon.vn/nha-thau/abc"
    ev = db.scalars(select(AnalyticsEvent).where(AnalyticsEvent.event == "contact_requested")).all()
    assert any(e.props == {"code": body["code"], "source": "contractor_card", "has_contractor": True}
               and e.session_id == chat.id for e in ev)


def test_create_minimal_and_blank_optionals(client, db):
    r = client.post("/api/contact-requests", json={"customer_name": "Chị Lan", "customer_phone": "0987 654 321",
                                                   "note": "   ", "source": None, "page_url": ""})
    assert r.status_code == 201, r.text
    row = db.get(ContactRequest, uuid.UUID(r.json()["id"]))
    assert row.customer_phone == "0987654321"
    assert row.note is None and row.source is None and row.page_url is None
    assert row.chat_session_id is None and row.contractor_id is None


def test_idempotent_within_ten_minutes(client, db):
    c = _contractor(db)
    first = client.post("/api/contact-requests", json=_body(contractor_id=c.id, source="contractor_card"))
    # Cùng SĐT (viết khác), cùng nhà thầu, không phiên chat -> trả lại yêu cầu cũ, không tạo trùng.
    again = client.post("/api/contact-requests", json=_body(customer_phone="0912 345 678", contractor_id=c.id,
                                                            source="composer"))
    assert first.status_code == again.status_code == 201
    assert again.json() == first.json()
    assert _count(db) == 1
    # Khác nhà thầu -> yêu cầu mới.
    other = client.post("/api/contact-requests", json=_body())
    assert other.status_code == 201 and other.json()["id"] != first.json()["id"]
    assert _count(db) == 2
    # Quá 10 phút -> yêu cầu mới.
    db.execute(update(ContactRequest).values(created_at=func.now() - func.make_interval(0, 0, 0, 0, 0, 11)))
    db.commit()
    later = client.post("/api/contact-requests", json=_body(contractor_id=c.id))
    assert later.status_code == 201 and later.json()["id"] != first.json()["id"]
    assert _count(db) == 3


def test_idempotency_distinguishes_chat_session(client, db):
    chat = ChatSession(id=uuid.uuid4())
    db.add(chat)
    db.commit()
    a = client.post("/api/contact-requests", json=_body(chat_session_id=str(chat.id))).json()
    b = client.post("/api/contact-requests", json=_body()).json()
    assert a["id"] != b["id"]
    assert client.post("/api/contact-requests", json=_body(chat_session_id=str(chat.id))).json() == a


def test_unknown_session_and_contractor_ignored(client, db):
    r = client.post("/api/contact-requests", json=_body(chat_session_id=str(uuid.uuid4()), contractor_id=999_999_999))
    assert r.status_code == 201, r.text
    row = db.get(ContactRequest, uuid.UUID(r.json()["id"]))
    assert row.chat_session_id is None and row.contractor_id is None
    ev = db.scalars(select(AnalyticsEvent).where(AnalyticsEvent.event == "contact_requested")).all()
    assert any(e.props.get("code") == row.code and e.props.get("has_contractor") is False for e in ev)


@pytest.mark.parametrize("extra,needle", [
    ({"customer_phone": "12345"}, "Số điện thoại không hợp lệ"),
    ({"customer_phone": ""}, "Số điện thoại không hợp lệ"),
    ({"customer_name": "   "}, "Vui lòng nhập họ tên"),
    ({"customer_name": ""}, "Vui lòng nhập họ tên"),
    ({"customer_name": "A" * 201}, "họ tên: tối đa 200 ký tự"),
    ({"source": "hacker"}, "Nguồn yêu cầu liên hệ không hợp lệ"),
    ({"source": "x" * 60}, "Nguồn yêu cầu liên hệ không hợp lệ"),
    ({"source": 123}, "Nguồn yêu cầu liên hệ không hợp lệ"),
    ({"note": "x" * 1001}, "ghi chú: tối đa 1000 ký tự"),
    ({"page_url": "https://a.vn/" + "x" * 500}, "địa chỉ trang: tối đa 500 ký tự"),
    ({"contractor_id": 0}, "Mã nhà thầu không hợp lệ"),
    ({"contractor_id": 2**63}, "Mã nhà thầu không hợp lệ"),
    ({"chat_session_id": "khong-phai-uuid"}, "mã phiên chat"),
])
def test_validation_vietnamese(client, db, extra, needle):
    r = client.post("/api/contact-requests", json=_body(**extra))
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert isinstance(detail, str) and needle in detail
    assert _count(db) == 0


def test_missing_fields_422(client, db):
    r = client.post("/api/contact-requests", json={"customer_name": "A"})
    assert r.status_code == 422 and "số điện thoại: thiếu thông tin bắt buộc" in r.json()["detail"]


def test_name_trimmed_before_length_check(client, db):
    r = client.post("/api/contact-requests", json=_body(customer_name="  " + "A" * 200 + "  "))
    assert r.status_code == 201, r.text


# ------------------------------------------------------------------ tóm tắt công trình
def test_project_summary():
    assert project_summary(SLOTS) == "Nhà phố · TP. Hồ Chí Minh · 100 m² · 4 tầng · Xây thô"
    assert project_summary({**SLOTS, "district": "Quận 7", "has_mezzanine": True, "budget_label": "2–4 tỷ"}) == (
        "Nhà phố · Quận 7, TP. Hồ Chí Minh · 100 m² · 4 tầng + lửng · Xây thô · Ngân sách 2–4 tỷ")
    assert project_summary({}) is None
    assert project_summary(None) is None
    assert project_summary({"_state": {"stage": "collect"}}) is None


# ------------------------------------------------------------------ admin
@pytest.mark.parametrize("method,path", [
    ("get", "/api/admin/contact-requests"),
    ("patch", f"/api/admin/contact-requests/{uuid.uuid4()}"),
])
def test_admin_requires_token(client, method, path):
    r = getattr(client, method)(path)
    assert r.status_code == 401
    r = getattr(client, method)(path, headers={"X-Admin-Token": "sai-ma"})
    assert r.status_code == 401


def _seed_requests(client, db):
    chat = ChatSession(id=uuid.uuid4(), slots=SLOTS)
    db.add(chat)
    db.commit()
    c = _contractor(db)
    a = client.post("/api/contact-requests", json=_body(chat_session_id=str(chat.id), contractor_id=c.id,
                                                        source="contractor_detail", note="Cần tư vấn")).json()
    b = client.post("/api/contact-requests", json={"customer_name": "<b>Chị Lan</b>",
                                                   "customer_phone": "0987654321", "source": "composer"}).json()
    return chat, c, a, b


def test_admin_list_items_and_counts(client, db):
    chat, c, a, b = _seed_requests(client, db)
    r = client.get("/api/admin/contact-requests", headers=H)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 2 and data["new_count"] == 2
    by_id = {i["id"]: i for i in data["items"]}
    ia = by_id[a["id"]]
    assert set(ia) == {"id", "code", "customer_name", "customer_phone", "note", "source", "page_url", "status",
                       "handled_note", "created_at", "contractor", "project"}
    assert ia["code"] == a["code"] and ia["customer_name"] == "Nguyễn Văn A" and ia["customer_phone"] == "0912345678"
    assert ia["contractor"] == {"id": c.id, "name": c.name}
    assert ia["project"] == "Nhà phố · TP. Hồ Chí Minh · 100 m² · 4 tầng · Xây thô"
    assert ia["source"] == "contractor_detail" and ia["note"] == "Cần tư vấn" and ia["status"] == "new"
    ib = by_id[b["id"]]
    assert ib["contractor"] is None and ib["project"] is None
    assert ib["customer_name"] == "<b>Chị Lan</b>"  # lưu nguyên văn; trang admin hiển thị bằng textContent


def test_admin_list_order_limit_offset(client, db):
    _, _, a, b = _seed_requests(client, db)
    db.execute(update(ContactRequest).where(ContactRequest.id == uuid.UUID(a["id"]))
               .values(created_at=func.now() - func.make_interval(0, 0, 0, 1)))
    db.commit()
    items = client.get("/api/admin/contact-requests", headers=H).json()["items"]
    assert [i["id"] for i in items] == [b["id"], a["id"]]  # mới nhất trước
    page = client.get("/api/admin/contact-requests", headers=H, params={"limit": 1, "offset": 1}).json()
    assert [i["id"] for i in page["items"]] == [a["id"]] and page["total"] == 2
    assert client.get("/api/admin/contact-requests", headers=H, params={"limit": 0}).status_code == 422


def test_admin_patch_status_and_filters(client, db):
    _, _, a, b = _seed_requests(client, db)
    r = client.patch(f"/api/admin/contact-requests/{a['id']}", headers=H,
                     json={"status": "contacted", "handled_note": "  Đã gọi, hẹn gặp thứ 7  "})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "contacted" and out["handled_note"] == "Đã gọi, hẹn gặp thứ 7"
    assert out["contractor"] is not None and out["project"]

    new = client.get("/api/admin/contact-requests", headers=H, params={"status": "new"}).json()
    assert [i["id"] for i in new["items"]] == [b["id"]] and new["total"] == 1 and new["new_count"] == 1
    contacted = client.get("/api/admin/contact-requests", headers=H, params={"status": "contacted"}).json()
    assert [i["id"] for i in contacted["items"]] == [a["id"]] and contacted["new_count"] == 1
    assert client.get("/api/admin/contact-requests", headers=H, params={"status": "done"}).json()["total"] == 0

    # Chỉ gửi trạng thái -> giữ ghi chú; gửi handled_note null -> xóa ghi chú.
    r = client.patch(f"/api/admin/contact-requests/{a['id']}", headers=H, json={"status": "done"})
    assert r.json()["status"] == "done" and r.json()["handled_note"] == "Đã gọi, hẹn gặp thứ 7"
    r = client.patch(f"/api/admin/contact-requests/{a['id']}", headers=H, json={"handled_note": None})
    assert r.json()["status"] == "done" and r.json()["handled_note"] is None
    assert db.get(ContactRequest, uuid.UUID(a["id"])).status == "done"


def test_admin_patch_errors(client, db):
    _, _, a, _ = _seed_requests(client, db)
    r = client.patch(f"/api/admin/contact-requests/{a['id']}", headers=H, json={"status": "xong"})
    assert r.status_code == 422 and "trạng thái" in r.json()["detail"]
    r = client.patch(f"/api/admin/contact-requests/{a['id']}", headers=H, json={"handled_note": "x" * 1001})
    assert r.status_code == 422 and "ghi chú xử lý" in r.json()["detail"]
    r = client.patch(f"/api/admin/contact-requests/{uuid.uuid4()}", headers=H, json={"status": "done"})
    assert r.status_code == 404 and "Không tìm thấy yêu cầu liên hệ" in r.json()["detail"]
    r = client.patch("/api/admin/contact-requests/abc", headers=H, json={"status": "done"})
    assert r.status_code == 422 and "mã yêu cầu liên hệ" in r.json()["detail"]
    r = client.get("/api/admin/contact-requests", headers=H, params={"status": "xong"})
    assert r.status_code == 422


def test_deleting_contractor_or_session_keeps_request(client, db):
    chat, c, a, _ = _seed_requests(client, db)
    db.delete(chat)
    db.commit()
    db.expire_all()
    row = db.get(ContactRequest, uuid.UUID(a["id"]))
    assert row is not None and row.chat_session_id is None and row.contractor_id == c.id


def test_admin_page_has_contact_tab(client):
    html = client.get("/admin").text
    assert 'data-tab="contacts"' in html and "Yêu cầu liên hệ" in html
    # Tab ngay sau "Chỉ số".
    assert html.index('data-tab="metrics"') < html.index('data-tab="contacts"') < html.index('data-tab="violations"')
    assert "/api/admin/contact-requests" in html and "contactCount" in html
    for label in ("Ô chat", "Thẻ nhà thầu", "Hồ sơ nhà thầu", "Đã liên hệ", "tel:"):
        assert label in html


# ------------------------------------------------------------------ sửa theo review
def test_duplicate_records_newer_details(client, db):
    first = client.post("/api/contact-requests", json=_body(note="Gọi buổi sáng")).json()
    again = client.post("/api/contact-requests", json=_body(customer_name="Nguyễn Văn B", note="Hỏi về móng",
                                                            page_url="https://dezon.vn/planning")).json()
    assert again == first and _count(db) == 1  # không tạo trùng
    row = db.get(ContactRequest, uuid.UUID(first["id"]))
    db.refresh(row)
    assert row.customer_name == "Nguyễn Văn B"
    assert row.note == "Gọi buổi sáng\nHỏi về móng"
    assert row.page_url == "https://dezon.vn/planning"


def test_handled_request_is_not_reused(client, db):
    first = client.post("/api/contact-requests", json=_body()).json()
    db.execute(update(ContactRequest).values(status="contacted"))
    db.commit()
    again = client.post("/api/contact-requests", json=_body()).json()
    assert again["id"] != first["id"] and _count(db) == 2


def test_rate_limited_per_ip(client, db):
    app.dependency_overrides[chat_service.get_contact_rate_limiter] = lambda: _LIMITER
    codes = [client.post("/api/contact-requests", json=_body(customer_phone=f"090123456{i}")).status_code
             for i in range(3)]
    assert codes == [201, 201, 429]
    r = client.post("/api/contact-requests", json=_body(customer_phone="0901234569"))
    assert r.status_code == 429 and "quá nhiều yêu cầu liên hệ" in r.json()["detail"]
    assert int(r.headers["Retry-After"]) >= 1
    assert _count(db) == 2


_LIMITER = MemoryRateLimiter(2)
