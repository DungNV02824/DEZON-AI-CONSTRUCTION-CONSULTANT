import { useEffect, useLayoutEffect, useRef } from 'react';
import type { UIMessage } from '../state/useChat';
import { BlockRenderer } from './blocks/BlockRenderer';
import { BlockBoundary } from './common/BlockBoundary';

function TypingDots() {
  return (
    <span className="dz-typing" role="status" aria-label="Em đang trả lời">
      <span />
      <span />
      <span />
    </span>
  );
}

export function MessageList({
  messages,
  busy,
  onRetry,
  variant = 'panel',
}: {
  messages: UIMessage[];
  busy: boolean;
  onRetry: (key: string) => void;
  /** page: câu hỏi kèm lựa chọn nhanh hiển thị thành một thẻ (giao diện mở rộng). */
  variant?: 'panel' | 'page';
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const nearBottom = useRef(true);
  const lastKey = messages[messages.length - 1]?.key;
  const last = messages[messages.length - 1];
  const lastBlocks = last?.blocks.length ?? 0;
  const lastLen = last?.content.length ?? 0;
  // Tin nhắn trợ lý mới nhất (không tính tin nhắn cục bộ) mới được bấm quick replies.
  const activeKey = [...messages].reverse().find((m) => m.role === 'assistant' && !m.local)?.key;

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    nearBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  // Tin nhắn mới: cuộn xuống cuối.
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    nearBottom.current = true;
  }, [lastKey]);

  // Văn bản đang stream: bám đáy nếu người dùng đang ở gần đáy.
  useEffect(() => {
    const el = scrollRef.current;
    if (el && nearBottom.current) el.scrollTop = el.scrollHeight;
  }, [lastLen]);

  // Block mới đến: nếu tin nhắn cao hơn khung nhìn, cuộn tới ĐẦU tin nhắn để khách đọc từ đầu (lời dẫn rồi tới bảng/thẻ).
  // Vị trí tính bằng getBoundingClientRect so với khung cuộn — KHÔNG dùng offsetTop: .dz-messages không định vị nên
  // offsetTop tính theo .dz-panel (cộng thêm chiều cao header) và làm khung chat cuộn vào giữa đoạn văn.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !lastKey || lastBlocks === 0) return;
    const node = el.querySelector<HTMLElement>(`[data-key="${lastKey}"]`);
    if (!node) return;
    const rect = node.getBoundingClientRect();
    if (rect.height > el.clientHeight - 24) {
      el.scrollTop = Math.max(0, el.scrollTop + rect.top - el.getBoundingClientRect().top - 8);
      nearBottom.current = false;
    } else {
      el.scrollTop = el.scrollHeight;
    }
  }, [lastBlocks, lastKey]);

  return (
    <div className="dz-messages" ref={scrollRef} onScroll={onScroll} role="log" aria-live="polite" aria-relevant="additions">
      {messages.map((m) => {
        const active = m.key === activeKey && !busy && !m.pending;
        // Giao diện mở rộng: lượt hỏi chỉ gồm câu hỏi + lựa chọn nhanh -> một thẻ (câu hỏi ở đầu, nút dạng lưới).
        const askCard =
          variant === 'page' &&
          active &&
          m.role === 'assistant' &&
          !!m.content &&
          !m.error &&
          m.blocks.length === 1 &&
          m.blocks[0]!.type === 'quick_replies';
        if (askCard) {
          return (
            <div key={m.key} data-key={m.key} className="dz-msg dz-msg--assistant">
              <div className="dz-askcard">
                <p className="dz-askcard__q">{m.content}</p>
                <div className="dz-askcard__body">
                  <BlockBoundary>
                    <BlockRenderer block={m.blocks[0]!} active={active} />
                  </BlockBoundary>
                </div>
              </div>
            </div>
          );
        }
        return (
          <div key={m.key} data-key={m.key} className={`dz-msg dz-msg--${m.role}`}>
            {m.role === 'user' ? (
              <div className="dz-bubble dz-bubble--user">{m.content}</div>
            ) : (
              <>
                {m.content && (
                  <div className="dz-bubble dz-bubble--bot">
                    {m.content}
                    {m.pending && <span className="dz-caret" aria-hidden="true" />}
                  </div>
                )}
                {m.pending && !m.content && m.blocks.length === 0 && (
                  <div className="dz-bubble dz-bubble--bot">
                    <TypingDots />
                  </div>
                )}
                {m.blocks.length > 0 && (
                  <div className="dz-blocks">
                    {m.blocks.map((b, i) => (
                      <BlockBoundary key={i}>
                        <BlockRenderer block={b} active={active} />
                      </BlockBoundary>
                    ))}
                  </div>
                )}
                {m.error && (
                  <div className="dz-bubble dz-bubble--error" role="alert">
                    {m.error}
                    {m.retryText && (
                      <button type="button" className="dz-linkbtn" onClick={() => onRetry(m.key)}>
                        Thử lại
                      </button>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}
