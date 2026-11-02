"""Nhập đơn giá / hệ số từ CSV/XLSX vào DB test (đặc tả 11.2 bước 3)."""
import io
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.db.models import ConstructionRate, DataSource, EstimateRule
from app.ingestion.csv_import import import_file, import_table, read_table

SAMPLES = Path(__file__).resolve().parents[2] / "docs" / "samples"


def _csv(text: str, bom: bool = True) -> bytes:
    return ("﻿" if bom else "").encode("utf-8") + text.encode("utf-8")


def test_sample_files_are_utf8_with_bom():
    for name in ("construction_rates.csv", "estimate_rules.csv"):
        raw = (SAMPLES / name).read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")
        raw.decode("utf-8")


def test_import_sample_rates(db):
    report = import_file(db, SAMPLES / "construction_rates.csv", "rates")
    assert report.status == "ok", report.as_dict()
    assert report.inserted == 5 and not report.errors
    ds = db.get(DataSource, report.source_id)
    assert ds.source_type == "csv_import" and ds.status == "reference" and ds.raw_hash
    row = db.scalar(select(ConstructionRate).where(
        ConstructionRate.building_type == "biet_thu", ConstructionRate.province == "*",
        ConstructionRate.work_scope == "xay_tho", ConstructionRate.quality_tier == "trung_binh",
        ConstructionRate.effective_from == date(2026, 11, 1)))
    # dòng dùng nhãn tiếng Việt + "4,0 triệu" + ngày dd/mm/yyyy được chuẩn hóa
    assert row is not None and row.price_min == Decimal(4_000_000) and row.price_max == Decimal(4_800_000)
    assert row.source_id == ds.id
    hcm = db.scalar(select(ConstructionRate).where(ConstructionRate.province == "TP. Hồ Chí Minh",
                                                   ConstructionRate.effective_from == date(2026, 11, 1)))
    assert hcm is not None

    again = import_file(db, SAMPLES / "construction_rates.csv", "rates")
    assert again.status == "unchanged" and again.inserted == 0


def test_import_rates_reports_bad_rows(db):
    content = _csv(
        "building_type,province,work_scope,quality_tier,price_min,price_max,effective_from,effective_to\n"
        "nha_pho,*,xay_tho,trung_binh,3600000,4100000,2026-12-01,\n"            # 2 hợp lệ
        "lau_dai,*,xay_tho,trung_binh,3600000,4100000,2026-12-01,\n"            # 3 enum sai
        "nha_pho,*,xay_tho,kha,5000000,4000000,2026-12-01,\n"                   # 4 min > max
        "nha_pho,*,son_nuoc,trung_binh,1,2,2026-12-01,\n"                       # 5 phạm vi sai
        "nha_pho,*,hoan_thien,trung_binh,Liên hệ,3000000,2026-12-01,\n"         # 6 giá giữ chỗ
        "nha_pho,Sao Hỏa,hoan_thien,trung_binh,2500000,3000000,2026-12-01,\n"   # 7 tỉnh không nhận ra
        "nha_pho,*,tron_goi,trung_binh,5500000,6800000,ngày mai,\n"             # 8 ngày sai
        "nha_pho,*,xay_tho,trung_binh,3600000,4100000,2026-12-01,\n"            # 9 trùng dòng 2
        "can_ho,Hà Nội,noi_that,,2500000,5000000,2026-12-01,2026-11-01\n"       # 10 hết hạn trước hiệu lực
    )
    report = import_table(db, content, "don_gia.csv", "rates")
    assert report.status == "partial"
    assert report.inserted == 1
    rows_with_errors = sorted({e.row for e in report.errors})
    assert rows_with_errors == [3, 4, 5, 6, 7, 8, 9, 10]
    by_row = {e.row: e for e in report.errors}
    assert by_row[3].field == "building_type"
    assert "price_min" in by_row[4].message
    assert by_row[7].field == "province"
    assert all(e.message for e in report.errors)  # thông báo tiếng Việt cho từng dòng
    assert report.as_dict()["error_count"] == len(report.errors)


def test_import_rates_does_not_override_higher_priority_source(db):
    # khóa trùng với đơn giá seed (nguồn dezon_editorial, ưu tiên cao hơn csv_import)
    content = _csv("building_type,province,work_scope,quality_tier,price_min,price_max,effective_from\n"
                   "nha_pho,*,xay_tho,trung_binh,1000000,2000000,2026-01-01\n")
    seed_row = db.scalar(select(ConstructionRate).where(
        ConstructionRate.building_type == "nha_pho", ConstructionRate.province == "*",
        ConstructionRate.work_scope == "xay_tho", ConstructionRate.quality_tier == "trung_binh",
        ConstructionRate.effective_from == date(2026, 1, 1)))
    before = (seed_row.price_min, seed_row.price_max, seed_row.source_id)
    report = import_table(db, content, "ghi_de.csv", "rates")
    assert report.skipped == 1 and report.inserted == 0
    db.refresh(seed_row)
    assert (seed_row.price_min, seed_row.price_max, seed_row.source_id) == before
    forced = import_table(db, content, "ghi_de_force.csv", "rates", force=True)
    assert forced.updated == 1
    db.refresh(seed_row)
    assert seed_row.price_min == Decimal(1_000_000)


def test_import_sample_rules_never_auto_approved(db):
    report = import_file(db, SAMPLES / "estimate_rules.csv", "rules")
    assert report.status == "ok", report.as_dict()
    assert report.inserted == 10
    rule = db.scalar(select(EstimateRule).where(EstimateRule.code == "foundation_pile", EstimateRule.version == 2))
    assert rule.coefficient == Decimal("0.400") and rule.is_default is True
    assert rule.approved is False  # chỉ admin duyệt mới bật
    assert db.get(DataSource, rule.source_id).status == "reference"
    basement = db.scalar(select(EstimateRule).where(EstimateRule.code == "basement_lt_1_3",
                                                    EstimateRule.version == 2))
    assert basement.label_vi == "Hầm sâu < 1,3 m"


def test_import_rules_reports_bad_rows(db):
    content = _csv("code,group_name,label_vi,coefficient,base,is_default,version\n"
                   "roof_green,mai,Mái xanh,0.6,roof_area,false,3\n"
                   "bad code,mai,Mã sai,0.6,roof_area,false,3\n"
                   "x1,khong_co,Nhóm sai,0.6,roof_area,,3\n"
                   "x2,mai,Cơ sở sai,0.6,dien_tich_lo,,3\n"
                   "x3,mai,Hệ số âm,-1,roof_area,,3\n"
                   "x4,mai,Phiên bản sai,0.5,roof_area,,0\n"
                   "x5,mai,,0.5,roof_area,,3\n")
    report = import_table(db, content, "he_so.csv", "rules")
    assert report.inserted == 1
    assert sorted({e.row for e in report.errors}) == [3, 4, 5, 6, 7, 8]


def test_missing_required_column_and_non_utf8(db):
    report = import_table(db, _csv("building_type,province\nnha_pho,*\n"), "thieu_cot.csv", "rates")
    assert report.status == "failed" and "price_min" in report.errors[0].message
    bad = "building_type,province\nbiệt thự,*\n".encode("cp1258", errors="replace")
    report2 = import_table(db, b"\xff\xfe" + bad, "sai_ma.csv", "rates")
    assert report2.status == "failed" and "UTF-8" in report2.errors[0].message


def test_semicolon_csv_and_vietnamese_headers():
    content = _csv("Loại công trình;Tỉnh thành;Phạm vi;Mức chất lượng;Đơn giá từ;Đơn giá đến;Hiệu lực từ\n"
                   "Nhà phố;*;Xây thô;Khá;3900000;4600000;2026-12-01\n")
    rows = read_table(content, "vn.csv")
    assert rows[0]["building_type"] == "Nhà phố" and rows[0]["price_max"] == "4600000"


def test_import_xlsx(db):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["building_type", "province", "work_scope", "quality_tier", "price_min", "price_max",
               "effective_from"])
    ws.append(["van_phong", "Đà Nẵng", "xay_tho", "trung_binh", 3600000, 4300000, date(2026, 12, 1)])
    ws.append(["van_phong", "Đà Nẵng", "xay_tho", "kha", 4300000, 3600000, date(2026, 12, 1)])
    buf = io.BytesIO()
    wb.save(buf)
    report = import_table(db, buf.getvalue(), "don_gia.xlsx", "rates")
    assert report.inserted == 1 and [e.row for e in report.errors] == [3]
    row = db.scalar(select(ConstructionRate).where(ConstructionRate.province == "Đà Nẵng",
                                                   ConstructionRate.building_type == "van_phong"))
    assert row.effective_from == date(2026, 12, 1)


# ------------------------------------------------------------------ review fixes
def test_price_without_unit_is_not_misread(db):
    """'4.3' trước đây bị đọc thành 43 đồng, '4300000.0' thành 43.000.000 — nay: lỗi dòng / đọc đúng."""
    content = _csv(
        "building_type,province,work_scope,quality_tier,price_min,price_max,effective_from\n"
        "nha_pho,Đà Nẵng,xay_tho,trung_binh,4300000.0,4700000.0,2027-01-01\n"   # 2 hợp lệ (xuất từ Excel)
        "nha_pho,Đà Nẵng,hoan_thien,trung_binh,4.3,4.7,2027-01-01\n"            # 3 thiếu đơn vị
        "nha_pho,Đà Nẵng,tron_goi,trung_binh,\"4,3\",\"4,7\",2027-01-01\n"      # 4 thiếu đơn vị
        "nha_pho,Đà Nẵng,noi_that,trung_binh,4.300.000,4tr7,2027-01-01\n"      # 5 hợp lệ
    )
    report = import_table(db, content, "don_gia_don_vi.csv", "rates")
    assert report.inserted == 2
    assert sorted({e.row for e in report.errors}) == [3, 4]
    assert "đồng/m²" in report.errors[0].message
    rough = db.scalar(select(ConstructionRate).where(ConstructionRate.province == "Đà Nẵng",
                                                     ConstructionRate.work_scope == "xay_tho",
                                                     ConstructionRate.effective_from == date(2027, 1, 1)))
    assert (rough.price_min, rough.price_max) == (Decimal(4_300_000), Decimal(4_700_000))
    interior = db.scalar(select(ConstructionRate).where(ConstructionRate.province == "Đà Nẵng",
                                                        ConstructionRate.work_scope == "noi_that",
                                                        ConstructionRate.effective_from == date(2027, 1, 1)))
    assert (interior.price_min, interior.price_max) == (Decimal(4_300_000), Decimal(4_700_000))


def test_xlsx_small_numeric_price_rejected(db):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["building_type", "province", "work_scope", "price_min", "price_max", "effective_from"])
    ws.append(["van_phong", "Cần Thơ", "xay_tho", 4.3, 4.7, date(2027, 1, 1)])  # ô số 4.3 -> không phải 4 đồng
    buf = io.BytesIO()
    wb.save(buf)
    report = import_table(db, buf.getvalue(), "nho.xlsx", "rates")
    assert report.inserted == 0 and {e.field for e in report.errors} >= {"price_min", "price_max"}


def test_reimport_identical_rule_keeps_admin_approval_and_force_reapplies(db):
    content = _csv("code,group_name,label_vi,coefficient,base,is_default,version\n"
                   "roof_review,mai,Mái thử duyệt,0.6,roof_area,false,7\n")
    assert import_table(db, content, "a.csv", "rules").inserted == 1
    rule = db.scalar(select(EstimateRule).where(EstimateRule.code == "roof_review"))
    rule.approved = True  # admin duyệt
    db.flush()
    # cùng nội dung, file khác -> không bỏ duyệt
    report = import_table(db, content, "b.csv", "rules")
    assert report.updated == 1  # chỉ đổi nguồn
    db.refresh(rule)
    assert rule.approved is True
    # hệ số đổi -> phải duyệt lại
    changed = content.replace(b"0.6", b"0.65")
    import_table(db, changed, "b.csv", "rules")
    db.refresh(rule)
    assert rule.coefficient == Decimal("0.650") and rule.approved is False
    # file không đổi nhưng admin sửa tay -> force=True áp lại nội dung file
    rule.coefficient = Decimal("0.9")
    db.flush()
    assert import_table(db, changed, "b.csv", "rules").status == "unchanged"
    forced = import_table(db, changed, "b.csv", "rules", force=True)
    assert forced.updated == 1
    db.refresh(rule)
    assert rule.coefficient == Decimal("0.650")
