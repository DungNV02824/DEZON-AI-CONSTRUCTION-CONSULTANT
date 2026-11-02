"""EvidenceValue — định dạng chuẩn mọi tool trả về (đặc tả mục 4.3, 4.4).

Mọi con số/thông tin hiển thị cho khách phải đi qua EvidenceValue để có nguồn,
ngày cập nhật, trạng thái xác minh và cờ dữ liệu cũ.
"""
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from app.config import get_settings
from app.formatting import NO_DATA


class VerificationStatus(StrEnum):
    verified = "verified"
    on_dezon = "on_dezon"
    reference = "reference"
    unavailable = "unavailable"


class SourceType(StrEnum):
    dezon_api = "dezon_api"
    contractor_verified = "contractor_verified"
    crawl_public = "crawl_public"
    dezon_editorial = "dezon_editorial"
    csv_import = "csv_import"


STATUS_LABEL_VI: dict[VerificationStatus, str] = {
    VerificationStatus.verified: "Đã xác minh",
    VerificationStatus.on_dezon: "Có dữ liệu trên Dezon",
    VerificationStatus.reference: "Dữ liệu tham khảo",
    VerificationStatus.unavailable: "Chưa có dữ liệu",
}

# Thứ tự ưu tiên nguồn (nguyên tắc 2.10): số nhỏ = ưu tiên cao.
SOURCE_PRIORITY: dict[SourceType, int] = {
    SourceType.dezon_api: 1,
    SourceType.contractor_verified: 2,
    SourceType.crawl_public: 3,
    SourceType.dezon_editorial: 4,
    SourceType.csv_import: 5,
}


class SourceRef(BaseModel):
    label: str
    url: str | None = None
    updated_at: date | None = None  # ngày cập nhật hiển thị cho khách (ISO)


class EvidenceValue(BaseModel):
    value: Any = None
    display: str = NO_DATA
    status: VerificationStatus = VerificationStatus.unavailable
    source: SourceRef | None = None
    is_stale: bool = False

    @classmethod
    def unavailable(cls) -> "EvidenceValue":
        return cls(value=None, display=NO_DATA, status=VerificationStatus.unavailable, source=None,
                   is_stale=False)

    @property
    def has_value(self) -> bool:
        return self.value is not None and self.status != VerificationStatus.unavailable


def default_status_for(source_type: str | SourceType, approved: bool | None = None) -> VerificationStatus:
    """Ánh xạ mặc định source_type -> trạng thái (mục 4.4)."""
    st = SourceType(source_type)
    if st == SourceType.contractor_verified:
        return VerificationStatus.verified
    if st == SourceType.dezon_editorial:
        return VerificationStatus.verified if approved else VerificationStatus.reference
    if st == SourceType.csv_import:
        return VerificationStatus.reference
    return VerificationStatus.on_dezon  # dezon_api, crawl_public


def _as_date(d: date | datetime | None) -> date | None:
    if d is None:
        return None
    return d.date() if isinstance(d, datetime) else d


def compute_is_stale(updated_at: date | datetime | None, today: date | None = None,
                     stale_days: int | None = None) -> bool:
    d = _as_date(updated_at)
    if d is None:
        return False
    today = today or date.today()
    stale_days = stale_days if stale_days is not None else get_settings().stale_days
    return (today - d).days > stale_days


def source_ref_from(ds: Any) -> SourceRef | None:
    """Tạo SourceRef từ một bản ghi data_sources (ORM DataSource hoặc object có cùng thuộc tính)."""
    if ds is None:
        return None
    updated = _as_date(getattr(ds, "source_updated_at", None)) or _as_date(getattr(ds, "fetched_at", None))
    return SourceRef(label=ds.label, url=ds.url, updated_at=updated)


def make_evidence(value: Any, display: str, source: Any = None, status: VerificationStatus | str | None = None,
                  today: date | None = None, stale_days: int | None = None) -> EvidenceValue:
    """Đóng gói một giá trị kèm nguồn.

    - `value is None` -> EvidenceValue.unavailable() (luôn hiển thị "Chưa có dữ liệu").
    - `source` có thể là DataSource ORM, SourceRef hoặc None.
    - `status` mặc định lấy từ `source.status` (DataSource) hoặc 'reference' nếu không xác định.
    """
    if value is None:
        return EvidenceValue.unavailable()
    ref = source if isinstance(source, SourceRef) else source_ref_from(source)
    if status is None:
        status = getattr(source, "status", None) or VerificationStatus.reference
    return EvidenceValue(
        value=value,
        display=display,
        status=VerificationStatus(status),
        source=ref,
        is_stale=compute_is_stale(ref.updated_at if ref else None, today, stale_days),
    )
