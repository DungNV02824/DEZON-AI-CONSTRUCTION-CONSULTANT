import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { CONTACT_STAFF_LABEL } from './ContactStaffButton';
import { ContactStaffForm, type ContactDone } from './ContactStaffForm';

export const COMPOSER_FOOTNOTE = '';

function HeadsetIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
      <path
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M4 14v-2a8 8 0 0 1 16 0v2M4 14a2 2 0 0 1 2-2h1v6H6a2 2 0 0 1-2-2zm16 0a2 2 0 0 0-2-2h-1v6h1a2 2 0 0 0 2-2zm-3 4c0 1.7-1.8 3-4 3h-1"
      />
    </svg>
  );
}

/**
 * Nút nhỏ "Liên hệ nhân viên" (luôn hiện, tông chìm) + popover form họ tên / SĐT neo phía trên ô nhập.
 * Đóng bằng nút ×, phím Esc hoặc bấm ra ngoài. Chạy được trong Shadow DOM: chỉ dùng ref + composedPath().
 * Popover định vị `absolute` theo khối bao ô nhập gần nhất có `position: relative`.
 */
export function ContactStaffLauncher() {
  const [open, setOpen] = useState(false);
  const [done, setDone] = useState<ContactDone | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);

  const close = useCallback((refocus: boolean) => {
    setOpen(false);
    if (refocus) setTimeout(() => btnRef.current?.focus(), 0);
  }, []);

  // Bấm ra ngoài nút / popover -> đóng. composedPath() thấy cả phần tử bên trong shadow root (mode "open").
  useEffect(() => {
    if (!open) return;
    const onDown = (e: Event) => {
      const path = typeof e.composedPath === 'function' ? e.composedPath() : [];
      const inside = [btnRef.current, popRef.current].some(
        (el) => !!el && (path.includes(el) || (e.target instanceof Node && el.contains(e.target))),
      );
      if (!inside) close(false);
    };
    document.addEventListener('pointerdown', onDown, true);
    return () => document.removeEventListener('pointerdown', onDown, true);
  }, [open, close]);

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Escape' && open) {
      // Chặn để khung chat / giao diện mở rộng không đóng theo.
      e.preventDefault();
      e.stopPropagation();
      close(true);
    }
  };

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        className="dz-contactbtn"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((v) => !v)}
        onKeyDown={onKeyDown}
      >
        <HeadsetIcon />
        {CONTACT_STAFF_LABEL}
      </button>
      {open && (
        <div ref={popRef} className="dz-contactpop" role="dialog" aria-label="Liên hệ nhân viên Dezon" onKeyDown={onKeyDown}>
          <button type="button" className="dz-contactpop__close" onClick={() => close(true)} aria-label="Đóng" title="Đóng">
            <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
              <path
                fill="currentColor"
                d="M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"
              />
            </svg>
          </button>
          <ContactStaffForm
            source="composer"
            variant="plain"
            result={done}
            onSubmitted={setDone}
            onAgain={() => setDone(null)}
            focusOnMount
          />
        </div>
      )}
    </>
  );
}

/** Dòng chú thích dưới ô nhập + nút "Liên hệ nhân viên" bên phải (cả khung chat lẫn giao diện mở rộng). */
export function ComposerFootnote() {
  return (
    <div className="dz-footrow">
      <p className="dz-footnote">{COMPOSER_FOOTNOTE}</p>
      <ContactStaffLauncher />
    </div>
  );
}
