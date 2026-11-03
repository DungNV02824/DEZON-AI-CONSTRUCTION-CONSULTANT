import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ApiError, type ApiClient } from '../api/client';
import {
  BACK_VALUE,
  CONFIRM_LABEL,
  CONFIRM_VALUE,
  MORE_LABEL,
  MORE_VALUE,
  SKIP_VALUE,
  createMockClient,
  mockReply,
} from '../api/mock';
import type { QuestionStepBlock, UIBlock } from '../api/types';
import { MOCK_SESSION_KEY, SESSION_KEY } from '../state/useChat';
import { questionOf } from '../test/mockFlow';
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

const DEFAULTS_ALL =
  'Tạm tính: xây kín đất (100 m²/tầng), không hầm, không lửng, mái bê tông cốt thép — anh/chị bấm Sửa hoặc Cung cấp thêm thông tin để thay đổi.';
const PILE = 'Móng tính theo thông lệ: móng cọc (nhà từ 4 tầng nổi trở lên), chưa gồm chi phí ép cọc.';
const newSession = (): Parameters<typeof mockReply>[1] => ({ session_id: 'x', stage: 'collect', slots: {}, messages: [], nextId: 1 });
const summaryOf = (r: { blocks: UIBlock[] }) => {
  const b = r.blocks.find((x) => x.type === 'summary_confirm');
  if (!b || b.type !== 'summary_confirm') throw new Error('phải có thẻ summary_confirm');
  return b;
};

describe('mock client — 5 câu hỏi rồi thẻ xác nhận (quyết định chủ sản phẩm)', () => {
  it('đúng 5 câu i/5 -> thẻ xác nhận (Tạm tính) -> Cung cấp thêm thông tin (i/N) -> Sửa một dòng -> bảng khái toán 490 m²', async () => {
    const client = createMockClient({ delayMs: 0, storage: memoryStorage() });
    const s = await client.createSession();
    expect(s.messages[0]!.content).toMatch(/Xin chào anh\/chị/);
    expect(s.messages[0]!.ui_blocks[0]!.type).toBe('question_step');

    let runs = 0;
    const run = async (text: string) => {
      let content = '';
      const blocks: UIBlock[] = [];
      let done = false;
      runs++;
      await client.sendMessage(s.session_id, text, {
        onText: (d) => (content += d),
        onBlock: (b) => blocks.push(b),
        onDone: () => (done = true),
      });
      expect(done).toBe(true);
      return { content, blocks };
    };

    const step = (r: { blocks: UIBlock[] }) => {
      const b = r.blocks.find((x) => x.type === 'question_step');
      expect(b, 'phải có thẻ question_step').toBeTruthy();
      return b as QuestionStepBlock;
    };
    const labels = (b: QuestionStepBlock) => b.options.map((o) => o.label);
    // Lời chào: thẻ câu hỏi loại công trình, câu 1/5 (nút Trước bị khóa).
    const g = s.messages[0]!.ui_blocks[0] as QuestionStepBlock;
    expect(g).toMatchObject({
      type: 'question_step',
      question: 'Loại công trình anh/chị dự định xây dựng?',
      step: 1,
      total: 5,
      back_value: null,
      skippable: false,
    });
    expect(labels(g)).toEqual(['Nhà phố', 'Biệt thự', 'Căn hộ', 'Văn phòng', 'Khách sạn', 'Khác']);
    expect(s.messages[0]!.content).not.toMatch(/loại công trình nào/); // văn bản không lặp lại câu hỏi

    const r1 = await run('Tôi muốn xây nhà phố.');
    expect(r1.content).toBe('Dạ, em đã ghi nhận.');
    expect(r1.blocks.some((b) => b.type === 'quick_replies')).toBe(false);
    expect(step(r1)).toMatchObject({ question: 'Khu vực xây dựng?', step: 2, total: 5, back_value: BACK_VALUE });
    const r2 = await run('TP. Hồ Chí Minh');
    expect(step(r2)).toMatchObject({ question: 'Diện tích đất?', step: 3, total: 5 });
    expect(step(r2).options).toEqual([]);
    expect(step(r2).chips!.map((c) => c.label)).toEqual(['4 × 10', '5 × 10', '6 × 10', '6 × 12', '9 × 12', '12 × 10']);
    // Diện tích đất và số tầng là hai câu riêng.
    const r3 = await run('5x20');
    expect(step(r3)).toMatchObject({ question: 'Số tầng dự kiến?', step: 4, total: 5 });
    expect(labels(step(r3))).toEqual(['1 trệt', '1 trệt 1 lầu', '1 trệt 2 lầu', '1 trệt 3 lầu', '1 trệt 4 lầu']);
    const r4 = await run('1 trệt 3 lầu');
    expect(step(r4)).toMatchObject({ question: 'Phạm vi thi công cần khái toán?', step: 5, total: 5, skippable: false });
    expect(labels(step(r4))).toEqual(['Xây thô', 'Hoàn thiện', 'Trọn gói', 'Nội thất']);

    // Câu 5 xong -> thẻ xác nhận ngay: không hỏi chi tiết / phong cách / ngân sách / mức đầu tư.
    const r5 = await run('Xây thô');
    expect(r5.blocks.some((b) => b.type === 'question_step')).toBe(false);
    const sum = summaryOf(r5);
    expect(sum.title).toBe('Xác nhận thông tin — Nhà phố');
    expect(sum).toMatchObject({ confirm_label: CONFIRM_LABEL, confirm_value: CONFIRM_VALUE, more_label: MORE_LABEL, more_value: MORE_VALUE });
    expect(sum.items.map((i) => [i.label, i.value])).toEqual([
      ['Loại công trình', 'Nhà phố'],
      ['Khu vực', 'TP. Hồ Chí Minh'],
      ['Diện tích đất', '5 × 20 m (100 m²)'],
      ['Số tầng', '1 trệt 3 lầu (4 tầng)'],
      ['Phạm vi', 'Xây thô'],
      // Chi tiết chưa hỏi: mặc định "(tạm tính)", có nút Sửa.
      ['Diện tích xây dựng mỗi tầng', '100 m² (tạm tính)'],
      ['Tầng hầm', 'Không (tạm tính)'],
      ['Tầng lửng', 'Không (tạm tính)'],
      ['Mái', 'Mái bê tông cốt thép (tạm tính)'],
    ]);
    expect(sum.items.every((i) => !!i.edit_value)).toBe(true);
    expect(sum.assumptions).toEqual([DEFAULTS_ALL, PILE]);

    // "Cung cấp thêm thông tin": hỏi chi tiết từng câu, bộ đếm theo nhóm câu bổ sung.
    const m1 = await run(MORE_VALUE);
    expect(step(m1)).toMatchObject({
      question: 'Diện tích xây dựng mỗi tầng (m²)?',
      step: 1,
      total: 3,
      back_value: null,
      skippable: false,
      input_placeholder: 'Nhập diện tích (m²)',
    });
    expect(labels(step(m1))).toEqual(['Xây kín đất (100 m²)', 'Chưa biết']);
    const m2 = await run('Chưa biết');
    expect(step(m2)).toMatchObject({ question: 'Nhà có tầng hầm hoặc tầng lửng không?', step: 2, total: 3, back_value: BACK_VALUE });
    expect(labels(step(m2))).toEqual(['Không hầm, không lửng', 'Có tầng lửng', 'Có tầng hầm', 'Có cả hầm và lửng']);
    const m3 = await run('Có tầng hầm');
    expect(step(m3)).toMatchObject({ question: 'Loại mái dự kiến?', step: 3, total: 4 }); // có hầm -> thêm câu độ sâu
    expect(labels(step(m3))).toEqual(['Mái bằng bê tông', 'Mái tôn', 'Mái ngói kèo thép', 'Mái ngói đổ bê tông', 'Chưa biết']);
    const m4 = await run('Mái tôn');
    expect(step(m4)).toMatchObject({ question: 'Độ sâu tầng hầm?', step: 4, total: 4 });
    expect(labels(step(m4))).toEqual(['Dưới 1,3 m', '1,3–1,7 m', '1,7–2 m', 'Trên 2 m', 'Chưa biết']);
    const sum2 = summaryOf(await run('1,3–1,7 m'));
    expect(sum2.items).toContainEqual({ label: 'Tầng hầm', value: 'Có (sâu 1,5 m)', edit_value: 'Tôi muốn sửa thông tin hầm, lửng của nhà' });
    expect(sum2.items).toContainEqual({ label: 'Tầng lửng', value: 'Không', edit_value: 'Tôi muốn sửa thông tin hầm, lửng của nhà' });
    expect(sum2.items).toContainEqual({
      label: 'Diện tích xây dựng mỗi tầng',
      value: '100 m² (tạm tính)',
      edit_value: 'Tôi muốn sửa diện tích xây dựng',
    });
    expect(sum2.items).toContainEqual({ label: 'Mái', value: 'Mái tôn', edit_value: 'Tôi muốn sửa loại mái' });
    // Còn diện tích xây dựng "chưa biết" -> vẫn tạm tính, nút cung cấp thêm vẫn còn.
    expect(sum2.assumptions).toEqual([
      'Tạm tính: xây kín đất (100 m²/tầng) — anh/chị bấm Sửa hoặc Cung cấp thêm thông tin để thay đổi.',
      PILE,
    ]);
    expect(sum2.more_label).toBe(MORE_LABEL);

    // Sửa một dòng chi tiết -> hỏi lại đúng câu đó (1/1) -> về thẻ xác nhận.
    const e1 = await run('Tôi muốn sửa loại mái');
    expect(step(e1)).toMatchObject({ question: 'Loại mái dự kiến?', step: 1, total: 1, back_value: null });
    const sum3 = summaryOf(await run('Mái bằng bê tông'));
    expect(sum3.items).toContainEqual({ label: 'Mái', value: 'Mái bê tông cốt thép', edit_value: 'Tôi muốn sửa loại mái' });

    const est = (await run(CONFIRM_VALUE)).blocks.find((b) => b.type === 'estimate_table');
    expect(est && est.type === 'estimate_table' && est.data.converted_area_m2).toBe(490);
    expect(est && est.type === 'estimate_table' && est.data.cost.display).toBe('2,058–2,303 tỷ');
    expect(est && est.type === 'estimate_table' && est.data.rate.source?.label).toMatch(/^Tổng hợp đơn giá xây thô do \d+ nhà thầu/);
    const st = await client.getSession(s.session_id);
    expect(st.messages.length).toBe(1 + 2 * runs);
    // Không câu hỏi nào trong phiên hỏi phong cách / ngân sách / mức đầu tư.
    const asked = st.messages.flatMap((m) => m.ui_blocks).filter((b): b is QuestionStepBlock => b.type === 'question_step');
    expect(asked.map((q) => q.question).filter((q) => /Phong cách|Ngân sách|Mức đầu tư/.test(q))).toEqual([]);
  });

  it('phạm vi Hoàn thiện chưa có đơn giá -> bảng khái toán ghi "Chưa có dữ liệu"', () => {
    const session = newSession();
    const r = mockReply('nhà phố 5x20 Thủ Đức 4 tầng hoàn thiện, xây kín đất, không hầm không lửng, mái tôn', session);
    const sum = summaryOf(r);
    expect(sum.more_label).toBeNull(); // đủ chi tiết -> không còn gì để cung cấp thêm
    expect(sum.assumptions).toEqual([]); // không tạm tính, Hoàn thiện không nêu móng
    const r2 = mockReply('Đúng rồi', session);
    const est = r2.blocks.find((b) => b.type === 'estimate_table');
    expect(est && est.type === 'estimate_table' && est.data.cost.display).toBe('Chưa có dữ liệu');
    expect(est && est.type === 'estimate_table' && est.data.cost.status).toBe('unavailable');
  });

  it('câu đầy đủ một lần (kể cả chi tiết, ngân sách) -> thẻ xác nhận ngay; ngân sách ghi nhận, Sửa ngân sách hỏi lại 1 câu', () => {
    const session = newSession();
    const r = mockReply('nhà phố 5x20 Thủ Đức 1 trệt 3 lầu xây kín đất, không hầm không lửng, mái bằng bê tông, xây thô ngân sách 2 tỷ', session);
    const sum = summaryOf(r);
    expect(sum.items).toContainEqual({ label: 'Ngân sách', value: '2 tỷ', edit_value: 'Tôi muốn sửa ngân sách' });
    expect(sum.items).toContainEqual({ label: 'Diện tích xây dựng mỗi tầng', value: '100 m²', edit_value: 'Tôi muốn sửa diện tích xây dựng' });
    expect(sum.more_label).toBeNull();
    expect(sum.assumptions).toEqual([PILE]);
    const q = questionOf(mockReply('Tôi muốn sửa ngân sách', session))!;
    expect(q).toMatchObject({ question: 'Ngân sách dự kiến?', step: 1, total: 1, skippable: true, skip_value: SKIP_VALUE });
    // "1,5 tỷ" là câu trả lời cho câu ngân sách, không phải yêu cầu điều chỉnh ngân sách.
    const sum2 = summaryOf(mockReply('1,5 tỷ', session));
    expect(sum2.items).toContainEqual({ label: 'Ngân sách', value: '1,5 tỷ', edit_value: 'Tôi muốn sửa ngân sách' });
  });

  it('như backend: Sửa một dòng chi tiết chỉ hỏi đúng câu đó (1/1, không hỏi tiếp độ sâu hầm); "ok" giữ mặc định; lượt bổ sung chỉ đếm câu còn thiếu', () => {
    const session = newSession();
    summaryOf(mockReply('nhà phố 5x20 Thủ Đức 1 trệt 3 lầu xây thô', session));
    const q = questionOf(mockReply('Tôi muốn sửa thông tin hầm, lửng của nhà', session))!;
    expect(q).toMatchObject({ question: 'Nhà có tầng hầm hoặc tầng lửng không?', step: 1, total: 1, back_value: null });
    const sum = summaryOf(mockReply('Có tầng hầm, không lửng', session));
    expect(sum.assumptions![0]).toBe(
      'Tạm tính: xây kín đất (100 m²/tầng), hầm sâu 1,7–2 m, mái bê tông cốt thép — anh/chị bấm Sửa hoặc Cung cấp thêm thông tin để thay đổi.',
    );
    // Lượt "Cung cấp thêm thông tin": chỉ các câu còn thiếu (diện tích xây dựng, mái, độ sâu hầm) -> i/3.
    expect(questionOf(mockReply(MORE_VALUE, session))).toMatchObject({ question: 'Diện tích xây dựng mỗi tầng (m²)?', step: 1, total: 3, back_value: null });
    expect(questionOf(mockReply('ok', session))).toMatchObject({
      question: 'Loại mái dự kiến?',
      step: 2,
      total: 3,
      input_placeholder: 'Nhập câu trả lời của anh/chị', // như backend (placeholder mặc định)
    });
    expect(session.slots.building_area_m2 ?? null).toBeNull(); // "ok" = giữ mặc định, không phải xác nhận
    expect(session.slots.confirmed).toBeFalsy();
    expect(questionOf(mockReply('Mái tôn', session))).toMatchObject({ question: 'Độ sâu tầng hầm?', step: 3, total: 3 });
  });

  it('câu khách đã nêu được bỏ qua, bộ đếm giữ vị trí cố định i/5', () => {
    const session = newSession();
    const r = mockReply('Biệt thự Hà Nội 2 tầng', session);
    expect(questionOf(r)).toMatchObject({ question: 'Diện tích đất?', step: 3, total: 5 });
    expect(questionOf(mockReply('Đất 9×12 m', session))).toMatchObject({ question: 'Phạm vi thi công cần khái toán?', step: 5, total: 5 });
  });

  it('Trước (back_value) hỏi lại câu trước; Sửa (edit_value) hỏi lại đúng mục rồi về thẻ xác nhận', () => {
    const session = newSession();
    mockReply('Nhà phố', session);
    const land = mockReply('Hà Nội', session);
    expect(questionOf(land)?.question).toBe('Diện tích đất?');
    const back = mockReply(BACK_VALUE, session);
    expect(questionOf(back)).toMatchObject({ question: 'Khu vực xây dựng?', step: 2, total: 5 });
    expect(back.text).not.toMatch(/khu vực/i);
    expect(questionOf(mockReply('Đà Nẵng', session))?.question).toBe('Diện tích đất?');
    expect(questionOf(mockReply('Đất 6×12 m', session))?.question).toBe('Số tầng dự kiến?');
    expect(questionOf(mockReply('1 trệt', session))?.question).toBe('Phạm vi thi công cần khái toán?');
    expect(session.slots.floors).toBe(1);

    const s2 = newSession();
    summaryOf(mockReply('nhà phố 5x20 Thủ Đức 1 trệt 3 lầu xây thô', s2));
    const edit = mockReply('Tôi muốn sửa số tầng', s2);
    expect(questionOf(edit)).toMatchObject({ question: 'Số tầng dự kiến?', step: 4, total: 5 });
    const sum = summaryOf(mockReply('1 trệt 2 lầu', s2));
    expect(sum.items.find((i) => i.label === 'Số tầng')!.value).toBe('1 trệt 2 lầu (3 tầng)');

    // Trước trong lượt bổ sung: lùi trong nhóm câu bổ sung.
    mockReply(MORE_VALUE, s2);
    mockReply('Xây kín đất (100 m²)', s2);
    const b2 = mockReply(BACK_VALUE, s2);
    expect(questionOf(b2)).toMatchObject({ question: 'Diện tích xây dựng mỗi tầng (m²)?', step: 1, total: 3 });
    mockReply('80 m²', s2);
    expect(s2.slots).toMatchObject({ land_area_m2: 100, building_area_m2: 80 });
    // "chưa biết" (gõ hoặc chọn) giữ mặc định.
    expect(questionOf(mockReply('chưa biết', s2))?.question).toBe('Loại mái dự kiến?');
    const sum2 = summaryOf(mockReply('Tôi chưa biết loại mái', s2));
    expect(sum2.assumptions![0]).toBe('Tạm tính: không hầm, không lửng, mái bê tông cốt thép — anh/chị bấm Sửa hoặc Cung cấp thêm thông tin để thay đổi.');
  });

  it('căn hộ: câu 3 "Diện tích căn hộ (m²)?" (50…300 m²), câu 4 số tầng của căn (1, 2 duplex); không hỏi chi tiết', () => {
    const session = newSession();
    mockReply('Căn hộ', session);
    const q = questionOf(mockReply('TP. Hồ Chí Minh', session))!;
    expect(q).toMatchObject({ question: 'Diện tích căn hộ (m²)?', step: 3, total: 5 });
    expect(q.options.map((o) => o.label)).toEqual(['50 m²', '80 m²', '120 m²', '200 m²', '300 m²']);
    expect(q.input_placeholder).toBe('Nhập diện tích (m²)');
    const f = questionOf(mockReply(q.options[1]!.value, session))!;
    expect(session.slots).toMatchObject({ land_area_m2: 80 });
    expect(f).toMatchObject({ question: 'Căn hộ có mấy tầng?', step: 4, total: 5 });
    expect(f.options.map((o) => o.label)).toEqual(['1 tầng', '2 tầng (duplex)']);
    mockReply(f.options[1]!.value, session);
    expect(session.slots.floors).toBe(2);
    const sum = summaryOf(mockReply('Trọn gói', session));
    expect(sum.items.map((i) => [i.label, i.value])).toContainEqual(['Diện tích căn hộ', '80 m²']);
    expect(sum.items.map((i) => [i.label, i.value])).toContainEqual(['Số tầng', '2 tầng (duplex)']);
    expect(sum.more_label).toBeNull();
    expect(sum.assumptions).toEqual([]);
  });

  it('nhận diện diện tích dạng "100 m²"; "tìm hiểu" không bị hiểu nhầm là tìm nhà thầu; đủ 5 ý -> thẻ xác nhận', () => {
    const session = newSession();
    const r = mockReply('Tôi muốn tìm hiểu chi phí nhà phố TP.HCM đất 100 m², 4 tầng, xây thô', session);
    expect(summaryOf(r).assumptions).toEqual([DEFAULTS_ALL, PILE]);
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
    expect(await screen.findByText('Khu vực xây dựng?')).toBeInTheDocument();
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
    // Đủ 5 thông tin -> thẻ xác nhận ngay; chi tiết tạm tính theo mặc định (dòng "Tạm tính" trong Giả định).
    expect(await screen.findByText('Xác nhận thông tin — Nhà phố')).toBeInTheDocument();
    expect(screen.getByText(/^Tạm tính: xây kín đất \(100 m²\/tầng\), không hầm, không lửng, mái bê tông cốt thép/)).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: 'Tính ngay → xem bảng khái toán' }));
    expect(await screen.findByText('2,058–2,303 tỷ')).toBeInTheDocument();
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
    await screen.findByText('Khu vực xây dựng?');
    expect(sendMessage).toHaveBeenCalledTimes(1);
  });
});

describe('App (widget) — nút "Liên hệ nhân viên" dưới ô nhập', () => {
  it('luôn hiện cạnh chú thích; mở popover, Esc đóng popover mà không đóng khung chat; gửi kèm phiên + page_url', async () => {
    const mock = createMockClient({ delayMs: 0, storage: memoryStorage() });
    const createContactRequest = vi.fn(mock.createContactRequest);
    const client = fakeClient({ createSession: mock.createSession, getSession: mock.getSession, createContactRequest });
    render(<App client={client} initiallyOpen />);
    await screen.findByText(/Xin chào anh\/chị/);
    const panel = screen.getByRole('dialog', { name: 'Dezon AI Consultant' });
    const btn = screen.getByRole('button', { name: 'Liên hệ nhân viên' });
    expect(btn.closest('.dz-footrow')).toBeTruthy();
    expect(panel).toContainElement(btn);

    fireEvent.click(btn);
    const pop = screen.getByRole('dialog', { name: 'Liên hệ nhân viên Dezon' });
    expect(pop.closest('.dz-composer-area')).toBeTruthy();
    fireEvent.keyDown(screen.getByLabelText(/Họ tên/), { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Liên hệ nhân viên Dezon' })).toBeNull();
    expect(screen.getByRole('dialog', { name: 'Dezon AI Consultant' })).toBeInTheDocument();

    fireEvent.click(btn);
    fireEvent.change(screen.getByLabelText(/Họ tên/), { target: { value: 'Anh Bình' } });
    fireEvent.change(screen.getByLabelText(/Số điện thoại/), { target: { value: '0903 111 222' } });
    fireEvent.click(screen.getByRole('button', { name: 'Gửi yêu cầu liên hệ' }));
    expect(await screen.findByText(/Mã yêu cầu:/)).toHaveTextContent(/LH-[0-9A-F]{6}/);
    const body = createContactRequest.mock.calls[0]![0];
    expect(body).toMatchObject({ customer_name: 'Anh Bình', customer_phone: '0903111222', source: 'composer', contractor_id: null });
    expect(body.chat_session_id).toBeTruthy();
    expect(body.page_url).toBe(window.location.href);
    fireEvent.click(screen.getByRole('button', { name: 'Đóng' }));
    expect(screen.queryByRole('dialog', { name: 'Liên hệ nhân viên Dezon' })).toBeNull();
  });
});
