import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ReactElement } from 'react';
import type { EvidenceValue, SourceRef, UIBlock } from '../../api/types';
import type { UIMessage } from '../../state/useChat';
import { NO_DATA } from '../../utils/format';
import { MessageList } from '../MessageList';
import { WidgetContext } from '../../state/context';
import { makeActions } from '../../test/utils';
import { SourcePopoverProvider } from './SourcePopover';
import { ANSWER_SOURCES_INTRO, AnswerSourcesLink, AnswerSourcesProvider } from './AnswerSources';

const SRC_A: SourceRef = { label: 'Hồ sơ nhà thầu A trên Dezon', url: 'https://dezon.vn/nha-thau/a', updated_at: '2026-01-15' };
const SRC_RATE: SourceRef = { label: 'Đơn giá tham khảo', url: null, updated_at: '2026-10-01' };
const ev = (display: string, source: SourceRef = SRC_A, is_stale = false): EvidenceValue => ({
  value: display,
  display,
  status: 'on_dezon',
  source,
  is_stale,
});
const NA: EvidenceValue = { value: null, display: NO_DATA, status: 'unavailable', source: null, is_stale: false };

const BLOCKS: UIBlock[] = [
  {
    type: 'comparison_table',
    data: {
      contractors: [{ id: 1, slug: 'a', name: 'Nhà thầu A', profile_url: 'https://dezon.vn/nha-thau/a' }],
      rows: [
        { key: 'rough_price', label_vi: 'Đơn giá xây thô', cells: [ev('4,3–4,7 triệu/m²', SRC_A, true)] },
        { key: 'warranty', label_vi: 'Bảo hành', cells: [NA] },
      ],
    },
  },
  {
    type: 'source_badges',
    badges: [{ label: SRC_A.label, status: 'on_dezon', source: SRC_A }],
    items: [{ group: 'Đơn giá tham chiếu', label: 'Đơn giá tham chiếu (VND/m²)', evidence: { ...ev('5–6 triệu/m²', SRC_RATE), status: 'reference' } }],
  },
];

const link = () => screen.getByRole('button', { name: /^Nguồn dữ liệu \(\d+\)$/ });
const dialog = () => screen.queryByRole('dialog', { name: 'Nguồn dữ liệu' });

describe('AnswerSourcesLink (V2 — Nguồn dữ liệu)', () => {
  it('hiện "Nguồn dữ liệu (N)" với N = số nguồn; không có số liệu -> không hiện', () => {
    const { unmount } = render(<AnswerSourcesLink blocks={BLOCKS} />);
    expect(link()).toHaveTextContent('Nguồn dữ liệu (2)');
    unmount();
    render(<AnswerSourcesLink blocks={[{ type: 'quick_replies', options: [] }, { type: 'source_badges', badges: [] }]} />);
    expect(screen.queryByRole('button', { name: /Nguồn dữ liệu/ })).toBeNull();
  });

  it('bấm mở hộp thoại: nhóm theo nguồn, link tab mới rel=noopener, ngày, cảnh báo cũ, bảng Thông tin | Giá trị', () => {
    render(<AnswerSourcesLink blocks={BLOCKS} />);
    fireEvent.click(link());
    const dlg = dialog()!;
    expect(dlg).toHaveAttribute('aria-modal', 'true');
    expect(within(dlg).getByText(ANSWER_SOURCES_INTRO)).toBeInTheDocument();
    const a = within(dlg).getByRole('link', { name: /Hồ sơ nhà thầu A trên Dezon/ });
    expect(a).toHaveAttribute('href', SRC_A.url);
    expect(a).toHaveAttribute('target', '_blank');
    expect(a.getAttribute('rel')).toContain('noopener');
    expect(within(dlg).getByText('15/01/2026')).toBeInTheDocument();
    expect(within(dlg).getByText(/Dữ liệu cập nhật ngày 15\/01\/2026, có thể đã cũ/)).toBeInTheDocument();
    expect(within(dlg).getByText('Có dữ liệu trên Dezon')).toBeInTheDocument();
    expect(within(dlg).getByText('Dữ liệu tham khảo')).toBeInTheDocument();
    expect(within(dlg).getAllByRole('columnheader', { name: 'Thông tin' }).length).toBeGreaterThan(0);
    expect(within(dlg).getAllByRole('columnheader', { name: 'Giá trị' }).length).toBeGreaterThan(0);
    expect(within(dlg).getByText('4,3–4,7 triệu/m²')).toBeInTheDocument();
    expect(within(dlg).getByText('5–6 triệu/m²')).toBeInTheDocument();
    // Nguồn không có URL: hiển thị nhãn, không có link.
    expect(within(dlg).getByText('Đơn giá tham khảo')).not.toHaveAttribute('href');
    // Mục thu gọn "Chưa có dữ liệu (k)" ở cuối.
    const missing = dlg.querySelector('details.dz-srcdlg__missing')!;
    expect(missing).not.toHaveAttribute('open');
    expect(within(missing as HTMLElement).getByText(`${NO_DATA} (1)`)).toBeInTheDocument();
    expect(within(missing as HTMLElement).getByText('Bảo hành')).toBeInTheDocument();
    expect(dlg.textContent).not.toContain('tốt nhất');
  });

  it('Esc đóng hộp thoại, không lan ra ngoài, trả focus về link; nút × và bấm nền cũng đóng', () => {
    const outer = vi.fn();
    render(
      <div onKeyDown={(e) => e.key === 'Escape' && outer()}>
        <AnswerSourcesLink blocks={BLOCKS} />
      </div>,
    );
    fireEvent.click(link());
    expect(screen.getByRole('button', { name: 'Đóng' })).toHaveFocus();
    fireEvent.keyDown(dialog()!, { key: 'Escape' });
    expect(dialog()).toBeNull();
    expect(outer).not.toHaveBeenCalled();
    expect(link()).toHaveFocus();
    fireEvent.click(link());
    fireEvent.click(screen.getByRole('button', { name: 'Đóng' }));
    expect(dialog()).toBeNull();
    fireEvent.click(link());
    fireEvent.click(document.querySelector('.dz-srcdlg')!);
    expect(dialog()).toBeNull();
  });

  it('Tab tới được mục thu gọn "Chưa có dữ liệu (k)": link nguồn cuối -> summary -> ×; Shift+Tab ngược lại', () => {
    render(<AnswerSourcesLink blocks={BLOCKS} />);
    fireEvent.click(link());
    const dlg = dialog()!;
    const close = within(dlg).getByRole('button', { name: 'Đóng' });
    const lastLink = within(dlg).getByRole('link', { name: /Hồ sơ nhà thầu A trên Dezon/ });
    const summary = dlg.querySelector('details.dz-srcdlg__missing > summary') as HTMLElement;
    lastLink.focus();
    // Không chặn Tab ở link cuối -> trình duyệt chuyển focus sang summary (phần tử kế tiếp trong vòng).
    expect(fireEvent.keyDown(lastLink, { key: 'Tab' })).toBe(true);
    expect(lastLink).toHaveFocus();
    summary.focus();
    expect(fireEvent.keyDown(summary, { key: 'Tab' })).toBe(false); // summary là phần tử cuối -> quay về ×
    expect(close).toHaveFocus();
    expect(fireEvent.keyDown(close, { key: 'Tab', shiftKey: true })).toBe(false);
    expect(summary).toHaveFocus();
  });

  it('không có link nguồn nào: Tab từ × sang summary chứ không kẹt ở ×', () => {
    const blocks: UIBlock[] = [
      {
        type: 'source_badges',
        badges: [],
        items: [
          { group: 'Đơn giá tham chiếu', label: 'Đơn giá tham chiếu (VND/m²)', evidence: { ...ev('5–6 triệu/m²', SRC_RATE), status: 'reference' } },
          { group: 'Tổng quan', label: 'Bảo hành', evidence: NA },
        ],
      },
    ];
    render(<AnswerSourcesLink blocks={blocks} />);
    fireEvent.click(link());
    const dlg = dialog()!;
    expect(within(dlg).queryByRole('link')).toBeNull();
    const close = within(dlg).getByRole('button', { name: 'Đóng' });
    expect(close).toHaveFocus();
    // × là phần tử đầu, summary là phần tử cuối: Tab ở × không bị chặn (sang summary); Shift+Tab ở × -> summary.
    expect(fireEvent.keyDown(close, { key: 'Tab' })).toBe(true);
    expect(fireEvent.keyDown(close, { key: 'Tab', shiftKey: true })).toBe(false);
    expect(dlg.querySelector('details > summary')).toHaveFocus();
  });

  it('hộp thoại có tabIndex=-1: focus rơi vào hộp (bấm chữ) thì Esc vẫn đóng, Tab quay về ×', () => {
    render(<AnswerSourcesLink blocks={BLOCKS} />);
    fireEvent.click(link());
    const dlg = dialog()!;
    expect(dlg).toHaveAttribute('tabindex', '-1');
    dlg.focus();
    expect(dlg).toHaveFocus();
    expect(fireEvent.keyDown(dlg, { key: 'Tab' })).toBe(false);
    expect(within(dlg).getByRole('button', { name: 'Đóng' })).toHaveFocus();
    dlg.focus();
    fireEvent.keyDown(dlg, { key: 'Escape' });
    expect(dialog()).toBeNull();
  });
});

describe('AnswerSourcesProvider (hộp thoại render ở cấp gốc)', () => {
  const actions = makeActions();
  const msg: UIMessage = { key: 'm1', role: 'assistant', content: 'Dạ, em gửi anh/chị.', blocks: BLOCKS };
  const tree = (variant: 'panel' | 'page' = 'panel') => (
    <WidgetContext.Provider value={actions}>
      <SourcePopoverProvider>
        <div className={variant === 'page' ? 'dz-root dz-root--page' : 'dz-root'}>
          <AnswerSourcesProvider>
            <div className="dz-panel">
              <MessageList messages={[msg]} busy={false} onRetry={() => undefined} variant={variant} />
            </div>
          </AnswerSourcesProvider>
        </div>
      </SourcePopoverProvider>
    </WidgetContext.Provider>
  );

  it('hộp thoại nằm ngoài .dz-panel và ngoài log aria-live; aria-expanded + trả focus khi đóng', () => {
    const { container } = render(tree());
    expect(link()).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(link());
    const dlg = dialog()!;
    expect(dlg.closest('[role="log"]')).toBeNull();
    expect(dlg.closest('.dz-panel')).toBeNull();
    expect(dlg.closest('.dz-root')).toBe(container.querySelector('.dz-root'));
    expect(container.querySelector('[role="log"] .dz-srcdlg')).toBeNull();
    expect(link()).toHaveAttribute('aria-expanded', 'true');
    expect(within(dlg).getByRole('button', { name: 'Đóng' })).toHaveFocus();
    fireEvent.keyDown(dlg, { key: 'Escape' });
    expect(dialog()).toBeNull();
    expect(link()).toHaveAttribute('aria-expanded', 'false');
    expect(link()).toHaveFocus();
  });

  it('mở từ giao diện mở rộng -> hộp thoại giữ lớp .dz-root--page', () => {
    const { container } = render(tree('page'));
    fireEvent.click(link());
    expect(container.querySelector('.dz-srcdlg')).toHaveClass('dz-root--page');
  });
});

describe('MessageList + Nguồn dữ liệu', () => {
  const actions = makeActions();
  const wrap = (ui: ReactElement) => (
    <WidgetContext.Provider value={actions}>
      <SourcePopoverProvider>{ui}</SourcePopoverProvider>
    </WidgetContext.Provider>
  );
  const msg = (over: Partial<UIMessage>): UIMessage => ({ key: 'm1', role: 'assistant', content: 'Dạ, em gửi anh/chị.', blocks: BLOCKS, ...over });

  it('link nằm cuối tin nhắn trợ lý; ẩn khi đang nhận / lỗi; chip source_badges vẫn ẩn', () => {
    const { container, rerender } = render(wrap(<MessageList messages={[msg({})]} busy={false} onRetry={() => undefined} />));
    const m = container.querySelector('.dz-msg--assistant')!;
    expect(m.lastElementChild).toHaveClass('dz-srclink');
    expect(container.querySelector('.dz-srcbadges')).toBeNull();
    rerender(wrap(<MessageList messages={[msg({ pending: true })]} busy onRetry={() => undefined} />));
    expect(container.querySelector('.dz-srclink')).toBeNull();
    rerender(wrap(<MessageList messages={[msg({ error: 'Lỗi' })]} busy={false} onRetry={() => undefined} />));
    expect(container.querySelector('.dz-srclink')).toBeNull();
    rerender(wrap(<MessageList messages={[msg({}), msg({ key: 'u', role: 'user', content: 'hi', blocks: [] })]} busy={false} onRetry={() => undefined} />));
    expect(container.querySelectorAll('.dz-srclink')).toHaveLength(1);
  });

  it('giao diện mở rộng (page) cũng có link', () => {
    const { container } = render(wrap(<MessageList messages={[msg({})]} busy={false} onRetry={() => undefined} variant="page" />));
    expect(container.querySelector('.dz-srclink')).toHaveTextContent('Nguồn dữ liệu (2)');
  });
});
