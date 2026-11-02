import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import {
  BUDGET_FIXTURE,
  COMPARISON_FIXTURE,
  ESTIMATE_FIXTURE,
  MATCH_FIXTURE,
  PROFILES,
} from '../../api/fixtures';
import type { ComparisonMatrix, EstimateResult } from '../../api/types';
import { renderWithWidget } from '../../test/utils';
import { BlockRenderer } from './BlockRenderer';
import { ComparisonTable } from './ComparisonTable';
import { ContractorCardView } from './ContractorCard';
import { EstimateTable } from './EstimateTable';

describe('ComparisonTable', () => {
  it('ô thiếu dữ liệu -> "Chưa có dữ liệu", không có ô trống (kể cả khi cells thiếu phần tử)', () => {
    const matrix: ComparisonMatrix = {
      ...COMPARISON_FIXTURE,
      rows: [
        ...COMPARISON_FIXTURE.rows,
        // backend gửi thiếu ô -> frontend vẫn điền "Chưa có dữ liệu"
        { key: 'extra', label_vi: 'Hàng thiếu ô', cells: [COMPARISON_FIXTURE.rows[0]!.cells[0]!] },
      ],
    };
    const { container } = renderWithWidget(<ComparisonTable block={{ type: 'comparison_table', data: matrix }} />);
    const cells = Array.from(container.querySelectorAll('tbody td'));
    expect(cells).toHaveLength(matrix.rows.length * matrix.contractors.length);
    for (const td of cells) expect(td.textContent?.trim()).not.toBe('');
    const missing = cells.filter((td) => td.textContent?.includes('Chưa có dữ liệu'));
    expect(missing.length).toBeGreaterThan(5);
    // Mọi ô thiếu có lớp chữ xám
    for (const td of missing) expect(td.querySelector('.dz-ev--unavailable')).not.toBeNull();
  });

  it('bấm tên nhà thầu -> mở contractor_detail', () => {
    const { actions } = renderWithWidget(<ComparisonTable block={{ type: 'comparison_table', data: COMPARISON_FIXTURE }} />);
    fireEvent.click(screen.getByRole('button', { name: 'Xem chi tiết Smartcons Tech' }));
    expect(actions.openContractor).toHaveBeenCalledWith('smartcons-tech', 2);
  });

  it('bảng tổng quan (hàng đầu projects_of_type) có tiêu đề "Tổng quan", bảng so sánh giữ "So sánh"', () => {
    const n = COMPARISON_FIXTURE.contractors.length;
    const overview: ComparisonMatrix = {
      ...COMPARISON_FIXTURE,
      rows: [
        { key: 'projects_of_type', label_vi: 'Công trình nhà phố có trang dự án', cells: COMPARISON_FIXTURE.rows[0]!.cells },
        ...COMPARISON_FIXTURE.rows,
      ],
    };
    const { unmount } = renderWithWidget(<ComparisonTable block={{ type: 'comparison_table', data: overview }} />);
    expect(screen.getByRole('heading', { name: `Tổng quan ${n} nhà thầu` })).toBeInTheDocument();
    unmount();
    renderWithWidget(<ComparisonTable block={{ type: 'comparison_table', data: COMPARISON_FIXTURE }} />);
    expect(screen.getByRole('heading', { name: `So sánh ${n} nhà thầu` })).toBeInTheDocument();
  });
});

describe('EstimateTable', () => {
  it('hiển thị số liệu từ engine và luôn có ghi chú miễn trừ', () => {
    renderWithWidget(<EstimateTable block={{ type: 'estimate_table', data: ESTIMATE_FIXTURE }} />);
    expect(screen.getByText('2,058–2,352 tỷ')).toBeInTheDocument();
    expect(screen.getByText('490 m²')).toBeInTheDocument();
    expect(screen.getByText('4,2–4,8 triệu/m²')).toBeInTheDocument();
    const note = screen.getByRole('note');
    expect(note).toHaveTextContent('Không phải báo giá thi công');
  });

  it('disclaimer rỗng từ backend -> vẫn hiển thị miễn trừ mục 6.6 kèm nguồn đơn giá', () => {
    const data: EstimateResult = { ...ESTIMATE_FIXTURE, disclaimer: '' };
    renderWithWidget(<EstimateTable block={{ type: 'estimate_table', data }} />);
    const note = screen.getByRole('note');
    expect(note).toHaveTextContent('Đây là khái toán sơ bộ dựa trên đơn giá tham chiếu');
    expect(note).toHaveTextContent('31/10/2026');
    expect(note).toHaveTextContent('Không phải báo giá thi công');
  });

  it('không có đơn giá -> chi phí "Chưa có dữ liệu" nhưng vẫn có miễn trừ', () => {
    const none = { value: null, display: 'Chưa có dữ liệu', status: 'unavailable' as const, source: null, is_stale: false };
    const data: EstimateResult = { ...ESTIMATE_FIXTURE, cost_min: null, cost_max: null, cost: none, rate: none };
    renderWithWidget(<EstimateTable block={{ type: 'estimate_table', data }} />);
    expect(screen.getAllByText('Chưa có dữ liệu')).toHaveLength(2);
    expect(screen.getByRole('note')).toHaveTextContent('Không phải báo giá thi công');
  });

  it('nút "Xem chi tiết hệ số" mở bảng A_i · k_i · A_i×k_i', () => {
    renderWithWidget(<EstimateTable block={{ type: 'estimate_table', data: ESTIMATE_FIXTURE }} />);
    const btn = screen.getByRole('button', { name: 'Xem chi tiết hệ số' });
    expect(btn).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(btn);
    const region = screen.getByRole('region', { name: 'Chi tiết hệ số khái toán' });
    expect(within(region).getByText('Móng cọc (chưa gồm ép cọc)')).toBeInTheDocument();
    expect(within(region).getByText('0,4')).toBeInTheDocument();
    expect(within(region).getByText('40')).toBeInTheDocument();
  });
});

describe('EstimateTable — nguồn đơn giá', () => {
  it('dòng đơn giá tham chiếu hiển thị nguồn kèm ngày cập nhật dd/mm/yyyy (không chỉ trong tooltip)', () => {
    renderWithWidget(<EstimateTable block={{ type: 'estimate_table', data: ESTIMATE_FIXTURE }} />);
    const row = screen.getByRole('rowheader', { name: 'Đơn giá tham chiếu' }).closest('tr')!;
    expect(row).toHaveTextContent('4,2–4,8 triệu/m²');
    expect(row).toHaveTextContent(/Nguồn: .*cập nhật 31\/10\/2026/);
  });
});

describe('ContractorCard', () => {
  it('có link hồ sơ gốc (tab mới, rel noopener), lý do là nội dung chính', () => {
    const item = MATCH_FIXTURE.results[0]!;
    renderWithWidget(<ContractorCardView item={item} />);
    const link = screen.getByRole('link', { name: /Xem hồ sơ năng lực ↗/ });
    expect(link).toHaveAttribute('href', 'https://dezon.vn/nha-thau/kien-phong');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link.getAttribute('rel')).toContain('noopener');
    expect(screen.getByText('Vì sao em đề xuất')).toBeInTheDocument();
    expect(screen.getByText('7 công trình ghi nhận trên Dezon')).toBeInTheDocument();
    expect(screen.getByText('Công trình ghi nhận trên Dezon')).toBeInTheDocument();
    expect(screen.getByText('Phù hợp')).toBeInTheDocument();
    expect(screen.getByText('Điểm 76/100')).toBeInTheDocument();
    expect(screen.getAllByRole('img').length).toBeGreaterThan(0);
  });

  it('nhà thầu thiếu dữ liệu -> "Chưa có dữ liệu" + điểm cần lưu ý; không có chữ "tốt nhất"', () => {
    const item = MATCH_FIXTURE.results[1]!;
    const { container } = renderWithWidget(<ContractorCardView item={item} />);
    expect(screen.getAllByText('Chưa có dữ liệu').length).toBeGreaterThanOrEqual(3);
    expect(screen.getByText('Chưa có thông tin đơn giá — cần đối chiếu')).toBeInTheDocument();
    expect(screen.getByText('Cần đánh giá thêm')).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/tốt nhất/i);
  });
});

describe('ContractorList', () => {
  it('Xem chi tiết ghi nhận click + mở hồ sơ; chọn 2 nhà thầu rồi So sánh', () => {
    const { actions } = renderWithWidget(<BlockRenderer block={{ type: 'contractor_list', data: MATCH_FIXTURE }} active />);
    expect(screen.getByText('3 nhà thầu phù hợp nhất với tiêu chí của anh/chị')).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: 'Xem chi tiết' })[0]!);
    expect(actions.recordClick).toHaveBeenCalledWith(1, 101);
    expect(actions.openContractor).toHaveBeenCalledWith('kien-phong', 1);

    const compareBtns = screen.getAllByRole('button', { name: 'So sánh' });
    fireEvent.click(compareBtns[0]!);
    fireEvent.click(compareBtns[1]!);
    const bar = screen.getByRole('region', { name: 'Nhà thầu đã chọn' });
    fireEvent.click(within(bar).getByRole('button', { name: 'So sánh' }));
    expect(actions.compare).toHaveBeenCalledWith([1, 2], MATCH_FIXTURE.criteria.estimate_session_id);
  });
});

describe('ContractorDetail', () => {
  it('lưới 3 ảnh + ô "+N", danh sách công trình có link dự án, link hồ sơ đầy đủ', () => {
    const { actions } = renderWithWidget(
      <BlockRenderer block={{ type: 'contractor_detail', data: PROFILES['kien-phong']!, estimate_session_id: 'est-9' }} active />,
    );
    expect(screen.getByText('+4')).toBeInTheDocument();
    const proj = screen.getByRole('link', { name: 'Indochine Villa Quận 7 Sài Gòn ↗' });
    expect(proj).toHaveAttribute('href', 'https://dezon.vn/du-an/indochine-villa-quan-7');
    expect(screen.getAllByText(/Đơn vị thi công: Kiến Phong/)).toHaveLength(3);
    expect(screen.getByRole('link', { name: 'Mở hồ sơ đầy đủ trên Dezon ↗' })).toHaveAttribute('target', '_blank');
    fireEvent.click(screen.getByRole('button', { name: 'Yêu cầu báo giá' }));
    expect(actions.openQuoteForm).toHaveBeenCalledWith([{ id: 1, name: 'Kiến Phong' }], 'est-9');
  });

  it('hồ sơ thiếu dữ liệu -> bảng thông tin và công trình hiển thị "Chưa có dữ liệu"', () => {
    renderWithWidget(<BlockRenderer block={{ type: 'contractor_detail', data: PROFILES['huy-hoang']! }} active />);
    expect(screen.getAllByText('Chưa có dữ liệu').length).toBeGreaterThanOrEqual(8);
  });
});

describe('QuickReplies / SummaryConfirm / BudgetOptions / SourceBadges', () => {
  it('quick replies gửi value; "Nhập thông tin khác" focus ô nhập', () => {
    const { actions } = renderWithWidget(
      <BlockRenderer block={{ type: 'quick_replies', options: [{ label: 'Nhà phố', value: 'Tôi muốn xây nhà phố' }], allow_free_text: true }} active />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Nhà phố' }));
    expect(actions.send).toHaveBeenCalledWith('Tôi muốn xây nhà phố');
    fireEvent.click(screen.getByRole('button', { name: 'Nhập thông tin khác' }));
    expect(actions.focusInput).toHaveBeenCalled();
  });

  it('quick replies cũ (không active) bị ẩn', () => {
    renderWithWidget(<BlockRenderer block={{ type: 'quick_replies', options: [{ label: 'A', value: 'A' }] }} active={false} />);
    expect(screen.queryByRole('button', { name: 'A' })).toBeNull();
  });

  it('summary_confirm: 2 nút gửi value mặc định', () => {
    const { actions } = renderWithWidget(
      <BlockRenderer block={{ type: 'summary_confirm', items: [{ label: 'Loại công trình', value: 'Nhà phố' }], assumptions: ['Mức trung bình'] }} active />,
    );
    expect(screen.getByText('Mức trung bình')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Đúng rồi' }));
    expect(actions.send).toHaveBeenCalledWith('Đúng rồi, tính khái toán giúp tôi');
  });

  it('budget_options: chi phí display, tiết kiệm, nhãn "Vừa ngân sách"', () => {
    renderWithWidget(<BlockRenderer block={{ type: 'budget_options', data: BUDGET_FIXTURE }} active />);
    expect(screen.getByText('1,474–1,685 tỷ')).toBeInTheDocument();
    expect(screen.getAllByText('Vừa ngân sách')).toHaveLength(1);
    expect(screen.getByText('Tiết kiệm khoảng 584 triệu so với khái toán hiện tại')).toBeInTheDocument();
  });

  it('source_badges: chip nhãn + tooltip nguồn', () => {
    renderWithWidget(
      <BlockRenderer
        block={{ type: 'source_badges', badges: [{ label: 'Dezon', status: 'on_dezon', source: { label: 'dezon.vn', url: 'https://dezon.vn', updated_at: '2026-10-31' } }] }}
        active
      />,
    );
    fireEvent.mouseEnter(screen.getByRole('button', { name: /Dezon/ }));
    expect(screen.getByRole('tooltip')).toHaveTextContent('31/10/2026');
  });
});

describe('QuoteRequestForm', () => {
  it('kiểm tra SĐT Việt Nam, gửi kèm estimate_session_id, hiển thị mã yêu cầu', async () => {
    const { actions } = renderWithWidget(
      <BlockRenderer
        block={{ type: 'quote_request_form', contractors: [{ id: 1, name: 'Kiến Phong' }, { id: 2, name: 'Smartcons Tech' }], estimate_session_id: 'est-1' }}
        active
      />,
    );
    fireEvent.change(screen.getByLabelText(/Họ tên/), { target: { value: 'Nguyễn Văn An' } });
    fireEvent.change(screen.getByLabelText(/Số điện thoại/), { target: { value: '12345' } });
    fireEvent.click(screen.getByRole('button', { name: 'Gửi yêu cầu báo giá' }));
    expect(await screen.findByText(/chưa đúng định dạng Việt Nam/)).toBeInTheDocument();
    expect(actions.submitQuote).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText(/Số điện thoại/), { target: { value: '0901 234 567' } });
    fireEvent.click(screen.getByLabelText('Smartcons Tech'));
    fireEvent.click(screen.getByRole('button', { name: 'Gửi yêu cầu báo giá' }));
    await waitFor(() =>
      expect(actions.submitQuote).toHaveBeenCalledWith({
        contractor_ids: [1],
        estimate_session_id: 'est-1',
        customer_name: 'Nguyễn Văn An',
        customer_phone: '0901234567',
        customer_email: null,
        note: null,
      }),
    );
    expect(await screen.findByText('DZ-TEST-0001')).toBeInTheDocument();
  });
});

describe('QuoteRequestForm — hồi quy', () => {
  it('form không có nhà thầu -> báo lỗi hiển thị được (không im lặng), không gửi', async () => {
    const { actions } = renderWithWidget(
      <BlockRenderer block={{ type: 'quote_request_form', contractors: [], estimate_session_id: null }} active />,
    );
    fireEvent.change(screen.getByLabelText(/Họ tên/), { target: { value: 'Trần Thị Bình' } });
    fireEvent.change(screen.getByLabelText(/Số điện thoại/), { target: { value: '0901234567' } });
    fireEvent.click(screen.getByRole('button', { name: 'Gửi yêu cầu báo giá' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Chưa có nhà thầu nào được chọn');
    expect(actions.submitQuote).not.toHaveBeenCalled();
  });

  it('SĐT dạng +84 được chuẩn hóa về dạng trong nước 0xxxxxxxxx', async () => {
    const { actions } = renderWithWidget(
      <BlockRenderer block={{ type: 'quote_request_form', contractors: [{ id: 1, name: 'Kiến Phong' }] }} active />,
    );
    fireEvent.change(screen.getByLabelText(/Họ tên/), { target: { value: 'Lê Văn Cường' } });
    fireEvent.change(screen.getByLabelText(/Số điện thoại/), { target: { value: '+84 912.345.678' } });
    fireEvent.click(screen.getByRole('button', { name: 'Gửi yêu cầu báo giá' }));
    await waitFor(() =>
      expect(actions.submitQuote).toHaveBeenCalledWith(expect.objectContaining({ customer_phone: '0912345678', contractor_ids: [1] })),
    );
  });
});
