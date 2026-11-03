/**
 * Giao diện mở rộng "AI for Planning": thanh bên (cuộc trò chuyện mới, Recent chats, đăng nhập) + khung chat rộng.
 * Dùng cho chế độ trang đầy đủ (data-mode="page", vd dezon.vn/planning/) và nút "Mở rộng" của widget nổi.
 */
import { useState, type RefObject } from 'react';
import type { ChatController } from '../state/useChat';
import { Composer } from './Composer';
import { ComposerFootnote } from './common/ContactStaffPopover';
import { MessageList } from './MessageList';
import vuathauLogo from '../assets/vuathau-logo.png';

export interface PlanningLayoutProps {
  chat: ChatController;
  isMock: boolean;
  /** Link "Đăng nhập / Đăng ký"; rỗng -> ẩn nút. */
  loginUrl: string;
  inputRef: RefObject<HTMLTextAreaElement>;
  /** Có giá trị khi đang ở chế độ "Mở rộng" của widget nổi: nút thu nhỏ về khung chat. */
  onMinimize?: () => void;
}

function ChatIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
      <path
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
        d="M5 4.5h14a1.5 1.5 0 0 1 1.5 1.5v9a1.5 1.5 0 0 1-1.5 1.5H10l-4.5 3.5v-3.5H5A1.5 1.5 0 0 1 3.5 15V6A1.5 1.5 0 0 1 5 4.5z"
      />
    </svg>
  );
}

function CollapseIcon({ flipped = false }: { flipped?: boolean }) {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" style={flipped ? { transform: 'scaleX(-1)' } : undefined}>
      <path fill="currentColor" d="M17.6 6.4 16.2 5l-7 7 7 7 1.4-1.4L12 12zm-6 0L10.2 5l-7 7 7 7 1.4-1.4L6 12z" />
    </svg>
  );
}

export function PlanningLayout({ chat, isMock, loginUrl, inputRef, onMinimize }: PlanningLayoutProps) {
  const [collapsed, setCollapsed] = useState(false); // desktop: thu gọn thanh bên
  const [drawer, setDrawer] = useState(false); // mobile: thanh bên dạng ngăn kéo
  const loading = chat.busy || chat.status === 'loading';

  const newChat = () => {
    setDrawer(false);
    void chat.newConversation();
  };
  const openChat = (id: string) => {
    setDrawer(false);
    void chat.openSession(id);
  };
  const clearAll = () => {
    if (!chat.history.length) return;
    const ok = typeof window.confirm === 'function' ? window.confirm('Xóa toàn bộ lịch sử trò chuyện trên thiết bị này?') : true;
    if (ok) void chat.clearHistory();
  };

  return (
    <div className={`dz-plan${collapsed ? ' dz-plan--collapsed' : ''}${drawer ? ' dz-plan--drawer' : ''}`}>
      <aside className="dz-plan__side" aria-label="Lịch sử trò chuyện">
        <div className="dz-plan__brand">
          {/* Logo Vua Thầu (chữ trắng, dùng trên nền navy của thanh bên) — lấy từ vuathau.com.vn. */}
          <img className="dz-plan__logo" src={vuathauLogo} alt="Vua Thầu" />
          <span className="dz-plan__product">AI for Planning</span>
          <button
            type="button"
            className="dz-plan__iconbtn"
            onClick={() => (drawer ? setDrawer(false) : setCollapsed(true))}
            aria-label="Thu gọn thanh bên"
            title="Thu gọn"
          >
            <CollapseIcon />
          </button>
        </div>

        <button type="button" className="dz-plan__new" onClick={newChat} disabled={loading}>
          <span aria-hidden="true">+</span> New chat
        </button>

        <div className="dz-plan__recent-head">
          <span>Recent chats</span>
          {chat.history.length > 0 && (
            <button type="button" className="dz-plan__clear" onClick={clearAll} disabled={loading}>
              <svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true">
                <path fill="currentColor" d="M6 19a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7H6zM19 4h-3.5l-1-1h-5l-1 1H5v2h14z" />
              </svg>
              Xóa tất cả
            </button>
          )}
        </div>

        <ul className="dz-plan__list">
          {chat.history.length === 0 && <li className="dz-plan__empty">Chưa có cuộc trò chuyện nào.</li>}
          {chat.history.map((h) => (
            <li key={h.id}>
              <button
                type="button"
                className={`dz-plan__item${h.id === chat.sessionId ? ' dz-plan__item--active' : ''}`}
                onClick={() => openChat(h.id)}
                aria-current={h.id === chat.sessionId ? 'true' : undefined}
                title={h.title}
                disabled={loading && h.id !== chat.sessionId}
              >
                <ChatIcon />
                <span className="dz-plan__item-title">{h.title}</span>
              </button>
            </li>
          ))}
        </ul>

        {loginUrl && (
          <div className="dz-plan__foot">
            <a className="dz-plan__login" href={loginUrl}>
              <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                <path
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm-7 8.5c.8-3.2 3.6-5.5 7-5.5s6.2 2.3 7 5.5"
                />
              </svg>
              Đăng nhập / Đăng ký
            </a>
          </div>
        )}
      </aside>
      {drawer && <div className="dz-plan__backdrop" onClick={() => setDrawer(false)} aria-hidden="true" />}

      <main className="dz-plan__main">
        <div className="dz-plan__topbar">
          <button
            type="button"
            className="dz-plan__iconbtn dz-plan__menu"
            onClick={() => setDrawer(true)}
            aria-label="Mở lịch sử trò chuyện"
          >
            <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
              <path fill="currentColor" d="M3 6h18v2H3zm0 5h18v2H3zm0 5h18v2H3z" />
            </svg>
          </button>
          {collapsed && (
            <button
              type="button"
              className="dz-plan__iconbtn dz-plan__expand"
              onClick={() => setCollapsed(false)}
              aria-label="Mở thanh bên"
              title="Mở thanh bên"
            >
              <CollapseIcon flipped />
            </button>
          )}
          <span className="dz-plan__toptitle">
            <span className="dz-plan__toplogo">
              <img src={vuathauLogo} alt="Vua Thầu" />
            </span>{' '}
            AI for Planning
          </span>
          {isMock && <span className="dz-mock-tag">Chế độ giả lập</span>}
          <span className="dz-plan__spacer" />
          {onMinimize && (
            <button type="button" className="dz-plan__iconbtn" onClick={onMinimize} aria-label="Thu nhỏ về khung chat" title="Thu nhỏ">
              <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                <path fill="currentColor" d="M5 16h3v3h2v-5H5zm3-8H5v2h5V5H8zm6 11h2v-3h3v-2h-5zm2-11V5h-2v5h5V8z" />
              </svg>
            </button>
          )}
        </div>

        <div className="dz-plan__chat">
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
            <MessageList key={chat.sessionId ?? ''} messages={chat.messages} busy={chat.busy} onRetry={chat.retry} variant="page" />
          )}
          <div className="dz-plan__composer">
            <Composer
              ref={inputRef}
              variant="page"
              disabled={chat.status !== 'ready'}
              busy={chat.busy}
              onSend={(t) => void chat.send(t)}
            />
            <ComposerFootnote />
          </div>
        </div>
      </main>
    </div>
  );
}
