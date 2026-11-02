import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ApiError, type ApiClient } from '../api/client';
import { createMockClient, mockReply } from '../api/mock';
import type { UIBlock } from '../api/types';
import { MOCK_SESSION_KEY, SESSION_KEY } from '../state/useChat';
import { App } from './App';

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

describe('mock client — luồng hội thoại mục 5.4', () => {
  it('chào + quick replies, hỏi từng bước, câu đầy đủ -> tóm tắt, xác nhận -> bảng khái toán 490 m²', async () => {
    const client = createMockClient({ delayMs: 0, storage: memoryStorage() });
    const s = await client.createSession();
    expect(s.messages[0]!.content).toMatch(/Xin chào anh\/chị/);
    expect(s.messages[0]!.ui_blocks[0]!.type).toBe('quick_replies');

    const run = async (text: string) => {
      let content = '';
      const blocks: UIBlock[] = [];
      let done = false;
      await client.sendMessage(s.session_id, text, {
        onText: (d) => (content += d),
        onBlock: (b) => blocks.push(b),
        onDone: () => (done = true),
      });
      expect(done).toBe(true);
      return { content, blocks };
    };

    const r1 = await run('Tôi muốn xây nhà phố.');
    expect(r1.content).toMatch(/khu vực nào/);
    const r2 = await run('TP. Hồ Chí Minh');
    expect(r2.content).toMatch(/diện tích đất và số tầng/);
    const r3 = await run('5x20, 1 trệt 3 lầu');
    expect(r3.content).toMatch(/phạm vi thi công/);
    const r4 = await run('Xây thô');
    expect(r4.blocks[0]!.type).toBe('summary_confirm');
    const r5 = await run('Đúng rồi, tính khái toán giúp tôi');
    const est = r5.blocks.find((b) => b.type === 'estimate_table');
    expect(est && est.type === 'estimate_table' && est.data.converted_area_m2).toBe(490);
    expect(est && est.type === 'estimate_table' && est.data.cost.display).toBe('2,058–2,352 tỷ');
    const st = await client.getSession(s.session_id);
    expect(st.messages.length).toBe(11);
  });

  it('câu đầy đủ một lần -> chỉ xác nhận, không hỏi lại', () => {
    const r = mockReply('nhà phố 5x20 Thủ Đức 1 trệt 3 lầu xây thô ngân sách 2 tỷ', {
      session_id: 'x',
      stage: 'collect',
      slots: {},
      messages: [],
      nextId: 1,
    });
    expect(r.blocks[0]!.type).toBe('summary_confirm');
  });

  it('nhận diện diện tích dạng "100 m²"; "tìm hiểu" không bị hiểu nhầm là tìm nhà thầu', () => {
    const session = { session_id: 'x', stage: 'collect', slots: {}, messages: [], nextId: 1 };
    const r = mockReply('Tôi muốn tìm hiểu chi phí nhà phố TP.HCM đất 100 m², 4 tầng, xây thô', session);
    expect(r.blocks[0]!.type).toBe('summary_confirm');
    expect(session.slots).toMatchObject({ land_area_m2: 100, floors: 4 });
  });
});

describe('App (widget)', () => {
  it('mở khung chat, gửi bằng Enter, stream câu trả lời, giữ phiên khi tải lại', async () => {
    const storage = memoryStorage();
    const client = createMockClient({ delayMs: 0, storage });
    // useChat dùng window.localStorage mặc định — mock client cũng dùng storage riêng; đồng bộ khóa phiên qua window.localStorage.
    const { unmount } = render(<App client={client} />);
    fireEvent.click(screen.getByRole('button', { name: /Mở trợ lý Dezon AI/ }));
    expect(screen.getByRole('dialog', { name: 'Dezon AI Consultant' })).toBeInTheDocument();
    expect(await screen.findByText(/Xin chào anh\/chị/)).toBeInTheDocument();
    expect(screen.getByText('Chế độ giả lập')).toBeInTheDocument();

    const input = screen.getByLabelText('Nội dung tin nhắn');
    fireEvent.change(input, { target: { value: 'Tôi muốn xây nhà phố' } });
    await act(async () => {
      fireEvent.keyDown(input, { key: 'Enter' });
    });
    expect(await screen.findByText(/anh\/chị dự định xây nhà ở khu vực nào\?/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'TP. Hồ Chí Minh' })).toBeEnabled());

    const savedId = window.localStorage.getItem(MOCK_SESSION_KEY);
    expect(savedId).toBeTruthy();
    unmount();

    // "Tải lại trang": client mới cùng storage, App mới -> getSession khôi phục hội thoại.
    const client2 = createMockClient({ delayMs: 0, storage });
    render(<App client={client2} initiallyOpen />);
    expect(await screen.findByText('Tôi muốn xây nhà phố')).toBeInTheDocument();
    expect(window.localStorage.getItem(MOCK_SESSION_KEY)).toBe(savedId);
  });

  it('bấm quick reply cho tới bảng khái toán và mở hồ sơ nhà thầu', async () => {
    const client = createMockClient({ delayMs: 0, storage: memoryStorage() });
    render(<App client={client} initiallyOpen />);
    await screen.findByText(/Xin chào anh\/chị/);
    const input = screen.getByLabelText('Nội dung tin nhắn');
    fireEvent.change(input, { target: { value: 'nhà phố 5x20 Thủ Đức 1 trệt 3 lầu xây thô ngân sách 2 tỷ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Gửi tin nhắn' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Đúng rồi' }));
    expect(await screen.findByText('2,058–2,352 tỷ')).toBeInTheDocument();
    expect(screen.getByRole('note')).toHaveTextContent('Không phải báo giá thi công');
    fireEvent.click(await screen.findByRole('button', { name: 'Tìm nhà thầu phù hợp' }));
    expect(await screen.findByText('3 nhà thầu phù hợp nhất với tiêu chí của anh/chị')).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: 'Xem chi tiết' })[0]!);
    expect(await screen.findByText('Công trình thực tế trên Dezon')).toBeInTheDocument();
  });
});

function fakeClient(overrides: Partial<ApiClient>): ApiClient {
  const base = createMockClient({ delayMs: 0, storage: memoryStorage() });
  return { ...base, isMock: false, ...overrides };
}

describe('useChat — khôi phục phiên', () => {
  it('lỗi mạng khi getSession -> giữ session_id đã lưu, báo lỗi, KHÔNG tạo phiên mới', async () => {
    window.localStorage.setItem(SESSION_KEY, 'old-session');
    const createSession = vi.fn();
    const client = fakeClient({
      getSession: vi.fn(async () => {
        throw new TypeError('Failed to fetch');
      }),
      createSession,
    });
    render(<App client={client} initiallyOpen />);
    expect(await screen.findByText(/chưa kết nối được tới máy chủ Dezon/)).toBeInTheDocument();
    expect(createSession).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(SESSION_KEY)).toBe('old-session');
  });

  it('getSession 404 -> bỏ phiên cũ, tạo phiên mới', async () => {
    window.localStorage.setItem(SESSION_KEY, 'expired');
    const mock = createMockClient({ delayMs: 0, storage: memoryStorage() });
    const client = fakeClient({
      getSession: vi.fn(async () => {
        throw new ApiError(404, 'Không tìm thấy phiên');
      }),
      createSession: mock.createSession,
    });
    render(<App client={client} initiallyOpen />);
    expect(await screen.findByText(/Xin chào anh\/chị/)).toBeInTheDocument();
    expect(window.localStorage.getItem(SESSION_KEY)).not.toBe('expired');
  });

  it('bấm quick reply 2 lần liên tiếp chỉ gửi 1 tin nhắn', async () => {
    const mock = createMockClient({ delayMs: 0, storage: memoryStorage() });
    const sendMessage = vi.fn(mock.sendMessage);
    const client = fakeClient({ createSession: mock.createSession, getSession: mock.getSession, sendMessage });
    render(<App client={client} initiallyOpen />);
    await screen.findByText(/Xin chào anh\/chị/);
    // Gửi 2 lần trong cùng một lượt (trước khi React kịp render lại trạng thái busy).
    const nhaPho = screen.getByRole('button', { name: 'Nhà phố' });
    await act(async () => {
      nhaPho.click();
      nhaPho.click();
    });
    await screen.findByText(/khu vực nào/);
    expect(sendMessage).toHaveBeenCalledTimes(1);
  });
});
