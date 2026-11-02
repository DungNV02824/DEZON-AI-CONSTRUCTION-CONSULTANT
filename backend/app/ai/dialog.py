"""Logic hỏi tiếp & tóm tắt (đặc tả mục 5.1, 5.2, 5.4). Hàm thuần — không gọi LLM/DB.

- `greeting()` -> lời chào + quick replies loại công trình.
- `next_question(slots)` -> câu hỏi cho slot BẮT BUỘC còn thiếu (theo thứ tự 5.2, tối đa 1–2 thông tin/lượt)
  hoặc None khi đã đủ (slot tùy chọn như quality_tier dùng mặc định và nêu trong tóm tắt).
- `build_summary(slots)` -> "Em tóm tắt lại: ..." + SummaryConfirmBlock.
- `budget_question(slots)` -> hỏi ngân sách sau khái toán nếu chưa có.

`value` của mỗi quick reply là tin nhắn gửi lên khi khách bấm — `slot_filling.extract` hiểu được (có test).
"""
from __future__ import annotations

import re

from pydantic import BaseModel

from app.ai.normalize import fold
from app.formatting import NO_DATA, format_m2, format_vnd, vi_number
from app.schemas.common import BUILDING_TYPE_VI, QUALITY_TIER_VI, WORK_SCOPE_VI
from app.schemas.slots import Slots, missing_required
from app.schemas.ui_blocks import QuickRepliesBlock, QuickReply, SummaryConfirmBlock, SummaryItem


class NextQuestion(BaseModel):
    slots: list[str]  # slot đang hỏi — truyền lại cho extract(expected_slots=...) ở lượt sau
    text: str
    quick_replies: QuickRepliesBlock


def _qr(*pairs: tuple[str, str]) -> list[QuickReply]:
    return [QuickReply(label=label, value=value) for label, value in pairs]


BUILDING_TYPE_REPLIES = _qr(
    ("Nhà phố", "Nhà phố"), ("Biệt thự", "Biệt thự"), ("Căn hộ", "Căn hộ"), ("Văn phòng", "Văn phòng"),
    # value tự mô tả để extract() hiểu cả khi không có expected_slots (vd ngay sau lời chào).
    ("Khách sạn", "Khách sạn"), ("Khác", "Công trình khác"),
)
# Slot đang hỏi trong lời chào — orchestrator truyền vào extract(expected_slots=...) ở lượt đầu tiên.
GREETING_EXPECTED_SLOTS: list[str] = ["building_type"]
PROVINCE_REPLIES = _qr(("TP. Hồ Chí Minh", "TP. Hồ Chí Minh"), ("Hà Nội", "Hà Nội"), ("Đà Nẵng", "Đà Nẵng"))
LAND_REPLIES = _qr(("4 × 16 m", "Đất 4×16 m"), ("5 × 20 m", "Đất 5×20 m"))
FLOORS_REPLIES = _qr(("1 trệt 2 lầu", "1 trệt 2 lầu"), ("1 trệt 3 lầu", "1 trệt 3 lầu"))
WORK_SCOPE_REPLIES = _qr(("Xây thô", "Xây thô"), ("Hoàn thiện", "Hoàn thiện"), ("Trọn gói", "Trọn gói"),
                         ("Nội thất", "Nội thất"))
QUALITY_TIER_REPLIES = _qr(("Tiết kiệm", "Mức tiết kiệm"), ("Trung bình", "Mức trung bình"),
                           ("Khá", "Mức khá"), ("Cao cấp", "Mức cao cấp"))
BUDGET_REPLIES = _qr(("Dưới 1,5 tỷ", "Ngân sách dưới 1,5 tỷ"), ("1,5–2,5 tỷ", "Ngân sách 1,5–2,5 tỷ"),
                     ("2,5–4 tỷ", "Ngân sách 2,5–4 tỷ"), ("Trên 4 tỷ", "Ngân sách trên 4 tỷ"))
START_TIME_REPLIES = _qr(("Trong 3 tháng", "Khởi công trong 3 tháng"), ("3–6 tháng", "Khởi công sau 3–6 tháng"),
                         ("Chưa xác định", "Thời gian khởi công chưa xác định"))

GREETING_TEXT = (
    "Xin chào anh/chị! Em có thể hỗ trợ khái toán chi phí xây dựng và tìm nhà thầu phù hợp trên Dezon. "
    "Anh/chị đang có nhu cầu xây dựng loại công trình nào ạ?"
)


def greeting() -> tuple[str, QuickRepliesBlock]:
    return GREETING_TEXT, QuickRepliesBlock(options=BUILDING_TYPE_REPLIES)


def next_question(slots: Slots) -> NextQuestion | None:
    """Câu hỏi cho slot bắt buộc còn thiếu đầu tiên (thứ tự 5.2). Đủ slot bắt buộc -> None."""
    missing = missing_required(slots)
    if not missing:
        return None
    if "building_type" in missing:
        return NextQuestion(
            slots=["building_type"],
            text="Anh/chị đang có nhu cầu xây dựng loại công trình nào ạ?",
            quick_replies=QuickRepliesBlock(options=BUILDING_TYPE_REPLIES),
        )
    if "province" in missing:
        return NextQuestion(
            slots=["province", "district"],
            text="Dạ, anh/chị dự định xây dựng ở khu vực nào ạ? (tỉnh/thành phố, quận/huyện nếu có)",
            quick_replies=QuickRepliesBlock(options=PROVINCE_REPLIES),
        )
    need_land, need_floors = "land_area_m2" in missing, "floors" in missing
    if need_land and need_floors:
        return NextQuestion(
            slots=["land_area_m2", "floors"],
            text="Anh/chị cho em xin thêm diện tích đất và số tầng dự kiến nhé.",
            quick_replies=QuickRepliesBlock(options=LAND_REPLIES + FLOORS_REPLIES),
        )
    if need_land:
        return NextQuestion(
            slots=["land_area_m2"],
            text="Anh/chị cho em xin diện tích đất (ngang × dài hoặc tổng m²) nhé.",
            quick_replies=QuickRepliesBlock(options=LAND_REPLIES),
        )
    if need_floors:
        return NextQuestion(
            slots=["floors"],
            text="Anh/chị dự kiến xây mấy tầng ạ? (ví dụ 1 trệt 3 lầu)",
            quick_replies=QuickRepliesBlock(options=FLOORS_REPLIES),
        )
    if "work_scope" in missing:
        return NextQuestion(
            slots=["work_scope"],
            text="Anh/chị muốn khái toán cho phạm vi nào ạ: xây thô, hoàn thiện, trọn gói hay nội thất?",
            quick_replies=QuickRepliesBlock(options=WORK_SCOPE_REPLIES),
        )
    return None  # pragma: no cover — REQUIRED_SLOTS mới chưa có câu hỏi


def budget_question(slots: Slots) -> NextQuestion | None:
    """Hỏi ngân sách (dùng sau khái toán). Đã có ngân sách hoặc đã hỏi rồi -> None."""
    if slots.budget_vnd is not None or "budget_vnd" in slots.asked:
        return None
    return NextQuestion(
        slots=["budget_vnd"],
        text="Anh/chị dự kiến ngân sách khoảng bao nhiêu để em đề xuất nhà thầu phù hợp ạ?",
        quick_replies=QuickRepliesBlock(options=BUDGET_REPLIES),
    )


# ---------------------------------------------------------------------------
# Sửa thông tin (C-F9) và không có nhà thầu (C-F11)
# ---------------------------------------------------------------------------
# value: tin nhắn "Tôi muốn sửa <mục>" -> detect_intent = edit, parse_message không trích slot nào (có test).
EDIT_FIELD_REPLIES = _qr(
    ("Loại công trình", "Tôi muốn sửa loại công trình"), ("Khu vực", "Tôi muốn sửa khu vực"),
    ("Diện tích đất", "Tôi muốn sửa diện tích đất"), ("Số tầng", "Tôi muốn sửa số tầng"),
    ("Phạm vi", "Tôi muốn sửa phạm vi công việc"), ("Mức hoàn thiện", "Tôi muốn sửa mức chất lượng"),
    ("Giữ nguyên", "Thông tin đúng rồi"),
)
# (mẫu trên chuỗi đã gập dấu, slot, câu hỏi, quick replies) — thứ tự: mẫu cụ thể trước.
_EDIT_FIELDS: list[tuple[re.Pattern[str], list[str], str, list[QuickReply]]] = [
    (re.compile(r"muc hoan thien|chat luong|vat tu"), ["quality_tier"],
     "Dạ, anh/chị muốn chọn mức hoàn thiện nào ạ?", QUALITY_TIER_REPLIES),
    (re.compile(r"loai cong trinh|loai nha"), ["building_type"],
     "Dạ, anh/chị muốn đổi sang loại công trình nào ạ?", BUILDING_TYPE_REPLIES),
    (re.compile(r"khu vuc|dia diem|tinh thanh|thanh pho|quan huyen"), ["province", "district"],
     "Dạ, anh/chị dự định xây dựng ở khu vực nào ạ? (tỉnh/thành phố, quận/huyện nếu có)", PROVINCE_REPLIES),
    (re.compile(r"dien tich|kich thuoc|\bdat\b"), ["land_area_m2"],
     "Dạ, anh/chị cho em xin lại diện tích đất (ngang × dài hoặc tổng m²) nhé.", LAND_REPLIES),
    (re.compile(r"so tang|\btang\b|\blau\b"), ["floors"],
     "Dạ, anh/chị muốn đổi thành mấy tầng ạ? (ví dụ 1 trệt 2 lầu)", FLOORS_REPLIES),
    (re.compile(r"pham vi|hang muc"), ["work_scope"],
     "Dạ, anh/chị muốn khái toán cho phạm vi nào ạ: xây thô, hoàn thiện, trọn gói hay nội thất?",
     WORK_SCOPE_REPLIES),
]


def edit_field_question(message: str) -> NextQuestion | None:
    """Khách nói muốn sửa một mục nhưng chưa nêu giá trị mới ("Tôi muốn sửa số tầng") -> hỏi đúng mục đó.
    Không nêu mục nào -> None (orchestrator hỏi "sửa thông tin nào" kèm EDIT_FIELD_REPLIES)."""
    f = fold(message)
    for rx, slots, text, replies in _EDIT_FIELDS:
        if rx.search(f):
            return NextQuestion(slots=slots, text=text, quick_replies=QuickRepliesBlock(options=replies))
    return None


def no_match_replies(province: str | None) -> list[QuickReply]:
    """Bước tiếp khi không có nhà thầu phù hợp ở khu vực của khách: tìm ở thành phố khác, sửa thông tin."""
    others = [r for r in PROVINCE_REPLIES if not province or fold(r.value) != fold(province)][:2]
    return ([QuickReply(label=f"Tìm nhà thầu tại {r.label}", value=f"Tìm nhà thầu tại {r.value}") for r in others]
            + [QuickReply(label="Sửa thông tin công trình", value="Tôi muốn sửa lại thông tin")])


# ---------------------------------------------------------------------------
# Tóm tắt
# ---------------------------------------------------------------------------


def describe_land(slots: Slots) -> str:
    """"5 × 20 m (100 m²)" | "100 m²" | "Chưa có dữ liệu"."""
    if slots.land_width_m and slots.land_length_m:
        area = slots.land_area_m2 or slots.land_width_m * slots.land_length_m
        return f"{vi_number(slots.land_width_m)} × {vi_number(slots.land_length_m)} m ({format_m2(area)})"
    if slots.land_area_m2:
        return format_m2(slots.land_area_m2)
    return NO_DATA


def describe_floors(slots: Slots) -> str:
    """"1 trệt 3 lầu (4 tầng)"; có lửng: "1 trệt 1 lửng 2 lầu (3 tầng + lửng)"; 1 tầng: "1 trệt (1 tầng)"."""
    if not slots.floors:
        return NO_DATA
    parts = ["1 trệt"]
    if slots.has_mezzanine:
        parts.append("1 lửng")
    if slots.floors > 1:
        parts.append(f"{slots.floors - 1} lầu")
    suffix = f"{slots.floors} tầng" + (" + lửng" if slots.has_mezzanine else "")
    return f"{' '.join(parts)} ({suffix})"


def describe_location(slots: Slots) -> str:
    if not slots.province:
        return NO_DATA
    return f"{slots.district}, {slots.province}" if slots.district else slots.province


def default_assumptions(slots: Slots) -> list[str]:
    """Các mặc định sẽ áp dụng khi khái toán (mục 6.2) — luôn hiển thị cho khách."""
    out: list[str] = []
    if slots.quality_tier is None:
        out.append("Mức hoàn thiện trung bình (anh/chị chưa chọn).")
    if slots.building_area_m2 is None:
        land = f" ({format_m2(slots.land_area_m2)})" if slots.land_area_m2 else ""
        out.append(f"Diện tích xây dựng mỗi tầng bằng diện tích đất{land}.")
    # Phạm vi hoàn thiện/nội thất và căn hộ không tính móng (C-F12) -> không nêu giả định móng.
    no_foundation = str(slots.work_scope) in ("hoan_thien", "noi_that") or str(slots.building_type) == "can_ho"
    if slots.foundation_type is None and not no_foundation:
        if slots.floors and slots.floors >= 4:
            out.append("Móng cọc (nhà từ 4 tầng nổi trở lên), chưa gồm chi phí ép cọc.")
        else:
            out.append("Móng băng (nhà đến 3 tầng nổi).")
    if slots.roof_type is None:
        out.append("Mái bê tông cốt thép, diện tích mái bằng diện tích xây dựng.")
    if slots.basement is None:
        out.append("Không có tầng hầm.")
    if slots.has_mezzanine is None:
        out.append("Không có tầng lửng.")
    return out


_ROOF_VI = {"roof_metal": "Mái tôn", "roof_concrete": "Mái bê tông cốt thép", "roof_tile_on_truss": "Mái ngói kèo thép",
            "roof_tile_on_concrete": "Mái ngói đổ BTCT", "none": "Không có mái"}
_ROOFTOP_VI = {"none": "Không có", "covered": "Có mái che", "open": "Không mái che"}
_FOUNDATION_VI = {"foundation_single": "Móng đơn", "foundation_strip": "Móng băng", "foundation_pile": "Móng cọc"}


def build_summary(slots: Slots) -> tuple[str, SummaryConfirmBlock]:
    """Tóm tắt để khách xác nhận trước khi khái toán (mục 5.3, ví dụ 5.4)."""
    btype = BUILDING_TYPE_VI.get(str(slots.building_type), NO_DATA) if slots.building_type else NO_DATA
    scope = WORK_SCOPE_VI.get(str(slots.work_scope), NO_DATA) if slots.work_scope else NO_DATA
    if slots.quality_tier:
        quality = QUALITY_TIER_VI[str(slots.quality_tier)]
    else:
        quality = f"{QUALITY_TIER_VI['trung_binh']} (mặc định)"

    items = [
        SummaryItem(label="Loại công trình", value=btype),
        SummaryItem(label="Khu vực", value=describe_location(slots)),
        SummaryItem(label="Diện tích đất", value=describe_land(slots)),
        SummaryItem(label="Số tầng", value=describe_floors(slots)),
        SummaryItem(label="Phạm vi", value=scope),
        SummaryItem(label="Mức hoàn thiện", value=quality),
    ]
    if slots.building_area_m2:
        items.append(SummaryItem(label="Diện tích xây dựng mỗi tầng", value=format_m2(slots.building_area_m2)))
    if slots.basement:
        depth = f" (sâu {vi_number(slots.basement_depth_m)} m)" if slots.basement_depth_m else ""
        items.append(SummaryItem(label="Tầng hầm", value=f"Có{depth}"))
    if slots.roof_type:
        items.append(SummaryItem(label="Mái", value=_ROOF_VI[slots.roof_type]))
    if slots.rooftop_terrace:
        items.append(SummaryItem(label="Sân thượng", value=_ROOFTOP_VI[slots.rooftop_terrace]))
    if slots.foundation_type:
        items.append(SummaryItem(label="Móng", value=_FOUNDATION_VI[slots.foundation_type]))
    if slots.budget_vnd:
        items.append(SummaryItem(label="Ngân sách", value=format_vnd(slots.budget_vnd)))
    if slots.start_time:
        items.append(SummaryItem(label="Thời gian khởi công", value=slots.start_time))

    # Câu tóm tắt dạng văn xuôi như ví dụ 5.4.
    phrase: list[str] = []
    where = f" tại {describe_location(slots)}" if slots.province else ""
    if slots.building_type and str(slots.building_type) != "khac":
        phrase.append(f"{btype}{where}")
    elif slots.building_type:
        phrase.append(f"Công trình khác{where}")
    else:
        phrase.append(f"Công trình{where}")
    if slots.land_area_m2:
        phrase.append(f"đất {describe_land(slots)}")
    if slots.floors:
        phrase.append(describe_floors(slots))
    if slots.work_scope:
        phrase.append(scope.lower())
    phrase.append(f"mức hoàn thiện {quality.lower()}")
    if slots.budget_vnd:
        phrase.append(f"ngân sách {format_vnd(slots.budget_vnd)}")
    text = f"Em tóm tắt lại: {', '.join(phrase)}. "
    missing = missing_required(slots)
    if missing:
        text += "Em còn thiếu một vài thông tin, anh/chị bổ sung giúp em nhé."
    else:
        text += "Anh/chị xác nhận để em tính khái toán nhé?"

    block = SummaryConfirmBlock(items=items, assumptions=default_assumptions(slots))
    return text, block
