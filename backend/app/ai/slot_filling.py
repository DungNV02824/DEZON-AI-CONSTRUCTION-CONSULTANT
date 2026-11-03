"""Slot filling (đặc tả mục 5.3, 9.1).

`extract(message, current, provider=None, expected_slots=None) -> SlotUpdate`

1. Luôn chạy bộ trích theo quy tắc (`app.ai.normalize`).
2. Nếu có provider thật (name != "mock"): gọi `provider.extract` với JSON schema chặt (sinh từ Pydantic) để lấy
   giá trị THÔ, rồi chuẩn hóa bằng code. Giá trị quy tắc được ưu tiên; LLM chỉ bổ sung trường quy tắc không tìm
   thấy. Lỗi provider -> bỏ qua, dùng kết quả quy tắc.
3. `expected_slots`: slot đang hỏi ở lượt trước — dùng để hiểu câu trả lời ngắn ("4", "Khác", "100").
4. Gộp vào slot hiện tại, giữ nhất quán (đổi tỉnh -> bỏ quận cũ khác tỉnh; đổi diện tích -> bỏ ngang/dài cũ).
5. Đổi slot sau khi đã xác nhận -> confirmed=False; ý định xác nhận khi đủ slot bắt buộc -> confirmed=True.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai.normalize import (
    NUM,
    bare_budget,
    detect_intent,
    fold,
    nfc,
    parse_budget_info,
    parse_building_type,
    parse_design_style,
    parse_floors,
    parse_investment_level,
    parse_location,
    parse_message,
    parse_mezzanine_area,
    parse_start_time,
    parse_vn_number,
    province_of_district,
)
from app.ai.providers.base import LLMProvider
from app.schemas.common import BuildingType, WorkScope
from app.schemas.slots import (
    DESIGN_STYLE_VI,
    DETAIL_SLOTS,
    INVESTMENT_LEVEL_VI,
    OPTIONAL_SLOTS,
    Slots,
    missing_required,
    slot_value_known,
)

log = logging.getLogger(__name__)

SlotIntent = Literal["provide_info", "confirm", "edit", "question", "out_of_scope", "other"]

# Trường không phải thông tin khách cung cấp — không tính là "changed".
_META_FIELDS = {"confirmed", "asked"}


class SlotUpdate(BaseModel):
    slots: Slots  # slot mới đã gộp
    changed: list[str] = Field(default_factory=list)  # tên slot đổi giá trị so với `current`
    intent: SlotIntent = "other"
    missing_required: list[str] = Field(default_factory=list)
    llm_used: bool = False  # đã gọi LLM extract thành công


# ---------------------------------------------------------------------------
# Structured output cho LLM (giá trị thô — code chuẩn hóa lại)
# ---------------------------------------------------------------------------


class LLMSlotExtraction(BaseModel):
    """Schema bắt buộc cho `provider.extract`. Mọi trường bắt buộc có mặt, không biết -> null."""
    model_config = ConfigDict(extra="forbid")

    building_type: Literal["nha_pho", "biet_thu", "can_ho", "van_phong", "khach_san", "khac"] | None = Field(
        description="Loại công trình khách nói")
    province: str | None = Field(description="Tỉnh/thành phố đúng như khách viết")
    district: str | None = Field(description="Quận/huyện/thành phố trực thuộc đúng như khách viết")
    land_width_m: float | None = Field(description="Chiều ngang đất (m)")
    land_length_m: float | None = Field(description="Chiều dài đất (m)")
    land_area_m2: float | None = Field(description="Diện tích đất (m²) khi khách nói trực tiếp")
    building_area_m2: float | None = Field(description="Diện tích xây dựng mỗi tầng (m²) nếu khách nói")
    floors_text: str | None = Field(description="Nguyên văn mô tả số tầng, vd '1 trệt 3 lầu'")
    floors: int | None = Field(description="Số tầng nổi tính cả trệt, chỉ khi khách nói rõ")
    has_mezzanine: bool | None
    mezzanine_area_m2: float | None = Field(description="Diện tích tầng lửng (m²), chỉ khi khách nói rõ")
    basement: bool | None
    basement_depth_m: float | None = Field(description="Độ sâu tầng hầm (m) nếu khách nói")
    roof_type: Literal["roof_metal", "roof_concrete", "roof_tile_on_truss", "roof_tile_on_concrete"] | None
    rooftop_terrace: Literal["none", "covered", "open"] | None
    foundation_type: Literal["foundation_single", "foundation_strip", "foundation_pile"] | None
    work_scope: Literal["xay_tho", "hoan_thien", "tron_goi", "noi_that"] | None
    budget_text: str | None = Field(description="Nguyên văn số tiền ngân sách, kể cả chữ 'trên'/'dưới', vd '2 tỷ', "
                                                "'1,5–2,5 tỷ', 'trên 10 tỷ'")
    start_time: str | None = Field(description="Nguyên văn thời gian dự kiến khởi công")
    design_style: str | None = Field(description="Phong cách thiết kế khách muốn, nguyên văn (vd 'hiện đại')")
    investment_level: Literal["tiet_kiem", "pho_thong", "trung_cap", "sieu_cao_cap"] | None = Field(
        description="Mức đầu tư khách nói rõ (tiết kiệm/phổ thông/trung cấp/siêu cao cấp)")
    intent: Literal["provide_info", "confirm", "edit", "question", "out_of_scope", "other"]


EXTRACTION_JSON_SCHEMA: dict[str, Any] = LLMSlotExtraction.model_json_schema()

EXTRACTION_SYSTEM_PROMPT = (
    "Bạn là bộ trích thông tin cho trợ lý tư vấn xây dựng Dezon. Đọc MỘT tin nhắn của khách và điền JSON "
    "theo schema. Chỉ điền thông tin khách NÓI RÕ trong tin nhắn; không suy đoán, không tự tính, không lấy từ "
    "kiến thức bên ngoài. Không có thì để null. floors_text, budget_text, province, district, start_time ghi "
    "NGUYÊN VĂN như khách viết. intent: provide_info (cung cấp thông tin), confirm (đồng ý/xác nhận), "
    "edit (muốn sửa), question (hỏi), out_of_scope (ngoài chủ đề xây dựng), other."
)


# ---------------------------------------------------------------------------
# Chuẩn hóa giá trị thô (từ LLM hoặc tool update_project_requirements)
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(NUM)

# Số viết bằng chữ — chỉ dùng để KIỂM TRA giá trị LLM có xuất hiện trong tin nhắn hay không. So khớp trên chuỗi
# CÓ DẤU (chữ thường) để không nhầm "tầm"/"sau"/"tư" (gập ra "tam"/"sau"/"tu") thành số.
_DIGIT_WORDS = {"một": 1, "mốt": 1, "hai": 2, "ba": 3, "bốn": 4, "tư": 4, "năm": 5, "lăm": 5, "nhăm": 5,
                "sáu": 6, "bảy": 7, "bẩy": 7, "tám": 8, "chín": 9}
_D = "một|hai|ba|bốn|tư|năm|sáu|bảy|bẩy|tám|chín"
_WORD_NUM_RE = re.compile(
    rf"\b(?:({_D})\s+)?(mười|mươi|chục)(?:\s+({_D}|mốt|lăm|nhăm))?\b|\b({_D})\b"
)


def _word_numbers(text: str) -> set[float]:
    """"hai mươi" -> 20, "mười lăm" -> 15, "hai" -> 2."""
    out: set[float] = set()
    for m in _WORD_NUM_RE.finditer(nfc(text).lower()):
        if m.group(4):
            out.add(float(_DIGIT_WORDS[m.group(4)]))
            continue
        tens = _DIGIT_WORDS[m.group(1)] if m.group(1) else 1
        units = _DIGIT_WORDS[m.group(3)] if m.group(3) else 0
        out.add(float(tens * 10 + units))
        if m.group(1):
            out.add(float(_DIGIT_WORDS[m.group(1)]))
    return out


def _numbers_in(text: str) -> set[float]:
    """Mọi số xuất hiện trong văn bản (chữ số và số viết bằng chữ)."""
    f = fold(text)
    out: set[float] = set()
    for m in _NUM_RE.finditer(f):
        v = parse_vn_number(m.group(0))
        if v is not None:
            out.add(round(v, 2))
    out |= _word_numbers(text)
    return out


def _numbers_subset(value_text: str, nums: set[float] | None) -> bool:
    """Mọi chữ số trong `value_text` (vd budget_text/floors_text của LLM) đều có trong tin nhắn."""
    if nums is None:
        return True
    found = [parse_vn_number(m.group(0)) for m in _NUM_RE.finditer(fold(value_text))]
    return all(v is not None and round(v, 2) in nums for v in found)


_LOC_PREFIX_RE = re.compile(r"^(?:tp|thanh pho|tinh|quan|huyen|thi xa|q)\s+")


def _loc_in_message(value: str, message: str | None) -> bool:
    """Địa danh LLM trả về phải xuất hiện trong tin nhắn (bỏ tiền tố TP./Tỉnh/Quận..., không phân biệt dấu)."""
    if message is None:
        return True

    def norm(s: str) -> str:
        return " ".join(re.sub(r"[._/,;:()\-]", " ", fold(s)).split())

    v = _LOC_PREFIX_RE.sub("", norm(value))
    return bool(v) and f" {v} " in f" {norm(message)} "


# Với tin nhắn (đường LLM): trường cấu trúc chỉ nhận khi tin nhắn có từ khóa liên quan (chống LLM tự thêm).
_STRUCT_KEYWORDS: dict[str, re.Pattern[str]] = {
    "has_mezzanine": re.compile(r"\b(?:lung|gac)\b"),
    "basement": re.compile(r"\bham\b"),
    "roof_type": re.compile(r"\b(?:mai|ngoi|ton|be tong|btct)\b"),
    "rooftop_terrace": re.compile(r"\b(?:san thuong|tum)\b"),
    "foundation_type": re.compile(r"\b(?:mong|coc)\b"),
}


def _as_float(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int | float):
        return float(v)
    if isinstance(v, str):
        return parse_vn_number(v.strip())
    return None


def _in_message(value: str, message: str | None) -> bool:
    return message is None or fold(value).strip() in fold(message)


def normalize_raw_slots(raw: dict[str, Any], message: str | None = None) -> dict[str, Any]:
    """Chuẩn hóa dict giá trị thô (đầu ra LLM / tham số tool) thành giá trị slot hợp lệ.

    Nếu có `message`: chỉ nhận số/chuỗi tự do thực sự xuất hiện trong tin nhắn (chống LLM bịa).
    Bỏ qua mọi giá trị không chuẩn hóa được.
    """
    out: dict[str, Any] = {}
    nums = _numbers_in(message) if message is not None else None
    fmsg = fold(message) if message is not None else None

    def num_ok(v: float) -> bool:
        return nums is None or round(v, 2) in nums

    bt = raw.get("building_type")
    if isinstance(bt, str) and bt:
        if bt in BuildingType.__members__:
            out["building_type"] = BuildingType(bt)
        elif parsed := parse_building_type(bt):
            out["building_type"] = parsed

    for key in ("district", "province"):
        val = raw.get(key)
        if not isinstance(val, str) or not val.strip():
            continue
        if not _loc_in_message(val, message):
            continue  # LLM đưa địa danh không có trong tin nhắn -> bỏ
        loc = parse_location(val)
        if key == "district" and "district" in loc:
            out["district"] = loc["district"]
            out.setdefault("province", loc["province"])
        elif key == "province" and "province" in loc:
            out["province"] = loc["province"]
        elif _in_message(val, message) and len(val.strip()) <= 60:
            out[key] = nfc(val.strip())

    w, length = _as_float(raw.get("land_width_m")), _as_float(raw.get("land_length_m"))
    if w and length and 1 <= w <= 300 and 1 <= length <= 1000 and num_ok(w) and num_ok(length):
        out["land_width_m"], out["land_length_m"] = w, length
        out["land_area_m2"] = round(w * length, 2)
    else:
        area = _as_float(raw.get("land_area_m2"))
        if area and 5 <= area <= 1_000_000 and num_ok(area):
            out["land_area_m2"] = area
    barea = _as_float(raw.get("building_area_m2"))
    if barea and 5 <= barea <= 1_000_000 and num_ok(barea):
        out["building_area_m2"] = barea

    ftext = raw.get("floors_text")
    if isinstance(ftext, str) and ftext.strip() and _numbers_subset(ftext, nums):
        out.update(parse_floors(ftext))
    if "floors" not in out:
        fl = raw.get("floors")
        if isinstance(fl, int | float) and not isinstance(fl, bool) and float(fl).is_integer() and 1 <= fl <= 50:
            if nums is None or float(fl) in nums or float(fl) - 1 in nums:
                out["floors"] = int(fl)

    def struct_ok(key: str) -> bool:
        return fmsg is None or bool(_STRUCT_KEYWORDS[key].search(fmsg))

    for key in ("has_mezzanine", "basement"):
        if isinstance(raw.get(key), bool) and key not in out and struct_ok(key):
            out[key] = raw[key]
    marea = _as_float(raw.get("mezzanine_area_m2"))
    if (marea and 3 <= marea <= 1_000_000 and num_ok(marea) and struct_ok("has_mezzanine")
            and out.get("has_mezzanine") is not False):
        out["mezzanine_area_m2"] = marea
    depth = _as_float(raw.get("basement_depth_m"))
    if depth and 0.5 <= depth <= 20 and num_ok(depth) and struct_ok("basement") and out.get("basement") is not False:
        out["basement_depth_m"] = depth
    enums: dict[str, set[str]] = {
        "roof_type": {"roof_metal", "roof_concrete", "roof_tile_on_truss", "roof_tile_on_concrete", "none"},
        "rooftop_terrace": {"none", "covered", "open"},
        "foundation_type": {"foundation_single", "foundation_strip", "foundation_pile"},
    }
    for key, allowed in enums.items():
        if raw.get(key) in allowed and struct_ok(key):
            out[key] = raw[key]
    if raw.get("work_scope") in WorkScope.__members__:
        out["work_scope"] = WorkScope(raw["work_scope"])

    btext = raw.get("budget_text")
    if isinstance(btext, str) and btext.strip() and _numbers_subset(btext, nums):
        info = parse_budget_info(btext)
        if info is not None:
            out["budget_vnd"] = info[0]
            if info[1]:
                out["budget_label"] = info[1]
    if "budget_vnd" not in out and message is None:  # tham số tool: cho phép số nguyên VND
        bv = raw.get("budget_vnd")
        if isinstance(bv, int | float) and not isinstance(bv, bool):
            b = bare_budget(float(bv))
            if b:
                out["budget_vnd"] = b

    st = raw.get("start_time")
    if isinstance(st, str) and st.strip():
        parsed_st = parse_start_time(st)
        if parsed_st:
            out["start_time"] = parsed_st
        elif _in_message(st, message) and len(st.strip()) <= 80:
            out["start_time"] = nfc(st.strip())

    # Phong cách / mức đầu tư (chỉ ghi nhận): mã chuẩn, hoặc chữ khách nói (phải có trong tin nhắn).
    style = raw.get("design_style")
    if isinstance(style, str) and style.strip():
        code = style.strip() if style.strip() in DESIGN_STYLE_VI else parse_design_style(style, bare=True)
        if code and (message is None or _in_message(DESIGN_STYLE_VI[code], message) or style_in(message, code)):
            out["design_style"] = code
        elif not code and _in_message(style, message) and len(style.strip()) <= MAX_FREE_TEXT:
            out["design_style"] = _free_text(style)
    level = raw.get("investment_level")
    if isinstance(level, str) and level.strip():
        code = level.strip() if level.strip() in INVESTMENT_LEVEL_VI else parse_investment_level(level, bare=True)
        if code and (message is None or parse_investment_level(message, bare=True) == code
                     or parse_investment_level(message) == code):
            out["investment_level"] = code
        elif not code and message is None and len(level.strip()) <= MAX_FREE_TEXT:
            out["investment_level"] = _free_text(level)
    return out


MAX_FREE_TEXT = 60


def _free_text(text: str) -> str:
    t = nfc(text).strip(" .!,;:")
    return t[:1].upper() + t[1:]


def style_in(message: str | None, code: str) -> bool:
    """Tin nhắn nói tới phong cách `code` (có hoặc không có chữ "phong cách")."""
    if message is None:
        return True
    return parse_design_style(message) == code or bool(
        re.search(rf"\b{re.escape(fold(DESIGN_STYLE_VI[code]))}\b", fold(message)))


# ---------------------------------------------------------------------------
# Câu trả lời ngắn theo slot đang hỏi
# ---------------------------------------------------------------------------

_OTHER_WORDS = {"khac", "loai khac", "cong trinh khac", "khac a", "nhap khac", "nhap thong tin khac"}
_BARE_NO_ANSWER_RE = re.compile(r"(?:da\s+|vang\s+)?(?:khong|ko|k)(?:\s+co)?(?:\s+(?:a|nhe|dau))?")
_BARE_YES_ANSWER_RE = re.compile(r"(?:da\s+|vang\s+)?co(?:\s+a)?")
# "xây kín đất" / "kín đất" khi đang hỏi diện tích xây dựng -> bằng diện tích đất.
_FULL_LOT_RE = re.compile(r"\bkin\s+(?:het\s+)?(?:dat|lo|mat bang)\b|\bxay\s+kin\b|\bbang\s+(?:dien tich\s+)?dat\b")
_LAND_WORD_RE = re.compile(r"\bdat\b|\blo\b|\bthua\b|\bkhu dat\b")
_UNKNOWN_RE = re.compile(r"\b(?:chua xac dinh|chua biet|chua ro|khong biet|khong ro|chua co)\b")
# Câu trả lời "bỏ qua" cho câu hỏi chi tiết (không nêu giá trị nhưng rõ là không có ý kiến) -> dùng mặc định, nêu rõ
# trong tóm tắt. Câu trả lời không hiểu được KHÔNG thuộc nhóm này -> hỏi lại (không âm thầm dùng mặc định).
_SKIP_ANSWER_RE = re.compile(
    r"(?:da\s+|vang\s+)?(?:khong|ko|k|chua)(?:\s+co)?(?:\s+(?:a|nhe|dau))?"
    r"|(?:bo qua(?: cau(?: hoi)? nay)?|tuy (?:em|ban|ben em)|sao cung duoc|gi cung duoc|nao cung duoc|mac dinh|khong quan trong"
    r"|khong can|de em chon|em chon giup)(?:\s+(?:a|nhe|di))?"
)
# Mái (câu trả lời ngắn khi đang hỏi loại mái): "tôn", "ngói", "bê tông", "BTCT", "mái thái", "mái nhật".
_ROOF_PREFIX = r"(?:(?:lop|lam|la)\s+)?(?:mai\s+)?"
_ROOF_SHORT: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"{_ROOF_PREFIX}ton(?:\s+(?:lanh|kem|mau|chong nong|cach nhiet))?"), "roof_metal"),
    (re.compile(rf"{_ROOF_PREFIX}ngoi\s+(?:tren\s+)?(?:keo|vi keo|xa go)\s+(?:thep|sat)(?:\s+ma kem)?"
                r"|(?:(?:lop|lam|la)\s+)?mai\s+(?:thai|nhat)"), "roof_tile_on_truss"),
    (re.compile(rf"{_ROOF_PREFIX}ngoi(?:\s+(?:do|tren|dan tren)\s+(?:san\s+)?(?:be tong|btct))?"),
     "roof_tile_on_concrete"),
    (re.compile(rf"{_ROOF_PREFIX}(?:bang|do|bang do)?\s*(?:be tong(?:\s+cot thep)?|btct)|{_ROOF_PREFIX}bang"),
     "roof_concrete"),
]
# Từ (có dấu, chữ thường) cho thấy câu trả lời KHÔNG phải tên địa danh ("tôi chưa chọn được", "để tôi hỏi vợ đã").
_NOT_PLACE_WORDS = {
    "tôi", "mình", "anh", "chị", "em", "tui", "tao", "không", "chưa", "để", "đã", "sẽ", "biết", "rõ", "nữa", "hỏi",
    "vợ", "chồng", "gì", "sao", "muốn", "xây", "nhà", "được", "chọn", "nghĩ", "cần", "đâu", "thì", "là", "ạ", "nhé",
    "vậy", "nào", "đi", "chút", "xíu", "ok", "ừ", "vâng", "dạ", "cho", "với", "giúp", "xem",
}


def _short_text(message: str) -> str:
    """Tin nhắn đã gập dấu, bỏ dấu câu (GIỮ dấu thập phân/hàng nghìn giữa hai chữ số: "1,5" không thành "1 5") và
    bỏ từ đệm cuối câu ("ạ", "nhé", "thôi")."""
    f = re.sub(r"[!;:?]+|[.,](?!\d)|(?<!\d)[.,]", " ", fold(message)).strip()
    f = re.sub(r"\s+", " ", f)
    return re.sub(r"\s*\b(?:a|nhe|nha|thoi)$", "", f).strip()


_CONFIRM_BUTTON = "dung roi tinh khai toan giup toi"  # _short_text của dialog.CONFIRM_VALUE


# Tin nhắn của nút điều hướng thẻ câu hỏi ("Quay lại câu trước", "Tôi muốn cung cấp thêm thông tin") — không phải
# câu trả lời (vd không phải tên tỉnh).
_NAV_PHRASE_RE = re.compile(r"(?:quay lai|tro lai|lui lai)(?: (?:cau(?: hoi)?|buoc) truoc(?: do)?)?"
                            r"|(?:toi muon )?cung cap them(?: thong tin)?(?: de (?:bao gia|khai toan) chinh xac hon)?")


def is_skip_answer(message: str) -> bool:
    """"Bỏ qua câu này" (nút Bỏ qua), "bỏ qua", "tùy em"... hoặc tin nhắn nút điều hướng — khách không nêu giá trị."""
    f = _short_text(message)
    return bool(_SKIP_ANSWER_RE.fullmatch(f) or _NAV_PHRASE_RE.fullmatch(f))


def is_bare_yes(message: str) -> bool:
    """"có", "có ạ", "dạ có" — câu trả lời có/không chưa nói rõ có cái gì."""
    return bool(_BARE_YES_ANSWER_RE.fullmatch(_short_text(message)))


def _short_roof(f: str) -> str | None:
    for rx, value in _ROOF_SHORT:
        if rx.fullmatch(f):
            return value
    return None


def _short_answer(message: str, expected: list[str], found: dict[str, Any]) -> dict[str, Any]:
    """Hiểu câu trả lời ngắn theo slot đang hỏi. Chỉ trả slot mà quy tắc chung chưa tìm thấy."""
    out: dict[str, Any] = {}
    exp = set(expected)
    if "land_width_m" in exp or "land_length_m" in exp:
        exp.add("land_area_m2")
    f = _short_text(message)

    if "roof_type" in exp and "roof_type" not in found and (roof := _short_roof(f)):
        out["roof_type"] = roof
    if "building_type" in exp and "building_type" not in found and f in _OTHER_WORDS:
        out["building_type"] = BuildingType.khac
    if "work_scope" in exp and "work_scope" not in found and re.fullmatch(r"(?:phan\s+)?tho", f):
        out["work_scope"] = WorkScope.xay_tho

    m = re.fullmatch(rf"(?:khoang|tam|chung|du kien|la)?\s*({NUM})", f)
    if m:
        v = parse_vn_number(m.group(1))
        if v is not None:
            land_exp = "land_area_m2" in exp and "land_area_m2" not in found
            if ("floors" in exp and "floors" not in found and v.is_integer() and 1 <= v <= 50
                    and (not land_exp or v <= 10)):
                out["floors"] = int(v)
            elif land_exp and 10 <= v <= 1_000_000:
                out["land_area_m2"] = int(v) if v.is_integer() else v
            elif "building_area_m2" in exp and "building_area_m2" not in found and 5 <= v <= 1_000_000:
                out["building_area_m2"] = int(v) if v.is_integer() else v
            elif "mezzanine_area_m2" in exp and "mezzanine_area_m2" not in found and 3 <= v <= 1_000_000:
                out["mezzanine_area_m2"] = int(v) if v.is_integer() else v
            elif "basement_depth_m" in exp and "basement_depth_m" not in found and 0.5 <= v <= 20:
                out["basement_depth_m"] = int(v) if v.is_integer() else v
            elif "budget_vnd" in exp and "budget_vnd" not in found:
                b = bare_budget(v)
                if b:
                    out["budget_vnd"] = b
    md = re.fullmatch(rf"(?:sau\s+)?(?:khoang|tam|chung)?\s*({NUM})\s*(?:m|met)", f)
    if md and "basement_depth_m" in exp and "basement_depth_m" not in found:
        v = parse_vn_number(md.group(1))
        if v is not None and 0.5 <= v <= 20:
            out["basement_depth_m"] = int(v) if v.is_integer() else v
    # "không"/"không có" trả lời câu hỏi hầm/lửng -> không có; "có" chỉ rõ nghĩa khi đang hỏi đúng một mục.
    yes_no = [k for k in ("basement", "has_mezzanine") if k in exp and k not in found]
    if yes_no and _BARE_NO_ANSWER_RE.fullmatch(f):
        out.update({k: False for k in yes_no})
    elif len(yes_no) == 1 and _BARE_YES_ANSWER_RE.fullmatch(f):
        out[yes_no[0]] = True
    m2 = re.fullmatch(rf"({NUM})\s+({NUM})\s*m?", f)
    if m2 and "land_area_m2" in exp and "land_area_m2" not in found:
        w, length = parse_vn_number(m2.group(1)), parse_vn_number(m2.group(2))
        if w and length and 1 <= w <= 300 and 1 <= length <= 1000:
            out.update(land_width_m=w, land_length_m=length, land_area_m2=round(w * length, 2))

    no_signal = not found and not out and detect_intent(message) is None
    if "province" in exp and "province" not in found and no_signal and f and f not in _OTHER_WORDS:
        words = f.split()
        orig_words = set(re.findall(r"\w+", nfc(message).lower()))
        if (len(words) <= 5 and not re.search(r"\d", f) and not _UNKNOWN_RE.search(f)
                and not is_skip_answer(message) and not orig_words & _NOT_PLACE_WORDS):
            text = nfc(message).strip(" .!,")
            out["province"] = text.title() if text == text.lower() else text
    # Câu hỏi tùy chọn: câu trả lời trần ("hiện đại", "phổ thông") hoặc chữ khách tự nhập (ô nhập của thẻ câu hỏi).
    free_ok = (no_signal and f and f not in _OTHER_WORDS and len(nfc(message).strip()) <= MAX_FREE_TEXT
               and not _UNKNOWN_RE.search(f) and not _SKIP_ANSWER_RE.fullmatch(f))
    if "design_style" in exp and "design_style" not in found:
        if code := parse_design_style(message, bare=True):
            out["design_style"] = code
        elif free_ok:
            out["design_style"] = _free_text(message)
    if "investment_level" in exp and "investment_level" not in found:
        if code := parse_investment_level(message, bare=True):
            out["investment_level"] = code
        elif free_ok:
            out["investment_level"] = _free_text(message)
    if "start_time" in exp and "start_time" not in found and not out and f and f not in _OTHER_WORDS:
        if _UNKNOWN_RE.search(f):
            out["start_time"] = "Chưa xác định"
        elif (no_signal and len(f) <= 60
              and not set(re.findall(r"\w+", nfc(message).lower())) & (_NOT_PLACE_WORDS - {"đi"})):
            out["start_time"] = nfc(message).strip(" .!,")
    return out


_LAND_KEYS = frozenset({"land_area_m2", "land_width_m", "land_length_m"})
_AREA_NUM_RE = re.compile(rf"(?<![\d.,])({NUM})(?!\d|[.,]\d)\s*(?:m2|m 2|met vuong|m vuong)(?![a-z0-9])")
# Số m² đứng ngay sau "xây" ("đất 100m2, xây 80m2") — "xây dựng"/"sàn" đã do normalize.parse_dimensions xử lý.
_BUILD_TAG_RE = re.compile(r"\bxay\s+(?:khoang\s+|tam\s+|chung\s+|la\s+)?$")


def _tagged_building_area(f: str, found: dict[str, Any], expected: list[str]) -> bool:
    """"đất 100m2, xây 80m2" / "xây 80m2 trên đất 100m2" -> building_area_m2=80, land_area_m2=100. Sửa `found`.

    Chỉ áp dụng khi rõ ngữ cảnh có hai diện tích (có từ chỉ đất, có ngang × dài) hoặc đang hỏi diện tích xây dựng —
    "muốn xây 100m2" đứng một mình vẫn là diện tích đất."""
    if not (_LAND_WORD_RE.search(f) or "land_width_m" in found or "building_area_m2" in expected):
        return False
    areas = []
    for m in _AREA_NUM_RE.finditer(f):
        v = parse_vn_number(m.group(1))
        if v is not None and 5 <= v <= 1_000_000:
            areas.append((v, bool(_BUILD_TAG_RE.search(f[max(0, m.start() - 20):m.start()]))))
    tagged = [v for v, is_b in areas if is_b]
    if len(tagged) != 1:
        return False
    building = tagged[0]
    others = [v for v, is_b in areas if not is_b]
    found["building_area_m2"] = int(building) if building.is_integer() else building
    if "land_width_m" not in found:
        if len(others) == 1:
            found["land_area_m2"] = int(others[0]) if others[0].is_integer() else others[0]
        elif not others and found.get("land_area_m2") == building:
            found.pop("land_area_m2")  # số duy nhất là diện tích xây -> không phải diện tích đất
    return True


def _building_area_answer(message: str, expected: list[str], found: dict[str, Any], current: Slots) -> bool:
    """Diện tích xây dựng mỗi tầng. Sửa `found` tại chỗ.

    - "xây kín đất" (bất kỳ lúc nào, đã biết diện tích đất) -> bằng diện tích đất (mới nói trong tin hoặc đã có).
    - "đất 100m2, xây 80m2" -> diện tích xây dựng là số đi sau "xây".
    - Đang hỏi diện tích xây dựng và đã có diện tích đất: "80 m2" là diện tích xây dựng, không phải đổi diện tích đất
      (trừ khi khách nói rõ "đất ..." hoặc nêu ngang × dài).

    Trả True khi tin nhắn là câu trả lời diện tích xây dựng KHÔNG nói gì về đất -> không nhận diện tích đất từ LLM."""
    f = fold(message)
    _, mezz_span = parse_mezzanine_area(message)
    if mezz_span is not None:  # "lửng 40m2" không phải diện tích đất/xây dựng
        f = f[:mezz_span[0]] + " " * (mezz_span[1] - mezz_span[0]) + f[mezz_span[1]:]
    land_said = bool(_LAND_WORD_RE.search(f)) or "land_width_m" in found
    answering = "building_area_m2" in expected and current.land_area_m2 is not None and not land_said
    if "building_area_m2" in found:
        return answering
    land = found.get("land_area_m2", current.land_area_m2)
    if land is not None and _FULL_LOT_RE.search(f):
        found["building_area_m2"] = land
        return False
    if _tagged_building_area(f, found, expected):
        return answering
    if answering and "land_area_m2" in found:
        found["building_area_m2"] = found.pop("land_area_m2")
    return answering


_MEZZ_HALF_RE = re.compile(r"\b(?:1/2|mot nua|nua|50 ?%)\s+(?:dien tich\s+)?san\b")
_MEZZ_FULL_RE = re.compile(r"\b(?:bang|kin|full|het)\s+(?:ca\s+|het\s+|dien tich\s+)?san\b|\bca\s+san\b")


def _mezzanine_area_answer(message: str, expected: list[str], found: dict[str, Any], current: Slots) -> bool:
    """Câu trả lời cho câu hỏi diện tích tầng lửng. Sửa `found` tại chỗ.

    - "khoảng 1/2 sàn" / "bằng cả sàn" -> tính theo diện tích một sàn (xây dựng, chưa có thì đất).
    - "40 m2" (không nói gì về đất) là diện tích lửng, không phải đổi diện tích đất/xây dựng.

    Trả True khi đang trả lời câu hỏi diện tích lửng và tin nhắn không nói gì về đất -> không nhận diện tích đất/xây
    dựng từ LLM."""
    if "mezzanine_area_m2" not in expected:
        return False
    f = fold(message)
    answering = not (_LAND_WORD_RE.search(f) or "land_width_m" in found)
    if "mezzanine_area_m2" in found or not answering:
        return answering
    ground = found.get("building_area_m2") or current.building_area_m2 or current.land_area_m2
    if ground and _MEZZ_HALF_RE.search(f):
        found["mezzanine_area_m2"] = _clean_area(round(ground / 2, 1))
    elif ground and _MEZZ_FULL_RE.search(f):
        found["mezzanine_area_m2"] = _clean_area(ground)
    elif "land_area_m2" in found:
        found["mezzanine_area_m2"] = found.pop("land_area_m2")
    elif "building_area_m2" in found and current.building_area_m2 is not None:
        found["mezzanine_area_m2"] = found.pop("building_area_m2")
    return True


def _clean_area(v: float) -> float | int:
    return int(v) if float(v).is_integer() else v


# ---------------------------------------------------------------------------
# Gộp
# ---------------------------------------------------------------------------


def _llm_extract(provider: LLMProvider, message: str, expected: list[str] | None) -> tuple[dict, str | None]:
    system = EXTRACTION_SYSTEM_PROMPT
    if expected:
        system += f" Câu hỏi trước của trợ lý đang hỏi về: {', '.join(expected)}."
    raw = provider.extract(system, message, EXTRACTION_JSON_SCHEMA)
    if not isinstance(raw, dict) or not raw:
        return {}, None
    intent = raw.get("intent")
    found = normalize_raw_slots(raw, message=message)
    # "khac" là lựa chọn mặc định dễ bị LLM điền bừa: chỉ nhận khi đang hỏi loại công trình hoặc khách nói "khác".
    if found.get("building_type") == BuildingType.khac and "building_type" not in (expected or []) \
            and not re.search(r"\bkhac\b", fold(message)):
        found.pop("building_type")
    return found, intent if isinstance(intent, str) else None


def _merge(current: Slots, found: dict[str, Any]) -> tuple[Slots, list[str]]:
    data = current.model_dump()
    changed: list[str] = []

    def setv(k: str, v: Any) -> None:
        if data.get(k) != v:
            data[k] = v
            if k not in changed:
                changed.append(k)

    for k, v in found.items():
        if k in Slots.model_fields and k not in _META_FIELDS:
            setv(k, v)

    # Nhất quán vị trí: đổi tỉnh mà không nói quận -> bỏ quận cũ nếu quận thuộc tỉnh khác/không rõ.
    if "province" in changed and "district" not in found and data.get("district"):
        if province_of_district(data["district"]) != data["province"]:
            setv("district", None)
    # Ngân sách mới không kèm nhãn khoảng ("3,5 tỷ") -> bỏ nhãn cũ ("2–4 tỷ") để bản xác nhận hiện đúng số mới.
    if "budget_vnd" in found and "budget_label" not in found and data.get("budget_label"):
        setv("budget_label", None)
    # Nhất quán diện tích: khách nói diện tích mới (không kèm ngang/dài) -> bỏ ngang/dài cũ không khớp.
    if "land_area_m2" in changed and "land_width_m" not in found:
        w, length = data.get("land_width_m"), data.get("land_length_m")
        if w and length and abs(w * length - data["land_area_m2"]) > 0.01:
            setv("land_width_m", None)
            setv("land_length_m", None)
    if data.get("land_area_m2") is None and data.get("land_width_m") and data.get("land_length_m"):
        setv("land_area_m2", round(data["land_width_m"] * data["land_length_m"], 2))
    # Sửa diện tích đất (không nói lại diện tích xây dựng): "xây kín đất" (xây dựng = đất cũ) đi theo đất mới; diện
    # tích xây dựng cũ lớn hơn đất mới -> bỏ, hỏi lại khách (không tính trên diện tích sàn vượt khỏi lô đất).
    old_land, old_building, new_land = current.land_area_m2, current.building_area_m2, data.get("land_area_m2")
    if ("land_area_m2" in changed and "building_area_m2" not in found and old_land is not None
            and old_building is not None and new_land is not None):
        if abs(old_building - old_land) < 0.01:
            setv("building_area_m2", new_land)
        elif old_building > new_land:
            setv("building_area_m2", None)
            data["asked"] = [a for a in data.get("asked") or [] if a != "building_area_m2"]

    # Không có lửng -> không còn diện tích lửng; lần sau khách nói có lửng thì hỏi lại diện tích.
    if "has_mezzanine" in changed and data.get("has_mezzanine") is not True:
        setv("mezzanine_area_m2", None)
        data["asked"] = [a for a in data.get("asked") or [] if a != "mezzanine_area_m2"]

    try:
        return Slots.model_validate(data), changed
    except ValidationError:
        # Phòng hờ: gộp từng trường, bỏ trường không hợp lệ.
        log.warning("slot merge validation failed; merging field by field")
        safe = current.model_dump()
        ok_changed: list[str] = []
        for k in changed:
            trial = {**safe, k: data[k]}
            try:
                Slots.model_validate(trial)
            except ValidationError:
                continue
            safe = trial
            ok_changed.append(k)
        return Slots.model_validate(safe), ok_changed


def extract(message: str, current: Slots, provider: LLMProvider | None = None,
            expected_slots: list[str] | None = None) -> SlotUpdate:
    """Trích & gộp slot từ một tin nhắn của khách. Hàm không ghi DB."""
    current = current or Slots()
    expected = list(expected_slots or [])
    found = parse_message(message)
    building_answer = _building_area_answer(message, expected, found, current)
    mezz_answer = _mezzanine_area_answer(message, expected, found, current)
    short = _short_answer(message, expected, found)
    found.update(short)

    llm_used = False
    llm_intent: str | None = None
    if provider is not None and getattr(provider, "name", "mock") != "mock":
        try:
            llm_found, llm_intent = _llm_extract(provider, message, expected)
            llm_used = True
            for k, v in llm_found.items():
                # Trả lời câu hỏi diện tích xây dựng ("80 m2") không bao giờ là sửa diện tích đất (kể cả khi LLM đọc
                # "80 m2" thành diện tích đất).
                if building_answer and k in _LAND_KEYS:
                    continue
                # Trả lời câu hỏi diện tích lửng ("40 m2") không phải sửa diện tích đất / xây dựng.
                if mezz_answer and (k in _LAND_KEYS or k == "building_area_m2"):
                    continue
                if k not in found:
                    found[k] = v
        except Exception:  # noqa: BLE001 — lỗi provider không được làm hỏng lượt chat
            log.exception("LLM slot extraction failed; dùng kết quả rule-based")

    new_slots, changed = _merge(current, found)

    f = _short_text(message)
    raw_intent = detect_intent(message)
    if raw_intent == "edit" and short and set(short) <= set(expected):
        raw_intent = None  # "không" trả lời câu hỏi hầm/lửng là thông tin, không phải ý muốn sửa
    if (raw_intent == "edit" and expected and set(expected) <= {*DETAIL_SLOTS, *OPTIONAL_SLOTS}
            and _SKIP_ANSWER_RE.fullmatch(f)):
        raw_intent = None  # "không" trả lời câu hỏi chi tiết (vd mái) / tùy chọn = không có ý kiến, không phải sửa
    if raw_intent is None and llm_intent in ("confirm", "edit", "question", "out_of_scope"):
        raw_intent = llm_intent  # type: ignore[assignment]
    overwrote = any(getattr(current, k) is not None for k in changed if k in found)
    intent: SlotIntent
    if changed:
        intent = "edit" if (raw_intent == "edit" or overwrote) else "provide_info"
    elif raw_intent:
        intent = raw_intent
    elif found:
        intent = "provide_info"
    else:
        intent = "other"

    # Xác nhận chỉ khi đã đủ thông tin TRƯỚC khi tính slot vừa hỏi là "đã hỏi": khách đáp "ok" cho câu hỏi chi tiết
    # không phải là xác nhận bản tóm tắt (bản tóm tắt chưa hiển thị).
    confirmed = current.confirmed
    if changed or intent == "edit":
        confirmed = False
    # Đang hỏi câu chi tiết ("Cung cấp thêm thông tin") / tùy chọn: bản tóm tắt chưa hiển thị lại -> "ok"/"được"
    # không phải xác nhận tính khái toán (trừ đúng tin nhắn của nút "Tính ngay" trên bản tóm tắt cũ).
    asking_optional = bool(set(expected) & {*OPTIONAL_SLOTS, *DETAIL_SLOTS})
    if (intent == "confirm" and not missing_required(new_slots)
            and (not asking_optional or f == _CONFIRM_BUTTON)):
        confirmed = True
    # Slot chi tiết chỉ tính "đã hỏi" khi khách trả lời được, nói "chưa biết"/bỏ qua; câu trả lời không hiểu được
    # (hoặc "có" chung chung cho câu hỏi hầm + lửng) -> hỏi lại thay vì âm thầm dùng mặc định. Câu tùy chọn tính "đã
    # hỏi" trừ khi khách hỏi lại / muốn sửa mục khác (khi đó thẻ câu hỏi hiện lại).
    skip = bool(_UNKNOWN_RE.search(f) or _SKIP_ANSWER_RE.fullmatch(f)) or raw_intent == "confirm"
    asked = list(new_slots.asked)
    for s in expected:
        if s in asked:
            continue
        if s in DETAIL_SLOTS and getattr(new_slots, s) is None and not skip:
            continue
        if (s in OPTIONAL_SLOTS and not slot_value_known(new_slots, s) and not skip
                and raw_intent in ("question", "edit", "out_of_scope")):
            continue
        asked.append(s)
    new_slots = new_slots.model_copy(update={"confirmed": confirmed, "asked": asked})

    return SlotUpdate(slots=new_slots, changed=changed, intent=intent, missing_required=missing_required(new_slots),
                      llm_used=llm_used)


def update_slots(current: Slots, partial: dict[str, Any], message: str | None = None) -> SlotUpdate:
    """Gộp slot từ tham số tool `update_project_requirements` (giá trị thô, được chuẩn hóa bằng code).

    `message`: văn bản khách đã nói trong phiên. Khi có, áp bộ lọc chống bịa của normalize_raw_slots —
    số/địa danh LLM đưa vào phải xuất hiện trong lời khách (guardrail 2.1: slot là nguồn hợp lệ cho validator).
    """
    found = normalize_raw_slots(partial, message=message)
    new_slots, changed = _merge(current or Slots(), found)
    confirmed = (current.confirmed if current else False) and not changed
    new_slots = new_slots.model_copy(update={"confirmed": confirmed})
    return SlotUpdate(slots=new_slots, changed=changed, intent="provide_info" if changed else "other",
                      missing_required=missing_required(new_slots))
