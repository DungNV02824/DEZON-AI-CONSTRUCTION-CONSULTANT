import { useState } from 'react';
import type { ContactSource, QuoteFormContractor } from '../../api/types';
import { ContactStaffForm, type ContactDone } from './ContactStaffForm';

export const CONTACT_STAFF_LABEL = 'Liên hệ nhân viên';
export const CONTACT_STAFF_SENT_LABEL = '✓ Đã gửi liên hệ';

/**
 * Nút "Liên hệ nhân viên" trên thẻ / hồ sơ nhà thầu: bấm để mở (hoặc thu gọn) form họ tên + SĐT ngay bên dưới
 * hàng nút; gửi thành công -> nút thành "✓ Đã gửi liên hệ".
 * Dùng trong hàng nút flex-wrap: form chiếm trọn một dòng bên dưới.
 */
export function ContactStaffButton({
  className = 'dz-btn dz-btn--sm',
  source = 'contractor_card',
  contractor = null,
}: {
  className?: string;
  source?: ContactSource;
  contractor?: QuoteFormContractor | null;
}) {
  const [open, setOpen] = useState(false);
  const [done, setDone] = useState<ContactDone | null>(null);
  return (
    <>
      <button type="button" className={className} aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {done ? CONTACT_STAFF_SENT_LABEL : CONTACT_STAFF_LABEL}
      </button>
      {open && (
        <div className="dz-contact-inline">
          <ContactStaffForm source={source} contractor={contractor} result={done} onSubmitted={setDone} focusOnMount />
        </div>
      )}
    </>
  );
}
