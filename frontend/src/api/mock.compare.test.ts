import { describe, expect, it } from 'vitest';
import { createMockClient } from './mock';

const FLOW = [
  'Nhà phố',
  'TP. Hồ Chí Minh',
  'Đất 5×20 m',
  '1 trệt 3 lầu',
  'Trọn gói',
  'Đúng rồi',
  'Tìm nhà thầu phù hợp',
];

describe('mock compare() theo phiên đang dùng', () => {
  it('nút "So sánh" trên danh sách nhà thầu chấm theo khái toán trọn gói của phiên, không dùng mẫu xây thô', async () => {
    const client = createMockClient({ delayMs: 0, storage: null });
    const { session_id } = await client.createSession();
    for (const content of FLOW) await client.sendMessage(session_id, content, {});
    const st = await client.getSession(session_id);
    const list = st.messages
      .flatMap((m) => m.ui_blocks)
      .find((b) => b.type === 'contractor_list');
    expect(list).toBeTruthy();
    const results = (list as Extract<typeof list, { type: 'contractor_list' }>)!.data.results;
    const ids = results.slice(0, 2).map((r) => r.contractor.id);
    const cmp = await client.compare(ids);
    const text = JSON.stringify(cmp);
    for (const r of results.slice(0, 2)) expect(text).toContain(`${r.score}/100`);
    expect(text).not.toContain('Đơn giá xây thô');
  });
});
