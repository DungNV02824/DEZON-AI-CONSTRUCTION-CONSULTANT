"""Hợp đồng dữ liệu của Matching Engine và hồ sơ nhà thầu (đặc tả mục 7, 8)."""
from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field

from app.evidence import EvidenceValue, SourceRef
from app.schemas.common import BuildingType, WorkScope

FIT_HIGH = "Phù hợp cao"
FIT_OK = "Phù hợp"
FIT_REVIEW = "Cần đánh giá thêm"


class MatchCriteria(BaseModel):
    province: str
    district: str | None = None
    building_type: BuildingType
    work_scope: WorkScope | None = None
    budget_vnd: int | None = None
    floors: int | None = None
    converted_area_m2: float | None = None  # lấy từ estimate nếu có estimate_session_id
    estimate_session_id: UUID | None = None
    limit: int = Field(default=5, ge=3, le=5)


class CriterionScore(BaseModel):
    key: str  # location | building_type | budget_fit | experience | similar_projects | profile_quality
    label_vi: str
    score: float  # 0..1
    weight: float  # 0..1
    weighted: float  # score × weight × 100
    note: str  # giải thích ngắn cách tính


class Reason(BaseModel):
    text: str
    evidence: str  # khóa trỏ tới một EvidenceValue trong ContractorCard.evidence, vd "rough_price"


class Caution(BaseModel):
    text: str
    evidence: str | None = None


class ImageRef(BaseModel):
    url: str
    caption: str | None = None
    project_slug: str | None = None


class ContractorCard(BaseModel):
    id: int
    slug: str
    name: str
    badge: str | None = None
    avatar_url: str | None = None
    profile_url: str  # link hồ sơ gốc trên dezon.vn
    profile_source: SourceRef | None = None
    cover_images: list[ImageRef] = []  # tối đa 3 ảnh dự án
    # Mọi trường số liệu là EvidenceValue. Khóa chuẩn:
    # province, building_types, years_experience, founded_year, staff, rough_price, finish_price,
    # typical_project_value, dezon_project_count, warranty, profile_completeness
    evidence: dict[str, EvidenceValue]


class MatchedContractor(BaseModel):
    rank: int
    score: int  # 0..100
    fit_label: str  # FIT_HIGH | FIT_OK | FIT_REVIEW
    breakdown: list[CriterionScore]
    reasons: list[Reason]  # ≥ 2, mỗi lý do trỏ tới evidence
    cautions: list[Caution]
    estimated_cost: EvidenceValue  # A_quy_doi × đơn giá nhà thầu (nếu có)
    relaxed_location: bool = False  # chỉ khớp do nới sang tỉnh lân cận
    contractor: ContractorCard


class MatchResult(BaseModel):
    recommendation_log_id: int | None = None
    weights_version: int
    criteria: MatchCriteria
    total_candidates: int  # số nhà thầu qua bộ lọc cứng
    relaxed_to_neighbors: bool = False
    results: list[MatchedContractor]
    notes: list[str] = []


class ProjectSummary(BaseModel):
    id: int
    slug: str
    name: str
    building_type: BuildingType | None = None
    province: str | None = None
    district: str | None = None
    floors: int | None = None
    status: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    project_url: str
    role: str | None = None  # vai trò của nhà thầu đang xem
    contractor_names: list[str] = []
    images: list[ImageRef] = []
    source: SourceRef | None = None


class ContractorProfile(BaseModel):
    card: ContractorCard
    description: EvidenceValue
    # Bảng thông tin (mục 8.3): founded_year, staff_workers, staff_office, rough_price, finish_price,
    # typical_project_value, dezon_project_count, warranty
    info: dict[str, EvidenceValue]
    projects: list[ProjectSummary]
    images: list[ImageRef]  # toàn bộ ảnh dự án (frontend hiển thị 3 + "+N")
    total_images: int
    chips: list[str] = ["Hồ sơ nhà thầu", "Dự án thực tế", "Đơn giá tham khảo"]


class ComparedContractor(BaseModel):
    id: int
    slug: str
    name: str
    profile_url: str
    avatar_url: str | None = None


class ComparisonRow(BaseModel):
    key: str  # area | dezon_project_count | experience | rough_price | finish_price | projects | warranty | fit
    label_vi: str
    cells: list[EvidenceValue]  # cùng thứ tự với ComparisonMatrix.contractors; ô thiếu = unavailable


class ComparisonMatrix(BaseModel):
    contractors: list[ComparedContractor]
    rows: list[ComparisonRow]


class OverviewContractor(BaseModel):
    """Một nhà thầu trong câu trả lời câu hỏi tự do (mục 8.4)."""
    card: ContractorCard
    has_projects_of_type: bool  # có công trình loại được hỏi trong danh sách dự án trên Dezon
    projects_of_type: list[ProjectSummary] = []  # kèm project_url để khách xem công trình thực tế
    # Tổng số công trình đúng loại có trang dự án (đủ, không bị cắt như projects_of_type) — để LLM/validator
    # truy được câu "N công trình <loại> có trang dự án".
    projects_of_type_count: int = 0
    profile_verified: bool = False  # hồ sơ có nguồn trạng thái verified


class ContractorOverview(BaseModel):
    """Tổng quan nhà thầu theo khu vực + loại công trình (mục 8.4). Mọi số là EvidenceValue."""
    province: str
    building_type: BuildingType
    contractor_count: EvidenceValue  # số nhà thầu phù hợp, vd "3 nhà thầu"
    contractors_with_projects: EvidenceValue  # số nhà thầu có công trình loại đó thực tế
    total_dezon_projects: EvidenceValue  # tổng "công trình ghi nhận trên Dezon" của các nhà thầu trên
    rough_price_range: EvidenceValue  # khoảng đơn giá xây thô tham khảo từ hồ sơ nhà thầu
    finish_price_range: EvidenceValue  # khoảng đơn giá hoàn thiện tham khảo
    with_warranty_info: EvidenceValue  # vd "1/3 nhà thầu có thông tin bảo hành"
    verified_profiles: EvidenceValue  # vd "0/3 hồ sơ đã xác minh"
    contractors: list[OverviewContractor]
    notes: list[str] = []
    # Số trang dự án đúng loại công trình được hỏi (vd "1 công trình biệt thự có trang dự án trên Dezon").
    # total_dezon_projects là tổng mọi loại công trình — không được diễn giải là số công trình loại được hỏi.
    projects_of_type_count: EvidenceValue = Field(default_factory=EvidenceValue.unavailable)
