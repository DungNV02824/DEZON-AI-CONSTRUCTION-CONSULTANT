/**
 * Client REST + SSE cho backend Dezon AI Consultant (docs/API_CONTRACT.md).
 * `createApiClient` chọn client HTTP thật hoặc client giả lập (mock) theo cấu hình widget.
 */
import { readSSE } from './sse';
import type {
  ChatStreamEvent,
  ClickResponse,
  ComparisonMatrix,
  ContractorProfile,
  CreateSessionResponse,
  QuoteRequestBody,
  QuoteRequestResponse,
  SessionStateResponse,
  StreamDone,
  UIBlock,
  UUID,
} from './types';

export interface StreamHandlers {
  onText?: (delta: string) => void;
  onBlock?: (block: UIBlock) => void;
  onDone?: (done: StreamDone) => void;
  onError?: (message: string) => void;
}

export interface ApiClient {
  readonly isMock: boolean;
  createSession(userId?: string): Promise<CreateSessionResponse>;
  getSession(sessionId: UUID): Promise<SessionStateResponse>;
  /**
   * Gửi tin nhắn và đọc luồng SSE. Gọi handlers theo từng sự kiện; Promise hoàn tất khi luồng đóng.
   * Luồng đóng mà không có `done`/`error` -> gọi onError (kết nối bị gián đoạn).
   */
  sendMessage(sessionId: UUID, content: string, handlers: StreamHandlers, signal?: AbortSignal): Promise<void>;
  getContractor(slug: string): Promise<ContractorProfile>;
  compare(contractorIds: number[], estimateSessionId?: UUID | null): Promise<ComparisonMatrix>;
  createQuoteRequest(body: QuoteRequestBody): Promise<QuoteRequestResponse>;
  recordClick(logId: number, contractorId: number): Promise<ClickResponse>;
}

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export const STREAM_INTERRUPTED = 'Kết nối bị gián đoạn trước khi em trả lời xong.';

function joinUrl(base: string, path: string): string {
  return `${base.replace(/\/+$/, '')}${path}`;
}

async function errorFrom(res: Response): Promise<ApiError> {
  let message = `HTTP ${res.status}`;
  try {
    const body: unknown = await res.json();
    if (body && typeof body === 'object' && 'detail' in body) {
      const detail = (body as { detail: unknown }).detail;
      message = typeof detail === 'string' ? detail : JSON.stringify(detail);
    }
  } catch {
    /* thân phản hồi không phải JSON */
  }
  return new ApiError(res.status, message);
}

/** Phân phối một sự kiện tới handlers. Trả về true nếu là sự kiện kết thúc (done/error). */
export function dispatchStreamEvent(ev: ChatStreamEvent, h: StreamHandlers): boolean {
  switch (ev.type) {
    case 'text':
      h.onText?.(ev.delta);
      return false;
    case 'ui_block':
      h.onBlock?.(ev.block);
      return false;
    case 'done': {
      const { type: _t, ...done } = ev;
      void _t;
      h.onDone?.(done);
      return true;
    }
    case 'error':
      h.onError?.(ev.message);
      return true;
    default:
      return false;
  }
}

export function createHttpClient(apiBase: string, fetchImpl: typeof fetch = (...a) => fetch(...a)): ApiClient {
  async function json<T>(method: string, path: string, body?: unknown): Promise<T> {
    const res = await fetchImpl(joinUrl(apiBase, path), {
      method,
      headers: body !== undefined ? { 'Content-Type': 'application/json', Accept: 'application/json' } : { Accept: 'application/json' },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) throw await errorFrom(res);
    return (await res.json()) as T;
  }

  return {
    isMock: false,
    createSession: (userId) => json('POST', '/api/chat/sessions', userId ? { user_id: userId } : {}),
    getSession: (id) => json('GET', `/api/chat/sessions/${encodeURIComponent(id)}`),
    async sendMessage(sessionId, content, handlers, signal) {
      const res = await fetchImpl(joinUrl(apiBase, `/api/chat/sessions/${encodeURIComponent(sessionId)}/messages`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify({ content }),
        signal,
      });
      if (!res.ok) throw await errorFrom(res);
      if (!res.body) throw new ApiError(res.status, 'Phản hồi không có nội dung luồng');
      let finished = false;
      for await (const ev of readSSE(res.body)) {
        if (dispatchStreamEvent(ev, handlers)) finished = true;
      }
      if (!finished) handlers.onError?.(STREAM_INTERRUPTED);
    },
    getContractor: (slug) => json('GET', `/api/contractors/${encodeURIComponent(slug)}`),
    compare: (ids, estimateSessionId) =>
      json('POST', '/api/contractors/compare', {
        contractor_ids: ids,
        ...(estimateSessionId ? { estimate_session_id: estimateSessionId } : {}),
      }),
    createQuoteRequest: (body) => json('POST', '/api/quote-requests', body),
    recordClick: (logId, contractorId) =>
      json('POST', `/api/recommendations/${encodeURIComponent(String(logId))}/click`, { contractor_id: contractorId }),
  };
}
