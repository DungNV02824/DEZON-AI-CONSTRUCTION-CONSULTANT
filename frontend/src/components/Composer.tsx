import { forwardRef, useState, type KeyboardEvent } from 'react';

export interface ComposerProps {
  disabled: boolean;
  busy: boolean;
  onSend: (text: string) => void;
  /** page: ô nhập dạng viên thuốc, nút gửi tròn (giao diện mở rộng / trang /planning). */
  variant?: 'panel' | 'page';
}

/** Ô nhập: Enter để gửi, Shift+Enter xuống dòng. */
export const Composer = forwardRef<HTMLTextAreaElement, ComposerProps>(function Composer({ disabled, busy, onSend, variant = 'panel' }, ref) {
  const [text, setText] = useState('');
  const canSend = !disabled && !busy && text.trim().length > 0;

  const submit = () => {
    if (!canSend) return;
    onSend(text.trim());
    setText('');
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <form
      className={`dz-composer${variant === 'page' ? ' dz-composer--page' : ''}`}
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <textarea
        ref={ref}
        className="dz-composer__input"
        rows={1}
        value={text}
        placeholder={busy ? 'Em đang trả lời…' : variant === 'page' ? 'Hỏi bất kỳ điều gì' : 'Nhập câu hỏi hoặc mô tả công trình…'}
        aria-label="Nội dung tin nhắn"
        disabled={disabled}
        maxLength={2000}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
      />
      <button type="submit" className="dz-composer__send" disabled={!canSend} aria-label="Gửi tin nhắn">
        {variant === 'page' ? (
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
            <path fill="currentColor" d="M11 20V7.83l-5.59 5.59L4 12l8-8 8 8-1.41 1.41L13 7.83V20z" />
          </svg>
        ) : (
          <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
            <path fill="currentColor" d="M3.4 20.4 21 12 3.4 3.6l-.01 6.53L15 12 3.39 13.87z" />
          </svg>
        )}
      </button>
    </form>
  );
});
