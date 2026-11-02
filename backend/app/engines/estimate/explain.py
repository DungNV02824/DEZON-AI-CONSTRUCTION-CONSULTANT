"""Phần giải thích sau khái toán (đặc tả mục 6.5, 6.6).

Toàn bộ là văn bản tĩnh chọn theo input — KHÔNG chứa con số tự tạo (ngoài ngày cập nhật
và nhãn nguồn lấy từ đơn giá).
"""
from app.evidence import SourceRef
from app.formatting import NO_DATA
from app.schemas.common import QualityTier, WorkScope
from app.schemas.estimate import EstimateInput

from . import rules as R

SCOPE_INCLUDES_VI: dict[WorkScope, str] = {
    WorkScope.xay_tho: (
        "Phạm vi Xây thô: gồm phần móng, khung bê tông cốt thép, xây tô tường, mái và hệ ống điện nước "
        "âm tường; chưa gồm vật tư hoàn thiện."
    ),
    WorkScope.hoan_thien: (
        "Phạm vi Hoàn thiện: gồm vật tư và nhân công hoàn thiện (ốp lát, sơn bả, trần, cửa, thiết bị "
        "điện nước, thiết bị vệ sinh) trên phần thô đã có."
    ),
    WorkScope.tron_goi: (
        "Phạm vi Trọn gói: gồm phần thô và phần hoàn thiện theo mức chất lượng đã chọn."
    ),
    WorkScope.noi_that: (
        "Phạm vi Nội thất: gồm đồ nội thất và lắp đặt bên trong theo diện tích sàn sử dụng."
    ),
}

_COMMON_EXCLUSIONS = [
    "Chi phí xin giấy phép xây dựng và các thủ tục pháp lý",
    "Chi phí thiết kế kiến trúc, kết cấu",
    "Điện nước ngoài nhà, đấu nối hạ tầng (điện, nước, thoát nước công cộng)",
]

_SCOPE_EXCLUSIONS: dict[WorkScope, list[str]] = {
    WorkScope.xay_tho: [
        "Vật tư hoàn thiện (gạch ốp lát, sơn, trần, cửa, thiết bị vệ sinh, thiết bị điện)",
        "Nội thất rời (giường, tủ, sofa, rèm…)",
        "Tháo dỡ công trình cũ, gia cố nhà lân cận",
    ],
    WorkScope.hoan_thien: [
        "Phần xây thô (móng, khung kết cấu, xây tô)",
        "Nội thất rời (giường, tủ, sofa, rèm…)",
    ],
    WorkScope.tron_goi: [
        "Nội thất rời (giường, tủ, sofa, rèm…)",
        "Thiết bị đặc thù (thang máy, điều hòa, năng lượng mặt trời, hồ bơi…)",
        "Tháo dỡ công trình cũ, gia cố nhà lân cận",
    ],
    WorkScope.noi_that: [
        "Phần xây thô và hoàn thiện của công trình",
        "Thiết bị điện tử, đồ gia dụng",
        "Chi phí thiết kế nội thất chi tiết (nếu nhà thầu tính riêng)",
    ],
}

_STRUCTURE_SCOPES = {WorkScope.xay_tho, WorkScope.tron_goi}


def build_exclusions(inp: EstimateInput) -> list[str]:
    """Hạng mục CHƯA bao gồm trong khái toán, theo phạm vi thi công (input đã áp mặc định)."""
    scope = WorkScope(inp.work_scope)
    out: list[str] = []
    apartment = inp.building_type == "can_ho"
    # Căn hộ không tính móng nên không nhắc ép cọc (kể cả khi input có foundation_type).
    if scope in _STRUCTURE_SCOPES and not apartment and inp.foundation_type == R.FOUNDATION_PILE:
        out.append("Chi phí ép cọc hoặc khoan cọc (hệ số móng cọc chưa gồm phần ép cọc)")
    if scope == WorkScope.noi_that:
        out.extend(_SCOPE_EXCLUSIONS[scope])
        out.append("Chi phí xin phép sửa chữa/cải tạo (nếu có)")
        return out
    if apartment:
        out.extend(x for x in _SCOPE_EXCLUSIONS[scope] if "Tháo dỡ" not in x)
        out.append("Chi phí thiết kế")
        out.append("Phí, thủ tục thi công với ban quản lý tòa nhà")
        return out
    out.extend(_COMMON_EXCLUSIONS)
    out.extend(_SCOPE_EXCLUSIONS[scope])
    return out


_GENERAL_DRIVERS = [
    "Hẻm nhỏ, xe tải vật tư khó vào làm tăng chi phí vận chuyển và nhân công",
    "Đất yếu hoặc nền đất phức tạp cần gia cố móng",
    "Nhiều ban công, ô văng, chi tiết kiến trúc phức tạp",
    "Biến động giá vật liệu (thép, xi măng, cát, đá) theo thời điểm thi công",
]


def build_cost_drivers(inp: EstimateInput, has_basement: bool, has_mezzanine: bool) -> list[str]:
    """Yếu tố có thể làm chi phí tăng: theo input trước, sau đó là yếu tố chung."""
    scope = WorkScope(inp.work_scope)
    out: list[str] = []
    structural = scope not in R.INTERIOR_ONLY_SCOPES and inp.building_type != "can_ho"
    # Phạm vi Hoàn thiện không gồm phần thô: không nhắc đào hầm, ép cọc, khung kết cấu.
    rough = scope in _STRUCTURE_SCOPES
    if structural:
        if has_basement:
            out.append("Tầng hầm: đào đất, vách chắn đất, chống thấm và bơm nước làm chi phí tăng đáng kể"
                       if rough else "Tầng hầm được tính hệ số quy đổi cao hơn sàn thường")
        if rough and inp.foundation_type == R.FOUNDATION_PILE:
            out.append("Móng cọc: chi phí ép/khoan cọc tính riêng và phụ thuộc kết quả khảo sát địa chất")
        if inp.floors >= 4:
            out.append("Nhà nhiều tầng cần khung kết cấu và móng lớn hơn, chi phí giàn giáo và vận chuyển "
                       "vật tư lên cao tăng" if rough
                       else "Nhà nhiều tầng làm chi phí giàn giáo và vận chuyển vật tư lên cao tăng")
        if inp.roof_type in (R.ROOF_TILE_ON_TRUSS, R.ROOF_TILE_ON_CONCRETE):
            out.append("Mái ngói có hệ số quy đổi cao hơn mái bê tông cốt thép hoặc mái tôn")
        if inp.rooftop_terrace == "covered":
            out.append("Sân thượng có mái che được tính hệ số cao hơn sân thượng để trống")
        if has_mezzanine:
            out.append("Tầng lửng được tính như một sàn đầy đủ")
    if inp.quality_tier in (QualityTier.kha, QualityTier.cao_cap) and scope != WorkScope.xay_tho:
        out.append("Mức chất lượng vật tư hoàn thiện cao làm đơn giá tăng")
    if structural:
        out.extend(d for d in _GENERAL_DRIVERS if rough or "móng" not in d)
    else:
        out.append("Thi công trong tòa nhà có giới hạn giờ làm và vận chuyển vật tư qua thang máy")
        out.append("Biến động giá vật liệu theo thời điểm thi công")
    return out


DISCLAIMER_TEMPLATE = (
    "Đây là khái toán sơ bộ dựa trên đơn giá tham chiếu {source} và các giả định nêu trên, "
    "chưa tính điều kiện thực tế của khu đất. Không phải báo giá thi công. "
    "Vui lòng yêu cầu báo giá từ nhà thầu để có con số chính xác."
)


def build_disclaimer(source: SourceRef | None) -> str:
    """Ghi chú miễn trừ bắt buộc (mục 6.6), điền {nguồn, ngày cập nhật dd/mm/yyyy}."""
    if source is None:
        src = f"({NO_DATA})"
    else:
        if source.updated_at:
            updated = f"cập nhật {source.updated_at.strftime('%d/%m/%Y')}"
        else:
            updated = f"ngày cập nhật: {NO_DATA}"
        src = f"({source.label}, {updated})"
    return DISCLAIMER_TEMPLATE.format(source=src)
