"""Định dạng số kiểu Việt Nam: dấu phẩy thập phân, dấu chấm hàng nghìn.

Dùng chung cho engine (chuỗi `display`), validator (đối chiếu số) và ui_blocks.
"""
from decimal import ROUND_HALF_UP, Decimal

TY = 1_000_000_000
TRIEU = 1_000_000
NO_DATA = "Chưa có dữ liệu"


def vi_number(x: float | int | Decimal, max_decimals: int = 2, min_decimals: int = 0) -> str:
    """1234.5 -> '1.234,5'; 2.058 -> '2,058' (max_decimals=3); bỏ số 0 thừa (giữ tối thiểu `min_decimals` chữ số)."""
    d = Decimal(str(x)).quantize(Decimal(1).scaleb(-max_decimals), rounding=ROUND_HALF_UP)
    sign = "-" if d < 0 else ""
    d = abs(d)
    int_part, _, frac = f"{d:f}".partition(".")
    frac = frac.rstrip("0")
    if min_decimals:
        frac = frac.ljust(min(min_decimals, max_decimals), "0")
    int_grouped = f"{int(int_part):,}".replace(",", ".")
    return f"{sign}{int_grouped},{frac}" if frac else f"{sign}{int_grouped}"


def round_to_million(amount: float | Decimal) -> int:
    return int((Decimal(str(amount)) / TRIEU).quantize(Decimal(1), rounding=ROUND_HALF_UP) * TRIEU)


def _unit_for(amount: float) -> tuple[int, str, int]:
    if abs(amount) >= TY:
        return TY, "tỷ", 3
    return TRIEU, "triệu", 1


def format_vnd(amount: float | int | Decimal | None) -> str:
    """2_058_000_000 -> '2,058 tỷ'; 850_000_000 -> '850 triệu'."""
    if amount is None:
        return NO_DATA
    a = float(amount)
    div, unit, dec = _unit_for(a)
    return f"{vi_number(a / div, dec)} {unit}"


def _decimals(formatted: str) -> int:
    return len(formatted.partition(",")[2])


def format_vnd_range(lo: float | int | Decimal | None, hi: float | int | Decimal | None) -> str:
    """(2_058e6, 2_352e6) -> '2,058–2,352 tỷ'; khác đơn vị -> '850 triệu–1,2 tỷ'."""
    if lo is None or hi is None:
        return NO_DATA
    lo_f, hi_f = float(lo), float(hi)
    div_lo, unit_lo, dec_lo = _unit_for(lo_f)
    div_hi, unit_hi, dec_hi = _unit_for(hi_f)
    if unit_lo == unit_hi:
        if lo_f == hi_f:
            return format_vnd(lo_f)
        # Hai đầu khoảng cùng số chữ số thập phân: "1,740–2,058 tỷ" (không "1,74–2,058").
        width = max(_decimals(vi_number(lo_f / div_lo, dec_lo)), _decimals(vi_number(hi_f / div_hi, dec_hi)))
        return (f"{vi_number(lo_f / div_lo, dec_lo, width)}–{vi_number(hi_f / div_hi, dec_hi, width)} "
                f"{unit_hi}")
    return f"{format_vnd(lo_f)}–{format_vnd(hi_f)}"


def format_price_per_m2_range(lo: float | int | Decimal | None, hi: float | int | Decimal | None) -> str:
    """(4_300_000, 4_700_000) -> '4,3–4,7 triệu/m²'."""
    if lo is None and hi is None:
        return NO_DATA
    if lo is None:  # chỉ có cận trên (cận dưới trên hồ sơ là giá trị mặc định của form)
        return f"tối đa {vi_number(float(hi) / TRIEU, 2)} triệu/m²"
    if hi is None or float(lo) == float(hi):
        v = lo
        return f"{vi_number(float(v) / TRIEU, 2)} triệu/m²"
    return f"{vi_number(float(lo) / TRIEU, 2)}–{vi_number(float(hi) / TRIEU, 2)} triệu/m²"


def format_m2(x: float | int | Decimal | None) -> str:
    if x is None:
        return NO_DATA
    return f"{vi_number(x, 2)} m²"
