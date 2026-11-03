/**
 * Review sửa lỗi — frontend (#1/#16 nhãn, #17, #18, #27, #28, #29 phần mock/fallback).
 * Mọi chuỗi kiểm tra ở đây khớp nguyên văn đầu ra backend (dialog.default_assumptions, calculate_estimate,
 * suggest_adjustments, market_rates.market_label) — mock không được tự tạo phương án / nguồn khác backend.
 */
import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import {
  BUDGET_FIXTURE,
  COMPARISON_FIXTURE,
  ESTIMATE_FIXTURE,
  MATCH_FIXTURE,
  PROFILES,
  estimateFixtureFor,
  estimateSourceBadgesFor,
  matchFixtureFor,
} from '../../api/fixtures';
import { detailsApply, mockReply } from '../../api/mock';
import type { EstimateResult, Slots, UIBlock } from '../../api/types';
import { questionOf } from '../../test/mockFlow';
import { renderWithWidget } from '../../test/utils';
import { FINISH_PRICE_LABEL_VI, NO_RATE_DISCLAIMER } from '../../utils/labels';
import { BlockRenderer } from './BlockRenderer';
import { ContractorCardView } from './ContractorCard';
import { ContractorDetail } from './ContractorDetail';
import { EstimateTable, fallbackDisclaimer } from './EstimateTable';

const newSession = () => ({ session_id: 'x', stage: 'collect', slots: {} as Slots, messages: [], nextId: 1 });
const find = <T extends UIBlock['type']>(blocks: UIBlock[], type: T) =>
  blocks.find((b): b is Extract<UIBlock, { type: T }> => b.type === type);

// ---------------------------------------------------------------- #1 / #16

describe('#1/#16 — đơn giá hoàn thiện cơ bản = TRỌN GÓI; phạm vi Hoàn thiện không có chi phí ước', () => {
  it('thẻ nhà thầu ghi "Đơn giá trọn gói (hoàn thiện cơ bản)", không còn nhãn "Đơn giá hoàn thiện"', () => {
    renderWithWidget(<ContractorCardView item={MATCH_FIXTURE.results[0]!} workScope="xay_tho" />);
    expect(FINISH_PRICE_LABEL_VI).toBe('Đơn giá trọn gói (hoàn thiện cơ bản)');
    expect(screen.getByText(FINISH_PRICE_LABEL_VI)).toBeInTheDocument();
    expect(screen.queryByText('Đơn giá hoàn thiện')).toBeNull();
    // Xây thô: vẫn hiển thị chi phí ước theo đơn giá xây thô.
    expect(screen.getByText('2,107–2,303 tỷ')).toBeInTheDocument();
  });

  it.each(['hoan_thien', 'noi_that'] as const)('phạm vi %s: thẻ không hiển thị chi phí ước kể cả khi payload còn gửi', (scope) => {
    const item = MATCH_FIXTURE.results[0]!; // estimated_cost có giá trị (payload cũ)
    renderWithWidget(<ContractorCardView item={item} workScope={scope} />);
    expect(screen.queryByText('2,107–2,303 tỷ')).toBeNull();
    const costRow = screen.getByText('Chi phí ước tính theo đơn giá nhà thầu').closest('div')!;
    expect(within(costRow).getByText('Chưa có dữ liệu')).toBeInTheDocument();
  });

  it('danh sách nhà thầu truyền phạm vi từ criteria.work_scope xuống thẻ', () => {
    const data = { ...MATCH_FIXTURE, criteria: { ...MATCH_FIXTURE.criteria, work_scope: 'hoan_thien' as const } };
    renderWithWidget(<BlockRenderer block={{ type: 'contractor_list', data }} active />);
    expect(screen.queryByText('2,107–2,303 tỷ')).toBeNull();
  });

  it('hồ sơ nhà thầu và bảng so sánh mẫu dùng cùng nhãn trọn gói', () => {
    renderWithWidget(<ContractorDetail block={{ type: 'contractor_detail', data: PROFILES['kien-phong']! }} />);
    expect(screen.getByText(FINISH_PRICE_LABEL_VI)).toBeInTheDocument();
    expect(screen.queryByText('Đơn giá hoàn thiện cơ bản')).toBeNull();
    expect(COMPARISON_FIXTURE.rows.find((r) => r.key === 'finish_price')!.label_vi).toBe(FINISH_PRICE_LABEL_VI);
  });

  it('mock tìm nhà thầu cho phạm vi Hoàn thiện: không chi phí ước, không lấy đơn giá làm lý do', () => {
    const m = matchFixtureFor('hoan_thien', 450);
    expect(m.criteria.work_scope).toBe('hoan_thien');
    expect(m.criteria.converted_area_m2).toBe(450);
    for (const r of m.results) {
      expect(r.estimated_cost.status).toBe('unavailable');
      expect(r.reasons.some((x) => x.evidence === 'rough_price' || x.evidence === 'finish_price')).toBe(false);
      expect(r.cautions[0]!.text).toBe('Nhà thầu chưa công bố đơn giá riêng cho phạm vi hoàn thiện trên Dezon — cần yêu cầu báo giá riêng');
      expect(r.breakdown.find((c) => c.key === 'budget_fit')!.score).toBe(0.5);
    }
    const session = newSession();
    mockReply('nhà phố 5x20 Thủ Đức 4 tầng hoàn thiện, xây kín đất, không hầm không lửng, mái bằng bê tông', session);
    const r = mockReply('Tìm nhà thầu phù hợp', session);
    const list = find(r.blocks, 'contractor_list')!;
    expect(list.data.criteria.work_scope).toBe('hoan_thien');
    expect(list.data.results.every((x) => x.estimated_cost.status === 'unavailable')).toBe(true);
  });
});

// ---------------------------------------------------------------- #17

describe('#17 — phương án ngân sách mẫu đúng đầu ra engine suggest_adjustments', () => {
  const ENGINE_CODES = ['reduce_floor', 'remove_basement', 'change_roof', 'open_rooftop', 'remove_mezzanine', 'phase_rough_first', 'combined'];

  it('chỉ dùng mã phương án engine sinh ra, không thu nhỏ diện tích xây dựng', () => {
    for (const o of BUDGET_FIXTURE.options) {
      expect(ENGINE_CODES).toContain(o.code);
      expect(o.changes).not.toHaveProperty('building_area_m2');
    }
    expect(BUDGET_FIXTURE.options.map((o) => o.code)).toEqual(['reduce_floor', 'change_roof', 'combined']);
  });

  it('fits_budget nhất quán với cost_max ≤ ngân sách; ca 1,5 tỷ không có phương án nào vừa', () => {
    for (const o of BUDGET_FIXTURE.options) {
      expect(o.fits_budget).toBe((o.cost_max ?? Infinity) <= BUDGET_FIXTURE.target_budget_vnd);
      expect(o.saving_min).toBe(ESTIMATE_FIXTURE.cost_min! - o.cost_min!);
    }
    expect(BUDGET_FIXTURE.options.some((o) => o.fits_budget)).toBe(false);
    // Giảm còn 3 tầng -> móng mặc định chuyển sang móng băng: 0,5×100 + 3×100 + 0,5×100 = 400 m².
    const reduce = BUDGET_FIXTURE.options[0]!;
    expect(reduce.converted_area_m2).toBe(400);
    expect(reduce.description).toContain('móng băng');
  });

  it('lời dẫn mock nói thật: chưa có phương án nào đưa chi phí về trong ngân sách', () => {
    const r = mockReply('Ngân sách của tôi chỉ 1,5 tỷ', newSession());
    expect(r.text).toContain('chưa có phương án nào đưa chi phí về trong ngân sách 1,5 tỷ');
  });
});

// ---------------------------------------------------------------- #18

describe('#18 — nguồn đơn giá mock khớp phạm vi / loại công trình / tỉnh khách hỏi', () => {
  it('Trọn gói nhà phố TP. HCM: nguồn "trọn gói (hoàn thiện cơ bản)", đơn giá/chi phí riêng, chip nguồn cùng nguồn', () => {
    const e = estimateFixtureFor('tron_goi');
    expect(e.rate.source!.label).toBe(
      'Tổng hợp đơn giá trọn gói (hoàn thiện cơ bản) do 29 nhà thầu nhà phố tại TP. Hồ Chí Minh công bố trên Dezon',
    );
    expect(e.rate.display).toBe('6,8–9 triệu/m²');
    expect(e.cost.display).toBe('3,332–4,410 tỷ');
    // 490 m² × đơn giá, làm tròn triệu (như backend round_to_million).
    expect(e.cost_min).toBe(Math.round((490 * 6_800_000) / 1e6) * 1e6);
    expect(e.cost_max).toBe(Math.round((490 * 9_000_000) / 1e6) * 1e6);
    expect(e.disclaimer).toContain('trọn gói (hoàn thiện cơ bản)');
    expect(e.disclaimer).not.toContain('xây thô');
    expect(estimateSourceBadgesFor(e)!.badges[0]!.source).toEqual(e.rate.source);
  });

  it('mock Trọn gói: lời dẫn lấy nhãn từ nguồn của bảng khái toán', () => {
    const session = newSession();
    mockReply('nhà phố 5x20 Thủ Đức 4 tầng trọn gói, xây kín đất, không hầm không lửng, mái bằng bê tông', session);
    const r = mockReply('Đúng rồi', session);
    const est = find(r.blocks, 'estimate_table')!;
    expect(r.text).toContain(est.data.rate.source!.label);
    expect(r.text).toContain('3,332–4,410 tỷ');
    expect(r.text).not.toContain('xây thô');
    expect(find(r.blocks, 'source_badges')!.badges[0]!.source!.label).toBe(est.data.rate.source!.label);
  });

  it('Biệt thự Hà Nội: không mượn nguồn nhà phố TP. HCM -> chi phí "Chưa có dữ liệu" và lý do đúng', () => {
    const session = newSession();
    mockReply('biệt thự Hà Nội 5x20 4 tầng trọn gói, xây kín đất, không hầm không lửng, mái bằng bê tông', session);
    const r = mockReply('Đúng rồi', session);
    const est = find(r.blocks, 'estimate_table')!;
    expect(est.data.input.building_type).toBe('biet_thu');
    expect(est.data.input.province).toBe('Hà Nội');
    expect(est.data.cost.status).toBe('unavailable');
    expect(est.data.rate.source).toBeNull();
    expect(JSON.stringify(est.data)).not.toContain('nhà phố tại TP. Hồ Chí Minh');
    expect(est.data.warnings).toContain('Chưa có đơn giá tham chiếu cho Biệt thự tại Hà Nội (phạm vi Trọn gói) nên em chưa thể khái toán chi phí');
    expect(r.text).not.toContain('nhà phố tại TP. Hồ Chí Minh');
    expect(r.text).toContain('Biệt thự tại Hà Nội');
    expect(find(r.blocks, 'source_badges')).toBeUndefined();
  });

  it('đơn giá mock = đúng số backend trả cho nhà phố TP. HCM (xây thô 34 nhà thầu, trọn gói 29 nhà thầu)', () => {
    const rough = estimateFixtureFor('xay_tho');
    expect(rough.rate.display).toBe('4,2–4,7 triệu/m²');
    expect(rough.rate.source!.label).toBe('Tổng hợp đơn giá xây thô do 34 nhà thầu nhà phố tại TP. Hồ Chí Minh công bố trên Dezon');
    expect(rough.cost.display).toBe('2,058–2,303 tỷ');
    expect(estimateFixtureFor('tron_goi').rate.value).toEqual([6_800_000, 9_000_000]);
  });

  const tronGoiSession = () => {
    const session = newSession();
    mockReply('nhà phố 5x20 Thủ Đức 4 tầng trọn gói, xây kín đất, không hầm không lửng, mái bằng bê tông', session);
    const est = find(mockReply('Đúng rồi', session).blocks, 'estimate_table')!.data;
    return { session, est };
  };

  it('Trọn gói: điều chỉnh ngân sách dùng đúng khái toán trọn gói (chi phí hiện tại, phương án, nguồn)', () => {
    const { session, est } = tronGoiSession();
    const r = mockReply('Ngân sách của tôi chỉ 1,5 tỷ', session);
    const bud = find(r.blocks, 'budget_options')!.data;
    expect(bud.current_cost).toEqual(est.cost);
    expect(bud.current_cost.display).toBe('3,332–4,410 tỷ');
    expect(r.text).toContain('khái toán hiện tại là 3,332–4,410 tỷ');
    expect(r.text).not.toContain('2,058–2,303 tỷ,');
    expect(bud.options.map((o) => o.code)).toEqual(['phase_rough_first', 'reduce_floor', 'change_roof', 'combined']);
    const byCode = Object.fromEntries(bud.options.map((o) => [o.code, o]));
    // Phương án vẫn trọn gói -> nguồn trọn gói; phương án chuyển sang xây thô -> nguồn xây thô (như engine tính lại).
    expect(byCode.reduce_floor!.cost.source!.label).toBe(est.rate.source!.label);
    expect(byCode.reduce_floor!.cost.display).toBe('2,72–3,60 tỷ');
    expect(byCode.phase_rough_first!.cost.source!.label).toContain('đơn giá xây thô');
    expect(byCode.phase_rough_first!.changes).toEqual({ work_scope: 'xay_tho' });
    for (const o of bud.options) expect(o.saving_min).toBe(est.cost_min! - o.cost_min!);
    expect(r.text).toContain('chỉ là chi phí giai đoạn xây thô');
  });

  it('Trọn gói: tìm nhà thầu ước chi phí theo "Đơn giá trọn gói (hoàn thiện cơ bản)", không mượn giá xây thô', () => {
    const { session } = tronGoiSession();
    mockReply('Ngân sách của tôi chỉ 1,5 tỷ', session);
    const r = mockReply('Tìm nhà thầu phù hợp', session);
    const m = find(r.blocks, 'contractor_list')!.data;
    expect(m.criteria.work_scope).toBe('tron_goi');
    expect(m.criteria.budget_vnd).toBe(1_500_000_000);
    const kp = m.results.find((x) => x.contractor.slug === 'kien-phong')!;
    expect(kp.estimated_cost.display).toBe('3,577–4,655 tỷ'); // 490 m² × 7,3–9,5 triệu/m²
    expect(kp.estimated_cost.source!.label).toBe(
      'Ước tính = diện tích quy đổi 490 m² × đơn giá trọn gói (hoàn thiện cơ bản) trên hồ sơ Kiến Phong',
    );
    expect(kp.reasons.map((x) => x.text)).toContain('Đơn giá trọn gói (hoàn thiện cơ bản) 7,3–9,5 triệu/m²');
    const budget = kp.breakdown.find((c) => c.key === 'budget_fit')!;
    expect(budget.note).toBe('Chi phí ước 3,577–4,655 tỷ vượt ngân sách 1,5 tỷ');
    expect(budget.score).toBe(0);
    expect(JSON.stringify(m)).not.toContain('xây thô');
    expect(r.text).toContain('3,577–4,655 tỷ');
    expect(r.text).not.toContain('xây thô');
  });

  it('Biệt thự Hà Nội: ngân sách và tìm nhà thầu không mượn số liệu / nhà thầu nhà phố TP. HCM', () => {
    const session = newSession();
    mockReply('biệt thự Hà Nội 5x20 4 tầng trọn gói, xây kín đất, không hầm không lửng, mái bằng bê tông', session);
    mockReply('Đúng rồi', session);
    const b = mockReply('Ngân sách của tôi chỉ 1,5 tỷ', session);
    const bud = find(b.blocks, 'budget_options')!.data;
    expect(bud.current_cost.status).toBe('unavailable');
    expect(bud.options).toEqual([]);
    expect(b.text).not.toMatch(/\d tỷ \(|triệu\/m²/);
    const r = mockReply('Tìm nhà thầu phù hợp', session);
    expect(find(r.blocks, 'contractor_list')).toBeUndefined();
    expect(r.text).toContain('biệt thự tại Hà Nội');
    expect(r.text).not.toContain('Kiến Phong');
    const q = mockReply('Yêu cầu báo giá', session);
    expect(find(q.blocks, 'quote_request_form')!.contractors).toEqual([]);
  });

  it('Hoàn thiện: không ước chi phí theo giá nhà thầu, không nêu đơn giá làm lý do; so sánh dùng cùng kết quả', () => {
    const session = newSession();
    mockReply('nhà phố 5x20 Thủ Đức 4 tầng hoàn thiện, xây kín đất, không hầm không lửng, mái bằng bê tông', session);
    mockReply('Đúng rồi', session);
    const m = find(mockReply('Tìm nhà thầu phù hợp', session).blocks, 'contractor_list')!.data;
    expect(m.criteria.work_scope).toBe('hoan_thien');
    expect(m.criteria.converted_area_m2).toBe(450);
    for (const x of m.results) {
      expect(x.estimated_cost.status).toBe('unavailable');
      expect(x.reasons.some((y) => y.text.startsWith('Đơn giá'))).toBe(false);
    }
    const cmp = find(mockReply('So sánh 3 nhà thầu', session).blocks, 'comparison_table')!.data;
    const fit = cmp.rows.find((row) => row.key === 'fit')!;
    expect(fit.cells.map((c) => c.value)).toEqual(m.results.map((x) => x.score));
  });

  it('mẫu xây thô (ngân sách 2 tỷ) = đầu ra engine backend', () => {
    const kp = MATCH_FIXTURE.results[0]!;
    expect(kp.contractor.slug).toBe('kien-phong');
    expect(kp.score).toBe(67);
    expect(kp.estimated_cost.display).toBe('2,107–2,303 tỷ');
    expect(kp.breakdown.find((c) => c.key === 'budget_fit')!.note).toBe('Chi phí ước 2,107–2,303 tỷ vượt ngân sách 2 tỷ');
    expect(kp.reasons.map((x) => x.text)).toContain('Đơn giá xây thô 4,3–4,7 triệu/m²');
    expect(MATCH_FIXTURE.results.map((x) => x.score)).toEqual([67, 42, 41]);
  });
});

// ---------------------------------------------------------------- #27

describe('#27 — căn hộ / Nội thất không hỏi chi tiết; Hoàn thiện / Nội thất / căn hộ không tính móng', () => {
  it('detailsApply giống backend schemas.slots.details_apply', () => {
    expect(detailsApply({ building_type: 'can_ho', work_scope: 'xay_tho' })).toBe(false);
    expect(detailsApply({ building_type: 'nha_pho', work_scope: 'noi_that' })).toBe(false);
    expect(detailsApply({ building_type: 'nha_pho', work_scope: 'hoan_thien' })).toBe(true);
    expect(detailsApply({ building_type: 'nha_pho', work_scope: 'xay_tho' })).toBe(true);
  });

  it.each([
    ['căn hộ TP.HCM 100 m² 2 tầng xây thô', 'can_ho'],
    ['nhà phố TP.HCM 5x20 4 tầng nội thất', 'nha_pho'],
  ])('"%s" -> tóm tắt ngay, không có dòng chi tiết, không nêu quy ước móng', (text, bt) => {
    const session = newSession();
    const ask = mockReply(text, session);
    expect(session.slots.building_type).toBe(bt);
    // Đủ 5 thông tin -> thẻ xác nhận ngay (không hỏi chi tiết, phong cách, ngân sách, mức đầu tư).
    expect(questionOf(ask)).toBeUndefined();
    const sum = find(ask.blocks, 'summary_confirm')!;
    expect(sum).toBeDefined();
    // Căn hộ / Nội thất: không tạm tính chi tiết kết cấu, không có nút "Cung cấp thêm thông tin".
    expect(sum.more_label).toBeNull();
    expect((sum.assumptions ?? []).some((a) => a.startsWith('Tạm tính'))).toBe(false);
    const labels = sum.items.map((i) => i.label);
    expect(labels).not.toContain('Diện tích xây dựng mỗi tầng');
    expect(labels).not.toContain('Tầng hầm / tầng lửng');
    expect(labels).not.toContain('Mái');
    expect((sum.assumptions ?? []).some((a) => a.startsWith('Móng'))).toBe(false);
  });

  it('Hoàn thiện: chi tiết vẫn áp dụng (tạm tính, cung cấp thêm hỏi được) nhưng không nêu móng; bảng không có móng, 450 m²', () => {
    const session = newSession();
    const ask = find(mockReply('nhà phố TP.HCM 5x20 4 tầng hoàn thiện', session).blocks, 'summary_confirm')!;
    expect(ask.assumptions).toEqual([
      'Tạm tính: xây kín đất (100 m²/tầng), không hầm, không lửng, mái bê tông cốt thép — anh/chị bấm Sửa hoặc Cung cấp thêm thông tin để thay đổi.',
    ]);
    expect(questionOf(mockReply('Tôi muốn cung cấp thêm thông tin', session))?.question).toBe('Diện tích xây dựng mỗi tầng (m²)?');
    const s2 = newSession();
    const sum = find(mockReply('nhà phố 5x20 Thủ Đức 4 tầng hoàn thiện, xây kín đất, không hầm không lửng, mái bằng bê tông', s2).blocks, 'summary_confirm')!;
    expect((sum.assumptions ?? []).some((a) => a.startsWith('Móng'))).toBe(false);

    const e = estimateFixtureFor('hoan_thien');
    expect(e.items.some((i) => i.group_name === 'mong')).toBe(false);
    expect(e.converted_area_m2).toBe(450);
    expect(e.input.foundation_type).toBeNull();
    expect(e.assumptions).toContain('Phạm vi Hoàn thiện: không tính diện tích móng (móng thuộc phần xây thô, không có hạng mục hoàn thiện)');
    expect(e.assumptions.some((a) => a.startsWith('Móng tính theo thông lệ'))).toBe(false);
  });

  it('mock xác nhận Hoàn thiện: lời dẫn nói 450 m², không phải 490 m²', () => {
    const session = newSession();
    mockReply('nhà phố 5x20 Thủ Đức 4 tầng hoàn thiện, xây kín đất, không hầm không lửng, mái bằng bê tông', session);
    const r = mockReply('Đúng rồi', session);
    expect(r.text).toContain('450 m²');
    expect(r.text).not.toContain('490');
  });

  it('Nội thất / căn hộ: chỉ diện tích sàn (400 m²), không móng, không mái', () => {
    for (const e of [estimateFixtureFor('noi_that'), estimateFixtureFor('xay_tho', { building_type: 'can_ho', province: 'TP. Hồ Chí Minh' })]) {
      expect(e.items.every((i) => i.group_name === 'tang')).toBe(true);
      expect(e.converted_area_m2).toBe(400);
      expect(e.cost.status).toBe('unavailable');
    }
    const apt = estimateFixtureFor('xay_tho', { building_type: 'can_ho', province: 'TP. Hồ Chí Minh' });
    expect(apt.items[0]!.label_vi).toBe('Sàn căn hộ — tầng 1');
    expect(apt.assumptions).toContain('Căn hộ: không tính móng, mái, hầm, sân thượng và sân (thuộc phần chung của tòa nhà)');
  });
});

// ---------------------------------------------------------------- #28

describe('#28 — mặc định độ sâu hầm giống engine (1,7–2 m)', () => {
  it('có hầm, chưa biết độ sâu -> tạm tính "hầm sâu 1,7–2 m"; cung cấp thêm chỉ hỏi độ sâu', () => {
    const session = newSession();
    const ask = find(
      mockReply('nhà phố 5x20 Thủ Đức 4 tầng xây thô, xây kín đất, có tầng hầm, không lửng, mái bằng bê tông', session).blocks,
      'summary_confirm',
    )!;
    expect(ask.assumptions![0]).toBe('Tạm tính: hầm sâu 1,7–2 m — anh/chị bấm Sửa hoặc Cung cấp thêm thông tin để thay đổi.');
    const q = questionOf(mockReply('Tôi muốn cung cấp thêm thông tin', session));
    expect(q).toMatchObject({ question: 'Độ sâu tầng hầm?', step: 1, total: 1 });
    const sum = find(mockReply(q!.options.at(-1)!.value, session).blocks, 'summary_confirm')!; // "Chưa biết"
    expect(sum.assumptions ?? []).toContain('Tạm tính: hầm sâu 1,7–2 m — anh/chị bấm Sửa hoặc Cung cấp thêm thông tin để thay đổi.');
    expect((sum.assumptions ?? []).join(' ')).not.toContain('1,3–1,7');
  });
});

// ---------------------------------------------------------------- #29

describe('#29 — miễn trừ khi không có đơn giá', () => {
  const noRate: EstimateResult = estimateFixtureFor('hoan_thien');

  it('fixture không đơn giá dùng đúng câu miễn trừ no-rate của backend', () => {
    expect(noRate.disclaimer).toBe(NO_RATE_DISCLAIMER);
    expect(estimateFixtureFor('noi_that').disclaimer).toBe(NO_RATE_DISCLAIMER);
    expect(NO_RATE_DISCLAIMER).not.toContain('dựa trên đơn giá tham chiếu');
  });

  it('fallbackDisclaimer: không đơn giá -> câu no-rate; có đơn giá -> nêu nguồn đơn giá', () => {
    expect(fallbackDisclaimer(noRate)).toBe(NO_RATE_DISCLAIMER);
    expect(fallbackDisclaimer(ESTIMATE_FIXTURE)).toContain('dựa trên đơn giá tham chiếu (Tổng hợp đơn giá xây thô');
  });

  it('backend gửi disclaimer rỗng cho khái toán không đơn giá -> hiển thị câu no-rate', () => {
    renderWithWidget(<EstimateTable block={{ type: 'estimate_table', data: { ...noRate, disclaimer: '' } }} />);
    const note = screen.getByRole('note');
    expect(note).toHaveTextContent('chưa tính được chi phí');
    expect(note).not.toHaveTextContent('dựa trên đơn giá tham chiếu');
  });
});
