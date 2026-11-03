import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithWidget } from '../../test/utils';
import { COMPOSER_FOOTNOTE, ComposerFootnote } from './ContactStaffPopover';

const trigger = () => screen.getByRole('button', { name: 'Liên hệ nhân viên' });
const popover = () => screen.queryByRole('dialog', { name: 'Liên hệ nhân viên Dezon' });

describe('ComposerFootnote + popover "Liên hệ nhân viên"', () => {
  it('chú thích và nút nhỏ cùng một dòng; bấm nút mở popover (focus Họ tên), bấm lại đóng', () => {
    renderWithWidget(<ComposerFootnote />);
    // Dòng chú thích (COMPOSER_FOOTNOTE, có thể để trống) và nút nằm cùng một dòng .dz-footrow.
    expect(trigger()).toHaveClass('dz-contactbtn');
    expect(trigger().closest('.dz-footrow')?.querySelector('.dz-footnote')?.textContent).toBe(COMPOSER_FOOTNOTE);
    expect(popover()).toBeNull();
    fireEvent.click(trigger());
    expect(trigger()).toHaveAttribute('aria-expanded', 'true');
    expect(popover()).toBeInTheDocument();
    expect(screen.getByLabelText(/Họ tên/)).toHaveFocus();
    fireEvent.click(trigger());
    expect(popover()).toBeNull();
  });

  it('Esc đóng popover và không lan ra ngoài (khung chat không đóng theo)', () => {
    const outer = vi.fn();
    renderWithWidget(
      <div onKeyDown={(e) => e.key === 'Escape' && !e.defaultPrevented && outer()}>
        <ComposerFootnote />
      </div>,
    );
    fireEvent.click(trigger());
    fireEvent.keyDown(screen.getByLabelText(/Họ tên/), { key: 'Escape' });
    expect(popover()).toBeNull();
    expect(outer).not.toHaveBeenCalled();
  });

  it('nút × đóng popover; bấm ra ngoài đóng, bấm bên trong không đóng', () => {
    renderWithWidget(
      <div>
        <p>bên ngoài</p>
        <ComposerFootnote />
      </div>,
    );
    fireEvent.click(trigger());
    fireEvent.click(screen.getByRole('button', { name: 'Đóng' }));
    expect(popover()).toBeNull();

    fireEvent.click(trigger());
    fireEvent.pointerDown(screen.getByLabelText(/Số điện thoại/));
    expect(popover()).toBeInTheDocument();
    fireEvent.pointerDown(screen.getByText('bên ngoài'));
    expect(popover()).toBeNull();
  });

  it('gửi từ popover: source "composer", giữ thông báo thành công khi đóng rồi mở lại', async () => {
    const submitContact = vi.fn(async () => ({ id: 'x', code: 'LH-00AA11' }));
    renderWithWidget(<ComposerFootnote />, { submitContact, chatSessionId: 's-1' });
    fireEvent.click(trigger());
    fireEvent.change(screen.getByLabelText(/Họ tên/), { target: { value: 'Chị Mai' } });
    fireEvent.change(screen.getByLabelText(/Số điện thoại/), { target: { value: '0901 234 567' } });
    fireEvent.click(screen.getByRole('button', { name: 'Gửi yêu cầu liên hệ' }));
    expect(await screen.findByText(/Mã yêu cầu:/)).toHaveTextContent('LH-00AA11');
    expect(submitContact).toHaveBeenCalledWith(
      expect.objectContaining({ source: 'composer', contractor_id: null, chat_session_id: 's-1', customer_phone: '0901234567' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Đóng' }));
    fireEvent.click(trigger());
    expect(screen.getByRole('status')).toHaveTextContent('LH-00AA11');
  });
});
