"""Hợp đồng dữ liệu phân tích báo giá nhà thầu so với khái toán (V2 — quotes; docs/API_CONTRACT.md "Admin —
yêu cầu báo giá").

Chỉ dùng cho nhân viên (trang admin): nhân viên nhập báo giá nhà thầu gửi (Zalo, PDF, Excel), engine
`app/engines/quote_analysis.py` so với snapshot khái toán. Mọi giá trị hiển thị là EvidenceValue.
"""
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.evidence import EvidenceValue, SourceRef, VerificationStatus

QuoteGroup = Literal["mong", "tang", "ham", "mai", "san_thuong", "ban_cong", "san", "khac"]

# Nhãn nhóm hạng mục (estimate_rules.group_name) cho bảng so sánh.
QUOTE_GROUP_VI: dict[str, str] = {
    "mong": "Móng",
    "tang": "Tầng (trệt, lầu, lửng)",
    "ham": "Tầng hầm",
    "mai": "Mái",
    "san_thuong": "Sân thượng",
    "ban_cong": "Ban công",
    "san": "Sân",
    "khac": "Hạng mục khác",
}

# below/within/above: so với khoảng khái toán [min, max]; not_comparable: thiếu số một trong hai bên.
Position = Literal["below", "within", "above", "not_comparable"]
LinePosition = Literal["below", "within", "above", "missing_in_quote", "extra_in_quote", "not_comparable"]

POSITION_VI: dict[str, str] = {
    "below": "Thấp hơn khoảng khái toán",
    "within": "Trong khoảng khái toán",
    "above": "Cao hơn khoảng khái toán",
    "not_comparable": "Chưa so được",
    "missing_in_quote": "Báo giá chưa có",
    "extra_in_quote": "Khái toán không có",
}


class QuoteLine(BaseModel):
    """Một dòng hạng mục trong báo giá nhà thầu (lưu ở contractor_quotes.lines). Mọi số đều tùy chọn."""
    group: QuoteGroup = "khac"
    label: str = Field(default="", max_length=200)
    rule_code: str | None = Field(default=None, max_length=100)  # mã hệ số khái toán khi điền sẵn từ khái toán
    area_m2: float | None = Field(default=None, ge=0, le=1_000_000)
    coefficient: float | None = Field(default=None, ge=0, le=10)
    converted_m2: float | None = Field(default=None, ge=0, le=1_000_000)  # bỏ trống -> area_m2 × coefficient
    amount_vnd: int | None = Field(default=None, ge=0, le=10**15)


class QuoteData(BaseModel):
    """Đầu vào thuần của engine (dựng từ contractor_quotes hoặc từ form xem trước)."""
    quote_id: int | None = None
    contractor_name: str | None = None
    total_amount: int | None = None  # VND
    price_per_m2: int | None = None  # VND/m²
    converted_area_m2: float | None = None  # diện tích sàn quy đổi theo nhà thầu
    scope_text: str | None = None
    file_url: str | None = None
    received_at: date | None = None
    verified: bool = False
    lines: list[QuoteLine] = []


class QuoteComparison(BaseModel):
    """So một chỉ số (tổng tiền, đơn giá/m², diện tích quy đổi) giữa khái toán và báo giá."""
    label_vi: str
    estimate: EvidenceValue  # khoảng [min, max] (hoặc một giá trị với diện tích)
    quote: EvidenceValue
    position: Position = "not_comparable"
    # (báo giá − điểm giữa khái toán) / điểm giữa, luôn có khi hai bên đủ số; display "+12,5%".
    gap_pct: EvidenceValue = Field(default_factory=EvidenceValue.unavailable)
    # Phần vượt ra ngoài khoảng: (báo giá − cận trên)/cận trên hoặc (cận dưới − báo giá)/cận dưới; trong khoảng ->
    # unavailable.
    outside_pct: EvidenceValue = Field(default_factory=EvidenceValue.unavailable)
    notable: bool = False  # ngoài khoảng [min, max]
    note_vi: str = ""


class QuoteLineAnalysis(BaseModel):
    group: str
    label_vi: str
    estimate_area: EvidenceValue  # Σ diện tích quy đổi của nhóm trong khái toán
    quote_area: EvidenceValue  # Σ diện tích quy đổi của nhóm trong báo giá
    estimate_cost: EvidenceValue  # Σ diện tích quy đổi × [P_min, P_max]
    quote_amount: EvidenceValue
    position: LinePosition = "not_comparable"
    gap_pct: EvidenceValue = Field(default_factory=EvidenceValue.unavailable)
    notable: bool = False
    notes: list[str] = []
    # Hạng mục khái toán trong nhóm chưa có dòng báo giá (ghép theo rule_code + diễn giải). Khác rỗng -> estimate_area,
    # estimate_cost và kết quả so chỉ tính phần khái toán của các hạng mục đã có dòng.
    missing_items: list[str] = []


class GapBreakdown(BaseModel):
    """ΔC = báo giá − điểm giữa khái toán = (A_q − A_e)·P_mid + A_q·(P_q − P_mid) + phần còn lại."""
    total_gap: EvidenceValue
    area_effect: EvidenceValue  # do diện tích quy đổi khác
    price_effect: EvidenceValue  # do đơn giá/m² khác
    other_effect: EvidenceValue  # phần còn lại (phạm vi, làm tròn, tổng tiền không khớp đơn giá × diện tích)
    note_vi: str = ""


class QuoteAnalysisResult(BaseModel):
    estimate_session_id: UUID | None = None
    quote_id: int | None = None
    contractor_name: str | None = None
    quote_status: VerificationStatus = VerificationStatus.reference  # verified khi đã đối chiếu file gốc
    quote_source: SourceRef | None = None
    total: QuoteComparison
    price_per_m2: QuoteComparison
    area: QuoteComparison
    breakdown: GapBreakdown | None = None
    lines: list[QuoteLineAnalysis] = []
    lines_compared: bool = False  # báo giá có ít nhất một dòng hạng mục
    scope_text: str | None = None
    exclusions: list[str] = []  # hạng mục khái toán chưa bao gồm (để đối chiếu phạm vi báo giá)
    notable: bool = False
    summary: list[str] = []
    disclaimer: str = ""
    warnings: list[str] = []
