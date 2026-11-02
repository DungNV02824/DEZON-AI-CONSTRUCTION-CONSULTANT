from datetime import date

from sqlalchemy import func, select

from app.db.models import Contractor, EstimateRule
from app.evidence import (
    EvidenceValue,
    VerificationStatus,
    compute_is_stale,
    default_status_for,
    make_evidence,
)
from app.formatting import (
    format_price_per_m2_range,
    format_vnd,
    format_vnd_range,
    round_to_million,
    vi_number,
)
from app.schemas.estimate import EstimateInput
from app.schemas.slots import Slots, missing_required, to_estimate_input


def test_vi_number():
    assert vi_number(1234.5) == "1.234,5"
    assert vi_number(2.058, 3) == "2,058"
    assert vi_number(4.0) == "4"


def test_format_money():
    assert format_vnd_range(2_058_000_000, 2_352_000_000) == "2,058–2,352 tỷ"
    assert format_price_per_m2_range(4_300_000, 4_700_000) == "4,3–4,7 triệu/m²"
    assert format_vnd(850_000_000) == "850 triệu"
    assert format_vnd_range(850_000_000, 1_200_000_000) == "850 triệu–1,2 tỷ"
    assert format_vnd(None) == "Chưa có dữ liệu"
    assert round_to_million(2_058_400_000) == 2_058_000_000


def test_evidence():
    u = EvidenceValue.unavailable()
    assert u.display == "Chưa có dữ liệu" and u.status == VerificationStatus.unavailable
    assert make_evidence(None, "x").display == "Chưa có dữ liệu"
    assert default_status_for("csv_import") == "reference"
    assert default_status_for("dezon_editorial", approved=True) == "verified"
    assert default_status_for("crawl_public") == "on_dezon"
    assert compute_is_stale(date(2026, 1, 1), today=date(2026, 11, 1), stale_days=180)
    assert not compute_is_stale(date(2026, 10, 1), today=date(2026, 11, 1), stale_days=180)


def test_slots_to_input():
    s = Slots(building_type="nha_pho", province="TP. Hồ Chí Minh", land_width_m=5, land_length_m=20,
              floors=4, work_scope="xay_tho")
    assert missing_required(s) == ["land_area_m2"]
    s.land_area_m2 = 100
    assert missing_required(s) == []
    inp = to_estimate_input(s)
    assert inp.land_area_m2 == 100 and inp.quality_tier == "trung_binh"
    assert EstimateInput(building_type="nha_pho", province="x", land_width_m=4, land_length_m=16, floors=2,
                         work_scope="xay_tho").land_area_m2 == 64


def test_seeded_db(db):
    assert db.scalar(select(func.count()).select_from(EstimateRule)) == 16
    assert db.scalar(select(Contractor).where(Contractor.slug == "kien-phong")).years_experience == 8


def test_vnd_range_same_unit_same_decimals():
    """C-F10: hai đầu khoảng cùng đơn vị hiển thị cùng số chữ số thập phân."""
    assert format_vnd_range(1_740_000_000, 2_058_000_000) == "1,740–2,058 tỷ"
    assert format_vnd_range(1_500_000_000, 2_058_000_000) == "1,500–2,058 tỷ"
    assert format_vnd_range(1_280_000_000, 1_520_000_000) == "1,28–1,52 tỷ"
    assert format_vnd_range(2_000_000_000, 3_000_000_000) == "2–3 tỷ"
    assert format_vnd_range(850_000_000, 900_000_000) == "850–900 triệu"
    assert vi_number(1.74, 3, 3) == "1,740"
