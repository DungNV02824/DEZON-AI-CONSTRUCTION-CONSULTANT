"""Chỉ số đo lường sau MVP (đặc tả mục 12) cho dashboard admin.

Mỗi chỉ số là một `Metric` có `value` (tỷ lệ 0..1, số lượt hoặc sai số tương đối), tử số/mẫu số khi có,
`sample_size` và `note`. Không đủ dữ liệu để tính -> `value=None` kèm ghi chú lý do (không tự điền số).
"""
from __future__ import annotations

import statistics
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    AnalyticsEvent,
    ChatMessage,
    ChatSession,
    Contractor,
    ContractorQuote,
    DataSource,
    EstimateSession,
    GuardrailViolation,
    QuoteRequest,
    RecommendationLog,
)

MAX_RANK = 5
NO_DATA_NOTE = "Chưa có dữ liệu để tính"

# Trường hồ sơ dùng để tính tỷ lệ "trường đã xác minh" (giá trị có nguồn data_sources.status = verified).
COVERAGE_FIELDS = ("founded_year", "years_experience", "staff_workers", "staff_office", "rough_price_min",
                   "rough_price_max", "finish_price_min", "finish_price_max", "typical_project_value",
                   "dezon_project_count", "has_warranty_info", "warranty_text", "description", "avatar_url")


class Metric(BaseModel):
    key: str
    label_vi: str
    value: float | None = None
    unit: str = "ratio"  # ratio (0..1, hiển thị %) | count | turns | relative_error
    numerator: float | None = None
    denominator: float | None = None
    sample_size: int = 0
    note: str = ""
    breakdown: list[dict[str, Any]] = Field(default_factory=list)


class MetricsReport(BaseModel):
    generated_at: datetime
    metrics: dict[str, Metric]
    totals: dict[str, int]


def _ratio(num: float, den: float) -> float | None:
    return round(num / den, 4) if den else None


def _count(db: Session, model, *conds) -> int:
    return int(db.scalar(select(func.count()).select_from(model).where(*conds)) or 0)


# ------------------------------------------------------------------ từng chỉ số
def estimate_completion_rate(db: Session) -> Metric:
    sessions = _count(db, ChatSession)
    with_estimate = int(db.scalar(select(func.count(func.distinct(EstimateSession.chat_session_id)))
                                  .where(EstimateSession.chat_session_id.is_not(None))) or 0)
    m = Metric(key="estimate_completion_rate", label_vi="Tỷ lệ hoàn thành khái toán",
               numerator=with_estimate, denominator=sessions, sample_size=sessions,
               value=_ratio(with_estimate, sessions))
    m.note = ("Số phiên chat có ít nhất một khái toán / số phiên chat đã bắt đầu."
              if sessions else f"{NO_DATA_NOTE}: chưa có phiên chat nào.")
    return m


def avg_turns_to_complete(db: Session) -> Metric:
    rows = db.scalars(select(AnalyticsEvent.props).where(AnalyticsEvent.event == "slots_completed")).all()
    turns: list[float] = []
    for p in rows:
        try:
            t = float((p or {}).get("turns"))
        except (TypeError, ValueError):
            continue
        if t >= 0:
            turns.append(t)
    m = Metric(key="avg_turns_to_complete", label_vi="Số lượt hỏi trung bình để đủ thông tin", unit="turns",
               sample_size=len(turns))
    if turns:
        m.value = round(statistics.fmean(turns), 2)
        m.note = (f"Trung bình props.turns của {len(turns)} sự kiện slots_completed; "
                  f"trung vị {statistics.median(turns):g}.")
    else:
        m.note = (f"{NO_DATA_NOTE}: chưa có sự kiện analytics 'slots_completed' kèm props.turns "
                  "(orchestrator ghi khi đủ thông tin bắt buộc).")
    return m


def estimate_accuracy(db: Session) -> tuple[Metric, Metric]:
    rows = db.execute(
        select(ContractorQuote.total_amount, EstimateSession.cost_min, EstimateSession.cost_max)
        .join(QuoteRequest, ContractorQuote.quote_request_id == QuoteRequest.id)
        .join(EstimateSession, QuoteRequest.estimate_session_id == EstimateSession.id)
    ).all()
    total_quotes = _count(db, ContractorQuote)
    usable = [(float(q), float(lo), float(hi)) for q, lo, hi in rows
              if q is not None and lo is not None and hi is not None and float(lo) + float(hi) > 0]
    within = sum(1 for q, lo, hi in usable if lo <= q <= hi)
    errors = [(q - (lo + hi) / 2) / ((lo + hi) / 2) for q, lo, hi in usable]
    skipped = total_quotes - len(usable)
    in_range = Metric(key="estimate_accuracy_in_range", label_vi="Độ chính xác khái toán — % báo giá nằm trong khoảng",
                      numerator=within, denominator=len(usable), sample_size=len(usable),
                      value=_ratio(within, len(usable)))
    median = Metric(key="estimate_median_error", label_vi="Độ chính xác khái toán — sai số trung vị",
                    unit="relative_error", sample_size=len(usable),
                    value=round(statistics.median(errors), 4) if errors else None)
    if usable:
        note = (f"So {len(usable)} báo giá thực tế (contractor_quotes.total_amount) với [cost_min, cost_max] của "
                "khái toán liên quan qua quote_requests.")
        if skipped:
            note += f" Bỏ qua {skipped} báo giá thiếu tổng tiền hoặc không gắn khái toán có chi phí."
        in_range.note = note
        median.note = "Trung vị (báo giá − điểm giữa khái toán) / điểm giữa; dương = báo giá cao hơn khái toán."
        if errors:
            median.breakdown = [{"mean_abs_error": round(statistics.fmean(abs(e) for e in errors), 4)}]
    else:
        msg = f"{NO_DATA_NOTE}: chưa có báo giá thực tế nào gắn với khái toán (qua quote_requests)."
        in_range.note = median.note = msg
    return in_range, median


def _log_group_key(log: RecommendationLog) -> str:
    if log.chat_session_id:
        return f"chat:{log.chat_session_id}"
    if log.estimate_session_id:
        return f"estimate:{log.estimate_session_id}"
    return f"log:{log.id}"


def _quote_links(db: Session, logs: list[RecommendationLog]) -> tuple[dict[int, set[int]], int]:
    """Gắn mỗi yêu cầu báo giá với tối đa một lượt đề xuất: (log_id -> {contractor_id}), số yêu cầu đã gắn.

    Liên kết qua estimate_session_id chung, hoặc qua sự kiện 'quote_request_created' (session_id = phiên chat
    của lượt đề xuất). Ưu tiên lượt đề xuất gần nhất trước thời điểm gửi yêu cầu chứa nhà thầu đó.
    """
    def contains(log: RecommendationLog, cid: int) -> bool:
        return any(r.get("contractor_id") == cid for r in (log.results or []))

    def pick(cands: list[RecommendationLog], cid: int, at: datetime | None) -> RecommendationLog | None:
        cands = [lg for lg in cands if contains(lg, cid)]
        if not cands:
            return None
        before = [lg for lg in cands if at is None or lg.created_at is None or lg.created_at <= at]
        return max(before or cands, key=lambda lg: (lg.created_at or datetime.min.replace(tzinfo=UTC), lg.id))

    by_estimate: dict[uuid.UUID, list[RecommendationLog]] = {}
    by_chat: dict[uuid.UUID, list[RecommendationLog]] = {}
    for lg in logs:
        if lg.estimate_session_id:
            by_estimate.setdefault(lg.estimate_session_id, []).append(lg)
        if lg.chat_session_id:
            by_chat.setdefault(lg.chat_session_id, []).append(lg)

    links: dict[int, set[int]] = {}
    linked = 0
    seen: set[tuple[int, int]] = set()
    for qr in db.scalars(select(QuoteRequest).where(QuoteRequest.estimate_session_id.is_not(None))):
        lg = pick(by_estimate.get(qr.estimate_session_id, []), qr.contractor_id, qr.created_at)
        if lg is not None:
            links.setdefault(lg.id, set()).add(qr.contractor_id)
            seen.add((lg.id, qr.contractor_id))
            linked += 1
    events = db.scalars(select(AnalyticsEvent).where(AnalyticsEvent.event == "quote_request_created",
                                                     AnalyticsEvent.session_id.is_not(None))).all()
    for ev in events:
        for cid in (ev.props or {}).get("contractor_ids") or []:
            lg = pick(by_chat.get(ev.session_id, []), cid, ev.created_at)
            if lg is not None and (lg.id, cid) not in seen:
                links.setdefault(lg.id, set()).add(cid)
                seen.add((lg.id, cid))
                linked += 1
    return links, linked


def selection_by_rank(db: Session, logs: list[RecommendationLog],
                      quote_links: dict[int, set[int]]) -> Metric:
    shown = [0] * (MAX_RANK + 1)
    clicked = [0] * (MAX_RANK + 1)
    quoted = [0] * (MAX_RANK + 1)
    for lg in logs:
        clicks = set(lg.clicked_contractor_ids or [])
        quotes = quote_links.get(lg.id, set())
        for r in lg.results or []:
            rank = r.get("rank")
            if not isinstance(rank, int) or not 1 <= rank <= MAX_RANK:
                continue
            shown[rank] += 1
            if r.get("contractor_id") in clicks:
                clicked[rank] += 1
            if r.get("contractor_id") in quotes:
                quoted[rank] += 1
    total_shown, total_clicked = sum(shown), sum(clicked)
    m = Metric(key="contractor_selection_by_rank", label_vi="Tỷ lệ nhà thầu được khách chọn theo thứ hạng",
               numerator=total_clicked, denominator=total_shown, sample_size=len(logs),
               value=_ratio(total_clicked, total_shown))
    m.breakdown = [{"rank": rank, "shown": shown[rank], "clicked": clicked[rank], "quoted": quoted[rank],
                    "click_rate": _ratio(clicked[rank], shown[rank]),
                    "quote_rate": _ratio(quoted[rank], shown[rank])}
                   for rank in range(1, MAX_RANK + 1) if shown[rank]]
    m.note = ("Giá trị = lượt bấm / lượt hiển thị nhà thầu được đề xuất; breakdown theo thứ hạng gồm cả tỷ lệ "
              "gửi yêu cầu báo giá." if total_shown else f"{NO_DATA_NOTE}: chưa có lượt đề xuất nhà thầu nào.")
    return m


def quote_request_rate(db: Session, logs: list[RecommendationLog], quote_links: dict[int, set[int]]) -> Metric:
    groups: dict[str, bool] = {}
    for lg in logs:
        key = _log_group_key(lg)
        groups[key] = groups.get(key, False) or bool(quote_links.get(lg.id))
    sessions = len(groups)
    with_quote = sum(1 for v in groups.values() if v)
    total_qr = _count(db, QuoteRequest)
    m = Metric(key="quote_request_rate", label_vi="Tỷ lệ gửi yêu cầu báo giá", numerator=with_quote,
               denominator=sessions, sample_size=sessions, value=_ratio(with_quote, sessions))
    m.breakdown = [{"quote_requests": total_qr, "recommendation_logs": len(logs),
                    "quote_requests_per_recommendation_log": _ratio(total_qr, len(logs))}]
    m.note = ("Số phiên có matching đã gửi ≥ 1 yêu cầu báo giá / số phiên có matching (phiên = phiên chat, hoặc "
              "phiên khái toán khi gọi qua API). breakdown kèm tỷ lệ thô quote_requests / recommendation_logs."
              if sessions else f"{NO_DATA_NOTE}: chưa có lượt matching nào.")
    return m


def guardrail_violation_rate(db: Session) -> Metric:
    violations = _count(db, GuardrailViolation)
    answers = _count(db, ChatMessage, ChatMessage.role == "assistant")
    m = Metric(key="guardrail_violation_rate", label_vi="Tỷ lệ vi phạm guardrail", numerator=violations,
               denominator=answers, sample_size=answers, value=_ratio(violations, answers))
    rows = db.execute(select(GuardrailViolation.rule, func.count()).group_by(GuardrailViolation.rule)
                      .order_by(func.count().desc())).all()
    m.breakdown = [{"rule": rule, "count": int(n)} for rule, n in rows]
    if answers:
        m.note = ("Số dòng guardrail_violations / số câu trả lời của trợ lý (chat_messages role=assistant). "
                  "Một câu trả lời có thể ghi nhiều vi phạm (kể cả lần sinh lại).")
    else:
        m.note = f"{NO_DATA_NOTE}: chưa có câu trả lời nào của trợ lý."
        if violations:
            m.note += f" Đã ghi {violations} vi phạm."
    return m


def data_coverage(db: Session) -> list[Metric]:
    contractors = db.scalars(select(Contractor).where(Contractor.is_active.is_(True))).all()
    n = len(contractors)
    with_price = sum(1 for c in contractors if c.rough_price_min is not None or c.finish_price_min is not None)
    with_exp = sum(1 for c in contractors if c.years_experience is not None)
    status = dict(db.execute(select(DataSource.id, DataSource.status)).all())
    filled = verified = 0
    for c in contractors:
        fs = c.field_sources or {}
        for f in COVERAGE_FIELDS:
            v = getattr(c, f)
            if v is None or (isinstance(v, str) and not v.strip()):
                continue
            filled += 1
            sid = fs.get(f, c.source_id)
            try:
                sid = int(sid) if sid is not None else None
            except (TypeError, ValueError):
                sid = None
            if sid is not None and str(status.get(sid)) == "verified":
                verified += 1
    empty = f"{NO_DATA_NOTE}: chưa có nhà thầu đang hoạt động."
    price = Metric(key="coverage_price", label_vi="Độ phủ dữ liệu — % nhà thầu có đơn giá",
                   numerator=with_price, denominator=n, sample_size=n, value=_ratio(with_price, n),
                   note="Có đơn giá xây thô hoặc hoàn thiện trên hồ sơ." if n else empty)
    exp = Metric(key="coverage_experience", label_vi="Độ phủ dữ liệu — % nhà thầu có kinh nghiệm",
                 numerator=with_exp, denominator=n, sample_size=n, value=_ratio(with_exp, n),
                 note="Có số năm kinh nghiệm trên hồ sơ." if n else empty)
    ver = Metric(key="coverage_verified_fields", label_vi="Độ phủ dữ liệu — % trường đã xác minh",
                 numerator=verified, denominator=filled, sample_size=n, value=_ratio(verified, filled))
    ver.note = ("Trường hồ sơ có giá trị mà nguồn (contractors.field_sources, mặc định source_id) có "
                "data_sources.status = verified / tổng trường có giá trị." if filled else
                (empty if not n else f"{NO_DATA_NOTE}: hồ sơ nhà thầu chưa có trường nào có giá trị."))
    return [price, exp, ver]


# ------------------------------------------------------------------ tổng hợp
def compute_metrics(db: Session) -> MetricsReport:
    logs = list(db.scalars(select(RecommendationLog).order_by(RecommendationLog.id)).all())
    quote_links, _ = _quote_links(db, logs)
    acc_in, acc_err = estimate_accuracy(db)
    metrics = [
        estimate_completion_rate(db),
        avg_turns_to_complete(db),
        acc_in,
        acc_err,
        selection_by_rank(db, logs, quote_links),
        quote_request_rate(db, logs, quote_links),
        guardrail_violation_rate(db),
        *data_coverage(db),
    ]
    totals = {
        "chat_sessions": _count(db, ChatSession),
        "assistant_messages": _count(db, ChatMessage, ChatMessage.role == "assistant"),
        "estimate_sessions": _count(db, EstimateSession),
        "recommendation_logs": len(logs),
        "quote_requests": _count(db, QuoteRequest),
        "contractor_quotes": _count(db, ContractorQuote),
        "guardrail_violations": _count(db, GuardrailViolation),
        "active_contractors": _count(db, Contractor, Contractor.is_active.is_(True)),
    }
    return MetricsReport(generated_at=datetime.now(UTC), metrics={m.key: m for m in metrics}, totals=totals)
