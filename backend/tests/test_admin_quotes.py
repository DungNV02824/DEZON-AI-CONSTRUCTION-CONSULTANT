"""Admin — yêu cầu báo giá & báo giá nhà thầu (V2 — quotes): danh sách, chi tiết, nhập/sửa/xóa báo giá, phân tích."""
import uuid

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import AnalyticsEvent, Contractor, ContractorQuote, QuoteRequest
from app.engines.quote_analysis import relative_gap
from app.formatting import NO_DATA, format_vnd_range, round_to_million

TOKEN = "quotes-admin-token"
H = {"X-Admin-Token": TOKEN}
HCM = "TP. Hồ Chí Minh"
ESTIMATE = {"building_type": "nha_pho", "province": HCM, "land_area_m2": 100, "floors": 4,
            "foundation_type": "foundation_pile", "roof_type": "roof_concrete", "work_scope": "xay_tho"}


@pytest.fixture(autouse=True)
def _admin_token(monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_token", TOKEN)


def _contractors(db, n=2) -> list[Contractor]:
    return list(db.scalars(select(Contractor).where(Contractor.is_active.is_(True)).order_by(Contractor.id)))[:n]


def _setup(client, db, phone="0912345678", with_estimate=True):
    est = client.post("/api/estimates", json=ESTIMATE).json() if with_estimate else None
    cs = _contractors(db)
    body = {"contractor_ids": [c.id for c in cs], "customer_name": "Chị Lan", "customer_phone": phone}
    if est:
        body["estimate_session_id"] = est["estimate_session_id"]
    r = client.post("/api/quote-requests", json=body)
    assert r.status_code == 201, r.text
    return est, cs, r.json()


def _quote_body(**kw):
    body = {"total_amount": 2_500_000_000, "price_per_m2": 5_000_000, "converted_area_m2": 500,
            "scope_text": "Xây thô + nhân công hoàn thiện", "file_url": "https://drive.google.com/file/d/abc",
            "entered_by": "Nhân viên A"}
    body.update(kw)
    return body


@pytest.mark.parametrize("method,path", [
    ("get", "/api/admin/quote-requests"),
    ("get", f"/api/admin/quote-requests/{uuid.uuid4()}"),
    ("post", f"/api/admin/quote-requests/{uuid.uuid4()}/quotes"),
    ("post", f"/api/admin/quote-requests/{uuid.uuid4()}/analysis-preview"),
    ("put", "/api/admin/contractor-quotes/1"),
    ("delete", "/api/admin/contractor-quotes/1"),
    ("get", "/api/admin/contractor-quotes/1/analysis"),
])
def test_requires_token(client, method, path):
    assert getattr(client, method)(path).status_code == 401
    assert getattr(client, method)(path, headers={"X-Admin-Token": "sai"}).status_code == 401


def test_list_detail_and_code(client, db):
    est, cs, created = _setup(client, db)
    data = client.get("/api/admin/quote-requests", headers=H).json()
    assert data["total"] == 2 and data["new_count"] == 2
    items = {i["id"]: i for i in data["items"]}
    assert set(items) == set(created["ids"])
    for i in items.values():
        # Mã khách nhận (chung cho cả lượt gửi) — dù đây không phải yêu cầu đầu tiên của lượt
        assert i["code"] == created["code"]
        assert i["status"] == "new" and i["quote_count"] == 0
        assert i["customer_name"] == "Chị Lan" and i["customer_phone"] == "0912345678"
        assert i["estimate_session_id"] == est["estimate_session_id"]
        assert i["estimate_cost"] == format_vnd_range(est["cost_min"], est["cost_max"])
        assert i["project"] == "Nhà phố · TP. Hồ Chí Minh · 100 m² đất · 4 tầng · Xây thô"
    assert {i["contractor"]["id"] for i in items.values()} == {c.id for c in cs}

    # Lọc theo mã DZ-xxxxxx (có/không tiền tố, chữ thường) và theo trạng thái
    for code in (created["code"], created["code"][3:].lower()):
        f = client.get("/api/admin/quote-requests", headers=H, params={"code": code}).json()
        assert f["total"] == 2, code
    assert client.get("/api/admin/quote-requests", headers=H, params={"code": "DZ-ZZZZZZ"}).json()["total"] == 0
    assert client.get("/api/admin/quote-requests", headers=H, params={"status": "quoted"}).json()["total"] == 0
    assert client.get("/api/admin/quote-requests", headers=H, params={"status": "xong"}).status_code == 422
    page = client.get("/api/admin/quote-requests", headers=H, params={"limit": 1, "offset": 1}).json()
    assert page["total"] == 2 and len(page["items"]) == 1

    d = client.get(f"/api/admin/quote-requests/{created['ids'][0]}", headers=H)
    assert d.status_code == 200, d.text
    d = d.json()
    assert d["request"]["code"] == created["code"] and d["quotes"] == []
    assert d["estimate"]["cost"]["display"] == format_vnd_range(est["cost_min"], est["cost_max"])
    assert d["estimate"]["converted_area"]["display"] == "490 m²"
    assert len(d["estimate"]["items"]) == len(d["line_template"]) == 6  # móng + 4 tầng + mái
    first = d["line_template"][0]
    assert first["group"] == "mong" and first["rule_code"] == "foundation_pile" and first["coefficient"] == 0.4
    assert "amount_vnd" not in first or first["amount_vnd"] is None

    r = client.get(f"/api/admin/quote-requests/{uuid.uuid4()}", headers=H)
    assert r.status_code == 404 and r.json()["detail"] == "Không tìm thấy yêu cầu báo giá."
    r = client.get("/api/admin/quote-requests/abc", headers=H)
    assert r.status_code == 422 and "mã yêu cầu báo giá" in r.json()["detail"]


def test_create_update_delete_quote(client, db):
    est, cs, created = _setup(client, db)
    qr_id = created["ids"][0]
    lines = [{"group": "mong", "label": "Móng cọc", "rule_code": "foundation_pile", "area_m2": 100,
              "coefficient": 0.5, "amount_vnd": 200_000_000},
             {"group": "mai", "label": "Mái BTCT", "rule_code": "roof_concrete", "area_m2": 100, "coefficient": 0.5},
             {"group": "tang", "label": "Lầu 1", "rule_code": "floor_standard"}]  # dòng trống -> bỏ
    r = client.post(f"/api/admin/quote-requests/{qr_id}/quotes", headers=H,
                    json=_quote_body(lines=lines, received_at="2026-11-05"))
    assert r.status_code == 201, r.text
    q = r.json()
    assert q["contractor"]["id"] == db.get(QuoteRequest, uuid.UUID(qr_id)).contractor_id
    assert q["total_amount"] == 2_500_000_000 and q["converted_area_m2"] == 500
    assert q["status"] == "reference" and q["verified"] is False and q["received_date"] == "2026-11-05"
    assert [ln["group"] for ln in q["lines"]] == ["mong", "mai"]
    db.expire_all()
    assert db.get(QuoteRequest, uuid.UUID(qr_id)).status == "quoted"
    ev = db.scalars(select(AnalyticsEvent).where(AnalyticsEvent.event == "contractor_quote_recorded")).all()
    assert len(ev) == 1 and ev[0].props["contractor_quote_id"] == q["id"] and ev[0].props["lines"] == 2

    listed = {i["id"]: i for i in client.get("/api/admin/quote-requests", headers=H).json()["items"]}
    assert listed[qr_id]["status"] == "quoted" and listed[qr_id]["quote_count"] == 1
    d = client.get(f"/api/admin/quote-requests/{qr_id}", headers=H).json()
    assert [x["id"] for x in d["quotes"]] == [q["id"]]

    # Chỉ số "Độ chính xác khái toán" có dữ liệu
    m = client.get("/api/admin/metrics", headers=H).json()["metrics"]
    assert m["estimate_accuracy_in_range"]["sample_size"] == 1 and m["estimate_accuracy_in_range"]["value"] == 0
    lo, hi = est["cost_min"], est["cost_max"]
    assert lo < hi < 2_500_000_000
    assert m["estimate_median_error"]["value"] == pytest.approx(relative_gap(2_500_000_000, lo, hi), abs=1e-3)

    # Phân tích
    a = client.get(f"/api/admin/contractor-quotes/{q['id']}/analysis", headers=H)
    assert a.status_code == 200, a.text
    a = a.json()
    assert a["quote_id"] == q["id"] and a["estimate_session_id"] == est["estimate_session_id"]
    assert a["total"]["position"] == "above" and a["total"]["notable"] is True
    assert a["total"]["note_vi"].startswith("Cao hơn khoảng khái toán ")
    assert a["quote_source"]["label"].endswith("nhận ngày 05/11/2026")
    assert a["quote_source"]["url"] == "https://drive.google.com/file/d/abc"
    assert a["lines_compared"] is True
    assert a["breakdown"]["total_gap"]["value"] == round_to_million(2_500_000_000 - (lo + hi) / 2)
    by = {ln["group"]: ln for ln in a["lines"]}
    assert by["mong"]["position"] == "above" and by["tang"]["position"] == "missing_in_quote"
    assert by["mai"]["position"] == "within"
    assert "không phải báo giá" in a["disclaimer"]

    # Sửa: đã đối chiếu file gốc -> Đã xác minh; PUT thay toàn bộ
    r = client.put(f"/api/admin/contractor-quotes/{q['id']}", headers=H,
                   json=_quote_body(total_amount=(lo + hi) // 2, verified=True, lines=[], file_url=""))
    assert r.status_code == 200, r.text
    u = r.json()
    assert u["verified"] is True and u["status"] == "verified" and u["lines"] == [] and u["file_url"] is None
    assert u["received_date"] == "2026-11-05"  # không gửi ngày nhận -> giữ ngày cũ
    a = client.get(f"/api/admin/contractor-quotes/{q['id']}/analysis", headers=H).json()
    assert a["quote_status"] == "verified" and a["total"]["position"] == "within"
    assert a["total"]["quote"]["status"] == "verified" and a["lines_compared"] is False

    # Xóa báo giá cuối cùng -> yêu cầu về "new"
    assert client.delete(f"/api/admin/contractor-quotes/{q['id']}", headers=H).json() == {"ok": True}
    db.expire_all()
    assert db.get(QuoteRequest, uuid.UUID(qr_id)).status == "new"
    assert client.delete(f"/api/admin/contractor-quotes/{q['id']}", headers=H).status_code == 404
    r = client.put(f"/api/admin/contractor-quotes/{q['id']}", headers=H, json=_quote_body())
    assert r.status_code == 404 and r.json()["detail"] == "Không tìm thấy báo giá nhà thầu."
    assert client.get(f"/api/admin/contractor-quotes/{q['id']}/analysis", headers=H).status_code == 404


def test_won_status_kept(client, db):
    _, _, created = _setup(client, db)
    qr = db.get(QuoteRequest, uuid.UUID(created["ids"][0]))
    qr.status = "won"
    db.commit()
    r = client.post(f"/api/admin/quote-requests/{qr.id}/quotes", headers=H, json=_quote_body())
    assert r.status_code == 201
    db.expire_all()
    assert db.get(QuoteRequest, qr.id).status == "won"


def test_preview_does_not_save(client, db):
    _, _, created = _setup(client, db)
    qr_id = created["ids"][1]
    r = client.post(f"/api/admin/quote-requests/{qr_id}/analysis-preview", headers=H,
                    json=_quote_body(total_amount=1_000_000_000, price_per_m2=None, converted_area_m2=None))
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["quote_id"] is None and a["total"]["position"] == "below"
    assert a["price_per_m2"]["quote"]["display"] == NO_DATA
    assert db.scalar(select(func.count()).select_from(ContractorQuote)) == 0
    db.expire_all()
    assert db.get(QuoteRequest, uuid.UUID(qr_id)).status == "new"


def test_quote_without_estimate_409(client, db):
    _, _, created = _setup(client, db, phone="0987654321", with_estimate=False)
    qr_id = created["ids"][0]
    d = client.get(f"/api/admin/quote-requests/{qr_id}", headers=H).json()
    assert d["estimate"] is None and d["line_template"] == [] and d["request"]["estimate_cost"] is None
    r = client.post(f"/api/admin/quote-requests/{qr_id}/analysis-preview", headers=H, json=_quote_body())
    assert r.status_code == 409 and "chưa gắn khái toán" in r.json()["detail"]
    q = client.post(f"/api/admin/quote-requests/{qr_id}/quotes", headers=H, json=_quote_body()).json()
    r = client.get(f"/api/admin/contractor-quotes/{q['id']}/analysis", headers=H)
    assert r.status_code == 409 and "chưa gắn khái toán" in r.json()["detail"]


@pytest.mark.parametrize("body,needle", [
    ({"total_amount": None, "price_per_m2": None}, "Cần nhập tổng tiền hoặc đơn giá/m² của báo giá."),
    ({"total_amount": 0}, "tổng tiền báo giá"),
    ({"file_url": "javascript:alert(1)"}, "Link file gốc phải là địa chỉ http:// hoặc https://"),
    ({"lines": [{"group": "nha"}]}, "nhóm hạng mục"),
    ({"lines": [{"amount_vnd": -1}]}, "thành tiền"),
    ({"lines": [{"label": "x"}] * 101}, "dòng hạng mục"),
    ({"scope_text": "x" * 4001}, "phạm vi báo giá"),
])
def test_validation_vietnamese(client, db, body, needle):
    _, _, created = _setup(client, db)
    r = client.post(f"/api/admin/quote-requests/{created['ids'][0]}/quotes", headers=H, json=_quote_body(**body))
    assert r.status_code == 422 and needle in r.json()["detail"], r.text
    assert db.scalar(select(func.count()).select_from(ContractorQuote)) == 0


def test_unknown_request_404(client):
    r = client.post(f"/api/admin/quote-requests/{uuid.uuid4()}/quotes", headers=H, json=_quote_body())
    assert r.status_code == 404 and r.json()["detail"] == "Không tìm thấy yêu cầu báo giá."
    assert client.put("/api/admin/contractor-quotes/0", headers=H, json=_quote_body()).status_code == 422


def test_admin_page_has_quotes_tab(client):
    html = client.get("/admin").text
    assert 'data-tab="quotes"' in html and "Yêu cầu báo giá" in html
    assert html.index('data-tab="contacts"') < html.index('data-tab="quotes"') < html.index('data-tab="violations"')
    for s in ("/api/admin/quote-requests", "/api/admin/contractor-quotes/", "analysis-preview", "Nhập báo giá",
              "đã đối chiếu file gốc", "Xem phân tích", "Khái toán là con số sơ bộ"):
        assert s in html, s
    assert "tốt nhất" not in html
    # Điện thoại 360px: bảng khái toán/phân tích dựng bằng JS nằm trong khung cuộn, ô nhập không rộng hơn màn hình
    assert "box.appendChild(t);" not in html and "box.appendChild(lt);" not in html
    assert html.count("el('div', { class: 'scroll' }, [t])") == 2 and "el('div', { class: 'scroll' }, [lt])" in html
    assert "min-width: 320px" not in html and "#qDetail input[name=file_url] { max-width: 100%; }" in html
