import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderWithWidget } from '../../test/utils';
import { SummaryConfirm } from './SummaryConfirm';

const items = [
  ['Loại công trình', 'Khách sạn'], ['Khu vực', 'TP. Hồ Chí Minh'], ['Diện tích đất', '6 × 12 m (72 m²)'],
  ['Số tầng', '1 trệt 1 lửng 2 lầu'], ['Phạm vi', 'Trọn gói'], ['Diện tích xây dựng mỗi tầng', '72 m²'],
  ['Tầng hầm', 'Không'], ['Tầng lửng', 'Có (72 m²)'], ['Mái', 'Mái tôn'], ['Phong cách', 'Tối giản'],
  ['Ngân sách', '2 tỷ'], ['Mức đầu tư', 'Phổ thông'],
].map(([label, value]) => ({ label, value, edit_value: `Tôi muốn sửa ${label.toLowerCase()}` }));

describe('Thẻ xác nhận gọn', () => {
  it('chỉ hiện 5 mục chính; 7 mục còn lại nằm trong "Xem thêm 7 mục", vẫn sửa được', () => {
    const { container } = renderWithWidget(
      <SummaryConfirm
        block={{ type: 'summary_confirm', title: 'Xác nhận thông tin — Khách sạn', items, confirm_label: 'Tính ngay → xem bảng khái toán', confirm_value: 'Đúng rồi' }}
        active
      />,
    );
    const main = container.querySelector('.dz-sumtable') as HTMLElement;
    expect(main.querySelectorAll('tr')).toHaveLength(5);
    expect(main).toHaveTextContent('Ngân sách');
    expect(main).not.toHaveTextContent('Mức đầu tư');
    const more = screen.getByText('Xem thêm 7 mục');
    fireEvent.click(more);
    expect(container.querySelector('.dz-sumcard__more')).toHaveTextContent('Mức đầu tư');
    expect(screen.getByRole('button', { name: 'Sửa mức đầu tư' })).toBeInTheDocument();
  });
});
