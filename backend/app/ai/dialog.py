"""Logic hỏi tiếp & tóm tắt (đặc tả mục 5.1, 5.2, 5.4). Hàm thuần — không gọi LLM/DB.

- `greeting()` -> lời chào + thẻ question_step hỏi loại công trình.
- `next_question(slots)` -> câu hỏi kế tiếp trong ĐÚNG 5 câu cốt lõi (chủ sản phẩm): 1/5 Loại công trình · 2/5 Khu
  vực · 3/5 Diện tích đất (căn hộ: diện tích căn hộ) · 4/5 Số tầng · 5/5 Phạm vi thi công. Câu đã có thông tin thì
  bỏ qua nhưng số bước giữ cố định (bước = vị trí câu hỏi trong danh sách). Hết câu hỏi -> None (hiện bản tóm tắt).
- Chi tiết (diện tích xây dựng, hầm/lửng, mái, rồi độ sâu hầm / diện tích lửng nếu có) KHÔNG hỏi trước bản tóm tắt:
  dùng mặc định của engine, nêu rõ ở mục "Giả định". `next_question(slots, more=True)` (khách bấm "Cung cấp thêm
  thông tin") hỏi lần lượt các mục chi tiết còn thiếu (bước i/N của nhóm câu hỏi thêm), rồi quay lại bản tóm tắt.
  Khách trả lời "chưa biết" -> slot nằm trong `slots.asked`, dùng mặc định.
- `step_block(q, slots)` -> thẻ QuestionStepBlock (tiêu đề, gợi ý, các dòng đánh số, chip, ô nhập, bước i/N).
- `build_summary(slots)` -> "Em tóm tắt lại: ..." + SummaryConfirmBlock (nút Sửa từng dòng, Tính ngay, Cung cấp thêm).
- `budget_question(slots)` -> hỏi ngân sách SAU khái toán (không hỏi trong phần hỏi trước bản tóm tắt).
- Phong cách / mức đầu tư: không hỏi; chỉ ghi nhận khi khách tự nói (vẫn sửa được nếu đã có).

`value` của mỗi lựa chọn là tin nhắn gửi lên khi khách bấm — `slot_filling.extract` hiểu được (có test).
"""
from __future__ import annotations

import re

from pydantic import BaseModel

from app.ai.normalize import fold
from app.formatting import NO_DATA, format_m2, format_vnd, vi_number
from app.schemas.common import BUILDING_TYPE_VI, WORK_SCOPE_VI
from app.schemas.slots import (
    CONDITIONAL_DETAILS,
    DESIGN_STYLE_VI,
    DETAIL_SLOTS,
    INVESTMENT_LEVEL_VI,
    OPTIONAL_SLOTS,
    Slots,
    describe_design_style,
    describe_investment_level,
    details_apply,
    has_budget,
    investment_note,
    missing_required,
    slot_value_known,
)
from app.schemas.ui_blocks import (
    QuestionStepBlock,
    QuickRepliesBlock,
    QuickReply,
    SummaryConfirmBlock,
    SummaryItem,
)

# Tin nhắn do các nút điều hướng của thẻ câu hỏi / bản xác nhận gửi lên (frontend dùng đúng các chuỗi này).
BACK_VALUE = "Quay lại câu trước"
SKIP_VALUE = "Bỏ qua câu này"
MORE_LABEL = "Cung cấp thêm thông tin để báo giá chính xác hơn"
MORE_VALUE = "Tôi muốn cung cấp thêm thông tin"
CONFIRM_LABEL = "Tính ngay → xem bảng khái toán"
CONFIRM_VALUE = "Đúng rồi, tính khái toán giúp tôi"  # detect_intent -> confirm
PLACEHOLDER_TEXT = "Nhập câu trả lời của anh/chị"
PLACEHOLDER_AREA = "Nhập diện tích (m²)"


class NextQuestion(BaseModel):
    slots: list[str]  # slot đang hỏi — truyền lại cho extract(expected_slots=...) ở lượt sau
    text: str  # câu hỏi dạng văn xuôi (cho LLM biết hệ thống đang hỏi gì; không nối vào tin nhắn khi có thẻ)
    quick_replies: QuickRepliesBlock  # chip + lựa chọn (dùng khi hỏi bằng nút chọn nhanh, vd ngân sách sau khái toán)
    key: str = ""  # khóa câu hỏi trong QUESTION_KEYS
    title: str = ""  # tiêu đề thẻ question_step
    hint: str | None = None
    options: list[QuickReply] = []
    chips: list[QuickReply] = []
    placeholder: str | None = PLACEHOLDER_TEXT
    skippable: bool = False


def _qr(*pairs: tuple[str, str]) -> list[QuickReply]:
    return [QuickReply(label=label, value=value) for label, value in pairs]


def _lc(text: str) -> str:
    return text[:1].lower() + text[1:]


def _question(key: str, slots: list[str], text: str, title: str, options: list[QuickReply],
              chips: list[QuickReply] | None = None, hint: str | None = None,
              placeholder: str | None = PLACEHOLDER_TEXT, skippable: bool = False) -> NextQuestion:
    chips = list(chips or [])
    return NextQuestion(slots=slots, text=text, quick_replies=QuickRepliesBlock(options=[*chips, *options]), key=key,
                        title=title, hint=hint, options=list(options), chips=chips, placeholder=placeholder,
                        skippable=skippable)


BUILDING_TYPE_REPLIES = _qr(
    ("Nhà phố", "Nhà phố"), ("Biệt thự", "Biệt thự"), ("Căn hộ", "Căn hộ"), ("Văn phòng", "Văn phòng"),
    # value tự mô tả để extract() hiểu cả khi không có expected_slots (vd ngay sau lời chào).
    ("Khách sạn", "Khách sạn"), ("Khác", "Công trình khác"),
)
# Slot đang hỏi trong lời chào — orchestrator truyền vào extract(expected_slots=...) ở lượt đầu tiên.
GREETING_EXPECTED_SLOTS: list[str] = ["building_type"]
PROVINCE_REPLIES = _qr(("TP. Hồ Chí Minh", "TP. Hồ Chí Minh"), ("Hà Nội", "Hà Nội"), ("Đà Nẵng", "Đà Nẵng"))
# Kích thước đất (chip): value parse ra ngang × dài (normalize.parse_dimensions).
LAND_REPLIES = _qr(("4 × 10", "Đất 4×10 m"), ("5 × 10", "Đất 5×10 m"), ("6 × 10", "Đất 6×10 m"),
                   ("6 × 12", "Đất 6×12 m"), ("9 × 12", "Đất 9×12 m"), ("12 × 10", "Đất 12×10 m"))
FLOORS_REPLIES = _qr(("1 trệt", "1 trệt"), ("1 trệt 1 lầu", "1 trệt 1 lầu"), ("1 trệt 2 lầu", "1 trệt 2 lầu"),
                     ("1 trệt 3 lầu", "1 trệt 3 lầu"), ("1 trệt 4 lầu", "1 trệt 4 lầu"))
# Căn hộ: diện tích sử dụng (value parse ra land_area_m2) và số tầng của chính căn hộ.
APARTMENT_AREA_REPLIES = _qr(*((f"{a} m²", f"Diện tích căn hộ {a} m2") for a in (50, 80, 120, 200, 300)))
APARTMENT_FLOORS_REPLIES = _qr(("1 tầng", "Căn hộ 1 tầng"), ("2 tầng (duplex)", "Căn hộ 2 tầng (duplex)"))
WORK_SCOPE_REPLIES = _qr(("Xây thô", "Xây thô"), ("Hoàn thiện", "Hoàn thiện"), ("Trọn gói", "Trọn gói"),
                         ("Nội thất", "Nội thất"))
# Câu hỏi chi tiết (chỉ khi khách bấm "Cung cấp thêm thông tin" / "Sửa"). value tự mô tả để parse_message hiểu.
BUILDING_AREA_UNKNOWN = QuickReply(label="Chưa biết", value="Tôi chưa biết diện tích xây dựng")
BASEMENT_MEZZ_REPLIES = _qr(("Không hầm, không lửng", "Không hầm, không lửng"),
                            ("Có tầng lửng", "Có tầng lửng, không hầm"),
                            ("Có tầng hầm", "Có tầng hầm, không lửng"),
                            ("Có cả hầm và lửng", "Có tầng hầm và tầng lửng"))
BASEMENT_ONLY_REPLIES = _qr(("Không có hầm", "Không có tầng hầm"), ("Có tầng hầm", "Có tầng hầm"))
MEZZ_ONLY_REPLIES = _qr(("Không có lửng", "Không có tầng lửng"), ("Có tầng lửng", "Có tầng lửng"))
BASEMENT_DEPTH_REPLIES = _qr(("Dưới 1,3 m", "Hầm sâu 1,2 m"), ("1,3–1,7 m", "Hầm sâu 1,5 m"),
                             ("1,7–2 m", "Hầm sâu 1,9 m"), ("Trên 2 m", "Hầm sâu 2,5 m"),
                             ("Chưa biết", "Tôi chưa biết độ sâu hầm"))
ROOF_REPLIES = _qr(("Mái bằng bê tông", "Mái bằng bê tông"), ("Mái tôn", "Mái tôn"),
                   ("Mái ngói kèo thép", "Mái ngói kèo thép"), ("Mái ngói đổ bê tông", "Mái ngói đổ bê tông"),
                   ("Chưa biết", "Tôi chưa biết loại mái"))
LAND_TEXT = "Anh/chị cho em xin diện tích đất (ngang × dài hoặc tổng m²) nhé."
APARTMENT_AREA_TEXT = "Căn hộ của anh/chị rộng khoảng bao nhiêu m² ạ?"
FLOORS_TEXT = "Anh/chị dự kiến xây mấy tầng ạ? (ví dụ 1 trệt 3 lầu)"
APARTMENT_FLOORS_TEXT = "Căn hộ của anh/chị có mấy tầng ạ? (căn duplex là 2 tầng)"
BUILDING_AREA_TEXT = "Diện tích xây dựng mỗi tầng khoảng bao nhiêu m² ạ? (phần sàn xây, không tính sân)"
BASEMENT_MEZZ_TEXT = "Nhà có tầng hầm hoặc tầng lửng không ạ?"
MEZZANINE_AREA_TEXT = "Tầng lửng rộng khoảng bao nhiêu m² ạ?"
MEZZANINE_AREA_UNKNOWN = QuickReply(label="Chưa biết", value="Tôi chưa biết diện tích tầng lửng")
BASEMENT_DEPTH_TEXT = "Tầng hầm dự kiến sâu khoảng bao nhiêu mét ạ? (tính từ mặt đất xuống sàn hầm)"
ROOF_TEXT = "Mái nhà dự kiến làm loại nào ạ?"
# Câu hỏi tùy chọn — KHÔNG hỏi trong phần hỏi; chỉ dùng khi khách sửa mục đã có (phong cách / mức đầu tư chỉ ghi
# nhận). Ngân sách hỏi sau khái toán (budget_question) bằng nút chọn nhanh BUDGET_REPLIES.
DESIGN_STYLE_REPLIES = _qr(("Hiện đại", "Phong cách hiện đại"), ("Tối giản", "Phong cách tối giản"),
                           ("Tân cổ điển", "Phong cách tân cổ điển"), ("Indochine", "Phong cách Indochine"))
BUDGET_REPLIES = _qr(("<2 tỷ", "Ngân sách dưới 2 tỷ"), ("2–4 tỷ", "Ngân sách 2–4 tỷ"), ("4–6 tỷ", "Ngân sách 4–6 tỷ"),
                     ("6–10 tỷ", "Ngân sách 6–10 tỷ"), (">10 tỷ", "Ngân sách trên 10 tỷ"))
INVESTMENT_LEVEL_REPLIES = _qr(("Tiết kiệm", "Mức đầu tư tiết kiệm"), ("Phổ thông", "Mức đầu tư phổ thông"),
                               ("Trung cấp", "Mức đầu tư trung cấp"), ("Siêu cao cấp", "Mức đầu tư siêu cao cấp"))
DESIGN_STYLE_TEXT = "Anh/chị mong muốn phong cách thiết kế nào ạ?"
BUDGET_TEXT = "Anh/chị dự kiến ngân sách khoảng bao nhiêu ạ?"
INVESTMENT_LEVEL_TEXT = "Anh/chị mong muốn mức đầu tư nào ạ?"
BUDGET_HINT = "Có ngân sách giúp em tư vấn sát hơn và tự gợi ý phân khúc phù hợp ạ."
START_TIME_REPLIES = _qr(("Trong 3 tháng", "Khởi công trong 3 tháng"), ("3–6 tháng", "Khởi công sau 3–6 tháng"),
                         ("Chưa xác định", "Thời gian khởi công chưa xác định"))

GREETING_TEXT = ("Xin chào anh/chị! Em có thể hỗ trợ khái toán chi phí xây dựng và tìm nhà thầu phù hợp trên Dezon. "
                 "Anh/chị chọn loại công trình bên dưới để em bắt đầu nhé.")

# Khóa câu hỏi. Phần hỏi trước bản tóm tắt: ĐÚNG 5 câu cốt lõi (CORE_KEYS), bước i/5 cố định. Câu chi tiết
# (DETAIL_KEYS) chỉ hỏi khi khách bấm "Cung cấp thêm thông tin" (bước i/N của nhóm này) hoặc "Sửa" một dòng. Câu tùy
# chọn (OPTIONAL_KEYS) không bao giờ hỏi trong luồng — chỉ khi khách sửa mục đã có.
QUESTION_KEYS: list[str] = ["building_type", "province", "land", "floors", "work_scope", "building_area",
                            "basement_mezz", "roof", "basement_depth", "mezzanine_area", "design_style", "budget",
                            "investment"]
CORE_KEYS = QUESTION_KEYS[:5]
DETAIL_KEYS = QUESTION_KEYS[5:10]
OPTIONAL_KEYS = QUESTION_KEYS[10:]
CORE_TOTAL = len(CORE_KEYS)
# Khóa câu hỏi -> slot của câu hỏi (câu hỏi "xong" khi mọi slot đã có giá trị hoặc đã hỏi — slot chi tiết/tùy chọn).
KEY_SLOTS: dict[str, list[str]] = {
    "building_type": ["building_type"], "province": ["province"], "land": ["land_area_m2"], "floors": ["floors"],
    "work_scope": ["work_scope"], "building_area": ["building_area_m2"], "basement_mezz": ["basement", "has_mezzanine"],
    "mezzanine_area": ["mezzanine_area_m2"], "basement_depth": ["basement_depth_m"], "roof": ["roof_type"],
    "design_style": ["design_style"], "budget": ["budget_vnd"], "investment": ["investment_level"],
}
_SLOT_KEY = {s: k for k, ss in KEY_SLOTS.items() for s in ss}


def _is_apartment(slots: Slots) -> bool:
    return str(slots.building_type) == "can_ho"


def planned_keys(slots: Slots | None = None) -> list[str]:
    """Danh sách câu hỏi của phần hỏi trước bản tóm tắt: luôn đúng 5 câu cốt lõi (bước i/5 cố định)."""
    return list(CORE_KEYS)


def detail_keys(slots: Slots) -> list[str]:
    """Nhóm câu hỏi thêm ("Cung cấp thêm thông tin") áp dụng cho công trình: diện tích xây dựng, hầm/lửng, mái, rồi
    độ sâu hầm (nếu có hầm), diện tích lửng (nếu có lửng). Căn hộ / phạm vi nội thất: không có."""
    if not details_apply(slots):
        return []
    out: list[str] = []
    for k in DETAIL_KEYS:
        cond = CONDITIONAL_DETAILS.get(KEY_SLOTS[k][0])
        if cond is None or getattr(slots, cond) is True:
            out.append(k)
    return out


def more_plan(slots: Slots, base: list[str]) -> list[str]:
    """Danh sách câu của MỘT lượt "Cung cấp thêm thông tin" (bước i/N tính trên danh sách này): `base` = câu chi tiết
    còn thiếu lúc khách bấm nút (state.more_keys); khách vừa trả lời có hầm / có lửng trong lượt -> thêm độ sâu hầm /
    diện tích lửng ở cuối. Câu không còn áp dụng (vd đổi sang căn hộ) bị loại."""
    applicable = detail_keys(slots)
    out = [k for k in base if k in applicable]
    if "basement_mezz" in out:
        out += [k for k in ("basement_depth", "mezzanine_area") if k in applicable and k not in out]
    return out


def _slot_done(slots: Slots, slot: str) -> bool:
    if slot_value_known(slots, slot):
        return True
    return (slot in DETAIL_SLOTS or slot in OPTIONAL_SLOTS) and slot in slots.asked


def key_done(slots: Slots, key: str) -> bool:
    return all(_slot_done(slots, s) for s in KEY_SLOTS[key])


def current_key(slots: Slots, more: bool = False, plan: list[str] | None = None) -> str | None:
    """Câu hỏi kế tiếp: câu cốt lõi đầu tiên chưa có thông tin; `more` (khách bấm "Cung cấp thêm thông tin"): đủ cốt
    lõi -> câu chi tiết đầu tiên chưa có thông tin và chưa hỏi (trong `plan` của lượt nếu có)."""
    for k in CORE_KEYS:
        if not key_done(slots, k):
            return k
    if more:
        for k in (plan if plan is not None else detail_keys(slots)):
            if not key_done(slots, k):
                return k
    return None


def greeting() -> tuple[str, QuestionStepBlock]:
    q = question_for_key("building_type", Slots())
    return GREETING_TEXT, step_block(q, Slots())


def next_question(slots: Slots, more: bool = False, plan: list[str] | None = None) -> NextQuestion | None:
    """Câu hỏi kế tiếp (5 câu cốt lõi; `more`: thêm nhóm câu chi tiết). Không còn câu nào để hỏi -> None."""
    key = current_key(slots, more, plan)
    return question_for_key(key, slots) if key else None


def question_for_key(key: str, slots: Slots) -> NextQuestion:
    """Câu hỏi của một khóa (dùng cả khi hỏi lại câu đã trả lời: "Quay lại câu trước", "Sửa")."""
    apartment = _is_apartment(slots)
    if key == "building_type":
        return _question(key, ["building_type"], "Anh/chị đang có nhu cầu xây dựng loại công trình nào ạ?",
                         "Loại công trình anh/chị dự định xây dựng?", BUILDING_TYPE_REPLIES)
    if key == "province":
        return _question(key, ["province", "district"],
                         "Dạ, anh/chị dự định xây dựng ở khu vực nào ạ? (tỉnh/thành phố, quận/huyện nếu có)",
                         "Khu vực xây dựng?", PROVINCE_REPLIES,
                         hint="Tỉnh/thành phố, kèm quận/huyện nếu có — đơn giá khác nhau theo khu vực.",
                         placeholder="Nhập tỉnh/thành phố, quận/huyện")
    if key == "land" and apartment:
        return _question(key, ["land_area_m2"], APARTMENT_AREA_TEXT, "Diện tích căn hộ (m²)?", APARTMENT_AREA_REPLIES,
                         hint="Diện tích sử dụng của căn hộ (theo sổ hồng hoặc hợp đồng mua bán).",
                         placeholder=PLACEHOLDER_AREA)
    if key == "land":
        return _question(key, ["land_area_m2"], LAND_TEXT, "Diện tích đất?", [], chips=LAND_REPLIES,
                         hint="Chọn kích thước đất ngang × dài (m), hoặc nhập tổng diện tích đất (m²).",
                         placeholder="Nhập ngang × dài (m) hoặc tổng m²")
    if key == "floors" and apartment:
        return _question(key, ["floors"], APARTMENT_FLOORS_TEXT, "Căn hộ có mấy tầng?", APARTMENT_FLOORS_REPLIES,
                         hint="Số tầng của chính căn hộ — căn duplex là 2 tầng.", placeholder="Nhập số tầng")
    if key == "floors":
        return _question(key, ["floors"], FLOORS_TEXT, "Số tầng dự kiến?", FLOORS_REPLIES,
                         hint="Tầng trệt tính là 1 tầng; hầm, lửng (nếu có) anh/chị bổ sung ở bản xác nhận.",
                         placeholder="Nhập số tầng (vd 1 trệt 3 lầu)")
    if key == "work_scope":
        return _question(key, ["work_scope"],
                         "Anh/chị muốn khái toán cho phạm vi nào ạ: xây thô, hoàn thiện, trọn gói hay nội thất?",
                         "Phạm vi thi công cần khái toán?", WORK_SCOPE_REPLIES,
                         hint="Trọn gói gồm phần thô và hoàn thiện.")
    if key == "building_area":
        return _question(key, ["building_area_m2"], BUILDING_AREA_TEXT, "Diện tích xây dựng mỗi tầng (m²)?",
                         building_area_replies(slots), hint="Phần sàn xây, không tính sân.",
                         placeholder=PLACEHOLDER_AREA)
    if key == "basement_mezz":
        need_basement = not _slot_done(slots, "basement")
        need_mezz = not _slot_done(slots, "has_mezzanine")
        if need_basement and not need_mezz:
            return _question(key, ["basement"], "Nhà có tầng hầm không ạ?", "Nhà có tầng hầm không?",
                             BASEMENT_ONLY_REPLIES)
        if need_mezz and not need_basement:
            return _question(key, ["has_mezzanine"], "Nhà có tầng lửng không ạ?", "Nhà có tầng lửng không?",
                             MEZZ_ONLY_REPLIES)
        return _question(key, ["basement", "has_mezzanine"], BASEMENT_MEZZ_TEXT,
                         "Nhà có tầng hầm hoặc tầng lửng không?", BASEMENT_MEZZ_REPLIES)
    if key == "mezzanine_area":
        return _question(key, ["mezzanine_area_m2"], MEZZANINE_AREA_TEXT, "Diện tích tầng lửng (m²)?",
                         mezzanine_area_replies(slots), placeholder=PLACEHOLDER_AREA)
    if key == "basement_depth":
        return _question(key, ["basement_depth_m"], BASEMENT_DEPTH_TEXT, "Độ sâu tầng hầm?", BASEMENT_DEPTH_REPLIES,
                         hint="Tính từ mặt đất xuống sàn hầm.", placeholder="Nhập độ sâu (m)")
    if key == "roof":
        return _question(key, ["roof_type"], ROOF_TEXT, "Loại mái dự kiến?", ROOF_REPLIES)
    if key == "design_style":
        return _question(key, ["design_style"], DESIGN_STYLE_TEXT, "Phong cách thiết kế mong muốn?",
                         DESIGN_STYLE_REPLIES, hint="Em ghi nhận để gửi kèm yêu cầu báo giá cho nhà thầu ạ.",
                         skippable=True)
    if key == "budget":
        return _question(key, ["budget_vnd"], BUDGET_TEXT, "Ngân sách dự kiến?", BUDGET_REPLIES, hint=BUDGET_HINT,
                         placeholder="Nhập ngân sách (vd 3,5 tỷ)", skippable=True)
    if key == "investment":
        return _question(key, ["investment_level"], INVESTMENT_LEVEL_TEXT, "Mức đầu tư mong muốn?",
                         INVESTMENT_LEVEL_REPLIES,
                         hint="Em ghi nhận để gửi nhà thầu; mức đầu tư chưa được tính vào khái toán ạ.",
                         skippable=True)
    raise KeyError(key)


def key_of(slot_names: list[str] | None) -> str | None:
    """Khóa câu hỏi ứng với danh sách slot đang hỏi (state.expected_slots)."""
    for s in slot_names or []:
        if s in _SLOT_KEY:
            return _SLOT_KEY[s]
    return None


def step_position(key: str, slots: Slots, plan: list[str] | None = None) -> tuple[int, int]:
    """(bước, tổng) của câu hỏi: câu cốt lõi -> vị trí cố định i/5; câu chi tiết -> vị trí trong `plan` (câu của lượt
    "Cung cấp thêm thông tin" — more_plan; sửa một dòng: [khóa]) — None: mọi câu chi tiết áp dụng; câu khác (sửa
    phong cách / ngân sách / mức đầu tư) hoặc ngoài `plan` -> 1/1."""
    if key in CORE_KEYS:
        return CORE_KEYS.index(key) + 1, CORE_TOTAL
    keys = plan if plan is not None else detail_keys(slots)
    if key in keys:
        return keys.index(key) + 1, len(keys)
    return 1, 1


def step_block(q: NextQuestion, slots: Slots, plan: list[str] | None = None) -> QuestionStepBlock:
    """Thẻ câu hỏi từng bước: bước/tổng theo `step_position` (5 câu cốt lõi luôn i/5)."""
    step, total = step_position(q.key, slots, plan)
    return QuestionStepBlock(question=q.title or q.text, hint=q.hint, options=list(q.options), chips=list(q.chips),
                             input_placeholder=q.placeholder, step=step, total=total, skippable=q.skippable,
                             skip_value=SKIP_VALUE if q.skippable else None,
                             back_value=BACK_VALUE if step > 1 else None, slots=list(q.slots))


def previous_key(slots: Slots, current: str | None, plan: list[str] | None = None) -> str | None:
    """Câu hỏi trước câu `current` trong cùng nhóm (5 câu cốt lõi, hoặc `plan` — câu của lượt hỏi thêm); None: đang
    ở câu đầu của nhóm. `current` None / ngoài các nhóm (đang ở bản tóm tắt) -> câu cốt lõi cuối cùng."""
    if current in DETAIL_KEYS:
        keys = plan if plan is not None else detail_keys(slots)
        if current not in keys:
            return None
    else:
        keys = list(CORE_KEYS)
    if current is None or current not in keys:
        return CORE_KEYS[-1]
    i = keys.index(current)
    return keys[i - 1] if i > 0 else None


def more_slots(slots: Slots) -> list[str]:
    """Slot chi tiết chưa có giá trị (đang dùng mặc định: chưa hỏi, hoặc khách đã trả lời "chưa biết") — nút "Cung
    cấp thêm thông tin" hỏi lần lượt các mục này."""
    out: list[str] = []
    for k in detail_keys(slots):
        out += [s for s in KEY_SLOTS[k] if not slot_value_known(slots, s)]
    return out


def reaskable_slots(slots: Slots) -> list[str]:
    """Slot chi tiết đã hỏi nhưng chưa có giá trị (khách trả lời "chưa biết") — "Cung cấp thêm thông tin" hỏi lại."""
    return [s for s in more_slots(slots) if s in slots.asked]


_NAV_CLEAN_RE = re.compile(r"[!.,;:?…]+")
_BACK_RE = re.compile(r"(?:quay lai|tro lai|lui lai)(?: (?:cau(?: hoi)?|buoc) truoc(?: do)?)?")
_MORE_RE = re.compile(r"(?:toi muon )?cung cap them(?: thong tin)?(?: de (?:bao gia|khai toan) chinh xac hon)?")


def nav_command(message: str) -> str | None:
    """Tin nhắn điều hướng của thẻ câu hỏi: "back" (Quay lại câu trước), "more" (Cung cấp thêm thông tin), None."""
    f = " ".join(_NAV_CLEAN_RE.sub(" ", fold(message)).split())
    f = re.sub(r"\s+(?:a|nhe|nha)$", "", f)
    if _BACK_RE.fullmatch(f):
        return "back"
    if _MORE_RE.fullmatch(f):
        return "more"
    return None


def building_area_replies(slots: Slots) -> list[QuickReply]:
    """"Xây kín đất (<đất> m²)" (value parse ra building_area_m2 = diện tích đất) + "Chưa biết"."""
    out: list[QuickReply] = []
    if slots.land_area_m2:
        land = vi_number(slots.land_area_m2)
        out.append(QuickReply(label=f"Xây kín đất ({land} m²)", value=f"Diện tích xây dựng mỗi tầng {land} m2"))
    return [*out, BUILDING_AREA_UNKNOWN]


def floor_area(slots: Slots) -> float | None:
    """Diện tích một sàn dùng làm mốc: diện tích xây dựng mỗi tầng, chưa có thì diện tích đất (như engine)."""
    return slots.building_area_m2 or slots.land_area_m2


def mezzanine_area_replies(slots: Slots) -> list[QuickReply]:
    """"Khoảng 1/2 sàn (<nửa> m²)", "Bằng cả sàn (<sàn> m²)" (value parse ra mezzanine_area_m2) + "Chưa biết"."""
    out: list[QuickReply] = []
    ground = floor_area(slots)
    if ground:
        half = vi_number(round(ground / 2, 1))
        full = vi_number(ground)
        out += [QuickReply(label=f"Khoảng 1/2 sàn ({half} m²)", value=f"Tầng lửng {half} m2"),
                QuickReply(label=f"Bằng cả sàn ({full} m²)", value=f"Tầng lửng {full} m2")]
    return [*out, MEZZANINE_AREA_UNKNOWN]


def detail_question(slots: Slots, missing: list[str] | None = None) -> NextQuestion | None:
    """Câu hỏi cho slot chi tiết chưa hỏi đầu tiên (diện tích xây dựng -> hầm + lửng -> mái -> độ sâu hầm -> diện
    tích lửng)."""
    for k in detail_keys(slots):
        if not key_done(slots, k):
            return question_for_key(k, slots)
    return None


BASEMENT_MEZZ_CLARIFY_TEXT = "Dạ, nhà có tầng hầm, tầng lửng hay cả hai ạ?"


def basement_mezz_clarify_question() -> NextQuestion:
    """Khách đáp "có" cho câu hỏi chung "có tầng hầm hoặc tầng lửng không" -> hỏi rõ có cái nào (không đoán)."""
    opts = [r for r in BASEMENT_MEZZ_REPLIES if r.label != "Không hầm, không lửng"]
    return _question("basement_mezz", ["basement", "has_mezzanine"], BASEMENT_MEZZ_CLARIFY_TEXT,
                     "Nhà có tầng hầm, tầng lửng hay cả hai?", opts)


def budget_question(slots: Slots) -> NextQuestion | None:
    """Hỏi ngân sách SAU khái toán (phần hỏi trước bản tóm tắt không hỏi ngân sách) — chỉ khi chưa có và CHƯA từng
    hỏi. Có rồi -> None."""
    if has_budget(slots) or "budget_vnd" in slots.asked:
        return None
    return _question("budget", ["budget_vnd"],
                     "Anh/chị dự kiến ngân sách khoảng bao nhiêu để em đề xuất nhà thầu phù hợp ạ?",
                     "Ngân sách dự kiến?", BUDGET_REPLIES, hint=BUDGET_HINT,
                     placeholder="Nhập ngân sách (vd 3,5 tỷ)", skippable=True)


# ---------------------------------------------------------------------------
# Sửa thông tin (C-F9) và không có nhà thầu (C-F11)
# ---------------------------------------------------------------------------
# value: tin nhắn "Tôi muốn sửa <mục>" -> detect_intent = edit, parse_message không trích slot nào (có test).
EDIT_BUILDING_TYPE = "Tôi muốn sửa loại công trình"
EDIT_PROVINCE = "Tôi muốn sửa khu vực"
EDIT_LAND = "Tôi muốn sửa diện tích đất"
EDIT_FLOORS = "Tôi muốn sửa số tầng"
EDIT_WORK_SCOPE = "Tôi muốn sửa phạm vi công việc"
EDIT_BUILDING_AREA = "Tôi muốn sửa diện tích xây dựng"
EDIT_BASEMENT_MEZZ = "Tôi muốn sửa thông tin hầm, lửng của nhà"
EDIT_MEZZANINE_AREA = "Tôi muốn sửa diện tích lửng của nhà"
EDIT_ROOF = "Tôi muốn sửa loại mái"
EDIT_DESIGN_STYLE = "Tôi muốn sửa phong cách thiết kế"
EDIT_BUDGET = "Tôi muốn sửa ngân sách"
EDIT_INVESTMENT = "Tôi muốn sửa mức đầu tư"
EDIT_FIELD_REPLIES = _qr(
    ("Loại công trình", EDIT_BUILDING_TYPE), ("Khu vực", EDIT_PROVINCE),
    ("Diện tích đất", EDIT_LAND), ("Số tầng", EDIT_FLOORS),
    ("Phạm vi", EDIT_WORK_SCOPE),
    ("Diện tích xây dựng", EDIT_BUILDING_AREA),
    ("Hầm, lửng", EDIT_BASEMENT_MEZZ),
    ("Diện tích lửng", EDIT_MEZZANINE_AREA), ("Mái", EDIT_ROOF),
    ("Phong cách", EDIT_DESIGN_STYLE), ("Ngân sách", EDIT_BUDGET), ("Mức đầu tư", EDIT_INVESTMENT),
    ("Giữ nguyên", "Thông tin đúng rồi"),
)
# Nút sửa các mục chi tiết — chỉ hiện khi câu hỏi chi tiết áp dụng (không áp dụng căn hộ / phạm vi nội thất).
_DETAIL_EDIT_LABELS = {"Diện tích xây dựng", "Hầm, lửng", "Diện tích lửng", "Mái"}


def edit_field_replies(slots: Slots | None = None) -> list[QuickReply]:
    """Nút "sửa mục nào". "Diện tích lửng" chỉ hiện khi nhà có lửng."""
    if slots is not None and not details_apply(slots):
        return [r for r in EDIT_FIELD_REPLIES if r.label not in _DETAIL_EDIT_LABELS]
    if slots is not None and slots.has_mezzanine is not True:
        return [r for r in EDIT_FIELD_REPLIES if r.label != "Diện tích lửng"]
    return list(EDIT_FIELD_REPLIES)


# (mẫu trên chuỗi đã gập dấu, khóa câu hỏi, câu hỏi văn xuôi) — thứ tự: mẫu cụ thể trước.
_EDIT_FIELDS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"phong cach|kieu (?:nha|thiet ke)"), "design_style", f"Dạ, {_lc(DESIGN_STYLE_TEXT)}"),
    (re.compile(r"muc dau tu|\bdau tu\b|phan khuc"), "investment", f"Dạ, {_lc(INVESTMENT_LEVEL_TEXT)}"),
    (re.compile(r"ngan sach"), "budget", f"Dạ, {_lc(BUDGET_TEXT)}"),
    (re.compile(r"dien tich (?:tang |gac )?lung|lung (?:rong|bao nhieu)"), "mezzanine_area",
     f"Dạ, {_lc(MEZZANINE_AREA_TEXT)}"),
    (re.compile(r"loai cong trinh|loai nha"), "building_type", "Dạ, anh/chị muốn đổi sang loại công trình nào ạ?"),
    (re.compile(r"khu vuc|dia diem|tinh thanh|thanh pho|quan huyen"), "province",
     "Dạ, anh/chị dự định xây dựng ở khu vực nào ạ? (tỉnh/thành phố, quận/huyện nếu có)"),
    (re.compile(r"dien tich (?:xay dung|san)|dtxd"), "building_area", f"Dạ, {_lc(BUILDING_AREA_TEXT)}"),
    (re.compile(r"dien tich|kich thuoc|\bdat\b"), "land",
     "Dạ, anh/chị cho em xin lại diện tích đất (ngang × dài hoặc tổng m²) nhé."),
    (re.compile(r"\bham\b|\blung\b"), "basement_mezz", f"Dạ, {_lc(BASEMENT_MEZZ_TEXT)}"),
    (re.compile(r"\bmai\b"), "roof", f"Dạ, {_lc(ROOF_TEXT)}"),
    (re.compile(r"so tang|\btang\b|\blau\b"), "floors", "Dạ, anh/chị muốn đổi thành mấy tầng ạ? (ví dụ 1 trệt 2 lầu)"),
    (re.compile(r"pham vi|hang muc"), "work_scope",
     "Dạ, anh/chị muốn khái toán cho phạm vi nào ạ: xây thô, hoàn thiện, trọn gói hay nội thất?"),
]


def edit_field_question(message: str, slots: Slots | None = None) -> NextQuestion | None:
    """Khách nói muốn sửa một mục nhưng chưa nêu giá trị mới ("Tôi muốn sửa số tầng") -> hỏi đúng mục đó (thẻ câu hỏi
    của mục). Không nêu mục nào -> None (orchestrator hỏi "sửa thông tin nào" kèm EDIT_FIELD_REPLIES)."""
    f = fold(message)
    slots = slots or Slots()
    for rx, key, text in _EDIT_FIELDS:
        if rx.search(f):
            q = question_for_key(key, slots)
            if key == "basement_mezz":  # sửa hầm/lửng: hỏi cả hai
                q = question_for_key(key, slots.model_copy(update={"basement": None, "has_mezzanine": None,
                                                                   "asked": []}))
            return q.model_copy(update={"text": text})
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


def building_exceeds_land(slots: Slots) -> bool:
    """Diện tích xây dựng mỗi tầng lớn hơn diện tích đất (không xét căn hộ) — cảnh báo trước khi xác nhận."""
    return (str(slots.building_type) != "can_ho" and slots.building_area_m2 is not None
            and slots.land_area_m2 is not None and slots.building_area_m2 > slots.land_area_m2 + 0.01)


DEFAULTS_PREFIX = "Tạm tính: "
DEFAULTS_SUFFIX = " — anh/chị bấm Sửa hoặc Cung cấp thêm thông tin để thay đổi."


def default_parts(slots: Slots) -> list[str]:
    """Mục chi tiết đang dùng mặc định của engine (khách chưa cung cấp / trả lời "chưa biết"), theo thứ tự: diện tích
    xây dựng, hầm (độ sâu), lửng (diện tích), mái. Căn hộ / phạm vi nội thất: không có."""
    if not details_apply(slots):
        return []
    out: list[str] = []
    ground = floor_area(slots)
    if slots.building_area_m2 is None:
        out.append(f"xây kín đất ({format_m2(slots.land_area_m2)}/tầng)" if slots.land_area_m2 else "xây kín đất")
    if slots.basement is None:
        out.append("không hầm")
    elif slots.basement and slots.basement_depth_m is None:
        out.append("hầm sâu 1,7–2 m")
    if slots.has_mezzanine is None:
        out.append("không lửng")
    elif slots.has_mezzanine and slots.mezzanine_area_m2 is None:
        out.append(f"lửng bằng diện tích sàn ({format_m2(ground)})" if ground else "lửng bằng diện tích sàn")
    if slots.roof_type is None:
        out.append("mái bê tông cốt thép")
    return out


def defaults_line(slots: Slots) -> str | None:
    """"Tạm tính: xây kín đất (100 m²/tầng), không hầm, không lửng, mái bê tông cốt thép — anh/chị bấm Sửa hoặc
    Cung cấp thêm thông tin để thay đổi." — None khi không mục nào dùng mặc định."""
    parts = default_parts(slots)
    return f"{DEFAULTS_PREFIX}{', '.join(parts)}{DEFAULTS_SUFFIX}" if parts else None


def default_assumptions(slots: Slots) -> list[str]:
    """Giả định sẽ áp dụng khi khái toán (mục 6.2) — luôn hiển thị cho khách, mỗi giả định một dòng ngắn: mặc định
    của các mục chi tiết chưa có thông tin (một dòng "Tạm tính: ..."), quy ước móng, ghi chú mức đầu tư."""
    out: list[str] = []
    if building_exceeds_land(slots):
        out.append(f"Diện tích xây dựng mỗi tầng ({format_m2(slots.building_area_m2)}) lớn hơn diện tích đất "
                   f"({format_m2(slots.land_area_m2)}), anh/chị kiểm tra lại giúp em nhé.")
    if line := defaults_line(slots):
        out.append(line)
    # Phạm vi hoàn thiện/nội thất và căn hộ không tính móng (C-F12) -> không nêu quy ước móng.
    no_foundation = str(slots.work_scope) in ("hoan_thien", "noi_that") or str(slots.building_type) == "can_ho"
    if slots.foundation_type is None and not no_foundation and slots.floors:
        if slots.floors >= 4:
            out.append("Móng tính theo thông lệ: móng cọc (nhà từ 4 tầng nổi trở lên), chưa gồm chi phí ép cọc.")
        else:
            out.append("Móng tính theo thông lệ: móng băng (nhà đến 3 tầng nổi).")
    if note := investment_note(slots.investment_level):
        out.append(note)
    return out


_ROOF_VI = {"roof_metal": "Mái tôn", "roof_concrete": "Mái bê tông cốt thép", "roof_tile_on_truss": "Mái ngói kèo thép",
            "roof_tile_on_concrete": "Mái ngói đổ BTCT", "none": "Không có mái"}
_ROOFTOP_VI = {"none": "Không có", "covered": "Có mái che", "open": "Không mái che"}
_STYLE_CODES = frozenset(DESIGN_STYLE_VI) - {"indochine"}  # tên riêng giữ chữ hoa
_LEVEL_CODES = frozenset(INVESTMENT_LEVEL_VI)
_FOUNDATION_VI = {"foundation_single": "Móng đơn", "foundation_strip": "Móng băng", "foundation_pile": "Móng cọc"}


TENTATIVE = "(tạm tính)"  # hậu tố giá trị mặc định trên bản xác nhận


def describe_basement(slots: Slots) -> str | None:
    """"Có (sâu 1,5 m)" | "Có" | "Không" | None (chưa có thông tin)."""
    if slots.basement is None:
        return None
    if not slots.basement:
        return "Không"
    return f"Có (sâu {vi_number(slots.basement_depth_m)} m)" if slots.basement_depth_m else "Có"


def describe_mezzanine(slots: Slots) -> str:
    """"Có (40 m²)" | "Có" | "Không"."""
    if not slots.has_mezzanine:
        return "Không"
    return f"Có ({format_m2(slots.mezzanine_area_m2)})" if slots.mezzanine_area_m2 else "Có"


def describe_budget(slots: Slots) -> str | None:
    """Ngân sách theo đúng lựa chọn của khách: nhãn khoảng ("2–4 tỷ", "Trên 10 tỷ") hoặc số tiền; None: chưa nêu."""
    if slots.budget_label:
        return slots.budget_label
    return format_vnd(slots.budget_vnd) if slots.budget_vnd else None


def describe_apartment_floors(slots: Slots) -> str:
    """Số tầng của căn hộ theo câu hỏi "Căn hộ có mấy tầng?": "1 tầng" | "2 tầng (duplex)" | "3 tầng"."""
    if not slots.floors:
        return NO_DATA
    return "2 tầng (duplex)" if slots.floors == 2 else f"{slots.floors} tầng"


def build_summary(slots: Slots) -> tuple[str, SummaryConfirmBlock]:
    """Tóm tắt để khách xác nhận trước khi khái toán (mục 5.3, ví dụ 5.4)."""
    btype = BUILDING_TYPE_VI.get(str(slots.building_type), NO_DATA) if slots.building_type else NO_DATA
    scope = WORK_SCOPE_VI.get(str(slots.work_scope), NO_DATA) if slots.work_scope else NO_DATA

    detail_edit = details_apply(slots)
    apartment = _is_apartment(slots)
    # Căn hộ: hỏi "Diện tích công trình (m²)?" và "Căn hộ có mấy tầng?" -> không dùng chữ "đất", "trệt/lầu".
    area_label, area_value = (("Diện tích căn hộ", format_m2(slots.land_area_m2) if slots.land_area_m2 else NO_DATA)
                              if apartment else ("Diện tích đất", describe_land(slots)))
    floors_value = describe_apartment_floors(slots) if apartment else describe_floors(slots)
    items = [
        SummaryItem(label="Loại công trình", value=btype, edit_value=EDIT_BUILDING_TYPE),
        SummaryItem(label="Khu vực", value=describe_location(slots), edit_value=EDIT_PROVINCE),
        SummaryItem(label=area_label, value=area_value, edit_value=EDIT_LAND),
        SummaryItem(label="Số tầng", value=floors_value, edit_value=EDIT_FLOORS),
        SummaryItem(label="Phạm vi", value=scope, edit_value=EDIT_WORK_SCOPE),
    ]
    # Mục chi tiết: công trình xây mới luôn có dòng (giá trị khách nêu, hoặc mặc định "(tạm tính)") kèm nút Sửa;
    # căn hộ / phạm vi nội thất chỉ hiện mục khách tự nêu (không sửa được — không áp dụng).
    if detail_edit or slots.building_area_m2:
        value = (format_m2(slots.building_area_m2) if slots.building_area_m2
                 else f"{format_m2(slots.land_area_m2)} {TENTATIVE}" if slots.land_area_m2 else f"Xây kín đất {TENTATIVE}")
        items.append(SummaryItem(label="Diện tích xây dựng mỗi tầng", value=value,
                                 edit_value=EDIT_BUILDING_AREA if detail_edit else None))
    basement = describe_basement(slots)
    if basement is not None or detail_edit:
        items.append(SummaryItem(label="Tầng hầm", value=basement or f"Không {TENTATIVE}",
                                 edit_value=EDIT_BASEMENT_MEZZ if detail_edit else None))
    if slots.has_mezzanine is not None or detail_edit:
        mezz_edit = EDIT_MEZZANINE_AREA if slots.has_mezzanine else EDIT_BASEMENT_MEZZ
        value = describe_mezzanine(slots) if slots.has_mezzanine is not None else f"Không {TENTATIVE}"
        items.append(SummaryItem(label="Tầng lửng", value=value, edit_value=mezz_edit if detail_edit else None))
    if slots.roof_type or detail_edit:
        value = _ROOF_VI[slots.roof_type] if slots.roof_type else f"Mái bê tông cốt thép {TENTATIVE}"
        items.append(SummaryItem(label="Mái", value=value, edit_value=EDIT_ROOF if detail_edit else None))
    if slots.rooftop_terrace:
        items.append(SummaryItem(label="Sân thượng", value=_ROOFTOP_VI[slots.rooftop_terrace]))
    if slots.foundation_type:
        items.append(SummaryItem(label="Móng", value=_FOUNDATION_VI[slots.foundation_type]))
    if style := describe_design_style(slots.design_style):
        items.append(SummaryItem(label="Phong cách", value=style, edit_value=EDIT_DESIGN_STYLE))
    budget = describe_budget(slots)
    if budget:
        items.append(SummaryItem(label="Ngân sách", value=budget, edit_value=EDIT_BUDGET))
    if level := describe_investment_level(slots.investment_level):
        items.append(SummaryItem(label="Mức đầu tư", value=level, edit_value=EDIT_INVESTMENT))
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
        phrase.append(f"diện tích {area_value}" if apartment else f"đất {area_value}")
    if slots.building_area_m2:
        phrase.append(f"xây dựng {format_m2(slots.building_area_m2)} mỗi tầng")
    if slots.floors:
        phrase.append(floors_value)
    if slots.has_mezzanine and slots.mezzanine_area_m2:
        phrase.append(f"lửng {format_m2(slots.mezzanine_area_m2)}")
    if slots.basement:
        phrase.append("có tầng hầm" + (f" sâu {vi_number(slots.basement_depth_m)} m" if slots.basement_depth_m else ""))
    if slots.roof_type:
        phrase.append(_lc(_ROOF_VI[slots.roof_type]))
    if slots.work_scope:
        phrase.append(scope.lower())
    if style:
        phrase.append(f"phong cách {_lc(style) if slots.design_style in _STYLE_CODES else style}")
    if budget:
        phrase.append(f"ngân sách {_lc(budget)}")
    if level:
        phrase.append(f"mức đầu tư {_lc(level) if slots.investment_level in _LEVEL_CODES else level}")
    text = f"Em tóm tắt lại: {', '.join(phrase)}. "
    if building_exceeds_land(slots):
        text += "Diện tích xây dựng mỗi tầng đang lớn hơn diện tích đất, anh/chị kiểm tra lại giúp em. "
    missing = missing_required(slots)
    if missing:
        text += "Em còn thiếu một vài thông tin, anh/chị bổ sung giúp em nhé."
    else:
        text += "Anh/chị xác nhận để em tính khái toán nhé?"

    more = bool(more_slots(slots))
    title = f"Xác nhận thông tin — {btype}" if slots.building_type else "Xác nhận thông tin"
    block = SummaryConfirmBlock(items=items, assumptions=default_assumptions(slots), title=title,
                                confirm_label=CONFIRM_LABEL, confirm_value=CONFIRM_VALUE,
                                more_label=MORE_LABEL if more else None, more_value=MORE_VALUE if more else None)
    return text, block
