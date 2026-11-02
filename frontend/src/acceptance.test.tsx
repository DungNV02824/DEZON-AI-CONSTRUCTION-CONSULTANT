/**
 * Hồi quy cho các phát hiện nghiệm thu MVP giai đoạn 4 (widget trên trình duyệt thật): W-F2, W-F3, W-F6, W-F7,
 * W-F8, W-F10, W-F11. CSS được kiểm tra trên văn bản styles.css vì jsdom không tính cascade theo specificity.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MATCH_FIXTURE, PROFILES } from './api/fixtures';
import { createMockClient } from './api/mock';
import type { ContractorProfile, UIBlock } from './api/types';
import { App } from './components/App';
import { MessageList } from './components/MessageList';
import { ContractorDetail, infoLabel } from './components/blocks/ContractorDetail';
import { ContractorList } from './components/blocks/ContractorList';
import { QuoteRequestForm } from './components/blocks/QuoteRequestForm';
import { SourcePopoverProvider } from './components/common/SourcePopover';
import { MOCK_SESSION_KEY, localMessagesKey, mergeLocals, type UIMessage } from './state/useChat';
import { renderWithWidget } from './test/utils';

// vitest không nạp nội dung CSS (css: false) -> đọc thẳng file; cwd = thư mục frontend.
const CSS = readFileSync(resolve(process.cwd(), 'src/styles.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');

/** Khối khai báo đầu tiên có selector đúng bằng `sel` (trong danh sách selector cách nhau dấu phẩy). */
function ruleBody(sel: string, css = CSS): string {
  const re = /([^{}]+)\{([^{}]*)\}/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(css))) {
    const sels = m[1]!.split(',').map((x) => x.trim());
    if (sels.includes(sel)) return m[2]!;
  }
  throw new Error(`Không thấy rule ${sel}`);
}

function mobileBlock(): string {
  const i = CSS.lastIndexOf('@media (max-width: 639.98px)');
  expect(i).toBeGreaterThan(0);
  return CSS.slice(i);
}

function memoryStorage(): Storage {
  const m = new Map<string, string>();
  return {
    get length() {
      return m.size;
    },
    clear: () => m.clear(),
    getItem: (k) => m.get(k) ?? null,
    key: (i) => [...m.keys()][i] ?? null,
    removeItem: (k) => void m.delete(k),
    setItem: (k, v) => void m.set(k, String(v)),
  };
}

describe('W-F2 — reset nút không đè màu/chữ của .dz-btn--primary', () => {
  it('không còn selector ".dz-root button|input|textarea" có specificity > 0 (chỉ dạng :where())', () => {
    const bad = CSS.match(/(^|[,}\s])\.dz-(root|popover)\s+(button|input|textarea)\b/gm);
    expect(bad).toBeNull();
    expect(CSS).toContain(':where(.dz-root, .dz-popover) :where(button, input, textarea)');
  });

  it('.dz-btn--primary chữ trắng, .dz-btn đậm 600, .dz-btn--sm 13px', () => {
    expect(ruleBody('.dz-btn--primary')).toMatch(/color:\s*#fff/);
    expect(ruleBody('.dz-btn')).toMatch(/font-weight:\s*600/);
    expect(ruleBody('.dz-btn--sm')).toMatch(/font-size:\s*13px/);
  });
});

describe('W-F3 — nhãn thông tin nhà thầu tiếng Việt', () => {
  it('years_experience -> "Kinh nghiệm"; khóa lạ không in tên khóa thô', () => {
    const base = PROFILES['kien-phong']!;
    const profile: ContractorProfile = {
      ...base,
      info: { ...base.info, years_experience: base.info.founded_year!, some_new_key: base.info.founded_year! },
    };
    const { container } = renderWithWidget(<ContractorDetail block={{ type: 'contractor_detail', data: profile }} />);
    const ths = Array.from(container.querySelectorAll('th[scope="row"]')).map((th) => th.textContent);
    expect(ths).toContain('Kinh nghiệm');
    for (const t of ths) expect(t).not.toMatch(/_/);
    expect(infoLabel('staff')).toBe('Nhân sự');
    expect(infoLabel('xyz_abc')).toBe('Thông tin khác');
  });
});

describe('W-F6 — chip nguồn dài xuống dòng trong khung chat', () => {
  it('.dz-srcbadges .dz-badge--full: white-space normal, max-width 100%', () => {
    const body = ruleBody('.dz-srcbadges .dz-badge--full');
    expect(body).toMatch(/white-space:\s*normal/);
    expect(body).toMatch(/max-width:\s*100%/);
    expect(body).toMatch(/overflow-wrap:\s*anywhere/);
  });
});

describe('W-F10 — vùng chạm / cỡ chữ trên mobile', () => {
  it('ô nhập ≥ 16px (iOS không zoom); nút/link chính ≥ 44px và chữ ≥ 13px khi ≤ 640px', () => {
    expect(ruleBody('.dz-composer__input')).toMatch(/font-size:\s*16px/);
    expect(ruleBody('.dz-field textarea')).toMatch(/font-size:\s*16px/);
    const mob = mobileBlock();
    expect(ruleBody('.dz-btn--sm', mob)).toMatch(/min-height:\s*44px/);
    expect(ruleBody('.dz-chip-btn', mob)).toMatch(/min-height:\s*44px/);
    expect(ruleBody('.dz-linkbtn', mob)).toMatch(/min-height:\s*44px/);
    expect(ruleBody('.dz-extlink', mob)).toMatch(/min-height:\s*44px/);
    expect(ruleBody('.dz-iconbtn', mob)).toMatch(/width:\s*44px/);
    expect(ruleBody('.dz-breakdown summary', mob)).toMatch(/min-height:\s*44px/);
    // Rule gốc có specificity cao (0,2,1 / 0,2,0) phải được nhắc lại trong media query.
    expect(ruleBody('.dz-reasons li .dz-badge--dot', mob)).toMatch(/min-height:\s*32px/);
    for (const sel of ['.dz-srcbadges .dz-badge--full', '.dz-pill', '.dz-badge', '.dz-small', '.dz-legend', '.dz-facts dt']) {
      expect(ruleBody(sel, mob)).toMatch(/font-size:\s*13px/);
    }
    expect(ruleBody('.dz-score')).toMatch(/font-size:\s*13px/);
  });
});

describe('W-F11 — nhãn phù hợp có màu riêng, chấm nguồn có chú giải', () => {
  it('dz-fit không dùng màu trạng thái 4.4', () => {
    const fit = ['.dz-fit', '.dz-fit--high', '.dz-fit--ok', '.dz-fit--review'].map((s) => ruleBody(s)).join('\n');
    expect(fit).not.toMatch(/--dz-(verified|on-dezon|reference|unavailable)/);
    expect(fit).not.toMatch(/#c2410c|#ffedd5|#15803d|#1d4ed8/i);
  });

  it('danh sách nhà thầu có chú giải 4 trạng thái hiển thị sẵn (không cần hover)', () => {
    renderWithWidget(<ContractorList block={{ type: 'contractor_list', data: MATCH_FIXTURE }} />);
    const legend = screen.getByRole('list', { name: 'Chú thích trạng thái dữ liệu' });
    expect(legend).toHaveTextContent('Đã xác minh');
    expect(legend).toHaveTextContent('Dữ liệu tham khảo');
  });
});

describe('W-F7 — block mới: cuộn tới đầu tin nhắn, không vào giữa đoạn văn', () => {
  afterEach(() => vi.restoreAllMocks());

  it('tin nhắn cao hơn khung nhìn -> scrollTop = vị trí đầu tin nhắn tính theo khung cuộn (không theo offsetParent)', () => {
    const msgs = (blocks: UIBlock[]): UIMessage[] => [
      { key: 'a', role: 'user', content: 'Đúng rồi', blocks: [] },
      { key: 'b', role: 'assistant', content: 'Lời dẫn dài...', blocks },
    ];
    // Bố cục giả: khung cuộn đặt ở y=100 (dưới header 60px + lề), tin nhắn "b" ở y=150, cao 900px, khung cao 500px.
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(function (this: HTMLElement) {
      return this.classList.contains('dz-messages') ? 500 : 0;
    });
    vi.spyOn(HTMLElement.prototype, 'offsetTop', 'get').mockReturnValue(9999); // offsetTop sai (theo .dz-panel) không được dùng
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const r = (top: number, height: number) => ({ top, height, bottom: top + height, left: 0, right: 0, width: 0, x: 0, y: top, toJSON: () => ({}) });
      if (this.classList.contains('dz-messages')) return r(100, 500) as DOMRect;
      if (this.dataset.key === 'b') return r(150, 900) as DOMRect;
      return r(0, 0) as DOMRect;
    });
    const { rerender, container } = render(
      <SourcePopoverProvider>
        <MessageList messages={msgs([])} busy={false} onRetry={() => undefined} />
      </SourcePopoverProvider>,
    );
    const log = container.querySelector<HTMLElement>('.dz-messages')!;
    log.scrollTop = 300;
    rerender(
      <SourcePopoverProvider>
        <MessageList
          messages={msgs([{ type: 'quick_replies', options: [{ label: 'Có', value: 'Có' }] } as UIBlock])}
          busy={false}
          onRetry={() => undefined}
        />
      </SourcePopoverProvider>,
    );
    // 300 (đang cuộn) + 150 (đỉnh tin nhắn) - 100 (đỉnh khung) - 8 (lề) = 342
    expect(log.scrollTop).toBe(342);
  });
});

describe('W-F8 — giữ hồ sơ/so sánh/mã báo giá sau khi tải lại trang', () => {
  it('mergeLocals chèn tin nhắn cục bộ ngay sau tin server làm mốc, mốc mất -> cuối', () => {
    const server: UIMessage[] = [
      { key: 's1', id: 1, role: 'assistant', content: 'chào', blocks: [] },
      { key: 's2', id: 2, role: 'user', content: 'x', blocks: [] },
      { key: 's3', id: 3, role: 'assistant', content: 'y', blocks: [] },
    ];
    const out = mergeLocals(server, [
      { key: 'l1', afterId: 1, content: 'L1', blocks: [] },
      { key: 'l2', afterId: 1, content: 'L2', blocks: [] },
      { key: 'l3', afterId: 99, content: 'L3', blocks: [] },
    ]);
    expect(out.map((m) => m.key)).toEqual(['s1', 'l1', 'l2', 's2', 's3', 'l3']);
    expect(out[1]!.local).toBe(true);
  });

  it('form báo giá đã gửi có submitted_code -> hiện thẻ thành công kèm mã (không hiện lại form)', () => {
    renderWithWidget(
      <QuoteRequestForm block={{ type: 'quote_request_form', contractors: [{ id: 1, name: 'A' }], submitted_code: 'DZ-EE351D' }} />,
    );
    expect(screen.getByText('Đã gửi yêu cầu báo giá')).toBeInTheDocument();
    expect(screen.getByText('DZ-EE351D')).toBeInTheDocument();
  });

  it('luồng thật: mở hồ sơ + gửi báo giá -> tải lại trang vẫn còn hồ sơ và mã DZ-…', async () => {
    window.localStorage.clear();
    const storage = memoryStorage();
    const client = createMockClient({ delayMs: 0, storage });
    const { unmount } = render(<App client={client} initiallyOpen />);
    await screen.findByText(/Xin chào anh\/chị/);
    const input = screen.getByLabelText('Nội dung tin nhắn');
    fireEvent.change(input, { target: { value: 'nhà phố 5x20 Thủ Đức 1 trệt 3 lầu xây thô ngân sách 2 tỷ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Gửi tin nhắn' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Đúng rồi' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Tìm nhà thầu phù hợp' }));
    await screen.findByText('3 nhà thầu phù hợp nhất với tiêu chí của anh/chị');
    fireEvent.click(screen.getAllByRole('button', { name: 'Xem chi tiết' })[0]!);
    await screen.findByText('Công trình thực tế trên Dezon');
    fireEvent.click(screen.getAllByRole('button', { name: 'Yêu cầu báo giá' })[0]!);
    const form = await screen.findByRole('form', { name: 'Yêu cầu báo giá' });
    fireEvent.change(form.querySelector('input[autocomplete="name"]')!, { target: { value: 'Nguyễn Văn A' } });
    const tel = form.querySelector<HTMLInputElement>('input[type="tel"]')!;
    fireEvent.change(tel, { target: { value: '0901234567' } });
    await act(async () => {
      fireEvent.submit(form);
    });
    const codeEl = await screen.findByText(/^DZ-/);
    const code = codeEl.textContent!;
    const sid = window.localStorage.getItem(MOCK_SESSION_KEY)!;
    await waitFor(() => expect(window.localStorage.getItem(localMessagesKey(MOCK_SESSION_KEY, sid))).toContain(code));
    unmount();

    // Tải lại trang: client mới cùng storage của mock server.
    render(<App client={createMockClient({ delayMs: 0, storage })} initiallyOpen />);
    expect(await screen.findByText(code)).toBeInTheDocument();
    expect(screen.getByText('Công trình thực tế trên Dezon')).toBeInTheDocument();
    expect(screen.getByText('Đã gửi yêu cầu báo giá')).toBeInTheDocument();
    // Thứ tự: hồ sơ đứng sau danh sách nhà thầu, thẻ thành công ở cuối.
    const log = screen.getByRole('log');
    const text = log.textContent!;
    expect(text.indexOf('3 nhà thầu phù hợp nhất')).toBeLessThan(text.indexOf('Công trình thực tế trên Dezon'));
    expect(text.indexOf('Công trình thực tế trên Dezon')).toBeLessThan(text.indexOf(code));
  });
});
