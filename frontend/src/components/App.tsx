import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import type { ApiClient } from '../api/client';
import type { QuoteFormContractor } from '../api/types';
import { WidgetContext, type WidgetActions } from '../state/context';
import { useChat } from '../state/useChat';
import { Composer } from './Composer';
import { MessageList } from './MessageList';
import { SourcePopoverProvider } from './common/SourcePopover';

const OPEN_KEY = 'dezon_ai_open';

function sessionFlag(get: boolean, value?: boolean): boolean {
  try {
    if (get) return window.sessionStorage.getItem(OPEN_KEY) === '1';
    window.sessionStorage.setItem(OPEN_KEY, value ? '1' : '0');
  } catch {
    /* bị chặn */
  }
  return false;
}

export interface AppProps {
  client: ApiClient;
  initiallyOpen?: boolean;
  /** Đăng ký hàm mở/đóng cho API toàn cục window.DezonWidget. */
  registerControls?: (c: { open(): void; close(): void; toggle(): void }) => void;
}

export function App({ client, initiallyOpen = false, registerControls }: AppProps) {
  const [open, setOpen] = useState<boolean>(() => initiallyOpen || sessionFlag(true));
  const chat = useChat(client);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const launcherRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);

  const { ensureSession } = chat;
  useEffect(() => {
    sessionFlag(false, open);
    if (open) {
      void ensureSession();
      // Đưa focus vào ô nhập khi mở (không ép trên thiết bị cảm ứng để tránh bật bàn phím).
      const coarse = typeof window.matchMedia === 'function' && window.matchMedia('(pointer: coarse)').matches;
      if (!coarse) setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open, ensureSession]);

  useEffect(() => {
    registerControls?.({ open: () => setOpen(true), close: () => setOpen(false), toggle: () => setOpen((v) => !v) });
  }, [registerControls]);

  const close = useCallback(() => {
    setOpen(false);
    setTimeout(() => launcherRef.current?.focus(), 0);
  }, []);

  const { runLocal, latestMatch, estimateSessionId, send, busy } = chat;

  const actions = useMemo<WidgetActions>(
    () => ({
      isMock: client.isMock,
      busy,
      send: (text) => void send(text),
      focusInput: () => inputRef.current?.focus(),
      openContractor: (slug) =>
        void runLocal(async () => {
          const profile = await client.getContractor(slug);
          return {
            content: `Dạ, đây là hồ sơ mở rộng của ${profile.card.name} trên Dezon.`,
            blocks: [{ type: 'contractor_detail', data: profile, estimate_session_id: estimateSessionId }],
          };
        }, 'Xin lỗi, em chưa tải được hồ sơ nhà thầu. Anh/chị vui lòng thử lại sau.'),
      compare: (ids, estId) =>
        void runLocal(async () => {
          const matrix = await client.compare(ids, estId ?? estimateSessionId);
          return {
            content: 'Dạ, đây là bảng so sánh các nhà thầu anh/chị đã chọn. Ô nào chưa có dữ liệu em ghi rõ "Chưa có dữ liệu".',
            blocks: [{ type: 'comparison_table', data: matrix }],
          };
        }, 'Xin lỗi, em chưa lập được bảng so sánh. Anh/chị vui lòng thử lại sau.'),
      openQuoteForm: (contractors: QuoteFormContractor[], estId) =>
        void runLocal(
          async () => ({
            content: 'Anh/chị để lại thông tin liên hệ để em gửi yêu cầu báo giá tới nhà thầu nhé.',
            blocks: [{ type: 'quote_request_form', contractors, estimate_session_id: estId ?? estimateSessionId }],
          }),
          'Xin lỗi, em chưa mở được biểu mẫu.',
        ),
      recordClick: (contractorId, logId) => {
        const id = logId ?? latestMatch?.recommendation_log_id ?? null;
        if (id == null) return;
        if (logId == null && latestMatch && !latestMatch.results.some((r) => r.contractor.id === contractorId)) return;
        client.recordClick(id, contractorId).catch(() => undefined);
      },
      submitQuote: (body) => client.createQuoteRequest(body),
      estimateSessionId,
    }),
    [client, busy, send, runLocal, latestMatch, estimateSessionId],
  );

  const onPanelKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape' && !e.defaultPrevented) {
      const target = e.target as HTMLElement;
      // Esc trong popover nguồn do popover tự xử lý; ở đây chỉ đóng khi không có popover ghim.
      const root = panelRef.current?.getRootNode() as Document | ShadowRoot | undefined;
      if (!root?.querySelector('.dz-popover')) {
        if (target.tagName !== 'TEXTAREA' || !(target as HTMLTextAreaElement).value) close();
      }
    }
  };

  return (
    <WidgetContext.Provider value={actions}>
      <SourcePopoverProvider>
        <div className="dz-root">
          {open && (
            <div
              ref={panelRef}
              className="dz-panel"
              role="dialog"
              aria-modal="false"
              aria-label="Dezon AI Consultant"
              onKeyDown={onPanelKeyDown}
            >
              <header className="dz-header">
                <div className="dz-header__logo" aria-hidden="true">
                  D
                </div>
                <div className="dz-header__titles">
                  <h2 className="dz-header__title">Dezon AI Consultant</h2>
                  <p className="dz-header__sub">
                    Khái toán &amp; tư vấn chọn nhà thầu
                    {client.isMock && <span className="dz-mock-tag">Chế độ giả lập</span>}
                  </p>
                </div>
                <button
                  type="button"
                  className="dz-iconbtn"
                  onClick={() => void chat.newConversation()}
                  disabled={chat.busy || chat.status === 'loading'}
                  aria-label="Bắt đầu cuộc trò chuyện mới"
                  title="Cuộc trò chuyện mới"
                >
                  <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                    <path
                      fill="currentColor"
                      d="M17.65 6.35A7.96 7.96 0 0 0 12 4a8 8 0 1 0 7.73 10h-2.08A6 6 0 1 1 12 6c1.66 0 3.14.69 4.22 1.78L13 11h7V4z"
                    />
                  </svg>
                </button>
                <button type="button" className="dz-iconbtn" onClick={close} aria-label="Đóng khung chat" title="Đóng">
                  <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
                    <path fill="currentColor" d="M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
                  </svg>
                </button>
              </header>

              {chat.status === 'error' ? (
                <div className="dz-state">
                  <p>Em chưa kết nối được tới máy chủ Dezon. Anh/chị vui lòng thử lại sau ít phút.</p>
                  <button type="button" className="dz-btn dz-btn--primary" onClick={() => void chat.ensureSession()}>
                    Thử lại
                  </button>
                </div>
              ) : chat.status !== 'ready' && chat.messages.length === 0 ? (
                <div className="dz-state" role="status">
                  <span className="dz-typing" aria-hidden="true">
                    <span />
                    <span />
                    <span />
                  </span>
                  <p>Đang kết nối…</p>
                </div>
              ) : (
                <MessageList messages={chat.messages} busy={chat.busy} onRetry={chat.retry} />
              )}

              <Composer ref={inputRef} disabled={chat.status !== 'ready'} busy={chat.busy} onSend={(t) => void chat.send(t)} />
              <p className="dz-footnote">Khái toán chỉ mang tính tham khảo, không phải báo giá thi công.</p>
            </div>
          )}

          <button
            ref={launcherRef}
            type="button"
            className={`dz-launcher${open ? ' dz-launcher--open' : ''}`}
            aria-label={open ? 'Đóng trợ lý Dezon AI' : 'Mở trợ lý Dezon AI: khái toán và tìm nhà thầu'}
            aria-expanded={open}
            onClick={() => (open ? close() : setOpen(true))}
          >
            {open ? (
              <svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true">
                <path fill="currentColor" d="M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
              </svg>
            ) : (
              <svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true">
                <path fill="currentColor" d="M6 3h12a3 3 0 0 1 3 3v9a3 3 0 0 1-3 3H10l-5 4v-4.17A3 3 0 0 1 3 15V6a3 3 0 0 1 3-3z" />
                <circle className="dz-launcher__dot" cx="8" cy="10.5" r="1.3" />
                <circle className="dz-launcher__dot" cx="12" cy="10.5" r="1.3" />
                <circle className="dz-launcher__dot" cx="16" cy="10.5" r="1.3" />
              </svg>
            )}
            {!open && <span className="dz-launcher__label">Tư vấn AI</span>}
          </button>
        </div>
      </SourcePopoverProvider>
    </WidgetContext.Provider>
  );
}
