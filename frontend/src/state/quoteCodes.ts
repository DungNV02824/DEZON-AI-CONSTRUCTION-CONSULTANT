import { useCallback, useState } from 'react';
import { defaultStorage, MOCK_SESSION_KEY, quoteCodesKey, SESSION_KEY } from './useChat';

/** Mã báo giá theo danh sách nhà thầu: { [listKey]: { [ids "1,2"]: "DZ-…" } }. */
type SavedQuotes = Record<string, Record<string, string>>;

function readAll(storage: Storage | null, k: string | null): SavedQuotes {
  if (!k) return {};
  try {
    const v = JSON.parse(storage?.getItem(k) ?? '{}');
    return v && typeof v === 'object' ? (v as SavedQuotes) : {};
  } catch {
    return {};
  }
}

/**
 * Mã báo giá gửi từ form mở ngay dưới thẻ nhà thầu (không phải tin nhắn nên không đi theo tin nhắn cục bộ):
 * lưu localStorage theo phiên chat + danh sách nhà thầu để tải lại trang vẫn thấy thẻ "Đã gửi" (W-F8).
 * Chưa có phiên (render độc lập trong test) -> chỉ giữ trong bộ nhớ.
 */
export function useSavedQuoteCodes(isMock: boolean, sessionId: string | null, listKey: string) {
  const storage = defaultStorage();
  const k = sessionId ? quoteCodesKey(isMock ? MOCK_SESSION_KEY : SESSION_KEY, sessionId) : null;
  const id = `${k ?? ''}|${listKey}`;
  const [state, setState] = useState(() => ({ id, codes: readAll(storage, k)[listKey] ?? {} }));
  // Đổi phiên/danh sách mà component không mount lại -> đọc lại mã của phiên mới (không mang mã phiên cũ sang).
  if (state.id !== id) setState({ id, codes: readAll(storage, k)[listKey] ?? {} });
  const codes = state.id === id ? state.codes : {};
  const remember = useCallback(
    (ids: string, code: string) => {
      setState((prev) => ({ ...prev, codes: { ...prev.codes, [ids]: code } }));
      if (!k) return;
      try {
        const all = readAll(storage, k);
        all[listKey] = { ...(all[listKey] ?? {}), [ids]: code };
        storage?.setItem(k, JSON.stringify(all));
      } catch {
        /* localStorage bị chặn — mã vẫn hiện trong phiên tab */
      }
    },
    [storage, k, listKey],
  );
  return [codes, remember] as const;
}
