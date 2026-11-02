import { describe, expect, it } from 'vitest';
import { SSEParser, readSSE, toChatStreamEvent } from './sse';
import type { ChatStreamEvent } from './types';

function streamOf(chunks: (string | Uint8Array)[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(typeof c === 'string' ? enc.encode(c) : c);
      controller.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>): Promise<ChatStreamEvent[]> {
  const out: ChatStreamEvent[] = [];
  for await (const ev of readSSE(stream)) out.push(ev);
  return out;
}

const FULL =
  'event: text\ndata: {"delta": "Dạ, "}\n\n' +
  'event: text\ndata: {"delta": "em chào anh/chị."}\n\n' +
  'event: ui_block\ndata: {"block": {"type": "quick_replies", "options": [{"label": "Nhà phố", "value": "Nhà phố"}], "allow_free_text": true}}\n\n' +
  'event: done\ndata: {"message_id": 123, "stage": "collect", "slots": {"building_type": null}, "missing_required": ["building_type"]}\n\n';

describe('SSEParser', () => {
  it('tách các sự kiện text / ui_block / done hoàn chỉnh', () => {
    const p = new SSEParser();
    const raws = p.feed(FULL);
    expect(raws.map((r) => r.event)).toEqual(['text', 'text', 'ui_block', 'done']);
    const events = raws.map(toChatStreamEvent);
    expect(events[0]).toEqual({ type: 'text', delta: 'Dạ, ' });
    expect(events[2]).toMatchObject({ type: 'ui_block', block: { type: 'quick_replies' } });
    expect(events[3]).toEqual({
      type: 'done',
      message_id: 123,
      stage: 'collect',
      slots: { building_type: null },
      missing_required: ['building_type'],
    });
  });

  it('chunk bị cắt ở mọi vị trí vẫn cho cùng kết quả', () => {
    const expected = new SSEParser().feed(FULL);
    for (let size = 1; size <= 17; size++) {
      const p = new SSEParser();
      const got = [];
      for (let i = 0; i < FULL.length; i += size) got.push(...p.feed(FULL.slice(i, i + size)));
      got.push(...p.flush());
      expect(got).toEqual(expected);
    }
  });

  it('hỗ trợ CRLF (kể cả bị cắt giữa \\r và \\n), comment, nhiều dòng data', () => {
    const p = new SSEParser();
    const a = p.feed(': keep-alive\r\nevent: text\r\ndata: {"delta":\r');
    const b = p.feed('\ndata: "x"}\r\n\r\n');
    const all = [...a, ...b];
    expect(all).toHaveLength(1);
    expect(all[0]!.data).toBe('{"delta":\n"x"}');
    expect(toChatStreamEvent(all[0]!)).toEqual({ type: 'text', delta: 'x' });
  });

  it('flush phát sự kiện cuối thiếu dòng trống kết thúc', () => {
    const p = new SSEParser();
    expect(p.feed('event: error\ndata: {"message": "Lỗi máy chủ"}')).toEqual([]);
    const tail = p.flush();
    expect(tail.map(toChatStreamEvent)).toEqual([{ type: 'error', message: 'Lỗi máy chủ' }]);
  });

  it('bỏ qua sự kiện lạ và JSON hỏng; error với data không phải JSON vẫn được báo', () => {
    expect(toChatStreamEvent({ event: 'ping', data: '{}' })).toBeNull();
    expect(toChatStreamEvent({ event: 'text', data: '{hỏng' })).toBeNull();
    expect(toChatStreamEvent({ event: 'ui_block', data: '{"block": 1}' })).toBeNull();
    expect(toChatStreamEvent({ event: 'error', data: 'timeout' })).toEqual({ type: 'error', message: 'timeout' });
  });

  it('done có khóa "type" trong payload vẫn là sự kiện done', () => {
    const ev = toChatStreamEvent({ event: 'done', data: '{"type":"x","message_id":1,"stage":"collect","slots":{},"missing_required":[]}' });
    expect(ev?.type).toBe('done');
  });
});

describe('readSSE', () => {
  it('đọc ReadableStream, ký tự tiếng Việt nhiều byte bị cắt giữa chunk không bị vỡ', async () => {
    const bytes = new TextEncoder().encode('event: text\ndata: {"delta": "Đơn giá 4,3–4,7 triệu/m²"}\n\nevent: done\ndata: {"message_id": 1, "stage": "estimate", "slots": {}, "missing_required": []}\n\n');
    // Cắt từng 3 byte -> chắc chắn cắt giữa ký tự UTF-8 ("Đ", "–", "²").
    const chunks: Uint8Array[] = [];
    for (let i = 0; i < bytes.length; i += 3) chunks.push(bytes.slice(i, i + 3));
    const events = await collect(streamOf(chunks));
    expect(events[0]).toEqual({ type: 'text', delta: 'Đơn giá 4,3–4,7 triệu/m²' });
    expect(events[1]).toMatchObject({ type: 'done', message_id: 1, stage: 'estimate' });
  });

  it('sự kiện error thay cho done', async () => {
    const events = await collect(streamOf(['event: text\ndata: {"delta":"Dạ"}\n\n', 'event: error\ndata: {"message":"LLM quá tải"}\n\n']));
    expect(events).toEqual([
      { type: 'text', delta: 'Dạ' },
      { type: 'error', message: 'LLM quá tải' },
    ]);
  });
});
