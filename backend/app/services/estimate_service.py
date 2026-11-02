"""Service khái toán: nạp hệ số/đơn giá từ DB, gọi engine thuần, lưu estimate_sessions.

Đặc tả mục 6.3 (chọn đơn giá), 9.2 (`calculate_estimate`, `suggest_budget_adjustments`,
`get_reference_rates`).
"""
import re
import unicodedata
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import ConstructionRate, EstimateItemRow, EstimateRule, EstimateSession
from app.engines.estimate import calculate_estimate, suggest_adjustments
from app.evidence import (
    EvidenceValue,
    SourceRef,
    VerificationStatus,
    compute_is_stale,
    make_evidence,
    source_ref_from,
)
from app.formatting import format_price_per_m2_range, format_vnd
from app.schemas.common import BuildingType, QualityTier, WorkScope
from app.schemas.estimate import (
    BudgetAdjustmentResult,
    EstimateInput,
    EstimateResult,
    RateData,
    RuleData,
    RuleSet,
)

NATIONWIDE = "*"
CUSTOMER_BUDGET_LABEL = "Ngân sách do anh/chị cung cấp"


class EstimateNotFoundError(LookupError):
    """Không tìm thấy estimate_session theo id."""


# --- Hệ số --------------------------------------------------------------------------------------

def load_ruleset(db: Session) -> RuleSet:
    """Mỗi code: version mới nhất đã approved; nếu chưa có bản approved thì version mới nhất."""
    rows = db.scalars(
        select(EstimateRule).options(selectinload(EstimateRule.source))
        .order_by(EstimateRule.code, EstimateRule.version.desc(), EstimateRule.id.desc())
    ).all()
    chosen: dict[str, EstimateRule] = {}
    for r in rows:
        cur = chosen.get(r.code)
        if cur is None or (r.approved and not cur.approved):
            chosen[r.code] = r
    rules = {
        code: RuleData(code=r.code, group_name=r.group_name, label_vi=r.label_vi,
                       coefficient=float(r.coefficient), base=r.base, approved=bool(r.approved),
                       version=r.version, source=source_ref_from(r.source))
        for code, r in chosen.items()
    }
    version = max((r.version for r in rules.values()), default=0)
    return RuleSet(version=version, rules=rules)


# --- Đơn giá ------------------------------------------------------------------------------------

# Bí danh thường gặp (sau khi bỏ dấu, bỏ tiền tố "TP."/"Thành phố"/"Tỉnh").
_PROVINCE_ALIASES = {
    "hcm": "ho chi minh", "tphcm": "ho chi minh", "sai gon": "ho chi minh", "saigon": "ho chi minh",
    "sg": "ho chi minh", "hn": "ha noi",
}
_PROVINCE_PREFIX = re.compile(r"^(?:thanh pho|tp|tinh)\b\s*")


def _fold(text: str) -> str:
    """Bỏ dấu tiếng Việt (gồm đ/Đ), chữ thường."""
    s = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn").casefold()


def normalize_province(name: str | None) -> str:
    """Khóa so khớp tỉnh: không phân biệt hoa thường, dấu, khoảng trắng/dấu chấm thừa, tiền tố
    "TP."/"Thành phố"/"Tỉnh" và bí danh phổ biến ("HCM", "Sài Gòn", "HN").
    Vd "TP. Hồ Chí Minh", "tp ho chi minh", "HCM" -> "ho chi minh"; "*" giữ nguyên."""
    s = (name or "").strip()
    if s == NATIONWIDE:
        return NATIONWIDE
    s = re.sub(r"[.,]", " ", _fold(s))
    s = re.sub(r"\s+", " ", s).strip()
    if s in _PROVINCE_ALIASES:
        return _PROVINCE_ALIASES[s]
    s = _PROVINCE_PREFIX.sub("", s).strip()
    return _PROVINCE_ALIASES.get(s, s)


def _to_rate_data(row: ConstructionRate, is_fallback: bool) -> RateData:
    src = row.source
    status = VerificationStatus.reference if is_fallback else VerificationStatus(
        src.status if src is not None and src.status else VerificationStatus.reference)
    return RateData(
        id=row.id, building_type=row.building_type, province=row.province, work_scope=row.work_scope,
        quality_tier=row.quality_tier, price_min=int(row.price_min), price_max=int(row.price_max),
        effective_from=row.effective_from, is_fallback=is_fallback, status=status,
        source=source_ref_from(src),
    )


def select_rate(db: Session, building_type: BuildingType | str, province: str,
                work_scope: WorkScope | str, quality_tier: QualityTier | str = QualityTier.trung_binh,
                on_date: date | None = None) -> RateData | None:
    """Đơn giá còn hiệu lực khớp chính xác; không có -> fallback province='*' (status reference).

    Giá trị enum không hợp lệ -> ValueError (kiểm tra trước khi gửi xuống PostgreSQL để không làm
    hỏng transaction bằng lỗi enum). quality_tier None -> 'trung_binh'."""
    building_type = BuildingType(building_type)
    work_scope = WorkScope(work_scope)
    quality_tier = QualityTier(quality_tier or QualityTier.trung_binh)
    on_date = on_date or date.today()
    rows = db.scalars(
        select(ConstructionRate).options(selectinload(ConstructionRate.source))
        .where(
            ConstructionRate.building_type == building_type.value,
            ConstructionRate.work_scope == work_scope.value,
            ConstructionRate.quality_tier == quality_tier.value,
            ConstructionRate.effective_from <= on_date,
            or_(ConstructionRate.effective_to.is_(None), ConstructionRate.effective_to >= on_date),
        )
        .order_by(ConstructionRate.effective_from.desc(), ConstructionRate.id.desc())
    ).all()
    wanted = normalize_province(province)
    exact = next((r for r in rows if normalize_province(r.province) == wanted), None)
    if exact is not None and exact.province != NATIONWIDE:
        return _to_rate_data(exact, is_fallback=False)
    fallback = next((r for r in rows if r.province.strip() == NATIONWIDE), None)
    if fallback is not None:
        return _to_rate_data(fallback, is_fallback=wanted != NATIONWIDE)
    return None


def rate_evidence(rate: RateData | None) -> EvidenceValue:
    if rate is None:
        return EvidenceValue.unavailable()
    return make_evidence([rate.price_min, rate.price_max],
                         format_price_per_m2_range(rate.price_min, rate.price_max),
                         source=rate.source, status=rate.status)


def get_reference_rates(db: Session, building_type: BuildingType | str, province: str,
                        work_scope: WorkScope | str, quality_tier: QualityTier | str = QualityTier.trung_binh,
                        ) -> tuple[EvidenceValue, RateData | None]:
    """Tool `get_reference_rates`: đơn giá + nguồn (unavailable nếu không có)."""
    rate = select_rate(db, building_type, province, work_scope, quality_tier or QualityTier.trung_binh)
    return rate_evidence(rate), rate


def _lookup_for(db: Session):
    def lookup(inp: EstimateInput) -> RateData | None:
        return select_rate(db, inp.building_type, inp.province, inp.work_scope, inp.quality_tier)
    return lookup


# --- Khái toán ----------------------------------------------------------------------------------

def create_estimate(db: Session, inp: EstimateInput, chat_session_id: uuid.UUID | None = None) -> EstimateResult:
    """Tính khái toán, lưu estimate_sessions + estimate_items, trả kết quả có estimate_session_id."""
    rules = load_ruleset(db)
    rate = _lookup_for(db)(inp)
    result = calculate_estimate(inp, rules, rate)
    sid = uuid.uuid4()
    result = result.model_copy(update={"estimate_session_id": sid})
    row = EstimateSession(
        id=sid,
        chat_session_id=chat_session_id,
        input=inp.model_dump(mode="json", exclude_unset=True),  # input GỐC (chưa áp mặc định)
        rules_version=result.rules_version,
        rate_id=result.rate_id,
        converted_area_m2=Decimal(str(result.converted_area_m2)),
        cost_min=result.cost_min,
        cost_max=result.cost_max,
        assumptions={
            "assumptions": result.assumptions,
            "exclusions": result.exclusions,
            "cost_drivers": result.cost_drivers,
            "warnings": result.warnings,
            "snapshot": result.model_dump(mode="json"),
        },
    )
    row.items = [
        EstimateItemRow(rule_code=it.rule_code, label_vi=it.label_vi, area_m2=Decimal(str(it.area_m2)),
                        coefficient=Decimal(str(it.coefficient)), converted_m2=Decimal(str(it.converted_m2)))
        for it in result.items
    ]
    db.add(row)
    db.commit()
    return result


def _load_row(db: Session, estimate_session_id: uuid.UUID | str) -> EstimateSession | None:
    try:
        sid = estimate_session_id if isinstance(estimate_session_id, uuid.UUID) else uuid.UUID(
            str(estimate_session_id))
    except ValueError:
        return None
    return db.get(EstimateSession, sid)


def _refresh_staleness(res: EstimateResult) -> EstimateResult:
    """Snapshot giữ nguyên con số đã hiển thị, nhưng cờ dữ liệu cũ (nguyên tắc 2.5) tính theo ngày đọc."""
    src = res.rate.source
    if src is None or src.updated_at is None or res.rate.is_stale or not compute_is_stale(src.updated_at):
        return res
    warn = f"Đơn giá cập nhật ngày {src.updated_at.strftime('%d/%m/%Y')}, có thể đã cũ"
    return res.model_copy(update={
        "rate": res.rate.model_copy(update={"is_stale": True}),
        "cost": res.cost.model_copy(update={"is_stale": True}) if res.cost.has_value else res.cost,
        "warnings": res.warnings if warn in res.warnings else [*res.warnings, warn],
    })


def _result_from_row(db: Session, row: EstimateSession) -> EstimateResult:
    snap = row.assumptions.get("snapshot") if isinstance(row.assumptions, dict) else None
    if snap:
        res = _refresh_staleness(EstimateResult.model_validate(snap))
    else:  # bản ghi không có snapshot (vd tạo từ nguồn khác): tính lại với hệ số hiện hành + đơn giá đã lưu
        rate = _to_rate_data(row.rate, is_fallback=row.rate.province == NATIONWIDE) if row.rate else None
        res = calculate_estimate(EstimateInput.model_validate(row.input), load_ruleset(db), rate)
    return res.model_copy(update={"estimate_session_id": row.id})


def get_estimate(db: Session, estimate_session_id: uuid.UUID | str) -> EstimateResult | None:
    row = _load_row(db, estimate_session_id)
    return _result_from_row(db, row) if row is not None else None


def suggest_budget_adjustments(db: Session, estimate_session_id: uuid.UUID | str,
                               target_budget_vnd: int) -> BudgetAdjustmentResult:
    """Tool `suggest_budget_adjustments`. Ném EstimateNotFoundError nếu không có phiên khái toán,
    ValueError nếu ngân sách không dương."""
    if target_budget_vnd is None or int(target_budget_vnd) <= 0:
        raise ValueError("target_budget_vnd phải là số dương (VND)")
    target_budget_vnd = int(target_budget_vnd)
    row = _load_row(db, estimate_session_id)
    if row is None:
        raise EstimateNotFoundError(str(estimate_session_id))
    current = _result_from_row(db, row)
    raw_inp = EstimateInput.model_validate(row.input)
    options = suggest_adjustments(raw_inp, load_ruleset(db), _lookup_for(db), target_budget_vnd, current)
    target_ev = EvidenceValue(value=target_budget_vnd, display=format_vnd(target_budget_vnd),
                              status=VerificationStatus.reference,
                              source=SourceRef(label=CUSTOMER_BUDGET_LABEL))
    return BudgetAdjustmentResult(estimate_session_id=row.id, target_budget_vnd=target_budget_vnd,
                                  target_budget=target_ev, current_cost=current.cost, options=options)
