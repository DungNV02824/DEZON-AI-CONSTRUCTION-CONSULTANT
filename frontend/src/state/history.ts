/**
 * Lịch sử các cuộc trò chuyện ("Recent chats") lưu ở localStorage của trình duyệt.
 * Khách chưa đăng nhập nên lịch sử chỉ có trên thiết bị này; nội dung thật vẫn nằm ở server (GET session).
 */

export interface ChatHistoryItem {
  id: string;
  /** Câu hỏi đầu tiên của khách (rút gọn). */
  title: string;
  updatedAt: number;
}

export const MAX_HISTORY = 30;
const TITLE_MAX = 80;

export const historyKey = (sessionKey: string) => `${sessionKey}:history`;

export function makeTitle(text: string): string {
  const t = text.replace(/\s+/g, ' ').trim();
  return t.length > TITLE_MAX ? `${t.slice(0, TITLE_MAX - 1)}…` : t;
}

export function loadHistory(storage: Storage | null, sessionKey: string): ChatHistoryItem[] {
  try {
    const raw = storage?.getItem(historyKey(sessionKey));
    if (!raw) return [];
    const arr: unknown = JSON.parse(raw);
    if (!Array.isArray(arr)) return [];
    return arr
      .filter(
        (x): x is ChatHistoryItem =>
          !!x && typeof x === 'object' && typeof x.id === 'string' && typeof x.title === 'string' && typeof x.updatedAt === 'number',
      )
      .slice(0, MAX_HISTORY);
  } catch {
    return [];
  }
}

export function saveHistory(storage: Storage | null, sessionKey: string, items: ChatHistoryItem[]): void {
  try {
    if (!items.length) storage?.removeItem(historyKey(sessionKey));
    else storage?.setItem(historyKey(sessionKey), JSON.stringify(items.slice(0, MAX_HISTORY)));
  } catch {
    /* localStorage bị chặn — lịch sử chỉ còn trong bộ nhớ của tab */
  }
}

/** Thêm/cập nhật một cuộc trò chuyện và đưa lên đầu danh sách. Giữ tiêu đề cũ nếu đã có. */
export function upsertHistory(items: ChatHistoryItem[], id: string, title: string, now: number): ChatHistoryItem[] {
  const existing = items.find((i) => i.id === id);
  const item: ChatHistoryItem = { id, title: existing?.title || makeTitle(title), updatedAt: now };
  return [item, ...items.filter((i) => i.id !== id)].slice(0, MAX_HISTORY);
}
