import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { buildPdfUrl, createHttpClient } from '../../api/client';
import { COMPARISON_FIXTURE, ESTIMATE_FIXTURE, MATCH_FIXTURE } from '../../api/fixtures';
import { createMockClient } from '../../api/mock';
import type { ComparisonMatrix } from '../../api/types';
import { renderWithWidget } from '../../test/utils';
import { BlockRenderer } from '../blocks/BlockRenderer';
import { ComparisonTable } from '../blocks/ComparisonTable';
import { EstimateTable } from '../blocks/EstimateTable';

const EST = '7c1e9a52-4f0b-4d0e-9a51-2b1d6c3e8f10';
const pdfUrl = (id: string, ids?: number[]) => buildPdfUrl('https://api.test/', id, ids);

describe('buildPdfUrl / client.pdfUrl', () => {
  it('ghép đúng đường dẫn và contractor_ids lặp lại', () => {
    expect(buildPdfUrl('https://api.test/', EST)).toBe(`https://api.test/api/estimates/${EST}/pdf`);
    expect(buildPdfUrl('https://api.test', EST, [3, 1])).toBe(
      `https://api.test/api/estimates/${EST}/pdf?contractor_ids=3&contractor_ids=1`,
    );
    expect(buildPdfUrl('', 'a/b')).toBe('/api/estimates/a%2Fb/pdf');
  });

  it('client HTTP trả link, client mock trả null (ẩn nút)', () => {
    expect(createHttpClient('https://api.test').pdfUrl(EST, [1, 2])).toBe(
      `https://api.test/api/estimates/${EST}/pdf?contractor_ids=1&contractor_ids=2`,
    );
    expect(createMockClient({ delayMs: 0, storage: null }).pdfUrl(EST)).toBeNull();
  });
});

describe('EstimateTable — nút "Tải PDF khái toán"', () => {
  it('có estimate_session_id -> thẻ <a> tải PDF mở tab mới', () => {
    renderWithWidget(<EstimateTable block={{ type: 'estimate_table', data: { ...ESTIMATE_FIXTURE, estimate_session_id: EST } }} />, {
      pdfUrl,
    });
    const link = screen.getByRole('link', { name: 'Tải PDF khái toán' });
    expect(link).toHaveAttribute('href', `https://api.test/api/estimates/${EST}/pdf`);
    expect(link).toHaveAttribute('target', '_blank');
    // Không có `download`: lỗi server là trang HTML tiếng Việt hiện trong tab mới (không thành file tải hỏng).
    expect(link).not.toHaveAttribute('download');
    expect(link.getAttribute('rel')).toContain('noopener');
  });

  it('không có estimate_session_id hoặc chế độ mock -> ẩn nút', () => {
    const { unmount } = renderWithWidget(
      <EstimateTable block={{ type: 'estimate_table', data: { ...ESTIMATE_FIXTURE, estimate_session_id: null } }} />,
      { pdfUrl },
    );
    expect(screen.queryByRole('link', { name: 'Tải PDF khái toán' })).toBeNull();
    unmount();
    renderWithWidget(<EstimateTable block={{ type: 'estimate_table', data: { ...ESTIMATE_FIXTURE, estimate_session_id: EST } }} />, {
      pdfUrl,
      isMock: true,
    });
    expect(screen.queryByRole('link', { name: 'Tải PDF khái toán' })).toBeNull();
  });

  it('client không xuất được (pdfUrl null, mặc định) -> ẩn nút', () => {
    renderWithWidget(<EstimateTable block={{ type: 'estimate_table', data: { ...ESTIMATE_FIXTURE, estimate_session_id: EST } }} />);
    expect(screen.queryByRole('link', { name: /Tải PDF/ })).toBeNull();
  });
});

describe('ComparisonTable — nút "Tải PDF (khái toán + so sánh)"', () => {
  const ids = COMPARISON_FIXTURE.contractors.map((c) => c.id);
  const expected = `https://api.test/api/estimates/${EST}/pdf?${ids.map((i) => `contractor_ids=${i}`).join('&')}`;

  it('block.estimate_session_id -> link kèm id các nhà thầu theo thứ tự cột', () => {
    renderWithWidget(
      <BlockRenderer block={{ type: 'comparison_table', data: COMPARISON_FIXTURE, estimate_session_id: EST }} active />,
      { pdfUrl },
    );
    expect(screen.getByRole('link', { name: 'Tải PDF (khái toán + so sánh)' })).toHaveAttribute('href', expected);
  });

  it('prop estimateId (bảng mở trong danh sách nhà thầu) được ưu tiên', () => {
    renderWithWidget(
      <ComparisonTable block={{ type: 'comparison_table', data: COMPARISON_FIXTURE, estimate_session_id: 'khac' }} estimateId={EST} />,
      { pdfUrl },
    );
    expect(screen.getByRole('link', { name: 'Tải PDF (khái toán + so sánh)' })).toHaveAttribute('href', expected);
  });

  it('không có khái toán, chế độ mock, bảng tổng quan hoặc số nhà thầu ngoài 2–5 -> ẩn nút', () => {
    const name = 'Tải PDF (khái toán + so sánh)';
    const cases: { matrix: ComparisonMatrix; est: string | null; isMock?: boolean }[] = [
      { matrix: COMPARISON_FIXTURE, est: null },
      { matrix: COMPARISON_FIXTURE, est: EST, isMock: true },
      {
        matrix: {
          ...COMPARISON_FIXTURE,
          rows: [{ key: 'projects_of_type', label_vi: 'Công trình nhà phố có trang dự án', cells: COMPARISON_FIXTURE.rows[0]!.cells }],
        },
        est: EST,
      },
      { matrix: { ...COMPARISON_FIXTURE, contractors: COMPARISON_FIXTURE.contractors.slice(0, 1) }, est: EST },
      {
        matrix: {
          ...COMPARISON_FIXTURE,
          contractors: Array.from({ length: 6 }, (_, i) => ({ ...COMPARISON_FIXTURE.contractors[0]!, id: 100 + i })),
        },
        est: EST,
      },
    ];
    for (const c of cases) {
      const { unmount } = renderWithWidget(
        <ComparisonTable block={{ type: 'comparison_table', data: c.matrix, estimate_session_id: c.est }} />,
        { pdfUrl, isMock: c.isMock ?? false },
      );
      expect(screen.queryByRole('link', { name })).toBeNull();
      unmount();
    }
  });

  it('bảng so sánh mở trong danh sách nhà thầu dùng estimate_session_id của tiêu chí', async () => {
    renderWithWidget(<BlockRenderer block={{ type: 'contractor_list', data: MATCH_FIXTURE }} active />, {
      loadComparison: vi.fn(async () => COMPARISON_FIXTURE),
      pdfUrl,
    });
    fireEvent.click(screen.getByRole('button', { name: 'So sánh 3 nhà thầu trong bảng' }));
    const link = await screen.findByRole('link', { name: 'Tải PDF (khái toán + so sánh)' });
    expect(link.getAttribute('href')).toContain(`/api/estimates/${MATCH_FIXTURE.criteria.estimate_session_id}/pdf?contractor_ids=`);
  });
});
