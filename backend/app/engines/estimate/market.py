"""Tổng hợp đơn giá/m² từ đơn giá các nhà thầu tự công bố trên hồ sơ Dezon (hàm thuần).

Dezon không công bố bảng giá; dữ liệu giá thật duy nhất là khoảng đơn giá mỗi nhà thầu ghi trên hồ sơ
(`rough_price_*` = xây thô, `finish_price_*` = "hoàn thiện cơ bản" = trọn gói). Quy tắc (docs/DECISIONS.md):

- Mỗi nhà thầu: cận dưới = min (nếu không có thì max), cận trên = max (nếu không có thì min).
- Khoảng tổng hợp = (phân vị 25 của các cận dưới, phân vị 75 của các cận trên), nội suy tuyến tính,
  làm tròn 50.000 VND.
- Cần tối thiểu `MIN_CONTRACTORS_FOR_RATE` nhà thầu, ít hơn -> None (không tự tạo số).
"""
from collections.abc import Iterable, Sequence
from decimal import ROUND_HALF_UP, Decimal

MIN_CONTRACTORS_FOR_RATE = 3
ROUND_TO_VND = 50_000

# Nhãn nguồn của mọi dòng đơn giá tổng hợp từ hồ sơ nhà thầu bắt đầu bằng tiền tố này. Đặt ở engine (thuần) để
# calculator nhận diện nguồn của đơn giá mà không phải import tầng service.
MARKET_LABEL_PREFIX = "Tổng hợp đơn giá "
# Đơn giá mẫu seed dev cũ (bảng giá tự đặt, không phải dữ liệu thật).
SEED_DEV_LABEL_PREFIX = "Đơn giá tham khảo (seed dev"


def is_market_label(label: str | None) -> bool:
    """Nhãn nguồn là đơn giá tổng hợp từ hồ sơ nhà thầu (không phải đơn giá chính thức Dezon nhập)?"""
    return bool(label) and label.startswith(MARKET_LABEL_PREFIX)


def is_seed_dev_label(label: str | None) -> bool:
    return bool(label) and label.startswith(SEED_DEV_LABEL_PREFIX)

PriceBound = int | float | Decimal | None


def contractor_bounds(price_min: PriceBound, price_max: PriceBound) -> tuple[Decimal, Decimal] | None:
    """(cận dưới, cận trên) của một nhà thầu; None nếu không có giá dương nào."""
    lo = Decimal(str(price_min)) if price_min is not None else None
    hi = Decimal(str(price_max)) if price_max is not None else None
    if lo is not None and lo <= 0:
        lo = None
    if hi is not None and hi <= 0:
        hi = None
    if lo is None and hi is None:
        return None
    lo = lo if lo is not None else hi
    hi = hi if hi is not None else lo
    return (lo, hi) if lo <= hi else (hi, lo)


def percentile(values: Sequence[Decimal | int | float], p: float) -> Decimal:
    """Phân vị p (0–1), nội suy tuyến tính giữa hai điểm liền kề (như numpy 'linear')."""
    if not values:
        raise ValueError("percentile của danh sách rỗng")
    xs = sorted(Decimal(str(v)) for v in values)
    pos = Decimal(str(p)) * (len(xs) - 1)
    i = int(pos)
    frac = pos - i
    if i + 1 >= len(xs):
        return xs[-1]
    return xs[i] + (xs[i + 1] - xs[i]) * frac


def round_to_step(x: Decimal, step: int = ROUND_TO_VND) -> int:
    return int((x / step).quantize(Decimal(1), rounding=ROUND_HALF_UP)) * step


def aggregate_price_range(ranges: Iterable[tuple[PriceBound, PriceBound]],
                          min_n: int = MIN_CONTRACTORS_FOR_RATE) -> tuple[int, int] | None:
    """Khoảng đơn giá tổng hợp (VND/m²) từ các khoảng (min, max) nhà thầu công bố.

    Bỏ qua khoảng không có giá; ít hơn `min_n` nhà thầu có giá -> None."""
    bounds = [b for b in (contractor_bounds(lo, hi) for lo, hi in ranges) if b is not None]
    if len(bounds) < max(min_n, 1):
        return None
    low = round_to_step(percentile([b[0] for b in bounds], 0.25))
    high = round_to_step(percentile([b[1] for b in bounds], 0.75))
    return (low, high) if low <= high else (high, low)
