/**
 * Bộ phân tích Server-Sent Events cho luồng POST (không dùng EventSource vì EventSource chỉ hỗ trợ GET).
 *
 * - `SSEParser` nhận các đoạn văn bản đã giải mã (có thể bị cắt ở bất kỳ vị trí nào) và trả về các
 *   sự kiện thô hoàn chỉnh { event, data } theo chuẩn WHATWG (dòng trống kết thúc một sự kiện).
 * - `toChatStreamEvent` chuyển sự kiện thô thành sự kiện chat có kiểu (text/ui_block/done/error).
 * - `readSSE` đọc ReadableStream<Uint8Array> (fetch body), giải mã UTF-8 dạng stream để không vỡ
 *   ký tự tiếng Việt nhiều byte khi bị cắt giữa chunk.
 */
import type { ChatStreamEvent, StreamDone, UIBlock } from './types';

export interface RawSSEEvent {
  event: string;
  data: string;
  id?: string;
}

export class SSEParser {
  private buffer = '';
  private eventName = '';
  private dataLines: string[] = [];
  private lastId: string | undefined;
  private sawCR = false;

  /** Nạp thêm một đoạn văn bản; trả về các sự kiện đã hoàn chỉnh. */
  feed(chunk: string): RawSSEEvent[] {
    let text = chunk;
    // Xử lý CRLF bị cắt đôi giữa hai chunk: "\r" ở cuối chunk trước, "\n" ở đầu chunk sau.
    if (this.sawCR && text.startsWith('\n')) text = text.slice(1);
    this.sawCR = text.endsWith('\r');
    this.buffer += text.replace(/\r\n?/g, '\n');

    const out: RawSSEEvent[] = [];
    let nl: number;
    while ((nl = this.buffer.indexOf('\n')) >= 0) {
      const line = this.buffer.slice(0, nl);
      this.buffer = this.buffer.slice(nl + 1);
      const ev = this.processLine(line);
      if (ev) out.push(ev);
    }
    return out;
  }

  /** Gọi khi luồng kết thúc: phát nốt sự kiện cuối nếu thiếu dòng trống kết thúc. */
  flush(): RawSSEEvent[] {
    const out: RawSSEEvent[] = [];
    if (this.buffer.length > 0) {
      const ev = this.processLine(this.buffer);
      this.buffer = '';
      if (ev) out.push(ev);
    }
    const last = this.dispatch();
    if (last) out.push(last);
    return out;
  }

  private processLine(line: string): RawSSEEvent | null {
    if (line === '') return this.dispatch();
    if (line.startsWith(':')) return null; // comment / keep-alive
    const colon = line.indexOf(':');
    let field: string;
    let value: string;
    if (colon === -1) {
      field = line;
      value = '';
    } else {
      field = line.slice(0, colon);
      value = line.slice(colon + 1);
      if (value.startsWith(' ')) value = value.slice(1);
    }
    switch (field) {
      case 'event':
        this.eventName = value;
        break;
      case 'data':
        this.dataLines.push(value);
        break;
      case 'id':
        this.lastId = value;
        break;
      default:
        break; // retry và trường lạ: bỏ qua
    }
    return null;
  }

  private dispatch(): RawSSEEvent | null {
    if (this.dataLines.length === 0) {
      this.eventName = '';
      return null;
    }
    const ev: RawSSEEvent = { event: this.eventName || 'message', data: this.dataLines.join('\n') };
    if (this.lastId !== undefined) ev.id = this.lastId;
    this.eventName = '';
    this.dataLines = [];
    return ev;
  }
}

function isObject(x: unknown): x is Record<string, unknown> {
  return typeof x === 'object' && x !== null && !Array.isArray(x);
}

/** Chuyển sự kiện thô sang sự kiện chat có kiểu. Sự kiện lạ hoặc JSON hỏng -> null (bỏ qua). */
export function toChatStreamEvent(raw: RawSSEEvent): ChatStreamEvent | null {
  let data: unknown;
  try {
    data = JSON.parse(raw.data);
  } catch {
    if (raw.event === 'error') return { type: 'error', message: raw.data };
    return null;
  }
  if (!isObject(data)) return null;
  switch (raw.event) {
    case 'text':
      return typeof data.delta === 'string' ? { type: 'text', delta: data.delta } : null;
    case 'ui_block':
      return isObject(data.block) && typeof data.block.type === 'string'
        ? { type: 'ui_block', block: data.block as unknown as UIBlock }
        : null;
    case 'done':
      // `type` đặt SAU spread để payload (nếu lỡ có khóa "type") không ghi đè loại sự kiện.
      return { ...(data as unknown as StreamDone), type: 'done' };
    case 'error':
      return { type: 'error', message: typeof data.message === 'string' ? data.message : 'Lỗi không xác định' };
    default:
      return null;
  }
}

/** Đọc toàn bộ luồng SSE, trả về từng sự kiện chat theo thứ tự. */
export async function* readSSE(stream: ReadableStream<Uint8Array>): AsyncGenerator<ChatStreamEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder('utf-8');
  const parser = new SSEParser();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      for (const raw of parser.feed(decoder.decode(value, { stream: true }))) {
        const ev = toChatStreamEvent(raw);
        if (ev) yield ev;
      }
    }
    const tail = decoder.decode();
    const rest = [...(tail ? parser.feed(tail) : []), ...parser.flush()];
    for (const raw of rest) {
      const ev = toChatStreamEvent(raw);
      if (ev) yield ev;
    }
  } finally {
    reader.releaseLock();
  }
}
