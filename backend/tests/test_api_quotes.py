"""REST yêu cầu báo giá: lưu quote_requests kèm estimate_session_id (nghiệm thu mục 13)."""
import uuid

from sqlalchemy import func, select

from app.db.models import AnalyticsEvent, Contractor, QuoteRequest

ESTIMATE = {"building_type": "nha_pho", "province": "TP. Hồ Chí Minh", "land_area_m2": 100, "floors": 4,
            "foundation_type": "foundation_pile", "roof_type": "roof_concrete", "work_scope": "xay_tho"}


def _two_ids(db):
    return list(db.scalars(select(Contractor.id).where(Contractor.is_active.is_(True)).order_by(Contractor.id)
                           .limit(2)))


def _body(ids, **extra):
    return {"contractor_ids": ids, "customer_name": "Nguyễn Văn A", "customer_phone": "+84 912.345.678",
            "customer_email": "a@example.com", "note": "Cần báo giá trước tháng 12", **extra}


def test_quote_request_saves_estimate_session_id(client, db):
    est = client.post("/api/estimates", json=ESTIMATE).json()
    ids = _two_ids(db)
    r = client.post("/api/quote-requests", json=_body(ids, estimate_session_id=est["estimate_session_id"]))
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == {"ids", "code"}
    assert len(body["ids"]) == 2 and body["code"].startswith("DZ-")
    rows = db.scalars(select(QuoteRequest).where(QuoteRequest.id.in_([uuid.UUID(i) for i in body["ids"]]))).all()
    assert len(rows) == 2
    assert {r.contractor_id for r in rows} == set(ids)
    assert all(str(r.estimate_session_id) == est["estimate_session_id"] for r in rows)
    assert all(r.customer_phone == "0912345678" and r.status == "new" for r in rows)
    ev = db.scalars(select(AnalyticsEvent).where(AnalyticsEvent.event == "quote_request_created")).all()
    assert any(e.props.get("code") == body["code"] for e in ev)


def test_quote_request_without_estimate(client, db):
    ids = _two_ids(db)[:1]
    r = client.post("/api/quote-requests", json=_body(ids, estimate_session_id=None, customer_email=None, note=None))
    assert r.status_code == 201
    row = db.get(QuoteRequest, uuid.UUID(r.json()["ids"][0]))
    assert row.estimate_session_id is None and row.customer_email is None


def test_quote_request_validation_vietnamese(client, db):
    ids = _two_ids(db)
    r = client.post("/api/quote-requests", json=_body(ids, customer_phone="12345"))
    assert r.status_code == 422 and "Số điện thoại không hợp lệ" in r.json()["detail"]
    r = client.post("/api/quote-requests", json=_body(ids, customer_email="khong-phai-email"))
    assert r.status_code == 422 and "Email không hợp lệ" in r.json()["detail"]
    r = client.post("/api/quote-requests", json=_body([]))
    assert r.status_code == 422 and "danh sách nhà thầu" in r.json()["detail"]
    r = client.post("/api/quote-requests", json=_body([1, 2, 3, 4, 5, 6]))
    assert r.status_code == 422
    r = client.post("/api/quote-requests", json=_body(ids, customer_name=""))
    assert r.status_code == 422 and "họ tên" in r.json()["detail"]


def test_quote_request_unknown_references(client, db):
    ids = _two_ids(db)
    r = client.post("/api/quote-requests", json=_body([999999999]))
    assert r.status_code == 422 and "Không tìm thấy nhà thầu" in r.json()["detail"]
    r = client.post("/api/quote-requests", json=_body(ids, estimate_session_id=str(uuid.uuid4())))
    assert r.status_code == 422 and "phiên khái toán" in r.json()["detail"]
    r = client.post("/api/quote-requests", json=_body([2**63]))
    assert r.status_code == 422


def test_quote_request_blank_name_is_422(client, db):
    before = db.scalar(select(func.count()).select_from(QuoteRequest))
    r = client.post("/api/quote-requests", json=_body(_two_ids(db), customer_name="   "))
    assert r.status_code == 422 and "họ tên" in r.json()["detail"]
    assert db.scalar(select(func.count()).select_from(QuoteRequest)) == before
