/**
 * Định dạng phía frontend.
 *
 * QUY TẮC: số liệu dạng EvidenceValue luôn hiển thị `display` từ backend, KHÔNG định dạng lại `value`.
 * Các hàm dưới đây chỉ dùng cho trường số thuần trong schema KHÔNG có chuỗi display đi kèm
 * (diện tích A_i, hệ số k_i, diện tích quy đổi, saving_min của phương án ngân sách) và cho ngày.
 * Chúng là bản chuyển ngữ 1-1 của backend/app/formatting.py (vi_number, format_m2, format_vnd).
 */

export const NO_DATA = 'Chưa có dữ liệu';

/** ISO "YYYY-MM-DD" (hoặc datetime ISO) -> "dd/mm/yyyy". Không dùng Date để tránh lệch múi giờ. */
export function formatDateVi(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return null;
  return `${m[3]}/${m[2]}/${m[1]}`;
}

function roundHalfUp(x: number, decimals: number): number {
  const f = 10 ** decimals;
  // Thêm epsilon tương đối để 2.0585 -> 2.059 như Decimal ROUND_HALF_UP.
  return Math.sign(x) * Math.round(Math.abs(x) * f * (1 + Number.EPSILON)) / f;
}

/** 1234.5 -> "1.234,5"; 2.058 -> "2,058" (maxDecimals=3); bỏ số 0 thừa. */
export function viNumber(x: number, maxDecimals = 2): string {
  const r = roundHalfUp(x, maxDecimals);
  const sign = r < 0 ? '-' : '';
  const [intPart = '0', frac = ''] = Math.abs(r).toFixed(maxDecimals).split('.');
  const fracTrim = frac.replace(/0+$/, '');
  const grouped = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  return fracTrim ? `${sign}${grouped},${fracTrim}` : `${sign}${grouped}`;
}

export function formatM2(x: number | null | undefined): string {
  if (x === null || x === undefined || Number.isNaN(x)) return NO_DATA;
  return `${viNumber(x, 2)} m²`;
}

/** 2_058_000_000 -> "2,058 tỷ"; 850_000_000 -> "850 triệu". */
export function formatVnd(amount: number | null | undefined): string {
  if (amount === null || amount === undefined || Number.isNaN(amount)) return NO_DATA;
  if (Math.abs(amount) >= 1e9) return `${viNumber(amount / 1e9, 3)} tỷ`;
  return `${viNumber(amount / 1e6, 1)} triệu`;
}

/** Hệ số k_i: 0.4 -> "0,4"; 1 -> "1,0" để dễ đọc như bảng 6.2. */
export function formatCoefficient(k: number): string {
  const s = viNumber(k, 2);
  return s.includes(',') ? s : `${s},0`;
}

/** Chuẩn hóa SĐT: bỏ khoảng trắng, dấu chấm, gạch, ngoặc. */
export function normalizePhone(raw: string): string {
  return raw.replace(/[\s.\-()]/g, '');
}

/**
 * Số điện thoại Việt Nam: di động 0[35789]xxxxxxxx (10 số) hoặc cố định 02xxxxxxxxx (11 số);
 * chấp nhận tiền tố +84 / 84 thay cho số 0.
 */
export function isValidVnPhone(raw: string): boolean {
  const p = normalizePhone(raw);
  return /^(?:\+?84|0)(?:[35789]\d{8}|2\d{9})$/.test(p);
}

/** SĐT đã hợp lệ -> dạng trong nước bắt đầu bằng 0 ("+84 901 234 567" -> "0901234567") để lưu thống nhất. */
export function toNationalPhone(raw: string): string {
  const p = normalizePhone(raw);
  return p.replace(/^\+?84(?=[235789]\d{8,9}$)/, '0');
}

export function isValidEmail(raw: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(raw.trim());
}
