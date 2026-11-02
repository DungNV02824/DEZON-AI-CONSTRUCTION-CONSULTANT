"""Test service khái toán với PostgreSQL (fixture `db`, dữ liệu seed dev)."""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.db.models import ConstructionRate, DataSource, EstimateItemRow, EstimateRule, EstimateSession
from app.engines.estimate import RULES_NOT_APPROVED_WARNING
from app.formatting import NO_DATA
from app.schemas.estimate import EstimateInput
from app.services import estimate_service as svc

HCM = "TP. Hồ Chí Minh"


def hcm_input(**kw) -> EstimateInput:
    base = dict(building_type="nha_pho", province=HCM, land_width_m=5, land_length_m=20, floors=4,
                work_scope="xay_tho")
    base.update(kw)
    return EstimateInput(**base)


def _source(db, status="verified", label="Bảng giá test") -> DataSource:
    ds = DataSource(source_type="dezon_api", label=label, status=status)
    db.add(ds)
    db.flush()
    return ds


# --- Hệ số --------------------------------------------------------------------------------------

def test_load_ruleset_from_seed(db):
    rs = svc.load_ruleset(db)
    assert len(rs.rules) == 16
    assert rs.version == 1
    assert rs["foundation_pile"].coefficient == 0.4
    assert not rs.all_approved
    assert rs["floor_standard"].source.label.startswith("Hệ số khái toán mặc định")


def test_load_ruleset_prefers_latest_approved_version(db):
    db.add(EstimateRule(code="floor_standard", group_name="tang", label_vi="Tầng (v2)", coefficient=1.0,
                        base="floor_area", version=2, approved=True))
    db.add(EstimateRule(code="floor_standard", group_name="tang", label_vi="Tầng (v3 nháp)", coefficient=1.1,
                        base="floor_area", version=3, approved=False))
    db.add(EstimateRule(code="roof_metal", group_name="mai", label_vi="Mái tôn (v2 nháp)", coefficient=0.35,
                        base="roof_area", version=2, approved=False))
    db.flush()
    rs = svc.load_ruleset(db)
    assert rs["floor_standard"].version == 2 and rs["floor_standard"].approved
    assert rs["roof_metal"].version == 2 and not rs["roof_metal"].approved
    assert rs.version == 2


# --- Đơn giá ------------------------------------------------------------------------------------

def test_select_rate_exact_hcm(db):
    rate = svc.select_rate(db, "nha_pho", HCM, "xay_tho", "trung_binh")
    assert rate is not None and not rate.is_fallback
    assert rate.province == HCM
    assert (rate.price_min, rate.price_max) == (3_550_000, 4_200_000)
    assert rate.status == "reference"
    assert rate.source.updated_at == date(2026, 10, 31)


def test_select_rate_province_normalized(db):
    rate = svc.select_rate(db, "nha_pho", "  tp.   hồ chí MINH ", "xay_tho", "trung_binh")
    assert rate is not None and rate.province == HCM and not rate.is_fallback


def test_select_rate_fallback_for_can_tho(db):
    rate = svc.select_rate(db, "nha_pho", "Cần Thơ", "xay_tho", "trung_binh")
    assert rate is not None and rate.is_fallback
    assert rate.province == "*"
    assert (rate.price_min, rate.price_max) == (3_400_000, 4_000_000)
    assert rate.status == "reference"


def test_select_rate_none_when_no_data(db):
    assert svc.select_rate(db, "can_ho", HCM, "xay_tho", "trung_binh") is None
    ev, rate = svc.get_reference_rates(db, "can_ho", HCM, "xay_tho")
    assert rate is None and ev.display == NO_DATA and ev.status == "unavailable"


def test_select_rate_effective_dates_and_source_status(db):
    ds = _source(db, status="verified")
    today = date.today()
    # Hết hiệu lực -> bỏ qua
    db.add(ConstructionRate(building_type="nha_pho", province="Đà Nẵng", work_scope="xay_tho",
                            quality_tier="trung_binh", price_min=1, price_max=2,
                            effective_from=today - timedelta(days=400), effective_to=today - timedelta(days=1),
                            source_id=ds.id))
    # Chưa tới ngày hiệu lực -> bỏ qua
    db.add(ConstructionRate(building_type="nha_pho", province="Đà Nẵng", work_scope="xay_tho",
                            quality_tier="trung_binh", price_min=3, price_max=4,
                            effective_from=today + timedelta(days=10), source_id=ds.id))
    # Mới hơn seed cho HCM -> được chọn, status lấy từ data_sources
    db.add(ConstructionRate(building_type="nha_pho", province=HCM, work_scope="xay_tho",
                            quality_tier="trung_binh", price_min=3_700_000, price_max=4_300_000,
                            effective_from=today - timedelta(days=1), source_id=ds.id))
    db.flush()
    dn = svc.select_rate(db, "nha_pho", "Đà Nẵng", "xay_tho", "trung_binh")
    assert dn.is_fallback and dn.price_min == 3_400_000
    hcm = svc.select_rate(db, "nha_pho", HCM, "xay_tho", "trung_binh")
    assert (hcm.price_min, hcm.status, hcm.source.label) == (3_700_000, "verified", "Bảng giá test")
    ev, _ = svc.get_reference_rates(db, "nha_pho", HCM, "xay_tho")
    assert ev.display == "3,7–4,3 triệu/m²" and ev.status == "verified"


def test_get_reference_rates(db):
    ev, rate = svc.get_reference_rates(db, "nha_pho", HCM, "xay_tho", "trung_binh")
    assert ev.value == [3_550_000, 4_200_000]
    assert ev.display == "3,55–4,2 triệu/m²"
    assert ev.status == "reference"
    assert ev.source.label == rate.source.label


# --- Khái toán ----------------------------------------------------------------------------------

def test_create_and_get_estimate_roundtrip(db):
    res = svc.create_estimate(db, hcm_input())
    assert res.estimate_session_id is not None
    assert res.converted_area_m2 == 490
    assert (res.cost_min, res.cost_max) == (1_740_000_000, 2_058_000_000)
    assert res.cost.display == "1,740–2,058 tỷ"
    assert res.rules_status == "reference" and RULES_NOT_APPROVED_WARNING in res.warnings
    assert res.rate_id is not None

    row = db.get(EstimateSession, res.estimate_session_id)
    assert row.cost_min == Decimal(1_740_000_000)
    assert row.converted_area_m2 == Decimal("490.00")
    assert row.input["floors"] == 4 and "foundation_type" not in row.input  # input gốc
    assert set(row.assumptions) >= {"assumptions", "exclusions", "cost_drivers", "warnings", "snapshot"}
    n_items = db.scalar(select(func.count()).select_from(EstimateItemRow)
                        .where(EstimateItemRow.estimate_session_id == row.id))
    assert n_items == len(res.items) == 6

    again = svc.get_estimate(db, res.estimate_session_id)
    assert again.model_dump() == res.model_dump()
    assert svc.get_estimate(db, str(res.estimate_session_id)).cost_min == res.cost_min


def test_get_estimate_missing(db):
    assert svc.get_estimate(db, uuid.uuid4()) is None
    assert svc.get_estimate(db, "khong-phai-uuid") is None


def test_get_estimate_without_snapshot_recomputes(db):
    rate = svc.select_rate(db, "nha_pho", HCM, "xay_tho", "trung_binh")
    row = EstimateSession(id=uuid.uuid4(), input=hcm_input().model_dump(mode="json", exclude_unset=True),
                          rules_version=1, rate_id=rate.id, converted_area_m2=490, cost_min=None, cost_max=None,
                          assumptions={})
    db.add(row)
    db.flush()
    res = svc.get_estimate(db, row.id)
    assert res.estimate_session_id == row.id
    assert res.cost_min == 1_740_000_000


def test_create_estimate_fallback_province(db):
    res = svc.create_estimate(db, hcm_input(province="Cần Thơ"))
    assert res.rate.status == "reference"
    assert "Dùng đơn giá mặc định toàn quốc do chưa có đơn giá riêng cho Cần Thơ" in res.assumptions
    assert res.cost_min == 1_666_000_000  # 490 × 3,4 triệu


def test_create_estimate_without_rate(db):
    res = svc.create_estimate(db, hcm_input(building_type="can_ho", floors=1, work_scope="xay_tho"))
    assert res.cost_min is None and res.cost.display == NO_DATA
    row = db.get(EstimateSession, res.estimate_session_id)
    assert row.cost_min is None and row.rate_id is None


# --- Ngân sách ----------------------------------------------------------------------------------

def test_budget_adjustments_hcm_1_2_ty(db):
    res = svc.create_estimate(db, hcm_input())
    out = svc.suggest_budget_adjustments(db, res.estimate_session_id, 1_200_000_000)
    assert out.estimate_session_id == res.estimate_session_id
    assert out.target_budget_vnd == 1_200_000_000
    assert out.target_budget.display == "1,2 tỷ"
    assert out.target_budget.status == "reference"
    assert out.target_budget.source.label == "Ngân sách do anh/chị cung cấp"
    assert out.current_cost.display == res.cost.display
    assert len(out.options) >= 2
    codes = {o.code for o in out.options}
    assert {"reduce_floor", "lower_quality"} <= codes
    lq = next(o for o in out.options if o.code == "lower_quality")
    assert lq.cost_min == 1_568_000_000  # 490 × 3,2 triệu (đơn giá tiết kiệm HCM trong seed)
    for o in out.options:
        assert o.cost_min is not None and o.cost.status in ("reference", "on_dezon", "verified")
        if o.code != "combined":
            assert o.saving_min > 0


def test_budget_adjustments_not_found(db):
    with pytest.raises(svc.EstimateNotFoundError):
        svc.suggest_budget_adjustments(db, uuid.uuid4(), 1_000_000_000)


# --- Bổ sung sau review ------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["HCM", "TPHCM", "Sài Gòn", "ho chi minh", "Thành phố Hồ Chí Minh",
                                  "tp.hồ chí minh", "Hồ Chí Minh"])
def test_select_rate_province_aliases_and_no_diacritics(db, name):
    rate = svc.select_rate(db, "nha_pho", name, "xay_tho", "trung_binh")
    assert rate is not None and rate.province == HCM and not rate.is_fallback


def test_normalize_province():
    assert svc.normalize_province("TP. Hồ Chí Minh") == "ho chi minh"
    assert svc.normalize_province("  Hà   Nội ") == svc.normalize_province("HN") == "ha noi"
    assert svc.normalize_province("Tỉnh Cần Thơ") == svc.normalize_province("can tho") == "can tho"
    assert svc.normalize_province("Đà Nẵng") == "da nang"
    assert svc.normalize_province("*") == "*"
    assert svc.normalize_province(None) == ""


def test_select_rate_invalid_enum_raises_without_breaking_session(db):
    with pytest.raises(ValueError):
        svc.select_rate(db, "nha pho", HCM, "xay_tho", "trung_binh")
    with pytest.raises(ValueError):
        svc.select_rate(db, "nha_pho", HCM, "xay_tho", "sang_trong")
    # Phiên DB vẫn dùng được (không có lỗi enum từ PostgreSQL làm hỏng transaction).
    rate = svc.select_rate(db, "nha_pho", HCM, "xay_tho", None)
    assert rate is not None and rate.quality_tier == "trung_binh"


def test_budget_adjustments_rejects_non_positive_budget(db):
    res = svc.create_estimate(db, hcm_input())
    for bad in (0, -1):
        with pytest.raises(ValueError):
            svc.suggest_budget_adjustments(db, res.estimate_session_id, bad)


def test_create_estimate_with_province_alias_uses_exact_rate(db):
    res = svc.create_estimate(db, hcm_input(province="HCM"))
    assert not any("toàn quốc" in a for a in res.assumptions)
    assert res.cost_min == 1_740_000_000


def test_get_estimate_refreshes_stale_flag_on_read(db):
    from app.engines.estimate import calculate_estimate
    from tests.test_estimate_engine import make_rate, make_rules

    old_rate = make_rate(updated=date(2020, 1, 1))
    snap = calculate_estimate(hcm_input(), make_rules(), old_rate, today=date(2020, 2, 1))
    assert not snap.rate.is_stale and not any("có thể đã cũ" in w for w in snap.warnings)
    row = EstimateSession(id=uuid.uuid4(), input=hcm_input().model_dump(mode="json", exclude_unset=True),
                          rules_version=1, rate_id=None, converted_area_m2=snap.converted_area_m2,
                          cost_min=snap.cost_min, cost_max=snap.cost_max,
                          assumptions={"snapshot": snap.model_dump(mode="json")})
    db.add(row)
    db.flush()
    res = svc.get_estimate(db, row.id)
    assert res.cost_min == snap.cost_min and res.cost.display == snap.cost.display  # con số giữ nguyên
    assert res.rate.is_stale and res.cost.is_stale
    assert "Đơn giá cập nhật ngày 01/01/2020, có thể đã cũ" in res.warnings
