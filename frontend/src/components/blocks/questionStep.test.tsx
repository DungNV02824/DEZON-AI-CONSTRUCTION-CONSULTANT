import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { createMockClient } from '../../api/mock';
import type { QuestionStepBlock, SummaryConfirmBlock } from '../../api/types';
import { renderWithWidget } from '../../test/utils';
import { App } from '../App';
import { BlockRenderer } from './BlockRenderer';

const BUDGET: QuestionStepBlock = {
  type: 'question_step',
  question: 'Ngân sách dự kiến?',
  hint: '💡 Có ngân sách giúp em báo giá sát hơn và tự gợi ý phân khúc phù hợp ạ.',
  options: [
    { label: 'Dưới 2 tỷ', value: 'Ngân sách dưới 2 tỷ' },
    { label: '2–4 tỷ', value: 'Ngân sách 2–4 tỷ' },
    { label: '4–6 tỷ', value: 'Ngân sách 4–6 tỷ' },
  ],
  chips: [],
  input_placeholder: 'Nhập câu trả lời của anh/chị',
  step: 2,
  total: 2,
  skippable: true,
  skip_value: 'Bỏ qua câu này',
  back_value: 'Quay lại câu trước',
  slots: ['budget_vnd'],
};

const FIRST: QuestionStepBlock = {
  type: 'question_step',
  question: 'Loại công trình anh/chị dự định xây dựng?',
  hint: null,
  options: [
    { label: 'Nhà phố', value: 'Nhà phố' },
    { label: 'Biệt thự', value: 'Biệt thự' },
  ],
  input_placeholder: null,
  step: 1,
  total: 5,
  skippable: false,
  skip_value: null,
  back_value: null,
};

const LAND: QuestionStepBlock = {
  type: 'question_step',
  question: 'Diện tích đất?',
  options: [],
  chips: [
    { label: '4 × 10', value: 'Đất 4×10 m' },
    { label: '5 × 10', value: 'Đất 5×10 m' },
    { label: '12 × 10', value: 'Đất 12×10 m' },
  ],
  input_placeholder: 'Nhập ngang × dài (m) hoặc tổng m²',
  step: 3,
  total: 5,
  back_value: 'Quay lại câu trước',
};

describe('QuestionStep (question_step)', () => {
  it('tiêu đề đậm, bộ đếm i/N, gợi ý 💡, dòng đánh số 1..n, ô nhập tự do có placeholder', () => {
    renderWithWidget(<BlockRenderer block={BUDGET} active />);
    const card = screen.getByRole('group', { name: 'Ngân sách dự kiến?' });
    expect(within(card).getByRole('heading', { name: 'Ngân sách dự kiến?' })).toBeInTheDocument();
    expect(within(card).getByText('2/2')).toBeInTheDocument();
    // 💡 do UI vẽ — không lặp hai lần khi backend đã kèm.
    const hint = card.querySelector('.dz-qstep__hint')!;
    expect(hint.textContent).toBe('💡 Có ngân sách giúp em báo giá sát hơn và tự gợi ý phân khúc phù hợp ạ.');
    const rows = card.querySelectorAll('button.dz-qstep__row');
    expect([...rows].map((r) => r.textContent)).toEqual(['1Dưới 2 tỷ', '22–4 tỷ', '34–6 tỷ']);
    expect(within(card).getByRole('button', { name: '2–4 tỷ' })).toBeInTheDocument(); // số thứ tự aria-hidden
    expect(within(card).getByPlaceholderText('Nhập câu trả lời của anh/chị')).toBeInTheDocument();
    expect(within(card).getByRole('button', { name: 'Tiếp' })).toBeDisabled(); // chưa chọn gì
  });

  it('bấm một dòng -> đánh dấu và gửi value ngay', () => {
    const { actions } = renderWithWidget(<BlockRenderer block={BUDGET} active />);
    const row = screen.getByRole('button', { name: '2–4 tỷ' });
    fireEvent.click(row);
    expect(actions.send).toHaveBeenCalledWith('Ngân sách 2–4 tỷ');
    expect(row).toHaveAttribute('aria-pressed', 'true');
  });

  it('phím số chọn dòng (chưa gửi); Tiếp gửi value dòng đã chọn; Enter trên thẻ cũng là Tiếp', () => {
    const { actions } = renderWithWidget(<BlockRenderer block={BUDGET} active />);
    const card = screen.getByRole('group', { name: 'Ngân sách dự kiến?' });
    expect(document.activeElement).toBe(card); // thẻ hiện hành nhận focus để dùng phím số ngay
    fireEvent.keyDown(card, { key: '3' });
    expect(actions.send).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '4–6 tỷ' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Tiếp' }));
    expect(actions.send).toHaveBeenLastCalledWith('Ngân sách 4–6 tỷ');
    fireEvent.keyDown(card, { key: '1' });
    fireEvent.keyDown(card, { key: 'Enter' });
    expect(actions.send).toHaveBeenLastCalledWith('Ngân sách dưới 2 tỷ');
    fireEvent.keyDown(card, { key: '9' }); // ngoài phạm vi -> bỏ qua
    expect(screen.getByRole('button', { name: 'Dưới 2 tỷ' })).toHaveAttribute('aria-pressed', 'true');
    // › = Tiếp
    fireEvent.click(screen.getByRole('button', { name: 'Câu tiếp theo' }));
    expect(actions.send).toHaveBeenCalledTimes(3);
  });

  it('gõ câu trả lời tự do -> Tiếp (hoặc Enter trong ô) gửi chữ đã gõ; phím số trong ô nhập không chọn dòng', () => {
    const { actions } = renderWithWidget(<BlockRenderer block={BUDGET} active />);
    const input = screen.getByPlaceholderText('Nhập câu trả lời của anh/chị');
    fireEvent.keyDown(input, { key: '3' });
    expect(screen.getByRole('button', { name: '4–6 tỷ' })).toHaveAttribute('aria-pressed', 'false');
    fireEvent.change(input, { target: { value: '  3,5 tỷ  ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Tiếp' }));
    expect(actions.send).toHaveBeenLastCalledWith('3,5 tỷ');
    fireEvent.change(input, { target: { value: '7 tỷ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(actions.send).toHaveBeenLastCalledWith('7 tỷ');
  });

  it('"Bỏ qua" gửi skip_value; "Trước" và ‹ gửi back_value', () => {
    const { actions } = renderWithWidget(<BlockRenderer block={BUDGET} active />);
    fireEvent.click(screen.getByRole('button', { name: 'Bỏ qua' }));
    expect(actions.send).toHaveBeenLastCalledWith('Bỏ qua câu này');
    fireEvent.click(screen.getByRole('button', { name: 'Trước' }));
    expect(actions.send).toHaveBeenLastCalledWith('Quay lại câu trước');
    fireEvent.click(screen.getByRole('button', { name: 'Câu trước' }));
    expect(actions.send).toHaveBeenCalledTimes(3);
  });

  it('câu đầu tiên: Trước / ‹ bị khóa; câu bắt buộc không có "Bỏ qua"; không có ô nhập khi input_placeholder null', () => {
    renderWithWidget(<BlockRenderer block={FIRST} active />);
    expect(screen.getByRole('button', { name: 'Trước' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Câu trước' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Bỏ qua' })).toBeNull();
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(screen.getByText('1/5')).toBeInTheDocument();
  });

  it('chip gọn (kích thước đất): bấm gửi value; phím số chọn chip khi câu chỉ có chip', () => {
    const { actions } = renderWithWidget(<BlockRenderer block={LAND} active />);
    const chips = screen.getByRole('group', { name: 'Lựa chọn nhanh' });
    expect(within(chips).getAllByRole('button').map((b) => b.textContent)).toEqual(['4 × 10', '5 × 10', '12 × 10']);
    fireEvent.click(within(chips).getByRole('button', { name: '12 × 10' }));
    expect(actions.send).toHaveBeenLastCalledWith('Đất 12×10 m');
    fireEvent.keyDown(screen.getByRole('group', { name: LAND.question }), { key: '2' });
    fireEvent.click(screen.getByRole('button', { name: 'Tiếp' }));
    expect(actions.send).toHaveBeenLastCalledWith('Đất 5×10 m');
  });

  it('thẻ cũ (không còn active) thu gọn, chỉ đọc; đang chờ trả lời -> mọi nút bị khóa', () => {
    const { unmount } = renderWithWidget(<BlockRenderer block={BUDGET} active={false} />);
    expect(screen.getByText('Ngân sách dự kiến?')).toBeInTheDocument();
    expect(screen.queryAllByRole('button')).toHaveLength(0);
    expect(screen.queryByRole('textbox')).toBeNull();
    unmount();
    renderWithWidget(<BlockRenderer block={BUDGET} active />, { busy: true });
    for (const b of screen.getAllByRole('button')) expect(b).toBeDisabled();
  });
});

const SUMMARY_V2: SummaryConfirmBlock = {
  type: 'summary_confirm',
  title: 'Xác nhận thông tin — Nhà phố',
  items: [
    { label: 'Loại công trình', value: 'Nhà phố', edit_value: 'Tôi muốn sửa loại công trình' },
    { label: 'Số tầng', value: '4 tầng (1 trệt 3 lầu)', edit_value: 'Tôi muốn sửa số tầng' },
    { label: 'Mái', value: 'Mái tôn', edit_value: 'Tôi muốn sửa loại mái' },
  ],
  assumptions: [
    'Tạm tính: xây kín đất (100 m²/tầng), không hầm, không lửng — anh/chị bấm Sửa hoặc Cung cấp thêm thông tin để thay đổi.',
    'Móng tính theo thông lệ: móng cọc (nhà từ 4 tầng nổi trở lên), chưa gồm chi phí ép cọc.',
  ],
  confirm: { label: 'Đúng rồi', value: 'Đúng rồi, tính khái toán giúp tôi' },
  edit: { label: 'Sửa lại', value: 'Tôi muốn sửa lại thông tin' },
  confirm_label: 'Tính ngay → xem bảng khái toán',
  confirm_value: 'Đúng rồi, tính khái toán giúp tôi',
  more_label: 'Cung cấp thêm thông tin để báo giá chính xác hơn',
  more_value: 'Tôi muốn cung cấp thêm thông tin',
};

describe('SummaryConfirm — thẻ xác nhận', () => {
  it('tiêu đề, bảng nhãn | giá trị | Sửa; Sửa gửi edit_value của dòng; giả định vẫn hiện', () => {
    const { actions } = renderWithWidget(<BlockRenderer block={SUMMARY_V2} active />);
    expect(screen.getByRole('heading', { name: 'Xác nhận thông tin — Nhà phố' })).toBeInTheDocument();
    expect(screen.getByRole('rowheader', { name: 'Số tầng' })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /^Sửa / })).toHaveLength(3);
    fireEvent.click(screen.getByRole('button', { name: 'Sửa số tầng' }));
    expect(actions.send).toHaveBeenLastCalledWith('Tôi muốn sửa số tầng');
    expect(screen.getByText('Giả định')).toBeInTheDocument();
    expect(screen.getByText(/móng cọc/)).toBeInTheDocument();
    expect(screen.getByText(/^Tạm tính: xây kín đất \(100 m²\/tầng\)/)).toBeInTheDocument();
    // Có Sửa từng dòng -> không còn nút "Sửa lại" chung.
    expect(screen.queryByRole('button', { name: 'Sửa lại' })).toBeNull();
  });

  it('nút lớn "Tính ngay → xem bảng khái toán" gửi confirm_value; nút "Cung cấp thêm thông tin..." gửi more_value', () => {
    const { actions } = renderWithWidget(<BlockRenderer block={SUMMARY_V2} active />);
    fireEvent.click(screen.getByRole('button', { name: 'Tính ngay → xem bảng khái toán' }));
    expect(actions.send).toHaveBeenLastCalledWith('Đúng rồi, tính khái toán giúp tôi');
    fireEvent.click(screen.getByRole('button', { name: 'Cung cấp thêm thông tin để báo giá chính xác hơn' }));
    expect(actions.send).toHaveBeenLastCalledWith('Tôi muốn cung cấp thêm thông tin');
  });

  it('không có more_label -> ẩn nút cung cấp thêm; thẻ cũ -> nút bị khóa', () => {
    renderWithWidget(<BlockRenderer block={{ ...SUMMARY_V2, more_label: null, more_value: null }} active={false} />);
    expect(screen.queryByRole('button', { name: /Cung cấp thêm/ })).toBeNull();
    expect(screen.getByRole('button', { name: 'Tính ngay → xem bảng khái toán' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Sửa số tầng' })).toBeDisabled();
  });

  it('payload cũ (không có trường mới) -> tiêu đề "Thông tin công trình", nút "Đúng rồi" / "Sửa lại", không có cột Sửa', () => {
    const { actions } = renderWithWidget(
      <BlockRenderer
        block={{
          type: 'summary_confirm',
          items: [{ label: 'Loại công trình', value: 'Nhà phố' }],
          confirm: { label: 'Đúng rồi', value: 'Đúng rồi, tính khái toán giúp tôi' },
          edit: { label: 'Sửa lại', value: 'Tôi muốn sửa lại thông tin' },
        }}
        active
      />,
    );
    expect(screen.getByRole('heading', { name: 'Thông tin công trình' })).toBeInTheDocument();
    expect(document.querySelector('.dz-sumtable__editbtn')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Sửa lại' }));
    expect(actions.send).toHaveBeenLastCalledWith('Tôi muốn sửa lại thông tin');
    fireEvent.click(screen.getByRole('button', { name: 'Đúng rồi' }));
    expect(actions.send).toHaveBeenLastCalledWith('Đúng rồi, tính khái toán giúp tôi');
  });
});

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

const activeCard = () => document.querySelector<HTMLElement>('.dz-qstep:not(.dz-qstep--done)');

describe('chế độ giả lập — luồng thẻ câu hỏi đầu-cuối', () => {
  it('5 câu i/5 (chuột + bàn phím + ô nhập + Trước) -> thẻ xác nhận -> Cung cấp thêm (i/3) -> Sửa -> Tính ngay', async () => {
    window.localStorage.clear();
    render(<App client={createMockClient({ delayMs: 0, storage: memoryStorage() })} initiallyOpen />);
    await screen.findByText(/Xin chào anh\/chị/);
    const waitQ = async (q: string, counter: string) => {
      await waitFor(() => expect(activeCard()?.querySelector('.dz-qstep__title')?.textContent).toBe(q));
      const card = activeCard()!;
      expect(card.querySelector('.dz-qstep__count')?.textContent).toBe(counter);
      return card;
    };
    let card = await waitQ('Loại công trình anh/chị dự định xây dựng?', '1/5');
    fireEvent.click(within(card).getByRole('button', { name: 'Nhà phố' }));

    // Bàn phím: phím số chọn, Enter = Tiếp.
    card = await waitQ('Khu vực xây dựng?', '2/5');
    fireEvent.keyDown(card, { key: '1' });
    expect(within(card).getByRole('button', { name: 'TP. Hồ Chí Minh' })).toHaveAttribute('aria-pressed', 'true');
    await act(async () => {
      fireEvent.keyDown(card, { key: 'Enter' });
    });

    card = await waitQ('Diện tích đất?', '3/5');
    // Trước -> hỏi lại khu vực.
    fireEvent.click(within(card).getByRole('button', { name: 'Trước' }));
    card = await waitQ('Khu vực xây dựng?', '2/5');
    fireEvent.click(within(card).getByRole('button', { name: 'Hà Nội' }));
    card = await waitQ('Diện tích đất?', '3/5');
    fireEvent.click(within(card).getByRole('button', { name: '5 × 10' }));

    // Ô nhập tự do.
    card = await waitQ('Số tầng dự kiến?', '4/5');
    fireEvent.change(within(card).getByPlaceholderText('Nhập số tầng (vd 1 trệt 3 lầu)'), { target: { value: '1 trệt 2 lầu' } });
    fireEvent.click(within(card).getByRole('button', { name: 'Tiếp' }));

    card = await waitQ('Phạm vi thi công cần khái toán?', '5/5');
    expect(within(card).queryByRole('button', { name: 'Bỏ qua' })).toBeNull(); // câu cốt lõi không bỏ qua được
    fireEvent.click(within(card).getByRole('button', { name: 'Xây thô' }));

    // Hết 5 câu -> thẻ xác nhận gọn: 4 dòng chính (loại công trình nằm ở tiêu đề), dòng "Tạm tính" trong Giả định.
    const sum = await screen.findByRole('region', { name: 'Xác nhận thông tin — Nhà phố' });
    const main = sum.querySelector('.dz-sumtable') as HTMLElement;
    expect([...main.querySelectorAll('th')].map((th) => th.textContent)).toEqual(['Khu vực', 'Diện tích đất', 'Số tầng', 'Phạm vi']);
    expect(
      within(sum).getByText(
        'Tạm tính: xây kín đất (50 m²/tầng), không hầm, không lửng, mái bê tông cốt thép — anh/chị bấm Sửa hoặc Cung cấp thêm thông tin để thay đổi.',
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Phong cách|Mức đầu tư|Ngân sách dự kiến/)).toBeNull();
    // Văn bản trợ lý không lặp câu hỏi của thẻ.
    expect(screen.queryByText(/khu vực nào/)).toBeNull();

    // Cung cấp thêm thông tin -> câu chi tiết từng thẻ, bộ đếm theo nhóm câu bổ sung.
    fireEvent.click(within(sum).getByRole('button', { name: 'Cung cấp thêm thông tin để báo giá chính xác hơn' }));
    card = await waitQ('Diện tích xây dựng mỗi tầng (m²)?', '1/3');
    expect(within(card).getByRole('button', { name: 'Xây kín đất (50 m²)' })).toBeInTheDocument();
    fireEvent.click(within(card).getByRole('button', { name: 'Chưa biết' }));
    card = await waitQ('Nhà có tầng hầm hoặc tầng lửng không?', '2/3');
    fireEvent.click(within(card).getByRole('button', { name: 'Không hầm, không lửng' }));
    card = await waitQ('Loại mái dự kiến?', '3/3');
    fireEvent.click(within(card).getByRole('button', { name: 'Mái tôn' }));

    await waitFor(() => expect(screen.getAllByRole('region', { name: 'Xác nhận thông tin — Nhà phố' })).toHaveLength(2));
    const sum2 = screen.getAllByRole('region', { name: 'Xác nhận thông tin — Nhà phố' })[1]!;
    expect(within(sum2).getByText('Mái tôn')).toBeInTheDocument();
    expect(
      within(sum2).getByText('Tạm tính: xây kín đất (50 m²/tầng) — anh/chị bấm Sửa hoặc Cung cấp thêm thông tin để thay đổi.'),
    ).toBeInTheDocument();

    // Sửa một dòng -> hỏi lại đúng câu đó (vị trí cố định 4/5) -> về thẻ xác nhận.
    fireEvent.click(within(sum2).getByRole('button', { name: 'Sửa số tầng' }));
    card = await waitQ('Số tầng dự kiến?', '4/5');
    fireEvent.click(within(card).getByRole('button', { name: '1 trệt 3 lầu' }));
    await waitFor(() => expect(screen.getAllByRole('region', { name: 'Xác nhận thông tin — Nhà phố' })).toHaveLength(3));
    const sum3 = screen.getAllByRole('region', { name: 'Xác nhận thông tin — Nhà phố' })[2]!;
    expect(within(sum3).getByText('1 trệt 3 lầu (4 tầng)')).toBeInTheDocument();
    fireEvent.click(within(sum3).getByRole('button', { name: 'Tính ngay → xem bảng khái toán' }));
    expect(await screen.findByText(/^Dạ, em đã tính/, { selector: '.dz-bubble--bot' })).toBeInTheDocument();
  });
});
