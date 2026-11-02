import { describe, expect, it, vi } from 'vitest';
import { STREAM_INTERRUPTED, createHttpClient } from './client';

function sseResponse(body: string, status = 200): Response {
  const enc = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      // chia đôi để mô phỏng chunk mạng
      const mid = Math.floor(body.length / 2);
      c.enqueue(enc.encode(body.slice(0, mid)));
      c.enqueue(enc.encode(body.slice(mid)));
      c.close();
    },
  });
  return new Response(stream, { status, headers: { 'Content-Type': 'text/event-stream' } });
}

const jsonResponse = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

describe('createHttpClient', () => {
  it('sendMessage: POST đúng URL/body rồi phát handlers theo thứ tự', async () => {
    const fetchMock = vi.fn(async () =>
      sseResponse(
        'event: text\ndata: {"delta":"Dạ, "}\n\nevent: text\ndata: {"delta":"chào anh/chị"}\n\n' +
          'event: ui_block\ndata: {"block":{"type":"source_badges","badges":[]}}\n\n' +
          'event: done\ndata: {"message_id":9,"stage":"collect","slots":{},"missing_required":[]}\n\n',
      ),
    );
    const client = createHttpClient('https://api.example.vn/', fetchMock as unknown as typeof fetch);
    const calls: string[] = [];
    await client.sendMessage('abc', 'Xin chào', {
      onText: (d) => calls.push(`text:${d}`),
      onBlock: (b) => calls.push(`block:${b.type}`),
      onDone: (d) => calls.push(`done:${d.message_id}`),
      onError: (m) => calls.push(`error:${m}`),
    });
    expect(calls).toEqual(['text:Dạ, ', 'text:chào anh/chị', 'block:source_badges', 'done:9']);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('https://api.example.vn/api/chat/sessions/abc/messages');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({ content: 'Xin chào' });
  });

  it('luồng đóng mà không có done -> onError "gián đoạn"', async () => {
    const client = createHttpClient('', (async () => sseResponse('event: text\ndata: {"delta":"Dạ"}\n\n')) as unknown as typeof fetch);
    const onError = vi.fn();
    await client.sendMessage('s1', 'hi', { onError });
    expect(onError).toHaveBeenCalledWith(STREAM_INTERRUPTED);
  });

  it('HTTP lỗi -> ném ApiError kèm detail', async () => {
    const client = createHttpClient('', (async () => jsonResponse({ detail: 'Không tìm thấy phiên' }, 404)) as unknown as typeof fetch);
    await expect(client.getSession('x')).rejects.toMatchObject({ name: 'ApiError', status: 404, message: 'Không tìm thấy phiên' });
  });

  it('REST: getContractor, compare, createQuoteRequest, recordClick gọi đúng endpoint', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true }));
    const client = createHttpClient('http://localhost:8000', fetchMock as unknown as typeof fetch);
    await client.getContractor('kien-phong');
    await client.compare([1, 2], 'est-1');
    await client.createQuoteRequest({ contractor_ids: [1], customer_name: 'An', customer_phone: '0901234567' });
    await client.recordClick(101, 2);
    await client.createSession();
    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][];
    expect(calls.map(([u, i]) => `${i.method} ${u}`)).toEqual([
      'GET http://localhost:8000/api/contractors/kien-phong',
      'POST http://localhost:8000/api/contractors/compare',
      'POST http://localhost:8000/api/quote-requests',
      'POST http://localhost:8000/api/recommendations/101/click',
      'POST http://localhost:8000/api/chat/sessions',
    ]);
    expect(JSON.parse(calls[1]![1].body as string)).toEqual({ contractor_ids: [1, 2], estimate_session_id: 'est-1' });
    expect(JSON.parse(calls[3]![1].body as string)).toEqual({ contractor_id: 2 });
  });
});
