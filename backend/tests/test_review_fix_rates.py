"""Review sửa lỗi — nhóm rates: đơn giá tổng hợp, snapshot/crawl, fixture seed, câu chữ khái toán.

Phát hiện #2, #4, #5, #6, #7, #8, #9, #10, #23, #24, #29.
"""
import importlib.util
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models import (
    ConstructionRate,
    Contractor,
    ContractorCapability,
    DataSource,
    EstimateSession,
    Project,
    ProjectImage,
)
from app.engines.estimate import build_disclaimer, calculate_estimate
from app.engines.estimate.explain import NO_RATE_DISCLAIMER
from app.evidence import SourceRef, VerificationStatus
from app.ingestion.upsert import ingest_page
from app.schemas.estimate import EstimateInput, RateData
from app.services import admin_service
from app.services import estimate_service as svc
from app.services.market_rates import (
    MARKET_LABEL_PREFIX,
    RETIRED_TIERS,
    SEED_DEV_LABEL_PREFIX,
    displace_market_rate,
    rebuild_market_rates,
)
from tests.test_estimate_engine import make_rules

BACKEND = Path(__file__).resolve().parent.parent
DN = "Đà Nẵng"
HCM = "TP. Hồ Chí Minh"
M = 1_000_000


# ------------------------------------------------------------------ tiện ích
def _contractor(db, slug, *, province=DN, rough=(None, None), finish=(None, None)):
    src = DataSource(source_type="crawl_public", url=f"https://dezon.vn/nha-thau/{slug}/", label=f"Hồ sơ {slug}",
                     status="on_dezon", source_updated_at=datetime(2026, 10, 1, tzinfo=UTC))
    db.add(src)
    db.flush()
    c = Contractor(slug=slug, name=slug, province=province, profile_url=src.url, source_id=src.id,
                   rough_price_min=rough[0], rough_price_max=rough[1],
                   finish_price_min=finish[0], finish_price_max=finish[1])
    db.add(c)
    db.flush()
    db.add(ContractorCapability(contractor_id=c.id, building_type="nha_pho", province=province))
    db.flush()
    return c


@pytest.fixture
def dn_market(db):
    """3 nhà thầu nhà phố Đà Nẵng có đơn giá xây thô -> đủ dựng dòng tổng hợp Đà Nẵng."""
    for i, rng in enumerate([(3 * M, 3_600_000), (3_200_000, 3_800_000), (3_500_000, 4_500_000)]):
        _contractor(db, f"rf-{i}", rough=rng)
    return db


def _dn_row(db, day=None):
    day = day or date.today()
    return db.scalar(select(ConstructionRate).where(
        ConstructionRate.building_type == "nha_pho", ConstructionRate.province == DN,
        ConstructionRate.work_scope == "xay_tho", ConstructionRate.effective_from == day))


def _use_rate(db, rate_id):
    """Một khái toán đã lưu tham chiếu dòng đơn giá."""
    db.add(EstimateSession(id=uuid.uuid4(), input={"building_type": "nha_pho"}, rate_id=rate_id, rules_version=1,
                           converted_area_m2=Decimal(100), assumptions={}))
    db.flush()


# ------------------------------------------------------------------ #9: không sửa dòng đã được khái toán dùng
def test_same_day_rebuild_keeps_rate_row_used_by_estimate(dn_market):
    db = dn_market
    rebuild_market_rates(db)
    row = _dn_row(db)
    before = (int(row.price_min), int(row.price_max), row.source.label)
    _use_rate(db, row.id)
    _contractor(db, "rf-x", rough=(6 * M, 7 * M))  # số liệu tổng hợp đổi trong ngày
    stats = rebuild_market_rates(db)
    db.refresh(row)
    assert (int(row.price_min), int(row.price_max), row.source.label) == before
    assert row.effective_to is None and stats["kept_in_use"] >= 1
    # select_rate vẫn trả đúng dòng (không bị _expire_old hết hiệu lực)
    assert svc.select_rate(db, "nha_pho", DN, "xay_tho").id == row.id


def test_same_day_rebuild_updates_unused_row_in_place(dn_market):
    db = dn_market
    rebuild_market_rates(db)
    row = _dn_row(db)
    _contractor(db, "rf-x", rough=(6 * M, 7 * M))
    rebuild_market_rates(db)
    db.refresh(row)
    assert row.source.label.startswith(MARKET_LABEL_PREFIX + "xây thô do 4 nhà thầu")


# ------------------------------------------------------------------ #24: giữ duyệt khi số liệu không đổi
def test_approval_survives_same_day_rebuild_and_next_day_when_price_unchanged(dn_market):
    db = dn_market
    d1 = date.today() - timedelta(days=1)
    rebuild_market_rates(db, today=d1)
    row1 = _dn_row(db, d1)
    admin_service.approve_rate(db, row1.id, "duyet-vien")
    rebuild_market_rates(db, today=d1)  # chạy lại trong ngày, giá không đổi
    db.refresh(row1)
    assert row1.source.status == "verified" and row1.source.verified_by == "duyet-vien"

    rebuild_market_rates(db)  # ngày mới, giá không đổi -> dòng mới giữ duyệt
    row2 = _dn_row(db)
    assert row2.id != row1.id and row2.source.status == "verified"
    assert row2.source.verified_by == "duyet-vien" and row2.source.verified_at is not None
    assert svc.select_rate(db, "nha_pho", DN, "xay_tho").status == "verified"


def test_approval_reset_when_price_changes(dn_market):
    db = dn_market
    rebuild_market_rates(db)
    row = _dn_row(db)
    admin_service.approve_rate(db, row.id, "duyet-vien")
    _contractor(db, "rf-x", rough=(6 * M, 7 * M))
    rebuild_market_rates(db)
    db.refresh(row)
    assert row.source.status == "on_dezon" and row.source.verified_by is None and row.source.verified_at is None


# ------------------------------------------------------------------ #8: đơn giá chính thức luôn thắng, kể cả cùng ngày
@pytest.mark.parametrize("used", [False, True])
def test_admin_official_rate_same_day_displaces_market_row(dn_market, used):
    db = dn_market
    rebuild_market_rates(db)
    market = _dn_row(db)
    market_id, market_prices = market.id, (int(market.price_min), int(market.price_max))
    if used:
        _use_rate(db, market_id)
    out = admin_service.create_rate(db, admin_service.RateIn(
        building_type="nha_pho", province=DN, work_scope="xay_tho", price_min=5 * M, price_max=6 * M,
        effective_from=date.today(), approved=True))
    assert out.price_min == 5 * M and out.approved
    old = db.get(ConstructionRate, market_id)
    if used:  # giữ nguyên giá/nguồn/khung hiệu lực; chỉ chuyển sang mức chất lượng không dùng để nhả khóa
        assert (int(old.price_min), int(old.price_max)) == market_prices
        assert old.effective_from == date.today() and old.effective_to is None
        assert old.quality_tier in RETIRED_TIERS
    else:
        assert old is None
    rate = svc.select_rate(db, "nha_pho", DN, "xay_tho")
    assert (rate.id, rate.price_min, rate.status) == (out.id, 5 * M, "verified")
    # Dựng lại trong ngày không giành lại khóa
    stats = rebuild_market_rates(db)
    assert stats["skipped_official"] >= 1
    assert svc.select_rate(db, "nha_pho", DN, "xay_tho").id == out.id


def test_admin_official_rate_still_conflicts_with_official_row(dn_market):
    db = dn_market
    data = admin_service.RateIn(building_type="nha_pho", province=DN, work_scope="xay_tho", price_min=5 * M,
                                price_max=6 * M, effective_from=date.today())
    admin_service.create_rate(db, data)
    with pytest.raises(admin_service.AdminError) as exc:
        admin_service.create_rate(db, data)
    assert exc.value.status_code == 409


def test_displace_refuses_official_row(db):
    src = DataSource(source_type="csv_import", label="Bảng giá Dezon (test)", status="verified")
    db.add(src)
    db.flush()
    row = ConstructionRate(building_type="nha_pho", province=DN, work_scope="xay_tho", quality_tier="trung_binh",
                           price_min=5 * M, price_max=6 * M, effective_from=date.today(), source_id=src.id)
    db.add(row)
    db.flush()
    with pytest.raises(ValueError):
        displace_market_rate(db, row)


# ------------------------------------------------------------------ #10: quận/huyện khớp đúng tỉnh
@pytest.mark.parametrize("place,expected", [("Thủ Đức", HCM), ("Quận 7", HCM), ("TP Thủ Đức, HCM", HCM),
                                            ("Cầu Giấy", "Hà Nội"), ("Tỉnh Bà Rịa - Vũng Tàu", None)])
def test_select_rate_canonicalizes_district_to_province(db, place, expected):
    rate = svc.select_rate(db, "nha_pho", place, "xay_tho")
    if expected is None:  # fixture không có dòng riêng cho tỉnh này -> toàn quốc
        assert rate.is_fallback and rate.province == "*"
    else:
        assert not rate.is_fallback and rate.province == expected


def test_normalize_province_uses_parse_vi_canonical_names():
    assert svc.normalize_province("Thủ Đức") == svc.normalize_province("TP. Hồ Chí Minh") == "ho chi minh"
    assert svc.normalize_province("Bà Rịa Vũng Tàu") == svc.normalize_province("Bà Rịa - Vũng Tàu")
    assert svc.normalize_province("Huế") == svc.normalize_province("Thừa Thiên Huế")
    assert svc.normalize_province("*") == "*"


# ------------------------------------------------------------------ #7: đơn giá seed dev không bao giờ tới tay khách
def test_select_rate_ignores_seed_dev_rows_outside_tests(db, monkeypatch):
    assert svc.select_rate(db, "nha_pho", HCM, "hoan_thien") is not None  # test bật fixture
    monkeypatch.delenv("ALLOW_SEED_DEV_RATES", raising=False)
    assert svc.select_rate(db, "nha_pho", HCM, "hoan_thien") is None
    assert svc.select_rate(db, "nha_pho", HCM, "xay_tho") is None


def _migration_ddl(name: str) -> str:
    path = BACKEND / "app/db/migrations/versions" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "0002" and mod.down_revision == "0001"
    return mod.DDL


def test_migration_0002_retires_seed_dev_rates_and_fixture_data(db):
    seed_rows = db.scalars(select(ConstructionRate).join(DataSource).where(
        DataSource.label.startswith(SEED_DEV_LABEL_PREFIX), ConstructionRate.effective_to.is_(None))).all()
    fi = db.scalar(select(Project).where(Project.slug == "kien-phong--france-indochine-22-ha-noi"))
    assert seed_rows and fi.building_type == "biet_thu"
    assert db.scalar(select(ProjectImage.id).where(ProjectImage.url.startswith("https://placehold.co/")))

    db.connection().exec_driver_sql(_migration_ddl("0002_retire_seed_dev_rates.py"))
    db.expire_all()
    yesterday = date.today() - timedelta(days=1)
    assert all(db.get(ConstructionRate, r.id).effective_to == yesterday for r in seed_rows)
    assert db.get(Project, fi.id).building_type is None
    assert db.scalar(select(ProjectImage.id).where(ProjectImage.url.startswith("https://placehold.co/"))) is None
    assert db.scalar(select(Contractor.id).where(Contractor.avatar_url.startswith("https://placehold.co/"))) is None


# ------------------------------------------------------------------ #5: seed.run() thật không nạp fixture
def test_seed_run_does_not_insert_fixtures_by_default(db, monkeypatch):
    from app.db import seed

    calls = []
    monkeypatch.setattr(seed, "seed_fixtures", lambda s: calls.append("fixtures"))
    monkeypatch.setattr(seed, "EXTRA_SEED_HOOKS", [])
    monkeypatch.delenv("SEED_FIXTURES", raising=False)
    seed.run(db)
    assert calls == []
    seed.run(db, fixtures=True)
    assert calls == ["fixtures"]
    monkeypatch.setenv("SEED_FIXTURES", "1")
    seed.run(db)
    assert calls == ["fixtures", "fixtures"]


# ------------------------------------------------------------------ #4: trang không đổi nhưng bản ghi parse đổi
KP_URL = "https://dezon.vn/nha-thau/rf-reparse/"


def _rec(**kw):
    rec = {"kind": "contractor", "slug": "rf-reparse", "external_id": "rf-reparse", "name": "RF Reparse",
           "profile_url": KP_URL, "province": DN, "rough_price_min": 2_000_000, "rough_price_max": 2_000_000}
    rec.update(kw)
    return rec


def test_reparsed_record_applies_even_when_page_hash_unchanged(db):
    [out] = ingest_page(db, [_rec()], url=KP_URL, raw_hash="page-1")
    assert out.action == "created"
    # bộ làm sạch mới bỏ giá rác: cùng hash trang, bản ghi parse khác -> vẫn cập nhật
    [out2] = ingest_page(db, [_rec(rough_price_min=None, rough_price_max=None)], url=KP_URL, raw_hash="page-1")
    assert out2.action == "updated"
    c = db.scalar(select(Contractor).where(Contractor.slug == "rf-reparse"))
    assert c.rough_price_min is None and c.rough_price_max is None
    # lần sau y hệt -> unchanged, không ghi
    c.description = "sửa tay"
    db.flush()
    [out3] = ingest_page(db, [_rec(rough_price_min=None, rough_price_max=None)], url=KP_URL, raw_hash="page-1")
    assert out3.action == "unchanged" and c.description == "sửa tay"


def test_legacy_page_hash_keeps_verification_when_parse_gives_same_data(db):
    ingest_page(db, [_rec()], url=KP_URL, raw_hash="page-1")
    ds = db.scalar(select(DataSource).where(DataSource.url == KP_URL))
    ds.raw_hash = "page-1"  # khóa kiểu cũ (chỉ hash trang)
    ds.status, ds.verified_by, ds.verified_at = "verified", "admin", datetime.now(UTC)
    db.flush()
    [out] = ingest_page(db, [_rec()], url=KP_URL, raw_hash="page-1")
    assert out.action == "unchanged"
    assert ds.status == "verified" and ds.verified_by == "admin" and ds.raw_hash.startswith("page-1:")
    # dữ liệu thật sự đổi -> bỏ xác minh
    ds.raw_hash = "page-1"
    db.flush()
    [out2] = ingest_page(db, [_rec(rough_price_max=2_500_000)], url=KP_URL, raw_hash="page-1")
    assert out2.action == "updated" and ds.status == "on_dezon" and ds.verified_by is None


# ------------------------------------------------------------------ #2: snapshot cũ không đè dữ liệu crawl mới hơn
def _write_snapshot(tmp_path, *, price, fetched_at, raw_hash="snap-hash"):
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"contractors": [{
        **_rec(rough_price_min=price, rough_price_max=price),
        "_source": {"url": KP_URL, "raw_hash": raw_hash, "fetched_at": fetched_at.isoformat()}}],
        "projects": []}, ensure_ascii=False), encoding="utf-8")
    return snap


def test_snapshot_does_not_overwrite_newer_crawl(db, tmp_path):
    from app.ingestion.seed_snapshot import load_snapshot

    snap_time = datetime(2026, 11, 2, 9, tzinfo=UTC)
    snap = _write_snapshot(tmp_path, price=4_000_000, fetched_at=snap_time)
    assert load_snapshot(db, snap)["created"] == 1
    # crawl sau snapshot ghi giá mới
    ingest_page(db, [_rec(rough_price_min=5_000_000, rough_price_max=5_000_000)], url=KP_URL,
                raw_hash="crawl-hash", fetched_at=snap_time + timedelta(days=1))
    res = load_snapshot(db, snap)  # khởi động lại container
    assert res["newer_in_db"] == 1 and res["updated"] == 0
    c = db.scalar(select(Contractor).where(Contractor.slug == "rf-reparse"))
    assert c.rough_price_min == 5_000_000
    ds = db.scalar(select(DataSource).where(DataSource.url == KP_URL))
    assert ds.fetched_at == snap_time + timedelta(days=1)


def test_snapshot_same_time_reapplies_new_parse(db, tmp_path):
    """DB nạp từ snapshot cũ (chưa crawl): snapshot mới cùng hash trang/thời điểm nhưng giá đã làm sạch -> áp dụng."""
    from app.ingestion.seed_snapshot import load_snapshot

    snap_time = datetime(2026, 11, 2, 9, tzinfo=UTC)
    load_snapshot(db, _write_snapshot(tmp_path, price=2_000_000, fetched_at=snap_time))
    res = load_snapshot(db, _write_snapshot(tmp_path, price=None, fetched_at=snap_time))
    assert res["updated"] == 1 and res["newer_in_db"] == 0
    c = db.scalar(select(Contractor).where(Contractor.slug == "rf-reparse"))
    assert c.rough_price_min is None


def test_seed_hook_skips_rebuild_when_snapshot_unchanged(dn_market, tmp_path, monkeypatch):
    from app.ingestion import seed_snapshot
    from app.services import market_rates

    db = dn_market

    snap = _write_snapshot(tmp_path, price=4_000_000, fetched_at=datetime(2026, 11, 2, 9, tzinfo=UTC))
    monkeypatch.setattr(seed_snapshot, "SNAPSHOT_PATH", snap)
    monkeypatch.setenv("SEED_SNAPSHOT", "1")
    calls = []
    real = market_rates.rebuild_market_rates
    monkeypatch.setattr(market_rates, "rebuild_market_rates", lambda s: calls.append(1) or real(s))
    seed_snapshot.seed_hook(db)  # tạo mới -> dựng đơn giá
    assert calls == [1]
    assert seed_snapshot._has_active_market_rates(db)
    seed_snapshot.seed_hook(db)  # không đổi gì và đã có dòng tổng hợp -> không dựng lại
    assert calls == [1]


# ------------------------------------------------------------------ #6/#23: câu giả định fallback nêu đúng nguồn
def _rate(label):
    return RateData(id=1, building_type="nha_pho", province="*", work_scope="xay_tho", quality_tier="trung_binh",
                    price_min=4 * M, price_max=5 * M, effective_from=date(2026, 1, 1), is_fallback=True,
                    status=VerificationStatus.verified, source=SourceRef(label=label))


def _inp(**kw):
    base = dict(building_type="nha_pho", province=DN, land_area_m2=100, floors=3, work_scope="xay_tho")
    base.update(kw)
    return EstimateInput(**base)


def test_fallback_assumption_for_official_nationwide_rate_names_its_source():
    res = calculate_estimate(_inp(), make_rules(), _rate("Bảng giá Dezon 2026"))
    [a] = [x for x in res.assumptions if x.startswith("Dùng đơn giá mặc định toàn quốc")]
    assert a == "Dùng đơn giá mặc định toàn quốc (nguồn: Bảng giá Dezon 2026) vì chưa có đơn giá riêng tại Đà Nẵng"
    assert "nhà thầu" not in a


def test_fallback_assumption_for_market_rate_keeps_contractor_wording():
    label = MARKET_LABEL_PREFIX + "xây thô do 5 nhà thầu nhà phố trên toàn quốc công bố trên Dezon"
    res = calculate_estimate(_inp(), make_rules(), _rate(label))
    assert ("Dùng đơn giá mặc định toàn quốc (tổng hợp đơn giá do các nhà thầu trên Dezon công bố) vì chưa đủ "
            "dữ liệu đơn giá nhà thầu tại Đà Nẵng (cần ít nhất 3 hồ sơ nhà thầu có công bố giá)") in res.assumptions


# ------------------------------------------------------------------ #29: không có đơn giá -> không nói "dựa trên đơn giá"
@pytest.mark.parametrize("scope", ["hoan_thien", "noi_that", "xay_tho"])
def test_no_rate_disclaimer_does_not_claim_reference_price(scope):
    res = calculate_estimate(_inp(work_scope=scope), make_rules(), None)
    assert res.disclaimer == NO_RATE_DISCLAIMER
    assert "dựa trên đơn giá tham chiếu" not in res.disclaimer and "chưa tính được chi phí" in res.disclaimer
    assert build_disclaimer(None, has_rate=False) == NO_RATE_DISCLAIMER
    # Có đơn giá nhưng thiếu nguồn: giữ mẫu cũ
    assert build_disclaimer(None).startswith("Đây là khái toán sơ bộ dựa trên đơn giá tham chiếu (Chưa có dữ liệu)")
