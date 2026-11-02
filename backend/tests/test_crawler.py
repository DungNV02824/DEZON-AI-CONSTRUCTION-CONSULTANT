"""Crawler: parser theo HTML thật (fixture), fetcher lịch sự (robots + delay), upsert theo raw_hash và ưu tiên nguồn.

Không gọi mạng thật: mọi HTTP đi qua httpx.MockTransport.
"""
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.db.models import Contractor, DataSource, Project, ProjectContractor
from app.evidence import SourceType
from app.ingestion.crawler import parsers
from app.ingestion.crawler.fetcher import (
    MIN_DELAY_SECONDS,
    FetchBudgetExceeded,
    Fetcher,
    RobotsDisallowed,
    content_hash,
)
from app.ingestion.crawler.runner import run_crawl
from app.ingestion.upsert import ingest_page, upsert_contractor, upsert_data_source

FIX = Path(__file__).parent / "fixtures" / "dezon"
SITE = "https://dezon.vn"
KP_URL = f"{SITE}/nha-thau/kien-phong/"
WORK_URL = f"{SITE}/nha-thau/kien-phong/indochine-villa-quan-7-sai-gon/"
PROJECT_URL = f"{SITE}/du-an/villa-vinh-cuu/"


def fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# ================================================================== parsers (HTML thật)
def test_parse_contractor_list_tiem_nang():
    cards = parsers.parse_contractor_list(fx("contractor_list_tiem_nang.html"), f"{SITE}/nha-thau/tiem-nang/")
    assert len(cards) == 41
    kp = next(c for c in cards if c["slug"] == "kien-phong")
    assert kp["name"] == "Kiến Phong"
    assert kp["profile_url"] == KP_URL
    assert kp["province"] == "TP. Hồ Chí Minh"
    assert kp["dezon_project_count"] == 7
    assert kp["years_experience"] == 8
    assert kp["building_types"] == ["nha_pho", "biet_thu", "can_ho"]
    assert kp["badge"] == "Nhà thầu tiềm năng"
    avora = next(c for c in cards if c["slug"] == "avora")
    assert "years_experience" not in avora  # "0 năm" = chưa khai báo, không ghi 0
    assert next(c for c in cards if c["slug"] == "chau-phuong-thinh")["province"] is None


def test_parse_contractor_list_main_page_has_both_card_layouts():
    cards = parsers.parse_contractor_list(fx("contractor_list.html"), f"{SITE}/nha-thau/")
    slugs = {c["slug"] for c in cards}
    assert {"kien-phong", "smartcons-tech", "huy-hoang", "coteccons"} <= slugs
    assert "tiem-nang" not in slugs  # trang nhóm không phải nhà thầu
    cot = next(c for c in cards if c["slug"] == "coteccons")
    assert cot["dezon_project_count"] == 38 and "biet_thu" in cot["building_types"]
    assert next(c for c in cards if c["slug"] == "kien-phong")["badge"] == "Nhà thầu tiềm năng"


def test_parse_contractor_profile_kien_phong_matches_spec_fixture():
    rec = parsers.parse_contractor_profile(fx("contractor_kien_phong.html"), KP_URL)
    # giá trị quan sát trong đặc tả mục 11.3
    assert rec["slug"] == "kien-phong" and rec["name"] == "Kiến Phong"
    assert rec["badge"] == "Nhà thầu tiềm năng"
    assert rec["province"] == "TP. Hồ Chí Minh"
    assert rec["founded_year"] == 2018
    assert rec["years_experience"] == 8
    assert (rec["staff_workers"], rec["staff_office"]) == (20, 12)
    assert (rec["rough_price_min"], rec["rough_price_max"]) == (4_300_000, 4_700_000)
    assert (rec["finish_price_min"], rec["finish_price_max"]) == (7_300_000, 9_500_000)
    assert rec["typical_project_value"] == 25_000_000_000
    assert rec["dezon_project_count"] == 7
    assert rec["building_types"][:3] == ["nha_pho", "biet_thu", "can_ho"]
    assert rec["has_warranty_info"] is None and rec["warranty_text"] is None  # trang không có -> None
    assert rec["profile_url"] == KP_URL
    assert rec["description"].startswith("Kiến Phong là đơn vị thi công")
    names = [w["name"] for w in rec["works"]]
    assert "France Indochine 22 Hà Nội" in names and "Green Pool Villa Vĩnh Long" in names
    assert any(w["url"] == WORK_URL for w in rec["works"])
    assert rec["source_updated_at"] is not None


def test_parse_contractor_profile_range_values_are_not_collapsed():
    rec = parsers.parse_contractor_profile(fx("contractor_huy_hoang.html"), f"{SITE}/nha-thau/huy-hoang/")
    assert rec["typical_project_value"] is None  # "7 – 15 tỷ" là khoảng -> không tự chọn 1 giá trị
    assert rec["extra"]["typical_project_value_range"] == [7_000_000_000, 15_000_000_000]
    assert rec["staff_workers"] is None and rec["staff_office"] == 5
    assert rec["founded_year"] == 2004


def test_parse_contractor_work_page():
    rec = parsers.parse_contractor_work(fx("contractor_work_indochine_villa_quan_7.html"), WORK_URL)
    assert rec["name"] == "Indochine Villa Quận 7 Sài gòn"
    assert rec["slug"] == "kien-phong--indochine-villa-quan-7-sai-gon"
    assert rec["project_url"] == WORK_URL
    assert rec["building_type"] == "biet_thu"
    assert rec["province"] == "TP. Hồ Chí Minh"
    assert len(rec["images"]) == 8 and rec["images"][0]["url"].startswith("https://dezon.vn/wp-content/")
    assert rec["contractors"] == [{"slug": "kien-phong", "name": "Kiến Phong", "profile_url": KP_URL,
                                   "role": "thi_cong"}]


def test_parse_project_list_and_pagination():
    res = parsers.parse_project_list(fx("project_list.html"), f"{SITE}/du-an/")
    assert len(res["items"]) == 20
    assert res["next_page"] == f"{SITE}/du-an/page/2/"
    vinh_cuu = next(i for i in res["items"] if i["slug"] == "villa-vinh-cuu")
    assert vinh_cuu["project_url"] == PROJECT_URL and vinh_cuu["name"] == "Villa Vĩnh Cửu"


def test_parse_project_page():
    rec = parsers.parse_project_page(fx("project_villa_vinh_cuu.html"), PROJECT_URL)
    assert rec["name"] == "Villa Vĩnh Cửu" and rec["slug"] == "villa-vinh-cuu"
    assert rec["building_type"] == "biet_thu"
    assert rec["province"] == "Đồng Nai"
    assert rec["floor_area_m2"] == 600
    assert rec["status"] == "completed" and rec["extra"]["completed_year"] == 2026
    assert rec["contractors"][0]["slug"] == "fyl-design-build"
    assert len(rec["images"]) == 5
    assert rec["description"].startswith("Từ giai đoạn hình thành ý tưởng")


# ================================================================== fetcher
def _transport(routes: dict[str, tuple[int, str]], seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        status, body = routes.get(str(request.url), (404, "not found"))
        return httpx.Response(status, text=body, headers={"content-type": "text/html; charset=utf-8"})

    return httpx.MockTransport(handler)


def _fetcher(routes, seen, sleeps, **kw) -> Fetcher:
    return Fetcher(client=httpx.Client(transport=_transport(routes, seen)), sleep=sleeps.append,
                   clock=lambda: 0.0, **kw)


def test_fetcher_respects_robots_delay_and_user_agent():
    seen: list[httpx.Request] = []
    sleeps: list[float] = []
    routes = {f"{SITE}/robots.txt": (200, fx("robots.txt")), KP_URL: (200, fx("contractor_kien_phong.html"))}
    f = _fetcher(routes, seen, sleeps, min_delay=0.5)  # < 2s bị nâng lên 2s
    res = f.fetch(KP_URL)
    assert res.ok and res.raw_hash == content_hash(res.html)
    with pytest.raises(RobotsDisallowed):
        f.fetch(f"{SITE}/planning/abc/")  # robots.txt thật chặn /planning/
    assert all("DezonAIBot" in r.headers["user-agent"] for r in seen)
    assert [str(r.url) for r in seen] == [f"{SITE}/robots.txt", KP_URL]  # trang bị chặn không được tải
    assert f.min_delay == MIN_DELAY_SECONDS
    assert sleeps and all(s >= MIN_DELAY_SECONDS for s in sleeps)


def test_fetcher_blocks_when_robots_disallows_bot():
    seen, sleeps = [], []
    routes = {f"{SITE}/robots.txt": (200, "User-agent: DezonAIBot\nDisallow: /\n"),
              KP_URL: (200, "<html></html>")}
    f = _fetcher(routes, seen, sleeps)
    with pytest.raises(RobotsDisallowed):
        f.fetch(KP_URL)
    assert [str(r.url) for r in seen] == [f"{SITE}/robots.txt"]


def test_fetcher_crawl_delay_and_budget():
    seen, sleeps = [], []
    routes = {f"{SITE}/robots.txt": (200, "User-agent: *\nCrawl-delay: 5\nAllow: /\n"),
              KP_URL: (200, "<html><main>a</main></html>")}
    f = _fetcher(routes, seen, sleeps, max_requests=2)
    f.fetch(KP_URL)
    assert sleeps == [5.0]
    with pytest.raises(FetchBudgetExceeded):
        f.fetch(KP_URL)


def test_content_hash_ignores_volatile_parts():
    a = '<html><head></head><body><script>var n="1"</script><main><h1>X</h1><div class="post_view">209</div></main></body></html>'
    b = '<html><head></head><body><script>var n="2"</script><main><h1>X</h1><div class="post_view">350</div></main></body></html>'
    c = a.replace("<h1>X</h1>", "<h1>Y</h1>")
    assert content_hash(a) == content_hash(b)
    assert content_hash(a) != content_hash(c)


# ================================================================== upsert (DB)
def _ingest_profile(db, html: str):
    rec = parsers.parse_contractor_profile(html, KP_URL)
    return ingest_page(db, [rec], url=KP_URL, source_type=SourceType.crawl_public, raw_hash=content_hash(html))


def test_upsert_profile_created_unchanged_updated(db):
    html = fx("contractor_kien_phong.html")
    out = _ingest_profile(db, html)[0]
    assert out.action == "updated"  # Kiến Phong đã có từ seed -> cập nhật theo trang thật
    kp = db.scalar(select(Contractor).where(Contractor.slug == "kien-phong"))
    ds = db.scalar(select(DataSource).where(DataSource.url == KP_URL))
    assert ds.source_type == "crawl_public" and ds.status == "on_dezon" and ds.raw_hash == content_hash(html)
    assert kp.profile_url == KP_URL and kp.source_id == ds.id
    assert kp.field_sources["rough_price_min"] == ds.id
    assert kp.rough_price_min == Decimal(4_300_000)
    assert db.scalar(select(DataSource).where(DataSource.url == KP_URL)).id == ds.id

    # raw_hash không đổi -> unchanged, không ghi gì
    kp.description = "sửa tay để kiểm tra"
    db.flush()
    out2 = _ingest_profile(db, html)[0]
    assert out2.action == "unchanged"
    assert kp.description == "sửa tay để kiểm tra"
    assert db.query(DataSource).filter(DataSource.url == KP_URL).count() == 1

    # nội dung đổi -> updated
    html2 = html.replace("Từ 4,3 đến 4,7 tr/m²", "Từ 4,4 đến 4,9 tr/m²")
    out3 = _ingest_profile(db, html2)[0]
    assert out3.action == "updated" and "rough_price_min" in out3.changed_fields
    db.refresh(kp)
    assert (kp.rough_price_min, kp.rough_price_max) == (Decimal(4_400_000), Decimal(4_900_000))
    assert db.get(DataSource, ds.id).raw_hash == content_hash(html2)


def test_upsert_respects_source_priority(db):
    kp = db.scalar(select(Contractor).where(Contractor.slug == "kien-phong"))
    api_ds, _ = upsert_data_source(db, url="https://api.dezon.test/contractors/kien-phong",
                                   label="Dữ liệu Dezon (API) — nhà thầu Kiến Phong",
                                   source_type=SourceType.dezon_api, raw_hash="api-1")
    assert api_ds.status == "on_dezon"
    upsert_contractor(db, {"slug": "kien-phong", "rough_price_min": 4_500_000, "warranty_text": None}, api_ds)
    assert kp.rough_price_min == Decimal(4_500_000)
    assert kp.field_sources["rough_price_min"] == api_ds.id

    out = _ingest_profile(db, fx("contractor_kien_phong.html"))[0]
    db.refresh(kp)
    assert kp.rough_price_min == Decimal(4_500_000)  # crawl (ưu tiên 3) không ghi đè API (ưu tiên 1)
    assert kp.field_sources["rough_price_min"] == api_ds.id
    assert "rough_price_min" in out.skipped_fields
    assert kp.rough_price_max == Decimal(4_700_000)  # trường API không cung cấp vẫn lấy từ crawl
    assert kp.field_sources["rough_price_max"] != api_ds.id

    # nguồn ưu tiên cao hơn được ghi đè nguồn thấp hơn
    upsert_contractor(db, {"slug": "kien-phong", "rough_price_max": 4_800_000}, api_ds)
    assert kp.rough_price_max == Decimal(4_800_000)
    # None từ nguồn khác không xóa dữ liệu đang có
    upsert_contractor(db, {"slug": "kien-phong", "founded_year": None}, api_ds)
    assert kp.founded_year == 2018


def test_upsert_work_project_images_and_link(db):
    html = fx("contractor_work_indochine_villa_quan_7.html")
    rec = parsers.parse_contractor_work(html, WORK_URL)
    out = ingest_page(db, [rec], url=WORK_URL, raw_hash=content_hash(html))[0]
    # Seed đã có công trình này (cùng URL/slug thật) -> crawl cập nhật bản ghi seed, không tạo trùng
    assert out.action == "updated"
    p = db.get(Project, out.entity_id)
    assert p.project_url == WORK_URL and p.building_type == "biet_thu" and len(p.images) == 8
    kp = db.scalar(select(Contractor).where(Contractor.slug == "kien-phong"))
    assert db.get(ProjectContractor, (p.id, kp.id, "thi_cong")) is not None
    again = ingest_page(db, [rec], url=WORK_URL, raw_hash=content_hash(html))[0]
    assert again.action == "unchanged"


def test_upsert_project_page_unknown_contractor(db):
    html = fx("project_villa_vinh_cuu.html")
    rec = parsers.parse_project_page(html, PROJECT_URL)
    out = ingest_page(db, [rec], url=PROJECT_URL, raw_hash=content_hash(html))[0]
    assert out.action == "created"
    assert db.scalar(select(Contractor).where(Contractor.slug == "fyl-design-build")) is None  # không tạo bừa
    out2 = ingest_page(db, [rec], url=PROJECT_URL, raw_hash="hash-moi", create_missing_contractors=True)[0]
    stub = db.scalar(select(Contractor).where(Contractor.slug == "fyl-design-build"))
    assert out2.action == "updated" and stub is not None
    assert stub.rough_price_min is None and stub.dezon_project_count is None  # chỉ tên + URL, không suy diễn


def test_list_page_does_not_override_profile_capabilities(db):
    html = fx("contractor_kien_phong.html")
    _ingest_profile(db, html)
    kp = db.scalar(select(Contractor).where(Contractor.slug == "kien-phong"))
    caps_before = sorted((c.building_type, c.province) for c in kp.capabilities)
    assert ("khac", "TP. Hồ Chí Minh") in caps_before
    list_html = fx("contractor_list_tiem_nang.html")
    cards = parsers.parse_contractor_list(list_html, f"{SITE}/nha-thau/tiem-nang/")
    outs = ingest_page(db, cards, url=f"{SITE}/nha-thau/tiem-nang/", label="Danh sách nhà thầu trên Dezon",
                       raw_hash=content_hash(list_html), primary=False)
    assert len(outs) == 41
    db.refresh(kp)
    assert sorted((c.building_type, c.province) for c in kp.capabilities) == caps_before
    assert kp.source_id == db.scalar(select(DataSource.id).where(DataSource.url == KP_URL))
    list_ds = db.scalar(select(DataSource.id).where(DataSource.url == f"{SITE}/nha-thau/tiem-nang/"))
    # Thẻ danh sách không giành trường đang thuộc trang hồ sơ (cùng mức ưu tiên crawl_public)
    assert kp.field_sources["dezon_project_count"] != list_ds
    huy = db.scalar(select(Contractor).where(Contractor.slug == "huy-hoang"))
    assert huy.dezon_project_count == 6  # giá trị seed (nguồn hồ sơ) giữ nguyên dù thẻ danh sách ghi 7
    assert "dezon_project_count" in next(o for o in outs if o.slug == "huy-hoang").skipped_fields
    # Trường đang trống thì thẻ danh sách được điền
    new_cards = [o for o in outs if o.action == "created"]
    assert new_cards and all(o.entity_id for o in new_cards)
    # Trang hồ sơ Huy Hoàng (trang thật ghi 7 công trình) mới cập nhật giá trị
    huy_url = f"{SITE}/nha-thau/huy-hoang/"
    huy_html = fx("contractor_huy_hoang.html")
    ingest_page(db, [parsers.parse_contractor_profile(huy_html, huy_url)], url=huy_url,
                raw_hash=content_hash(huy_html))
    db.refresh(huy)
    assert huy.dezon_project_count == 7


# ================================================================== runner (DB + HTTP giả lập)
def test_run_crawl_end_to_end_with_mock_http(db):
    seen: list[httpx.Request] = []
    sleeps: list[float] = []
    routes = {
        f"{SITE}/robots.txt": (200, fx("robots.txt")),
        f"{SITE}/nha-thau/tiem-nang/": (200, fx("contractor_list_tiem_nang.html")),
        KP_URL: (200, fx("contractor_kien_phong.html")),
        WORK_URL: (200, fx("contractor_work_indochine_villa_quan_7.html")),
        f"{SITE}/du-an/": (200, fx("project_list.html")),
        PROJECT_URL: (200, fx("project_villa_vinh_cuu.html")),
    }

    def crawl(max_pages):
        f = _fetcher(routes, seen, sleeps)
        return run_crawl(db, max_pages, fetcher=f, site_url=SITE, list_paths=("/nha-thau/tiem-nang/",),
                         max_contractors=1, works_per_contractor=7, include_project_pages=True)

    s1 = crawl(max_pages=40)
    assert s1["pages"] == 30  # 1 danh sách + 1 hồ sơ + 7 công trình + 1 /du-an/ + 20 dự án (404 tính là trang)
    assert s1["created"] >= 2 and s1["updated"] >= 1
    assert s1["errors"] == 6 + 19  # các URL không có fixture trả 404
    assert db.scalar(select(Project).where(Project.project_url == WORK_URL)) is not None
    assert db.scalar(select(Project).where(Project.project_url == PROJECT_URL)) is not None
    assert all("DezonAIBot" in r.headers["user-agent"] for r in seen)
    assert all(s >= 2.0 for s in sleeps)

    s2 = crawl(max_pages=40)
    assert s2["created"] == 0 and s2["updated"] == 0 and s2["unchanged"] >= 41 + 1 + 2

    s3 = crawl(max_pages=3)  # giới hạn số trang được tôn trọng
    assert s3["pages"] == 3


# ================================================================== review fixes
def test_fetcher_robots_server_error_means_disallow():
    """RFC 9309: robots.txt trả 5xx = không truy cập được -> không crawl (trước đây bị coi như cho phép)."""
    seen, sleeps = [], []
    routes = {f"{SITE}/robots.txt": (503, "busy"), KP_URL: (200, "<html></html>")}
    f = _fetcher(routes, seen, sleeps)
    with pytest.raises(RobotsDisallowed):
        f.fetch(KP_URL)
    assert [str(r.url) for r in seen] == [f"{SITE}/robots.txt"]
    # 404 = không có robots.txt -> được phép
    seen2: list[httpx.Request] = []
    f2 = _fetcher({KP_URL: (200, "<html><main>x</main></html>")}, seen2, [])
    assert f2.fetch(KP_URL).ok


def test_run_crawl_survives_parser_error(db, monkeypatch):
    seen, sleeps = [], []
    routes = {
        f"{SITE}/robots.txt": (200, fx("robots.txt")),
        f"{SITE}/nha-thau/tiem-nang/": (200, fx("contractor_list_tiem_nang.html")),
        KP_URL: (200, fx("contractor_kien_phong.html")),
    }

    def boom(html, url):
        raise ValueError("HTML lạ")

    monkeypatch.setattr(parsers, "parse_contractor_profile", boom)
    stats = run_crawl(db, 5, fetcher=_fetcher(routes, seen, sleeps), site_url=SITE,
                      list_paths=("/nha-thau/tiem-nang/",), max_contractors=1, include_project_pages=False)
    assert stats["pages"] == 2 and stats["errors"] == 1  # trang hồ sơ lỗi parse được đếm, không làm dừng job
    assert stats["unchanged"] + stats["created"] + stats["updated"] == 41  # thẻ danh sách vẫn được ghi


def test_work_location_prefers_project_name_over_page_meta():
    """Dòng meta trang công trình ghi khu vực nhà thầu; địa danh trong tên công trình được ưu tiên."""
    from app.ingestion.crawler.parsers import apply_location_from_name

    rec = apply_location_from_name({"name": "France Indochine 22 Hà Nội", "province": "TP. Hồ Chí Minh",
                                    "district": None, "extra": {}})
    assert rec["province"] == "Hà Nội" and rec["extra"]["location_source"] == "project_name"
    rec = apply_location_from_name({"name": "Indochine Villa Quận 7 Sài gòn", "province": None, "extra": {}})
    assert (rec["province"], rec["district"]) == ("TP. Hồ Chí Minh", "Quận 7")
    rec = apply_location_from_name({"name": "The Opera Duplex", "province": "TP. Hồ Chí Minh", "extra": {}})
    assert rec["province"] == "TP. Hồ Chí Minh" and rec["extra"]["location_source"] == "page_meta"
