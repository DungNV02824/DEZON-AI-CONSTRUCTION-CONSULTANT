"""Review sửa lỗi #8 (đường CSV): đơn giá chính thức nhập từ file luôn thay thế đơn giá tổng hợp cùng khóa/ngày.

- Dòng tổng hợp chưa có khái toán dùng -> bị xóa; đã có khái toán dùng -> giữ nguyên giá, nguồn và ngày hiệu lực,
  chỉ nhả khóa (chuyển sang mức chất lượng không dùng) để select_rate/rebuild không bao giờ chọn lại.
- force=True không bao giờ ghi giá chính thức đè lên dòng tổng hợp; dòng đã có khái toán dùng không bị đổi giá.
"""
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models import ConstructionRate, Contractor, ContractorCapability, DataSource, EstimateSession
from app.ingestion.csv_import import import_table
from app.services import admin_service
from app.services import estimate_service as svc
from app.services.market_rates import RETIRED_TIERS, is_market_label, rebuild_market_rates

DN = "Đà Nẵng"
M = 1_000_000
HEADER = "building_type,province,work_scope,quality_tier,price_min,price_max,effective_from\n"


def _csv(*lines: str) -> bytes:
    return ("﻿" + HEADER + "".join(f"{ln}\n" for ln in lines)).encode("utf-8")


def _contractor(db, slug, rough):
    src = DataSource(source_type="crawl_public", url=f"https://dezon.vn/nha-thau/{slug}/", label=f"Hồ sơ {slug}",
                     status="on_dezon", source_updated_at=datetime(2026, 10, 1, tzinfo=UTC))
    db.add(src)
    db.flush()
    c = Contractor(slug=slug, name=slug, province=DN, profile_url=src.url, source_id=src.id,
                   rough_price_min=rough[0], rough_price_max=rough[1])
    db.add(c)
    db.flush()
    db.add(ContractorCapability(contractor_id=c.id, building_type="nha_pho", province=DN))
    db.flush()


@pytest.fixture
def dn_market(db):
    """3 nhà thầu nhà phố Đà Nẵng có đơn giá xây thô -> dựng được dòng tổng hợp Đà Nẵng (và toàn quốc)."""
    for i, rng in enumerate([(3 * M, 3_600_000), (3_200_000, 3_800_000), (3_500_000, 4_500_000)]):
        _contractor(db, f"csvfix-{i}", rng)
    rebuild_market_rates(db)
    return db


def _row(db, province=DN, tier="trung_binh", day=None):
    return db.scalar(select(ConstructionRate).where(
        ConstructionRate.building_type == "nha_pho", ConstructionRate.province == province,
        ConstructionRate.work_scope == "xay_tho", ConstructionRate.quality_tier == tier,
        ConstructionRate.effective_from == (day or date.today())))


def _use_rate(db, rate_id):
    db.add(EstimateSession(id=uuid.uuid4(), input={"building_type": "nha_pho"}, rate_id=rate_id, rules_version=1,
                           converted_area_m2=Decimal(100), assumptions={}))
    db.flush()


def _snapshot(row):
    return (int(row.price_min), int(row.price_max), row.source_id, row.source.label, row.effective_from,
            row.effective_to)


def _official_csv():
    today = date.today().isoformat()
    return _csv(f"nha_pho,{DN},xay_tho,trung_binh,5000000,6000000,{today}",
                f"nha_pho,*,xay_tho,trung_binh,5500000,6500000,{today}")


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("used", [False, True])
def test_csv_official_rate_same_day_replaces_market_row(dn_market, used, force):
    db = dn_market
    market = _row(db)
    nationwide = _row(db, "*")
    assert is_market_label(market.source.label) and is_market_label(nationwide.source.label)
    assert svc.select_rate(db, "nha_pho", DN, "xay_tho").id == market.id  # trước khi nhập: dòng tổng hợp
    market_id, nationwide_id = market.id, nationwide.id
    before = _snapshot(market)
    if used:
        _use_rate(db, market_id)
        _use_rate(db, nationwide_id)

    report = import_table(db, _official_csv(), "bang_gia_dezon.csv", "rates", force=force)

    assert (report.inserted, report.skipped, report.updated, report.replaced_market) == (2, 0, 0, 2), report.errors
    assert report.status == "ok" and report.as_dict()["replaced_market"] == 2
    official = _row(db)
    assert official.id != market_id and (int(official.price_min), int(official.price_max)) == (5 * M, 6 * M)
    assert official.source.source_type == "csv_import" and not is_market_label(official.source.label)

    old = db.get(ConstructionRate, market_id)
    if used:
        # không ghi đè giá/nguồn/ngày hiệu lực của dòng khái toán đã dùng — chỉ nhả khóa sang mức không dùng
        db.refresh(old)
        assert _snapshot(old) == before
        assert old.quality_tier in RETIRED_TIERS
    else:
        assert old is None

    rate = svc.select_rate(db, "nha_pho", DN, "xay_tho")
    assert (rate.id, rate.price_min, rate.price_max, rate.is_fallback) == (official.id, 5 * M, 6 * M, False)
    other = svc.select_rate(db, "nha_pho", "Kon Tum", "xay_tho")  # tỉnh không có dòng riêng -> toàn quốc chính thức
    assert (other.price_min, other.price_max, other.is_fallback) == (5_500_000, 6_500_000, True)

    # Dựng lại trong ngày: không giành lại khóa, không ghi cửa sổ hiệu lực giả cho dòng đã dùng
    stats = rebuild_market_rates(db)
    assert stats["skipped_official"] >= 2
    assert svc.select_rate(db, "nha_pho", DN, "xay_tho").id == official.id
    if used:
        db.refresh(old)
        assert (int(old.price_min), int(old.price_max), old.effective_from) == before[:2] + (before[4],)
        assert old.effective_to is None or old.effective_to >= old.effective_from


def test_csv_dry_run_reports_market_replacement_without_writing(dn_market):
    db = dn_market
    market = _row(db)
    before = _snapshot(market)
    report = import_table(db, _official_csv(), "xem_truoc.csv", "rates", dry_run=True)
    assert (report.inserted, report.skipped, report.replaced_market) == (2, 0, 2)
    db.refresh(market)
    assert _snapshot(market) == before and market.quality_tier == "trung_binh"
    assert svc.select_rate(db, "nha_pho", DN, "xay_tho").id == market.id


def test_csv_force_never_rewrites_price_of_rate_used_by_estimate(dn_market):
    db = dn_market
    today = date.today().isoformat()
    import_table(db, _csv(f"nha_pho,{DN},xay_tho,trung_binh,5000000,6000000,{today}"), "v1.csv", "rates")
    official = _row(db)
    _use_rate(db, official.id)
    changed = _csv(f"nha_pho,{DN},xay_tho,trung_binh,7000000,8000000,{today}")
    for force in (False, True):
        report = import_table(db, changed, f"v2_{force}.csv", "rates", force=force)
        assert report.skipped == 1 and report.updated == 0, report.errors
        assert "khái toán" in report.errors[0].message
        db.refresh(official)
        assert (int(official.price_min), int(official.price_max)) == (5 * M, 6 * M)


def test_csv_reimport_unused_official_row_still_updates(dn_market):
    db = dn_market
    today = date.today().isoformat()
    import_table(db, _csv(f"nha_pho,{DN},xay_tho,trung_binh,5000000,6000000,{today}"), "v1.csv", "rates")
    report = import_table(db, _csv(f"nha_pho,{DN},xay_tho,trung_binh,5200000,6000000,{today}"), "v2.csv", "rates")
    assert report.updated == 1 and report.replaced_market == 0
    assert int(_row(db).price_min) == 5_200_000


def test_admin_official_rate_retires_used_market_row_without_moving_dates(dn_market):
    db = dn_market
    market = _row(db)
    before = _snapshot(market)
    _use_rate(db, market.id)
    out = admin_service.create_rate(db, admin_service.RateIn(
        building_type="nha_pho", province=DN, work_scope="xay_tho", price_min=5 * M, price_max=6 * M,
        effective_from=date.today(), approved=True))
    db.refresh(market)
    assert _snapshot(market) == before and market.quality_tier in RETIRED_TIERS
    assert svc.select_rate(db, "nha_pho", DN, "xay_tho").id == out.id
    # admin tạo thêm đơn giá chính thức ở đúng mức mà dòng tổng hợp đang "đỗ" -> dòng đó nhả tiếp sang mức khác
    parked = str(market.quality_tier)
    admin_service.create_rate(db, admin_service.RateIn(
        building_type="nha_pho", province=DN, work_scope="xay_tho", quality_tier=parked, price_min=5 * M,
        price_max=6 * M, effective_from=date.today()))
    db.refresh(market)
    assert market.quality_tier in RETIRED_TIERS and market.quality_tier != parked
    assert _snapshot(market) == before


def test_expire_old_never_gives_used_row_an_empty_window(dn_market):
    """Dòng tổng hợp hôm nay đã được khái toán dùng rồi hết nhóm (nhà thầu đổi giá) -> hết hiệu lực từ hôm nay,
    không ghi effective_to = hôm qua (trước cả ngày hiệu lực)."""
    db = dn_market
    market = _row(db)
    _use_rate(db, market.id)
    for c in db.scalars(select(Contractor).where(Contractor.slug.startswith("csvfix-"))):
        c.rough_price_min = c.rough_price_max = None
    db.flush()
    rebuild_market_rates(db)
    db.refresh(market)
    assert market.effective_from == date.today() and market.effective_to == date.today()
    # dòng chưa dùng vẫn bị ẩn ngay
    nationwide = _row(db, "*")
    assert nationwide.effective_to == date.today() - timedelta(days=1)
