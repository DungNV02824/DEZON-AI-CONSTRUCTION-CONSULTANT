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
}: {
  messages: UIMessage[];
  busy: boolean;
  onRetry: (key: string) => void;
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

  // Block mới đến: nếu tin nhắn cao hơn khung nhìn, cuộn tới đầu tin nhắn để khách đọc từ đầu.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !lastKey || lastBlocks === 0) return;
    const node = el.querySelector<HTMLElement>(`[data-key="${lastKey}"]`);
    if (!node) return;
    if (node.offsetHeight > el.clientHeight - 24) el.scrollTop = node.offsetTop - 8;
    else el.scrollTop = el.scrollHeight;
  }, [lastBlocks, lastKey]);

  return (
    <div className="dz-messages" ref={scrollRef} onScroll={onScroll} role="log" aria-live="polite" aria-relevant="additions">
      {messages.map((m) => {
        const active = m.key === activeKey && !busy && !m.pending;
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
