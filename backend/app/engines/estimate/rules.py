"""Hằng số và quy tắc chọn hệ số khái toán (đặc tả mục 6.2).

Chỉ chứa logic thuần: chọn mã rule theo input, thứ tự trạng thái xác minh, nhãn tiếng Việt.
"""
from app.evidence import VerificationStatus
from app.schemas.common import QualityTier, WorkScope

# --- Mã rule (khớp seed estimate_rules) --------------------------------------------------------
FLOOR = "floor_standard"
MEZZANINE = "mezzanine"
FOUNDATION_SINGLE = "foundation_single"
FOUNDATION_STRIP = "foundation_strip"
FOUNDATION_PILE = "foundation_pile"
BASEMENT_LT_1_3 = "basement_lt_1_3"
BASEMENT_1_3_1_7 = "basement_1_3_1_7"
BASEMENT_1_7_2_0 = "basement_1_7_2_0"
BASEMENT_GT_2_0 = "basement_gt_2_0"
ROOF_METAL = "roof_metal"
ROOF_CONCRETE = "roof_concrete"
ROOF_TILE_ON_TRUSS = "roof_tile_on_truss"
ROOF_TILE_ON_CONCRETE = "roof_tile_on_concrete"
ROOF_NONE = "none"
ROOFTOP_COVERED = "rooftop_covered"
ROOFTOP_OPEN = "rooftop_open"
YARD = "yard"

BASEMENT_RULES = (BASEMENT_LT_1_3, BASEMENT_1_3_1_7, BASEMENT_1_7_2_0, BASEMENT_GT_2_0)
# Khi khách có hầm nhưng chưa cho độ sâu: chọn nhóm 1,7–2,0 m (xem docs/DECISIONS.md).
DEFAULT_BASEMENT_RULE = BASEMENT_1_7_2_0

# Số tầng nổi tối đa còn mặc định móng băng (mục 6.2): ≤ 3 -> băng, ≥ 4 -> cọc.
STRIP_FOUNDATION_MAX_FLOORS = 3

FOUNDATION_LABEL_VI = {
    FOUNDATION_SINGLE: "móng đơn",
    FOUNDATION_STRIP: "móng băng",
    FOUNDATION_PILE: "móng cọc",
}
ROOF_LABEL_VI = {
    ROOF_METAL: "mái tôn",
    ROOF_CONCRETE: "mái bê tông cốt thép",
    ROOF_TILE_ON_TRUSS: "mái ngói kèo thép",
    ROOF_TILE_ON_CONCRETE: "mái ngói đổ bê tông cốt thép",
    ROOF_NONE: "không làm mái",
}
ROOFTOP_LABEL_VI = {"covered": "sân thượng có mái che", "open": "sân thượng không mái"}

# Thứ tự mức chất lượng từ rẻ đến đắt.
QUALITY_ORDER: list[QualityTier] = [
    QualityTier.tiet_kiem, QualityTier.trung_binh, QualityTier.kha, QualityTier.cao_cap,
]

# Phạm vi chỉ làm phần bên trong: không tính móng, mái, hầm, sân thượng, sân.
INTERIOR_ONLY_SCOPES = {WorkScope.noi_that}

# Thứ tự "độ tin cậy" của trạng thái: số lớn = mạnh hơn.
_STATUS_RANK = {
    VerificationStatus.unavailable: 0,
    VerificationStatus.reference: 1,
    VerificationStatus.on_dezon: 2,
    VerificationStatus.verified: 3,
}


def weakest_status(*statuses: VerificationStatus | str) -> VerificationStatus:
    """Trạng thái yếu nhất trong danh sách (vd verified + reference -> reference)."""
    vals = [VerificationStatus(s) for s in statuses]
    if not vals:
        return VerificationStatus.unavailable
    return min(vals, key=lambda s: _STATUS_RANK[s])


def default_foundation(floors: int) -> str:
    return FOUNDATION_STRIP if floors <= STRIP_FOUNDATION_MAX_FLOORS else FOUNDATION_PILE


def basement_rule_for_depth(depth_m: float | None) -> str:
    """Chọn rule hầm theo độ sâu: < 1,3 · 1,3–1,7 · (1,7–2,0] · > 2,0. None -> mặc định."""
    if depth_m is None:
        return DEFAULT_BASEMENT_RULE
    if depth_m < 1.3:
        return BASEMENT_LT_1_3
    if depth_m <= 1.7:
        return BASEMENT_1_3_1_7
    if depth_m <= 2.0:
        return BASEMENT_1_7_2_0
    return BASEMENT_GT_2_0


def lower_quality(tier: QualityTier | str) -> QualityTier | None:
    """Mức chất lượng thấp hơn một bậc; None nếu đã là tiết kiệm."""
    idx = QUALITY_ORDER.index(QualityTier(tier))
    return QUALITY_ORDER[idx - 1] if idx > 0 else None
