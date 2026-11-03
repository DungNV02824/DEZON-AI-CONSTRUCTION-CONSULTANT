import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import type { QuestionStepBlock, QuickReply } from '../../api/types';
import { useWidget } from '../../state/context';

/** Bỏ 💡 ở đầu nếu backend đã kèm — UI tự vẽ biểu tượng. */
const cleanHint = (h: string) => h.replace(/^\s*💡\s*/u, '');

function isTextField(el: Element | null): boolean {
  return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || (el as HTMLElement).isContentEditable);
}

/**
 * Thẻ câu hỏi từng bước (block `question_step`).
 *
 * Hành vi (thống nhất cho chuột/chạm và bàn phím):
 * - Bấm một dòng lựa chọn hoặc một chip -> đánh dấu và GỬI NGAY (nhanh như quick reply cũ).
 * - Phím số 1..9 -> chỉ đánh dấu dòng tương ứng; Enter / "Tiếp" / › gửi dòng đang chọn.
 * - Gõ vào ô nhập tự do -> "Tiếp" (hoặc Enter trong ô) gửi chữ đã gõ (ưu tiên hơn dòng đang chọn).
 * - "Bỏ qua" (chỉ câu tùy chọn) gửi `skip_value`; "Trước" / ‹ gửi `back_value` (khóa ở câu đầu tiên).
 * Chỉ thẻ của tin nhắn trợ lý mới nhất còn thao tác được; thẻ cũ thu gọn, chỉ đọc.
 */
export function QuestionStep({ block, active }: { block: QuestionStepBlock; active: boolean }) {
  const { send, busy } = useWidget();
  const [selected, setSelected] = useState<number | null>(null);
  const [text, setText] = useState('');
  const rootRef = useRef<HTMLElement | null>(null);
  const titleId = useId();

  const options = block.options ?? [];
  const chips = block.chips ?? [];
  // Phím số chọn trong các dòng; câu chỉ có chip -> phím số chọn chip.
  const numbered: QuickReply[] = options.length > 0 ? options : chips;
  const disabled = !active || busy;
  const typed = text.trim();
  const canNext = !disabled && (typed.length > 0 || selected !== null);
  const counter = `${block.step}/${block.total}`;
  const skip = block.skippable && block.skip_value ? block.skip_value : null;
  const back = block.back_value || null;

  // Thẻ vừa thành câu hỏi hiện hành: nhận focus để dùng phím số ngay — trừ khi khách đang gõ ở ô nhập khác.
  useEffect(() => {
    const el = rootRef.current;
    if (!active || !el) return;
    const root = el.getRootNode() as Document | ShadowRoot;
    const current = root.activeElement;
    if (current && current !== el.ownerDocument.body && isTextField(current)) return;
    el.focus({ preventScroll: true });
  }, [active]);

  const submit = (value: string) => {
    if (disabled || !value.trim()) return;
    send(value);
  };

  const next = () => {
    if (!canNext) return;
    if (typed) submit(typed);
    else if (selected !== null && numbered[selected]) submit(numbered[selected]!.value);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    if (disabled || e.nativeEvent.isComposing) return;
    if (isTextField(e.target as Element)) return; // ô nhập tự do tự xử lý Enter
    if (/^[1-9]$/.test(e.key) && !e.altKey && !e.ctrlKey && !e.metaKey) {
      const i = Number(e.key) - 1;
      if (i < numbered.length) {
        e.preventDefault();
        setSelected(i);
        setText('');
      }
    } else if (e.key === 'Enter' && e.target === e.currentTarget) {
      // Enter trên một nút đang focus = bấm nút đó (mặc định trình duyệt); trên thẻ = Tiếp.
      e.preventDefault();
      next();
    }
  };

  if (!active) {
    return (
      <section className="dz-qstep dz-qstep--done" aria-label={`Câu hỏi ${counter}: ${block.question}`}>
        <div className="dz-qstep__head">
          <h3 className="dz-qstep__title">{block.question}</h3>
          <span className="dz-qstep__count">{counter}</span>
        </div>
      </section>
    );
  }

  const isChipNumbered = options.length === 0;

  return (
    <section
      ref={rootRef}
      className="dz-qstep"
      role="group"
      aria-labelledby={titleId}
      tabIndex={-1}
      onKeyDown={onKeyDown}
    >
      <div className="dz-qstep__head">
        <h3 className="dz-qstep__title" id={titleId}>
          {block.question}
        </h3>
        <div className="dz-qstep__nav">
          <button
            type="button"
            className="dz-qstep__arrow"
            aria-label="Câu trước"
            disabled={disabled || !back}
            onClick={() => back && submit(back)}
          >
            ‹
          </button>
          <span className="dz-qstep__count" aria-label={`Câu ${block.step} trên ${block.total}`}>
            {counter}
          </span>
          <button type="button" className="dz-qstep__arrow" aria-label="Câu tiếp theo" disabled={!canNext} onClick={next}>
            ›
          </button>
        </div>
      </div>

      {block.hint && (
        <p className="dz-qstep__hint">
          <span aria-hidden="true">💡 </span>
          {cleanHint(block.hint)}
        </p>
      )}

      {chips.length > 0 && (
        <div className="dz-qstep__chips" role="group" aria-label="Lựa chọn nhanh">
          {chips.map((c, i) => (
            <button
              key={`${c.value}-${i}`}
              type="button"
              className={`dz-qstep__chip${isChipNumbered && selected === i ? ' is-selected' : ''}`}
              aria-pressed={isChipNumbered ? selected === i : undefined}
              disabled={disabled}
              onClick={() => {
                if (isChipNumbered) setSelected(i);
                submit(c.value);
              }}
            >
              {c.label}
            </button>
          ))}
        </div>
      )}

      {(options.length > 0 || block.input_placeholder) && (
        <div className="dz-qstep__rows">
          {options.map((o, i) => (
            <button
              key={`${o.value}-${i}`}
              type="button"
              className={`dz-qstep__row${selected === i ? ' is-selected' : ''}`}
              aria-pressed={selected === i}
              disabled={disabled}
              onClick={() => {
                setSelected(i);
                submit(o.value);
              }}
            >
              <span className="dz-qstep__num" aria-hidden="true">
                {i + 1}
              </span>
              <span className="dz-qstep__label">{o.label}</span>
            </button>
          ))}
          {block.input_placeholder && (
            <label className={`dz-qstep__row dz-qstep__row--input${typed ? ' is-selected' : ''}`}>
              <span className="dz-qstep__num dz-qstep__num--icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" width="13" height="13">
                  <path
                    fill="currentColor"
                    d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zm17.71-10.21a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"
                  />
                </svg>
              </span>
              <input
                type="text"
                className="dz-qstep__input"
                value={text}
                placeholder={block.input_placeholder}
                aria-label={block.input_placeholder}
                maxLength={500}
                disabled={disabled}
                onChange={(e) => {
                  setText(e.target.value);
                  if (e.target.value.trim()) setSelected(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    next();
                  }
                }}
              />
            </label>
          )}
        </div>
      )}

      <div className="dz-qstep__foot">
        {skip ? (
          <button type="button" className="dz-qstep__skip" disabled={disabled} onClick={() => submit(skip)}>
            Bỏ qua
          </button>
        ) : (
          <span />
        )}
        <div className="dz-qstep__btns">
          <button type="button" className="dz-btn dz-btn--sm" disabled={disabled || !back} onClick={() => back && submit(back)}>
            Trước
          </button>
          <button type="button" className="dz-btn dz-btn--sm dz-btn--dark" disabled={!canNext} onClick={next}>
            Tiếp
          </button>
        </div>
      </div>
    </section>
  );
}
