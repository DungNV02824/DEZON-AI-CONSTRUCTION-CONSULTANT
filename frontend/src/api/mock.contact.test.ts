import { describe, expect, it } from 'vitest';
import type { ContactRequestBody } from './types';
import { createMockClient } from './mock';

const base: ContactRequestBody = { customer_name: 'Chị Lan', customer_phone: '0901234567', source: 'composer' };

describe('mock createContactRequest', () => {
  it('trả {id, code "LH-XXXXXX"}; cùng SĐT + phiên + nhà thầu -> trả lại yêu cầu cũ', async () => {
    const client = createMockClient({ delayMs: 0, storage: null });
    const a = await client.createContactRequest({ ...base, chat_session_id: 's1' });
    expect(a.code).toMatch(/^LH-[0-9A-F]{6}$/);
    expect(a.id).toBeTruthy();
    expect(await client.createContactRequest({ ...base, customer_phone: '+84 901 234 567', chat_session_id: 's1' })).toEqual(a);
    const other = await client.createContactRequest({ ...base, chat_session_id: 's1', contractor_id: 3 });
    expect(other.id).not.toBe(a.id);
  });

  it('kiểm tra như backend: họ tên trống, SĐT sai, source lạ -> 422', async () => {
    const client = createMockClient({ delayMs: 0, storage: null });
    await expect(client.createContactRequest({ ...base, customer_name: '  ' })).rejects.toMatchObject({ status: 422 });
    await expect(client.createContactRequest({ ...base, customer_phone: '12345' })).rejects.toMatchObject({ status: 422 });
    await expect(
      client.createContactRequest({ ...base, source: 'khac' as unknown as ContactRequestBody['source'] }),
    ).rejects.toMatchObject({ status: 422 });
  });
});
