"""Output guardrail validator (đặc tả mục 2.1, 9.1, 13).

Chạy sau mỗi câu trả lời của LLM:

1. `extract_claims(answer)` trích mọi số liệu có đơn vị (tiền, đơn giá, số công trình, số năm, nhân sự,
   diện tích, số nhà thầu, điểm, phần trăm, năm thành lập) bằng regex.
2. `collect_allowed(tool_results, user_texts)` gom tập số được phép: mọi số trong JSON kết quả tool
   (kể cả số parse từ chuỗi `display`), độ dài các danh sách, và số khách tự nói.
3. `check(...)` đối chiếu (cho phép sai lệch ±1%, các định dạng `4,3`/`4.3`, `2,058 tỷ`/`2 tỷ 58 triệu`)
   và kiểm tra cụm cấm: "tốt nhất", gọi số dự án là "đã hoàn thành", coi khái toán là báo giá.
4. `run_guarded(...)` thực hiện vòng sinh lại: sai lần 1 -> gửi thông báo lỗi cho LLM sinh lại một lần;
   sai lần 2 -> thay bằng câu trả lời dự phòng dựng từ dữ liệu tool (`app.ai.fallback`).
5. `record_violations(...)` ghi bảng `guardrail_violations`.
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

TOLERANCE = 0.01  # ±1% (mục 2.1)

# --------------------------------------------------------------------------------------------------
# Loại claim
# --------------------------------------------------------------------------------------------------
MONEY = "money"  # VND
UNIT_PRICE = "unit_price"  # VND/m²
PROJECT_COUNT = "project_count"
CONTRACTOR_COUNT = "contractor_count"
STAFF = "staff"
YEARS = "years"  # số năm (kinh nghiệm, bảo hành...)
YEAR = "year"  # năm dương lịch (thành lập năm 2018)
AREA = "area"  # m²
SCORE = "score"
PERCENT = "percent"
NUMBER = "number"  # số lớn có phân cách hàng nghìn nhưng không có đơn vị (vd 2.058.000.000)

KIND_VI: dict[str, str] = {
    MONEY: "số tiền", UNIT_PRICE: "đơn giá", PROJECT_COUNT: "số công trình", CONTRACTOR_COUNT: "số nhà thầu",
    STAFF: "số nhân sự", YEARS: "số năm", YEAR: "năm", AREA: "diện tích", SCORE: "điểm số",
    PERCENT: "phần trăm", NUMBER: "con số",
}

RULE_UNTRACEABLE = "untraceable_number"
RULE_BEST = "best_claim"
RULE_PROJECT_LABEL = "project_count_label"
RULE_ESTIMATE_QUOTE = "estimate_as_quote"


class Claim(BaseModel):
    kind: str
    values: list[float]  # các cách hiểu có thể (vd "2.058 tỷ" -> [2.058e9, 2.058e12]); đã quy về VND / số
    text: str  # đoạn văn bản gốc
    span: tuple[int, int]

    @property
    def value(self) -> float:
        return self.values[0]


class Violation(BaseModel):
    rule: str
    text: str
    detail: str


class ValidationResult(BaseModel):
    ok: bool
    violations: list[Violation] = []
    claims: list[Claim] = []


# --------------------------------------------------------------------------------------------------
# Regex
# --------------------------------------------------------------------------------------------------
_F = re.IGNORECASE | re.UNICODE

NUM = r"\d+(?:[.,]\d+)*"
_NUM_RE = re.compile(rf"(?<![\w.,]){NUM}", _F)
_NUM_ANY_RE = re.compile(rf"(?<![\w]){NUM}", _F)

_W = r"(?!\w)"  # kết thúc từ (Unicode)

# Đơn vị đếm (có dấu + không dấu) — dùng chung cho regex đơn vị và cho phân số "2/3 nhà thầu".
_U_PROJECT = r"(?:công\s+trình|dự\s+án|cong\s+trinh|du\s+an)"
_U_CONTRACTOR = r"(?:nhà\s+thầu|đơn\s+vị|hồ\s+sơ|nha\s+thau|don\s+vi|ho\s+so)"
_U_STAFF = (r"(?:thợ|kỹ\s+sư|kĩ\s+sư|kiến\s+trúc\s+sư|nhân\s+viên|nhân\s+sự|công\s+nhân|người|văn\s+phòng|cán\s+bộ"
            r"|tho|ky\s+su|nhan\s+vien|nhan\s+su|cong\s+nhan|nguoi|van\s+phong|can\s+bo)")

# Che các đoạn chứa chữ số nhưng không phải số liệu: URL, email, ngày tháng, giờ.
# "2/3 nhà thầu", "0/3 hồ sơ" là phân số số đếm, KHÔNG che (để đối chiếu cả tử số và mẫu số).
_MASK_RES = [
    re.compile(r"https?://\S+|www\.\S+", _F),
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+", _F),
    re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}(?:[T ][\d:.+Z-]+)?\b"),
    re.compile(r"(?<![\d/])\d{1,2}/\d{1,2}/\d{2,4}(?![\d/])"),
    re.compile(rf"(?<![\d/])\d{{1,2}}/\d{{1,2}}(?![\d/])(?!\s*(?:{_U_PROJECT}|{_U_CONTRACTOR}|{_U_STAFF}){_W})", _F),
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b"),
]
_ISO_YEAR_RE = re.compile(r"\b(\d{4})-\d{1,2}-\d{1,2}")
_DMY_YEAR_RE = re.compile(r"\b\d{1,2}/\d{1,2}/(\d{4})\b")

_MONEY_UNIT_RE = re.compile(
    rf"\s*(?P<u>tỷ|tỉ|ty{_W}|triệu|trieu|tr{_W}|nghìn|ngàn|nghin|ngan{_W}|đồng|dong{_W}|vnđ|vnd|đ{_W})"
    rf"(?:\s*(?:đồng|dong|vnđ|vnd|đ){_W})?", _F)
_SMALLER_UNIT = {"tỷ": ("triệu", "nghìn"), "triệu": ("nghìn",)}
_PER_M2_RE = re.compile(
    rf"\s*(?:/|trên|tren|mỗi|moi|một|mot|cho\s+mỗi)\s*(?:m²|m2{_W}|mét\s+vuông|met\s+vuong|m{_W})", _F)
_COUNT_UNIT_RES: list[tuple[str, re.Pattern[str]]] = [
    (PROJECT_COUNT, re.compile(rf"\s*{_U_PROJECT}{_W}", _F)),
    (CONTRACTOR_COUNT, re.compile(rf"\s*{_U_CONTRACTOR}{_W}", _F)),
    (STAFF, re.compile(rf"\s*{_U_STAFF}{_W}", _F)),
    # "nam" không dấu chỉ nhận chữ thường (tránh "Nam" trong địa danh).
    (YEARS, re.compile(rf"\s*(?:năm|(?-i:nam)){_W}", _F)),
    (SCORE, re.compile(rf"\s*(?:/\s*100(?!\d)(?:\s*(?:điểm|diem){_W})?|(?:điểm|diem){_W})", _F)),
    (PERCENT, re.compile(rf"\s*(?:%|(?:phần|phan)\s+(?:trăm|tram){_W})", _F)),
    (AREA, re.compile(rf"\s*(?:m²|m2{_W}|(?:mét|met)\s+(?:vuông|vuong){_W})", _F)),
]
_FRACTION_RE = re.compile(r"\s*/\s*(?P<n>\d+)(?![\d.,])")
_RANGE_SEP_RE = re.compile(rf"\s*(?:–|—|-|~|đến{_W}|den{_W}|tới{_W}|toi{_W}|->)\s*(?P<n>{NUM})", _F)
# Đuôi số không được coi là phần lẻ của "1 tỷ 8" nếu theo sau là các đơn vị này.
_TAIL_BLOCK_RE = re.compile(
    rf"\s*(?:[.,]\d|\d|%|/|m²|m2{_W}|m{_W}|x{_W}|tầng|lầu|trệt|phòng|căn|tháng|ngày|tuần|năm|công\s+trình|"
    rf"dự\s+án|nhà\s+thầu|đơn\s+vị|thợ|người|nhân|kỹ|kĩ|văn\s+phòng|điểm|phần\s+trăm|tỷ|tỉ|triệu|nghìn|ngàn|"
    rf"tang|lau|tret|phong|can{_W}|thang|ngay|tuan|nam{_W}|cong|du{_W}|nha{_W}|don{_W}|tho{_W}|nguoi|diem|"
    rf"ty{_W}|trieu|nghin|ngan{_W})", _F)
# Số viết bằng chữ trước đơn vị đếm ("mười năm kinh nghiệm", "chín công trình"). Không gồm "một" (mạo từ:
# "một đơn vị thi công") và "tư" ("đầu tư công trình"). Số ghép ("mười lăm", "hai mươi") không hỗ trợ.
_WORD_NUM = {"hai": 2, "ba": 3, "bốn": 4, "năm": 5, "sáu": 6, "bảy": 7, "bẩy": 7, "tám": 8, "chín": 9, "mười": 10}
_WORD_NUM_RE = re.compile(rf"(?<!\w)(?P<w>{'|'.join(_WORD_NUM)})(?=\s)", _F)
# Không gồm số nhà thầu: "hai đơn vị này", "cả ba nhà thầu" là cách nói tham chiếu tự nhiên.
_WORD_KINDS = (PROJECT_COUNT, STAFF, YEARS)
_YEAR_PREFIX_RE = re.compile(r"(?:thành\s+lập(?:\s+(?:từ\s+)?năm)?|từ\s+năm)\s*:?\s*$", _F)
_DIM_RE = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*m?\s*[x×*]\s*(\d+(?:[.,]\d+)?)", _F)

_UNIT_MULT = {"tỷ": 1e9, "tỉ": 1e9, "triệu": 1e6, "tr": 1e6, "nghìn": 1e3, "ngàn": 1e3,
              "đồng": 1.0, "vnđ": 1.0, "vnd": 1.0, "đ": 1.0}


_UNIT_ALIAS = {"tỉ": "tỷ", "ty": "tỷ", "tr": "triệu", "trieu": "triệu", "ngàn": "nghìn", "nghin": "nghìn",
               "ngan": "nghìn", "dong": "đồng"}


def _norm_unit(u: str) -> str:
    u = u.lower()
    return _UNIT_ALIAS.get(u, u)


def parse_number(s: str) -> list[float]:
    """Parse một số viết kiểu VN hoặc kiểu Anh, trả về mọi cách hiểu hợp lệ.

    "4,3" / "4.3" -> [4.3]; "2.058" / "2,058" -> [2058, 2.058] (mơ hồ); "1.234.567" -> [1234567];
    "1.234,5" -> [1234.5]; "100" -> [100].
    """
    s = s.strip()
    if not s:
        return []
    has_dot, has_comma = "." in s, "," in s
    if not has_dot and not has_comma:
        return [float(s)]
    if has_dot and has_comma:
        dec = "." if s.rfind(".") > s.rfind(",") else ","
        thou = "," if dec == "." else "."
        int_part, _, frac = s.rpartition(dec)
        groups = int_part.split(thou)
        if any(len(g) != 3 for g in groups[1:]):
            return []
        return [float("".join(groups) + "." + frac)]
    sep = "." if has_dot else ","
    parts = s.split(sep)
    if len(parts) > 2:
        if all(len(p) == 3 for p in parts[1:]):
            return [float("".join(parts))]
        return []
    whole, frac = parts
    decimal_val = float(f"{whole}.{frac}")
    if len(frac) == 3:
        return [float(whole + frac), decimal_val]
    return [decimal_val]


def _mask(text: str) -> str:
    out = text
    for rx in _MASK_RES:
        out = rx.sub(lambda m: " " * len(m.group(0)), out)
    return out


def _tail_fraction(digits: str) -> float:
    return int(digits) / (10 ** len(digits))


def _money_claim(text: str, start: int, end: int, nums: list[float]) -> tuple[list[float], int, bool] | None:
    """Nếu ngay sau số là đơn vị tiền: trả (giá_trị_VND, vị trí kết thúc, là_đơn_giá)."""
    m = _MONEY_UNIT_RE.match(text, end)
    if not m:
        return None
    unit = _norm_unit(m.group("u"))
    mult = _UNIT_MULT[unit]
    values = [n * mult for n in nums]
    pos = m.end()
    # "2 tỷ 58 triệu", "1 tỷ 800 triệu"
    smaller = _SMALLER_UNIT.get(unit, ())
    m2 = re.compile(rf"[ \t]+(?P<n>{NUM})", _F).match(text, pos)
    consumed = False
    if smaller and m2:
        mu = _MONEY_UNIT_RE.match(text, m2.end())
        if mu and _norm_unit(mu.group("u")) in {_norm_unit(x) for x in smaller}:
            sub_mult = _UNIT_MULT[_norm_unit(mu.group("u"))]
            subs = parse_number(m2.group("n"))
            values = [v + s * sub_mult for v in values for s in subs]
            pos = mu.end()
            consumed = True
    if not consumed and unit in ("tỷ", "triệu"):
        # "1 tỷ 8" = 1,8 tỷ; "4 triệu 3" = 4,3 triệu; "1 tỷ rưỡi"
        mt = re.compile(r"[ \t]+(?P<d>\d{1,3})(?![\d.,])", _F).match(text, pos)
        if mt and not _TAIL_BLOCK_RE.match(text, mt.end()):
            frac = _tail_fraction(mt.group("d"))
            values = [v + frac * mult for v in values]
            pos = mt.end()
        else:
            mr = re.compile(rf"[ \t]+rưỡi{_W}", _F).match(text, pos)
            if mr:
                values = [v + 0.5 * mult for v in values]
                pos = mr.end()
    per = _PER_M2_RE.match(text, pos)
    if per:
        return values, per.end(), True
    return values, pos, False


def _classify(text: str, start: int, end: int, nums: list[float]) -> tuple[str, list[float], int] | None:
    """Xác định loại claim cho số tại [start, end) dựa trên đơn vị theo sau."""
    money = _money_claim(text, start, end, nums)
    if money:
        values, pos, per_m2 = money
        return (UNIT_PRICE if per_m2 else MONEY), values, pos
    for kind, rx in _COUNT_UNIT_RES:
        m = rx.match(text, end)
        if m:
            if kind == YEARS and re.match(r"\s*\d{4}(?!\d)", text[m.end():]):
                return None  # "tháng 6 năm 2027" là ngày tháng, không phải "6 năm"
            return kind, nums, m.end()
    # Phân số số đếm "2/3 nhà thầu": tử số mang loại của mẫu số; mẫu số được trích thành claim riêng.
    fr = _FRACTION_RE.match(text, end)
    if fr:
        inner = _classify(text, fr.start("n"), fr.end("n"), [1.0])
        if inner is not None and inner[0] in (PROJECT_COUNT, CONTRACTOR_COUNT, STAFF):
            return inner[0], nums, end
    return None


def extract_claims(text: str) -> list[Claim]:
    """Trích mọi số liệu có đơn vị trong `text`.

    Không trích số đứng một mình (số thứ tự "1.", "bước 2", "tầng 1", "Quận 7", "4 tầng") để tránh
    báo nhầm; ngoại lệ: số lớn có phân cách hàng nghìn (vd "2.058.000.000") -> kind "number".
    Nhận đơn vị không dấu ("5,2 trieu/m2", "15 cong trinh"), phân số số đếm ("2/3 nhà thầu") và số bằng chữ
    hai..mười trước công trình/dự án/nhân sự/năm ("mười năm kinh nghiệm").
    """
    if not text:
        return []
    masked = _mask(text)
    claims: list[Claim] = []
    consumed_to = 0  # bỏ qua số đã nằm trong claim trước ("2 tỷ 58 triệu", "88/100 điểm")
    for m in _NUM_RE.finditer(masked):
        start, end = m.span()
        if start < consumed_to:
            continue
        raw = m.group(0)
        nums = parse_number(raw)
        if not nums:
            continue
        cls = _classify(masked, start, end, nums)
        text_stop = cls[2] if cls else end
        if cls is None:
            # Khoảng "2,058–2,352 tỷ", "từ 4,3 đến 4,7 triệu/m²": số đầu thừa hưởng đơn vị của số sau.
            r = _RANGE_SEP_RE.match(masked, end)
            if r:
                one = _classify(masked, r.start("n"), r.end("n"), [1.0])
                two = _classify(masked, r.start("n"), r.end("n"), [2.0])
                if one is not None and two is not None:
                    factor = two[1][0] - one[1][0]  # hệ số đơn vị (bỏ phần đuôi cộng thêm)
                    cls = (one[0], [n * factor for n in nums], end)
                    text_stop = one[2]
        if cls is None:
            # Năm thành lập: "thành lập năm 2018", "Năm thành lập: 2018", "hoạt động từ năm 2018"
            if re.fullmatch(r"\d{4}", raw) and 1900 <= int(raw) <= 2100 and _YEAR_PREFIX_RE.search(
                    masked[max(0, start - 30):start]):
                cls = (YEAR, nums, end)
                text_stop = end
            elif raw.count(".") + raw.count(",") >= 2 and max(nums) >= 1_000_000:
                cls = (NUMBER, nums, end)
                text_stop = end
        if cls is None:
            continue
        kind, values, stop = cls
        consumed_to = stop
        claims.append(Claim(kind=kind, values=values, text=text[start:text_stop].strip(), span=(start, text_stop)))
    # Số viết bằng chữ trước đơn vị đếm.
    for m in _WORD_NUM_RE.finditer(masked):
        start, end = m.span()
        if any(c.span[0] <= start < c.span[1] for c in claims):
            continue
        val = float(_WORD_NUM[m.group("w").lower()])
        cls = _classify(masked, start, end, [val])
        if cls is None or cls[0] not in _WORD_KINDS:
            continue
        claims.append(Claim(kind=cls[0], values=[val], text=text[start:cls[2]].strip(), span=(start, cls[2])))
    claims.sort(key=lambda c: c.span[0])
    return claims


# --------------------------------------------------------------------------------------------------
# Tập giá trị được phép
# --------------------------------------------------------------------------------------------------
# Khóa JSON là số nội bộ, không phải số liệu hiển thị (không đưa vào tập chung).
_NOISE_KEY_RE = re.compile(r"(^id$|_id$|^ids$|rank|version|^limit$|chunk_index|sort_order)", re.I)
_SCORE_KEY_RE = re.compile(r"(score|weight|coefficient|completeness|similarity|^k$)", re.I)
_SKIP_STR_KEYS = frozenset({"url", "profile_url", "project_url", "avatar_url", "slug", "code"})
_LIST_KIND: dict[str, str] = {
    "projects": PROJECT_COUNT, "results": CONTRACTOR_COUNT, "contractors": CONTRACTOR_COUNT,
    "contractor_ids": CONTRACTOR_COUNT, "ids": CONTRACTOR_COUNT,
}
# Suy ra loại số liệu từ đường dẫn khóa JSON (vd results.contractor.evidence.staff.value.workers -> staff).
_PATH_KIND: list[tuple[str, re.Pattern[str]]] = [
    (PROJECT_COUNT, re.compile(r"project_count|projects_count|total_projects|total_dezon_projects", re.I)),
    (CONTRACTOR_COUNT, re.compile(
        r"total_candidates|contractor_count|contractors_with_projects|with_warranty_info|verified_profiles", re.I)),
    (PERCENT, re.compile(r"percent|pct", re.I)),
    (STAFF, re.compile(r"staff|worker|employee|engineer|office", re.I)),
    (YEARS, re.compile(r"years|experience|warranty", re.I)),
    (AREA, re.compile(r"area|m2", re.I)),
]
# Loại số đếm chỉ được đối chiếu với giá trị cùng loại (theo đơn vị trong chuỗi hoặc theo khóa JSON)
# + số khách tự nói; không đối chiếu với mọi số trong JSON (tránh "4 nhà thầu" khớp floors=4).
STRICT_KINDS = frozenset({PROJECT_COUNT, CONTRACTOR_COUNT, STAFF, YEARS, AREA})
# Số trần trong tin nhắn khách (không đơn vị, hoặc tích "5x20") chỉ được dùng cho các loại này. Số khách nói
# CÓ đơn vị ("ngân sách 2 tỷ", "gia đình 5 người") được gom theo đúng loại vào by_kind/money. Lý do: khách nói
# "5x20, 1 trệt 3 lầu" không được làm hợp lệ câu bịa "5 năm kinh nghiệm" / "3 nhà thầu" / "20 thợ".
USER_BARE_KINDS = frozenset({MONEY, UNIT_PRICE, NUMBER, AREA, YEAR})
# Tool chỉ dùng cho mô tả (mục 9.2): số trong kết quả KHÔNG được coi là nguồn số liệu.
DESCRIPTION_ONLY_TOOLS = frozenset({"search_knowledge"})


@dataclass
class AllowedValues:
    generic: set[float] = field(default_factory=set)  # số thô trong JSON tool (VND, số đếm...) + số trong chuỗi
    user: set[float] = field(default_factory=set)  # số khách tự nói (mọi loại claim đều được dùng)
    money: set[float] = field(default_factory=set)  # VND quy đổi từ chuỗi có đơn vị (tiền, đơn giá)
    by_kind: dict[str, set[float]] = field(default_factory=dict)  # theo đơn vị trong chuỗi / khóa JSON
    scores: set[float] = field(default_factory=set)  # điểm/trọng số (khóa score, weight...)
    years: set[float] = field(default_factory=set)  # năm từ khóa *_year, ngày ISO / dd/mm/yyyy

    def add_kind(self, kind: str, values: Iterable[float]) -> None:
        self.by_kind.setdefault(kind, set()).update(float(v) for v in values)

    def pool_for(self, kind: str) -> set[float]:
        pool = set(self.by_kind.get(kind, set()))
        if kind in USER_BARE_KINDS:
            pool |= self.user
        if kind in STRICT_KINDS:
            return pool
        if kind == SCORE:
            return pool | self.scores
        if kind == PERCENT:
            pool |= self.scores | {s * 100 for s in self.scores if 0 <= s <= 1}
            return pool | {g * 100 for g in self.generic if 0 < g < 1}
        pool |= self.generic
        if kind in (MONEY, UNIT_PRICE, NUMBER):
            pool |= self.money | self.by_kind.get(MONEY, set()) | self.by_kind.get(UNIT_PRICE, set())
        elif kind == YEAR:
            pool |= self.years
        return pool

    def matches(self, claim: Claim, tolerance: float = TOLERANCE) -> bool:
        pool = self.pool_for(claim.kind)
        return any(_close(v, a, tolerance) for v in claim.values for a in pool)


def _close(value: float, allowed: float, tolerance: float = TOLERANCE) -> bool:
    if allowed == 0:
        return value == 0
    return abs(value - allowed) <= tolerance * abs(allowed) + 1e-9


def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    return obj


def _path_kind(path: tuple[str, ...]) -> str | None:
    joined = ".".join(path)
    for kind, rx in _PATH_KIND:
        if rx.search(joined):
            return kind
    return None


def _bare_numbers(text: str) -> list[float]:
    out: list[float] = []
    masked = _mask(text)
    for m in _NUM_ANY_RE.finditer(masked):
        out.extend(parse_number(m.group(0)))
    for m in _DIM_RE.finditer(masked):  # "5x20" -> 100 (m²)
        for a in parse_number(m.group(1)):
            for b in parse_number(m.group(2)):
                out.append(round(a * b, 4))
    return out


def _add_text(allowed: AllowedValues, text: str, path_kind: str | None = None, from_user: bool = False) -> None:
    for y in _ISO_YEAR_RE.findall(text) + _DMY_YEAR_RE.findall(text):
        allowed.years.add(float(y))
    for c in extract_claims(text):
        if c.kind in (MONEY, UNIT_PRICE):
            allowed.money.update(c.values)
        else:
            allowed.add_kind(c.kind, c.values)
    bare = _bare_numbers(text)
    if from_user:
        allowed.user.update(bare)
        return
    allowed.generic.update(bare)
    if path_kind:
        allowed.add_kind(path_kind, bare)


def _walk(allowed: AllowedValues, obj: Any, path: tuple[str, ...] = ()) -> None:
    obj = _to_jsonable(obj)
    key = path[-1] if path else ""
    if isinstance(obj, dict):
        for k, v in obj.items():
            _walk(allowed, v, (*path, str(k)))
    elif isinstance(obj, (list, tuple, set)):
        items = list(obj)
        kind = _LIST_KIND.get(key)
        if kind:
            allowed.add_kind(kind, [len(items)])
        for it in items:
            _walk(allowed, it, path)
    elif isinstance(obj, bool) or obj is None:
        return
    elif isinstance(obj, (int, float)):
        v = float(obj)
        if _NOISE_KEY_RE.search(key):
            return
        if _SCORE_KEY_RE.search(key):
            allowed.scores.add(v)
        else:
            allowed.generic.add(v)
            pk = _path_kind(path)
            if pk:
                allowed.add_kind(pk, [v])
            if any(p.endswith("_year") or p == "year" for p in path):
                allowed.years.add(v)
    elif isinstance(obj, str):
        if _NOISE_KEY_RE.search(key) or key in _SKIP_STR_KEYS:
            return
        _add_text(allowed, obj, path_kind=_path_kind(path))


def _split_named(item: Any) -> tuple[str | None, Any]:
    if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str):
        return item[0], item[1]
    return None, item


def collect_allowed(tool_results: list[Any], user_texts: Sequence[str] = ()) -> AllowedValues:
    """Gom tập số được phép trích dẫn trong lượt hiện tại.

    `tool_results`: danh sách kết quả tool (pydantic, dict, list) hoặc cặp `(tool_name, result)`.
    Kết quả của tool chỉ-dùng-cho-mô-tả (`search_knowledge`) bị bỏ qua (mục 9.2: RAG không cho số liệu);
    khi không có tên, tên tool được suy từ hình dạng kết quả (`fallback.infer_tool_name`).
    `user_texts`: tin nhắn của khách trong phiên. Số có đơn vị ("2 tỷ", "100 m²", "5 người") được gom theo loại;
    số trần / tích "5x20" chỉ hợp lệ cho tiền, đơn giá, diện tích, số lớn, năm (`USER_BARE_KINDS`).
    """
    from app.ai.fallback import infer_tool_name  # import muộn: fallback import validator

    allowed = AllowedValues()
    for item in tool_results or []:
        name, result = _split_named(item)
        if name is None:
            name = infer_tool_name(result)  # vd list[KnowledgeChunk] truyền không kèm tên
        if name in DESCRIPTION_ONLY_TOOLS:
            continue
        _walk(allowed, result)
    for t in user_texts or ():
        if t:
            _add_text(allowed, t, from_user=True)
    return allowed


# --------------------------------------------------------------------------------------------------
# Cụm cấm
# --------------------------------------------------------------------------------------------------
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+|\n+")
_NEGATION_RE = re.compile(
    r"(không|chưa|chẳng)\s+(thể\s+|dám\s+|có\s+cơ\s+sở\s+để\s+|đủ\s+cơ\s+sở\s+để\s+)?"
    r"(khẳng\s+định|kết\s+luận|nói|đánh\s+giá|xác\s+định|xếp\s+hạng|cam\s+kết|so\s+sánh|gọi)"
    r"|(không|chưa)\s+(có|đủ)\s+(cơ\s+sở|căn\s+cứ|dữ\s+liệu|thông\s+tin)"
    r"|không\s+đồng\s+nghĩa|thay\s+vì|không\s+dùng|câu\s+hỏi|hỏi\s+(về\s+)?",
    _F)
# Phủ định đứng ngay trước cụm: "em không cam kết...", "không phải là báo giá", "không phải nhà thầu ... tốt nhất"
# (tối đa 3 từ chen giữa sau "không phải/không thể"). "Không phải ngẫu nhiên mà X là tốt nhất" KHÔNG là phủ định.
_NEG_TAIL_RE = re.compile(
    r"(không|chưa|chẳng)(\s+(thể|phải|được|nên))?(\s+là)?\s*$"
    r"|(không|chưa|chẳng)\s+(phải|thể)(\s+là)?(\s+\S+){0,3}\s*$", _F)
# Phủ định chỉ có hiệu lực trong cùng mệnh đề: cắt tiền tố tại ":", ";", ngoặc, "nhưng", "tuy nhiên", ", em/thì/còn".
_CLAUSE_CUT_RE = re.compile(
    r"[:;()\[\]]|(?<!\w)(?:nhưng|tuy\s+nhiên|song|thế\s+mà)(?!\w)|,\s*(?:em|thì|còn|và)(?!\w)", _F)
# Ngữ cảnh hợp lệ ngay trước cụm (≤ 2 từ): "yêu cầu báo giá ... để có con số chính xác", "gửi yêu cầu báo giá: ..."
_QUOTE_OK_CTX_RE = re.compile(
    r"(?:để\s+(?:có|nhận|biết)|yêu\s+cầu|nhà\s+thầu\s+sẽ|liên\s+hệ|gửi|xin)(?:\s+[^\s,]+){0,2}\s*$", _F)
_BEST_RE = re.compile(
    # "tốt nhất anh/chị nên..." là thành ngữ khuyên, không phải khẳng định.
    r"(?:tốt|tot)\s+(?:nhất|nhat)(?!\s+(?:là\s+|la\s+)?(?:anh|chị|chi|nên|nen|mình|bạn|hãy|em|quý)\b)"
    r"|(?:uy\s+tín|uy\s+tin|giỏi|chất\s+lượng|đáng\s+tin(?:\s+cậy)?|xuất\s+sắc|chuyên\s+nghiệp|đáng\s+chọn)\s+nhất"
    r"|uy\s+tin\s+nhat"
    r"|(?:nhà\s+thầu|đơn\s+vị|công\s+ty|thương\s+hiệu)\s+(?:\S+\s+){0,3}?hàng\s+đầu"
    r"|(?:là|đứng|nhà\s+thầu|đơn\s+vị|thương\s+hiệu|top)\s+(?:\S+\s+){0,2}?số\s*(?:1|một)(?![\d\w])"
    r"|\btop\s*1(?!\d)",
    _F)
_ORDINAL_CTX_RE = re.compile(r"bước|phương\s+án|lựa\s+chọn|mục|tầng|câu|trang|\blô\b|hẻm|đường|nhà\s+số", _F)
_PROJECT_DONE_RE = re.compile(
    r"\d+\s*(?:công\s+trình|dự\s+án)(?P<between>(?:\s+\S+){0,5}?)\s+(?:đã\s+)?(?:hoàn\s+thành|thi\s+công\s+xong"
    r"|bàn\s+giao|hoàn\s+tất)"
    r"|(?:đã\s+)?(?:hoàn\s+thành|thi\s+công\s+xong|bàn\s+giao|hoàn\s+tất)\s+(?:được\s+)?\d+\s*(?:công\s+trình|dự\s+án)",
    _F)
_QUOTE_RES = [
    # "Đây là báo giá", "khái toán này là báo giá thi công"
    re.compile(r"(?:đây|khái\s+toán|con\s+số|chi\s+phí|số\s+tiền|mức\s+giá)(?:\s+này|\s+trên)?\s+(?:là|chính\s+là)"
               r"\s+(?:một\s+)?(?:bản\s+)?báo\s+giá", _F),
    # "Đây là chi phí chính xác", "khái toán này là giá cố định"
    re.compile(r"(?:đây|khái\s+toán)(?:\s+này)?\s+(?:là|chính\s+là)\s+(?:giá|chi\s+phí|con\s+số)"
               r"\s+(?:chính\s+xác|cố\s+định|cuối\s+cùng)", _F),
    # "chi phí chính xác là 2 tỷ", "giá cố định: ..."
    re.compile(r"(?:chi\s+phí|giá|khái\s+toán|con\s+số|số\s+tiền)\s+(?:chính\s+xác|cố\s+định|cam\s+kết)"
               r"\s+(?:là|sẽ\s+là|:)", _F),
    # "khái toán này hoàn toàn chính xác", "con số trên là chính xác"
    re.compile(r"(?:khái\s+toán|con\s+số|chi\s+phí)(?:\s+này|\s+trên)?\s+(?:là\s+)?(?:hoàn\s+toàn\s+|rất\s+)?"
               r"chính\s+xác(?!\s+(?:hơn|nhất))", _F),
    re.compile(r"cam\s+kết\s+(?:chi\s+phí|giá|mức\s+giá|con\s+số)", _F),
    re.compile(r"báo\s+giá\s+(?:chính\s+xác|chính\s+thức|cuối\s+cùng|cố\s+định)\s+(?:cho|của)\s+(?:anh|chị|công\s+trình)"
               r"(?:.{0,30}?)\s+(?:là|:)", _F),
    # Gắn nhãn "báo giá" cho một con số: "Báo giá cho công trình của anh/chị là 2,058 tỷ", "báo giá sơ bộ: 2 tỷ"
    re.compile(r"báo\s+giá(?:\s+(?:sơ\s+bộ|dự\s+kiến|chính\s+xác|chính\s+thức|tạm\s+tính|thi\s+công))?"
               r"(?:\s+(?:cho|của)\s+[^.:;\d]{0,40}?)?\s*(?:là|:|khoảng|vào\s+khoảng|từ)\s*(?:khoảng\s+|từ\s+)?\d", _F),
]


def _sentences(text: str) -> list[str]:
    return [p for p in _SENT_SPLIT_RE.split(text) if p and p.strip()]


def _clause_prefix(sentence: str, match_start: int) -> str:
    """Phần mệnh đề đứng trước vị trí khớp (cắt tại dấu/ liên từ chuyển ý)."""
    prefix = sentence[:match_start]
    cut = 0
    for m in _CLAUSE_CUT_RE.finditer(prefix):
        cut = m.end()
    return prefix[cut:]


def _negated(sentence: str, match_start: int) -> bool:
    prefix = _clause_prefix(sentence, match_start)
    return bool(_NEGATION_RE.search(prefix) or _NEG_TAIL_RE.search(prefix))


def _phrase_violations(answer: str) -> list[Violation]:
    found: list[Violation] = []
    for sent in _sentences(answer):
        for m in _BEST_RE.finditer(sent):
            if _negated(sent, m.start()):
                continue
            if "số" in m.group(0).lower() and _ORDINAL_CTX_RE.search(m.group(0)):
                continue  # "là bước số 1", "phương án số 1"
            found.append(Violation(
                rule=RULE_BEST, text=m.group(0),
                detail="Không được khẳng định nhà thầu tốt nhất/số 1/uy tín nhất; dùng "
                       "\"phù hợp nhất với tiêu chí của anh/chị\"."))
        for m in _PROJECT_DONE_RE.finditer(sent):
            between = m.group("between") or ""
            if re.search(r"không|chưa", between, _F) or _negated(sent, m.start()):
                continue
            found.append(Violation(
                rule=RULE_PROJECT_LABEL, text=m.group(0),
                detail="Số dự án phải gọi là \"công trình ghi nhận trên Dezon\", không gọi là đã hoàn thành."))
        quote_hit = next((m for rx in _QUOTE_RES for m in rx.finditer(sent)
                          if not (_negated(sent, m.start())
                                  or _QUOTE_OK_CTX_RE.search(_clause_prefix(sent, m.start())))), None)
        if quote_hit is not None:  # tối đa một vi phạm estimate_as_quote mỗi câu
            found.append(Violation(
                rule=RULE_ESTIMATE_QUOTE, text=quote_hit.group(0),
                detail="Khái toán sơ bộ không phải báo giá; không khẳng định là báo giá/chi phí chính xác."))
    return found


# --------------------------------------------------------------------------------------------------
# API chính
# --------------------------------------------------------------------------------------------------
def check(answer: str, tool_results: list[Any], user_texts: Sequence[str] = (),
          allowed: AllowedValues | None = None) -> ValidationResult:
    """Kiểm tra câu trả lời của LLM theo guardrail mục 2.1.

    Trả `ValidationResult(ok, violations, claims)`. `allowed` có thể truyền sẵn để tái dùng.
    """
    allowed = allowed or collect_allowed(tool_results, user_texts)
    claims = extract_claims(answer)
    violations: list[Violation] = []
    seen: set[str] = set()
    for c in claims:
        if allowed.matches(c):
            continue
        if c.text in seen:
            continue
        seen.add(c.text)
        violations.append(Violation(
            rule=RULE_UNTRACEABLE, text=c.text,
            detail=f"{KIND_VI.get(c.kind, 'con số').capitalize()} \"{c.text}\" không có trong kết quả tool "
                   f"của lượt này (cho phép sai lệch ±1%)."))
    violations.extend(_phrase_violations(answer))
    return ValidationResult(ok=not violations, violations=violations, claims=claims)


def build_retry_message(violations: Sequence[Violation]) -> str:
    """Thông báo lỗi gửi lại LLM để sinh lại câu trả lời (một lần)."""
    lines = ["Câu trả lời vừa rồi vi phạm quy tắc dữ liệu của Dezon và đã bị chặn:"]
    rules = set()
    for v in violations:
        rules.add(v.rule)
        if v.rule == RULE_UNTRACEABLE:
            lines.append(f"- Số liệu \"{v.text}\" không có trong kết quả tool.")
        elif v.rule == RULE_BEST:
            lines.append(f"- Cụm \"{v.text}\": không được khẳng định nhà thầu tốt nhất/số 1/uy tín nhất.")
        elif v.rule == RULE_PROJECT_LABEL:
            lines.append(f"- Cụm \"{v.text}\": số dự án không đồng nghĩa với số dự án đã hoàn thành.")
        elif v.rule == RULE_ESTIMATE_QUOTE:
            lines.append(f"- Cụm \"{v.text}\": khái toán sơ bộ không phải báo giá.")
        else:
            lines.append(f"- \"{v.text}\": {v.detail}")
    lines.append("")
    lines.append("Hãy viết lại câu trả lời, tuân thủ:")
    lines.append("- CHỈ dùng số liệu có trong kết quả tool của lượt này; ưu tiên chép nguyên chuỗi `display`. "
                 "Không tự tính, không tự làm tròn khác đi, không thêm số mới.")
    lines.append("- Trường nào không có dữ liệu thì ghi đúng \"Chưa có dữ liệu\".")
    if RULE_BEST in rules:
        lines.append("- Dùng \"phù hợp nhất với tiêu chí của anh/chị\" thay cho \"tốt nhất\".")
    if RULE_PROJECT_LABEL in rules:
        lines.append("- Gọi số dự án là \"công trình ghi nhận trên Dezon\".")
    if RULE_ESTIMATE_QUOTE in rules:
        lines.append("- Nhắc rõ đây là khái toán sơ bộ, không phải báo giá thi công.")
    return "\n".join(lines)


def record_violations(db: Any, session_id: uuid.UUID | str | None, violations: Sequence[Violation],
                      answer: str, attempt: int) -> None:
    """Ghi các vi phạm vào bảng `guardrail_violations` (mỗi vi phạm một dòng).

    `detail` (JSONB) = {"text", "detail", "answer", "attempt"}.
    Hàm chỉ `db.add` + `db.flush()`, KHÔNG commit — caller (orchestrator) chịu trách nhiệm commit
    cùng transaction của lượt chat.
    """
    if not violations:
        return
    from app.db.models import GuardrailViolation

    sid: uuid.UUID | None
    if session_id is None or isinstance(session_id, uuid.UUID):
        sid = session_id
    else:
        try:
            sid = uuid.UUID(str(session_id))
        except ValueError:
            sid = None
    for v in violations:
        db.add(GuardrailViolation(session_id=sid, rule=v.rule,
                                  detail={"text": v.text, "detail": v.detail, "answer": answer,
                                          "attempt": attempt}))
    db.flush()


# --------------------------------------------------------------------------------------------------
# Vòng sinh lại
# --------------------------------------------------------------------------------------------------
@dataclass
class GuardedResult:
    text: str
    attempts: int  # số lần gọi generate
    used_fallback: bool
    violations: list[Violation] = field(default_factory=list)  # gộp mọi lần thử
    attempt_violations: list[list[Violation]] = field(default_factory=list)  # theo từng lần thử


def run_guarded(generate: Callable[[str | None], str], tool_results: list[Any],
                user_texts: Sequence[str] = (), max_attempts: int = 2,
                on_violation: Callable[[list[Violation], str, int], None] | None = None) -> GuardedResult:
    """Sinh câu trả lời có kiểm soát (mục 2.1).

    - `generate(None)` cho lần đầu; `generate(retry_message)` cho lần sinh lại (mặc định tối đa 2 lần).
    - Vẫn sai sau `max_attempts` -> câu trả lời dự phòng `fallback.build_fallback_text` (đã qua validator;
      nếu chính nó cũng không qua thì dùng câu xin lỗi chung không chứa số).
    - `on_violation(violations, answer, attempt)` được gọi mỗi lần bị chặn — orchestrator dùng để gọi
      `record_violations(db, session_id, ...)`.
    `tool_results` nhận kết quả thô hoặc cặp `(tool_name, result)`; exception từ `generate` không bị nuốt.
    """
    from app.ai import fallback

    allowed = collect_allowed(tool_results, user_texts)
    all_v: list[Violation] = []
    per_attempt: list[list[Violation]] = []
    retry: str | None = None
    attempts = 0
    for attempt in range(1, max(1, max_attempts) + 1):
        attempts = attempt
        text = generate(retry)
        res = check(text, tool_results, user_texts, allowed=allowed)
        per_attempt.append(res.violations)
        if res.ok:
            return GuardedResult(text=text, attempts=attempts, used_fallback=False, violations=all_v,
                                 attempt_violations=per_attempt)
        all_v.extend(res.violations)
        if on_violation is not None:
            on_violation(res.violations, text, attempt)
        retry = build_retry_message(res.violations)
    named = [fallback.ensure_named(item) for item in tool_results or []]
    fb = fallback.build_fallback_text(named)
    if not check(fb, tool_results, user_texts, allowed=allowed).ok:
        fb = fallback.GENERIC_APOLOGY
    return GuardedResult(text=fb, attempts=attempts, used_fallback=True, violations=all_v,
                         attempt_violations=per_attempt)
