import { screen, fireEvent } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { EvidenceValue } from '../../api/types';
import { renderWithWidget } from '../../test/utils';
import { EvidenceCell } from './EvidenceCell';
import { StatusBadge } from './StatusBadge';

const UNAVAILABLE: EvidenceValue = { value: null, display: 'Chưa có dữ liệu', status: 'unavailable', source: null, is_stale: false };
const PRICE: EvidenceValue = {
  value: [4300000, 4700000],
  display: '4,3–4,7 triệu/m²',
  status: 'on_dezon',
  source: { label: 'Hồ sơ nhà thầu Kiến Phong', url: 'https://dezon.vn/nha-thau/kien-phong', updated_at: '2026-10-31' },
  is_stale: false,
};

describe('EvidenceCell', () => {
  it('unavailable -> chữ xám đúng "Chưa có dữ liệu", không có badge', () => {
    const { container } = renderWithWidget(<EvidenceCell ev={UNAVAILABLE} />);
    const text = screen.getByText('Chưa có dữ liệu');
    expect(text).toBeInTheDocument();
    expect(container.querySelector('.dz-ev--unavailable')).not.toBeNull();
    expect(container.querySelector('button')).toBeNull();
  });

  it('null / undefined / display rỗng -> vẫn hiển thị "Chưa có dữ liệu" (không bao giờ trống)', () => {
    for (const ev of [null, undefined, { ...PRICE, display: '  ' }]) {
      const { unmount } = renderWithWidget(<EvidenceCell ev={ev} />);
      expect(screen.getByText('Chưa có dữ liệu')).toBeInTheDocument();
      unmount();
    }
  });

  it('hiển thị nguyên văn display (không định dạng lại value) kèm badge trạng thái', () => {
    renderWithWidget(<EvidenceCell ev={PRICE} />);
    expect(screen.getByText('4,3–4,7 triệu/m²')).toBeInTheDocument();
    expect(screen.queryByText(/4300000/)).toBeNull();
    expect(screen.getByText('Có dữ liệu trên Dezon')).toBeInTheDocument();
  });

  it('bấm badge -> tooltip nguồn với nhãn, link, ngày dd/mm/yyyy', () => {
    renderWithWidget(<EvidenceCell ev={PRICE} />);
    fireEvent.click(screen.getByRole('button', { name: /Có dữ liệu trên Dezon/ }));
    const tip = screen.getByRole('tooltip');
    expect(tip).toHaveTextContent('Hồ sơ nhà thầu Kiến Phong');
    expect(tip).toHaveTextContent('31/10/2026');
    const link = tip.querySelector('a')!;
    expect(link).toHaveAttribute('href', 'https://dezon.vn/nha-thau/kien-phong');
    expect(link).toHaveAttribute('target', '_blank');
  });

  it('is_stale -> cảnh báo ngày cập nhật', () => {
    renderWithWidget(<EvidenceCell ev={{ ...PRICE, is_stale: true, source: { ...PRICE.source!, updated_at: '2026-03-15' } }} />);
    expect(screen.getByText(/Dữ liệu cập nhật ngày 15\/03\/2026, có thể đã cũ/)).toBeInTheDocument();
  });
});

describe('StatusBadge', () => {
  it.each([
    ['verified', 'Đã xác minh'],
    ['on_dezon', 'Có dữ liệu trên Dezon'],
    ['reference', 'Dữ liệu tham khảo'],
    ['unavailable', 'Chưa có dữ liệu'],
  ] as const)('%s -> nhãn "%s" và lớp màu riêng', (status, label) => {
    const { container } = renderWithWidget(<StatusBadge status={status} />);
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(container.querySelector(`.dz-badge--${status}`)).not.toBeNull();
  });
});

describe('tooltip nguồn — hồi quy vòng lặp render (React #185)', () => {
  it('hover -> focus -> bấm ghim -> hover badge khác -> đóng: không lỗi, popover hoạt động', () => {
    const OTHER: EvidenceValue = { ...PRICE, display: '8 năm', source: { ...PRICE.source!, label: 'Nguồn khác' } };
    renderWithWidget(
      <div>
        <EvidenceCell ev={PRICE} badge="dot" />
        <EvidenceCell ev={OTHER} badge="dot" />
      </div>,
    );
    const [a, b] = screen.getAllByRole('button');
    fireEvent.mouseEnter(a!);
    fireEvent.focus(a!);
    fireEvent.click(a!);
    expect(screen.getByRole('tooltip')).toHaveTextContent('Hồ sơ nhà thầu Kiến Phong');
    fireEvent.mouseEnter(b!); // đang ghim a -> không bị thay
    expect(screen.getByRole('tooltip')).toHaveTextContent('Hồ sơ nhà thầu Kiến Phong');
    fireEvent.click(screen.getByRole('button', { name: 'Đóng thông tin nguồn' }));
    expect(screen.queryByRole('tooltip')).toBeNull();
  });
});
