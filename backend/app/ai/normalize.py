"""Chuẩn hóa thông tin khách nói thành giá trị slot (đặc tả mục 5.3).

Toàn bộ là hàm thuần, không gọi LLM/DB. So khớp không phân biệt dấu/hoa thường bằng cách "gập" chuỗi
(`fold`): bỏ dấu, đ→d, × → x, ² → 2, gạch ngang dài → "-". `fold` giữ nguyên độ dài chuỗi (NFC) để có thể
cắt lại đoạn văn bản gốc theo vị trí khớp trên chuỗi đã gập.

Hàm tổng hợp: `parse_message(text) -> dict[str, Any]` trả các slot tìm thấy (chỉ khóa có giá trị).
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal

from app.formatting import format_vnd
from app.schemas.common import BuildingType, WorkScope

HCM = "TP. Hồ Chí Minh"
HN = "Hà Nội"
DN = "Đà Nẵng"

Intent = Literal["confirm", "edit", "question", "out_of_scope"]

# ---------------------------------------------------------------------------
# Gập chuỗi & số
# ---------------------------------------------------------------------------

_SPECIAL = {
    "đ": "d", "Đ": "d", "×": "x", "✕": "x", "✖": "x", "²": "2",
    "–": "-", "—": "-", "−": "-", "‐": "-", "‑": "-", " ": " ",
}


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def fold(text: str) -> str:
    """Bỏ dấu + chữ thường, giữ nguyên độ dài so với `nfc(text)`.

    "Nhà phố 5×20 m² ở Thủ Đức" -> "nha pho 5x20 m2 o thu duc".
    """
    out: list[str] = []
    for ch in nfc(text):
        if ch in _SPECIAL:
            out.append(_SPECIAL[ch])
            continue
        base = unicodedata.normalize("NFD", ch)[0].lower()
        out.append(base[0] if base else ch)
    return "".join(out)


NUM = r"\d+(?:[.,]\d+)*"
# Số đứng riêng: không dính chữ số phía trước, không bị cắt giữa chừng phía sau.
_NUM_START = r"(?<![\d.,])"
_NUM_END = r"(?!\d|[.,]\d)"


def parse_vn_number(s: str) -> float | None:
    """Số kiểu Việt Nam: "4,5" -> 4.5; "2.000" -> 2000; "1.500.000" -> 1500000; "2.5" -> 2.5."""
    s = (s or "").strip()
    if not s or not re.fullmatch(NUM, s):
        return None
    if "." in s and "," in s:  # 1.234,5
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        parts = s.split(",")
        if len(parts) > 2:  # 1,500,000 (kiểu Anh)
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    elif "." in s:
        parts = s.split(".")
        if len(parts) > 2 or len(parts[1]) == 3:  # 2.000 / 1.500.000 -> phân tách hàng nghìn
            s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def _num(s: str) -> float:
    v = parse_vn_number(s)
    if v is None:  # regex đã đảm bảo dạng số; phòng hờ
        raise ValueError(s)
    return v


def _clean_number(v: float) -> float | int:
    return int(v) if float(v).is_integer() else round(v, 2)


_WORD_NUMBERS = {
    "mot": "1", "hai": "2", "ba": "3", "bon": "4", "tu": "4", "nam": "5", "sau": "6", "bay": "7",
    "tam": "8", "chin": "9", "muoi": "10",
}
_WORD_NUM_RE = re.compile(r"\b(" + "|".join(_WORD_NUMBERS) + r")(\s+)(?=(?:tret|lau|tang|lung|ham)\b)")


def _words_to_digits(f: str) -> str:
    """"mot tret hai lau" -> "1 tret 2 lau" (chỉ khi đứng ngay trước từ chỉ tầng)."""
    return _WORD_NUM_RE.sub(lambda m: _WORD_NUMBERS[m.group(1)] + m.group(2), f)


# ---------------------------------------------------------------------------
# Diện tích
# ---------------------------------------------------------------------------

_DIM_RE = re.compile(
    rf"{_NUM_START}({NUM}){_NUM_END}\s*(?:m\b|met\b)?\s*[x*]\s*({NUM}){_NUM_END}\s*(?:m\b|met\b)?"
)
_WIDTH_RE = re.compile(
    rf"\b(?:chieu\s+)?(?:ngang|rong|mat tien)\s*(?:la|khoang|:)?\s*({NUM}){_NUM_END}\s*(?:m\b|met\b)?"
    rf"(?!\s*(?:m2|m 2|met vuong|m vuong|2\b|vuong))"
)
_LENGTH_RE = re.compile(
    rf"\b(?:chieu\s+)?dai\s*(?:la|khoang|:)?\s*({NUM}){_NUM_END}\s*(?:m\b|met\b)?"
    rf"(?!\s*(?:m2|m 2|met vuong|m vuong|2\b|vuong))"
)
_AREA_RE = re.compile(rf"{_NUM_START}({NUM}){_NUM_END}\s*(?:m2|m 2|met vuong|m vuong)(?![a-z0-9])")
_AREA_BARE_RE = re.compile(
    rf"\bdien tich(?:\s+dat)?\s*(?:la|khoang|tam|:)?\s*({NUM}){_NUM_END}(?!\s*(?:m\b|met\b|x|\*|ty|ti|trieu|tr\b))"
)
_BUILDING_CTX = re.compile(r"xay dung|dien tich san|\bsan\b|\bxd\b|dtxd|moi tang|\b1 tang\b|mot tang")
_LAND_CTX = re.compile(r"\bdat\b|\blo\b|\bthua\b|\bkhu dat\b")


def _area_kind(f: str, start: int, end: int) -> str:
    """'land' hoặc 'building' dựa vào từ khóa gần nhất phía trước (và 'sàn' ngay phía sau)."""
    after = f[end:end + 8]
    if re.match(r"\s*san\b", after):
        return "building"
    window = f[max(0, start - 30):start]
    best_kind, best_pos = "land", -1
    for kind, rx in (("building", _BUILDING_CTX), ("land", _LAND_CTX)):
        for m in rx.finditer(window):
            if m.start() > best_pos:
                best_kind, best_pos = kind, m.start()
    return best_kind


def parse_dimensions(text: str) -> dict[str, Any]:
    """"5x20", "5 x 20m", "5×20 m", "ngang 5 dài 20", "rộng 4,5m dài 18m", "100m2", "120 mét vuông".

    Trả land_width_m, land_length_m, land_area_m2 (và building_area_m2 nếu khách nói "diện tích xây dựng/sàn").
    Quy ước "a x b" = ngang a × dài b.
    """
    f = fold(text)
    out: dict[str, Any] = {}
    w = length = None
    m = _DIM_RE.search(f)
    if m:
        w, length = _num(m.group(1)), _num(m.group(2))
    else:
        mw, ml = _WIDTH_RE.search(f), _LENGTH_RE.search(f)
        if mw and ml:
            w, length = _num(mw.group(1)), _num(ml.group(1))
    if w is not None and length is not None and 1 <= w <= 300 and 1 <= length <= 1000:
        out["land_width_m"] = _clean_number(w)
        out["land_length_m"] = _clean_number(length)
        out["land_area_m2"] = _clean_number(round(w * length, 2))

    for m in _AREA_RE.finditer(f):
        v = _num(m.group(1))
        if not 5 <= v <= 1_000_000:
            continue
        kind = _area_kind(f, m.start(), m.end())
        if kind == "building":
            out.setdefault("building_area_m2", _clean_number(v))
        elif "land_area_m2" not in out:
            out["land_area_m2"] = _clean_number(v)
    if "land_area_m2" not in out:
        m = _AREA_BARE_RE.search(f)
        if m and 5 <= _num(m.group(1)) <= 1_000_000:
            out["land_area_m2"] = _clean_number(_num(m.group(1)))
    return out


# ---------------------------------------------------------------------------
# Số tầng & kết cấu
# ---------------------------------------------------------------------------

_LAU_RE = re.compile(rf"{_NUM_START}(\d{{1,2}})\s*(?:tang\s+)?lau\b")
_TANG_RE = re.compile(rf"{_NUM_START}(\d{{1,2}})\s*tang\b(?!\s*(?:ham|lung|tret|thuong|mai|ap mai|lau))")
_TRET_RE = re.compile(r"\btret\b")
_LUNG_RE = re.compile(r"(?:\b\d\s*|\btang\s+|\bgac\s+|\bco\s+|\bthem\s+|\+\s*)lung\b|\blung\b(?=\s*(?:\d|,|\+|$))")
_NO_LUNG_RE = re.compile(r"\bkhong\s+(?:co\s+|lam\s+)?(?:tang\s+|gac\s+)?lung\b")
_CAP4_RE = re.compile(r"\bnha\s+cap\s*(?:4|bon)\b")
# "còn N tầng" = số tầng tuyệt đối; "giảm/bớt/thêm N tầng" = thay đổi tương đối (không phải số tầng).
_REMAIN_FLOORS_RE = re.compile(rf"\bcon(?:\s+lai)?\s+{_NUM_START}(\d{{1,2}})\s*tang\b(?!\s*(?:ham|lung))")
_RELATIVE_FLOORS_RE = re.compile(
    r"\b(?:giam|bot|them|cat|bo|nang|ha)\s+(?:di\s+|bot\s+|them\s+|xuong\s+|len\s+)?\d{1,2}\s*(?:tang|lau)\b")


def parse_floors(text: str) -> dict[str, Any]:
    """"1 trệt 3 lầu" -> floors=4; "1 trệt 1 lửng 2 lầu" -> floors=3, has_mezzanine=True;
    "3 tầng"/"nhà 4 tầng" -> floors=N; "trệt + 2 lầu" -> 3; "1 trệt" -> 1.

    Quy ước: "N lầu" (không nói trệt) = 1 trệt + N lầu = N+1 tầng (cách nói phổ biến phía Nam).
    Tầng lửng không tính vào số tầng nổi.
    """
    f = _words_to_digits(fold(text))
    out: dict[str, Any] = {}
    # Nhãn phương án "Giảm 1 tầng (còn 3 tầng)" -> floors=3 (không phải 1).
    remain = _REMAIN_FLOORS_RE.search(f)
    f = _RELATIVE_FLOORS_RE.sub(" ", f)
    lau = _LAU_RE.search(f)
    tang = _TANG_RE.search(f)
    if remain:
        floors = int(remain.group(1))
    elif lau:
        floors = int(lau.group(1)) + 1
    elif tang:
        floors = int(tang.group(1))
    elif _TRET_RE.search(f) or _CAP4_RE.search(f):
        floors = 1
    else:
        floors = None
    if floors is not None and 1 <= floors <= 50:
        out["floors"] = floors
    if _NO_LUNG_RE.search(f):
        out["has_mezzanine"] = False
    elif _LUNG_RE.search(f):
        out["has_mezzanine"] = True
    return out


_NO_BASEMENT_RE = re.compile(r"\bkhong\s+(?:co\s+|xay\s+|lam\s+|can\s+)?(?:tang\s+)?ham\b")
_BASEMENT_RE = re.compile(
    r"\btang\s+ham\b|\b\d\s*ham\b|\b(?:co|them|xay|lam)\s+ham\b|\bham\s+(?:sau|de xe|gui xe|xe)\b"
)
_BASEMENT_DEPTH_RE = re.compile(rf"\bham\s*(?:sau|cao)?\s*(?:khoang\s*)?({NUM}){_NUM_END}\s*(?:m\b|met\b)")


def parse_structure(text: str) -> dict[str, Any]:
    """Hầm, sân thượng, mái, móng.

    - "có tầng hầm"/"1 hầm" -> basement=True ("không hầm" -> False); "hầm sâu 1,5m" -> basement_depth_m.
    - "sân thượng" -> rooftop_terrace: "covered" nếu "có mái che", mặc định "open"; "không sân thượng" -> "none".
    - "mái tôn" -> roof_metal; "mái bằng/BTCT/bê tông" -> roof_concrete;
      "mái ngói kèo thép" -> roof_tile_on_truss; "mái ngói" (đổ BTCT hoặc không nói rõ) -> roof_tile_on_concrete.
    - "móng cọc" -> foundation_pile; "móng băng" -> foundation_strip; "móng đơn" -> foundation_single.
    """
    f = _words_to_digits(fold(text))
    out: dict[str, Any] = {}
    if _NO_BASEMENT_RE.search(f):
        out["basement"] = False
    elif _BASEMENT_RE.search(f):
        out["basement"] = True
        m = _BASEMENT_DEPTH_RE.search(f)
        if m and 0.5 <= _num(m.group(1)) <= 20:
            out["basement_depth_m"] = _clean_number(_num(m.group(1)))

    if re.search(r"\bkhong\s+(?:co\s+|lam\s+)?san thuong\b", f):
        out["rooftop_terrace"] = "none"
    elif re.search(r"\bsan thuong\b", f):
        if re.search(r"\bsan thuong\s+(?:khong\s+(?:co\s+)?mai|lo thien|de trong)", f):
            out["rooftop_terrace"] = "open"
        elif re.search(r"\bsan thuong\s+(?:co\s+)?(?:mai che|mai)\b", f):
            out["rooftop_terrace"] = "covered"
        else:
            out["rooftop_terrace"] = "open"

    if re.search(r"\bmai\s+ton\b", f):
        out["roof_type"] = "roof_metal"
    elif re.search(r"\bmai\s+ngoi\s+(?:tren\s+)?keo\s+(?:thep|sat)", f):
        out["roof_type"] = "roof_tile_on_truss"
    elif re.search(r"\bmai\s+ngoi\b", f):
        out["roof_type"] = "roof_tile_on_concrete"
    elif re.search(r"\bmai\s+(?:bang|btct|be tong|do be tong|do btct)\b", f):
        out["roof_type"] = "roof_concrete"

    if re.search(r"\bmong\s+coc\b|\bep\s+coc\b|\bkhoan\s+coc\b", f):
        out["foundation_type"] = "foundation_pile"
    elif re.search(r"\bmong\s+bang\b", f):
        out["foundation_type"] = "foundation_strip"
    elif re.search(r"\bmong\s+don\b", f):
        out["foundation_type"] = "foundation_single"
    return out


# ---------------------------------------------------------------------------
# Tiền (ngân sách)
# ---------------------------------------------------------------------------

TY = 1_000_000_000
TRIEU = 1_000_000
_TY = r"(?:ty|ti)(?![a-z])"  # "1tỷ5"/"1ty5" vẫn khớp (chữ số ngay sau đơn vị)
_TR = r"(?:trieu|tr)\b"
_PER_M2 = re.compile(r"\s*(?:/|tren|moi|1|mot)\s*m(?:2|\b)")
_RANGE_RE = re.compile(
    rf"{_NUM_START}({NUM}){_NUM_END}\s*({_TY}|{_TR})?\s*(?:-|den|toi|~)\s*({NUM}){_NUM_END}\s*({_TY}|{_TR})"
)
_TY_RE = re.compile(
    rf"{_NUM_START}({NUM}){_NUM_END}\s*{_TY}"
    rf"(?:\s*(ruoi)\b|\s*(\d{{1,3}})(?!\d|[.,]\d)"
    rf"(?!\s*(?:m\b|m2|met|x|\*|lau|tang|tret|lung|ham|nguoi|thang|nam|ngay|tuan|%|phong|can))\s*(?:{_TR})?)?"
)
_TR_RE = re.compile(rf"{_NUM_START}({NUM}){_NUM_END}\s*{_TR}")
_VND_RE = re.compile(rf"{_NUM_START}(\d{{1,3}}(?:[.,]\d{{3}}){{2,}}|\d{{7,}}){_NUM_END}\s*(?:d|dong|vnd)\b")
_BUDGET_BARE_RE = re.compile(
    rf"\bngan sach\s*(?:khoang|tam|la|du kien|toi da|:)?\s*(?:khoang\s*)?({NUM}){_NUM_END}"
    rf"(?!\s*(?:ty|ti|trieu|tr\b|m\b|m2|x|lau|tang))"
)

MIN_BUDGET = 10 * TRIEU
MAX_BUDGET = 1000 * TY


def _unit_value(unit: str | None) -> int:
    return TY if unit in ("ty", "ti") else TRIEU


def _ok_budget(v: float) -> int | None:
    v = int(round(v))
    return v if MIN_BUDGET <= v <= MAX_BUDGET else None


def parse_budget(text: str) -> int | None:
    """Số tiền khách nói -> VND.

    "2 tỷ"/"2 tỉ"/"2.000 triệu" -> 2_000_000_000; "2,5 tỷ"; "1 tỷ 8" -> 1,8 tỷ; "1 tỷ 800 triệu";
    "1 tỷ rưỡi"; "850 triệu"/"850tr". Khoảng giá: "Dưới 1,5 tỷ" -> 1,5 tỷ; "1,5–2,5 tỷ" -> 2,5 tỷ (cận trên);
    "Trên 4 tỷ" -> 4 tỷ (cận dưới — hội thoại dùng parse_budget_info: "trên" không có trần). Bỏ qua đơn giá theo m² ("5 triệu/m²").
    """
    f = fold(text)

    def per_m2(end: int) -> bool:
        return bool(_PER_M2.match(f, end))

    for m in _RANGE_RE.finditer(f):
        if per_m2(m.end()):
            continue
        hi_unit = _unit_value(m.group(4))
        v = _ok_budget(_num(m.group(3)) * hi_unit)
        if v:
            return v
    for m in _TY_RE.finditer(f):
        if per_m2(m.end()):
            continue
        base = _num(m.group(1)) * TY
        if m.group(2):  # rưỡi
            base += TY / 2
        elif m.group(3):
            extra = m.group(3)
            has_tr = bool(re.search(_TR, f[m.start(3):m.end()]))
            if has_tr or len(extra) == 3:
                base += int(extra) * TRIEU
            elif len(extra) == 2:
                base += int(extra) * 10 * TRIEU
            else:
                base += int(extra) * 100 * TRIEU
        v = _ok_budget(base)
        if v:
            return v
    for m in _TR_RE.finditer(f):
        if per_m2(m.end()):
            continue
        v = _ok_budget(_num(m.group(1)) * TRIEU)
        if v:
            return v
    for m in _VND_RE.finditer(f):
        v = _ok_budget(_num(m.group(1)))
        if v:
            return v
    m = _BUDGET_BARE_RE.search(f)
    if m:
        return bare_budget(_num(m.group(1)))
    return None


# "trên 10 tỷ", "hơn 5 tỷ", "từ 3 tỷ" (không kèm cận trên), "5 tỷ trở lên": cận DƯỚI — không có trần ngân sách.
_BUDGET_LOWER_RE = re.compile(
    rf"\b(?:tren|hon|it nhat|toi thieu|tu)\s+({NUM}){_NUM_END}\s*({_TY}|{_TR})(?!\s*(?:-|den|toi|~))"
    rf"|{_NUM_START}({NUM}){_NUM_END}\s*({_TY}|{_TR})\s*(?:tro len|tro di|tro ra)\b"
)
# "dưới 2 tỷ", "tối đa 3 tỷ": cận trên (dùng làm trần), nhãn giữ nguyên cách khách nói.
_BUDGET_UPPER_RE = re.compile(
    rf"\b(?:duoi|toi da|khong qua|chua toi|chua den)\s+({NUM}){_NUM_END}\s*({_TY}|{_TR})(?!\s*(?:-|den|toi|~))"
)


def parse_budget_info(text: str) -> tuple[int | None, str | None] | None:
    """Ngân sách khách nói -> (trần ngân sách VND | None, nhãn hiển thị | None). None: tin nhắn không nêu ngân sách.

    - "Ngân sách trên 10 tỷ" / "10 tỷ trở lên" -> (None, "Trên 10 tỷ"): chỉ có cận dưới -> KHÔNG có trần (không so
      ngân sách với khái toán, không chấm mức vừa ngân sách của nhà thầu); nhãn hiển thị trên bản xác nhận.
    - "Ngân sách 2–4 tỷ" -> (4 tỷ, "2–4 tỷ"): cận trên làm trần, nhãn giữ khoảng khách chọn.
    - "Ngân sách dưới 2 tỷ" -> (2 tỷ, "Dưới 2 tỷ").
    - "3,5 tỷ" -> (3,5 tỷ, None): hiển thị bằng format_vnd.
    """
    f = fold(text)
    for m in _BUDGET_LOWER_RE.finditer(f):
        num, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        if _PER_M2.match(f, m.end()):
            continue
        v = _ok_budget(_num(num) * _unit_value(unit))
        if v:
            return None, f"Trên {format_vnd(v)}"
    for m in _RANGE_RE.finditer(f):
        if _PER_M2.match(f, m.end()):
            continue
        hi_unit = _unit_value(m.group(4))
        hi = _ok_budget(_num(m.group(3)) * hi_unit)
        if hi:
            lo = _num(m.group(1)) * (_unit_value(m.group(2)) if m.group(2) else hi_unit)
            if not 0 < lo < hi:
                return hi, None
            lo_txt, hi_txt = format_vnd(lo), format_vnd(hi)
            lo_num, _, lo_unit = lo_txt.rpartition(" ")
            # Cùng đơn vị: "2–4 tỷ"; khác đơn vị: "800 triệu–1 tỷ" (không đệm số 0 như format_vnd_range).
            return hi, f"{lo_num}–{hi_txt}" if hi_txt.endswith(f" {lo_unit}") else f"{lo_txt}–{hi_txt}"
    for m in _BUDGET_UPPER_RE.finditer(f):
        if _PER_M2.match(f, m.end()):
            continue
        v = _ok_budget(_num(m.group(1)) * _unit_value(m.group(2)))
        if v:
            return v, f"Dưới {format_vnd(v)}"
    v = parse_budget(text)
    return (v, None) if v else None


def bare_budget(v: float) -> int | None:
    """Số không đơn vị khi đang nói về ngân sách: < 100 -> tỷ; 100..99.999 -> triệu; lớn hơn -> VND."""
    if v < 100:
        return _ok_budget(v * TY)
    if v < 100_000:
        return _ok_budget(v * TRIEU)
    return _ok_budget(v)


# ---------------------------------------------------------------------------
# Tỉnh / quận
# ---------------------------------------------------------------------------

# alias (đã gập) -> tên tỉnh chuẩn. Tên tỉnh theo danh mục trước sáp nhập 2025 (khớp seed).
_PROVINCE_ALIASES: dict[str, str] = {
    "tp ho chi minh": HCM, "thanh pho ho chi minh": HCM, "ho chi minh": HCM, "hcm": HCM, "tphcm": HCM,
    "tp hcm": HCM, "hcmc": HCM, "sai gon": HCM, "saigon": HCM, "sg": HCM,
    "ha noi": HN, "hanoi": HN, "hn": HN, "tp ha noi": HN, "thu do": HN,
    "da nang": DN, "danang": DN,
    "hai phong": "Hải Phòng", "can tho": "Cần Thơ",
    "binh duong": "Bình Dương", "dong nai": "Đồng Nai", "long an": "Long An",
    "ba ria vung tau": "Bà Rịa - Vũng Tàu", "ba ria - vung tau": "Bà Rịa - Vũng Tàu", "brvt": "Bà Rịa - Vũng Tàu",
    "vung tau": "Bà Rịa - Vũng Tàu",
    "tay ninh": "Tây Ninh", "binh phuoc": "Bình Phước", "tien giang": "Tiền Giang", "ben tre": "Bến Tre",
    "vinh long": "Vĩnh Long", "tra vinh": "Trà Vinh", "dong thap": "Đồng Tháp", "an giang": "An Giang",
    "kien giang": "Kiên Giang", "hau giang": "Hậu Giang", "soc trang": "Sóc Trăng", "bac lieu": "Bạc Liêu",
    "ca mau": "Cà Mau",
    "lam dong": "Lâm Đồng", "binh thuan": "Bình Thuận", "ninh thuan": "Ninh Thuận", "khanh hoa": "Khánh Hòa",
    "phu yen": "Phú Yên", "binh dinh": "Bình Định", "quang ngai": "Quảng Ngãi", "quang nam": "Quảng Nam",
    "thua thien hue": "Thừa Thiên Huế", "hue": "Thừa Thiên Huế", "quang tri": "Quảng Trị",
    "quang binh": "Quảng Bình", "ha tinh": "Hà Tĩnh", "nghe an": "Nghệ An", "thanh hoa": "Thanh Hóa",
    "dak lak": "Đắk Lắk", "daklak": "Đắk Lắk", "dac lac": "Đắk Lắk", "dak nong": "Đắk Nông",
    "gia lai": "Gia Lai", "kon tum": "Kon Tum",
    "bac ninh": "Bắc Ninh", "hung yen": "Hưng Yên", "ha nam": "Hà Nam", "nam dinh": "Nam Định",
    "thai binh": "Thái Bình", "ninh binh": "Ninh Bình", "hai duong": "Hải Dương", "vinh phuc": "Vĩnh Phúc",
    "phu tho": "Phú Thọ", "hoa binh": "Hòa Bình", "thai nguyen": "Thái Nguyên", "bac giang": "Bắc Giang",
    "quang ninh": "Quảng Ninh", "lang son": "Lạng Sơn", "cao bang": "Cao Bằng", "bac kan": "Bắc Kạn",
    "bac can": "Bắc Kạn", "tuyen quang": "Tuyên Quang", "ha giang": "Hà Giang", "yen bai": "Yên Bái",
    "lao cai": "Lào Cai", "lai chau": "Lai Châu", "dien bien": "Điện Biên", "son la": "Sơn La",
}

# alias (đã gập) -> (tên quận/huyện/thành phố trực thuộc chuẩn, tỉnh).
_DISTRICT_ALIASES: dict[str, tuple[str, str]] = {
    "tp thu duc": ("TP. Thủ Đức", HCM), "thanh pho thu duc": ("TP. Thủ Đức", HCM),
    "thu duc": ("TP. Thủ Đức", HCM),
    "binh thanh": ("Bình Thạnh", HCM), "go vap": ("Gò Vấp", HCM), "phu nhuan": ("Phú Nhuận", HCM),
    "tan binh": ("Tân Bình", HCM), "tan phu": ("Tân Phú", HCM), "binh tan": ("Bình Tân", HCM),
    "nha be": ("Nhà Bè", HCM), "hoc mon": ("Hóc Môn", HCM), "cu chi": ("Củ Chi", HCM),
    "binh chanh": ("Bình Chánh", HCM), "can gio": ("Cần Giờ", HCM),
    "cau giay": ("Cầu Giấy", HN), "hoan kiem": ("Hoàn Kiếm", HN), "ba dinh": ("Ba Đình", HN),
    "dong da": ("Đống Đa", HN), "hai ba trung": ("Hai Bà Trưng", HN), "thanh xuan": ("Thanh Xuân", HN),
    "tay ho": ("Tây Hồ", HN), "long bien": ("Long Biên", HN), "hoang mai": ("Hoàng Mai", HN),
    "ha dong": ("Hà Đông", HN), "nam tu liem": ("Nam Từ Liêm", HN), "bac tu liem": ("Bắc Từ Liêm", HN),
    "gia lam": ("Gia Lâm", HN), "dong anh": ("Đông Anh", HN), "soc son": ("Sóc Sơn", HN),
    "thanh tri": ("Thanh Trì", HN),
    "hai chau": ("Hải Châu", DN), "son tra": ("Sơn Trà", DN), "ngu hanh son": ("Ngũ Hành Sơn", DN),
    "lien chieu": ("Liên Chiểu", DN), "thanh khe": ("Thanh Khê", DN), "cam le": ("Cẩm Lệ", DN),
    "hoa vang": ("Hòa Vang", DN),
    "bien hoa": ("TP. Biên Hòa", "Đồng Nai"), "thu dau mot": ("TP. Thủ Dầu Một", "Bình Dương"),
    "di an": ("TP. Dĩ An", "Bình Dương"), "thuan an": ("TP. Thuận An", "Bình Dương"),
    "nha trang": ("TP. Nha Trang", "Khánh Hòa"), "da lat": ("TP. Đà Lạt", "Lâm Đồng"),
    "ha long": ("TP. Hạ Long", "Quảng Ninh"), "quy nhon": ("TP. Quy Nhơn", "Bình Định"),
    "buon ma thuot": ("TP. Buôn Ma Thuột", "Đắk Lắk"), "ninh kieu": ("Ninh Kiều", "Cần Thơ"),
    "phu quoc": ("TP. Phú Quốc", "Kiên Giang"),
}

# Alias dễ trùng với cụm từ thường ("tính giá lại", "nhà bé"...) — chỉ nhận khi văn bản gốc viết hoa chữ đầu.
_CASE_SENSITIVE = {"gia lai", "son la", "long an", "nha be", "hue", "ha nam", "thu do"}

_QUAN_RE = re.compile(r"\b(?:quan|q)\s*\.?\s*(\d{1,2})\b")

_DISTRICT_PROVINCE: dict[str, str] = {name: prov for name, prov in _DISTRICT_ALIASES.values()}


def _loc_text(text: str) -> tuple[str, str]:
    """(chuỗi gốc NFC, chuỗi gập) với dấu chấm/gạch dưới thay bằng khoảng trắng — giữ độ dài."""
    orig = nfc(text)
    f = fold(orig)
    f = re.sub(r"[._/,;:()]", " ", f)
    return orig, f


def _find_aliases(f: str, orig: str, aliases: dict[str, Any]) -> list[tuple[int, int, str]]:
    hits: list[tuple[int, int, str]] = []
    for alias in aliases:
        pat = r"(?<![a-z0-9])" + re.escape(alias).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
        for m in re.finditer(pat, f):
            if alias in _CASE_SENSITIVE and not orig[m.start()].isupper():
                continue
            hits.append((m.start(), m.end(), alias))
    # Bỏ các khớp nằm trong một khớp dài hơn.
    hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
    kept: list[tuple[int, int, str]] = []
    for h in hits:
        if any(k[0] <= h[0] and h[1] <= k[1] for k in kept):
            continue
        kept.append(h)
    return kept


def parse_location(text: str) -> dict[str, str]:
    """Tỉnh/quận. "Sài Gòn", "HCM", "TPHCM", "tp.hcm" -> province "TP. Hồ Chí Minh";
    "Thủ Đức" -> district "TP. Thủ Đức" + suy ra province; "Quận 7"/"Q7" -> district "Quận 7" (TP.HCM)."""
    orig, f = _loc_text(text)
    out: dict[str, str] = {}
    dhits = _find_aliases(f, orig, _DISTRICT_ALIASES)
    phits = _find_aliases(f, orig, _PROVINCE_ALIASES)
    # Khớp tỉnh nằm trong khớp quận (vd "ha dong" không trùng) -> ưu tiên quận.
    phits = [p for p in phits if not any(d[0] <= p[0] and p[1] <= d[1] for d in dhits)]
    if dhits:
        name, prov = _DISTRICT_ALIASES[dhits[0][2]]
        out["district"] = name
        out["province"] = prov
    else:
        m = _QUAN_RE.search(f)
        if m and 1 <= int(m.group(1)) <= 12:
            out["district"] = f"Quận {int(m.group(1))}"
            out["province"] = HCM
    if phits:
        out["province"] = _PROVINCE_ALIASES[phits[0][2]]
    return out


def normalize_province(name: str | None) -> str | None:
    """Chuẩn hóa tên tỉnh từ chuỗi bất kỳ (vd đầu ra LLM). Không nhận diện được -> None."""
    if not name:
        return None
    return parse_location(name).get("province")


def province_of_district(district: str | None) -> str | None:
    if not district:
        return None
    if re.fullmatch(r"Quận \d{1,2}", district):
        return HCM
    return _DISTRICT_PROVINCE.get(district)


# ---------------------------------------------------------------------------
# Loại công trình, phạm vi, thời gian
# ---------------------------------------------------------------------------

_BUILDING_PATTERNS: list[tuple[BuildingType, str]] = [
    (BuildingType.biet_thu, r"\b(?:biet thu|villa|dinh thu)\b"),
    (BuildingType.nha_pho, r"\b(?:nha pho|nha ong|nha lien ke|nha mat pho|nha mat tien|townhouse|shophouse)\b"),
    (BuildingType.can_ho, r"\b(?:can ho|chung cu|apartment|condo|penthouse)\b"),
    (BuildingType.van_phong, r"\b(?:van phong|office)\b"),
    (BuildingType.khach_san, r"\b(?:khach san|hotel|nha nghi)\b"),
]


# Cả tin nhắn chỉ là "Công trình khác"/"Loại công trình khác" (giá trị nút "Khác") -> khac.
_OTHER_TYPE_RE = re.compile(r"^(?:loai\s+)?(?:cong trinh|nha|loai)\s+khac(?:\s+(?:a|nhe|nha))?$")


def parse_building_type(text: str) -> BuildingType | None:
    f = fold(text)
    best: tuple[int, BuildingType] | None = None
    for bt, pat in _BUILDING_PATTERNS:
        m = re.search(pat, f)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), bt)
    if best:
        return best[1]
    if _OTHER_TYPE_RE.match(re.sub(r"[!.,;:?]+", " ", f).strip()):
        return BuildingType.khac
    return None


def parse_work_scope(text: str) -> WorkScope | None:
    """"xây thô"/"phần thô" -> xay_tho; "hoàn thiện" -> hoan_thien; "trọn gói"/"chìa khóa trao tay"
    hoặc "thô + hoàn thiện" -> tron_goi; "nội thất" -> noi_that. "mức hoàn thiện ..." không phải phạm vi (hội thoại
    không phân mức hoàn thiện)."""
    f = fold(text)
    if re.search(r"\b(?:tron goi|chia khoa trao tay|turnkey)\b", f):
        return WorkScope.tron_goi
    tho = re.search(r"\b(?:xay|phan|thi cong|nhan cong|khung)\s+tho\b", f)
    hoan_thien = re.search(r"(?<!muc )(?<!muc do )\bhoan thien\b(?!\s+(?:o\s+)?muc)", f)
    if tho and (hoan_thien or re.search(r"\btho\s*(?:va|\+|&|lan)\s*hoan thien\b", f)):
        return WorkScope.tron_goi
    if tho:
        return WorkScope.xay_tho
    if hoan_thien:
        return WorkScope.hoan_thien
    if re.search(r"(?<!khong gom )(?<!chua gom )\bnoi that\b", f):
        return WorkScope.noi_that
    return None


_TIME_CTX = re.compile(r"\b(?:khoi cong|bat dau|thi cong|du kien xay|muon xay|xay vao|thoi gian)\b")
_TIME_GENERIC = re.compile(
    r"\b(?:thang sau|thang toi|tuan sau|tuan toi|nam sau|nam toi|sau tet|ra tet|cuoi nam(?: nay| sau)?"
    r"|dau nam(?: nay| sau)?|giua nam(?: nay| sau)?|thang \d{1,2}(?:\s*/\s*\d{4}|\s+nam\s+\d{4})?"
    r"|quy \d(?:\s*/\s*\d{4}|\s+nam\s+\d{4})?|nam \d{4}|sau\s+\d{1,2}\s+thang(?:\s+nua)?|ngay)\b"
)


def parse_start_time(text: str) -> str | None:
    """"Trong 3 tháng"; "3–6 tháng"; "Chưa xác định"; hoặc cụm thời gian gốc ("tháng sau", "sau Tết"...)
    khi câu có ngữ cảnh khởi công."""
    orig = nfc(text)
    f = fold(orig)
    m = re.search(r"\btrong\s+(?:vong\s+)?(\d{1,2})\s*thang\b", f)
    if m:
        return f"Trong {int(m.group(1))} tháng"
    m = re.search(rf"{_NUM_START}(\d{{1,2}})\s*-\s*(\d{{1,2}})\s*thang\b", f)
    if m:
        return f"{int(m.group(1))}–{int(m.group(2))} tháng"
    ctx = _TIME_CTX.search(f)
    if ctx and re.search(r"\b(?:chua xac dinh|chua biet|chua ro|chua co ke hoach)\b", f):
        return "Chưa xác định"
    if ctx:
        g = _TIME_GENERIC.search(f)
        if g:
            s = orig[g.start():g.end()]
            return s[:1].upper() + s[1:]
    return None


# ---------------------------------------------------------------------------
# Phong cách thiết kế, mức đầu tư (chỉ ghi nhận — không vào khái toán)
# ---------------------------------------------------------------------------

_STYLE_WORDS: list[tuple[str, str]] = [
    ("tan_co_dien", r"tan co dien|neo ?classic"),
    ("hien_dai", r"hien dai|modern"),
    ("toi_gian", r"toi gian|minimal(?:ism|ist)?"),
    ("indochine", r"indochine|dong duong"),
]
_STYLE_CTX = (r"\b(?:phong cach|kieu|style|thiet ke|loi kien truc)\s+(?:(?:sang|thanh|la|theo)\s+)?"
              r"(?:thiet ke\s+|kien truc\s+|nha\s+)?")
_INVEST_WORDS: list[tuple[str, str]] = [
    ("sieu_cao_cap", r"sieu cao cap|cao cap|sang trong|hang sang|luxury"),
    ("trung_cap", r"trung cap|tam trung"),
    ("pho_thong", r"pho thong|co ban|trung binh|binh thuong"),
    ("tiet_kiem", r"tiet kiem|binh dan|gia re"),
]
_INVEST_CTX = (r"\b(?:muc dau tu|dau tu|phan khuc|goi dau tu)(?:\s+mong muon)?\s+(?:(?:o|sang|thanh|la|chon)\s+)?"
               r"(?:muc\s+|phan khuc\s+|goi\s+)?")


def parse_design_style(text: str, *, bare: bool = False) -> str | None:
    """Phong cách thiết kế: "phong cách hiện đại" -> hien_dai; "kiểu Indochine" -> indochine.

    `bare=True` (đang hỏi phong cách): nhận cả câu trả lời trần ("hiện đại", "tân cổ điển")."""
    f = re.sub(r"[!.,;:?]+", " ", fold(text))
    for code, words in _STYLE_WORDS:
        if re.search(_STYLE_CTX + rf"(?:{words})\b", f):
            return code
        if bare and re.fullmatch(rf"\s*(?:phong cach\s+|kieu\s+|style\s+)?(?:{words})(?:\s+(?:a|nhe|nha))?\s*", f):
            return code
    return None


def parse_investment_level(text: str, *, bare: bool = False) -> str | None:
    """Mức đầu tư: "mức đầu tư phổ thông" -> pho_thong; "đầu tư cao cấp" -> sieu_cao_cap.

    `bare=True` (đang hỏi mức đầu tư): nhận cả câu trả lời trần ("tiết kiệm", "trung cấp")."""
    f = re.sub(r"[!.,;:?]+", " ", fold(text))
    for code, words in _INVEST_WORDS:
        if re.search(_INVEST_CTX + rf"(?:{words})\b", f):
            return code
        if bare and re.fullmatch(rf"\s*(?:muc\s+|goi\s+|phan khuc\s+)?(?:{words})(?:\s+(?:a|nhe|nha))?\s*", f):
            return code
    return None


# ---------------------------------------------------------------------------
# Ý định
# ---------------------------------------------------------------------------

_NEGATED_CONFIRM = re.compile(r"\b(?:khong|chua|ko)\s+(?:dung|chuan|dong y|chinh xac|phai)\b")
_EDIT_RE = re.compile(
    r"\b(?:sua(?!\s+(?:chua|nha|cai tao))|doi lai|doi thanh|doi sang|thay doi|chinh lai|chinh sua|cap nhat lai|nhap lai"
    r"|nham|sai roi|bi sai|sai thong tin|khong phai)\b"
)
# "đổi ..." ở đầu câu — kiểm trên chuỗi gốc (chữ thường) vì "đợi"/"đội" cũng gập thành "doi".
_EDIT_START_RE = re.compile(r"^\s*(?:đổi|doi)\b")
# Trả lời trống "không"/"chưa"/"sai" (thường là đáp lại câu hỏi xác nhận) -> muốn sửa.
_BARE_NO_RE = re.compile(r"^(?:khong|ko|k|chua|sai)(?:\s+(?:a|nhe|dau))?$")
_CONFIRM_RE = re.compile(
    r"^\s*(?:da\s+|vang\s+|u\s+)?(?:dung roi|dung vay|dung the|dung a|dung\s*$|chuan roi|chuan|ok|oke|okay|okie"
    r"|xac nhan|dong y|chinh xac|vang|yes|duoc roi|duoc|tinh di|tinh giup|tinh khai toan|tiep tuc|u|um|uh)\b"
    r"|\b(?:xac nhan|dong y|dung roi)\b"
)
_QUESTION_RE = re.compile(
    r"\?|\b(?:bao nhieu|bao lau|the nao|nhu nao|ra sao|tai sao|vi sao|lam sao|co nen|nen chon|nen xay"
    r"|o dau|khi nao|hay khong|duoc khong|phai khong|la gi|gom nhung gi|nhung gi|cai gi|nao)\b"
    r"|\b(?:khong|ko|chua)\s*(?:a|vay|nhi|nhe)?\s*$"
)
_OUT_OF_SCOPE_RE = re.compile(
    r"\b(?:thoi tiet|bong da|chung khoan|co phieu|bitcoin|tien ao|crypto|xo so|lo de|nau an|mon an|cong thuc"
    r"|bai hat|ca si|phim|game|tinh yeu|nguoi yeu|chinh tri|bau cu|lam tho|viet code|lap trinh|dich sang"
    r"|ve may bay|du lich)\b"
)
_CONSTRUCTION_RE = re.compile(
    r"\b(?:nha|xay|thau|cong trinh|chi phi|don gia|gia|tang|lau|m2|noi that|thiet ke|vat lieu|mong|mai"
    r"|dat|khai toan|bao gia|du toan|ngan sach|thi cong|sua chua|can ho|biet thu|van phong|khach san)\b"
)


_HYPOTHETICAL_RE = re.compile(r"\b(?:neu|gia su|gia nhu|truong hop|thu tinh)\b")


def is_hypothetical(text: str) -> bool:
    """Câu giả định ("nếu chỉ xây 3 tầng thì hết bao nhiêu?") — tính thử, KHÔNG đổi thông tin công trình."""
    return bool(_HYPOTHETICAL_RE.search(fold(text)))


def detect_intent(text: str) -> Intent | None:
    """Ý định thô của tin nhắn: confirm | edit | question | out_of_scope | None (không rõ)."""
    f = fold(text).strip()
    if not f:
        return None
    f_words = re.sub(r"[!.,;:~]+", " ", f).strip()
    orig_lower = nfc(text).lower().strip()
    if (_NEGATED_CONFIRM.search(f_words) or _EDIT_RE.search(f_words) or _EDIT_START_RE.match(orig_lower)
            or _BARE_NO_RE.match(f_words)):
        return "edit"
    if _CONFIRM_RE.search(f_words) and "?" not in f:
        return "confirm"
    if _OUT_OF_SCOPE_RE.search(f_words) and not _CONSTRUCTION_RE.search(f_words):
        return "out_of_scope"
    if _QUESTION_RE.search(f_words) or "?" in f:
        return "question"
    return None


# ---------------------------------------------------------------------------
# Tổng hợp
# ---------------------------------------------------------------------------


_AREA_UNIT = r"\s*(?:m2|m 2|met vuong|m vuong)(?![a-z0-9])"
# "tầng lửng 40 m2", "lửng khoảng 50m2", "lửng rộng 40 m²", "diện tích lửng: 40" — số phải đứng ngay sau "lửng" (không
# vượt qua chữ số/dấu phẩy: "1 lửng 3 lầu, 80m2" không phải diện tích lửng).
_MEZZ_AREA_RE = re.compile(
    rf"\b(?:tang\s+|gac\s+)?lung\b[^\d,;]{{0,20}}?{_NUM_START}({NUM}){_NUM_END}{_AREA_UNIT}"
    rf"|\bdien tich\s+(?:tang\s+|gac\s+)?lung\s*(?:la|khoang|tam|chung|:)?\s*{_NUM_START}({NUM}){_NUM_END}"
    rf"(?:{_AREA_UNIT})?")


def parse_mezzanine_area(text: str) -> tuple[float | int | None, tuple[int, int] | None]:
    """Diện tích tầng lửng khách nói ("tầng lửng 40 m2") -> (40, vị trí trong chuỗi). Không có -> (None, None)."""
    m = _MEZZ_AREA_RE.search(fold(text))
    if not m:
        return None, None
    v = _num(m.group(1) or m.group(2))
    if not 3 <= v <= 1_000_000:
        return None, None
    return _clean_number(v), m.span()


def parse_message(text: str) -> dict[str, Any]:
    """Trích mọi slot nhận diện được bằng quy tắc. Chỉ trả khóa có giá trị (tên khớp `Slots`).

    Không trích mức hoàn thiện (quality_tier): chưa có dữ liệu đơn giá thật theo mức nên hội thoại không phân mức."""
    out: dict[str, Any] = {}
    bt = parse_building_type(text)
    if bt:
        out["building_type"] = bt
    out.update(parse_location(text))
    mezz, span = parse_mezzanine_area(text)
    dims_text = text
    if span is not None:  # diện tích lửng không phải diện tích đất/xây dựng (fold giữ nguyên độ dài so với nfc)
        t = nfc(text)
        dims_text = t[:span[0]] + " " * (span[1] - span[0]) + t[span[1]:]
    out.update(parse_dimensions(dims_text))
    out.update(parse_floors(text))
    out.update(parse_structure(text))
    if mezz is not None:
        out["mezzanine_area_m2"] = mezz
        out["has_mezzanine"] = True
    ws = parse_work_scope(text)
    if ws:
        out["work_scope"] = ws
    budget = parse_budget_info(text)
    if budget is not None:
        # "trên X tỷ": budget_vnd = None (bỏ trần cũ) + nhãn. Không có nhãn -> slot_filling bỏ nhãn cũ khi gộp.
        out["budget_vnd"] = budget[0]
        if budget[1]:
            out["budget_label"] = budget[1]
    st = parse_start_time(text)
    if st:
        out["start_time"] = st
    style = parse_design_style(text)
    if style:
        out["design_style"] = style
    level = parse_investment_level(text)
    if level:
        out["investment_level"] = level
    return out
