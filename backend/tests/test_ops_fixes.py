"""Hồi quy cho các phát hiện nghiệm thu vùng vận hành (fixer ops): O-F2, O-F3, O-F5, O-F6, O-F7, O-F8,
W-F4, W-F5, W-F11. (O-F1 có test ở test_acceptance_fixes.py::test_seed_as_main_runs_hooks.)"""
import json
import uuid

import pytest
from sqlalchemy import func, select

from app.db.models import ChatSession, ConstructionRate, Contractor, EstimateSession
from app.ingestion import csv_import
from app.services import admin_service

H = {"X-Admin-Token": "change-me"}
HCM = "TP. Hồ Chí Minh"
ESTIMATE = {"building_type": "nha_pho", "province": HCM, "land_area_m2": 100, "floors": 4,
            "foundation_type": "foundation_pile", "roof_type": "roof_concrete", "work_scope": "xay_tho"}


@pytest.fixture(autouse=True)
def _admin_token(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "admin_token", "change-me")
    monkeypatch.setattr(get_settings(), "app_env", "development")


def _ids(db, *slugs):
    rows = {c.slug: c.id for c in db.scalars(select(Contractor).where(Contractor.slug.in_(slugs)))}
    return [rows[s] for s in slugs]


# ------------------------------------------------------------------ O-F2: byte NUL / ký tự điều khiển
def _is_control_422(r):
    return r.status_code == 422 and "ký tự điều khiển" in r.json()["detail"]


def test_nul_in_query_and_path_is_422(client):
    assert _is_control_422(client.get("/api/contractors", params={"q": "\x00"}))
    assert _is_control_422(client.get("/api/contractors/kien%00phong"))
    assert _is_control_422(client.get("/api/contractors", params={"q": "a\x1fb"}))


def test_nul_in_json_body_is_422_for_every_router(client):
    r = client.post("/api/contractors/match",
                    content=json.dumps({"building_type": "nha_pho", "province": "Ha\u0000Noi", "work_scope": "xay_tho"}),
                    headers={"Content-Type": "application/json"})
    assert _is_control_422(r)
    assert _is_control_422(client.post("/api/chat/sessions", json={"user_id": "u\x00"}))
    assert _is_control_422(client.post("/api/estimates", json={**ESTIMATE, "province": "TP\x00HCM"}))
    sid = client.post("/api/chat/sessions", json={}).json()["session_id"]
    assert _is_control_422(client.post(f"/api/chat/sessions/{sid}/messages", json={"content": "xin\u0000chao"}))
    # Ký tự NUL thô (không escape) trong body
    r = client.post("/api/chat/sessions", content=b'{"user_id": "a\x00b"}', headers={"Content-Type": "application/json"})
    assert _is_control_422(r)


def test_normal_text_with_newline_tab_still_accepted(client):
    r = client.post("/api/chat/sessions", json={"user_id": "khach\t1"})
    assert r.status_code == 200
    r = client.post("/api/contractors/match", json={"province": HCM, "building_type": "nha_pho"})
    assert r.status_code == 200, r.text
    # Chuỗi '\\u0000' do khách gõ (dấu gạch chéo thật) không phải ký tự điều khiển
    r = client.get("/api/contractors", params={"q": "\\u0000"})
    assert r.status_code == 200


# ------------------------------------------------------------------ O-F5: 422 chat tiếng Việt, không echo input
def test_chat_validation_error_is_vietnamese_string(client):
    sid = client.post("/api/chat/sessions", json={}).json()["session_id"]
    r = client.post(f"/api/chat/sessions/{sid}/messages", json={"content": "a" * 5000})
    assert r.status_code == 422
    body = r.json()
    assert isinstance(body["detail"], str) and body["detail"].startswith("Dữ liệu không hợp lệ")
    assert "a" * 100 not in r.text  # không echo input
    r = client.post(f"/api/chat/sessions/{sid}/messages", content=b"{content:", headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and isinstance(r.json()["detail"], str) and "JSON" in r.json()["detail"]


def test_int_parsing_size_message_vi(client):
    est = client.post("/api/estimates", json=ESTIMATE).json()
    r = client.post(f"/api/estimates/{est['estimate_session_id']}/budget-adjustments",
                    content=b'{"target_budget_vnd": 1e308}', headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    assert "Unable to parse" not in r.json()["detail"]


# ------------------------------------------------------------------ O-F3: xlsx hỏng
def test_corrupt_xlsx_reports_failed_not_500(db, client):
    rep = csv_import.import_table(db, b"\x00\xff\xfe garbage", "x.xlsx", "rates", dry_run=True)
    assert rep.status == "failed" and "Excel không hợp lệ" in rep.errors[0].message
    r = client.post("/api/admin/import/rates", headers={**H, "Content-Type": "application/octet-stream"},
                    params={"filename": "x.xlsx", "dry_run": "true"}, content=b"\x00\xff\xfe garbage")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "failed" and "Excel không hợp lệ" in r.json()["errors"][0]["message"]
    r = client.post("/api/admin/import/rates", headers=H,
                    files={"file": ("bad.xlsx", b"PK\x03\x04 not really a zip", "application/octet-stream")})
    assert r.status_code == 200 and r.json()["status"] == "failed"


# ------------------------------------------------------------------ O-F7: dry_run xem trước đúng số dòng
RATES_CSV = ("building_type,province,work_scope,quality_tier,price_min,price_max,effective_from\n"
             "nha_pho,Đà Nẵng,xay_tho,trung_binh,3500000,4000000,2027-01-01\n"
             "nha_pho,Đà Nẵng,hoan_thien,trung_binh,2600000,3600000,2027-01-01\n"
             "biet_thu,Đà Nẵng,xay_tho,trung_binh,4100000,4900000,2027-01-01\n")


def test_dry_run_previews_insert_update_unchanged(db):
    content = RATES_CSV.encode("utf-8")
    before = db.scalar(select(func.count()).select_from(ConstructionRate))
    rep = csv_import.import_table(db, content, "dn.csv", "rates", dry_run=True)
    assert (rep.inserted, rep.updated, rep.unchanged, rep.skipped) == (3, 0, 0, 0) and rep.status == "ok"
    assert db.scalar(select(func.count()).select_from(ConstructionRate)) == before  # không ghi
    real = csv_import.import_table(db, content, "dn.csv", "rates")
    assert real.inserted == 3
    again = csv_import.import_table(db, content, "dn.csv", "rates", dry_run=True)
    assert again.status == "unchanged" and again.unchanged == 3 and again.inserted == 0
    changed = RATES_CSV.replace("3500000,4000000", "3550000,4000000").encode("utf-8")
    rep = csv_import.import_table(db, changed, "dn.csv", "rates", dry_run=True)
    assert (rep.inserted, rep.updated, rep.unchanged) == (0, 1, 2)
    row = db.scalar(select(ConstructionRate).where(ConstructionRate.province == "Đà Nẵng",
                                                   ConstructionRate.work_scope == "xay_tho",
                                                   ConstructionRate.building_type == "nha_pho"))
    assert int(row.price_min) == 3_500_000  # dry_run không sửa bản ghi


def test_dry_run_api_reports_would_insert(client, db):
    r = client.post("/api/admin/import/rates", headers=H, params={"dry_run": "true"},
                    files={"file": ("dn.csv", RATES_CSV.encode("utf-8"), "text/csv")})
    assert r.status_code == 200, r.text
    assert r.json()["dry_run"] is True and r.json()["inserted"] == 3 and r.json()["skipped"] == 0


# ------------------------------------------------------------------ O-F6: xem trạng thái job
class _FakeConn:
    def ping(self):
        return True


def test_sync_job_status_endpoint(client, monkeypatch):
    seen = {}

    def fake_fetch(job_id, connection=None):
        seen["id"] = job_id
        if job_id == "missing":
            return None
        return {"job_id": job_id, "job": "api_sync", "status": "finished", "enqueued_at": None, "started_at": None,
                "ended_at": None, "result": {"status": "skipped", "reason": "Chưa cấu hình DEZON_API_BASE_URL"},
                "error": None}

    monkeypatch.setattr(admin_service, "redis_connection", lambda: _FakeConn())
    monkeypatch.setattr(admin_service.jobs, "fetch_job", fake_fetch)
    r = client.get("/api/admin/sync/jobs/abc-123", headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "finished" and body["result"]["status"] == "skipped" and body["status_vi"] == "Bỏ qua"
    assert seen["id"] == "abc-123"
    assert client.get("/api/admin/sync/jobs/missing", headers=H).status_code == 404
    assert client.get("/api/admin/sync/jobs/a%20b", headers=H).status_code == 422
    assert client.get("/api/admin/sync/jobs/abc-123").status_code == 401


def test_sync_job_status_redis_down_503(client, monkeypatch):
    from redis import Redis

    monkeypatch.setattr(admin_service, "redis_connection",
                        lambda: Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.5, socket_timeout=0.5))
    r = client.get("/api/admin/sync/jobs/abc", headers=H)
    assert r.status_code == 503 and "Redis" in r.json()["detail"]


def test_enqueue_keeps_results_7_days(monkeypatch):
    from app import jobs

    captured = {}

    class Q:
        def enqueue_call(self, **kw):
            captured.update(kw)
            return type("J", (), {"id": "x"})()

    monkeypatch.setattr(jobs, "get_queue", lambda connection=None: Q())
    jobs.enqueue("reindex_all")
    assert captured["result_ttl"] == jobs.JOB_RESULT_TTL == 7 * 24 * 3600
    assert captured["failure_ttl"] == jobs.JOB_RESULT_TTL


def test_reindex_and_after_sync_jobs_log_stats(monkeypatch, caplog):
    from contextlib import contextmanager

    from app import jobs

    @contextmanager
    def fake_session():
        yield None

    monkeypatch.setattr(jobs, "_session", fake_session)
    monkeypatch.setattr("app.rag.embed.reindex_all", lambda db: 42)
    monkeypatch.setattr(jobs, "_after_sync", lambda db, changed: {"profile_completeness": "ok",
                                                                   "reindexed_chunks": 7})
    with caplog.at_level("INFO", logger="app.jobs"):
        assert jobs.job_reindex_all() == {"reindexed_chunks": 42}
        assert jobs.job_after_sync()["reindexed_chunks"] == 7
    assert "reindexed_chunks" in caplog.text and "job_after_sync" in caplog.text


# ------------------------------------------------------------------ O-F8: EMBEDDING_API_BASE qua Settings
def test_embedding_api_base_from_settings(monkeypatch):
    from app.config import Settings
    from app.rag import embed

    monkeypatch.setenv("EMBEDDING_API_BASE", "https://emb.example.com/v1/")
    s = Settings(_env_file=None)
    assert s.embedding_api_base == "https://emb.example.com/v1/"
    monkeypatch.setattr(embed, "get_settings", lambda: s.model_copy(update={"embedding_api_key": "k",
                                                                            "embedding_provider": "openai"}))
    embed._cached_embedder.cache_clear()
    try:
        e = embed.get_embedder()
        assert isinstance(e, embed.OpenAIEmbedder) and e.base_url == "https://emb.example.com/v1"
    finally:
        embed._cached_embedder.cache_clear()
    assert embed.OpenAIEmbedder("k", "m").base_url == "https://api.openai.com/v1"


# ------------------------------------------------------------------ W-F4: bảng so sánh dùng ngân sách của phiên chat
def _estimate_with_chat_budget(client, db, budget):
    chat = ChatSession(slots={**ESTIMATE, "district": None, "budget_vnd": budget})
    db.add(chat)
    db.flush()
    est = client.post("/api/estimates", json=ESTIMATE).json()
    es = db.get(EstimateSession, uuid.UUID(est["estimate_session_id"]))
    es.chat_session_id = chat.id
    db.flush()
    return est["estimate_session_id"]


def _fit_scores(body):
    fit = next(row for row in body["rows"] if row["key"] == "fit")
    return [c["value"]["score"] if c["value"] else None for c in fit["cells"]]


def test_compare_fit_uses_budget_from_chat_slots_same_as_match(client, db):
    ids = _ids(db, "kien-phong", "smartcons-tech", "huy-hoang")
    est_id = _estimate_with_chat_budget(client, db, 1_200_000_000)
    m = client.post("/api/contractors/match", json={"province": HCM, "building_type": "nha_pho",
                                                    "work_scope": "xay_tho", "floors": 4, "limit": 3,
                                                    "budget_vnd": 1_200_000_000, "estimate_session_id": est_id})
    assert m.status_code == 200, m.text
    match_score = {r["contractor"]["id"]: r["score"] for r in m.json()["results"]}
    r = client.post("/api/contractors/compare", json={"contractor_ids": ids, "estimate_session_id": est_id})
    assert r.status_code == 200, r.text
    scores = dict(zip(ids, _fit_scores(r.json()), strict=True))
    for cid, s in match_score.items():
        assert scores[cid] == s, (cid, scores, match_score)
    # Không có ngân sách -> điểm khác (tiêu chí ngân sách trung tính) => chứng minh ngân sách đã được dùng
    r2 = client.post("/api/contractors/compare", json={"contractor_ids": ids,
                                                       "estimate_session_id": client.post(
                                                           "/api/estimates", json=ESTIMATE).json()[
                                                           "estimate_session_id"]})
    assert _fit_scores(r2.json()) != _fit_scores(r.json())


def test_compare_budget_in_body_overrides(client, db):
    ids = _ids(db, "kien-phong", "smartcons-tech", "huy-hoang")
    est_id = client.post("/api/estimates", json=ESTIMATE).json()["estimate_session_id"]
    m = client.post("/api/contractors/match", json={"province": HCM, "building_type": "nha_pho", "limit": 3,
                                                    "budget_vnd": 1_200_000_000, "estimate_session_id": est_id})
    match_score = {r["contractor"]["id"]: r["score"] for r in m.json()["results"]}
    r = client.post("/api/contractors/compare", json={"contractor_ids": ids, "estimate_session_id": est_id,
                                                      "budget_vnd": 1_200_000_000})
    assert r.status_code == 200, r.text
    scores = dict(zip(ids, _fit_scores(r.json()), strict=True))
    assert all(scores[cid] == s for cid, s in match_score.items())
    bad = client.post("/api/contractors/compare", json={"contractor_ids": ids, "budget_vnd": -5})
    assert bad.status_code == 422 and isinstance(bad.json()["detail"], str)


# ------------------------------------------------------------------ W-F11 (backend): ô "Hồ sơ dự án" có nguồn
def test_compare_every_cell_has_status_and_source(client, db):
    ids = _ids(db, "kien-phong", "smartcons-tech", "huy-hoang")
    est_id = client.post("/api/estimates", json=ESTIMATE).json()["estimate_session_id"]
    body = client.post("/api/contractors/compare", json={"contractor_ids": ids, "estimate_session_id": est_id}).json()
    for row in body["rows"]:
        for cell in row["cells"]:
            assert cell["status"] in ("verified", "on_dezon", "reference", "unavailable")
            if cell["status"] != "unavailable":
                assert cell["source"] and cell["source"]["label"], (row["key"], cell)
    projects = next(row for row in body["rows"] if row["key"] == "projects")["cells"][0]
    assert projects["status"] == "on_dezon" and projects["source"]["url"].startswith("https://dezon.vn/nha-thau/")


# ------------------------------------------------------------------ W-F5: snapshot dữ liệu thật dezon.vn
def test_snapshot_file_is_real_dezon_data():
    from app.ingestion.seed_snapshot import SNAPSHOT_PATH

    data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    slugs = {c["slug"] for c in data["contractors"]}
    assert {"kien-phong", "smartcons-tech", "huy-hoang"} <= slugs
    for p in data["projects"]:
        assert p["project_url"].startswith("https://dezon.vn/nha-thau/")
        assert p["_source"]["url"].startswith("https://dezon.vn/") and p["_source"]["raw_hash"]
        for img in p["images"]:
            assert img["url"].startswith("https://dezon.vn/") and "placehold" not in img["url"]


def test_load_snapshot_gives_projects_and_images_for_demo_contractors(db):
    from app.ingestion.seed_snapshot import load_snapshot
    from app.services import contractor_service

    result = load_snapshot(db)
    assert result["status"] == "ok", result
    total_imgs = []
    for slug in ("kien-phong", "smartcons-tech", "huy-hoang"):
        c = db.scalar(select(Contractor).where(Contractor.slug == slug))
        profile = contractor_service.get_contractor_profile(db, slug)
        assert profile is not None
        projects = contractor_service.get_contractor_projects(db, c.id, limit=20)
        assert projects, slug
        assert all(p.project_url and p.project_url.startswith("https://dezon.vn/nha-thau/") for p in projects)
        imgs = [i.url for p in contractor_service._unique_projects(c) for i in p.images]
        assert imgs and not any("placehold" in u for u in imgs), slug
        assert c.avatar_url and c.avatar_url.startswith("https://dezon.vn/"), slug
        total_imgs.append(len(imgs))
    assert max(total_imgs) > 3  # để kiểm "+N" của lưới ảnh
    # Chạy lại không đổi gì (idempotent)
    again = load_snapshot(db)
    assert again["created"] == 0
