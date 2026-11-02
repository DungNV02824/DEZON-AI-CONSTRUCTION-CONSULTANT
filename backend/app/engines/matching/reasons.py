"""Evidence của thẻ nhà thầu + lý do đề xuất / điểm cần lưu ý (đặc tả mục 7.4). Hàm thuần.

Nguyên tắc:
- Mỗi Reason.evidence là một khóa trong ContractorCard.evidence và EvidenceValue đó PHẢI có dữ liệu.
- Mọi con số trong văn bản lý do lấy từ `display` của evidence (không tự định dạng/tự tạo số).
- Gọi số dự án là "công trình ghi nhận trên Dezon"; không dùng từ "tốt nhất".
"""
from __future__ import annotations

from datetime import date

from app.engines.matching.criteria import ContractorFacts, FieldSource, norm_place, profile_completeness
from app.engines.matching.scorer import (
    FLAG_OVER_BUDGET,
    FLAG_SCOPE_NO_PRICE,
    FLAG_UNDER_BUDGET,
    PRICE_KIND_VI,
    ScoredContractor,
)
from app.evidence import EvidenceValue, SourceRef, VerificationStatus, make_evidence
from app.formatting import format_price_per_m2_range, format_vnd, vi_number
from app.schemas.common import BUILDING_TYPE_VI
from app.schemas.matching import Caution, MatchCriteria, Reason

EVIDENCE_KEYS: tuple[str, ...] = (
    "province", "building_types", "years_experience", "founded_year", "staff", "rough_price",
    "finish_price", "typical_project_value", "dezon_project_count", "warranty", "profile_completeness",
)
PRICE_EVIDENCE_KEY = {"rough": "rough_price", "finish": "finish_price"}
BUILDING_TYPE_ORDER = list(BUILDING_TYPE_VI)
MAX_REASONS = 5


def _ev(value, display: str, src: FieldSource | None, today: date | None,
        stale_days: int | None) -> EvidenceValue:
    if value is None:
        return EvidenceValue.unavailable()
    return make_evidence(value, display, source=src.ref if src else None,
                         status=src.status if src else VerificationStatus.reference,
                         today=today, stale_days=stale_days)


def _price_range(lo, hi) -> list[int] | None:
    if lo is None and hi is None:
        return None
    lo = lo if lo is not None else hi
    hi = hi if hi is not None else lo
    return [int(lo), int(hi)]


def building_types_of(facts: ContractorFacts) -> list[str]:
    types = {c.building_type for c in facts.capabilities if c.building_type}
    types |= {p.building_type for p in facts.projects if p.building_type}
    return sorted(types, key=lambda t: BUILDING_TYPE_ORDER.index(t) if t in BUILDING_TYPE_ORDER else 99)


def staff_display(workers: int | None, office: int | None) -> str | None:
    parts = []
    if workers is not None:
        parts.append(f"{vi_number(workers, 0)} thợ")
    if office is not None:
        parts.append(f"{vi_number(office, 0)} văn phòng")
    return ", ".join(parts) or None


def project_count_display(n: int) -> str:
    return f"{vi_number(n, 0)} công trình ghi nhận trên Dezon"


def build_evidence(facts: ContractorFacts, today: date | None = None,
                   stale_days: int | None = None) -> dict[str, EvidenceValue]:
    """Dựng dict evidence chuẩn của ContractorCard từ dữ kiện + nguồn từng trường."""
    f = facts
    ev: dict[str, EvidenceValue] = {}

    def put(key, value, display, *fields):
        ev[key] = _ev(value, display, f.source_for(*fields), today, stale_days)

    if f.province:
        put("province", f.province, f.province, "province")
    else:
        # Hồ sơ không ghi tỉnh chính -> tỉnh trong capability, rồi tới tỉnh có dự án (bộ lọc cứng 7.2 dùng cả hai),
        # để mọi nhà thầu qua bộ lọc đều có evidence khu vực.
        provs = sorted({c.province for c in f.capabilities if c.province})
        if provs:
            put("province", provs, " · ".join(provs), "capabilities")
        else:
            provs = sorted({p.province for p in f.projects if p.province})
            put("province", provs or None, " · ".join(provs), "projects", "capabilities")
    types = building_types_of(f)
    put("building_types", types or None, " · ".join(BUILDING_TYPE_VI.get(t, t) for t in types), "capabilities")
    put("years_experience", f.years_experience,
        f"{vi_number(f.years_experience, 0)} năm" if f.years_experience is not None else "", "years_experience")
    put("founded_year", f.founded_year, str(f.founded_year), "founded_year")
    staff = staff_display(f.staff_workers, f.staff_office)
    put("staff", {"workers": f.staff_workers, "office": f.staff_office} if staff else None, staff or "",
        "staff_workers", "staff_office")
    rough = _price_range(f.rough_price_min, f.rough_price_max)
    put("rough_price", rough, format_price_per_m2_range(f.rough_price_min, f.rough_price_max),
        "rough_price_min", "rough_price_max")
    finish = _price_range(f.finish_price_min, f.finish_price_max)
    put("finish_price", finish, format_price_per_m2_range(f.finish_price_min, f.finish_price_max),
        "finish_price_min", "finish_price_max")
    tpv = int(f.typical_project_value) if f.typical_project_value is not None else None
    put("typical_project_value", tpv, f"{format_vnd(tpv)}/công trình" if tpv is not None else "",
        "typical_project_value")
    cnt = f.dezon_project_count
    put("dezon_project_count", cnt, project_count_display(cnt) if cnt is not None else "", "dezon_project_count")
    if f.has_warranty_info:
        text = (f.warranty_text or "").strip() or "Có thông tin bảo hành"
        put("warranty", text, text, "warranty_text", "has_warranty_info")
    else:
        ev["warranty"] = EvidenceValue.unavailable()
    comp = profile_completeness(f)
    base = f.default_source
    ref = SourceRef(label=f"Tính tự động từ hồ sơ {f.name} trên Dezon",
                    url=(base.ref.url if base and base.ref else None) or f.profile_url or None,
                    updated_at=base.ref.updated_at if base and base.ref else None)
    ev["profile_completeness"] = make_evidence(
        comp, f"Hồ sơ đầy đủ {vi_number(comp * 100, 0)}%", source=ref,
        status=VerificationStatus.reference, today=today, stale_days=stale_days)
    return ev


# --- Lý do đề xuất -----------------------------------------------------------------------------
def _has(ev: dict[str, EvidenceValue], key: str) -> bool:
    e = ev.get(key)
    return e is not None and e.has_value


def build_reasons(scored: ScoredContractor, evidence: dict[str, EvidenceValue],
                  criteria: MatchCriteria) -> list[Reason]:
    """Sinh lý do có cấu trúc (≥ 2 khi hồ sơ có tối thiểu khu vực / loại công trình / số công trình)."""
    reasons: list[Reason] = []
    bt_label = BUILDING_TYPE_VI.get(str(criteria.building_type), "").lower()

    # 1) Loại công trình
    if _has(evidence, "building_types"):
        types_txt = evidence["building_types"].display.lower().replace(" · ", ", ")
        if scored.has_type_capability and scored.has_type_project:
            text = f"Có năng lực và có dự án {bt_label} trên Dezon (nhận thi công {types_txt})"
        elif scored.has_type_capability or scored.has_type_project:
            text = f"Nhận thi công {types_txt}"
        else:
            text = f"Nhận thi công {types_txt} (chưa ghi nhận {bt_label})"
        reasons.append(Reason(text=text, evidence="building_types"))

    # 2) Khu vực
    if _has(evidence, "province"):
        prov = evidence["province"].display
        same_province = norm_place(prov) == norm_place(criteria.province)
        if scored.relaxed_location:
            text = f"Hoạt động tại {prov} — khu vực lân cận {criteria.province}"
        elif scored.location_kind == "district" and criteria.district:
            text = f"Hoạt động tại {prov}, có ghi nhận tại {criteria.district}"
        elif scored.location_kind == "province" and not same_province:
            # Hồ sơ ghi tỉnh chính khác, nhưng có năng lực/dự án tại tỉnh của khách.
            text = f"Hoạt động tại {prov}, có ghi nhận năng lực hoặc công trình tại {criteria.province}"
        else:
            text = f"Hoạt động tại {prov}"
        reasons.append(Reason(text=text, evidence="province"))

    # 3) Số công trình ghi nhận trên Dezon
    if _has(evidence, "dezon_project_count") and (evidence["dezon_project_count"].value or 0) > 0:
        reasons.append(Reason(text=evidence["dezon_project_count"].display, evidence="dezon_project_count"))

    # 4) Đơn giá theo phạm vi thi công của khách
    key = PRICE_EVIDENCE_KEY.get(scored.price_kind or "")
    if key and _has(evidence, key):
        text = f"Đơn giá {PRICE_KIND_VI[scored.price_kind]} {evidence[key].display}"
        bf = scored.criterion("budget_fit")
        if scored.estimated_cost and (criteria.budget_vnd or 0) > 0 and bf is not None and bf.score >= 1.0:
            text += ", chi phí ước phù hợp ngân sách của anh/chị"
        reasons.append(Reason(text=text, evidence=key))
    elif not key and FLAG_SCOPE_NO_PRICE not in scored.flags:
        # Chưa rõ phạm vi thi công -> nêu đơn giá có trên hồ sơ. Nội thất: đơn giá thô/hoàn thiện không áp dụng.
        for k, kind in (("rough_price", "rough"), ("finish_price", "finish")):
            if _has(evidence, k):
                reasons.append(Reason(text=f"Đơn giá {PRICE_KIND_VI[kind]} {evidence[k].display}", evidence=k))
                break

    # 5) Kinh nghiệm / nhân sự
    if _has(evidence, "years_experience") and (evidence["years_experience"].value or 0) > 0:
        reasons.append(Reason(text=f"{evidence['years_experience'].display} kinh nghiệm",
                              evidence="years_experience"))
    if _has(evidence, "staff"):
        reasons.append(Reason(text=f"Nhân sự {evidence['staff'].display}", evidence="staff"))

    # Dự phòng khi hồ sơ quá ít dữ liệu: mức đầy đủ hồ sơ (luôn có dữ liệu tính toán).
    if len(reasons) < 2 and _has(evidence, "profile_completeness"):
        reasons.append(Reason(text=evidence["profile_completeness"].display, evidence="profile_completeness"))
    return reasons[:MAX_REASONS]


# --- Điểm cần lưu ý ----------------------------------------------------------------------------
def build_cautions(scored: ScoredContractor, evidence: dict[str, EvidenceValue],
                   criteria: MatchCriteria) -> list[Caution]:
    out: list[Caution] = []
    key = PRICE_EVIDENCE_KEY.get(scored.price_kind or "")
    if FLAG_SCOPE_NO_PRICE in scored.flags:
        out.append(Caution(text="Đơn giá trên hồ sơ không áp dụng cho phạm vi nội thất — cần yêu cầu báo giá riêng"))
    elif key and not _has(evidence, key):
        out.append(Caution(text="Chưa có thông tin đơn giá — cần đối chiếu", evidence=key))
    elif not key and not (_has(evidence, "rough_price") or _has(evidence, "finish_price")):
        out.append(Caution(text="Chưa có thông tin đơn giá — cần đối chiếu", evidence="rough_price"))
    if not _has(evidence, "years_experience"):
        out.append(Caution(text="Chưa có thông tin kinh nghiệm", evidence="years_experience"))
    if not _has(evidence, "warranty"):
        out.append(Caution(text="Chưa có thông tin bảo hành", evidence="warranty"))
    if FLAG_OVER_BUDGET in scored.flags:
        out.append(Caution(text="Chi phí ước theo đơn giá nhà thầu vượt ngân sách của anh/chị — cần trao đổi thêm",
                           evidence=key))
    if FLAG_UNDER_BUDGET in scored.flags:
        out.append(Caution(text="Chi phí ước thấp hơn nhiều so với ngân sách — cần đối chiếu phạm vi và vật liệu",
                           evidence=key))
    if scored.relaxed_location:
        out.append(Caution(text=f"Chưa ghi nhận hoạt động tại {criteria.province}, chỉ khớp tỉnh lân cận",
                           evidence="province"))
    if not scored.has_type_project:
        bt_label = BUILDING_TYPE_VI.get(str(criteria.building_type), "").lower()
        out.append(Caution(text=f"Chưa có dự án {bt_label} trong danh sách công trình trên Dezon — "
                                "nên xem thêm hồ sơ"))
    stale = sorted({e.source.updated_at for e in evidence.values()
                    if e.is_stale and e.source and e.source.updated_at})
    if stale:
        dates = ", ".join(d.strftime("%d/%m/%Y") for d in stale)
        out.append(Caution(text=f"Một số thông tin hồ sơ cập nhật ngày {dates}, có thể đã cũ"))
    return out
