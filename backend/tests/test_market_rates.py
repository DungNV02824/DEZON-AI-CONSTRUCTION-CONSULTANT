"""Đơn giá tổng hợp từ đơn giá nhà thầu công bố trên Dezon (engine thuần + service với DB)."""
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models import (
    ConstructionRate,
    Contractor,
    ContractorCapability,
    DataSource,
    Project,
    ProjectContractor,
)
from app.engines.estimate.market import (
    MIN_CONTRACTORS_FOR_RATE,
    aggregate_price_range,
    contractor_bounds,
    percentile,
)
from app.services import estimate_service as svc
from app.services.market_rates import (
    MARKET_LABEL_PREFIX,
    SEED_DEV_LABEL_PREFIX,
    is_market_label,
    market_label,
    rebuild_market_rates,
)

DN = "Đà Nẵng"
M = 1_000_000


# --- Engine thuần -------------------------------------------------------------------------------

def test_min_contractors_constant():
    assert MIN_CONTRACTORS_FOR_RATE == 3


def test_contractor_bounds_uses_other_bound_when_missing():
    assert contractor_bounds(None, 4 * M) == (4 * M, 4 * M)
    assert contractor_bounds(3 * M, None) == (3 * M, 3 * M)
    assert contractor_bounds(5 * M, 4 * M) == (4 * M, 5 * M)
    assert contractor_bounds(None, None) is None
    assert contractor_bounds(0, None) is None


def test_percentile_linear_interpolation():
    assert percentile([1, 2, 3, 4], 0.25) == Decimal("1.75")
    assert percentile([1, 2, 3, 4], 0.75) == Decimal("3.25")
    assert percentile([7], 0.25) == 7
    with pytest.raises(ValueError):
        percentile([], 0.5)


def test_aggregate_needs_min_contractors():
    assert aggregate_price_range([(3 * M, 4 * M), (3.5 * M, 4.5 * M)]) is None
    # khoảng không có giá không được đếm
    assert aggregate_price_range([(3 * M, 4 * M), (3.5 * M, 4.5 * M), (None, None)]) is None
    assert aggregate_price_range([(3 * M, 4 * M), (3.5 * M, 4.5 * M)], min_n=2) is not None
    assert aggregate_price_range([]) is None


def test_aggregate_p25_lowers_p75_uppers_rounded_50k():
    ranges = [(3_000_000, 3_600_000), (None, 4_000_000), (3_500_000, 4_500_000), (3_200_000, 3_800_000)]
    # cận dưới [3,0; 3,2; 3,5; 4,0] -> P25 = 3,15 ; cận trên [3,6; 3,8; 4,0; 4,5] -> P75 = 4,125 -> 4,15
    assert aggregate_price_range(ranges) == (3_150_000, 4_150_000)
    assert aggregate_price_range([(Decimal(3_010_000), Decimal(3_990_000))] * 3) == (3_000_000, 4_000_000)


def test_market_label_text():
    assert market_label("xay_tho", 32, "nha_pho", "TP. Hồ Chí Minh") == (
        "Tổng hợp đơn giá xây thô do 32 nhà thầu nhà phố tại TP. Hồ Chí Minh công bố trên Dezon")
    lbl = market_label("tron_goi", 5, "biet_thu", "*")
    assert lbl.startswith(MARKET_LABEL_PREFIX + "trọn gói") and "trên toàn quốc" in lbl
    assert is_market_label(lbl) and not is_market_label("Bảng giá Dezon") and not is_market_label(None)


# --- Service với DB -----------------------------------------------------------------------------

def _contractor(db, slug, *, province=None, caps=(("nha_pho", None),), rough=(None, None), finish=(None, None),
                active=True, updated=None, project_province=None):
    src = DataSource(source_type="crawl_public", url=f"https://dezon.vn/nha-thau/{slug}/", label=f"Hồ sơ {slug}",
                     status="on_dezon", source_updated_at=updated)
    db.add(src)
    db.flush()
    c = Contractor(slug=slug, name=slug, province=province, profile_url=src.url, source_id=src.id,
                   is_active=active, rough_price_min=rough[0], rough_price_max=rough[1],
                   finish_price_min=finish[0], finish_price_max=finish[1])
    db.add(c)
    db.flush()
    for bt, prov in caps:
        db.add(ContractorCapability(contractor_id=c.id, building_type=bt, province=prov))
    if project_province:
        p = Project(slug=f"{slug}--p", name=f"Công trình {slug}", building_type="nha_pho",
                    province=project_province, project_url=f"{src.url}p/")
        db.add(p)
        db.flush()
        db.add(ProjectContractor(project_id=p.id, contractor_id=c.id, role="thi_cong"))
    db.flush()
    return c


@pytest.fixture
def market_db(db):
    d = lambda m, dd: datetime(2026, m, dd, tzinfo=UTC)  # noqa: E731
    _contractor(db, "t-a", province=DN, rough=(3_000_000, 3_600_000), finish=(6_000_000, 7_000_000),
                updated=d(9, 1))
    _contractor(db, "t-b", province="TP Đà Nẵng", rough=(None, 4_000_000), finish=(None, 8_000_000),
                updated=d(10, 15))
    _contractor(db, "t-c", caps=(("nha_pho", DN),), rough=(3_500_000, 4_500_000), updated=d(8, 1))
    # Không có tỉnh trên hồ sơ/năng lực -> lấy tỉnh công trình
    _contractor(db, "t-d", rough=(3_200_000, 3_800_000), project_province=DN, updated=d(7, 1))
    # Ngừng hoạt động -> không tính
    _contractor(db, "t-e", province=DN, rough=(9_000_000, 9_500_000), active=False, updated=d(10, 30))
    # Không có năng lực nhà phố -> không thuộc nhóm nhà phố
    _contractor(db, "t-f", province=DN, caps=(("khach_san", DN),), rough=(8_000_000, 9_000_000))
    return db


def _active(db, bt, prov, scope):
    today = date.today()
    return db.scalars(select(ConstructionRate).where(
        ConstructionRate.building_type == bt, ConstructionRate.province == prov,
        ConstructionRate.work_scope == scope, ConstructionRate.effective_from <= today,
        (ConstructionRate.effective_to.is_(None)) | (ConstructionRate.effective_to >= today))).all()


def test_rebuild_creates_province_and_nationwide_rows(market_db):
    db = market_db
    stats = rebuild_market_rates(db)
    assert stats["status"] == "ok" and stats["created"] >= 3

    [dn] = _active(db, "nha_pho", DN, "xay_tho")
    assert (int(dn.price_min), int(dn.price_max)) == (3_150_000, 4_150_000)
    assert dn.quality_tier == "trung_binh" and dn.effective_from == date.today() and dn.effective_to is None
    src = db.get(DataSource, dn.source_id)
    assert src.label == "Tổng hợp đơn giá xây thô do 4 nhà thầu nhà phố tại Đà Nẵng công bố trên Dezon"
    assert (src.source_type, src.status) == ("crawl_public", "on_dezon")
    assert src.url == f"https://dezon.vn/nha-thau/tiem-nang/#don-gia-nha_pho-xay_tho-da-nang-{date.today():%Y%m%d}"
    assert src.source_updated_at.date() == date(2026, 10, 15)  # mới nhất trong 4 nhà thầu

    # Trọn gói Đà Nẵng: chỉ 2 nhà thầu có giá -> không có dòng
    assert _active(db, "nha_pho", DN, "tron_goi") == []
    # TP HCM: chỉ Kiến Phong có giá -> không có dòng tổng hợp; đơn giá seed dev đã hết hiệu lực
    assert _active(db, "nha_pho", "TP. Hồ Chí Minh", "xay_tho") == []
    # Khách sạn: chỉ 1 nhà thầu -> không có
    assert _active(db, "khach_san", DN, "xay_tho") == []
    # Không có dữ liệu thật cho hoàn thiện / nội thất
    assert not any(r["work_scope"] in ("hoan_thien", "noi_that") for r in stats["rates"])

    # Toàn quốc nhà phố xây thô: Kiến Phong (4,3–4,7) + 4 nhà thầu Đà Nẵng
    [nat] = _active(db, "nha_pho", "*", "xay_tho")
    assert (int(nat.price_min), int(nat.price_max)) == (3_200_000, 4_500_000)
    assert db.get(DataSource, nat.source_id).label == (
        "Tổng hợp đơn giá xây thô do 5 nhà thầu nhà phố trên toàn quốc công bố trên Dezon")
    # Toàn quốc trọn gói: Kiến Phong (7,3–9,5), t-a (6–7), t-b (≤ 8) -> 3 nhà thầu
    [nat_tg] = _active(db, "nha_pho", "*", "tron_goi")
    assert (int(nat_tg.price_min), int(nat_tg.price_max)) == (6_650_000, 8_750_000)


def test_rebuild_expires_seed_dev_and_previous_market_rows(market_db):
    db = market_db
    today = date.today()
    old_src = DataSource(source_type="crawl_public", label=MARKET_LABEL_PREFIX + "xây thô cũ", status="on_dezon")
    db.add(old_src)
    db.flush()
    old = ConstructionRate(building_type="biet_thu", province=DN, work_scope="xay_tho", quality_tier="trung_binh",
                           price_min=1, price_max=2, effective_from=today - timedelta(days=30), source_id=old_src.id)
    db.add(old)
    db.flush()
    seed_rows = db.scalars(select(ConstructionRate).join(DataSource).where(
        DataSource.label.startswith(SEED_DEV_LABEL_PREFIX))).all()
    assert seed_rows and all(r.effective_to is None for r in seed_rows)

    stats = rebuild_market_rates(db)
    assert stats["expired"] >= len(seed_rows) + 1
    for r in [*seed_rows, old]:
        db.refresh(r)
        assert r.effective_to == today - timedelta(days=1)
    # Sau khi đơn giá seed dev hết hiệu lực: hoàn thiện không còn đơn giá
    assert svc.select_rate(db, "nha_pho", DN, "hoan_thien") is None
    # Tỉnh không đủ dữ liệu -> dùng dòng toàn quốc tổng hợp, trạng thái reference
    ct = svc.select_rate(db, "nha_pho", "Cần Thơ", "xay_tho")
    assert ct.is_fallback and ct.province == "*" and ct.status == "reference"
    assert ct.source.label.startswith(MARKET_LABEL_PREFIX)
    dn = svc.select_rate(db, "nha_pho", "da nang", "xay_tho", "cao_cap")
    assert not dn.is_fallback and dn.status == "on_dezon" and (dn.price_min, dn.price_max) == (3_150_000, 4_150_000)


def test_rebuild_twice_same_day_updates_in_place(market_db):
    db = market_db
    rebuild_market_rates(db)
    [first] = _active(db, "nha_pho", DN, "xay_tho")
    _contractor(db, "t-g", province=DN, rough=(5_000_000, 6_000_000))
    stats = rebuild_market_rates(db)
    assert stats["updated"] >= 1
    [again] = _active(db, "nha_pho", DN, "xay_tho")
    assert again.id == first.id
    db.refresh(again)
    assert db.get(DataSource, again.source_id).label.startswith(MARKET_LABEL_PREFIX + "xây thô do 5 nhà thầu")


def test_select_rate_prefers_official_over_market(market_db):
    db = market_db
    rebuild_market_rates(db)
    official = DataSource(source_type="csv_import", label="Bảng giá Dezon (test)", status="verified")
    db.add(official)
    db.flush()
    # Dòng chính thức hiệu lực từ trước -> vẫn được ưu tiên hơn dòng tổng hợp mới hơn
    db.add(ConstructionRate(building_type="nha_pho", province=DN, work_scope="xay_tho", quality_tier="trung_binh",
                            price_min=3_900_000, price_max=4_400_000,
                            effective_from=date.today() - timedelta(days=10), source_id=official.id))
    db.add(ConstructionRate(building_type="nha_pho", province="*", work_scope="xay_tho", quality_tier="trung_binh",
                            price_min=3_700_000, price_max=4_100_000,
                            effective_from=date.today() - timedelta(days=10), source_id=official.id))
    db.flush()
    dn = svc.select_rate(db, "nha_pho", DN, "xay_tho")
    assert (dn.price_min, dn.status, dn.source.label) == (3_900_000, "verified", "Bảng giá Dezon (test)")
    ct = svc.select_rate(db, "nha_pho", "Cần Thơ", "xay_tho")
    assert ct.is_fallback and ct.price_min == 3_700_000 and ct.status == "reference"
