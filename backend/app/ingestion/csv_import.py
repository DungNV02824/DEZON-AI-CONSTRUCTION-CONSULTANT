"""Nhập đơn giá (`construction_rates`) và hệ số khái toán (`estimate_rules`) từ CSV/XLSX (mục 11.2 bước 3).

- CSV: UTF-8 (có hoặc không BOM), phân cách `,` hoặc `;` (tự nhận). XLSX: sheet đầu tiên (openpyxl).
- Mỗi lần nhập = 1 bản ghi `data_sources` (source_type `csv_import` -> trạng thái `reference`), raw_hash = sha256 file.
- Dòng lỗi bị bỏ qua và liệt kê trong báo cáo; dòng hợp lệ vẫn được nhập.
- Không ghi đè bản ghi cùng khóa đang lấy từ nguồn ưu tiên cao hơn (2.10) trừ khi `force=True`.
- Ngoại lệ: dòng đơn giá TỔNG HỢP từ hồ sơ nhà thầu (`app.services.market_rates`, nhãn "Tổng hợp đơn giá ...") cùng
  khóa không chặn: đơn giá chính thức trong file luôn thay thế nó — dòng tổng hợp nhường khóa
  (`displace_market_rate`: xóa nếu chưa có khái toán dùng, nếu đã dùng thì giữ nguyên giá/nguồn/ngày) rồi dòng trong
  file được THÊM MỚI. Không bao giờ ghi giá chính thức đè lên dòng tổng hợp (kể cả `force=True`).
- Dòng đơn giá đã được khái toán đã lưu tham chiếu (`estimate_sessions.rate_id`) không bị đổi giá (kể cả
  `force=True`); chỉ được đặt ngày hết hiệu lực.
- Hệ số nhập vào luôn `approved=false`: chỉ admin duyệt mới bật (mục 11.2 bước 4).

Cột đơn giá: building_type, province, work_scope, quality_tier, price_min, price_max, effective_from, effective_to
Cột hệ số:  code, group_name, label_vi, coefficient, base, is_default, version
(xem docs/samples/*.csv). Giá trị enum chấp nhận mã (`nha_pho`) hoặc nhãn tiếng Việt (`Nhà phố`).
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ConstructionRate, DataSource, EstimateRule
from app.evidence import SourceType
from app.ingestion.parse_vi import (
    clean_text,
    fold_vi,
    normalize_province,
    parse_date_vi,
    parse_decimal_vi,
    parse_money,
)
from app.ingestion.upsert import priority_of, upsert_data_source
from app.schemas.common import BUILDING_TYPE_VI, QUALITY_TIER_VI, WORK_SCOPE_VI
from app.services.market_rates import displace_market_rate, is_market_label, rate_usage

Kind = Literal["rates", "rules"]

RATE_COLUMNS = ["building_type", "province", "work_scope", "quality_tier", "price_min", "price_max",
                "effective_from", "effective_to"]
RATE_REQUIRED = ["building_type", "province", "work_scope", "price_min", "price_max", "effective_from"]
RULE_COLUMNS = ["code", "group_name", "label_vi", "coefficient", "base", "is_default", "version"]
RULE_REQUIRED = ["code", "group_name", "label_vi", "coefficient", "base"]

RULE_GROUPS = {"mong", "tang", "ham", "mai", "san_thuong", "ban_cong", "san"}
RULE_BASES = {"floor_area", "mezzanine_area", "ground_floor_area", "basement_area", "roof_area",
              "rooftop_area", "yard_area", "balcony_area"}
MAX_PRICE_PER_M2 = 999_999_999_999  # NUMERIC(12,0)

# Tiêu đề cột tiếng Việt thường gặp -> tên cột chuẩn
HEADER_ALIASES = {
    "loai cong trinh": "building_type", "tinh thanh": "province", "tinh/thanh": "province", "khu vuc": "province",
    "pham vi": "work_scope", "pham vi cong viec": "work_scope", "muc chat luong": "quality_tier",
    "muc hoan thien": "quality_tier", "don gia tu": "price_min", "don gia min": "price_min",
    "don gia den": "price_max", "don gia max": "price_max", "hieu luc tu": "effective_from",
    "hieu luc den": "effective_to", "ma": "code", "nhom": "group_name", "nhan": "label_vi",
    "ten hang muc": "label_vi", "he so": "coefficient", "co so": "base", "mac dinh": "is_default",
    "phien ban": "version",
}


@dataclass
class RowError:
    row: int  # số dòng trong file (dòng tiêu đề = 1)
    message: str
    field: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"row": self.row, "field": self.field, "message": self.message}


@dataclass
class ImportReport:
    kind: str
    filename: str
    total_rows: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    replaced_market: int = 0  # số dòng tổng hợp từ hồ sơ nhà thầu bị đơn giá chính thức trong file thay thế
    errors: list[RowError] = field(default_factory=list)
    source_id: int | None = None
    status: str = "ok"  # ok | partial | failed | unchanged
    dry_run: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "filename": self.filename, "total_rows": self.total_rows,
                "inserted": self.inserted, "updated": self.updated, "unchanged": self.unchanged,
                "skipped": self.skipped, "replaced_market": self.replaced_market, "error_count": len(self.errors),
                "errors": [e.as_dict() for e in self.errors], "source_id": self.source_id,
                "status": self.status, "dry_run": self.dry_run}


# ------------------------------------------------------------------ đọc file
def _norm_header(h: Any) -> str:
    raw = clean_text(str(h or "")).lstrip("﻿")
    key = fold_vi(raw).strip().replace(" ", "_")
    if key in RATE_COLUMNS or key in RULE_COLUMNS:
        return key
    return HEADER_ALIASES.get(fold_vi(raw).strip(), key)


def read_table(content: bytes, filename: str) -> list[dict[str, Any]]:
    """bytes CSV/XLSX -> list dict (khóa cột đã chuẩn hóa). Mỗi dict có '_row' = số dòng trong file."""
    if filename.lower().endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        try:
            wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            try:
                if not wb.worksheets:
                    raise ValueError("File Excel không có sheet nào")
                rows = list(wb.worksheets[0].iter_rows(values_only=True))
            finally:
                wb.close()
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001 — zip hỏng (BadZipFile), XML hỏng, sai định dạng... (O-F3)
            raise ValueError("File Excel không hợp lệ hoặc bị hỏng — hãy lưu lại dưới dạng .xlsx "
                             "(hoặc xuất CSV UTF-8) rồi nhập lại") from exc
    else:
        try:
            text_ = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("File CSV phải được lưu bằng mã hóa UTF-8 (khuyến nghị UTF-8 có BOM)") from exc
        sample = text_[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = ","
        rows = list(csv.reader(io.StringIO(text_), delimiter=delimiter))
    if not rows:
        return []
    headers = [_norm_header(h) for h in rows[0]]
    out: list[dict[str, Any]] = []
    for idx, r in enumerate(rows[1:], start=2):
        if r is None or all(v is None or str(v).strip() == "" for v in r):
            continue
        rec = {headers[i]: r[i] for i in range(min(len(headers), len(r)))}
        rec["_row"] = idx
        out.append(rec)
    return out


# ------------------------------------------------------------------ chuẩn hóa giá trị
def _enum_lookup(labels: dict[str, str]) -> dict[str, str]:
    m = {k: k for k in labels}
    m.update({fold_vi(v): k for k, v in labels.items()})
    return m


_BT = _enum_lookup(BUILDING_TYPE_VI)
_WS = _enum_lookup(WORK_SCOPE_VI)
_QT = _enum_lookup(QUALITY_TIER_VI)
_WS.update({"phan tho": "xay_tho", "tho": "xay_tho", "tron goi chia khoa trao tay": "tron_goi",
            "hoan thien co ban": "hoan_thien"})
_BT.update({"biet thu": "biet_thu", "nha pho": "nha_pho", "can ho": "can_ho"})


def _enum(value: Any, mapping: dict[str, str]) -> str | None:
    if value is None:
        return None
    s = clean_text(str(value))
    return mapping.get(s) or mapping.get(fold_vi(s)) or mapping.get(fold_vi(s).replace(" ", "_"))


MIN_PRICE_PER_M2 = 100_000  # đơn giá VND/m² thấp hơn mức này gần như chắc chắn ghi sai đơn vị (vd "4,3")


def _price(value: Any) -> int | None:
    """Đơn giá VND/m²: 4300000 / "4.300.000" / "4300000.0" / "4,3 triệu" -> 4300000.

    Số không có đơn vị mà nhỏ hơn 100.000 ("4,3", "4.3", ô số 4.3 trong XLSX) -> None (lỗi dòng), không
    tự nhân với "triệu" — tránh vừa nhập sai 4,3 đồng/m² vừa đoán bừa đơn vị.
    """
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        num = Decimal(str(value))
    else:
        s = clean_text(str(value))
        num_val = parse_decimal_vi(s) if re.fullmatch(r"\d+(?:[.,]\d+)*", s) else None
        if num_val is None:
            return parse_money(s)  # có đơn vị: "4,3 triệu" -> 4300000; khoảng -> None
        num = Decimal(str(num_val))
    if num != num.to_integral_value() or num < MIN_PRICE_PER_M2:
        return None
    return int(num)


def _date(value: Any) -> date | None:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, datetime):  # ô ngày trong XLSX
        return value.date()
    if isinstance(value, date):
        return value
    s = clean_text(str(value))
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return parse_date_vi(s)


def _decimal(value: Any) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value).strip().replace(",", "."))
    except InvalidOperation:
        return None


def _bool(value: Any) -> bool | None:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, bool):
        return value
    f = fold_vi(str(value)).strip()
    if f in ("1", "true", "yes", "y", "co", "x", "dung"):
        return True
    if f in ("0", "false", "no", "n", "khong", "sai"):
        return False
    return None


def _province(value: Any) -> str | None:
    """'*' / 'Toàn quốc' -> '*'; tên tỉnh -> tên chuẩn; không nhận diện được -> None (lỗi dòng)."""
    s = clean_text(str(value or ""))
    if s == "*" or fold_vi(s) in ("toan quoc", "ca nuoc", "mac dinh"):
        return "*"
    return normalize_province(s)


def _differs(cur: Any, new: Any) -> bool:
    if cur is None or new is None:
        return cur is not new
    if isinstance(new, (int, Decimal)) and not isinstance(new, bool):
        return Decimal(str(cur)) != Decimal(str(new))
    return cur != new


# ------------------------------------------------------------------ validate từng dòng
def validate_rate_row(rec: dict[str, Any]) -> tuple[dict[str, Any] | None, list[RowError]]:
    row = rec.get("_row", 0)
    errs: list[RowError] = []
    for col in RATE_REQUIRED:
        if rec.get(col) is None or str(rec.get(col)).strip() == "":
            errs.append(RowError(row, f"Thiếu giá trị cột '{col}'", col))
    if errs:
        return None, errs
    bt = _enum(rec["building_type"], _BT)
    ws = _enum(rec["work_scope"], _WS)
    qt = _enum(rec.get("quality_tier"), _QT) if str(rec.get("quality_tier") or "").strip() else "trung_binh"
    if bt is None:
        errs.append(RowError(row, f"Loại công trình không hợp lệ: '{rec['building_type']}'", "building_type"))
    if ws is None:
        errs.append(RowError(row, f"Phạm vi công việc không hợp lệ: '{rec['work_scope']}'", "work_scope"))
    if qt is None:
        errs.append(RowError(row, f"Mức chất lượng không hợp lệ: '{rec.get('quality_tier')}'", "quality_tier"))
    pmin, pmax = _price(rec["price_min"]), _price(rec["price_max"])
    if pmin is None or pmin <= 0:
        errs.append(RowError(row, f"Đơn giá tối thiểu không hợp lệ: '{rec['price_min']}' (ghi bằng đồng/m², vd "
                                  "4300000, hoặc kèm đơn vị, vd '4,3 triệu')", "price_min"))
    if pmax is None or pmax <= 0:
        errs.append(RowError(row, f"Đơn giá tối đa không hợp lệ: '{rec['price_max']}' (ghi bằng đồng/m², vd "
                                  "4700000, hoặc kèm đơn vị, vd '4,7 triệu')", "price_max"))
    if pmin and pmax and pmin > pmax:
        errs.append(RowError(row, "price_min phải nhỏ hơn hoặc bằng price_max", "price_min"))
    if pmax and pmax > MAX_PRICE_PER_M2:
        errs.append(RowError(row, "Đơn giá vượt giới hạn lưu trữ", "price_max"))
    eff_from = _date(rec["effective_from"])
    eff_to = _date(rec.get("effective_to"))
    if eff_from is None:
        errs.append(RowError(row, f"Ngày hiệu lực không hợp lệ: '{rec['effective_from']}'", "effective_from"))
    if rec.get("effective_to") not in (None, "") and eff_to is None:
        errs.append(RowError(row, f"Ngày hết hiệu lực không hợp lệ: '{rec['effective_to']}'", "effective_to"))
    if eff_from and eff_to and eff_to < eff_from:
        errs.append(RowError(row, "effective_to phải sau effective_from", "effective_to"))
    province = _province(rec["province"])
    if province is None:
        errs.append(RowError(row, f"Tỉnh/thành không nhận diện được: '{rec['province']}' (dùng '*' cho toàn quốc)",
                             "province"))
    if errs:
        return None, errs
    return {"building_type": bt, "province": province, "work_scope": ws, "quality_tier": qt,
            "price_min": pmin, "price_max": pmax, "effective_from": eff_from, "effective_to": eff_to}, []


def validate_rule_row(rec: dict[str, Any]) -> tuple[dict[str, Any] | None, list[RowError]]:
    row = rec.get("_row", 0)
    errs: list[RowError] = []
    for col in RULE_REQUIRED:
        if rec.get(col) is None or str(rec.get(col)).strip() == "":
            errs.append(RowError(row, f"Thiếu giá trị cột '{col}'", col))
    if errs:
        return None, errs
    code = clean_text(str(rec["code"]))
    group = fold_vi(clean_text(str(rec["group_name"]))).replace(" ", "_")
    base = clean_text(str(rec["base"]))
    coef = _decimal(rec["coefficient"])
    if not code.replace("_", "").isalnum():
        errs.append(RowError(row, f"Mã hệ số chỉ gồm chữ, số, dấu gạch dưới: '{code}'", "code"))
    if group not in RULE_GROUPS:
        errs.append(RowError(row, f"Nhóm không hợp lệ: '{rec['group_name']}' (hợp lệ: {sorted(RULE_GROUPS)})",
                             "group_name"))
    if base not in RULE_BASES:
        errs.append(RowError(row, f"Cơ sở diện tích không hợp lệ: '{base}' (hợp lệ: {sorted(RULE_BASES)})",
                             "base"))
    if coef is None or coef <= 0 or coef >= 100:
        errs.append(RowError(row, f"Hệ số phải là số trong (0, 100): '{rec['coefficient']}'", "coefficient"))
    version_raw = rec.get("version")
    version = 1
    if version_raw not in (None, ""):
        try:
            version = int(str(version_raw).strip())
            if version < 1:
                raise ValueError
        except ValueError:
            errs.append(RowError(row, f"Phiên bản phải là số nguyên >= 1: '{version_raw}'", "version"))
    is_default = _bool(rec.get("is_default"))
    if rec.get("is_default") not in (None, "") and is_default is None:
        errs.append(RowError(row, f"is_default phải là true/false: '{rec.get('is_default')}'", "is_default"))
    if errs:
        return None, errs
    return {"code": code, "group_name": group, "label_vi": clean_text(str(rec["label_vi"])),
            "coefficient": coef.quantize(Decimal("0.001")), "base": base, "is_default": bool(is_default),
            "version": version, "approved": False}, []


# ------------------------------------------------------------------ nhập
def _protected(db: Session, existing_source_id: int | None) -> bool:
    """Bản ghi hiện có đến từ nguồn ưu tiên cao hơn csv_import? Đơn giá tổng hợp từ hồ sơ nhà thầu (lưu với
    source_type crawl_public) KHÔNG được bảo vệ: đơn giá chính thức luôn đứng trước nó."""
    from app.ingestion.upsert import get_source

    ds = get_source(db, existing_source_id)
    if ds is None or is_market_label(ds.label):
        return False
    return priority_of(ds.source_type) < priority_of(SourceType.csv_import)


def _is_market_rate(db: Session, row: ConstructionRate) -> bool:
    from app.ingestion.upsert import get_source

    ds = get_source(db, row.source_id)
    return ds is not None and is_market_label(ds.label)


def _price_changes(existing: ConstructionRate, data: dict[str, Any]) -> bool:
    return _differs(existing.price_min, data["price_min"]) or _differs(existing.price_max, data["price_max"])


def import_table(db: Session, content: bytes, filename: str, kind: Kind, *, label: str | None = None,
                 force: bool = False, dry_run: bool = False) -> ImportReport:
    """Nhập bảng đơn giá (`kind="rates"`) hoặc hệ số (`kind="rules"`). Trả báo cáo chi tiết từng dòng lỗi."""
    if kind not in ("rates", "rules"):
        raise ValueError("kind phải là 'rates' hoặc 'rules'")
    report = ImportReport(kind=kind, filename=filename, dry_run=dry_run)
    try:
        rows = read_table(content, filename)
    except ValueError as exc:
        report.status = "failed"
        report.errors.append(RowError(0, str(exc)))
        return report
    report.total_rows = len(rows)
    if not rows:
        report.status = "failed"
        report.errors.append(RowError(0, "File không có dòng dữ liệu"))
        return report
    required = RATE_REQUIRED if kind == "rates" else RULE_REQUIRED
    missing_cols = [c for c in required if c not in rows[0]]
    if missing_cols:
        report.status = "failed"
        report.errors.append(RowError(1, f"Thiếu cột bắt buộc: {', '.join(missing_cols)}"))
        return report

    validator = validate_rate_row if kind == "rates" else validate_rule_row
    valid: list[tuple[int, dict[str, Any]]] = []
    seen_keys: set[tuple] = set()
    for rec in rows:
        data, errs = validator(rec)
        if errs:
            report.errors.extend(errs)
            continue
        key = (tuple(data[k] for k in ("building_type", "province", "work_scope", "quality_tier",
                                       "effective_from")) if kind == "rates" else (data["code"], data["version"]))
        if key in seen_keys:
            report.errors.append(RowError(rec["_row"], "Dòng trùng khóa với một dòng phía trên trong file"))
            continue
        seen_keys.add(key)
        valid.append((rec["_row"], data))

    raw_hash = hashlib.sha256(content).hexdigest()
    label = label or (f"Bảng đơn giá nhập từ file {Path(filename).name}" if kind == "rates"
                      else f"Bộ hệ số khái toán nhập từ file {Path(filename).name}")
    if dry_run:
        # Xem trước: KHÔNG ghi DB, nhưng chạy đúng logic so khớp để báo số dòng sẽ thêm/cập nhật/giữ nguyên/bị chặn
        # (O-F7). Nguồn được tìm giống upsert_data_source (theo nhãn, url rỗng, loại csv_import).
        ds = db.scalar(select(DataSource).where(DataSource.label == label, DataSource.url.is_(None),
                                                DataSource.source_type == SourceType.csv_import.value))
        ds_id = ds.id if ds is not None else None
        report.source_id = ds_id
        if ds is not None and ds.raw_hash == raw_hash and not force:
            report.unchanged = len(valid)
            report.status = "unchanged"
            return report
    else:
        ds, state = upsert_data_source(db, url=None, label=label, source_type=SourceType.csv_import,
                                       raw_hash=raw_hash)
        ds_id = ds.id
        report.source_id = ds.id
        if state == "unchanged" and not force:
            report.unchanged = len(valid)
            report.status = "unchanged"
            return report

    for row_no, data in valid:
        if kind == "rates":
            existing = db.scalar(select(ConstructionRate).where(
                ConstructionRate.building_type == data["building_type"],
                ConstructionRate.province == data["province"],
                ConstructionRate.work_scope == data["work_scope"],
                ConstructionRate.quality_tier == data["quality_tier"],
                ConstructionRate.effective_from == data["effective_from"]))
        else:
            existing = db.scalar(select(EstimateRule).where(EstimateRule.code == data["code"],
                                                            EstimateRule.version == data["version"]))
        if kind == "rates" and existing is not None and _is_market_rate(db, existing):
            # Đơn giá chính thức thay thế dòng tổng hợp cùng khóa: nhường khóa rồi THÊM dòng mới — không ghi đè giá
            # lên dòng tổng hợp (khái toán đã lưu có thể đang tham chiếu nó), kể cả khi force=True.
            if not dry_run:
                try:
                    displace_market_rate(db, existing)
                except ValueError as exc:
                    report.skipped += 1
                    report.errors.append(RowError(row_no, f"Không thay được đơn giá tổng hợp cùng khóa: {exc}"))
                    continue
            report.replaced_market += 1
            existing = None
        if existing is None:
            if not dry_run:
                model = ConstructionRate if kind == "rates" else EstimateRule
                db.add(model(**data, source_id=ds_id))
            report.inserted += 1
            continue
        if kind == "rates" and _price_changes(existing, data) and (used := rate_usage(db, existing.id)):
            report.skipped += 1
            report.errors.append(RowError(row_no, f"Đơn giá cùng khóa đã được dùng trong {used} khái toán — không "
                                                  "đổi giá; hãy nhập đơn giá mới với ngày hiệu lực khác"))
            continue
        if not force and _protected(db, existing.source_id):
            report.skipped += 1
            report.errors.append(RowError(row_no, "Bản ghi cùng khóa đang lấy từ nguồn ưu tiên cao hơn — bỏ qua "
                                                  "(dùng force=True nếu admin muốn thay thế)"))
            continue
        changed = False
        for k, v in data.items():
            if k == "approved":
                continue  # xử lý bên dưới: chỉ bỏ duyệt khi nội dung hệ số thật sự thay đổi
            if _differs(getattr(existing, k), v):
                if not dry_run:
                    setattr(existing, k, v)
                changed = True
        if kind == "rules" and changed and existing.approved and not dry_run:
            existing.approved = False  # hệ số đã đổi -> admin phải duyệt lại (mục 11.2 bước 4)
        if existing.source_id != ds_id:
            if not dry_run:
                existing.source_id = ds_id
            changed = True
        if changed:
            report.updated += 1
        else:
            report.unchanged += 1
    db.flush()
    report.status = "partial" if report.errors else "ok"
    return report


def import_file(db: Session, path: str | Path, kind: Kind, **kwargs: Any) -> ImportReport:
    """Nhập từ đường dẫn file (.csv / .xlsx)."""
    p = Path(path)
    return import_table(db, p.read_bytes(), p.name, kind, **kwargs)


def import_rates(db: Session, path: str | Path, **kwargs: Any) -> ImportReport:
    return import_file(db, path, "rates", **kwargs)


def import_rules(db: Session, path: str | Path, **kwargs: Any) -> ImportReport:
    return import_file(db, path, "rules", **kwargs)
