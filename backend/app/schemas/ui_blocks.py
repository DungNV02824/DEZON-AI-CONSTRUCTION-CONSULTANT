"""Các khối UI có cấu trúc trả về frontend (đặc tả mục 8.1).

Nguyên tắc 2.1: khối UI được dựng từ JSON kết quả tool, KHÔNG từ văn bản LLM.
Frontend render theo trường `type` (discriminated union).
"""
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.evidence import SourceRef, VerificationStatus
from app.schemas.estimate import BudgetAdjustmentResult, EstimateResult
from app.schemas.matching import ComparisonMatrix, ContractorProfile, MatchResult


class QuickReply(BaseModel):
    label: str  # chữ trên nút
    value: str  # nội dung gửi lên như tin nhắn của khách khi bấm


class QuickRepliesBlock(BaseModel):
    type: Literal["quick_replies"] = "quick_replies"
    options: list[QuickReply]
    allow_free_text: bool = True  # hiển thị lựa chọn "Nhập thông tin khác"


class SummaryItem(BaseModel):
    label: str
    value: str


class SummaryConfirmBlock(BaseModel):
    type: Literal["summary_confirm"] = "summary_confirm"
    items: list[SummaryItem]
    assumptions: list[str] = []
    confirm: QuickReply = QuickReply(label="Đúng rồi", value="Đúng rồi, tính khái toán giúp tôi")
    edit: QuickReply = QuickReply(label="Sửa lại", value="Tôi muốn sửa lại thông tin")


class EstimateTableBlock(BaseModel):
    type: Literal["estimate_table"] = "estimate_table"
    data: EstimateResult


class BudgetOptionsBlock(BaseModel):
    type: Literal["budget_options"] = "budget_options"
    data: BudgetAdjustmentResult


class ContractorListBlock(BaseModel):
    type: Literal["contractor_list"] = "contractor_list"
    data: MatchResult


class ComparisonTableBlock(BaseModel):
    type: Literal["comparison_table"] = "comparison_table"
    data: ComparisonMatrix


class ContractorDetailBlock(BaseModel):
    type: Literal["contractor_detail"] = "contractor_detail"
    data: ContractorProfile
    estimate_session_id: UUID | None = None


class SourceBadge(BaseModel):
    label: str  # vd "Dezon"
    status: VerificationStatus
    source: SourceRef


class SourceBadgesBlock(BaseModel):
    type: Literal["source_badges"] = "source_badges"
    badges: list[SourceBadge]


class QuoteFormContractor(BaseModel):
    id: int
    name: str


class QuoteRequestFormBlock(BaseModel):
    type: Literal["quote_request_form"] = "quote_request_form"
    contractors: list[QuoteFormContractor]
    estimate_session_id: UUID | None = None


UIBlock = Annotated[
    QuickRepliesBlock | SummaryConfirmBlock | EstimateTableBlock | BudgetOptionsBlock | ContractorListBlock
    | ComparisonTableBlock | ContractorDetailBlock | SourceBadgesBlock | QuoteRequestFormBlock,
    Field(discriminator="type"),
]
