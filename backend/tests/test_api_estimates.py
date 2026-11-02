"""REST khái toán: POST/GET /api/estimates, budget-adjustments, GET /api/rates (mục 10, 13)."""
import uuid

from sqlalchemy import select

from app.db.models import AnalyticsEvent, ChatSession, EstimateSession

ACCEPTANCE = {
    "building_type": "nha_pho", "province": "TP. Hồ Chí Minh", "land_area_m2": 100, "floors": 4,
    "foundation_type": "foundation_pile", "roof_type": "roof_concrete", "work_scope": "xay_tho",
}


def _create(client, **extra):
    r = client.post("/api/estimates", json={**ACCEPTANCE, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_create_estimate_acceptance_490(client, db):
    body = _create(client)
    assert body["converted_area_m2"] == 490
    assert body["estimate_session_id"]
    assert body["cost_min"] and body["cost_max"] and body["cost_min"] <= body["cost_max"]
    assert body["cost"]["display"] and body["rate"]["status"] in ("reference", "on_dezon", "verified")
    assert body["disclaimer"]
    codes = {it["rule_code"] for it in body["items"]}
    assert {"foundation_pile", "floor_standard", "roof_concrete"} <= codes
    row = db.get(EstimateSession, uuid.UUID(body["estimate_session_id"]))
    assert row is not None and float(row.converted_area_m2) == 490
    ev = db.scalars(select(AnalyticsEvent).where(AnalyticsEvent.event == "estimate_created")).all()
    assert any(e.props.get("estimate_session_id") == body["estimate_session_id"] for e in ev)


def test_create_estimate_from_dimensions_and_chat_session(client, db):
    chat = ChatSession(id=uuid.uuid4())
    db.add(chat)
    db.flush()
    data = {k: v for k, v in ACCEPTANCE.items() if k != "land_area_m2"}
    body = _create(client, **{**data, "land_width_m": 5, "land_length_m": 20, "chat_session_id": str(chat.id)})
    assert body["input"]["land_area_m2"] == 100
    row = db.get(EstimateSession, uuid.UUID(body["estimate_session_id"]))
    assert row.chat_session_id == chat.id
    ev = db.scalars(select(AnalyticsEvent).where(AnalyticsEvent.event == "estimate_created",
                                                 AnalyticsEvent.session_id == chat.id)).all()
    assert len(ev) == 1


def test_create_estimate_unknown_chat_session_is_ignored(client, db):
    body = _create(client, chat_session_id=str(uuid.uuid4()))
    row = db.get(EstimateSession, uuid.UUID(body["estimate_session_id"]))
    assert row.chat_session_id is None


def test_create_estimate_validation_errors_vietnamese(client):
    data = {k: v for k, v in ACCEPTANCE.items() if k != "land_area_m2"}
    r = client.post("/api/estimates", json=data)
    assert r.status_code == 422
    assert "land_area_m2" in r.json()["detail"] and "Dữ liệu không hợp lệ" in r.json()["detail"]
    r = client.post("/api/estimates", json={**ACCEPTANCE, "floors": 0})
    assert r.status_code == 422 and "số tầng" in r.json()["detail"]
    r = client.post("/api/estimates", json={**ACCEPTANCE, "building_type": "lau_dai"})
    assert r.status_code == 422 and "loại công trình" in r.json()["detail"]
    r = client.post("/api/estimates", content="{bad json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    assert "Traceback" not in r.text


def test_get_estimate(client):
    created = _create(client)
    r = client.get(f"/api/estimates/{created['estimate_session_id']}")
    assert r.status_code == 200
    got = r.json()
    assert got["converted_area_m2"] == 490 and got["cost_min"] == created["cost_min"]
    assert got["estimate_session_id"] == created["estimate_session_id"]


def test_get_estimate_not_found(client):
    assert client.get(f"/api/estimates/{uuid.uuid4()}").status_code == 404
    r = client.get("/api/estimates/khong-phai-uuid")
    assert r.status_code == 404 and r.json()["detail"] == "Không tìm thấy phiên khái toán."


def test_budget_adjustments(client):
    created = _create(client)
    r = client.post(f"/api/estimates/{created['estimate_session_id']}/budget-adjustments",
                    json={"target_budget_vnd": 1_200_000_000})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target_budget_vnd"] == 1_200_000_000
    assert len(body["options"]) >= 2
    assert all(o["cost"]["display"] for o in body["options"])


def test_budget_adjustments_errors(client):
    created = _create(client)
    sid = created["estimate_session_id"]
    r = client.post(f"/api/estimates/{sid}/budget-adjustments", json={"target_budget_vnd": 0})
    assert r.status_code == 422 and "Ngân sách" in r.json()["detail"]
    r = client.post(f"/api/estimates/{sid}/budget-adjustments", json={"target_budget_vnd": "nhiều"})
    assert r.status_code == 422
    r = client.post(f"/api/estimates/{uuid.uuid4()}/budget-adjustments", json={"target_budget_vnd": 10**9})
    assert r.status_code == 404


def test_get_rates(client):
    r = client.get("/api/rates", params={"building_type": "nha_pho", "province": "HCM", "work_scope": "xay_tho"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rate"]["display"].endswith("triệu/m²")
    assert body["rate_data"]["province"] == "TP. Hồ Chí Minh" and body["rate_data"]["is_fallback"] is False
    assert body["rate"]["source"]["label"]


def test_get_rates_fallback_and_unavailable(client):
    r = client.get("/api/rates", params={"building_type": "nha_pho", "province": "Cà Mau", "work_scope": "xay_tho",
                                         "quality_tier": "kha"})
    assert r.status_code == 200
    assert r.json()["rate_data"]["is_fallback"] is True and r.json()["rate"]["status"] == "reference"
    # Không có đơn giá cho căn hộ xây thô trong seed -> "Chưa có dữ liệu"
    r = client.get("/api/rates", params={"building_type": "can_ho", "province": "HCM", "work_scope": "xay_tho"})
    assert r.status_code == 200
    assert r.json() == {"rate": {"value": None, "display": "Chưa có dữ liệu", "status": "unavailable",
                                 "source": None, "is_stale": False}, "rate_data": None}


def test_get_rates_validation(client):
    r = client.get("/api/rates", params={"building_type": "nha_pho", "province": "HCM", "work_scope": "sai"})
    assert r.status_code == 422 and "phạm vi công việc" in r.json()["detail"]
    r = client.get("/api/rates", params={"building_type": "nha_pho", "work_scope": "xay_tho"})
    assert r.status_code == 422 and "thiếu thông tin bắt buộc" in r.json()["detail"]


# ------------------------------------------------------------------ review: giới hạn đầu vào (không 500)
def test_create_estimate_rejects_blank_province(client):
    for prov in ("", "   "):
        r = client.post("/api/estimates", json={**ACCEPTANCE, "province": prov})
        assert r.status_code == 422, r.text
        assert "tỉnh/thành" in r.json()["detail"]


def test_create_estimate_huge_area_is_422_not_500(client):
    r = client.post("/api/estimates", json={**ACCEPTANCE, "land_area_m2": 1e12})
    assert r.status_code == 422 and "diện tích đất" in r.json()["detail"]
    data = {k: v for k, v in ACCEPTANCE.items() if k != "land_area_m2"}
    r = client.post("/api/estimates", json={**data, "land_width_m": 4000, "land_length_m": 4000})
    assert r.status_code == 422 and "vượt giới hạn" in r.json()["detail"]


def test_create_estimate_overflow_on_save_is_422(client, db):
    """Đơn giá cực lớn (admin nhập) × diện tích lớn tràn NUMERIC(16,0) khi lưu -> 422, phiên DB vẫn dùng được."""
    from datetime import date

    from app.db.models import ConstructionRate, DataSource

    src = DataSource(source_type="dezon_editorial", label="Thử tràn số", status="reference")
    db.add(src)
    db.flush()
    db.add(ConstructionRate(building_type="nha_pho", province="Tỉnh Thử Tràn", work_scope="xay_tho",
                            quality_tier="trung_binh", price_min=900_000_000_000, price_max=999_999_999_999,
                            effective_from=date(2026, 1, 1), source_id=src.id))
    db.flush()
    r = client.post("/api/estimates", json={**ACCEPTANCE, "province": "Tỉnh Thử Tràn", "land_area_m2": 150_000,
                                            "floors": 50})
    assert r.status_code == 422, r.text
    assert "vượt giới hạn" in r.json()["detail"] and "Traceback" not in r.text
    assert _create(client)["converted_area_m2"] == 490


def test_budget_adjustments_rejects_absurd_budget(client):
    sid = _create(client)["estimate_session_id"]
    r = client.post(f"/api/estimates/{sid}/budget-adjustments", json={"target_budget_vnd": 10**30})
    assert r.status_code == 422 and "ngân sách mục tiêu" in r.json()["detail"]


def test_get_rates_blank_province_is_422(client):
    r = client.get("/api/rates", params={"building_type": "nha_pho", "province": "   ", "work_scope": "xay_tho"})
    assert r.status_code == 422 and "tỉnh/thành" in r.json()["detail"]
