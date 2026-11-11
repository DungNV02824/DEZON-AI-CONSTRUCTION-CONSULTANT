import { describe, expect, it } from 'vitest';
import {
  BUDGET_FIXTURE,
  COMPARISON_FIXTURE,
  ESTIMATE_FIXTURE,
  ESTIMATE_SOURCE_BADGES,
  MATCH_FIXTURE,
  PROFILES,
} from '../api/fixtures';
import type { EvidenceValue, SourceRef, UIBlock } from '../api/types';
import { collectAnswerSources } from './answerSources';
import { NO_DATA } from './format';

const SRC_A: SourceRef = { label: 'Hồ sơ nhà thầu A trên Dezon', url: 'https://dezon.vn/nha-thau/a', updated_at: '2026-10-31' };
const ev = (display: string, source: SourceRef | null = SRC_A, status: EvidenceValue['status'] = 'on_dezon', is_stale = false): EvidenceValue => ({
  value: display,
  display,
  status,
  source,
  is_stale,
});
const NA: EvidenceValue = { value: null, display: NO_DATA, status: 'unavailable', source: null, is_stale: false };

describe('collectAnswerSources (V2 — Nguồn dữ liệu)', () => {
  it('không có block có số liệu -> rỗng', () => {
    const r = collectAnswerSources([{ type: 'quick_replies', options: [] }]);
    expect(r).toEqual({ groups: [], missing: [], total: 0 });
    expect(collectAnswerSources(undefined).total).toBe(0);
  });

  it('bảng khái toán: đơn giá + chi phí, giữ nguyên display, nhóm theo nguồn', () => {
    const r = collectAnswerSources([{ type: 'estimate_table', data: ESTIMATE_FIXTURE }, ESTIMATE_SOURCE_BADGES]);
    const all = r.groups.flatMap((g) => g.entries);
    expect(all.map((e) => e.label)).toEqual(['Đơn giá tham chiếu', 'Chi phí sơ bộ']);
    expect(all[0]!.display).toBe(ESTIMATE_FIXTURE.rate.display);
    expect(all[1]!.display).toBe(ESTIMATE_FIXTURE.cost.display);
    expect(r.groups[0]!.source).toEqual(ESTIMATE_FIXTURE.rate.source);
    expect(r.total).toBe(2);
  });

  it('cùng giá trị ở thẻ nhà thầu và bảng so sánh chỉ liệt kê một lần; unavailable vào missing', () => {
    const blocks: UIBlock[] = [
      { type: 'contractor_list', data: MATCH_FIXTURE },
      { type: 'comparison_table', data: COMPARISON_FIXTURE },
    ];
    const r = collectAnswerSources(blocks);
    const keys = r.groups.flatMap((g) => g.entries.map((e) => `${e.context}|${e.label}|${e.display}`));
    expect(new Set(keys).size).toBe(keys.length);
    expect(r.missing.every((e) => e.display === NO_DATA && e.status === 'unavailable')).toBe(true);
    // Nhãn tiếng Việt, không in khóa thô.
    const labels = r.groups.flatMap((g) => g.entries.map((e) => e.label));
    expect(labels).toContain('Công trình ghi nhận trên Dezon');
    expect(labels.some((l) => /_/.test(l))).toBe(false);
    expect(JSON.stringify(r)).not.toContain('tốt nhất');
  });

  it('nhóm theo (nguồn, ngày, trạng thái); cờ dữ liệu cũ của nhóm; giá trị không có nguồn vào nhóm riêng', () => {
    const r = collectAnswerSources([
      {
        type: 'comparison_table',
        data: {
          contractors: [{ id: 1, slug: 'a', name: 'Nhà thầu A', profile_url: 'https://dezon.vn/nha-thau/a' }],
          rows: [
            { key: 'rough_price', label_vi: 'Đơn giá xây thô', cells: [ev('4,3–4,7 triệu/m²')] },
            { key: 'finish_price', label_vi: 'Đơn giá trọn gói', cells: [ev('7 triệu/m²', SRC_A, 'on_dezon', true)] },
            { key: 'warranty', label_vi: 'Bảo hành', cells: [ev('12 tháng', null, 'reference')] },
            { key: 'experience', label_vi: 'Kinh nghiệm', cells: [NA] },
          ],
        },
      },
    ]);
    expect(r.groups).toHaveLength(2);
    expect(r.groups[0]!.entries.map((e) => e.label)).toEqual(['Đơn giá xây thô', 'Đơn giá trọn gói']);
    expect(r.groups[0]!.isStale).toBe(true);
    expect(r.groups[0]!.entries[0]!.context).toBe('Nhà thầu A');
    expect(r.groups[1]!.source).toBeNull();
    expect(r.missing.map((e) => e.label)).toEqual(['Kinh nghiệm']);
    expect(r.total).toBe(4);
  });

  it('items của source_badges (giá trị không có block) được tính; block cũ không có items vẫn chạy', () => {
    const r = collectAnswerSources([
      {
        type: 'source_badges',
        badges: [],
        items: [
          { group: 'Tổng quan nhà thầu — Biệt thự tại TP. Hồ Chí Minh', label: 'Số nhà thầu phù hợp', evidence: ev('3 nhà thầu') },
          { group: 'Tổng quan nhà thầu — Biệt thự tại TP. Hồ Chí Minh', label: 'Khoảng đơn giá trọn gói', evidence: NA },
        ],
      },
      { type: 'source_badges', badges: [] },
    ]);
    expect(r.groups[0]!.entries[0]).toMatchObject({ label: 'Số nhà thầu phù hợp', display: '3 nhà thầu' });
    expect(r.groups[0]!.entries[0]!.context).toContain('Tổng quan nhà thầu');
    expect(r.missing).toHaveLength(1);
  });

  it('phương án ngân sách + hồ sơ nhà thầu; block hỏng không làm hỏng cả tin nhắn', () => {
    const profile = Object.values(PROFILES)[0]!;
    const r = collectAnswerSources([
      { type: 'budget_options', data: BUDGET_FIXTURE },
      { type: 'contractor_detail', data: profile },
      { type: 'contractor_list', data: null } as unknown as UIBlock,
    ]);
    const labels = r.groups.flatMap((g) => g.entries.map((e) => e.label));
    expect(labels).toContain('Ngân sách của anh/chị');
    expect(labels).toContain('Chi phí khái toán hiện tại');
    expect(r.total).toBeGreaterThan(3);
  });

  it('phạm vi không có đơn giá riêng: bỏ chi phí ước tính theo đơn giá nhà thầu (như thẻ nhà thầu)', () => {
    const data = { ...MATCH_FIXTURE, criteria: { ...MATCH_FIXTURE.criteria, work_scope: 'noi_that' as const } };
    const r = collectAnswerSources([{ type: 'contractor_list', data }]);
    const labels = [...r.groups.flatMap((g) => g.entries), ...r.missing].map((e) => e.label);
    expect(labels).not.toContain('Chi phí ước tính theo đơn giá nhà thầu');
  });
});
