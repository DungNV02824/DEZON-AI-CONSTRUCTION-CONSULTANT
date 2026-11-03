import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { CONTACT_STAFF_NOTICE, ContactStaffButton } from './ContactStaffButton';

describe('ContactStaffButton (chỉ giao diện)', () => {
  it('bấm "Liên hệ nhân viên" -> hiện thông báo đã báo nhân viên Dezon, nút không bấm lại được', () => {
    render(<ContactStaffButton />);
    expect(screen.queryByRole('status')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Liên hệ nhân viên' }));
    expect(screen.getByRole('status')).toHaveTextContent(CONTACT_STAFF_NOTICE);
    expect(screen.getByRole('button', { name: /Liên hệ nhân viên/ })).toBeDisabled();
  });
});
