"""Yêu cầu báo giá & báo giá nhà thầu cho nhân viên (V2 — quotes; docs/API_CONTRACT.md "Admin — yêu cầu báo giá").

Chỉ dùng ở trang admin (`/api/admin/*`, cần X-Admin-Token): nhân viên xem yêu cầu báo giá khách gửi, nhập báo giá
nhà thầu gửi về (Zalo, PDF, Excel — chỉ lưu link file gốc, không tải file lên) và xem phân tích so với khái toán
(engine thuần `app/engines/quote_analysis.py`). Không có endpoint công khai cho khách ở vòng này.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy import Text, cast, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    AnalyticsEvent,
    ChatSession,
    Contractor,
    ContractorQuote,
    EstimateSession,
    QuoteRequest,
)
from app.engines.quote_analysis import compare_quote
from app.evidence import EvidenceValue, VerificationStatus, make_evidence
from app.formatting import format_m2, format_vnd_range
from app.schemas.common import BUILDING_TYPE_VI, WORK_SCOPE_VI
from app.schemas.estimate import EstimateItem, EstimateResult
from app.schemas.quote_analysis import QUOTE_GROUP_VI, QuoteAnalysisResult, QuoteData, QuoteLine
from app.services import estimate_service
from app.services.admin_service import AdminError
from app.services.contact_service import ContractorRef, project_summary

VN_TZ = timezone(timedelta(hours=7), "Asia/Ho_Chi_Minh")  # Việt Nam không có giờ mùa hè
CODE_PREFIX = "DZ-"
MAX_LINES = 100
RECALCULATED_WARNING = ("Bản ghi khái toán cũ không lưu snapshot nên khái toán được tính lại với hệ số hiện hành; "
                        "con số có thể khác số khách đã thấy.")
NO_ESTIMATE_MESSAGE = "Yêu cầu báo giá này chưa gắn khái toán nên chưa phân tích được."


class QuoteRequestStatus(StrEnum):
    new = "new"
    sent = "sent"
    quoted = "quoted"
    won = "won"
    lost = "lost"


# ------------------------------------------------------------------ mô hình vào/ra
def _blank_to_none(v: Any) -> Any:
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


class ContractorQuoteIn(BaseModel):
    """Báo giá nhà thầu do nhân viên nhập. PUT thay toàn bộ (trường không gửi -> giá trị mặc định)."""
    total_amount: int | None = Field(default=None, gt=0, le=10**15)  # VND
    price_per_m2: int | None = Field(default=None, gt=0, le=10**11)  # VND/m²
    converted_area_m2: float | None = Field(default=None, gt=0, le=10**7)  # diện tích quy đổi theo nhà thầu
    scope_text: str | None = Field(default=None, max_length=4000)
    file_url: str | None = Field(default=None, max_length=1000)  # link file gốc (Drive/Zalo...), không tải file
    received_at: date | None = None  # ngày nhận báo giá; bỏ trống = hôm nay
    verified: bool = False  # đã đối chiếu file gốc -> "Đã xác minh", ngược lại "Dữ liệu tham khảo"
    entered_by: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=2000)
    lines: list[QuoteLine] = Field(default_factory=list, max_length=MAX_LINES)

    @field_validator("scope_text", "file_url", "entered_by", "note", mode="before")
    @classmethod
    def _strip(cls, v: Any) -> Any:
        return _blank_to_none(v)

    @field_validator("file_url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r"^https?://\S+$", v, re.IGNORECASE):
            raise ValueError("Link file gốc phải là địa chỉ http:// hoặc https://")
        return v

    @field_validator("lines")
    @classmethod
    def _drop_empty_lines(cls, v: list[QuoteLine]) -> list[QuoteLine]:
        # Dòng điền sẵn từ khái toán mà nhân viên không nhập số nào của nhà thầu -> bỏ (không coi là "có dòng").
        return [ln for ln in v if any(x is not None for x in (ln.area_m2, ln.coefficient, ln.converted_m2,
                                                              ln.amount_vnd))]

    @model_validator(mode="after")
    def _need_number(self) -> ContractorQuoteIn:
        if self.total_amount is None and self.price_per_m2 is None:
            raise ValueError("Cần nhập tổng tiền hoặc đơn giá/m² của báo giá.")
        return self


class ContractorQuoteOut(BaseModel):
    id: int
    quote_request_id: UUID | None = None
    contractor: ContractorRef | None = None
    total_amount: int | None = None
    price_per_m2: int | None = None
    converted_area_m2: float | None = None
    scope_text: str | None = None
    file_url: str | None = None
    received_at: datetime
    received_date: date  # ngày nhận theo giờ Việt Nam
    verified: bool = False
    status: VerificationStatus = VerificationStatus.reference  # verified | reference
    entered_by: str | None = None
    note: str | None = None
    lines: list[QuoteLine] = []


class QuoteRequestOut(BaseModel):
    id: UUID
    code: str  # mã khách nhận khi gửi yêu cầu (DZ-XXXXXX, chung cho các nhà thầu gửi cùng lúc)
    created_at: datetime
    status: str
    customer_name: str | None = None
    customer_phone: str | None = None
    customer_email: str | None = None
    note: str | None = None
    contractor: ContractorRef | None = None
    estimate_session_id: UUID | None = None
    estimate_cost: str | None = None  # "2,058–2,352 tỷ" hoặc "Chưa có dữ liệu"; None = không gắn khái toán
    project: str | None = None
    chat_session_id: UUID | None = None
    quote_count: int = 0


class QuoteRequestList(BaseModel):
    items: list[QuoteRequestOut]
    total: int
    new_count: int


class EstimateSummary(BaseModel):
    estimate_session_id: UUID
    project: str | None = None
    cost: EvidenceValue
    rate: EvidenceValue
    converted_area: EvidenceValue
    items: list[EstimateItem] = []
    exclusions: list[str] = []
    warnings: list[str] = []
    disclaimer: str = ""


class QuoteRequestDetail(BaseModel):
    request: QuoteRequestOut
    estimate: EstimateSummary | None = None
    quotes: list[ContractorQuoteOut] = []
    # Dòng hạng mục điền sẵn cho form "Nhập báo giá" (nhóm, diễn giải, mã hệ số, diện tích, hệ số của khái toán).
    line_template: list[QuoteLine] = []


# ------------------------------------------------------------------ mã DZ-xxxxxx
def _code_of(qid: UUID) -> str:
    return CODE_PREFIX + qid.hex[:6].upper()


def _batch_key(r: QuoteRequest) -> tuple[Any, ...]:
    return (r.created_at, r.customer_phone, r.estimate_session_id)


def _codes(db: Session, rows: list[QuoteRequest]) -> dict[UUID, str]:
    """Mã khách nhận = "DZ-" + 6 ký tự hex đầu của id yêu cầu ĐẦU TIÊN trong lượt gửi (quote_service). Bảng không lưu
    mã: tìm các yêu cầu cùng lượt (cùng created_at — cùng transaction —, SĐT, khái toán) rồi đối chiếu sự kiện
    analytics 'quote_request_created' (props.code, contractor_ids). Không đối chiếu được -> mã theo id của chính nó."""
    if not rows:
        return {}
    keys = {_batch_key(r) for r in rows}
    sib = db.scalars(select(QuoteRequest).where(QuoteRequest.created_at.in_({k[0] for k in keys}))).all()
    batches: dict[tuple[Any, ...], list[QuoteRequest]] = {}
    for s in sib:
        if _batch_key(s) in keys:
            batches.setdefault(_batch_key(s), []).append(s)
    candidates = {_code_of(s.id) for b in batches.values() for s in b}
    props = db.scalars(select(AnalyticsEvent.props).where(
        AnalyticsEvent.event == "quote_request_created",
        AnalyticsEvent.props["code"].astext.in_(candidates))).all()
    by_code = {p.get("code"): p for p in props if isinstance(p, dict)}
    out: dict[UUID, str] = {}
    for r in rows:
        est = str(r.estimate_session_id) if r.estimate_session_id else None
        found = None
        for s in batches.get(_batch_key(r), []):
            p = by_code.get(_code_of(s.id))
            if p and r.contractor_id in (p.get("contractor_ids") or []) and (p.get("estimate_session_id") or None) == est:
                found = _code_of(s.id)
                break
        out[r.id] = found or _code_of(r.id)
    return out


def _ids_for_code(db: Session, code: str) -> list[UUID] | None:
    """Các yêu cầu cùng lượt gửi với mã DZ-xxxxxx (hoặc 6 ký tự hex). Mã sai dạng -> None."""
    hex6 = code.strip().upper().removeprefix(CODE_PREFIX).lower()
    if not re.fullmatch(r"[0-9a-f]{6}", hex6):
        return None
    heads = db.scalars(select(QuoteRequest).where(
        func.replace(cast(QuoteRequest.id, Text), "-", "").startswith(hex6))).all()
    ids: list[UUID] = []
    for h in heads:
        ids.extend(db.scalars(select(QuoteRequest.id).where(
            QuoteRequest.created_at == h.created_at,
            QuoteRequest.customer_phone.is_not_distinct_from(h.customer_phone),
            QuoteRequest.estimate_session_id.is_not_distinct_from(h.estimate_session_id))).all())
    return list(dict.fromkeys(ids))


# ------------------------------------------------------------------ đọc
def _input_summary(raw: dict | None) -> str | None:
    if not isinstance(raw, dict):
        return None
    parts: list[str] = []
    if raw.get("building_type"):
        parts.append(BUILDING_TYPE_VI.get(str(raw["building_type"]), str(raw["building_type"])))
    if raw.get("province"):
        parts.append(str(raw["province"]))
    land = raw.get("land_area_m2")
    if not land and raw.get("land_width_m") and raw.get("land_length_m"):
        land = float(raw["land_width_m"]) * float(raw["land_length_m"])
    if land:
        parts.append(f"{format_m2(float(land))} đất")
    if raw.get("floors"):
        parts.append(f"{raw['floors']} tầng")
    if raw.get("work_scope"):
        parts.append(WORK_SCOPE_VI.get(str(raw["work_scope"]), str(raw["work_scope"])))
    return " · ".join(parts) or None


def _request_outs(db: Session, rows: list[QuoteRequest]) -> list[QuoteRequestOut]:
    codes = _codes(db, rows)
    cids = {r.contractor_id for r in rows if r.contractor_id}
    contractors = {c.id: c for c in db.scalars(select(Contractor).where(Contractor.id.in_(cids)))} if cids else {}
    eids = {r.estimate_session_id for r in rows if r.estimate_session_id}
    ests = {e.id: e for e in db.scalars(select(EstimateSession).where(EstimateSession.id.in_(eids)))} if eids else {}
    sids = {e.chat_session_id for e in ests.values() if e.chat_session_id}
    chats = {c.id: c for c in db.scalars(select(ChatSession).where(ChatSession.id.in_(sids)))} if sids else {}
    rids = [r.id for r in rows]
    counts = dict(db.execute(select(ContractorQuote.quote_request_id, func.count())
                             .where(ContractorQuote.quote_request_id.in_(rids))
                             .group_by(ContractorQuote.quote_request_id)).all()) if rids else {}
    out: list[QuoteRequestOut] = []
    for r in rows:
        c = contractors.get(r.contractor_id) if r.contractor_id else None
        est = ests.get(r.estimate_session_id) if r.estimate_session_id else None
        chat = chats.get(est.chat_session_id) if est is not None and est.chat_session_id else None
        cost = None
        if est is not None:
            cost = format_vnd_range(est.cost_min, est.cost_max)
        project = (project_summary(chat.slots) if chat is not None else None) or (
            _input_summary(est.input) if est is not None else None)
        out.append(QuoteRequestOut(
            id=r.id, code=codes[r.id], created_at=r.created_at, status=r.status, customer_name=r.customer_name,
            customer_phone=r.customer_phone, customer_email=r.customer_email, note=r.note,
            contractor=ContractorRef(id=c.id, name=c.name) if c is not None else None,
            estimate_session_id=r.estimate_session_id, estimate_cost=cost, project=project,
            chat_session_id=chat.id if chat is not None else None, quote_count=int(counts.get(r.id, 0))))
    return out


def list_quote_requests(db: Session, status: QuoteRequestStatus | None = None, code: str | None = None,
                        limit: int = 50, offset: int = 0) -> QuoteRequestList:
    """Mới nhất trước. `total` theo bộ lọc; `new_count` = số yêu cầu trạng thái 'new' (không phụ thuộc bộ lọc).
    `code`: mã DZ-xxxxxx khách nhận (hoặc 6 ký tự hex) -> các yêu cầu của lượt gửi đó; mã sai dạng -> rỗng."""
    conds = []
    if status is not None:
        conds.append(QuoteRequest.status == status.value)
    if code and code.strip():
        ids = _ids_for_code(db, code)
        if not ids:
            new_count = db.scalar(select(func.count()).select_from(QuoteRequest)
                                  .where(QuoteRequest.status == QuoteRequestStatus.new.value)) or 0
            return QuoteRequestList(items=[], total=0, new_count=new_count)
        conds.append(QuoteRequest.id.in_(ids))
    rows = list(db.scalars(select(QuoteRequest).where(*conds)
                           .order_by(QuoteRequest.created_at.desc(), QuoteRequest.id)
                           .limit(limit).offset(offset)).all())
    total = db.scalar(select(func.count()).select_from(QuoteRequest).where(*conds)) or 0
    new_count = db.scalar(select(func.count()).select_from(QuoteRequest)
                          .where(QuoteRequest.status == QuoteRequestStatus.new.value)) or 0
    return QuoteRequestList(items=_request_outs(db, rows), total=total, new_count=new_count)


def _vn_date(dt: datetime) -> date:
    return (dt.replace(tzinfo=VN_TZ) if dt.tzinfo is None else dt.astimezone(VN_TZ)).date()


def _lines(raw: Any) -> list[QuoteLine]:
    out: list[QuoteLine] = []
    for item in raw if isinstance(raw, list) else []:
        try:
            out.append(QuoteLine.model_validate(item))
        except ValidationError:
            continue  # dòng hỏng (sửa tay trong DB) -> bỏ qua, không làm hỏng cả báo giá
    return out


def _quote_out(db: Session, q: ContractorQuote) -> ContractorQuoteOut:
    c = db.get(Contractor, q.contractor_id) if q.contractor_id else None
    return ContractorQuoteOut(
        id=q.id, quote_request_id=q.quote_request_id,
        contractor=ContractorRef(id=c.id, name=c.name) if c is not None else None,
        total_amount=int(q.total_amount) if q.total_amount is not None else None,
        price_per_m2=int(q.price_per_m2) if q.price_per_m2 is not None else None,
        converted_area_m2=float(q.converted_area_m2) if q.converted_area_m2 is not None else None,
        scope_text=q.scope_text, file_url=q.file_url, received_at=q.received_at,
        received_date=_vn_date(q.received_at), verified=bool(q.verified),
        status=VerificationStatus.verified if q.verified else VerificationStatus.reference,
        entered_by=q.entered_by, note=q.note, lines=_lines(q.lines))


def _get_request(db: Session, request_id: UUID) -> QuoteRequest:
    qr = db.get(QuoteRequest, request_id)
    if qr is None:
        raise AdminError(404, "Không tìm thấy yêu cầu báo giá.")
    return qr


def _get_quote(db: Session, quote_id: int) -> ContractorQuote:
    q = db.get(ContractorQuote, quote_id)
    if q is None:
        raise AdminError(404, "Không tìm thấy báo giá nhà thầu.")
    return q


def _estimate_for(db: Session, qr: QuoteRequest) -> tuple[EstimateResult, list[str]] | None:
    if qr.estimate_session_id is None:
        return None
    row = db.get(EstimateSession, qr.estimate_session_id)
    if row is None:
        return None
    res = estimate_service.get_estimate(db, row.id)
    if res is None:
        return None
    has_snapshot = isinstance(row.assumptions, dict) and bool(row.assumptions.get("snapshot"))
    return res, ([] if has_snapshot else [RECALCULATED_WARNING])


def get_quote_request(db: Session, request_id: UUID) -> QuoteRequestDetail:
    qr = _get_request(db, request_id)
    req = _request_outs(db, [qr])[0]
    estimate = None
    template: list[QuoteLine] = []
    found = _estimate_for(db, qr)
    if found is not None:
        res, extra = found
        row = db.get(EstimateSession, res.estimate_session_id)
        a = float(res.converted_area_m2)
        estimate = EstimateSummary(
            estimate_session_id=res.estimate_session_id,  # type: ignore[arg-type]
            project=_input_summary(row.input if row is not None else None),
            cost=res.cost, rate=res.rate,
            converted_area=make_evidence(a, format_m2(a), status=res.rules_status) if a > 0
            else EvidenceValue.unavailable(),
            items=res.items, exclusions=res.exclusions, warnings=[*res.warnings, *extra], disclaimer=res.disclaimer)
        template = [QuoteLine(group=it.group_name if it.group_name in QUOTE_GROUP_VI else "khac", label=it.label_vi,
                              rule_code=it.rule_code, area_m2=it.area_m2, coefficient=it.coefficient)
                    for it in res.items]
    quotes = db.scalars(select(ContractorQuote).where(ContractorQuote.quote_request_id == qr.id)
                        .order_by(ContractorQuote.received_at, ContractorQuote.id)).all()
    return QuoteRequestDetail(request=req, estimate=estimate, quotes=[_quote_out(db, q) for q in quotes],
                              line_template=template)


# ------------------------------------------------------------------ ghi
def _received(d: date | None) -> datetime | None:
    return datetime(d.year, d.month, d.day, 12, tzinfo=VN_TZ) if d is not None else None


def _apply(q: ContractorQuote, data: ContractorQuoteIn) -> None:
    q.total_amount = data.total_amount  # type: ignore[assignment]
    q.price_per_m2 = data.price_per_m2  # type: ignore[assignment]
    q.converted_area_m2 = data.converted_area_m2  # type: ignore[assignment]
    q.scope_text = data.scope_text
    q.file_url = data.file_url
    q.verified = data.verified
    q.entered_by = data.entered_by
    q.note = data.note
    q.lines = [ln.model_dump(mode="json", exclude_none=True) for ln in data.lines]
    received = _received(data.received_at)
    if received is not None:
        q.received_at = received


def create_contractor_quote(db: Session, request_id: UUID, data: ContractorQuoteIn) -> ContractorQuoteOut:
    """Lưu báo giá cho một yêu cầu báo giá (nhà thầu = nhà thầu của yêu cầu). Yêu cầu đang 'new'/'sent' -> 'quoted'
    ('won'/'lost' giữ nguyên). Ghi analytics 'contractor_quote_recorded'. Commit."""
    qr = _get_request(db, request_id)
    q = ContractorQuote(quote_request_id=qr.id, contractor_id=qr.contractor_id)
    _apply(q, data)
    db.add(q)
    if qr.status in (QuoteRequestStatus.new.value, QuoteRequestStatus.sent.value):
        qr.status = QuoteRequestStatus.quoted.value
    db.flush()
    db.add(AnalyticsEvent(event="contractor_quote_recorded",
                          props={"quote_request_id": str(qr.id), "contractor_quote_id": q.id,
                                 "verified": data.verified, "lines": len(data.lines)}))
    db.commit()
    db.refresh(q)
    return _quote_out(db, q)


def update_contractor_quote(db: Session, quote_id: int, data: ContractorQuoteIn) -> ContractorQuoteOut:
    q = _get_quote(db, quote_id)
    _apply(q, data)
    db.commit()
    db.refresh(q)
    return _quote_out(db, q)


def delete_contractor_quote(db: Session, quote_id: int) -> None:
    """Xóa báo giá. Yêu cầu đang 'quoted' mà không còn báo giá nào -> về 'new'."""
    q = _get_quote(db, quote_id)
    qr = db.get(QuoteRequest, q.quote_request_id) if q.quote_request_id else None
    db.delete(q)
    db.flush()
    if qr is not None and qr.status == QuoteRequestStatus.quoted.value:
        left = db.scalar(select(func.count()).select_from(ContractorQuote)
                         .where(ContractorQuote.quote_request_id == qr.id)) or 0
        if not left:
            qr.status = QuoteRequestStatus.new.value
    db.commit()


# ------------------------------------------------------------------ phân tích
def _quote_data(name: str | None, data: ContractorQuoteIn | ContractorQuote, quote_id: int | None = None,
                received: date | None = None) -> QuoteData:
    lines = data.lines if isinstance(data, ContractorQuoteIn) else _lines(data.lines)
    return QuoteData(
        quote_id=quote_id, contractor_name=name,
        total_amount=int(data.total_amount) if data.total_amount is not None else None,
        price_per_m2=int(data.price_per_m2) if data.price_per_m2 is not None else None,
        converted_area_m2=float(data.converted_area_m2) if data.converted_area_m2 is not None else None,
        scope_text=data.scope_text, file_url=data.file_url, received_at=received, verified=bool(data.verified),
        lines=lines)


def _analyze(db: Session, qr: QuoteRequest, quote: QuoteData) -> QuoteAnalysisResult:
    found = _estimate_for(db, qr)
    if found is None:
        raise AdminError(409, NO_ESTIMATE_MESSAGE)
    res, extra = found
    out = compare_quote(res, quote)
    if extra:
        out = out.model_copy(update={"warnings": [*extra, *out.warnings]})
    return out


def analyze_contractor_quote(db: Session, quote_id: int) -> QuoteAnalysisResult:
    """Phân tích một báo giá đã lưu. Không gắn yêu cầu/khái toán -> 409."""
    q = _get_quote(db, quote_id)
    qr = db.get(QuoteRequest, q.quote_request_id) if q.quote_request_id else None
    if qr is None:
        raise AdminError(409, NO_ESTIMATE_MESSAGE)
    c = db.get(Contractor, q.contractor_id) if q.contractor_id else None
    return _analyze(db, qr, _quote_data(c.name if c else None, q, q.id, _vn_date(q.received_at)))


def preview_analysis(db: Session, request_id: UUID, data: ContractorQuoteIn) -> QuoteAnalysisResult:
    """Xem trước phân tích từ dữ liệu form (không lưu)."""
    qr = _get_request(db, request_id)
    c = db.get(Contractor, qr.contractor_id) if qr.contractor_id else None
    received = data.received_at or datetime.now(VN_TZ).date()
    return _analyze(db, qr, _quote_data(c.name if c else None, data, None, received))

