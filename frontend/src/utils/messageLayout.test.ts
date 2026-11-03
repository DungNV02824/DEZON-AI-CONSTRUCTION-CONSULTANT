import { describe, expect, it } from 'vitest';
import type { UIBlock } from '../api/types';
import { layoutMessage, type MessagePart } from './messageLayout';

const est = { type: 'estimate_table' } as unknown as UIBlock;
const list = { type: 'contractor_list' } as unknown as UIBlock;
const qr = { type: 'quick_replies', options: [] } as unknown as UIBlock;

const BUDGET_Q = 'Anh/chị cho em biết ngân sách dự kiến để em đánh giá mức phù hợp ngân sách của từng nhà thầu chính xác hơn nhé.';
const P_EST = 'Chi phí khái toán xây nhà phố trọn gói tại Hà Nội khoảng 1,395–1,917 tỷ đồng. Dựa trên đơn giá của 4 nhà thầu trên Dezon.';
const P_CON = 'Em đề xuất 5 nhà thầu phù hợp với tiêu chí của anh/chị gồm: Cao Vũ, Kiến Phong, Hoàng Minh Phát.';

/** Tóm tắt thứ tự: chữ -> 'T:<đầu đoạn>', block -> type. */
const order = (parts: MessagePart[]) => parts.map((p) => (p.kind === 'text' ? `T:${p.text.split(' ')[0]}` : p.block.type));

describe('layoutMessage — mỗi đoạn chữ đứng ngay trên block của nó', () => {
  it('khái toán + nhà thầu + câu hỏi ngân sách -> chữ khái toán, bảng, chữ nhà thầu, danh sách, câu hỏi, nút', () => {
    const parts = layoutMessage(`${P_EST}\n\n${P_CON}\n\n${BUDGET_Q}`, [est, list, qr]);
    expect(order(parts)).toEqual(['T:Chi', 'estimate_table', 'T:Em', 'contractor_list', 'T:Anh/chị', 'quick_replies']);
  });

  it('chỉ danh sách nhà thầu + câu hỏi -> chữ, danh sách, câu hỏi ngay trên nút', () => {
    const parts = layoutMessage(`${P_CON}\n\n${BUDGET_Q}`, [list, qr]);
    expect(order(parts)).toEqual(['T:Em', 'contractor_list', 'T:Anh/chị', 'quick_replies']);
  });

  it('đoạn cuối không phải câu hỏi -> giữ thứ tự cũ (không kéo đoạn giới thiệu xuống dưới danh sách)', () => {
    const parts = layoutMessage(P_CON, [list, qr]);
    expect(order(parts)).toEqual(['T:Em', 'contractor_list', 'quick_replies']);
  });

  it('câu hỏi dạng "...?" trước nút cũng được đưa xuống ngay trên nút', () => {
    const parts = layoutMessage(`${P_EST}\n\nAnh/chị muốn xem nhà thầu không?`, [est, qr]);
    expect(order(parts)).toEqual(['T:Chi', 'estimate_table', 'T:Anh/chị', 'quick_replies']);
  });

  it('chỉ có quick_replies (thẻ hỏi) hoặc không tách được -> chữ trước, block sau như cũ', () => {
    expect(order(layoutMessage(BUDGET_Q, [qr]))).toEqual(['T:Anh/chị', 'quick_replies']);
    expect(order(layoutMessage(`${P_EST} ${P_CON}`, [est, list]))).toEqual(['T:Chi', 'estimate_table', 'contractor_list']);
    expect(order(layoutMessage('', [est]))).toEqual(['estimate_table']);
    expect(order(layoutMessage('Dạ.', []))).toEqual(['T:Dạ.']);
  });

  it('gợi ý câu hỏi tiếp theo (suggestions) luôn ở cuối, sau nút chọn nhanh', () => {
    const sugg = { type: 'suggestions', options: [] } as unknown as UIBlock;
    expect(order(layoutMessage(`${P_EST}

${P_CON}`, [est, list, qr, sugg]))).toEqual([
      'T:Chi',
      'estimate_table',
      'T:Em',
      'contractor_list',
      'quick_replies',
      'suggestions',
    ]);
    expect(order(layoutMessage(`${P_CON}

${BUDGET_Q}`, [list, qr, sugg]))).toEqual([
      'T:Em',
      'contractor_list',
      'T:Anh/chị',
      'quick_replies',
      'suggestions',
    ]);
    expect(order(layoutMessage('Dạ, đây là hồ sơ.', [sugg]))).toEqual(['T:Dạ,', 'suggestions']);
  });

  it('giữ nguyên chỉ số block gốc (index) để làm key ổn định', () => {
    const parts = layoutMessage(`${P_EST}\n\n${P_CON}\n\n${BUDGET_Q}`, [est, list, qr]);
    expect(parts.filter((p) => p.kind === 'block').map((p) => (p as { index: number }).index)).toEqual([0, 1, 2]);
  });
});
