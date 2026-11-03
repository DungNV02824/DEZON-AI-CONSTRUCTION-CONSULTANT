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


class QuestionStepBlock(BaseModel):
    """Thẻ câu hỏi từng bước khi thu thập thông tin (thay quick_replies cho câu hỏi slot).

    Chọn một dòng `options` / một `chips` -> gửi `value` như tin nhắn của khách; ô nhập tự do gửi chữ khách gõ.
    "Bỏ qua" gửi `skip_value` (chỉ khi `skippable`), "Trước"/‹ gửi `back_value` (None = bước đầu, nút bị tắt).
    """
    type: Literal["question_step"] = "question_step"
    question: str  # tiêu đề câu hỏi in đậm, vd "Diện tích công trình (m²)?"
    hint: str | None = None  # dòng gợi ý (frontend thêm biểu tượng 💡 phía trước)
    options: list[QuickReply] = []  # các dòng đánh số 1..n
    chips: list[QuickReply] = []  # lựa chọn nhanh dạng chip nhỏ (vd kích thước đất), có thể rỗng
    input_placeholder: str | None = None  # None = không có dòng nhập tự do
    step: int = 1  # vị trí câu hỏi hiện tại (bắt đầu từ 1)
    total: int = 1  # tổng số câu hỏi dự kiến cho thông tin hiện tại
    skippable: bool = False
    skip_value: str | None = None
    back_value: str | None = None
    slots: list[str] = []  # slot đang hỏi (thông tin tham khảo)


class SummaryItem(BaseModel):
    label: str
    value: str
    edit_value: str | None = None  # tin nhắn gửi khi khách bấm "Sửa" ở dòng này (None = không sửa riêng được)


class SummaryConfirmBlock(BaseModel):
    type: Literal["summary_confirm"] = "summary_confirm"
    items: list[SummaryItem]
    assumptions: list[str] = []
    confirm: QuickReply = QuickReply(label="Đúng rồi", value="Đúng rồi, tính khái toán giúp tôi")
    edit: QuickReply = QuickReply(label="Sửa lại", value="Tôi muốn sửa lại thông tin")
    title: str | None = None  # vd "Xác nhận thông tin — Nhà phố"
    confirm_label: str | None = None  # nút chính, vd "Tính ngay → xem bảng khái toán"
    confirm_value: str | None = None  # tin nhắn gửi khi bấm nút chính (được hiểu là xác nhận)
    more_label: str | None = None  # nút phụ "Cung cấp thêm thông tin..."; None = ẩn (không còn câu nào để hỏi thêm)
    more_value: str | None = None


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


class SuggestionsBlock(BaseModel):
    """Gợi ý câu hỏi tiếp theo theo ngữ cảnh (cuối lượt trả lời, khi luồng chính đã xong — app/ai/suggestions.py).

    Bấm một gợi ý -> gửi `value` như tin nhắn của khách. Chỉ lượt mới nhất hiển thị (như quick_replies).
    """
    type: Literal["suggestions"] = "suggestions"
    title: str = "Gợi ý cho anh/chị"
    options: list[QuickReply]


UIBlock = Annotated[
    QuickRepliesBlock | QuestionStepBlock | SummaryConfirmBlock | EstimateTableBlock | BudgetOptionsBlock
    | ContractorListBlock | ComparisonTableBlock | ContractorDetailBlock | SourceBadgesBlock | QuoteRequestFormBlock
    | SuggestionsBlock,
    Field(discriminator="type"),
]
