"""Slot thu thập trong hội thoại (đặc tả mục 5.2)."""
from pydantic import BaseModel

from app.schemas.common import BuildingType, QualityTier, WorkScope
from app.schemas.estimate import EstimateInput, FoundationType, RooftopType, RoofType


class Slots(BaseModel):
    building_type: BuildingType | None = None
    province: str | None = None
    district: str | None = None
    land_width_m: float | None = None
    land_length_m: float | None = None
    land_area_m2: float | None = None
    building_area_m2: float | None = None
    floors: int | None = None
    has_mezzanine: bool | None = None
    mezzanine_area_m2: float | None = None  # chỉ có nghĩa khi has_mezzanine = True (hỏi sau khi khách nói có lửng)
    basement: bool | None = None
    basement_depth_m: float | None = None
    roof_type: RoofType | None = None
    rooftop_terrace: RooftopType | None = None
    foundation_type: FoundationType | None = None
    work_scope: WorkScope | None = None
    # Giữ trường để tương thích schema; hội thoại KHÔNG hỏi/không đặt mức hoàn thiện (chưa có dữ liệu thật theo mức).
    quality_tier: QualityTier | None = None
    budget_vnd: int | None = None  # trần ngân sách (so với khái toán, chấm nhà thầu); None khi khách chỉ nêu cận dưới
    # Nhãn ngân sách khách chọn ("2–4 tỷ", "Dưới 2 tỷ", "Trên 10 tỷ") — hiển thị thay cho budget_vnd. "Trên X tỷ" chỉ
    # có nhãn (budget_vnd = None): không có trần nên không so với khái toán.
    budget_label: str | None = None
    start_time: str | None = None
    # Câu hỏi tùy chọn (bỏ qua được) — CHỈ GHI NHẬN: hiển thị trên bản xác nhận, gửi kèm yêu cầu báo giá, KHÔNG làm
    # thay đổi khái toán (chưa có đơn giá thật theo phong cách / mức đầu tư). Giá trị: mã trong DESIGN_STYLE_VI /
    # INVESTMENT_LEVEL_VI hoặc chữ khách tự nhập.
    design_style: str | None = None
    investment_level: str | None = None
    confirmed: bool = False  # khách đã xác nhận bản tóm tắt trước khi khái toán
    # Các slot đã hỏi khách (kể cả khi khách chưa trả lời) — tránh hỏi lại slot tùy chọn như budget_vnd và slot chi
    # tiết khách trả lời "chưa biết" (khi đó dùng mặc định và nêu rõ trong tóm tắt).
    asked: list[str] = []


# Slot cốt lõi bắt buộc để khái toán (land_area_m2 được suy ra từ rộng × dài khi chuẩn hóa).
REQUIRED_SLOTS: list[str] = ["building_type", "province", "land_area_m2", "floors", "work_scope"]

# Slot chi tiết KHÔNG hỏi trước bản tóm tắt (chủ sản phẩm: phần hỏi chỉ gồm đúng 5 câu cốt lõi). Bản xác nhận nêu rõ
# mặc định của engine (xây kín đất, không hầm, không lửng, mái BTCT); khách bấm "Cung cấp thêm thông tin" để được hỏi
# lần lượt các mục này, hoặc "Sửa" từng dòng. basement_depth_m chỉ hỏi khi có hầm, mezzanine_area_m2 chỉ hỏi khi có
# lửng. Khách trả lời "chưa biết" -> slot nằm trong `asked`, dùng mặc định có nêu rõ.
DETAIL_SLOTS: list[str] = ["building_area_m2", "basement", "has_mezzanine", "roof_type", "basement_depth_m",
                           "mezzanine_area_m2"]
# Slot chi tiết chỉ áp dụng khi slot điều kiện = True.
CONDITIONAL_DETAILS: dict[str, str] = {"basement_depth_m": "basement", "mezzanine_area_m2": "has_mezzanine"}

# Slot tùy chọn: KHÔNG hỏi trong phần hỏi (ngân sách hỏi SAU khái toán bằng nút chọn nhanh; phong cách / mức đầu tư
# chỉ ghi nhận khi khách tự nói). Không bao giờ chặn khái toán (không thuộc missing_required).
OPTIONAL_SLOTS: list[str] = ["design_style", "budget_vnd", "investment_level"]

# Thứ tự slot (mục 5.2). Phần hỏi trước bản tóm tắt chỉ gồm REQUIRED_SLOTS.
ASK_ORDER: list[str] = [*REQUIRED_SLOTS, *DETAIL_SLOTS, *OPTIONAL_SLOTS, "start_time"]

DESIGN_STYLE_VI: dict[str, str] = {
    "hien_dai": "Hiện đại", "toi_gian": "Tối giản", "tan_co_dien": "Tân cổ điển", "indochine": "Indochine",
}
INVESTMENT_LEVEL_VI: dict[str, str] = {
    "tiet_kiem": "Tiết kiệm", "pho_thong": "Phổ thông", "trung_cap": "Trung cấp", "sieu_cao_cap": "Siêu cao cấp",
}


def describe_design_style(value: str | None) -> str | None:
    """Mã phong cách -> nhãn tiếng Việt; chữ khách tự nhập giữ nguyên; None -> None."""
    if not value:
        return None
    return DESIGN_STYLE_VI.get(value, value)


def describe_investment_level(value: str | None) -> str | None:
    if not value:
        return None
    return INVESTMENT_LEVEL_VI.get(value, value)


def investment_note(value: str | None) -> str | None:
    """Ghi chú bắt buộc khi khách chọn mức đầu tư: mức đầu tư chỉ ghi nhận, không vào khái toán."""
    label = describe_investment_level(value)
    if not label:
        return None
    shown = label[:1].lower() + label[1:] if value in INVESTMENT_LEVEL_VI else f"“{label}”"
    return f"Mức đầu tư {shown} chưa được tính vào khái toán vì Dezon chưa có đơn giá theo mức đầu tư."


def project_preferences(slots: "Slots") -> list[str]:
    """Dòng mô tả mong muốn của khách (phong cách, mức đầu tư) gửi kèm yêu cầu báo giá."""
    out: list[str] = []
    if style := describe_design_style(slots.design_style):
        out.append(f"Phong cách thiết kế: {style}")
    if level := describe_investment_level(slots.investment_level):
        out.append(f"Mức đầu tư mong muốn: {level}")
    return out


def missing_core(slots: Slots) -> list[str]:
    """Slot cốt lõi (REQUIRED_SLOTS) còn thiếu — đủ thì tính được khái toán (chi tiết dùng mặc định)."""
    return [k for k in REQUIRED_SLOTS if getattr(slots, k) is None]


def details_apply(slots: Slots) -> bool:
    """Câu hỏi chi tiết kết cấu chỉ áp dụng cho công trình xây mới (không áp dụng căn hộ / phạm vi nội thất)."""
    return str(slots.building_type) != "can_ho" and str(slots.work_scope) != "noi_that"


def missing_required(slots: Slots) -> list[str]:
    """Thông tin còn phải hỏi trước khi tóm tắt: đúng 5 slot cốt lõi còn thiếu (chi tiết dùng mặc định có nêu rõ)."""
    return missing_core(slots)


def has_budget(slots: Slots) -> bool:
    """Khách đã nêu ngân sách: có trần (budget_vnd) hoặc chỉ có nhãn ("Trên 10 tỷ")."""
    return slots.budget_vnd is not None or bool(slots.budget_label)


def slot_value_known(slots: Slots, slot: str) -> bool:
    """Slot đã có giá trị (budget_vnd: kể cả khi khách chỉ nêu cận dưới — có budget_label)."""
    return has_budget(slots) if slot == "budget_vnd" else getattr(slots, slot) is not None


def missing_optional(slots: Slots) -> list[str]:
    """Câu hỏi tùy chọn chưa có giá trị và CHƯA hỏi khách (theo thứ tự OPTIONAL_SLOTS)."""
    return [k for k in OPTIONAL_SLOTS if not slot_value_known(slots, k) and k not in slots.asked]


def to_estimate_input(slots: Slots) -> EstimateInput:
    """Chuyển slot đã đủ sang EstimateInput. Gọi khi missing_core(slots) == []."""
    data = slots.model_dump(exclude={"budget_vnd", "budget_label", "start_time", "confirmed", "asked", "quality_tier",
                                     "design_style", "investment_level"},
                            exclude_none=True)
    # has_mezzanine/basement chỉ truyền khi khách đã trả lời (None -> không đặt) để engine phân biệt
    # "khách nói không" với "chưa có thông tin" khi ghi giả định. Diện tích lửng chỉ truyền khi có lửng (engine coi
    # mezzanine_area_m2 có giá trị là có lửng).
    if slots.has_mezzanine is not True:
        data.pop("mezzanine_area_m2", None)
    return EstimateInput(**data)
