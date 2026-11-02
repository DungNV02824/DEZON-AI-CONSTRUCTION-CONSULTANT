"""RAG (embed, chunk, retrieve), search_knowledge, cùng các phần ghép nối ingestion: api_sync, seed_snapshot, jobs."""
import json
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import func, select

from app.db.models import Contractor, Document, Project
from app.rag.embed import (
    EMBED_DIM,
    HashEmbedder,
    OpenAIEmbedder,
    build_documents,
    chunk_text,
    cosine,
    reindex_all,
    reindex_entity,
    strip_numeric_claims,
)
from app.schemas.knowledge import USAGE_NOTE_VI, KnowledgeChunk


# ================================================================== embedder thuần
def test_hash_embedder_deterministic_and_normalized():
    e = HashEmbedder()
    a1, a2 = e.embed(["Biệt thự phong cách Indochine"])[0], HashEmbedder().embed(["Biệt thự phong cách Indochine"])[0]
    assert a1 == a2 and len(a1) == EMBED_DIM
    assert abs(sum(v * v for v in a1) - 1.0) < 1e-9
    empty = e.embed([""])[0]
    assert len(empty) == EMBED_DIM and abs(sum(v * v for v in empty) - 1.0) < 1e-9


def test_hash_embedder_similar_sentences_score_higher():
    e = HashEmbedder()
    q, near, far = e.embed([
        "thi công biệt thự phong cách Indochine",
        "nhà thầu xây biệt thự Indochine tại Quận 7",
        "báo giá sơn chống thấm cho văn phòng",
    ])
    assert cosine(q, near) > cosine(q, far) + 0.1
    # gấp dấu + đồng nghĩa: không dấu / "villa" vẫn gần "biệt thự"
    no_accent, villa, other = e.embed(["biet thu indochine", "Indochine Villa", "căn hộ chung cư Hà Nội"])
    bt = e.embed(["Biệt thự Indochine"])[0]
    assert cosine(bt, no_accent) > 0.9
    assert cosine(bt, villa) > cosine(bt, other)


def test_openai_embedder_uses_httpx(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append((str(request.url), request.headers["authorization"], body))
        return httpx.Response(200, json={"data": [{"index": i, "embedding": [0.0] * 8} for i in
                                                  range(len(body["input"]))]})

    emb = OpenAIEmbedder("sk-test", "text-embedding-3-small", dim=8, base_url="https://emb.test/v1",
                         client=httpx.Client(transport=httpx.MockTransport(handler)))
    out = emb.embed(["a", "b"])
    assert len(out) == 2 and len(out[0]) == 8
    assert calls[0][0] == "https://emb.test/v1/embeddings" and calls[0][1] == "Bearer sk-test"
    assert calls[0][2]["dimensions"] == 8 and calls[0][2]["model"] == "text-embedding-3-small"
    with pytest.raises(ValueError):
        OpenAIEmbedder("", "m")


def test_chunk_text_respects_size_and_overlap():
    sentences = [f"Câu số {i} mô tả không gian sống rộng mở và kết nối thiên nhiên." for i in range(40)]
    chunks = chunk_text(" ".join(sentences), max_chars=300, overlap=80)
    assert len(chunks) > 3
    assert all(len(c) <= 300 for c in chunks)
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        assert prev.split(". ")[-1].rstrip(".") in nxt  # câu cuối chunk trước lặp lại ở chunk sau
    assert chunk_text("") == []
    assert chunk_text("Một câu ngắn.") == ["Một câu ngắn."]


def test_strip_numeric_claims_keeps_names():
    text = ("Kiến Phong là đơn vị thi công tại TP. HCM, thành lập năm 2018. Quy mô ghi nhận: 12 nhân sự văn phòng, "
            "20 nhân sự thi công, 15 dự án/năm. Đơn giá Từ 4,3 đến 4,7 tr/m², quy mô 25 tỷ / công trình. "
            "Tiêu biểu: Indochine Villa Quận 7 và France Indochine 22.")
    out = strip_numeric_claims(text)
    for bad in ("2018", "12 nhân sự", "20 nhân sự", "15 dự án", "4,3", "4,7", "25 tỷ"):
        assert bad not in out
    assert "Quận 7" in out and "France Indochine 22" in out


# ================================================================== DB: documents + search
def test_build_documents_contractor_has_no_structured_numbers(db):
    kp = db.scalar(select(Contractor).where(Contractor.slug == "kien-phong"))
    docs = build_documents(db, "contractor", kp)
    text = "\n".join(docs)
    assert "Kiến Phong" in text and "Biệt thự" in text and "Indochine" in text
    assert "Công trình ghi nhận trên Dezon" in text
    for number in ("4,3", "4.300.000", "4300000", "25 tỷ", "2018", "20 thợ", "12 văn phòng"):
        assert number not in text


def test_reindex_all_upserts_and_removes_extra_chunks(db):
    n = reindex_all(db)
    contractors = db.scalar(select(func.count()).select_from(Contractor).where(Contractor.is_active.is_(True)))
    projects = db.scalar(select(func.count()).select_from(Project))
    total_docs = db.scalar(select(func.count()).select_from(Document))
    assert n == total_docs >= contractors + projects
    # idempotent: chạy lại không tạo chunk trùng
    assert reindex_all(db) == n
    assert db.scalar(select(func.count()).select_from(Document)) == total_docs

    kp = db.scalar(select(Contractor).where(Contractor.slug == "kien-phong"))
    kp.description = " ".join(f"Đoạn mô tả số {i} về phong cách Indochine và hiện đại." for i in range(60))
    db.flush()
    many = reindex_entity(db, "contractor", kp.id)
    assert many > 1
    kp.description = "Mô tả ngắn."
    db.flush()
    assert reindex_entity(db, "contractor", kp.id) == 1
    assert db.scalar(select(func.count()).select_from(Document).where(
        Document.entity_type == "contractor", Document.entity_id == kp.id)) == 1

    kp.is_active = False
    db.flush()
    assert reindex_entity(db, "contractor", kp.id) == 0
    assert db.scalar(select(func.count()).select_from(Document).where(
        Document.entity_type == "contractor", Document.entity_id == kp.id)) == 0


def test_search_biet_thu_indochine_returns_kien_phong_and_projects(db):
    from app.rag.retrieve import search

    reindex_all(db)
    results = search(db, "biệt thự Indochine", k=5)
    assert results and all(isinstance(r, KnowledgeChunk) for r in results)
    names = [r.entity_name for r in results]
    assert "Kiến Phong" in names
    assert any(r.entity_type == "project" and "Indochine" in (r.entity_name or "") for r in results)
    assert "Huy Hoàng" not in names[:3]
    for r in results:
        assert r.url and r.url.startswith("https://dezon.vn/")
        assert r.usage_note == USAGE_NOTE_VI
        assert r.source is not None and r.source.label
    assert results == sorted(results, key=lambda r: r.score, reverse=True)
    kp = next(r for r in results if r.entity_name == "Kiến Phong")
    assert kp.url == db.scalar(select(Contractor.profile_url).where(Contractor.slug == "kien-phong"))

    only_projects = search(db, "biet thu indochine", entity_type="project", k=3)  # không dấu vẫn tìm được
    assert only_projects and all(r.entity_type == "project" for r in only_projects)
    assert search(db, "   ") == []


def test_search_knowledge_service(db):
    from app.services.knowledge_service import search_knowledge

    reindex_all(db)
    res = search_knowledge(db, "Green Pool Villa Vĩnh Long", limit=3)
    assert res[0].entity_name == "Green Pool Villa Vĩnh Long"
    assert res[0].url.rstrip("/").endswith("green-pool-villa-vinh-long")
    assert len(search_knowledge(db, "nhà phố", entity_type="khong_hop_le", limit=2)) <= 2


def test_search_knowledge_drops_irrelevant_chunks(db):
    """C-F3: câu hỏi chi phí lạc đề (ép cọc, giả định số tầng) không trả mô tả nhà thầu/dự án không liên quan."""
    from app.services.knowledge_service import search_knowledge

    reindex_all(db)
    assert search_knowledge(db, "ép cọc hết bao nhiêu tiền?") == []
    assert search_knowledge(db, "nếu tôi chỉ xây 3 tầng thì hết bao nhiêu?") == []
    assert search_knowledge(db, "Green Pool Villa Vĩnh Long", limit=3)  # câu hỏi đúng thực thể vẫn có kết quả


# ================================================================== api_sync / snapshot / jobs
def test_api_sync_skipped_when_not_configured(db, monkeypatch):
    from app.ingestion import api_sync

    monkeypatch.setattr(api_sync, "is_configured", lambda: False)
    res = api_sync.run_api_sync(db)
    assert res["status"] == "skipped" and "DEZON_API_BASE_URL" in res["reason"]


def test_api_sync_with_mock_client(db):
    from app.ingestion.api_sync import DezonApiClient, run_api_sync

    payloads = {
        "/contractors": {"data": [{"id": 9001, "slug": "kien-phong", "name": "Kiến Phong",
                                   "rough_price_min": 4_500_000, "rough_price_max": 4_900_000,
                                   "province": "TP. HCM", "building_types": ["Nhà phố", "Biệt thự"],
                                   "updated_at": "2026-11-01T00:00:00Z"}], "next_page": None},
        "/projects": {"data": [], "next_page": None},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer tok"
        return httpx.Response(200, json=payloads[request.url.path.replace("/v1", "")])

    client = DezonApiClient("https://api.dezon.test/v1", "tok",
                            client=httpx.Client(transport=httpx.MockTransport(handler)))
    res = run_api_sync(db, client=client, commit=False)
    assert res["status"] == "ok" and res["updated"] == 1
    kp = db.scalar(select(Contractor).where(Contractor.slug == "kien-phong"))
    assert int(kp.rough_price_min) == 4_500_000
    src = kp.field_sources["rough_price_min"]
    from app.db.models import DataSource
    ds = db.get(DataSource, src)
    assert ds.source_type == "dezon_api" and ds.status == "on_dezon"
    again = run_api_sync(db, client=client, commit=False)
    assert again["unchanged"] == 1 and again["updated"] == 0


def test_seed_snapshot_noop_by_default(db, monkeypatch, tmp_path):
    from app.ingestion import seed_snapshot

    monkeypatch.delenv("SEED_SNAPSHOT", raising=False)
    before = db.scalar(select(func.count()).select_from(Contractor))
    seed_snapshot.seed_hook(db)
    assert db.scalar(select(func.count()).select_from(Contractor)) == before

    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"contractors": [{
        "kind": "contractor", "slug": "snapshot-test", "external_id": "snapshot-test", "name": "Nhà thầu Snapshot",
        "profile_url": "https://dezon.vn/nha-thau/snapshot-test/", "province": "Hà Nội",
        "building_types": ["nha_pho"], "_source": {"url": "https://dezon.vn/nha-thau/snapshot-test/",
                                                   "raw_hash": "abc", "fetched_at": datetime.now(UTC).isoformat()}}],
        "projects": []}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(seed_snapshot, "SNAPSHOT_PATH", snap)
    monkeypatch.setenv("SEED_SNAPSHOT", "1")
    res = seed_snapshot.load_snapshot(db, snap)
    assert res["status"] == "ok" and res["created"] == 1
    assert seed_snapshot.load_snapshot(db, tmp_path / "khong_co.json")["status"] == "skipped"


def test_jobs_registry_and_scheduler_time():
    from zoneinfo import ZoneInfo

    from app import jobs
    from app.worker import next_run_at

    with pytest.raises(ValueError):
        jobs.enqueue("khong_ton_tai")
    assert {"crawl", "api_sync", "import_csv", "reindex_all", "after_sync", "daily_sync"} <= set(jobs.JOBS)
    tz = ZoneInfo("Asia/Ho_Chi_Minh")
    assert next_run_at(datetime(2026, 11, 2, 1, 30, tzinfo=tz)) == datetime(2026, 11, 2, 2, 0, tzinfo=tz)
    assert next_run_at(datetime(2026, 11, 2, 2, 0, tzinfo=tz)) == datetime(2026, 11, 3, 2, 0, tzinfo=tz)
    # 19:30 UTC = 02:30 giờ VN hôm sau -> lần kế tiếp là 02:00 ngày kế nữa
    assert next_run_at(datetime(2026, 11, 2, 19, 30, tzinfo=UTC)) == datetime(2026, 11, 4, 2, 0, tzinfo=tz)


def test_enqueue_daily_sync_once_per_day(monkeypatch):
    from app import worker

    class FakeRedis:
        def __init__(self):
            self.keys = {}

        def set(self, key, value, nx=False, ex=None):
            if nx and key in self.keys:
                return None
            self.keys[key] = value
            return True

    queued = []
    monkeypatch.setattr("app.jobs.enqueue", lambda name, connection=None, **kw: queued.append(name))
    conn = FakeRedis()
    when = datetime(2026, 11, 3, 2, 0, tzinfo=worker.TZ)
    assert worker.enqueue_daily_sync(conn, when) is True
    assert worker.enqueue_daily_sync(conn, when) is False
    assert queued == ["daily_sync"]


def test_daily_sync_picks_api_or_crawl(monkeypatch):
    from app import jobs

    monkeypatch.setattr(jobs, "job_api_sync", lambda: {"status": "ok"})
    monkeypatch.setattr(jobs, "job_crawl", lambda: {"pages": 0})

    class S:
        dezon_api_base_url = ""

    monkeypatch.setattr(jobs, "get_settings", lambda: S)
    assert jobs.job_daily_sync()["source"] == "crawl"
    S.dezon_api_base_url = "https://api.dezon.test"
    assert jobs.job_daily_sync()["source"] == "api"


def test_resolve_embedding_provider():
    from app.rag.embed import resolve_provider

    class S:
        embedding_provider = "auto"
        embedding_api_key = ""

    assert resolve_provider(S) == "hash"
    S.embedding_api_key = "sk-x"
    assert resolve_provider(S) == "openai"
    S.embedding_provider = "hash"
    assert resolve_provider(S) == "hash"


# ================================================================== review fixes
def test_strip_numeric_claims_more_patterns():
    text = ("Đơn giá 4.300.000/m² cho phần thô. Bảo hành 12 tháng. Thành lập 2018, đội 30 kỹ sư, "
            "đã làm 120 căn nhà, khách sạn 5 sao. Dự án Indochine Villa Quận 7, block S1.2018.")
    out = strip_numeric_claims(text)
    for bad in ("4.300.000", "12 tháng", "2018,", "30 kỹ sư", "120 căn", "5 sao"):
        assert bad not in out
    assert "Quận 7" in out and "S1.2018" in out


def test_api_sync_skips_records_without_id(db):
    from app.ingestion.api_sync import DezonApiClient, run_api_sync

    payloads = {"/contractors": {"data": [{"name": "Không định danh"},
                                          {"id": 77, "name": "Giá lỗi", "rough_price_min": "abc"}]},
                "/projects": {"data": []}}
    client = DezonApiClient("https://api.dezon.test", "tok", client=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json=payloads[r.url.path]))))
    res = run_api_sync(db, client=client, commit=False)
    assert res["status"] == "ok" and res["skipped"] == 1 and res["errors"] == 1 and res["created"] == 0
    assert db.scalar(select(Contractor).where(Contractor.slug == "None")) is None


def test_daily_sync_falls_back_to_crawl_when_api_skipped(monkeypatch):
    from app import jobs

    monkeypatch.setattr(jobs, "job_api_sync", lambda: {"status": "skipped", "reason": "thiếu token"})
    monkeypatch.setattr(jobs, "job_crawl", lambda: {"pages": 1})

    class S:
        dezon_api_base_url = "https://api.dezon.test"

    monkeypatch.setattr(jobs, "get_settings", lambda: S)
    out = jobs.job_daily_sync()
    assert out["source"] == "crawl" and out["pages"] == 1


def test_enqueue_passes_job_kwargs_verbatim(monkeypatch):
    from app import jobs

    calls = []

    class Q:
        def enqueue_call(self, func, kwargs, **opts):  # opts: result_ttl/failure_ttl của RQ
            calls.append((func, kwargs))
            return "job"

    monkeypatch.setattr(jobs, "get_queue", lambda connection=None: Q())
    assert jobs.enqueue("import_csv", path="/tmp/a.csv", kind="rates") == "job"
    assert calls == [(jobs.job_import_csv, {"path": "/tmp/a.csv", "kind": "rates"})]
