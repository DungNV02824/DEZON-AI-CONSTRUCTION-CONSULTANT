"""Dữ kiện đầu vào của Matching Engine (đặc tả mục 7) — dataclass thuần, không phụ thuộc DB.

Service nạp dữ liệu từ DB rồi dựng `ContractorFacts`; engine (scorer, reasons) chỉ đọc các dataclass này.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.evidence import SourceRef, VerificationStatus


@dataclass(frozen=True)
class FieldSource:
    """Nguồn của một trường dữ liệu: tham chiếu hiển thị + trạng thái xác minh (data_sources.status)."""
    ref: SourceRef | None
    status: str = VerificationStatus.on_dezon.value


@dataclass(frozen=True)
class CapabilityFact:
    building_type: str | None = None
    work_scope: str | None = None
    province: str | None = None
    district: str | None = None


@dataclass(frozen=True)
class ProjectFact:
    building_type: str | None = None
    province: str | None = None
    district: str | None = None
    floors: int | None = None


@dataclass
class ContractorFacts:
    id: int
    name: str
    slug: str = ""
    profile_url: str = ""
    is_active: bool = True
    province: str | None = None
    founded_year: int | None = None
    years_experience: int | None = None
    staff_workers: int | None = None
    staff_office: int | None = None
    rough_price_min: float | None = None
    rough_price_max: float | None = None
    finish_price_min: float | None = None
    finish_price_max: float | None = None
    typical_project_value: float | None = None
    dezon_project_count: int | None = None
    has_warranty_info: bool | None = None
    warranty_text: str | None = None
    description: str | None = None
    avatar_url: str | None = None
    capabilities: list[CapabilityFact] = field(default_factory=list)
    projects: list[ProjectFact] = field(default_factory=list)
    # Nguồn theo từng trường (khóa = tên cột contractors, thêm "capabilities"); thiếu -> default_source.
    field_sources: dict[str, FieldSource] = field(default_factory=dict)
    default_source: FieldSource | None = None

    def source_for(self, *fields: str) -> FieldSource | None:
        for f in fields:
            if f in self.field_sources:
                return self.field_sources[f]
        return self.default_source

    def status_of(self, fld: str) -> str:
        src = self.source_for(fld)
        return src.status if src else VerificationStatus.unavailable.value


# --- 7.3 profile_completeness ------------------------------------------------------------------
PROFILE_FIELDS: tuple[str, ...] = (
    "founded_year", "years_experience", "staff_workers", "staff_office", "rough_price_min",
    "finish_price_min", "description", "avatar_url", "dezon_project_count", "has_warranty_info",
)


def _field_present(facts: ContractorFacts, fld: str) -> bool:
    v: Any = getattr(facts, fld)
    if fld == "dezon_project_count":
        return v is not None and v > 0
    if fld in ("rough_price_min", "finish_price_min"):  # có đơn giá = có ít nhất một cận
        return v is not None or getattr(facts, fld.replace("_min", "_max")) is not None
    if fld == "has_warranty_info":
        return v is True  # False = hồ sơ không có thông tin bảo hành
    if isinstance(v, str):
        return bool(v.strip())
    return v is not None


def present_profile_fields(facts: ContractorFacts) -> list[str]:
    return [f for f in PROFILE_FIELDS if _field_present(facts, f)]


def profile_completeness(facts: ContractorFacts) -> float:
    """Tỷ lệ 10 trường hồ sơ có giá trị (mục 7.3), làm tròn 3 chữ số."""
    return round(len(present_profile_fields(facts)) / len(PROFILE_FIELDS), 3)


def verified_ratio(facts: ContractorFacts) -> float:
    """Tỷ lệ trường verified trên cùng 10 trường hồ sơ (trường trống tính là chưa verified)."""
    n = sum(1 for f in present_profile_fields(facts)
            if facts.status_of(f) == VerificationStatus.verified.value)
    return round(n / len(PROFILE_FIELDS), 3)


def has_any_price(facts: ContractorFacts) -> bool:
    return any(v is not None for v in (facts.rough_price_min, facts.rough_price_max,
                                       facts.finish_price_min, facts.finish_price_max))


def missing_important_fields(facts: ContractorFacts) -> list[str]:
    """Trường quan trọng cho nhãn "Cần đánh giá thêm" (mục 7.3): đơn giá, kinh nghiệm."""
    missing = []
    if not has_any_price(facts):
        missing.append("price")
    if facts.years_experience is None:
        missing.append("years_experience")
    return missing


# --- Chuẩn hóa địa danh để so khớp -------------------------------------------------------------
# Tiền tố có dấu (so trên chuỗi còn dấu, tránh cắt nhầm địa danh như "Quan Hóa").
_PLACE_PREFIX = re.compile(r"^(thành phố|tp\.?|tỉnh|quận|q\.|huyện|thị xã|tx\.?)\s*", re.IGNORECASE)
# Tiền tố không dấu (khách/LLM gõ không dấu). "quan"/"q" chỉ cắt khi theo sau là số ("quan 7", "q7").
_PLACE_PREFIX_ASCII = re.compile(r"^(thanh pho |tp |tinh |huyen |thi xa |tx |quan (?=\d)|q ?(?=\d))")
_PLACE_ALIASES: dict[str, str] = {
    "hcm": "ho chi minh", "hcmc": "ho chi minh", "tphcm": "ho chi minh", "sai gon": "ho chi minh",
    "saigon": "ho chi minh", "sg": "ho chi minh", "ho chi minh city": "ho chi minh",
    "hn": "ha noi", "hanoi": "ha noi", "brvt": "ba ria vung tau",
}


def fold_vi(s: str) -> str:
    """Bỏ dấu tiếng Việt (NFC/NFD đều được), đ -> d, chữ thường."""
    t = unicodedata.normalize("NFD", s.casefold())
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    return t.replace("đ", "d")


def norm_place(s: str | None) -> str:
    """Khóa so khớp địa danh: 'TP. Hồ Chí Minh' ~ 'Hồ Chí Minh' ~ 'Ho Chi Minh' ~ 'TP.HCM' ~ 'Sài Gòn';
    'TP. Thủ Đức' ~ 'Thu Duc'; 'Quận 7' ~ 'Q7' ~ 'Q.7' ~ '7'; 'Bà Rịa - Vũng Tàu' ~ 'Ba Ria Vung Tau'."""
    if not s:
        return ""
    t = re.sub(r"\s+", " ", unicodedata.normalize("NFC", s).strip().casefold())
    prev = None
    while prev != t:
        prev = t
        t = _PLACE_PREFIX.sub("", t).strip()
    t = re.sub(r"[^0-9a-z]+", " ", fold_vi(t)).strip()
    prev = None
    while prev != t:
        prev = t
        t = _PLACE_PREFIX_ASCII.sub("", t).strip()
    return _PLACE_ALIASES.get(t, t)
