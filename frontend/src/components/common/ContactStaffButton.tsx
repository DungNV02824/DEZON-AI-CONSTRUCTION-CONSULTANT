import { useState } from 'react';

export const CONTACT_STAFF_LABEL = 'Liên hệ nhân viên';
export const CONTACT_STAFF_NOTICE =
  'Đã thông báo cho nhân viên Dezon. Nhân viên sẽ liên hệ anh/chị trong thời gian sớm nhất.';

/**
 * Nút "Liên hệ nhân viên" — hiện CHỈ là giao diện theo yêu cầu chủ sản phẩm: bấm thì hiện thông báo,
 * chưa gửi yêu cầu nào về hệ thống (chưa có nhân viên nhận). Khi nối chức năng thật, gửi yêu cầu tại onClick.
 * Dùng trong hàng nút flex-wrap: thông báo chiếm trọn một dòng bên dưới.
 */
export function ContactStaffButton({ className = 'dz-btn dz-btn--sm' }: { className?: string }) {
  const [sent, setSent] = useState(false);
  return (
    <>
      <button type="button" className={className} onClick={() => setSent(true)} disabled={sent} aria-pressed={sent}>
        {sent ? `✓ ${CONTACT_STAFF_LABEL}` : CONTACT_STAFF_LABEL}
      </button>
      {sent && (
        <p className="dz-contact-notice" role="status">
          {CONTACT_STAFF_NOTICE}
        </p>
      )}
    </>
  );
}
