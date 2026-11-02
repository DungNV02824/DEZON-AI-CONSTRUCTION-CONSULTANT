"""Admin tối thiểu: token, CRUD đơn giá/hệ số/trọng số, xác minh nguồn, vi phạm, đồng bộ, nhập file, trang /admin."""
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import ConstructionRate, DataSource, GuardrailViolation, MatchingWeights
from app.services import admin_service

TOKEN = "test-admin-token"
H = {"X-Admin-Token": TOKEN}
SAMPLES = Path(__file__).resolve().parents[2] / "docs" / "samples"
HCM = "TP. Hồ Chí Minh"
ESTIMATE = {"building_type": "nha_pho", "province": HCM, "land_area_m2": 100, "floors": 4,
            "foundation_type": "foundation_pile", "roof_type": "roof_concrete", "work_scope": "xay_tho"}
WEIGHTS_V2 = {"location": 0.25, "building_type": 0.2, "budget_fit": 0.15, "experience": 0.15,
              "similar_projects": 0.15, "profile_quality": 0.1}


@pytest.fixture(autouse=True)
def _admin_token(monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_token", TOKEN)


# ------------------------------------------------------------------ token & trang
@pytest.mark.parametrize("method,path", [
    ("get", "/api/admin/rates"), ("get", "/api/admin/rules"), ("get", "/api/admin/weights"),
    ("post", "/api/admin/weights"), ("get", "/api/admin/sources"), ("post", "/api/admin/sources/1/verify"),
    ("get", "/api/admin/guardrail-violations"), ("get", "/api/admin/metrics"), ("post", "/api/admin/sync/crawl"),
    ("post", "/api/admin/import/rates"), ("delete", "/api/admin/rates/1"),
])
def test_admin_requires_token(client, method, path):
    r = getattr(client, method)(path)
    assert r.status_code == 401
    assert "X-Admin-Token" in r.json()["detail"]
    r = getattr(client, method)(path, headers={"X-Admin-Token": "sai-ma"})
    assert r.status_code == 401


def test_admin_page_served_without_token(client):
    r = client.get("/admin")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "Quản trị" in r.text and "X-Admin-Token" in r.text and "Xác minh" in r.text
    assert TOKEN not in r.text


# ------------------------------------------------------------------ đơn giá
def _rate_body(**extra):
    return {"building_type": "biet_thu", "province": "Đà Nẵng", "work_scope": "xay_tho", "quality_tier": "kha",
            "price_min": 5_000_000, "price_max": 5_800_000, "effective_from": "2026-11-01", **extra}


def test_rates_list_and_filter(client):
    r = client.get("/api/admin/rates", headers=H, params={"building_type": "nha_pho", "province": HCM,
                                                          "work_scope": "xay_tho"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 4  # 4 mức chất lượng seed
    assert all(i["province"] == HCM and i["source_status"] == "reference" and i["approved"] is False
               for i in body["items"])
    assert client.get("/api/admin/rates", headers=H, params={"work_scope": "sai"}).status_code == 422


def test_rate_crud_and_approve(client, db):
    r = client.post("/api/admin/rates", headers=H, json=_rate_body())
    assert r.status_code == 201, r.text
    rate = r.json()
    assert rate["approved"] is False and rate["source_status"] == "reference"
    rid = rate["id"]
    assert client.get(f"/api/admin/rates/{rid}", headers=H).json()["price_min"] == 5_000_000

    r = client.put(f"/api/admin/rates/{rid}", headers=H, json={"price_max": 6_000_000, "effective_to": "2027-12-31"})
    assert r.status_code == 200 and r.json()["price_max"] == 6_000_000 and r.json()["effective_to"] == "2027-12-31"

    r = client.post(f"/api/admin/rates/{rid}/approve", headers=H, json={"verified_by": "kiem-dinh@dezon.vn"})
    assert r.status_code == 200
    assert r.json()["rate"]["approved"] is True and r.json()["affected_rates"] == 1
    ds = db.get(DataSource, rate["source_id"])
    assert ds.status == "verified" and ds.verified_by == "kiem-dinh@dezon.vn" and ds.verified_at is not None
    # Đơn giá đã duyệt được dùng với trạng thái verified
    q = client.get("/api/rates", params={"building_type": "biet_thu", "province": "Đà Nẵng", "work_scope": "xay_tho",
                                         "quality_tier": "kha"}).json()
    assert q["rate"]["status"] == "verified" and q["rate_data"]["price_max"] == 6_000_000

    assert client.delete(f"/api/admin/rates/{rid}", headers=H).json() == {"ok": True}
    assert client.get(f"/api/admin/rates/{rid}", headers=H).status_code == 404
    assert client.delete(f"/api/admin/rates/{rid}", headers=H).status_code == 404


def test_rate_create_approved_directly(client):
    r = client.post("/api/admin/rates", headers=H, json=_rate_body(approved=True))
    assert r.status_code == 201 and r.json()["approved"] is True


def test_rate_validation_and_conflicts(client):
    assert client.post("/api/admin/rates", headers=H, json=_rate_body(price_min=6_000_000)).status_code == 422
    r = client.post("/api/admin/rates", headers=H, json=_rate_body(price_min=4300, price_max=4700))
    assert r.status_code == 422 and "VND/m²" in r.json()["detail"]
    r = client.post("/api/admin/rates", headers=H, json=_rate_body(effective_to="2026-01-01"))
    assert r.status_code == 422
    r = client.post("/api/admin/rates", headers=H, json=_rate_body(building_type="lau_dai"))
    assert r.status_code == 422 and "loại công trình" in r.json()["detail"]
    r = client.post("/api/admin/rates", headers=H, json=_rate_body(source_id=999999))
    assert r.status_code == 404
    assert client.post("/api/admin/rates", headers=H, json=_rate_body()).status_code == 201
    assert client.post("/api/admin/rates", headers=H, json=_rate_body()).status_code == 409
    assert client.put("/api/admin/rates/999999", headers=H, json={"price_min": 5_000_000}).status_code == 404


def test_rate_used_by_estimate_cannot_be_deleted(client, db):
    est = client.post("/api/estimates", json=ESTIMATE).json()
    rid = est["rate_id"]
    assert rid
    r = client.delete(f"/api/admin/rates/{rid}", headers=H)
    assert r.status_code == 409 and "khái toán" in r.json()["detail"]
    r = client.put(f"/api/admin/rates/{rid}", headers=H, json={"price_min": 3_000_000})
    assert r.status_code == 409
    r = client.put(f"/api/admin/rates/{rid}", headers=H, json={"effective_to": "2027-01-01"})
    assert r.status_code == 200
    assert db.get(ConstructionRate, rid) is not None


# ------------------------------------------------------------------ hệ số
def test_rules_list(client):
    r = client.get("/api/admin/rules", headers=H, params={"latest_only": True})
    assert r.status_code == 200 and r.json()["total"] == 16
    assert all(i["approved"] is False and i["version"] == 1 for i in r.json()["items"])


def test_rule_new_version_approved_is_used_by_estimate(client):
    r = client.post("/api/admin/rules", headers=H,
                    json={"code": "foundation_pile", "coefficient": 0.45, "approved": True})
    assert r.status_code == 201, r.text
    rule = r.json()
    assert rule["version"] == 2 and rule["approved"] is True
    assert rule["group_name"] == "mong" and rule["base"] == "ground_floor_area"  # kế thừa phiên bản cũ
    est = client.post("/api/estimates", json=ESTIMATE).json()
    assert est["converted_area_m2"] == pytest.approx(495)  # 100×0,45 + 4×100 + 100×0,5
    versions = client.get("/api/admin/rules", headers=H, params={"code": "foundation_pile"}).json()["items"]
    assert [v["version"] for v in versions] == [2, 1]


def test_rule_approve_and_update_rules(client):
    rule = client.get("/api/admin/rules", headers=H, params={"code": "roof_metal"}).json()["items"][0]
    r = client.post(f"/api/admin/rules/{rule['id']}/approve", headers=H)
    assert r.status_code == 200 and r.json()["approved"] is True
    r = client.delete(f"/api/admin/rules/{rule['id']}", headers=H)
    assert r.status_code == 409
    r = client.put(f"/api/admin/rules/{rule['id']}", headers=H, json={"coefficient": 0.35})
    assert r.status_code == 422 and "phiên bản mới" in r.json()["detail"]
    r = client.put(f"/api/admin/rules/{rule['id']}", headers=H, json={"approved": False, "label_vi": "Mái tôn (lợp)"})
    assert r.status_code == 200 and r.json()["approved"] is False and r.json()["label_vi"] == "Mái tôn (lợp)"
    assert client.put("/api/admin/rules/999999", headers=H, json={"approved": True}).status_code == 404


def test_rule_validation(client):
    r = client.post("/api/admin/rules", headers=H, json={"code": "ma_moi", "coefficient": 0.5})
    assert r.status_code == 422 and "group_name" in r.json()["detail"]
    r = client.post("/api/admin/rules", headers=H, json={"code": "ma_moi", "coefficient": 0.5, "group_name": "xx",
                                                         "label_vi": "Mới", "base": "floor_area"})
    assert r.status_code == 422
    r = client.post("/api/admin/rules", headers=H, json={"code": "foundation_pile", "coefficient": 0})
    assert r.status_code == 422
    r = client.post("/api/admin/rules", headers=H, json={"code": "Có Dấu", "coefficient": 1})
    assert r.status_code == 422
    r = client.post("/api/admin/rules", headers=H, json={"code": "balcony", "coefficient": 0.5, "group_name": "ban_cong",
                                                         "label_vi": "Ban công", "base": "balcony_area"})
    assert r.status_code == 201 and r.json()["version"] == 1
    assert client.delete(f"/api/admin/rules/{r.json()['id']}", headers=H).json() == {"ok": True}


# ------------------------------------------------------------------ trọng số
def test_weights_new_version_is_active_and_used_by_match(client, db):
    r = client.post("/api/admin/weights", headers=H, json={"weights": WEIGHTS_V2, "note": "Thử A/B"})
    assert r.status_code == 201, r.text
    w = r.json()
    assert w["version"] == 2 and w["is_active"] is True
    assert db.scalar(select(func.count()).select_from(MatchingWeights).where(MatchingWeights.is_active)) == 1
    m = client.post("/api/contractors/match", json={"province": HCM, "building_type": "nha_pho"}).json()
    assert m["weights_version"] == 2
    assert {b["key"]: b["weight"] for b in m["results"][0]["breakdown"]}["location"] == pytest.approx(0.25)

    items = client.get("/api/admin/weights", headers=H).json()["items"]
    assert [(i["version"], i["is_active"]) for i in items] == [(2, True), (1, False)]
    assert items[0]["recommendation_count"] == 1

    r = client.post("/api/admin/weights/1/activate", headers=H)
    assert r.status_code == 200 and r.json()["is_active"] is True
    assert db.scalar(select(MatchingWeights.version).where(MatchingWeights.is_active)) == 1
    m = client.post("/api/contractors/match", json={"province": HCM, "building_type": "nha_pho"}).json()
    assert m["weights_version"] == 1
    r = client.delete("/api/admin/weights/2", headers=H)
    assert r.status_code == 409  # đã dùng trong recommendation_logs


def test_weights_inactive_version_and_delete(client, db):
    r = client.post("/api/admin/weights", headers=H, json={"weights": WEIGHTS_V2, "activate": False})
    assert r.status_code == 201 and r.json()["is_active"] is False
    assert db.scalar(select(MatchingWeights.version).where(MatchingWeights.is_active)) == 1
    v = r.json()["version"]
    r = client.put(f"/api/admin/weights/{v}", headers=H, json={"note": "Bản nháp"})
    assert r.status_code == 200 and r.json()["note"] == "Bản nháp" and r.json()["is_active"] is False
    assert client.delete(f"/api/admin/weights/{v}", headers=H).json() == {"ok": True}
    assert client.delete(f"/api/admin/weights/{v}", headers=H).status_code == 404


def test_weights_validation(client):
    bad_sum = {**WEIGHTS_V2, "location": 0.3}
    r = client.post("/api/admin/weights", headers=H, json={"weights": bad_sum})
    assert r.status_code == 422 and "Tổng trọng số" in r.json()["detail"]
    missing = {k: v for k, v in WEIGHTS_V2.items() if k != "budget_fit"}
    r = client.post("/api/admin/weights", headers=H, json={"weights": {**missing, "location": 0.4}})
    assert r.status_code == 422 and "budget_fit" in r.json()["detail"]
    r = client.post("/api/admin/weights", headers=H, json={"weights": {**WEIGHTS_V2, "gia_re": 0}})
    assert r.status_code == 422 and "gia_re" in r.json()["detail"]
    r = client.post("/api/admin/weights", headers=H,
                    json={"weights": {**WEIGHTS_V2, "location": -0.05, "building_type": 0.5}})
    assert r.status_code == 422
    # sai số trong ±0.001 được chấp nhận
    near = {**WEIGHTS_V2, "location": 0.2505}
    assert client.post("/api/admin/weights", headers=H, json={"weights": near, "activate": False}).status_code == 201
    r = client.put("/api/admin/weights/1", headers=H, json={"is_active": False})
    assert r.status_code == 422
    assert client.delete("/api/admin/weights/1", headers=H).status_code == 409
    assert client.post("/api/admin/weights/999/activate", headers=H).status_code == 404


# ------------------------------------------------------------------ nguồn dữ liệu
def test_sources_list_and_verify(client, db):
    r = client.get("/api/admin/sources", headers=H, params={"status": "on_dezon"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert items and all(i["status"] == "on_dezon" for i in items)
    sid = items[0]["id"]
    r = client.post(f"/api/admin/sources/{sid}/verify", headers=H, json={"verified_by": "Nguyễn Kiểm Định"})
    assert r.status_code == 200
    assert r.json()["status"] == "verified" and r.json()["verified_by"] == "Nguyễn Kiểm Định"
    assert r.json()["verified_at"]
    ds = db.get(DataSource, sid)
    assert ds.status == "verified" and ds.verified_at is not None
    verified = client.get("/api/admin/sources", headers=H, params={"status": "verified"}).json()["items"]
    assert sid in {i["id"] for i in verified}
    # Không gửi body -> verified_by mặc định "admin"
    if len(items) > 1:
        assert client.post(f"/api/admin/sources/{items[1]['id']}/verify", headers=H).json()["verified_by"] == "admin"


def test_sources_errors(client):
    assert client.post("/api/admin/sources/999999/verify", headers=H).status_code == 404
    r = client.get("/api/admin/sources", headers=H, params={"status": "da_duyet"})
    assert r.status_code == 422 and "trạng thái" in r.json()["detail"]


# ------------------------------------------------------------------ vi phạm guardrail
def test_guardrail_violations_list(client, db):
    sid = uuid.uuid4()
    db.add_all([GuardrailViolation(session_id=sid, rule="untraceable_number",
                                   detail={"text": "5,1 triệu/m²", "detail": "không có trong tool", "attempt": 1}),
                GuardrailViolation(session_id=sid, rule="best_claim", detail={"text": "tốt nhất", "attempt": 2})])
    db.flush()
    r = client.get("/api/admin/guardrail-violations", headers=H, params={"limit": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 2 and len(body["items"]) == 1
    assert body["items"][0]["session_id"] == str(sid)
    r = client.get("/api/admin/guardrail-violations", headers=H, params={"rule": "best_claim"})
    assert [i["rule"] for i in r.json()["items"]] == ["best_claim"]
    assert client.get("/api/admin/guardrail-violations", headers=H, params={"limit": 0}).status_code == 422


# ------------------------------------------------------------------ đồng bộ
class _FakeConn:
    def ping(self):
        return True


class _FakeJob:
    id = "job-123"


def test_sync_enqueues_job(client, monkeypatch):
    calls = []
    monkeypatch.setattr(admin_service, "redis_connection", lambda: _FakeConn())
    monkeypatch.setattr(admin_service.jobs, "enqueue", lambda name, **kw: calls.append((name, kw)) or _FakeJob())
    r = client.post("/api/admin/sync/crawl", headers=H, params={"max_pages": 3})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued" and r.json()["job_id"] == "job-123" and r.json()["job"] == "crawl"
    assert calls[0][0] == "crawl" and calls[0][1]["max_pages"] == 3
    for source, job in (("reindex", "reindex_all"), ("after_sync", "after_sync"), ("api", "api_sync")):
        r = client.post(f"/api/admin/sync/{source}", headers=H)
        assert r.status_code == 200 and r.json()["job"] == job
    assert client.post("/api/admin/sync/crawl", headers=H, params={"max_pages": "x"}).status_code == 422
    assert client.post("/api/admin/sync/crawl", headers=H, params={"max_pages": 0}).status_code == 422


def test_sync_redis_unavailable_is_503(client, monkeypatch):
    from redis import Redis

    monkeypatch.setattr(admin_service, "redis_connection",
                        lambda: Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.5, socket_timeout=0.5))
    r = client.post("/api/admin/sync/reindex", headers=H)
    assert r.status_code == 503
    assert "Redis" in r.json()["detail"] and "worker" in r.json()["detail"]


def test_sync_invalid_source(client):
    r = client.post("/api/admin/sync/ftp", headers=H)
    assert r.status_code == 422


def test_sync_csv_upload_and_path(monkeypatch, client, db):
    content = (SAMPLES / "construction_rates.csv").read_bytes()
    r = client.post("/api/admin/sync/csv", headers=H, data={"kind": "rates"},
                    files={"file": ("construction_rates.csv", content, "text/csv")})
    assert r.status_code == 200, r.text
    report = r.json()["report"]
    assert r.json()["status"] == "done" and report["kind"] == "rates"
    assert report["status"] in ("ok", "partial") and report["inserted"] + report["updated"] > 0
    monkeypatch.setattr(get_settings(), "import_dir", str(SAMPLES))  # K3: chỉ đọc trong IMPORT_DIR
    r = client.post("/api/admin/sync/csv", headers=H,
                    json={"kind": "rules", "path": str(SAMPLES / "estimate_rules.csv"), "dry_run": True})
    assert r.status_code == 200 and r.json()["report"]["dry_run"] is True
    r = client.post("/api/admin/sync/csv", headers=H, json={"path": str(SAMPLES / "estimate_rules.csv")})
    assert r.status_code == 422 and "kind" in r.json()["detail"]
    r = client.post("/api/admin/sync/csv", headers=H, json={"kind": "rates"})
    assert r.status_code == 422
    r = client.post("/api/admin/sync/csv", headers=H, json={"kind": "rates", "path": "/khong/co/file.csv"})
    assert r.status_code == 422


# ------------------------------------------------------------------ nhập file
def test_import_rates_dry_run_then_real(client, db):
    content = (SAMPLES / "construction_rates.csv").read_bytes()
    before = db.scalar(select(func.count()).select_from(ConstructionRate))
    r = client.post("/api/admin/import/rates", headers=H, params={"dry_run": "true"},
                    files={"file": ("construction_rates.csv", content, "text/csv")})
    assert r.status_code == 200, r.text
    assert r.json()["dry_run"] is True and r.json()["inserted"] == 0
    assert db.scalar(select(func.count()).select_from(ConstructionRate)) == before
    r = client.post("/api/admin/import/rates", headers=H,
                    files={"file": ("construction_rates.csv", content, "text/csv")})
    rep = r.json()
    assert r.status_code == 200 and rep["dry_run"] is False and rep["inserted"] > 0 and rep["source_id"]
    assert db.scalar(select(func.count()).select_from(ConstructionRate)) == before + rep["inserted"]
    assert db.get(DataSource, rep["source_id"]).source_type == "csv_import"
    # Nhập lại cùng file -> unchanged
    r = client.post("/api/admin/import/rates", headers=H,
                    files={"file": ("construction_rates.csv", content, "text/csv")})
    assert r.json()["status"] == "unchanged"


def test_import_rules_creates_unapproved_versions(client):
    content = (SAMPLES / "estimate_rules.csv").read_bytes()
    r = client.post("/api/admin/import/rules", headers=H, data={"force": "true"},
                    files={"file": ("estimate_rules.csv", content, "text/csv")})
    assert r.status_code == 200 and r.json()["inserted"] > 0
    items = client.get("/api/admin/rules", headers=H, params={"code": "floor_standard"}).json()["items"]
    assert items[0]["version"] == 2 and items[0]["approved"] is False


def test_import_errors(client):
    r = client.post("/api/admin/import/rates", headers=H, files={"file": ("rates.txt", b"a,b\n1,2", "text/plain")})
    assert r.status_code == 422 and ".csv" in r.json()["detail"]
    r = client.post("/api/admin/import/rates", headers=H, files={"file": ("rates.csv", b"", "text/csv")})
    assert r.status_code == 422
    r = client.post("/api/admin/import/rates", headers=H,
                    files={"file": ("rates.csv", b"building_type,province\nnha_pho,HCM\n", "text/csv")})
    assert r.status_code == 200 and r.json()["status"] == "failed" and r.json()["error_count"] >= 1
    assert client.post("/api/admin/import/prices", headers=H).status_code == 422
    r = client.post("/api/admin/import/rates", headers=H)
    assert r.status_code == 422 and "file" in r.json()["detail"]
    big = b"x" * (admin_service.MAX_UPLOAD_BYTES + 10)
    r = client.post("/api/admin/import/rates", headers=H, files={"file": ("big.csv", big, "text/csv")})
    assert r.status_code == 413


def test_raw_body_upload(client):
    content = (SAMPLES / "construction_rates.csv").read_bytes()
    r = client.post("/api/admin/import/rates", headers={**H, "Content-Type": "text/csv"},
                    params={"filename": "rates.csv", "dry_run": "1"}, content=content)
    assert r.status_code == 200 and r.json()["dry_run"] is True and r.json()["total_rows"] > 0


def test_admin_page_links_only_http_urls(client):
    """URL nguồn (từ crawl/API) chỉ thành link khi là http(s) — chặn href "javascript:" trong trang quản trị."""
    r = client.get("/admin")
    assert r"/^https?:\/\//i.test(s.url)" in r.text


def test_rule_non_finite_coefficient_vietnamese(client):
    r = client.post("/api/admin/rules", headers=H, json={"code": "foundation_pile", "coefficient": "NaN"})
    assert r.status_code == 422 and "hệ số" in r.json()["detail"]
    assert "Input should" not in r.json()["detail"]


def test_sync_csv_path_restricted_to_import_dir(monkeypatch, client, tmp_path):
    """K3: không đọc được file ngoài IMPORT_DIR, kể cả qua '..'; lỗi không tiết lộ file có tồn tại."""
    monkeypatch.setattr(get_settings(), "import_dir", str(SAMPLES))
    ok = client.post("/api/admin/sync/csv", headers=H, json={"kind": "rules", "path": "estimate_rules.csv",
                                                             "dry_run": True})
    assert ok.status_code == 200, ok.text
    outside = tmp_path / "secret.csv"
    outside.write_text("a,b\n1,2\n", encoding="utf-8")
    for path in (str(outside), "../samples/../../README.md.csv", str(SAMPLES / ".." / "DECISIONS.csv")):
        r = client.post("/api/admin/sync/csv", headers=H, json={"kind": "rates", "path": path})
        assert r.status_code == 422 and "IMPORT_DIR" in r.json()["detail"]
    missing = client.post("/api/admin/sync/csv", headers=H, json={"kind": "rates", "path": "khong-co.csv"})
    assert missing.json()["detail"] == r.json()["detail"]  # cùng thông báo -> không dò được file tồn tại


def test_admin_default_token_locked_in_production(monkeypatch, client):
    """K2: APP_ENV=production + ADMIN_TOKEN mặc định -> khóa trang quản trị."""
    s = get_settings()
    monkeypatch.setattr(s, "app_env", "production")
    monkeypatch.setattr(s, "admin_token", "change-me")
    r = client.get("/api/admin/metrics", headers={"X-Admin-Token": "change-me"})
    assert r.status_code == 503 and "ADMIN_TOKEN" in r.json()["detail"]
    monkeypatch.setattr(s, "admin_token", "a-strong-secret-token")
    assert client.get("/api/admin/metrics", headers={"X-Admin-Token": "a-strong-secret-token"}).status_code == 200
