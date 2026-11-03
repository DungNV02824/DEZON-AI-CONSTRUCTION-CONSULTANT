"""ORM ánh xạ 1-1 với DDL trong migrations/versions/0001_initial.py (đặc tả mục 4.2).

Migration là nguồn sự thật cho schema; các model ở đây chỉ để truy vấn/ghi.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

VerificationStatusT = ENUM("verified", "on_dezon", "reference", "unavailable",
                           name="verification_status", create_type=False)
SourceTypeT = ENUM("dezon_api", "contractor_verified", "crawl_public", "dezon_editorial", "csv_import",
                   name="source_type", create_type=False)
BuildingTypeT = ENUM("nha_pho", "biet_thu", "can_ho", "van_phong", "khach_san", "khac",
                     name="building_type", create_type=False)
WorkScopeT = ENUM("xay_tho", "hoan_thien", "tron_goi", "noi_that", name="work_scope", create_type=False)
QualityTierT = ENUM("tiet_kiem", "trung_binh", "kha", "cao_cap", name="quality_tier", create_type=False)


class Base(DeclarativeBase):
    pass


class DataSource(Base):
    __tablename__ = "data_sources"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_type: Mapped[str] = mapped_column(SourceTypeT)
    url: Mapped[str | None] = mapped_column(Text)
    label: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(VerificationStatusT, server_default="on_dezon")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_hash: Mapped[str | None] = mapped_column(Text)


class Contractor(Base):
    __tablename__ = "contractors"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    external_id: Mapped[str | None] = mapped_column(Text, unique=True)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    badge: Mapped[str | None] = mapped_column(Text)
    province: Mapped[str | None] = mapped_column(Text)
    founded_year: Mapped[int | None] = mapped_column(Integer)
    years_experience: Mapped[int | None] = mapped_column(Integer)
    staff_workers: Mapped[int | None] = mapped_column(Integer)
    staff_office: Mapped[int | None] = mapped_column(Integer)
    rough_price_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    rough_price_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    finish_price_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    finish_price_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    typical_project_value: Mapped[Decimal | None] = mapped_column(Numeric(16, 0))
    dezon_project_count: Mapped[int | None] = mapped_column(Integer)
    has_warranty_info: Mapped[bool | None] = mapped_column(Boolean)
    warranty_text: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    profile_url: Mapped[str] = mapped_column(Text)
    profile_completeness: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id"))
    field_sources: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    source: Mapped[DataSource | None] = relationship()
    capabilities: Mapped[list["ContractorCapability"]] = relationship(
        back_populates="contractor", cascade="all, delete-orphan")
    project_links: Mapped[list["ProjectContractor"]] = relationship(back_populates="contractor")


class ContractorCapability(Base):
    __tablename__ = "contractor_capabilities"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    contractor_id: Mapped[int] = mapped_column(ForeignKey("contractors.id", ondelete="CASCADE"))
    building_type: Mapped[str | None] = mapped_column(BuildingTypeT)
    work_scope: Mapped[str | None] = mapped_column(WorkScopeT)
    province: Mapped[str | None] = mapped_column(Text)
    district: Mapped[str | None] = mapped_column(Text)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id"))

    contractor: Mapped[Contractor] = relationship(back_populates="capabilities")


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    external_id: Mapped[str | None] = mapped_column(Text, unique=True)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    building_type: Mapped[str | None] = mapped_column(BuildingTypeT)
    province: Mapped[str | None] = mapped_column(Text)
    district: Mapped[str | None] = mapped_column(Text)
    floors: Mapped[int | None] = mapped_column(Integer)
    land_area_m2: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    floor_area_m2: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    project_url: Mapped[str] = mapped_column(Text)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    source: Mapped[DataSource | None] = relationship()
    images: Mapped[list["ProjectImage"]] = relationship(
        back_populates="project", order_by="ProjectImage.sort_order", cascade="all, delete-orphan")
    contractor_links: Mapped[list["ProjectContractor"]] = relationship(back_populates="project")


class ProjectContractor(Base):
    __tablename__ = "project_contractors"
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    contractor_id: Mapped[int] = mapped_column(ForeignKey("contractors.id", ondelete="CASCADE"),
                                               primary_key=True)
    role: Mapped[str] = mapped_column(Text, primary_key=True)  # thi_cong | thiet_ke | noi_that

    project: Mapped[Project] = relationship(back_populates="contractor_links")
    contractor: Mapped[Contractor] = relationship(back_populates="project_links")


class ProjectImage(Base):
    __tablename__ = "project_images"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(Text)
    caption: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, server_default="0")

    project: Mapped[Project] = relationship(back_populates="images")


class ConstructionRate(Base):
    __tablename__ = "construction_rates"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    building_type: Mapped[str] = mapped_column(BuildingTypeT)
    province: Mapped[str] = mapped_column(Text)  # '*' = toàn quốc
    work_scope: Mapped[str] = mapped_column(WorkScopeT)
    quality_tier: Mapped[str] = mapped_column(QualityTierT, server_default="trung_binh")
    price_min: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    price_max: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"))

    source: Mapped[DataSource] = relationship()


class EstimateRule(Base):
    __tablename__ = "estimate_rules"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(Text)
    group_name: Mapped[str] = mapped_column(Text)
    label_vi: Mapped[str] = mapped_column(Text)
    coefficient: Mapped[Decimal] = mapped_column(Numeric(5, 3))
    base: Mapped[str] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, server_default="false")
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    approved: Mapped[bool] = mapped_column(Boolean, server_default="false")
    source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id"))

    source: Mapped[DataSource | None] = relationship()


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          server_default=func.gen_random_uuid())
    user_id: Mapped[str | None] = mapped_column(Text)
    slots: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    stage: Mapped[str] = mapped_column(Text, server_default="collect")  # collect|estimate|match|present
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", order_by="ChatMessage.id", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(Text)  # user | assistant | tool
    content: Mapped[str | None] = mapped_column(Text)
    tool_name: Mapped[str | None] = mapped_column(Text)
    tool_payload: Mapped[dict | list | None] = mapped_column(JSONB)
    ui_blocks: Mapped[list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[ChatSession] = relationship(back_populates="messages")


class EstimateSession(Base):
    __tablename__ = "estimate_sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          server_default=func.gen_random_uuid())
    chat_session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chat_sessions.id"))
    input: Mapped[dict] = mapped_column(JSONB)
    rules_version: Mapped[int] = mapped_column(Integer)
    rate_id: Mapped[int | None] = mapped_column(ForeignKey("construction_rates.id"))
    converted_area_m2: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    cost_min: Mapped[Decimal | None] = mapped_column(Numeric(16, 0))
    cost_max: Mapped[Decimal | None] = mapped_column(Numeric(16, 0))
    assumptions: Mapped[dict | list] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    items: Mapped[list["EstimateItemRow"]] = relationship(
        back_populates="estimate_session", order_by="EstimateItemRow.id", cascade="all, delete-orphan")
    rate: Mapped[ConstructionRate | None] = relationship()


class EstimateItemRow(Base):
    __tablename__ = "estimate_items"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    estimate_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("estimate_sessions.id", ondelete="CASCADE"))
    rule_code: Mapped[str] = mapped_column(Text)
    label_vi: Mapped[str] = mapped_column(Text)
    area_m2: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    coefficient: Mapped[Decimal] = mapped_column(Numeric(5, 3))
    converted_m2: Mapped[Decimal] = mapped_column(Numeric(10, 2))

    estimate_session: Mapped[EstimateSession] = relationship(back_populates="items")


class QuoteRequest(Base):
    __tablename__ = "quote_requests"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          server_default=func.gen_random_uuid())
    estimate_session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("estimate_sessions.id"))
    contractor_id: Mapped[int | None] = mapped_column(ForeignKey("contractors.id"))
    customer_name: Mapped[str | None] = mapped_column(Text)
    customer_phone: Mapped[str | None] = mapped_column(Text)
    customer_email: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="new")  # new|sent|quoted|won|lost
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ContractorQuote(Base):
    __tablename__ = "contractor_quotes"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    quote_request_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("quote_requests.id"))
    contractor_id: Mapped[int | None] = mapped_column(ForeignKey("contractors.id"))
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 0))
    price_per_m2: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    scope_text: Mapped[str | None] = mapped_column(Text)
    file_url: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MatchingWeights(Base):
    __tablename__ = "matching_weights"
    version: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    weights: Mapped[dict] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="false")
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RecommendationLog(Base):
    __tablename__ = "recommendation_logs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chat_sessions.id"))
    estimate_session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("estimate_sessions.id"))
    weights_version: Mapped[int | None] = mapped_column(ForeignKey("matching_weights.version"))
    criteria: Mapped[dict] = mapped_column(JSONB)
    results: Mapped[list] = mapped_column(JSONB)
    clicked_contractor_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    entity_type: Mapped[str] = mapped_column(Text)  # contractor | project | article
    entity_id: Mapped[int] = mapped_column(BigInteger)
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GuardrailViolation(Base):
    __tablename__ = "guardrail_violations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    rule: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProvinceNeighbor(Base):
    __tablename__ = "province_neighbors"
    province: Mapped[str] = mapped_column(Text, primary_key=True)
    neighbor: Mapped[str] = mapped_column(Text, primary_key=True)


class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    event: Mapped[str] = mapped_column(Text)
    props: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ContactRequest(Base):
    """Yêu cầu liên hệ (migration 0003): khách để lại họ tên + SĐT để nhân viên Dezon gọi lại."""
    __tablename__ = "contact_requests"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          server_default=func.gen_random_uuid())
    code: Mapped[str] = mapped_column(Text, unique=True)
    customer_name: Mapped[str] = mapped_column(Text)
    customer_phone: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    chat_session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chat_sessions.id", ondelete="SET NULL"))
    contractor_id: Mapped[int | None] = mapped_column(ForeignKey("contractors.id", ondelete="SET NULL"))
    source: Mapped[str | None] = mapped_column(Text)  # composer | contractor_card | contractor_detail
    page_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="new")  # new | contacted | done
    handled_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

    contractor: Mapped[Contractor | None] = relationship()
    chat_session: Mapped[ChatSession | None] = relationship()
