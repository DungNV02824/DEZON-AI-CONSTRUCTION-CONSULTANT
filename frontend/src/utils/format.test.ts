import { describe, expect, it } from 'vitest';
import { formatCoefficient, formatDateVi, formatM2, formatVnd, isValidEmail, isValidVnPhone, toNationalPhone, viNumber } from './format';
import { findWidgetScript, readConfig } from '../config';

describe('định dạng (chuyển ngữ từ backend/app/formatting.py)', () => {
  it('viNumber', () => {
    expect(viNumber(1234.5)).toBe('1.234,5');
    expect(viNumber(2.058, 3)).toBe('2,058');
    expect(viNumber(490)).toBe('490');
    expect(viNumber(100.25)).toBe('100,25');
  });
  it('formatVnd / formatM2 / hệ số', () => {
    expect(formatVnd(2_058_000_000)).toBe('2,058 tỷ');
    expect(formatVnd(850_000_000)).toBe('850 triệu');
    expect(formatVnd(420_000_000)).toBe('420 triệu');
    expect(formatVnd(null)).toBe('Chưa có dữ liệu');
    expect(formatM2(490)).toBe('490 m²');
    expect(formatCoefficient(0.4)).toBe('0,4');
    expect(formatCoefficient(1)).toBe('1,0');
  });
  it('ngày dd/mm/yyyy không lệch múi giờ', () => {
    expect(formatDateVi('2026-10-31')).toBe('31/10/2026');
    expect(formatDateVi('2026-01-05T23:30:00+07:00')).toBe('05/01/2026');
    expect(formatDateVi(null)).toBeNull();
  });
});

describe('kiểm tra số điện thoại Việt Nam', () => {
  it.each(['0901234567', '0901 234 567', '090.123.4567', '+84901234567', '84 389 123 456', '02838123456'])('hợp lệ: %s', (p) =>
    expect(isValidVnPhone(p)).toBe(true),
  );
  it.each(['12345', '0123456789', '090123456', '09012345678', 'abc0901234567', '0401234567'])('không hợp lệ: %s', (p) =>
    expect(isValidVnPhone(p)).toBe(false),
  );
  it('email', () => {
    expect(isValidEmail('an@dezon.vn')).toBe(true);
    expect(isValidEmail('an@dezon')).toBe(false);
  });
});

describe('toNationalPhone', () => {
  it('+84 / 84 -> 0; số trong nước giữ nguyên', () => {
    expect(toNationalPhone('+84 901 234 567')).toBe('0901234567');
    expect(toNationalPhone('84901234567')).toBe('0901234567');
    expect(toNationalPhone('0901.234.567')).toBe('0901234567');
    expect(toNationalPhone('+84 28 3822 1234')).toBe('02838221234');
  });
});

describe('readConfig', () => {
  it('đọc data-api-base, data-open, data-mock từ thẻ script', () => {
    const s = document.createElement('script');
    s.dataset.apiBase = 'https://api.dezon.vn/';
    s.dataset.open = 'true';
    expect(readConfig(s, '', undefined)).toEqual({ apiBase: 'https://api.dezon.vn', open: true, mock: false });
    s.dataset.mock = 'true';
    expect(readConfig(s, '', undefined).mock).toBe(true);
  });
  it('fallback biến môi trường; ?dezon_mock=1 bật mock; không tự bật mock khi thiếu apiBase', () => {
    expect(readConfig(null, '', 'http://localhost:8000')).toEqual({ apiBase: 'http://localhost:8000', open: false, mock: false });
    expect(readConfig(null, '?dezon_mock=1', undefined).mock).toBe(true);
    expect(readConfig(null, '', undefined)).toEqual({ apiBase: '', open: false, mock: false });
  });
  it('data-api-base rỗng -> fallback VITE_API_BASE_URL (không âm thầm gọi cùng origin)', () => {
    const s = document.createElement('script');
    s.dataset.apiBase = '  ';
    expect(readConfig(s, '', 'https://api.dezon.vn/').apiBase).toBe('https://api.dezon.vn');
  });
  it('findWidgetScript: dùng currentScript, nếu null thì tìm thẻ dezon-widget.js', () => {
    const cur = document.createElement('script');
    expect(findWidgetScript(cur, document)).toBe(cur);
    const tag = document.createElement('script');
    tag.src = 'https://cdn.dezon.vn/dezon-widget.js';
    tag.dataset.apiBase = 'https://api.dezon.vn';
    document.body.appendChild(tag);
    try {
      expect(findWidgetScript(null, document)).toBe(tag);
    } finally {
      tag.remove();
    }
  });
});
