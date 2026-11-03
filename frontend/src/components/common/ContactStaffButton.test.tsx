import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithWidget } from '../../test/utils';
import { ContactStaffButton } from './ContactStaffButton';

describe('ContactStaffButton (thẻ / hồ sơ nhà thầu)', () => {
  it('bấm "Liên hệ nhân viên" -> mở form ngay bên dưới, tiêu đề nhắc tên nhà thầu, focus ô Họ tên; bấm lại thu gọn', () => {
    renderWithWidget(<ContactStaffButton source="contractor_card" contractor={{ id: 7, name: 'Kiến Phong' }} />);
    expect(screen.queryByRole('form')).toBeNull();
    const btn = screen.getByRole('button', { name: 'Liên hệ nhân viên' });
    expect(btn).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(btn);
    expect(btn).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('heading', { name: 'Nhân viên Dezon sẽ liên hệ anh/chị về Kiến Phong' })).toBeInTheDocument();
    expect(screen.getByLabelText(/Họ tên/)).toHaveFocus();
    fireEvent.click(btn);
    expect(screen.queryByLabelText(/Họ tên/)).toBeNull();
  });

  it('gửi -> submitContact kèm contractor_id + source; nút thành "✓ Đã gửi liên hệ", mở lại vẫn thấy thông báo', async () => {
    const submitContact = vi.fn(async () => ({ id: 'c1', code: 'LH-AB12CD' }));
    renderWithWidget(<ContactStaffButton source="contractor_detail" contractor={{ id: 9, name: 'BIC' }} />, {
      submitContact,
      chatSessionId: 'sess-1',
    });
    fireEvent.click(screen.getByRole('button', { name: 'Liên hệ nhân viên' }));
    fireEvent.change(screen.getByLabelText(/Họ tên/), { target: { value: 'Anh Nam' } });
    fireEvent.change(screen.getByLabelText(/Số điện thoại/), { target: { value: '+84 912 345 678' } });
    fireEvent.click(screen.getByRole('button', { name: 'Gửi yêu cầu liên hệ' }));
    expect(await screen.findByText(/Mã yêu cầu:/)).toHaveTextContent('LH-AB12CD');
    expect(submitContact).toHaveBeenCalledWith({
      customer_name: 'Anh Nam',
      customer_phone: '0912345678',
      note: null,
      chat_session_id: 'sess-1',
      contractor_id: 9,
      source: 'contractor_detail',
    });
    const btn = screen.getByRole('button', { name: '✓ Đã gửi liên hệ' });
    fireEvent.click(btn);
    expect(screen.queryByText(/Mã yêu cầu:/)).toBeNull();
    fireEvent.click(btn);
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('qua số 0912 345 678'));
  });
});
