"""REST nhà thầu: tìm kiếm, hồ sơ, match, compare, click (mục 7, 8, 10) + CORS."""
import uuid

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import AnalyticsEvent, Contractor, RecommendationLog

HCM = "TP. Hồ Chí Minh"
ESTIMATE = {"building_type": "nha_pho", "province": HCM, "land_area_m2": 100, "floors": 4,
            "foundation_type": "foundation_pile", "roof_type": "roof_concrete", "work_scope": "xay_tho"}


def _ids(db, *slugs):
    rows = {c.slug: c.id for c in db.scalars(select(Contractor).where(Contractor.slug.in_(slugs)))}
    return [rows[s] for s in slugs]


def _seed_slugs(db):
    return [c.slug for c in db.scalars(select(Contractor).order_by(Contractor.id)).all()][:3]


def _match(client, **extra):
    r = client.post("/api/contractors/match", json={"province": HCM, "building_type": "nha_pho", **extra})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ danh sách / hồ sơ
def test_list_contractors(client):
    r = client.get("/api/contractors", params={"province": "TP.HCM"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 3 and len(body["items"]) == min(body["total"], 20)
    card = body["items"][0]
    assert {"id", "slug", "name", "profile_url", "evidence"} <= set(card)
    assert card["evidence"]["dezon_project_count"]["display"].endswith("công trình ghi nhận trên Dezon")


def test_list_contractors_filters(client):
    r = client.get("/api/contractors", params={"q": "kien phong", "limit": 5})
    assert r.status_code == 200
    assert [c["slug"] for c in r.json()["items"]] == ["kien-phong"]
    r = client.get("/api/contractors", params={"building_type": "biet_thu", "province": HCM})
    assert r.status_code == 200 and r.json()["total"] >= 1


def test_list_contractors_bad_building_type_is_422(client):
    r = client.get("/api/contractors", params={"building_type": "lau_dai"})
    assert r.status_code == 422
    assert "loại công trình" in r.json()["detail"]
    r = client.get("/api/contractors", params={"limit": 0})
    assert r.status_code == 422


def test_get_contractor_profile(client):
    r = client.get("/api/contractors/kien-phong")
    assert r.status_code == 200
    body = r.json()
    assert "Kiến Phong" in body["card"]["name"]
    assert body["info"]["founded_year"]["display"] == "2018"
    assert body["projects"] and all(p["project_url"] for p in body["projects"])


def test_get_contractor_profile_not_found(client):
    assert client.get("/api/contractors/khong-ton-tai").status_code == 404
    assert client.get("/api/contractors/999999999").status_code == 404
    assert client.get("/api/contractors/" + "9" * 30).status_code == 404


# ------------------------------------------------------------------ match / click
def test_match_writes_recommendation_log(client, db):
    body = _match(client)
    assert body["recommendation_log_id"]
    assert 3 <= len(body["results"]) <= 5
    assert all(len(m["reasons"]) >= 2 for m in body["results"])
    log = db.get(RecommendationLog, body["recommendation_log_id"])
    assert log is not None and log.weights_version == body["weights_version"]
    assert [r["rank"] for r in log.results] == list(range(1, len(body["results"]) + 1))


def test_match_validation(client):
    r = client.post("/api/contractors/match", json={"province": HCM, "building_type": "nha_pho", "limit": 10})
    assert r.status_code == 422
    r = client.post("/api/contractors/match", json={"building_type": "nha_pho"})
    assert r.status_code == 422 and "tỉnh/thành" in r.json()["detail"]


def test_click_records_and_logs_event(client, db):
    body = _match(client)
    log_id = body["recommendation_log_id"]
    cid = body["results"][1]["contractor"]["id"]
    r = client.post(f"/api/recommendations/{log_id}/click", json={"contractor_id": cid})
    assert r.status_code == 200 and r.json() == {"ok": True}
    # Bấm lại không ghi trùng
    assert client.post(f"/api/recommendations/{log_id}/click", json={"contractor_id": cid}).status_code == 200
    log = db.get(RecommendationLog, log_id)
    db.refresh(log)
    assert list(log.clicked_contractor_ids) == [cid]
    ev = db.scalars(select(AnalyticsEvent).where(AnalyticsEvent.event == "contractor_clicked")).all()
    mine = [e for e in ev if e.props.get("recommendation_log_id") == log_id]
    assert mine and mine[0].props["contractor_id"] == cid and mine[0].props["rank"] == 2


def test_click_not_found(client, db):
    body = _match(client)
    log_id = body["recommendation_log_id"]
    in_results = {m["contractor"]["id"] for m in body["results"]}
    outsider = db.scalar(select(Contractor.id).where(Contractor.id.not_in(in_results)).limit(1))
    if outsider is not None:
        r = client.post(f"/api/recommendations/{log_id}/click", json={"contractor_id": outsider})
        assert r.status_code == 404
    assert client.post("/api/recommendations/999999999/click", json={"contractor_id": 1}).status_code == 404
    assert client.post(f"/api/recommendations/{log_id}/click", json={}).status_code == 422


# ------------------------------------------------------------------ compare
def test_compare_without_estimate_fit_unavailable(client, db):
    ids = _ids(db, *_seed_slugs(db))
    r = client.post("/api/contractors/compare", json={"contractor_ids": ids})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [c["id"] for c in body["contractors"]] == ids
    rows = {row["key"]: row for row in body["rows"]}
    assert set(rows) == {"area", "dezon_project_count", "experience", "rough_price", "finish_price", "projects",
                         "warranty", "fit"}
    assert all(c["status"] == "unavailable" and c["display"] == "Chưa có dữ liệu" for c in rows["fit"]["cells"])
    assert all(c["display"] for row in body["rows"] for c in row["cells"])  # không ô trống


def test_compare_with_estimate_has_fit_row(client, db):
    est = client.post("/api/estimates", json=ESTIMATE).json()
    ids = _ids(db, *_seed_slugs(db))
    r = client.post("/api/contractors/compare",
                    json={"contractor_ids": ids, "estimate_session_id": est["estimate_session_id"]})
    assert r.status_code == 200, r.text
    fit = next(row for row in r.json()["rows"] if row["key"] == "fit")
    assert fit["label_vi"] == "Mức phù hợp"
    for cell in fit["cells"]:
        assert cell["status"] == "reference" and cell["display"].endswith("/100")
        assert cell["value"]["fit_label"] in ("Phù hợp cao", "Phù hợp", "Cần đánh giá thêm")
        assert "Dezon Matching v" in cell["source"]["label"]


def test_compare_validation(client, db):
    ids = _ids(db, *_seed_slugs(db))
    r = client.post("/api/contractors/compare", json={"contractor_ids": ids[:1]})
    assert r.status_code == 422 and "danh sách nhà thầu" in r.json()["detail"]
    r = client.post("/api/contractors/compare", json={"contractor_ids": [ids[0], ids[0]]})
    assert r.status_code == 422 and "trùng" in r.json()["detail"]
    r = client.post("/api/contractors/compare", json={"contractor_ids": [ids[0], 999999999]})
    assert r.status_code == 422 and "Không tìm thấy nhà thầu" in r.json()["detail"]
    r = client.post("/api/contractors/compare", json={"contractor_ids": ids, "estimate_session_id": "abc"})
    assert r.status_code == 422


def test_compare_unknown_estimate_still_returns_matrix(client, db):
    ids = _ids(db, *_seed_slugs(db))
    r = client.post("/api/contractors/compare", json={"contractor_ids": ids, "estimate_session_id": str(uuid.uuid4())})
    assert r.status_code == 200
    fit = next(row for row in r.json()["rows"] if row["key"] == "fit")
    assert all(c["status"] == "unavailable" for c in fit["cells"])


# ------------------------------------------------------------------ CORS
def test_cors_preflight_allowed_origin(client):
    if "https://dezon.vn" not in get_settings().allowed_origins_list:
        pytest.skip("ALLOWED_ORIGINS của môi trường không gồm https://dezon.vn")
    r = client.options("/api/contractors/compare", headers={
        "Origin": "https://dezon.vn", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type"})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "https://dezon.vn"


def test_cors_preflight_rejects_other_origin(client):
    r = client.options("/api/contractors/compare", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in r.headers
    r = client.get("/api/contractors", headers={"Origin": "https://evil.example"})
    assert r.status_code == 200 and "access-control-allow-origin" not in r.headers


# ------------------------------------------------------------------ review: giới hạn đầu vào của match
@pytest.mark.parametrize("extra,field", [
    ({"province": "   "}, "tỉnh/thành"),
    ({"budget_vnd": -1}, "ngân sách"),
    ({"budget_vnd": 10**30}, "ngân sách"),
    ({"converted_area_m2": -5}, "diện tích quy đổi"),
    ({"floors": 0}, "số tầng"),
])
def test_match_rejects_invalid_criteria(client, db, extra, field):
    before = db.scalar(select(func.count()).select_from(RecommendationLog))
    r = client.post("/api/contractors/match", json={"province": HCM, "building_type": "nha_pho", **extra})
    assert r.status_code == 422, r.text
    assert field in r.json()["detail"]
    assert db.scalar(select(func.count()).select_from(RecommendationLog)) == before


def test_click_bad_log_id_label_vietnamese(client):
    r = client.post("/api/recommendations/abc/click", json={"contractor_id": 1})
    assert r.status_code == 422 and "mã lượt đề xuất" in r.json()["detail"]
