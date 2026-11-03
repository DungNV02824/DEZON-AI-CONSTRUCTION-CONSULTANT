import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithWidget } from '../../test/utils';
import { CONTACT_INFO_KEY, CONTACT_SUBMIT_ERROR, ContactStaffForm, contactSuccessText } from './ContactStaffForm';

function fill(name: string, phone: string, note?: string) {
  fireEvent.change(screen.getByLabelText(/Họ tên/), { target: { value: name } });
  fireEvent.change(screen.getByLabelText(/Số điện thoại/), { target: { value: phone } });
  if (note !== undefined) fireEvent.change(screen.getByLabelText(/Ghi chú/), { target: { value: note } });
}

const submit = () => fireEvent.click(screen.getByRole('button', { name: 'Gửi yêu cầu liên hệ' }));

describe('ContactStaffForm', () => {
  it('nhãn trường + placeholder SĐT; tiêu đề chung khi không có nhà thầu; không xưng "em" trên nhãn', () => {
    renderWithWidget(<ContactStaffForm source="composer" />);
    expect(screen.getByRole('heading', { name: 'Liên hệ nhân viên Dezon' })).toBeInTheDocument();
    expect(screen.getByLabelText(/Số điện thoại/)).toHaveAttribute('placeholder', '0901 234 567');
    expect(screen.getByLabelText('Ghi chú (không bắt buộc)').tagName).toBe('TEXTAREA');
    expect(screen.getByLabelText('Ghi chú (không bắt buộc)')).toHaveAttribute('rows', '2');
    const form = screen.getByRole('form', { name: 'Liên hệ nhân viên Dezon' });
    expect(form.textContent).not.toMatch(/\bem\b/i);
  });

  it('kiểm tra dữ liệu: thiếu họ tên / SĐT, SĐT sai định dạng -> không gửi', () => {
    const { actions } = renderWithWidget(<ContactStaffForm source="composer" />);
    submit();
    expect(screen.getByText('Vui lòng nhập họ tên.')).toBeInTheDocument();
    expect(screen.getByText('Vui lòng nhập số điện thoại.')).toBeInTheDocument();
    expect(screen.getByLabelText(/Họ tên/)).toHaveAttribute('aria-invalid', 'true');
    fill('Chị Lan', '12345');
    submit();
    expect(screen.queryByText('Vui lòng nhập họ tên.')).toBeNull();
    expect(screen.getByText('Số điện thoại chưa đúng định dạng Việt Nam (vd 0901 234 567).')).toBeInTheDocument();
    expect(actions.submitContact).not.toHaveBeenCalled();
  });

  it('gửi thành công: SĐT chuẩn hóa, source + chat_session_id; hiện thông báo kèm số đã định dạng và mã LH-', async () => {
    const submitContact = vi.fn(async () => ({ id: 'id-1', code: 'LH-9F3A21' }));
    renderWithWidget(<ContactStaffForm source="composer" />, { submitContact, chatSessionId: 'sess-9' });
    fill('  Chị Lan  ', '0901.234.567', ' Gọi sau 17h ');
    submit();
    expect(screen.getByRole('button', { name: 'Đang gửi…' })).toBeDisabled();
    const status = await screen.findByRole('status');
    expect(status).toHaveTextContent(
      'Đã thông báo cho nhân viên Dezon. Nhân viên sẽ liên hệ anh/chị qua số 0901 234 567 trong thời gian sớm nhất.',
    );
    expect(status).toHaveTextContent('Mã yêu cầu: LH-9F3A21');
    expect(submitContact).toHaveBeenCalledWith({
      customer_name: 'Chị Lan',
      customer_phone: '0901234567',
      note: 'Gọi sau 17h',
      chat_session_id: 'sess-9',
      contractor_id: null,
      source: 'composer',
    });
    expect(JSON.parse(window.localStorage.getItem(CONTACT_INFO_KEY)!)).toEqual({ name: 'Chị Lan', phone: '0901234567' });
  });

  it('lỗi gửi -> thông báo thử lại (role=alert), giữ nguyên dữ liệu đã nhập', async () => {
    const submitContact = vi.fn(async () => {
      throw new Error('500');
    });
    renderWithWidget(<ContactStaffForm source="composer" />, { submitContact });
    fill('Anh Tâm', '0987654321');
    submit();
    expect(await screen.findByRole('alert')).toHaveTextContent(CONTACT_SUBMIT_ERROR);
    expect(screen.getByLabelText(/Họ tên/)).toHaveValue('Anh Tâm');
    expect(screen.getByRole('button', { name: 'Gửi yêu cầu liên hệ' })).toBeEnabled();
  });

  it('điền sẵn họ tên + SĐT từ lần gửi thành công trước; localStorage hỏng -> để trống', () => {
    window.localStorage.setItem(CONTACT_INFO_KEY, JSON.stringify({ name: 'Anh Hùng', phone: '0912345678' }));
    const first = renderWithWidget(<ContactStaffForm source="composer" />);
    expect(screen.getByLabelText(/Họ tên/)).toHaveValue('Anh Hùng');
    expect(screen.getByLabelText(/Số điện thoại/)).toHaveValue('0912345678');
    first.unmount();
    window.localStorage.setItem(CONTACT_INFO_KEY, '{hỏng');
    renderWithWidget(<ContactStaffForm source="composer" />);
    expect(screen.getByLabelText(/Họ tên/)).toHaveValue('');
  });

  it('"Gửi yêu cầu khác" quay lại form (khi có onAgain)', async () => {
    const onAgain = vi.fn();
    renderWithWidget(<ContactStaffForm source="composer" onAgain={onAgain} />);
    fill('Anh Tâm', '0987654321');
    submit();
    fireEvent.click(await screen.findByRole('button', { name: 'Gửi yêu cầu khác' }));
    expect(onAgain).toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Gửi yêu cầu liên hệ' })).toBeInTheDocument();
    expect(screen.getByLabelText(/Họ tên/)).toHaveValue('Anh Tâm');
  });

  it('gửi xong -> focus chuyển vào thẻ "Đã gửi" (trình đọc màn hình đọc mã; Esc của khung bật lên vẫn nhận)', async () => {
    const submitContact = vi.fn(async () => ({ id: 'id-2', code: 'LH-ABC123' }));
    renderWithWidget(<ContactStaffForm source="composer" />, { submitContact });
    fill('Anh Minh', '0901234567');
    submit();
    const ok = await screen.findByRole('status');
    expect(ok).toHaveTextContent('LH-ABC123');
    expect(document.activeElement).toBe(ok);
  });

  it('contactSuccessText định dạng cả số cố định 11 số', () => {
    expect(contactSuccessText('02838221234')).toContain('qua số 028 3822 1234');
  });
});
