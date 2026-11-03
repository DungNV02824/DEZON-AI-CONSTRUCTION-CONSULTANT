"""Parse số liệu tiếng Việt từ trang Dezon / CSV (đặc tả mục 11.2).

Quy ước số Việt Nam: dấu phẩy là thập phân ("4,3"), dấu chấm là phân cách hàng nghìn ("1.500.000").
Dữ liệu nhập tay có thể dùng dấu chấm thập phân ("4.3 - 4.7tr/m2") nên parser chấp nhận cả hai:
dấu chấm chỉ được coi là phân cách hàng nghìn khi theo sau là đúng các nhóm 3 chữ số.

Nguyên tắc: không đoán. Chuỗi giữ chỗ ("Liên hệ", "Đang cập nhật"…), đơn vị không rõ, hoặc khoảng
giá trị khi cần một số duy nhất -> trả `None` (sẽ hiển thị "Chưa có dữ liệu").
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

TY = 1_000_000_000
TRIEU = 1_000_000
NGHIN = 1_000

PLACEHOLDERS = {
    "", "-", "--", "—", "–", "n/a", "na", "none", "null", "lien he", "dang cap nhat", "chua cap nhat",
    "chua co", "chua co du lieu", "thoa thuan", "dang update", "updating", "khong co", "khong ro",
    "xem chi tiet", "lien he bao gia", "...", "…",
}

_DASHES = "‒–—―−"  # ‒ – — ― −


def fold_vi(s: str | None) -> str:
    """Bỏ dấu tiếng Việt, đổi 'đ' -> 'd', viết thường. 'Biệt Thự' -> 'biet thu'."""
    if not s:
        return ""
    s = s.replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return s.lower()


def clean_text(s: str | None) -> str:
    """Chuẩn hóa khoảng trắng (kể cả NBSP), NFC."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFC", str(s)).replace("\xa0", " ").replace("​", "")
    return re.sub(r"\s+", " ", s).strip()


def is_placeholder(s: str | None) -> bool:
    """'Liên hệ', 'Đang cập nhật', '-', '' … -> True."""
    f = fold_vi(clean_text(s)).strip(" .:;")
    return f in PLACEHOLDERS or f.startswith("lien he") or f.startswith("dang cap nhat")


# ---------------------------------------------------------------- số
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
_MONEY_NUM_RE = re.compile(r"(?<![a-z\d])\d+(?:[.,]\d+)*")


def parse_decimal_vi(token: str | None) -> float | None:
    """'4,3' -> 4.3; '4.3' -> 4.3; '1.500.000' -> 1500000; '1.500' -> 1500; '2,058' -> 2.058."""
    if token is None:
        return None
    t = token.strip()
    if not t or not re.fullmatch(r"\d+(?:[.,]\d+)*", t):
        return None
    has_dot, has_comma = "." in t, "," in t
    if has_dot and has_comma:
        # dấu xuất hiện cuối cùng là thập phân
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif has_comma:
        parts = t.split(",")
        t = "".join(parts) if len(parts) > 2 else t.replace(",", ".")  # 1,234,567 hiếm gặp
    elif has_dot:
        parts = t.split(".")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3):
            t = "".join(parts)  # hàng nghìn
    try:
        return float(t)
    except ValueError:
        return None


_UNIT_PATTERNS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"^(ty|ti)(?![a-z])"), TY),
    (re.compile(r"^(trieu|tr)(?![a-z])"), TRIEU),
    (re.compile(r"^(nghin|ngan|k)(?![a-z])"), NGHIN),
    (re.compile(r"^(dong|vnd|d)(?![a-z])"), 1),
]


def _unit_at(folded_tail: str) -> int | None:
    """Đơn vị tiền ngay sau số (chuỗi đã fold)."""
    tail = folded_tail.lstrip()
    for pat, mult in _UNIT_PATTERNS:
        if pat.search(tail):
            return mult
    return None


_SHORTHAND_RE = re.compile(r"\s*(?:trieu|tr|ty|ti)(\d{1,3})(?![\d.,])")


def _normalize_numeric_text(s: str) -> str:
    s = clean_text(s)
    for d in _DASHES:
        s = s.replace(d, "-")
    return s


_RANGE_LINK_RE = re.compile(r"^[\s:]*(?:-|~|den|toi)[\s:]*$")


def _money_tokens(s: str) -> list[tuple[float, int | None, bool]]:
    """Danh sách (số, hệ số đơn vị|None, nối-khoảng-với-số-sau) theo thứ tự xuất hiện.

    Gộp '1 tỷ 200 triệu' -> 1,2 tỷ. Cờ thứ ba = True khi giữa số này và số kế tiếp chỉ có dấu nối khoảng
    ('-', '~', 'đến', 'tới'), tức hai số tạo thành một khoảng giá.
    """
    folded = fold_vi(_normalize_numeric_text(s))
    raw: list[tuple[float, int | None, int, int]] = []  # (giá trị, đơn vị, start, end)
    for m in _MONEY_NUM_RE.finditer(folded):  # bỏ số dính sau chữ ('m2')
        val = parse_decimal_vi(m.group())
        if val is not None:
            unit = _unit_at(folded[m.end():])
            end = m.end()
            # Viết tắt "3tr5" = 3,5 triệu; "1ty2" = 1,2 tỷ: chữ số dính liền sau đơn vị là phần thập phân.
            short = _SHORTHAND_RE.match(folded, m.end())
            if short and unit in (TY, TRIEU):
                digits = short.group(1)
                val += int(digits) / 10 ** len(digits)
                end = short.end()
            raw.append((val, unit, m.start(), end))
    merged: list[tuple[float, int | None, int, int]] = []
    i = 0
    while i < len(raw):
        v, u, st, e = raw[i]
        if u == TY and i + 1 < len(raw) and raw[i + 1][1] == TRIEU:
            between = folded[e:raw[i + 1][2]]
            if "-" not in between and "den" not in between.split():
                merged.append((v * TY + raw[i + 1][0] * TRIEU, 1, st, raw[i + 1][3]))
                i += 2
                continue
        merged.append((v, u, st, e))
        i += 1
    out: list[tuple[float, int | None, bool]] = []
    for j, (v, u, _st, e) in enumerate(merged):
        linked = False
        if j + 1 < len(merged):
            between = folded[e:merged[j + 1][2]]
            between = re.sub(r"^\s*(?:trieu|tr|ty|ti|nghin|ngan|k|dong|vnd|d)(?![a-z])", "", between)
            linked = bool(_RANGE_LINK_RE.match(between))
        out.append((v, u, linked))
    return out


def parse_money_range(s: str | None, default_unit: int | None = None) -> list[int] | None:
    """Khoảng tiền VND.

    '4,3–4,7 triệu/m²' -> [4300000, 4700000]; 'Từ 4,3 đến 4,7 tr/m²' -> [4300000, 4700000];
    '4.3 - 4.7tr/m2' -> [4300000, 4700000]; '25 tỷ/công trình' -> [25e9, 25e9];
    '850 triệu – 1,2 tỷ' -> [850e6, 1.2e9]; '3tr5/m²' -> [3500000, 3500000]. Không có đơn vị và không có
    `default_unit` -> None (trừ khi số đã ở dạng đồng, >= 100.000). Hai số không nối bằng dấu khoảng
    ('4,3 triệu xây thô, 7 triệu hoàn thiện') -> chỉ lấy số đầu, không ghép thành khoảng.
    """
    if s is None or is_placeholder(s):
        return None
    tokens = _money_tokens(s)
    if not tokens:
        return None
    if len(tokens) >= 2 and tokens[0][2]:
        pair = [(tokens[0][0], tokens[0][1]), (tokens[1][0], tokens[1][1])]
        # đơn vị của số sau áp cho số trước nếu số trước thiếu đơn vị ('4,3–4,7 triệu')
        if pair[0][1] is None and pair[1][1] is not None:
            pair[0] = (pair[0][0], pair[1][1])
    else:
        pair = [(tokens[0][0], tokens[0][1])]
    values: list[int] = []
    for v, u in pair:
        unit = u or default_unit
        if unit is None:
            if v >= 100_000:
                unit = 1
            else:
                return None
        values.append(int(round(v * unit)))
    lo, hi = (values[0], values[0]) if len(values) == 1 else (values[0], values[1])
    if lo > hi:
        lo, hi = hi, lo
    if hi <= 0:  # "0 đ" = chưa khai báo, không phải giá 0 đồng
        return None
    return [lo, hi]


def parse_money(s: str | None, default_unit: int | None = None, allow_range: bool = False) -> int | None:
    """Một số tiền duy nhất: '25 tỷ/công trình' -> 25000000000.

    Chuỗi là khoảng ('7 – 15 tỷ') -> None, vì không được tự chọn một giá trị đại diện
    (trừ khi `allow_range=True` thì trả cận dưới).
    """
    r = parse_money_range(s, default_unit)
    if r is None:
        return None
    if r[0] != r[1] and not allow_range:
        return None
    return r[0]


def parse_price_per_m2_range(s: str | None) -> list[int] | None:
    """Đơn giá VND/m². Chấp nhận 'triệu/m²', 'tr/m2', 'đ/m²'. Không đơn vị và số nhỏ -> None."""
    return parse_money_range(s)


# Đơn giá/m² ngoài khoảng này gần như chắc là gõ nhầm trên hồ sơ (vd "2.000.000 – 80.000.000").
PRICE_PER_M2_MIN = 500_000
PRICE_PER_M2_MAX = 40_000_000
PRICE_RANGE_MAX_RATIO = 10


def plausible_price_per_m2_range(rng: list[int] | tuple[int, int] | None) -> bool:
    """Khoảng đơn giá/m² có hợp lý để dùng cho khái toán & matching không (xem docs/DECISIONS.md)."""
    if not rng:
        return False
    lo, hi = min(rng), max(rng)
    return PRICE_PER_M2_MIN <= lo and hi <= PRICE_PER_M2_MAX and hi <= lo * PRICE_RANGE_MAX_RATIO


# Form hồ sơ nhà thầu trên dezon.vn để sẵn cận dưới 2 tr/m²: ~1/2 hồ sơ ghi "Từ 2 đến X tr/m²" (kể cả xây thô).
FORM_DEFAULT_MIN_PRICE = 2_000_000


# Sàn hợp lý theo loại đơn giá trên hồ sơ (thận trọng, dưới mức thấp nhất thực tế trên dezon.vn 02/11/2026:
# xây thô 3,8 tr/m², "hoàn thiện cơ bản" = trọn gói 6 tr/m²). Thấp hơn sàn -> gần như chắc là gõ nhầm/đơn giá nhân
# công ("Từ 0,5 đến 4,2 tr/m²" xây thô; "Từ 0,7 đến 2 tr/m²" hoàn thiện cơ bản). Xem docs/DECISIONS.md.
PRICE_KIND_ROUGH = "rough_price"
PRICE_KIND_FINISH = "finish_price"
PRICE_PER_M2_FLOOR_BY_KIND = {
    PRICE_KIND_ROUGH: 2_500_000,
    PRICE_KIND_FINISH: 4_000_000,
}


def clean_price_per_m2_range(rng: list[int] | None, kind: str | None = None
                             ) -> tuple[list[int | None] | None, str | None]:
    """Khoảng đơn giá/m² trên hồ sơ -> ([min, max] dùng được, lý do loại/sửa).

    `kind` = "rough_price" (xây thô) | "finish_price" ("hoàn thiện cơ bản" = trọn gói) | None (chỉ kiểm tra chung).
    - cận dưới = mặc định của form (2 tr) và cận trên lớn hơn -> bỏ cận dưới: `[None, max]`, lý do "default_min";
    - cận dưới thấp hơn sàn của `kind` và cận trên đạt sàn -> bỏ cận dưới: `[None, max]`, lý do "implausible_min";
    - khoảng (sau các bước trên) vô lý, hoặc cận trên dưới sàn -> `None`, lý do "implausible"."""
    if not rng:
        return None, None
    lo: int | None = min(rng)
    hi = max(rng)
    reason = None
    if lo == FORM_DEFAULT_MIN_PRICE and hi > lo:
        lo, reason = None, "default_min"
    floor = PRICE_PER_M2_FLOOR_BY_KIND.get(kind or "")
    if floor is not None:
        if hi < floor:
            return None, "implausible"
        if lo is not None and lo < floor:
            lo, reason = None, "implausible_min"
    if not plausible_price_per_m2_range([v for v in (lo, hi) if v is not None]):
        return None, "implausible"
    return [lo, hi], reason


def parse_int(s: str | None) -> int | None:
    """Số nguyên đầu tiên: '8 năm' -> 8; '7 công trình' -> 7; '1.200 m²' -> 1200."""
    if s is None or is_placeholder(s):
        return None
    m = _NUM_RE.search(_normalize_numeric_text(s))
    if not m:
        return None
    v = parse_decimal_vi(m.group())
    if v is None or v != int(v):
        return None
    return int(v)


def parse_count(s: str | None) -> int | None:
    """'7 công trình', '7 dự án trên Dezon' -> 7. Khoảng ('5-7') -> None."""
    if s is None or is_placeholder(s):
        return None
    norm = _normalize_numeric_text(s)
    if re.search(r"\d\s*-\s*\d", norm):
        return None
    return parse_int(norm)


def parse_years(s: str | None) -> int | None:
    """Số năm kinh nghiệm: '8 năm', '8 năm kinh nghiệm', 'Hơn 10 năm' -> 8/8/10. '2018' (năm) -> None."""
    if s is None or is_placeholder(s):
        return None
    folded = fold_vi(_normalize_numeric_text(s))
    m = re.search(r"(\d{1,3})\s*(\+)?\s*nam\b", folded)
    if m:
        return int(m.group(1))
    v = parse_int(s)
    if v is not None and v < 200 and re.fullmatch(r"\s*\d{1,3}\s*", folded):
        return v
    return None


def parse_year(s: str | None) -> int | None:
    """Năm dương lịch 4 chữ số: 'Năm thành lập: 2018' -> 2018; 'thành lập năm 2004' -> 2004."""
    if s is None or is_placeholder(s):
        return None
    m = re.search(r"(?<!\d)(1[89]\d{2}|20\d{2}|2100)(?!\d)", clean_text(s))
    return int(m.group(1)) if m else None


def parse_staff(s: str | None) -> tuple[int | None, int | None]:
    """Nhân sự (thợ, văn phòng): '20 thợ, 12 văn phòng' / '20 thợ · 12 văn phòng' -> (20, 12).

    Phần có nhãn 'thợ'/'công nhân'/'thi công' -> thợ; 'văn phòng' -> văn phòng.
    Phần không nhãn: vị trí 1 = thợ, vị trí 2 = văn phòng (định dạng hồ sơ Dezon).
    Phần là khoảng ('100 - 300 người') -> None cho phần đó.
    """
    if s is None or is_placeholder(s):
        return (None, None)
    norm = _normalize_numeric_text(s)
    parts = [p.strip() for p in re.split(r"[·,;|/]|\bvà\b", norm) if p.strip()]
    workers: int | None = None
    office: int | None = None
    for idx, part in enumerate(parts):
        f = fold_vi(part)
        is_range = bool(re.search(r"\d\s*-\s*\d|\bden\b", f))
        nums = [parse_decimal_vi(x) for x in _NUM_RE.findall(f)]
        nums = [int(n) for n in nums if n is not None and n == int(n)]
        if not nums:
            continue
        value = None if is_range else nums[0]
        if "van phong" in f or "vp" in f.split():
            office = value
        elif any(k in f for k in ("tho", "cong nhan", "thi cong", "nhan cong")):
            workers = value
        elif idx == 0 and workers is None:
            workers = value
        elif idx == 1 and office is None:
            office = value
    return (workers, office)


def parse_area_m2(s: str | None) -> float | None:
    """'600m2', '1.200 m²', '85,5 m2' -> số m². Khoảng -> None."""
    if s is None or is_placeholder(s):
        return None
    norm = _normalize_numeric_text(s)
    if re.search(r"\d\s*-\s*\d", norm):
        return None
    m = _NUM_RE.search(norm)
    return parse_decimal_vi(m.group()) if m else None


def parse_date_vi(s: str | None) -> date | None:
    """'22/10/2026' hoặc '2026-10-22' -> date."""
    if s is None or is_placeholder(s):
        return None
    t = clean_text(s)
    m = re.search(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", t)
    try:
        if m:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return None


# ---------------------------------------------------------------- danh mục
HCM = "TP. Hồ Chí Minh"
HN = "Hà Nội"

PROVINCES = [
    HCM, HN, "Đà Nẵng", "Hải Phòng", "Cần Thơ", "An Giang", "Bà Rịa - Vũng Tàu", "Bắc Giang", "Bắc Kạn",
    "Bạc Liêu", "Bắc Ninh", "Bến Tre", "Bình Định", "Bình Dương", "Bình Phước", "Bình Thuận", "Cà Mau",
    "Cao Bằng", "Đắk Lắk", "Đắk Nông", "Điện Biên", "Đồng Nai", "Đồng Tháp", "Gia Lai", "Hà Giang",
    "Hà Nam", "Hà Tĩnh", "Hải Dương", "Hậu Giang", "Hòa Bình", "Hưng Yên", "Khánh Hòa", "Kiên Giang",
    "Kon Tum", "Lai Châu", "Lâm Đồng", "Lạng Sơn", "Lào Cai", "Long An", "Nam Định", "Nghệ An",
    "Ninh Bình", "Ninh Thuận", "Phú Thọ", "Phú Yên", "Quảng Bình", "Quảng Nam", "Quảng Ngãi",
    "Quảng Ninh", "Quảng Trị", "Sóc Trăng", "Sơn La", "Tây Ninh", "Thái Bình", "Thái Nguyên",
    "Thanh Hóa", "Thừa Thiên Huế", "Tiền Giang", "Trà Vinh", "Tuyên Quang", "Vĩnh Long", "Vĩnh Phúc",
    "Yên Bái",
]
_PROVINCE_ALIASES: list[tuple[str, str]] = [
    # (mẫu đã fold, tên chuẩn) — kiểm tra theo thứ tự
    (r"\bhcm\b|tphcm|tp\.?\s*hcm|ho chi minh|sai\s*gon|\bsg\b|thu duc", HCM),
    (r"\bha\s*noi\b|\bhn\b", HN),
    (r"\bvung tau\b|\bba ria\b|\bbr-?vt\b", "Bà Rịa - Vũng Tàu"),
    (r"\bhue\b", "Thừa Thiên Huế"),
    (r"\bda lat\b", "Lâm Đồng"),
    (r"\bnha trang\b", "Khánh Hòa"),
    (r"\bphu quoc\b", "Kiên Giang"),
    (r"\bbien hoa\b", "Đồng Nai"),
]
# Quận/huyện nội thành TP.HCM thường gặp trong hồ sơ ("Quận 7", "Q9", "Tân Bình").
_HCM_DISTRICT_RE = re.compile(
    r"\b(quan|q)\s*\.?\s*(1[0-2]|[1-9])\b|\b(tan binh|binh thanh|go vap|phu nhuan|tan phu|binh tan|"
    r"nha be|binh chanh|hoc mon|cu chi|can gio)\b"
)
_HN_DISTRICT_RE = re.compile(
    r"\b(long bien|dong anh|cau giay|hoan kiem|ba dinh|dong da|hai ba trung|thanh xuan|hoang mai|"
    r"tay ho|nam tu liem|bac tu liem|ha dong|gia lam|soc son)\b"
)


def normalize_province(s: str | None) -> str | None:
    """Chuẩn hóa tên tỉnh/thành: 'TP. HCM', 'TP Hồ Chí Minh', 'Quận 7 TPHCM', 'HCM' -> 'TP. Hồ Chí Minh'.

    Không nhận ra tỉnh nào (vd 'không có văn phòng') -> None.
    """
    if s is None or is_placeholder(s):
        return None
    f = fold_vi(clean_text(s))
    f_clean = re.sub(r"https?://\S+", " ", f)
    for prov in PROVINCES:  # tên tỉnh đầy đủ trước (tránh 'Hà Nam' khớp nhầm)
        pf = fold_vi(prov)
        if prov not in (HCM, HN) and re.search(rf"\b{re.escape(pf)}\b", f_clean):
            return prov
    for pat, canon in _PROVINCE_ALIASES:
        if re.search(pat, f_clean):
            return canon
    if _HCM_DISTRICT_RE.search(f_clean):
        return HCM
    if _HN_DISTRICT_RE.search(f_clean):
        return HN
    return None


def split_list(s: str | None) -> list[str]:
    """'Nhà phố · Biệt thự · Căn hộ' / 'Nhà phố, biệt thự' -> ['Nhà phố', 'Biệt thự', 'Căn hộ']."""
    if s is None or is_placeholder(s):
        return []
    return [p.strip() for p in re.split(r"[·,;|•/]", clean_text(s)) if p.strip() and not is_placeholder(p)]


_BUILDING_TYPE_KEYWORDS: list[tuple[str, str]] = [
    (r"biet thu|villa|dinh thu|lau dai", "biet_thu"),
    (r"can ho|chung cu|penthouse|duplex|apartment|condo|studio apartment", "can_ho"),
    (r"khach san|hotel|resort|nghi duong|homestay", "khach_san"),
    (r"van phong|office|thuong mai|toa nha", "van_phong"),
    (r"nha pho|nha o|townhouse|nha ong|nha lien ke|shophouse|\bhouse\b|\bnha\b", "nha_pho"),
    (r"cong trinh khac|du an|nha xuong|cong nghiep|khac", "khac"),
]


def map_building_type(s: str | None) -> str | None:
    """Một nhãn -> enum building_type. 'Biệt thự' -> 'biet_thu'; 'Căn hộ dịch vụ' -> 'can_ho'."""
    if not s:
        return None
    f = fold_vi(clean_text(s))
    for pat, val in _BUILDING_TYPE_KEYWORDS:
        if re.search(pat, f):
            return val
    return None


def parse_building_types(s: str | None | list[str]) -> list[str]:
    """'Nhà phố · Biệt thự · Căn hộ · Công trình khác' -> ['nha_pho', 'biet_thu', 'can_ho', 'khac']."""
    items = s if isinstance(s, list) else split_list(s)
    out: list[str] = []
    for item in items:
        bt = map_building_type(item)
        if bt and bt not in out:
            out.append(bt)
    return out


def infer_building_type_from_name(name: str | None) -> str | None:
    """Suy loại công trình từ TÊN dự án khi trang không ghi rõ ('Indochine Villa Quận 7' -> biet_thu).

    Chỉ dùng từ khóa rõ ràng; tên chung chung ('The Opera') -> None. 'Công trình khác' không suy.
    """
    bt = map_building_type(name)
    return None if bt == "khac" else bt
