/**
 * Trạng thái hội thoại: phiên (lưu session_id vào localStorage để giữ hội thoại khi tải lại trang),
 * danh sách tin nhắn, stream văn bản dần dần và các tin nhắn cục bộ (kết quả REST như hồ sơ/so sánh).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, type ApiClient } from '../api/client';
import type { ChatMessageOut, ChatRole, MatchResult, QuoteRequestFormBlock, UIBlock } from '../api/types';

export interface UIMessage {
  key: string;
  id?: number;
  role: ChatRole;
  content: string;
  blocks: UIBlock[];
  /** Đang nhận stream / đang tải. */
  pending?: boolean;
  error?: string | null;
  /** Nội dung để gửi lại khi lỗi. */
  retryText?: string;
  /** Tin nhắn chỉ có ở trình duyệt (không lưu trên server). */
  local?: boolean;
  /** Tin nhắn cục bộ: id của tin nhắn server đứng ngay trước nó (để chèn lại đúng chỗ khi tải lại trang). */
  afterId?: number | null;
}

export type ChatStatus = 'idle' | 'loading' | 'ready' | 'error';

export const SESSION_KEY = 'dezon_ai_session_id';
export const MOCK_SESSION_KEY = 'dezon_ai_mock_session_id';

/** Khóa localStorage lưu các tin nhắn cục bộ (hồ sơ, so sánh, form/mã báo giá) của một phiên. */
export const localMessagesKey = (sessionKey: string, sessionId: string) => `${sessionKey}:local:${sessionId}`;
/** Giữ tối đa N tin nhắn cục bộ gần nhất để không vượt hạn mức localStorage. */
const MAX_LOCAL_SAVED = 20;

interface SavedLocal {
  key: string;
  afterId: number | null;
  content: string;
  blocks: UIBlock[];
}

const ERR_SEND = 'Xin lỗi, em đang gặp sự cố kết nối nên chưa trả lời được. Anh/chị vui lòng thử lại.';

let keySeq = 0;
const nextKey = () => `m${Date.now().toString(36)}${(keySeq++).toString(36)}`;

function fromServer(m: ChatMessageOut): UIMessage {
  return { key: `s${m.id}`, id: m.id, role: m.role, content: m.content ?? '', blocks: m.ui_blocks ?? [] };
}

function storageGet(storage: Storage | null, k: string): string | null {
  try {
    return storage?.getItem(k) ?? null;
  } catch {
    return null;
  }
}
function storageSet(storage: Storage | null, k: string, v: string | null) {
  try {
    if (v === null) storage?.removeItem(k);
    else storage?.setItem(k, v);
  } catch {
    /* localStorage bị chặn (chế độ riêng tư) — hội thoại chỉ giữ trong phiên tab */
  }
}

function loadLocals(storage: Storage | null, k: string): SavedLocal[] {
  const raw = storageGet(storage, k);
  if (!raw) return [];
  try {
    const arr: unknown = JSON.parse(raw);
    if (!Array.isArray(arr)) return [];
    return arr.filter(
      (x): x is SavedLocal =>
        !!x && typeof x === 'object' && typeof x.key === 'string' && typeof x.content === 'string' && Array.isArray(x.blocks),
    );
  } catch {
    return [];
  }
}

/** Id tin nhắn server cuối cùng trong danh sách (mốc để chèn tin nhắn cục bộ). */
function lastServerId(list: UIMessage[]): number | null {
  for (let i = list.length - 1; i >= 0; i--) {
    const m = list[i]!;
    if (!m.local && typeof m.id === 'number') return m.id;
  }
  return null;
}

/**
 * Ghép tin nhắn server với tin nhắn cục bộ đã lưu: mỗi tin cục bộ đứng sau tin server có id = afterId
 * (giữ thứ tự gốc); mốc không còn trong lịch sử -> xếp cuối.
 */
export function mergeLocals(server: UIMessage[], locals: SavedLocal[]): UIMessage[] {
  if (!locals.length) return server;
  const byAfter = new Map<number | null, UIMessage[]>();
  const ids = new Set(server.map((m) => m.id));
  const tail: UIMessage[] = [];
  for (const l of locals) {
    const msg: UIMessage = { key: l.key, role: 'assistant', content: l.content, blocks: l.blocks, local: true, afterId: l.afterId };
    if (l.afterId === null || !ids.has(l.afterId)) {
      tail.push(msg);
      continue;
    }
    const arr = byAfter.get(l.afterId) ?? [];
    arr.push(msg);
    byAfter.set(l.afterId, arr);
  }
  const out: UIMessage[] = [];
  for (const m of server) {
    out.push(m);
    if (m.id !== undefined) out.push(...(byAfter.get(m.id) ?? []));
  }
  return [...out, ...tail];
}

export function defaultStorage(): Storage | null {
  try {
    return typeof window !== 'undefined' ? window.localStorage : null;
  } catch {
    return null;
  }
}

export function useChat(client: ApiClient, storage: Storage | null = defaultStorage()) {
  const storageKey = client.isMock ? MOCK_SESSION_KEY : SESSION_KEY;
  const [status, setStatus] = useState<ChatStatus>('idle');
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<UIMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Khóa đồng bộ chống gửi trùng (Enter/bấm nút 2 lần trước khi state `busy` kịp render lại).
  const busyRef = useRef(false);
  const initRef = useRef<Promise<void> | null>(null);

  const patch = useCallback((key: string, fn: (m: UIMessage) => UIMessage) => {
    setMessages((prev) => prev.map((m) => (m.key === key ? fn(m) : m)));
  }, []);

  const startNew = useCallback(async () => {
    const s = await client.createSession();
    const prev = storageGet(storage, storageKey);
    if (prev && prev !== s.session_id) storageSet(storage, localMessagesKey(storageKey, prev), null);
    storageSet(storage, storageKey, s.session_id);
    setSessionId(s.session_id);
    setStage(s.stage);
    setMessages(s.messages.map(fromServer));
  }, [client, storage, storageKey]);

  /** Khởi tạo phiên (gọi khi mở khung chat lần đầu). Idempotent. */
  const ensureSession = useCallback(() => {
    if (initRef.current) return initRef.current;
    const run = async () => {
      setStatus('loading');
      const saved = storageGet(storage, storageKey);
      if (saved) {
        try {
          const s = await client.getSession(saved);
          setSessionId(s.session_id);
          setStage(s.stage);
          // Khôi phục cả tin nhắn chỉ có ở trình duyệt (hồ sơ, so sánh, mã yêu cầu báo giá).
          setMessages(mergeLocals(s.messages.map(fromServer), loadLocals(storage, localMessagesKey(storageKey, s.session_id))));
          setStatus('ready');
          return;
        } catch (e) {
          // Chỉ bỏ phiên đã lưu khi server xác nhận phiên không còn (4xx). Lỗi mạng / 5xx: giữ lại
          // session_id và báo lỗi để "Thử lại" khôi phục đúng hội thoại cũ thay vì tạo phiên mới.
          const status = e instanceof ApiError ? e.status : 0;
          if (status >= 400 && status < 500) {
            storageSet(storage, storageKey, null);
            storageSet(storage, localMessagesKey(storageKey, saved), null);
          } else {
            setStatus('error');
            initRef.current = null;
            return;
          }
        }
      }
      try {
        await startNew();
        setStatus('ready');
      } catch {
        setStatus('error');
        initRef.current = null; // cho phép thử lại
      }
    };
    initRef.current = run();
    return initRef.current;
  }, [client, storage, storageKey, startNew]);

  const send = useCallback(
    async (text: string) => {
      const content = text.trim();
      if (!content || busy || busyRef.current || !sessionId) return;
      busyRef.current = true;
      const userKey = nextKey();
      const botKey = nextKey();
      setMessages((prev) => [
        ...prev,
        { key: userKey, role: 'user', content, blocks: [] },
        { key: botKey, role: 'assistant', content: '', blocks: [], pending: true },
      ]);
      setBusy(true);
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      let failed: string | null = null;
      try {
        await client.sendMessage(
          sessionId,
          content,
          {
            onText: (delta) => patch(botKey, (m) => ({ ...m, content: m.content + delta })),
            onBlock: (block) => patch(botKey, (m) => ({ ...m, blocks: [...m.blocks, block] })),
            onDone: (done) => {
              patch(botKey, (m) => ({ ...m, id: done.message_id, pending: false }));
              setStage(done.stage);
            },
            onError: (message) => {
              failed = message || ERR_SEND;
            },
          },
          ctrl.signal,
        );
      } catch (e) {
        if ((e as { name?: string })?.name === 'AbortError') failed = null;
        else failed = ERR_SEND;
      } finally {
        abortRef.current = null;
        busyRef.current = false;
        setBusy(false);
      }
      if (failed) {
        patch(botKey, (m) => ({ ...m, pending: false, error: failed, retryText: content }));
      } else {
        patch(botKey, (m) => ({ ...m, pending: false }));
      }
    },
    [busy, sessionId, client, patch],
  );

  /** Gửi lại tin nhắn lỗi: xóa cặp tin nhắn lỗi rồi gửi lại. */
  const retry = useCallback(
    (key: string) => {
      const idx = messages.findIndex((m) => m.key === key);
      const msg = messages[idx];
      if (!msg?.retryText) return;
      const text = msg.retryText;
      setMessages((prev) => prev.filter((m, i) => !(m.key === key || (i === idx - 1 && m.role === 'user'))));
      void send(text);
    },
    [messages, send],
  );

  /** Thêm tin nhắn trợ lý cục bộ từ một thao tác REST (hồ sơ, so sánh, form báo giá). */
  const runLocal = useCallback(
    async (task: () => Promise<{ content: string; blocks: UIBlock[] }>, errorText: string) => {
      const key = nextKey();
      setMessages((prev) => [
        ...prev,
        { key, role: 'assistant', content: '', blocks: [], pending: true, local: true, afterId: lastServerId(prev) },
      ]);
      try {
        const r = await task();
        patch(key, (m) => ({ ...m, pending: false, content: r.content, blocks: r.blocks }));
      } catch {
        patch(key, (m) => ({ ...m, pending: false, error: errorText }));
      }
    },
    [patch],
  );

  /** Form báo giá gửi thành công: ghi mã vào block để thẻ thành công còn sau khi tải lại trang. */
  const markQuoteSubmitted = useCallback((block: QuoteRequestFormBlock, code: string) => {
    setMessages((prev) =>
      prev.map((m) =>
        m.blocks.includes(block) ? { ...m, blocks: m.blocks.map((b) => (b === block ? { ...block, submitted_code: code } : b)) } : m,
      ),
    );
  }, []);

  // Lưu tin nhắn cục bộ đã hoàn tất theo session_id (ghép lại khi tải lại trang).
  useEffect(() => {
    if (!sessionId) return;
    const locals: SavedLocal[] = messages
      .filter((m) => m.local && !m.pending && !m.error)
      .slice(-MAX_LOCAL_SAVED)
      .map((m) => ({ key: m.key, afterId: m.afterId ?? null, content: m.content, blocks: m.blocks }));
    const k = localMessagesKey(storageKey, sessionId);
    if (!locals.length) {
      storageSet(storage, k, null);
      return;
    }
    let json = JSON.stringify(locals);
    // Vượt hạn mức: bỏ dần tin cũ, ưu tiên giữ các tin mới nhất (thường chứa mã báo giá).
    while (locals.length > 1) {
      try {
        storage?.setItem(k, json);
        return;
      } catch {
        locals.shift();
        json = JSON.stringify(locals);
      }
    }
    storageSet(storage, k, json);
  }, [messages, sessionId, storage, storageKey]);

  const newConversation = useCallback(async () => {
    abortRef.current?.abort();
    setStatus('loading');
    try {
      await startNew();
      setStatus('ready');
    } catch {
      setStatus('error');
    }
  }, [startNew]);

  useEffect(() => () => abortRef.current?.abort(), []);

  /** estimate_session_id và MatchResult gần nhất trong hội thoại. */
  const { estimateSessionId, latestMatch } = useMemo(() => {
    let est: string | null = null;
    let match: MatchResult | null = null;
    for (const m of messages) {
      for (const b of m.blocks) {
        if (b.type === 'estimate_table' && b.data.estimate_session_id) est = b.data.estimate_session_id;
        if (b.type === 'budget_options' && b.data.estimate_session_id) est = b.data.estimate_session_id;
        if (b.type === 'contractor_list') {
          match = b.data;
          if (b.data.criteria?.estimate_session_id) est = b.data.criteria.estimate_session_id;
        }
      }
    }
    return { estimateSessionId: est, latestMatch: match };
  }, [messages]);

  return {
    status,
    sessionId,
    messages,
    busy,
    stage,
    ensureSession,
    send,
    retry,
    runLocal,
    markQuoteSubmitted,
    newConversation,
    estimateSessionId,
    latestMatch,
  };
}

export type ChatController = ReturnType<typeof useChat>;
