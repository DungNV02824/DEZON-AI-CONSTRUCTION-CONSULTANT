/**
 * Dữ liệu giả lập cho chế độ mock (data-mock="true" hoặc ?dezon_mock=1) và cho test giao diện.
 * Ca mẫu: nhà phố 5 × 20 m tại TP. Thủ Đức (TP. Hồ Chí Minh), 4 tầng, móng cọc, mái BTCT -> 490 m² quy đổi.
 * Đơn giá tổng hợp = đúng số backend đang trả (GET /api/rates, 02/11/2026) cho nhà phố tại TP. Hồ Chí Minh:
 *   xây thô 4,2–4,7 triệu/m² (34 nhà thầu) -> 2,058–2,303 tỷ; trọn gói 6,8–9 triệu/m² (29 nhà thầu) -> 3,332–4,410 tỷ.
 * Phương án ngân sách và kết quả tìm nhà thầu chép từ engine backend (suggest_adjustments, rank_contractors,
 * build_reasons/build_cautions) với cùng đầu vào; mọi bước sau khái toán dùng CÙNG phạm vi/loại công trình/tỉnh.
 * Mọi chuỗi `display` ở đây mô phỏng đầu ra của backend.
 * KHÔNG dùng làm dữ liệu thật.
 */
import { formatM2, formatVnd, viNumber } from '../utils/format';
import { BUILDING_TYPE_VI, NO_RATE_DISCLAIMER, WORK_SCOPE_VI } from '../utils/labels';
import type {
  BudgetAdjustmentResult,
  BuildingType,
  Caution,
  ComparisonMatrix,
  ComparisonRow,
  ContractorCard,
  ContractorProfile,
  EstimateResult,
  EvidenceValue,
  ImageRef,
  MatchResult,
  MatchedContractor,
  Reason,
  SourceBadgesBlock,
  SourceRef,
  WorkScope,
} from './types';

export const MOCK_ESTIMATE_SESSION_ID = '7c1e9a52-4f0b-4d0e-9a51-2b1d6c3e8f10';
export const NO_DATA = 'Chưa có dữ liệu';
const OBSERVED = '2026-10-31';
const SITE = 'https://dezon.vn';

export const unavailable = (): EvidenceValue => ({
  value: null,
  display: NO_DATA,
  status: 'unavailable',
  source: null,
  is_stale: false,
});

const ev = (value: unknown, display: string, source: SourceRef | null, status: EvidenceValue['status'] = 'on_dezon', isStale = false): EvidenceValue => ({
  value,
  display,
  status,
  source,
  is_stale: isStale,
});

// ---------------------------------------------------------------- nguồn

/** Khu vực mẫu có đơn giá tổng hợp trong chế độ giả lập (giống ca nghiệm thu mục 13). */
export const MOCK_RATE_BUILDING_TYPE: BuildingType = 'nha_pho';
export const MOCK_RATE_PROVINCE = 'TP. Hồ Chí Minh';

/** Ngày cập nhật nguồn của dòng đơn giá tổng hợp (= hồ sơ nhà thầu mới nhất trong nhóm), như backend trả. */
const RATE_UPDATED = '2026-08-26';
const RATE_UPDATED_VI = '26/08/2026';
const rateSource = (scope: 'xay_tho' | 'tron_goi', label: string): SourceRef => ({
  label,
  url: `${SITE}/nha-thau/tiem-nang/#don-gia-nha_pho-${scope}-tp-ho-chi-minh-20261102`,
  updated_at: RATE_UPDATED,
});

/**
 * Nhãn nguồn + đơn giá đúng như backend đang trả (GET /api/rates?building_type=nha_pho&province=TP. Hồ Chí Minh,
 * 02/11/2026): xây thô <- "Đơn giá xây thô", trọn gói <- "Đơn giá hoàn thiện cơ bản" (= trọn gói) trên hồ sơ nhà thầu.
 * Hoàn thiện / Nội thất: backend không có đơn giá -> không có mục ở đây.
 */
const RATE_SOURCES: Partial<Record<WorkScope, SourceRef>> = {
  xay_tho: rateSource('xay_tho', 'Tổng hợp đơn giá xây thô do 34 nhà thầu nhà phố tại TP. Hồ Chí Minh công bố trên Dezon'),
  tron_goi: rateSource(
    'tron_goi',
    'Tổng hợp đơn giá trọn gói (hoàn thiện cơ bản) do 29 nhà thầu nhà phố tại TP. Hồ Chí Minh công bố trên Dezon',
  ),
};
/** Đơn giá (VND/m²) + chuỗi hiển thị + id dòng construction_rates như backend trả. */
const RATES: Partial<Record<WorkScope, { min: number; max: number; display: string; id: number }>> = {
  xay_tho: { min: 4_200_000, max: 4_700_000, display: '4,2–4,7 triệu/m²', id: 194 },
  tron_goi: { min: 6_800_000, max: 9_000_000, display: '6,8–9 triệu/m²', id: 193 },
};
/** Chi phí = 490 m² × đơn giá (làm tròn triệu) — đúng như backend calculate_estimate tính. */
const COSTS: Partial<Record<WorkScope, { min: number; max: number; display: string }>> = {
  xay_tho: { min: 2_058_000_000, max: 2_303_000_000, display: '2,058–2,303 tỷ' },
  tron_goi: { min: 3_332_000_000, max: 4_410_000_000, display: '3,332–4,410 tỷ' },
};
const RULES_SOURCE: SourceRef = {
  label: 'Hệ số khái toán mặc định (seed dev — chờ Dezon duyệt)',
  url: null,
  updated_at: OBSERVED,
};
const profileSource = (slug: string, name: string, updated = OBSERVED): SourceRef => ({
  label: `Hồ sơ nhà thầu ${name}`,
  url: `${SITE}/nha-thau/${slug}`,
  updated_at: updated,
});
const MATCHING_SOURCE: SourceRef = { label: 'Matching Engine Dezon (trọng số v1)', url: null, updated_at: OBSERVED };

// ---------------------------------------------------------------- khái toán

/** Phạm vi chưa có đơn giá thật trên Dezon (Hoàn thiện, Nội thất) -> chi phí "Chưa có dữ liệu". */
export const SCOPES_WITHOUT_RATE: ReadonlyArray<WorkScope> = ['hoan_thien', 'noi_that'];

const RULES_NOT_APPROVED_WARNING = 'Hệ số khái toán chưa được Dezon phê duyệt chính thức';

// Văn bản tĩnh chép từ backend app/engines/estimate/explain.py (frontend không tự đặt nội dung khác).
const SCOPE_INCLUDES_VI: Record<WorkScope, string> = {
  xay_tho:
    'Phạm vi Xây thô: gồm phần móng, khung bê tông cốt thép, xây tô tường, mái và hệ ống điện nước âm tường; chưa gồm vật tư hoàn thiện.',
  hoan_thien:
    'Phạm vi Hoàn thiện: gồm vật tư và nhân công hoàn thiện (ốp lát, sơn bả, trần, cửa, thiết bị điện nước, thiết bị vệ sinh) trên phần thô đã có.',
  tron_goi: 'Phạm vi Trọn gói: gồm phần thô và phần hoàn thiện cơ bản.',
  noi_that: 'Phạm vi Nội thất: gồm đồ nội thất và lắp đặt bên trong theo diện tích sàn sử dụng.',
};
const COMMON_EXCLUSIONS = [
  'Chi phí xin giấy phép xây dựng và các thủ tục pháp lý',
  'Chi phí thiết kế kiến trúc, kết cấu',
  'Điện nước ngoài nhà, đấu nối hạ tầng (điện, nước, thoát nước công cộng)',
];
const SCOPE_EXCLUSIONS: Record<WorkScope, string[]> = {
  xay_tho: [
    'Vật tư hoàn thiện (gạch ốp lát, sơn, trần, cửa, thiết bị vệ sinh, thiết bị điện)',
    'Nội thất rời (giường, tủ, sofa, rèm…)',
    'Tháo dỡ công trình cũ, gia cố nhà lân cận',
  ],
  hoan_thien: ['Phần xây thô (móng, khung kết cấu, xây tô)', 'Nội thất rời (giường, tủ, sofa, rèm…)'],
  tron_goi: [
    'Nội thất rời (giường, tủ, sofa, rèm…)',
    'Thiết bị đặc thù (thang máy, điều hòa, năng lượng mặt trời, hồ bơi…)',
    'Tháo dỡ công trình cũ, gia cố nhà lân cận',
  ],
  noi_that: [
    'Phần xây thô và hoàn thiện của công trình',
    'Thiết bị điện tử, đồ gia dụng',
    'Chi phí thiết kế nội thất chi tiết (nếu nhà thầu tính riêng)',
  ],
};
const GENERAL_DRIVERS = [
  'Hẻm nhỏ, xe tải vật tư khó vào làm tăng chi phí vận chuyển và nhân công',
  'Đất yếu hoặc nền đất phức tạp cần gia cố móng',
  'Nhiều ban công, ô văng, chi tiết kiến trúc phức tạp',
  'Biến động giá vật liệu (thép, xi măng, cát, đá) theo thời điểm thi công',
];

/** Công trình khách hỏi (mock lấy từ slots); mặc định = ca mẫu nhà phố TP. Thủ Đức. */
export interface MockEstimateWhere {
  building_type?: BuildingType | null;
  province?: string | null;
  district?: string | null;
}
const SAMPLE_WHERE: MockEstimateWhere = {
  building_type: MOCK_RATE_BUILDING_TYPE,
  province: MOCK_RATE_PROVINCE,
  district: 'TP. Thủ Đức',
};

/** Đơn giá mẫu chỉ có cho nhà phố tại TP. Hồ Chí Minh, phạm vi Xây thô / Trọn gói (mock không tự tạo số cho nơi khác). */
export function mockHasRate(scope: WorkScope, where: MockEstimateWhere = SAMPLE_WHERE): boolean {
  return (
    !SCOPES_WITHOUT_RATE.includes(scope) &&
    RATES[scope] !== undefined &&
    (where.building_type ?? MOCK_RATE_BUILDING_TYPE) === MOCK_RATE_BUILDING_TYPE &&
    (where.province ?? MOCK_RATE_PROVINCE) === MOCK_RATE_PROVINCE
  );
}

/**
 * Khái toán mẫu (đất 5 × 20 m, 4 tầng nổi, mỗi tầng 100 m², không hầm/lửng, mái BTCT) theo phạm vi và công trình,
 * mô phỏng đúng backend `calculate_estimate` (bảng hệ số mục 6.2):
 * - Xây thô / Trọn gói: móng cọc + 4 sàn + mái = 490 m²; có chi phí chỉ khi có đơn giá mẫu (nhà phố, TP. HCM).
 * - Hoàn thiện: không tính móng (C-F12) -> 450 m²; Nội thất / căn hộ: chỉ diện tích sàn -> 400 m². Không có đơn giá.
 * - Loại công trình / tỉnh khác: không có đơn giá -> "Chưa có dữ liệu" (không mượn nguồn nhà phố TP. HCM).
 */
export function estimateFixtureFor(scope: WorkScope = 'xay_tho', where: MockEstimateWhere = SAMPLE_WHERE): EstimateResult {
  const buildingType = where.building_type ?? MOCK_RATE_BUILDING_TYPE;
  const province = where.province ?? MOCK_RATE_PROVINCE;
  const apartment = buildingType === 'can_ho';
  const interiorOnly = apartment || scope === 'noi_that';
  const finishingOnly = scope === 'hoan_thien';
  const withFoundation = !interiorOnly && !finishingOnly;
  const rough = scope === 'xay_tho' || scope === 'tron_goi';
  const floors = 4;
  const area = 100;

  const items: EstimateResult['items'] = [];
  if (withFoundation) {
    items.push({ rule_code: 'foundation_pile', label_vi: 'Móng cọc (chưa gồm ép cọc)', group_name: 'mong', area_m2: area, coefficient: 0.4, converted_m2: 40 });
  }
  for (let i = 0; i < floors; i++) {
    const label = apartment ? `Sàn căn hộ — tầng ${i + 1}` : i === 0 ? 'Tầng trệt' : `Lầu ${i}`;
    items.push({ rule_code: 'floor_standard', label_vi: label, group_name: 'tang', area_m2: area, coefficient: 1, converted_m2: area });
  }
  if (!interiorOnly) {
    items.push({ rule_code: 'roof_concrete', label_vi: 'Mái bê tông cốt thép', group_name: 'mai', area_m2: area, coefficient: 0.5, converted_m2: 50 });
  }
  const converted = items.reduce((s, it) => s + it.converted_m2, 0);

  const assumptions: string[] = [SCOPE_INCLUDES_VI[scope]];
  // Căn hộ / Nội thất: không hỏi chi tiết kết cấu -> diện tích sàn lấy theo diện tích khách cung cấp.
  if (interiorOnly) {
    assumptions.push(
      apartment
        ? `Diện tích sàn căn hộ = ${area} m² theo diện tích anh/chị cung cấp`
        : `Diện tích xây dựng mỗi tầng = diện tích đất ${area} m² (anh/chị chưa cung cấp)`,
    );
  }
  if (apartment) assumptions.push('Căn hộ: không tính móng, mái, hầm, sân thượng và sân (thuộc phần chung của tòa nhà)');
  else if (interiorOnly) {
    assumptions.push(
      'Phạm vi Nội thất: chỉ tính diện tích sàn sử dụng (các tầng và tầng lửng); không tính móng, mái, hầm, sân thượng và sân',
    );
  }
  if (!interiorOnly && finishingOnly) {
    assumptions.push('Phạm vi Hoàn thiện: không tính diện tích móng (móng thuộc phần xây thô, không có hạng mục hoàn thiện)');
  }
  if (withFoundation) assumptions.push('Móng tính theo thông lệ: móng cọc cho nhà ≥ 4 tầng nổi (anh/chị chưa cung cấp loại móng)');
  assumptions.push(
    apartment ? `Số tầng của căn hộ: ${floors}, mỗi tầng ${area} m²` : `Số tầng nổi: ${floors} (tính cả tầng trệt), mỗi tầng ${area} m²`,
  );
  if (!interiorOnly) assumptions.push(`Diện tích mái = diện tích xây dựng ${area} m² (anh/chị chưa cung cấp)`);

  const exclusions: string[] = [];
  if (rough && !apartment) exclusions.push('Chi phí ép cọc hoặc khoan cọc (hệ số móng cọc chưa gồm phần ép cọc)');
  if (scope === 'noi_that') exclusions.push(...SCOPE_EXCLUSIONS.noi_that, 'Chi phí xin phép sửa chữa/cải tạo (nếu có)');
  else if (apartment) {
    exclusions.push(
      ...SCOPE_EXCLUSIONS[scope].filter((x) => !x.includes('Tháo dỡ')),
      'Chi phí thiết kế',
      'Phí, thủ tục thi công với ban quản lý tòa nhà',
    );
  } else exclusions.push(...COMMON_EXCLUSIONS, ...SCOPE_EXCLUSIONS[scope]);

  const costDrivers: string[] = [];
  if (!interiorOnly) {
    if (rough) costDrivers.push('Móng cọc: chi phí ép/khoan cọc tính riêng và phụ thuộc kết quả khảo sát địa chất');
    costDrivers.push(
      rough
        ? 'Nhà nhiều tầng cần khung kết cấu và móng lớn hơn, chi phí giàn giáo và vận chuyển vật tư lên cao tăng'
        : 'Nhà nhiều tầng làm chi phí giàn giáo và vận chuyển vật tư lên cao tăng',
    );
    costDrivers.push(...GENERAL_DRIVERS.filter((d) => rough || !d.includes('móng')));
  } else {
    costDrivers.push(
      'Thi công trong tòa nhà có giới hạn giờ làm và vận chuyển vật tư qua thang máy',
      'Biến động giá vật liệu theo thời điểm thi công',
    );
  }

  const warnings: string[] = [];
  if (apartment && floors > 3) {
    warnings.push(
      'Với căn hộ, số tầng được hiểu là số tầng của chính căn hộ (vd căn duplex = 2), không phải vị trí tầng trong tòa nhà — anh/chị kiểm tra lại giúp em',
    );
  }
  warnings.push(RULES_NOT_APPROVED_WARNING);

  const input: EstimateResult['input'] = {
    building_type: buildingType,
    province,
    district: where.district ?? null,
    land_width_m: 5,
    land_length_m: 20,
    land_area_m2: 100,
    building_area_m2: area,
    floors,
    has_mezzanine: false,
    basement: false,
    roof_type: interiorOnly ? null : 'roof_concrete',
    rooftop_terrace: null,
    foundation_type: withFoundation ? 'foundation_pile' : null,
    work_scope: scope,
  };
  const base = {
    estimate_session_id: MOCK_ESTIMATE_SESSION_ID,
    input,
    items,
    converted_area_m2: converted,
    rules_version: 1,
    rules_status: 'reference' as const,
    assumptions,
    exclusions,
    cost_drivers: costDrivers,
  };

  const rate = RATES[scope];
  const cost = COSTS[scope];
  const source = RATE_SOURCES[scope];
  if (mockHasRate(scope, where) && rate && cost && source) {
    return {
      ...base,
      cost_min: cost.min,
      cost_max: cost.max,
      cost: ev([cost.min, cost.max], cost.display, source, 'reference'),
      rate: ev([rate.min, rate.max], rate.display, source, 'on_dezon'),
      rate_id: rate.id,
      disclaimer: `Đây là khái toán sơ bộ dựa trên đơn giá tham chiếu (${source.label}, cập nhật ${RATE_UPDATED_VI}) và các giả định nêu trên, chưa tính điều kiện thực tế của khu đất. Không phải báo giá thi công. Vui lòng yêu cầu báo giá từ nhà thầu để có con số chính xác.`,
      warnings,
    };
  }
  warnings.push(
    SCOPES_WITHOUT_RATE.includes(scope)
      ? `Các nhà thầu trên Dezon chưa công bố đơn giá riêng cho phạm vi ${WORK_SCOPE_VI[scope]} nên em chưa thể khái toán chi phí phần này. Anh/chị có thể chọn phạm vi Xây thô hoặc Trọn gói (có đơn giá do nhà thầu công bố trên Dezon), hoặc yêu cầu báo giá trực tiếp từ nhà thầu`
      : `Chưa có đơn giá tham chiếu cho ${BUILDING_TYPE_VI[buildingType] ?? buildingType} tại ${province} (phạm vi ${WORK_SCOPE_VI[scope]}) nên em chưa thể khái toán chi phí`,
  );
  return {
    ...base,
    cost_min: null,
    cost_max: null,
    cost: unavailable(),
    rate: unavailable(),
    rate_id: null,
    disclaimer: NO_RATE_DISCLAIMER,
    warnings,
  };
}

/** Ca mẫu: nhà phố TP. Thủ Đức, Xây thô -> 490 m², 2,058–2,303 tỷ. */
export const ESTIMATE_FIXTURE: EstimateResult = estimateFixtureFor('xay_tho');

/** Chip nguồn theo đúng nguồn đơn giá của bảng khái toán (null khi không có đơn giá). */
export function estimateSourceBadgesFor(estimate: EstimateResult): SourceBadgesBlock | null {
  const source = estimate.rate.source;
  if (estimate.rate.status === 'unavailable' || !source) return null;
  return {
    type: 'source_badges',
    badges: [
      { label: 'Đơn giá nhà thầu công bố trên Dezon', status: 'reference', source },
      { label: 'Hệ số khái toán', status: 'reference', source: RULES_SOURCE },
    ],
  };
}
export const ESTIMATE_SOURCE_BADGES: SourceBadgesBlock = estimateSourceBadgesFor(ESTIMATE_FIXTURE)!;

/** Ngân sách mục tiêu của kịch bản giả lập "Ngân sách của tôi chỉ 1,5 tỷ". */
export const MOCK_TARGET_BUDGET_VND = 1_500_000_000;

type BudgetOptionOut = BudgetAdjustmentResult['options'][number];

const budgetOption = (
  head: readonly [code: string, label: string, description: string],
  changes: Record<string, unknown>,
  convertedAreaM2: number,
  cost: [number, number, string],
  rateScope: 'xay_tho' | 'tron_goi',
  savingMin: number,
): BudgetOptionOut => ({
  code: head[0],
  label_vi: head[1],
  description: head[2],
  changes,
  converted_area_m2: convertedAreaM2,
  cost_min: cost[0],
  cost_max: cost[1],
  cost: ev([cost[0], cost[1]], cost[2], RATE_SOURCES[rateScope]!, 'reference'),
  saving_min: savingMin,
  fits_budget: cost[1] <= MOCK_TARGET_BUDGET_VND,
});

const REDUCE_FLOOR = [
  'reduce_floor',
  'Giảm 1 tầng (còn 3 tầng)',
  'Bớt một tầng, giữ nguyên các thông số khác. Móng tính lại theo mặc định: móng băng.',
] as const;
const CHANGE_ROOF = ['change_roof', 'Đổi sang mái tôn', 'Thay mái bê tông cốt thép bằng mái tôn.'] as const;

/**
 * Phương án cho ngân sách 1,5 tỷ của ca mẫu — chép đúng đầu ra backend `suggest_adjustments` (bảng hệ số mục 6.2,
 * đơn giá tổng hợp ở trên). Mỗi phương án mang nguồn đơn giá của phạm vi engine đã dùng để tính lại: Trọn gói có thêm
 * "Chia giai đoạn: làm phần xây thô trước" (chi phí chỉ là giai đoạn xây thô -> nguồn đơn giá xây thô).
 * Engine không thu nhỏ diện tích xây dựng; với 1,5 tỷ không phương án nào vừa -> mọi `fits_budget` = false.
 */
const BUDGET_OPTIONS: Partial<Record<WorkScope, BudgetOptionOut[]>> = {
  xay_tho: [
    budgetOption(REDUCE_FLOOR, { floors: 3 }, 400, [1_680_000_000, 1_880_000_000, '1,68–1,88 tỷ'], 'xay_tho', 378_000_000),
    budgetOption(CHANGE_ROOF, { roof_type: 'roof_metal' }, 470, [1_974_000_000, 2_209_000_000, '1,974–2,209 tỷ'], 'xay_tho', 84_000_000),
    budgetOption(
      [
        'combined',
        'Kết hợp: Giảm 1 tầng (còn 3 tầng) + đổi sang mái tôn',
        'Áp dụng đồng thời hai phương án “Giảm 1 tầng (còn 3 tầng)” và “Đổi sang mái tôn”. Móng tính lại theo mặc định: móng băng.',
      ],
      { floors: 3, roof_type: 'roof_metal' },
      380,
      [1_596_000_000, 1_786_000_000, '1,596–1,786 tỷ'],
      'xay_tho',
      462_000_000,
    ),
  ],
  tron_goi: [
    budgetOption(
      [
        'phase_rough_first',
        'Chia giai đoạn: làm phần xây thô trước',
        'Làm phần xây thô trước, phần hoàn thiện thực hiện sau khi có thêm ngân sách. Chi phí bên dưới chỉ là giai đoạn xây thô.',
      ],
      { work_scope: 'xay_tho' },
      490,
      [2_058_000_000, 2_303_000_000, '2,058–2,303 tỷ'],
      'xay_tho',
      1_274_000_000,
    ),
    budgetOption(REDUCE_FLOOR, { floors: 3 }, 400, [2_720_000_000, 3_600_000_000, '2,72–3,60 tỷ'], 'tron_goi', 612_000_000),
    budgetOption(CHANGE_ROOF, { roof_type: 'roof_metal' }, 470, [3_196_000_000, 4_230_000_000, '3,196–4,230 tỷ'], 'tron_goi', 136_000_000),
    budgetOption(
      [
        'combined',
        'Kết hợp: Chia giai đoạn: làm phần xây thô trước + giảm 1 tầng (còn 3 tầng)',
        'Áp dụng đồng thời hai phương án “Chia giai đoạn: làm phần xây thô trước” và “Giảm 1 tầng (còn 3 tầng)”. Móng tính lại theo mặc định: móng băng.',
      ],
      { work_scope: 'xay_tho', floors: 3 },
      400,
      [1_680_000_000, 1_880_000_000, '1,68–1,88 tỷ'],
      'xay_tho',
      1_652_000_000,
    ),
  ],
};

/**
 * Điều chỉnh ngân sách cho ĐÚNG khái toán vừa tính (cùng phạm vi, loại công trình, tỉnh) — như backend
 * `suggest_budget_adjustments` đọc lại phiên khái toán. Khái toán không có chi phí (chưa có đơn giá) -> chi phí hiện
 * tại "Chưa có dữ liệu" và không có phương án (engine không tính được phương án nào).
 */
export function budgetFixtureFor(estimate: EstimateResult): BudgetAdjustmentResult {
  const scope = estimate.input.work_scope;
  const options = estimate.cost.status === 'unavailable' ? [] : (BUDGET_OPTIONS[scope] ?? []);
  return {
    estimate_session_id: MOCK_ESTIMATE_SESSION_ID,
    target_budget_vnd: MOCK_TARGET_BUDGET_VND,
    target_budget: ev(MOCK_TARGET_BUDGET_VND, '1,5 tỷ', { label: 'Khách cung cấp', url: null, updated_at: null }, 'reference'),
    current_cost: estimate.cost,
    options,
  };
}

/** Phương án ngân sách 1,5 tỷ cho ca mẫu Xây thô. */
export const BUDGET_FIXTURE: BudgetAdjustmentResult = budgetFixtureFor(ESTIMATE_FIXTURE);

// ---------------------------------------------------------------- nhà thầu

const placeholder = (text: string, size = '800x600') => `https://placehold.co/${size}?text=${encodeURIComponent(text).replace(/%20/g, '+')}`;

const KP_SRC = profileSource('kien-phong', 'Kiến Phong');
const SC_SRC = profileSource('smartcons-tech', 'Smartcons Tech');
// Huy Hoàng: ngày cập nhật cũ để minh họa cảnh báo "có thể đã cũ" (is_stale).
const HH_SRC = profileSource('huy-hoang', 'Huy Hoàng', '2026-03-15');

// Ảnh thật trên dezon.vn (snapshot backend/app/db/seed_data/dezon_snapshot.json) để demo lightbox ở chế độ giả lập.
const KP_UPLOADS = `${SITE}/wp-content/uploads/dz-data/nt/kien-phong/o`;
const KP_PROJECTS = [
  {
    slug: 'france-indochine-22-ha-noi',
    name: 'France Indochine 22 Hà Nội',
    province: 'Hà Nội',
    district: null,
    url: `${SITE}/nha-thau/kien-phong/france-indochine-22-ha-noi/`,
    photos: ['ApoMbxpbgoLIAaxJt3cl2gFR', 'W2ZkbSD0YoRhkbxGNp4lA8F1', 'Z0Mcb5HU5oIuvKx0Qv8l57rX', 'U4TYbK0iFomZA1xLn2xl6oe0'],
  },
  {
    slug: 'green-pool-villa-vinh-long',
    name: 'Green Pool Villa Vĩnh Long',
    province: 'Vĩnh Long',
    district: null,
    url: `${SITE}/nha-thau/kien-phong/green-pool-villa-vinh-long/`,
    photos: ['VmpUbQRpQoqie5xgIrFliHOo', 'R0JHbbt4doaimtx8JLTlXtdo', 'OdfZbAjZAoMqudx1TFplrHYA', 'OgTcbyyQYo5SngxKVQRlbqwZ'],
  },
  {
    slug: 'indochine-villa-quan-7',
    name: 'Indochine Villa Quận 7 Sài Gòn',
    province: 'TP. Hồ Chí Minh',
    district: 'Quận 7',
    url: `${SITE}/nha-thau/kien-phong/indochine-villa-quan-7-sai-gon/`,
    photos: ['UmjqbDGOsoPA8Sxm4xmliVZ7', 'AmoNbyPi4oTp49xHWonlnFCP', 'WADbbDhZWoGJYVxAPQclf9h3', 'FJawbr9tpoRXZTxrWSYldaMl'],
  },
];

function kpProjectImages(p: (typeof KP_PROJECTS)[number]): ImageRef[] {
  return p.photos.map((id, k) => ({
    url: `${KP_UPLOADS}/${id}.jpg`,
    caption: `${p.name} — ảnh ${k + 1}`,
    project_slug: p.slug,
    project_name: p.name,
    project_url: p.url,
  }));
}

/** 12 ảnh (3 công trình × 4 ảnh) theo từng công trình — ContractorProfile.images (backend nối ảnh các công trình). */
const KP_IMAGES: ImageRef[] = KP_PROJECTS.flatMap(kpProjectImages);
/** cover_images của thẻ: xoay vòng giữa các công trình như backend `_cover_images` (ảnh 1 mỗi công trình, rồi ảnh 2, ...). */
const KP_COVERS: ImageRef[] = [0, 1, 2, 3].flatMap((k) => KP_PROJECTS.map((p) => kpProjectImages(p)[k]!));

export const KIEN_PHONG: ContractorCard = {
  id: 1,
  slug: 'kien-phong',
  name: 'Kiến Phong',
  badge: 'Nhà thầu tiềm năng',
  avatar_url: `${SITE}/wp-content/uploads/dz-data/nt/kien-phong/ApoMbxpbgoLIAaxJt3cl2gFR.jpg`,
  profile_url: `${SITE}/nha-thau/kien-phong`,
  profile_source: KP_SRC,
  cover_images: KP_COVERS,
  evidence: {
    province: ev('TP. Hồ Chí Minh', 'TP. Hồ Chí Minh', KP_SRC),
    building_types: ev(['nha_pho', 'biet_thu', 'can_ho'], 'Nhà phố · Biệt thự · Căn hộ', KP_SRC),
    years_experience: ev(8, '8 năm', KP_SRC),
    founded_year: ev(2018, '2018', KP_SRC),
    staff: ev({ workers: 20, office: 12 }, '20 thợ, 12 văn phòng', KP_SRC),
    rough_price: ev([4_300_000, 4_700_000], '4,3–4,7 triệu/m²', KP_SRC),
    finish_price: ev([7_300_000, 9_500_000], '7,3–9,5 triệu/m²', KP_SRC),
    typical_project_value: ev(25_000_000_000, '25 tỷ/công trình', KP_SRC),
    dezon_project_count: ev(7, '7 công trình', KP_SRC),
    warranty: unavailable(),
    profile_completeness: ev(0.9, '90%', KP_SRC),
  },
};

export const SMARTCONS: ContractorCard = {
  id: 2,
  slug: 'smartcons-tech',
  name: 'Smartcons Tech',
  badge: null,
  avatar_url: placeholder('Smartcons Tech', '160x160'),
  profile_url: `${SITE}/nha-thau/smartcons-tech`,
  profile_source: SC_SRC,
  cover_images: [],
  evidence: {
    province: ev('TP. Hồ Chí Minh', 'TP. Hồ Chí Minh', SC_SRC),
    building_types: ev(['nha_pho', 'biet_thu'], 'Nhà phố · Biệt thự', SC_SRC),
    years_experience: unavailable(),
    founded_year: unavailable(),
    staff: unavailable(),
    rough_price: unavailable(),
    finish_price: unavailable(),
    typical_project_value: unavailable(),
    dezon_project_count: ev(8, '8 công trình', SC_SRC),
    warranty: unavailable(),
    profile_completeness: ev(0.3, '30%', SC_SRC),
  },
};

export const HUY_HOANG: ContractorCard = {
  id: 3,
  slug: 'huy-hoang',
  name: 'Huy Hoàng',
  badge: null,
  avatar_url: null,
  profile_url: `${SITE}/nha-thau/huy-hoang`,
  profile_source: HH_SRC,
  cover_images: [],
  evidence: {
    province: ev('TP. Hồ Chí Minh', 'TP. Hồ Chí Minh', HH_SRC, 'on_dezon', true),
    building_types: ev(['nha_pho'], 'Nhà phố', HH_SRC, 'on_dezon', true),
    years_experience: unavailable(),
    founded_year: unavailable(),
    staff: unavailable(),
    rough_price: unavailable(),
    finish_price: unavailable(),
    typical_project_value: unavailable(),
    dezon_project_count: ev(6, '6 công trình', HH_SRC, 'on_dezon', true),
    warranty: unavailable(),
    profile_completeness: ev(0.2, '20%', HH_SRC, 'on_dezon', true),
  },
};

export const CONTRACTORS: ContractorCard[] = [KIEN_PHONG, SMARTCONS, HUY_HOANG];

// Mô phỏng Matching Engine backend (app/engines/matching: scorer + reasons) cho 3 nhà thầu mẫu, chỉ cho khu vực mẫu
// (nhà phố tại TP. Hồ Chí Minh). Các điểm không phụ thuộc phạm vi/ngân sách chép từ engine với cùng dữ kiện; điểm
// ngân sách, chi phí ước, lý do và lưu ý tính lại theo phạm vi + ngân sách đúng như scorer.py / reasons.py.

const MATCH_WEIGHTS = {
  location: 0.2,
  building_type: 0.2,
  budget_fit: 0.2,
  experience: 0.15,
  similar_projects: 0.15,
  profile_quality: 0.1,
} as const;
type CriterionKeyMock = keyof typeof MATCH_WEIGHTS;
const CRITERIA_LABEL_VI: Record<CriterionKeyMock, string> = {
  location: 'Khu vực hoạt động',
  building_type: 'Loại công trình đã thực hiện',
  budget_fit: 'Mức độ phù hợp ngân sách',
  experience: 'Kinh nghiệm và năng lực',
  similar_projects: 'Dự án thực tế tương tự',
  profile_quality: 'Mức độ đầy đủ và xác minh hồ sơ',
};
const NEUTRAL = 0.5;
const BUDGET_BAND_LOW = 0.9;
const OVERRUN_ZERO_AT = 0.4;
const UNDER_BUDGET_SCORE = 0.8;
const MAX_REASONS = 5;

type PriceKind = 'rough' | 'finish';
/** Hồ sơ Dezon: "Đơn giá xây thô" (rough) và "Đơn giá hoàn thiện cơ bản" = trọn gói (finish); Hoàn thiện/Nội thất: không có. */
const SCOPE_PRICE_KIND: Record<WorkScope, PriceKind | null> = {
  xay_tho: 'rough',
  tron_goi: 'finish',
  hoan_thien: null,
  noi_that: null,
};
const PRICE_KIND_VI: Record<PriceKind, string> = { rough: 'xây thô', finish: 'trọn gói (hoàn thiện cơ bản)' };
const PRICE_EVIDENCE_KEY: Record<PriceKind, 'rough_price' | 'finish_price'> = { rough: 'rough_price', finish: 'finish_price' };

interface MockFacts {
  card: ContractorCard;
  projectCount: number;
  prices: Partial<Record<PriceKind, [number, number]>>;
  experience: [number, string];
  profileQuality: [number, string];
  /** Nhận thi công … (building_types của hồ sơ, chữ thường). */
  typesText: string;
  /** Lý do sau lý do đơn giá (kinh nghiệm, nhân sự). */
  tailReasons: Reason[];
  hasYears: boolean;
  /** missing_important_fields ≥ 2 (thiếu đơn giá + kinh nghiệm) -> luôn "Cần đánh giá thêm". */
  missingImportant: boolean;
  staleDate?: string;
}

const MOCK_FACTS: MockFacts[] = [
  {
    card: KIEN_PHONG,
    projectCount: 7,
    prices: { rough: [4_300_000, 4_700_000], finish: [7_300_000, 9_500_000] },
    experience: [0.88, '8 năm kinh nghiệm; 32 nhân sự'],
    profileQuality: [0.63, 'Hồ sơ đầy đủ 90%, trường đã xác minh 0%'],
    typesText: 'nhà phố, biệt thự, căn hộ',
    tailReasons: [
      { text: '8 năm kinh nghiệm', evidence: 'years_experience' },
      { text: 'Nhân sự 20 thợ, 12 văn phòng', evidence: 'staff' },
    ],
    hasYears: true,
    missingImportant: false,
  },
  {
    card: SMARTCONS,
    projectCount: 8,
    prices: {},
    experience: [0, 'Chưa có số năm kinh nghiệm; chưa có thông tin nhân sự'],
    profileQuality: [0.21, 'Hồ sơ đầy đủ 30%, trường đã xác minh 0%'],
    typesText: 'nhà phố, biệt thự',
    tailReasons: [],
    hasYears: false,
    missingImportant: true,
  },
  {
    card: HUY_HOANG,
    projectCount: 6,
    prices: {},
    experience: [0, 'Chưa có số năm kinh nghiệm; chưa có thông tin nhân sự'],
    profileQuality: [0.14, 'Hồ sơ đầy đủ 20%, trường đã xác minh 0%'],
    typesText: 'nhà phố',
    tailReasons: [],
    hasYears: false,
    missingImportant: true,
    staleDate: '15/03/2026',
  },
];

/** backend formatting.vi_number(x, max_decimals, min_decimals). */
function viNumberMin(x: number, maxDecimals: number, minDecimals: number): string {
  const s = viNumber(x, maxDecimals);
  const [int, frac = ''] = s.split(',');
  const padded = frac.padEnd(Math.min(minDecimals, maxDecimals), '0');
  return padded ? `${int},${padded}` : int!;
}
/** backend formatting.format_vnd_range: hai đầu cùng đơn vị -> cùng số chữ số thập phân ("2,72–3,60 tỷ"). */
function vndRange(lo: number, hi: number): string {
  const unit = (a: number) => (Math.abs(a) >= 1e9 ? ([1e9, 'tỷ', 3] as const) : ([1e6, 'triệu', 1] as const));
  const [dl, ul, decl] = unit(lo);
  const [dh, uh, dech] = unit(hi);
  if (ul !== uh) return `${formatVnd(lo)}–${formatVnd(hi)}`;
  if (lo === hi) return formatVnd(lo);
  const decimals = (s: string) => (s.split(',')[1] ?? '').length;
  const width = Math.max(decimals(viNumber(lo / dl, decl)), decimals(viNumber(hi / dh, dech)));
  return `${viNumberMin(lo / dl, decl, width)}–${viNumberMin(hi / dh, dech, width)} ${uh}`;
}

const roundToMillion = (x: number) => Math.round(x / 1e6) * 1e6;
const round = (x: number, d: number) => Math.round(x * 10 ** d) / 10 ** d;

/** scorer.budget_score (quy tắc 7.3). */
function budgetScore(cost: [number, number], budget: number): [number, '' | 'over' | 'under'] {
  const [lo, hi] = cost;
  if (lo > budget) return [Math.max(0, 1 - (lo - budget) / budget / OVERRUN_ZERO_AT), 'over'];
  if (hi >= BUDGET_BAND_LOW * budget) return [1, ''];
  return [UNDER_BUDGET_SCORE, 'under'];
}

function scoreMock(f: MockFacts, scope: WorkScope, area: number, budget: number | null, district: string | null): MatchedContractor {
  const kind = SCOPE_PRICE_KIND[scope];
  const price = kind ? f.prices[kind] : undefined;
  const est: [number, number] | null = price && area > 0 ? [roundToMillion(area * price[0]), roundToMillion(area * price[1])] : null;
  let bud: number = NEUTRAL;
  let budNote: string;
  let flag: '' | 'over' | 'under' = '';
  if (!kind) {
    budNote = `Nhà thầu chưa công bố đơn giá riêng cho phạm vi ${WORK_SCOPE_VI[scope].toLowerCase()} trên Dezon — tính trung tính`;
  } else if (!price) {
    budNote = `Chưa có đơn giá ${PRICE_KIND_VI[kind]} — tính trung tính, cần xác minh mức giá`;
  } else if (!budget || budget <= 0) {
    budNote = 'Anh/chị chưa cung cấp ngân sách — tính trung tính';
  } else if (!est) {
    budNote = 'Chưa có diện tích quy đổi từ khái toán — tính trung tính';
  } else {
    [bud, flag] = budgetScore(est, budget);
    const costTxt = vndRange(est[0], est[1]);
    budNote =
      flag === 'over'
        ? `Chi phí ước ${costTxt} vượt ngân sách ${formatVnd(budget)}`
        : flag === 'under'
          ? `Chi phí ước ${costTxt} thấp hơn nhiều so với ngân sách ${formatVnd(budget)} — cần đối chiếu phạm vi`
          : `Chi phí ước ${costTxt} phù hợp ngân sách ${formatVnd(budget)}`;
  }

  const raw: Record<CriterionKeyMock, [number, string]> = {
    location: [0.8, `Khớp tỉnh/thành ${MOCK_RATE_PROVINCE}${district ? `; chưa ghi nhận hoạt động tại ${district}` : ''}`],
    building_type: [0.7, 'Có năng lực thi công nhà phố, chưa có dự án nhà phố trong danh sách trên Dezon'],
    budget_fit: [bud, budNote],
    experience: f.experience,
    similar_projects: [0, `0 dự án nhà phố tại ${MOCK_RATE_PROVINCE}, 4±1 tầng trên Dezon`],
    profile_quality: f.profileQuality,
  };
  const keys = Object.keys(MATCH_WEIGHTS) as CriterionKeyMock[];
  const breakdown = keys.map((k) => ({
    key: k,
    label_vi: CRITERIA_LABEL_VI[k],
    score: round(raw[k][0], 3),
    weight: MATCH_WEIGHTS[k],
    weighted: round(raw[k][0] * MATCH_WEIGHTS[k] * 100, 2),
    note: raw[k][1],
  }));
  const score = Math.round(round(keys.reduce((s, k) => s + raw[k][0] * MATCH_WEIGHTS[k], 0) * 100, 6));
  const fitLabel = f.missingImportant || score < 70 ? 'Cần đánh giá thêm' : score >= 85 ? 'Phù hợp cao' : 'Phù hợp';

  const key = kind ? PRICE_EVIDENCE_KEY[kind] : null;
  const reasons: Reason[] = [
    { text: `Nhận thi công ${f.typesText}`, evidence: 'building_types' },
    { text: `Hoạt động tại ${MOCK_RATE_PROVINCE}`, evidence: 'province' },
    { text: `${f.projectCount} công trình ghi nhận trên Dezon`, evidence: 'dezon_project_count' },
  ];
  if (kind && key && price) {
    const fits = est && (budget ?? 0) > 0 && bud >= 1 ? ', chi phí ước phù hợp ngân sách của anh/chị' : '';
    reasons.push({ text: `Đơn giá ${PRICE_KIND_VI[kind]} ${f.card.evidence[key]!.display}${fits}`, evidence: key });
  }
  reasons.push(...f.tailReasons);

  const cautions: Caution[] = [];
  if (!kind) {
    cautions.push({
      text: `Nhà thầu chưa công bố đơn giá riêng cho phạm vi ${WORK_SCOPE_VI[scope].toLowerCase()} trên Dezon — cần yêu cầu báo giá riêng`,
    });
  } else if (!price) {
    cautions.push({ text: 'Chưa có thông tin đơn giá — cần đối chiếu', evidence: key });
  }
  if (!f.hasYears) cautions.push({ text: 'Chưa có thông tin kinh nghiệm', evidence: 'years_experience' });
  cautions.push({ text: 'Chưa có thông tin bảo hành', evidence: 'warranty' });
  if (flag === 'over') {
    cautions.push({ text: 'Chi phí ước theo đơn giá nhà thầu vượt ngân sách của anh/chị — cần trao đổi thêm', evidence: key });
  } else if (flag === 'under') {
    cautions.push({ text: 'Chi phí ước thấp hơn nhiều so với ngân sách — cần đối chiếu phạm vi và vật liệu', evidence: key });
  }
  cautions.push({ text: 'Chưa có dự án nhà phố trong danh sách công trình trên Dezon — nên xem thêm hồ sơ' });
  if (f.staleDate) cautions.push({ text: `Một số thông tin hồ sơ cập nhật ngày ${f.staleDate}, có thể đã cũ` });

  const profile = f.card.profile_source;
  const estimatedCost =
    est && kind && profile
      ? ev(est, vndRange(est[0], est[1]), {
          label: `Ước tính = diện tích quy đổi ${formatM2(area)} × đơn giá ${PRICE_KIND_VI[kind]} trên hồ sơ ${f.card.name}`,
          url: profile.url,
          updated_at: profile.updated_at,
        })
      : unavailable();

  return {
    rank: 0,
    score,
    fit_label: fitLabel,
    breakdown,
    reasons: reasons.slice(0, MAX_REASONS),
    cautions,
    estimated_cost: estimatedCost,
    relaxed_location: false,
    contractor: f.card,
  };
}

export const MOCK_MATCH_NOTE = 'Dữ liệu giả lập để kiểm tra giao diện — không phải kết quả thật.';
export const MOCK_MATCH_NO_DATA_NOTE = `Chế độ giả lập chỉ có dữ liệu nhà thầu nhà phố tại ${MOCK_RATE_PROVINCE}.`;

export interface MockMatchOptions {
  where?: MockEstimateWhere;
  /** Ngân sách khách đã nêu (slot budget_vnd); null -> tiêu chí ngân sách trung tính như backend. */
  budgetVnd?: number | null;
}

/**
 * Kết quả tìm nhà thầu theo ĐÚNG phạm vi / loại công trình / tỉnh của khái toán, mô phỏng backend:
 * - Xây thô: chi phí ước = diện tích quy đổi × "Đơn giá xây thô" của nhà thầu; Trọn gói: × "Đơn giá hoàn thiện cơ bản"
 *   (= trọn gói), lý do "Đơn giá trọn gói (hoàn thiện cơ bản) …".
 * - Hoàn thiện / Nội thất: nhà thầu chưa công bố đơn giá riêng -> không ước chi phí, điểm ngân sách trung tính.
 * - Ngoài khu vực mẫu (khác nhà phố tại TP. Hồ Chí Minh): không có dữ liệu giả lập -> danh sách rỗng (không mượn
 *   nhà thầu TP. Hồ Chí Minh).
 */
export function matchFixtureFor(scope: WorkScope = 'xay_tho', convertedAreaM2?: number, opts: MockMatchOptions = {}): MatchResult {
  const where = opts.where ?? SAMPLE_WHERE;
  const buildingType = where.building_type ?? MOCK_RATE_BUILDING_TYPE;
  const province = where.province ?? MOCK_RATE_PROVINCE;
  const district = where.district ?? null;
  const area = convertedAreaM2 ?? estimateFixtureFor(scope, where).converted_area_m2;
  const budget = opts.budgetVnd ?? null;
  const covered = buildingType === MOCK_RATE_BUILDING_TYPE && province === MOCK_RATE_PROVINCE;
  const results = covered
    ? MOCK_FACTS.map((f) => scoreMock(f, scope, area, budget, district))
        .sort(
          (a, b) =>
            b.score - a.score ||
            (Number(b.contractor.evidence.dezon_project_count?.value) || 0) - (Number(a.contractor.evidence.dezon_project_count?.value) || 0) ||
            a.contractor.name.localeCompare(b.contractor.name, 'vi'),
        )
        .map((m, i) => ({ ...m, rank: i + 1 }))
    : [];
  const notes = [MOCK_MATCH_NOTE];
  if (!covered) notes.push(MOCK_MATCH_NO_DATA_NOTE);
  if (!budget) notes.push('Anh/chị chưa cung cấp ngân sách nên tiêu chí ngân sách được tính trung tính.');
  return {
    recommendation_log_id: 101,
    weights_version: 1,
    criteria: {
      province,
      district,
      building_type: buildingType,
      work_scope: scope,
      budget_vnd: budget,
      floors: 4,
      converted_area_m2: area,
      estimate_session_id: MOCK_ESTIMATE_SESSION_ID,
      limit: 5,
    },
    total_candidates: results.length,
    relaxed_to_neighbors: false,
    results,
    notes,
  };
}

/** Ca mẫu: nhà phố TP. Thủ Đức, Xây thô, 490 m² quy đổi, ngân sách 2 tỷ. */
export const MATCH_FIXTURE: MatchResult = matchFixtureFor('xay_tho', 490, { budgetVnd: 2_000_000_000 });

// ---------------------------------------------------------------- hồ sơ mở rộng

function kpProjects() {
  return KP_PROJECTS.map((p, i) => ({
    id: i + 1,
    slug: p.slug,
    name: p.name,
    building_type: 'biet_thu' as const,
    province: p.province,
    district: p.district,
    floors: null,
    status: null,
    start_date: i === 2 ? '2024-03-01' : null,
    end_date: i === 2 ? '2025-01-15' : null,
    project_url: p.url,
    role: 'thi_cong',
    contractor_names: ['Kiến Phong'],
    images: kpProjectImages(p),
    source: { label: `Dự án ${p.name}`, url: p.url, updated_at: OBSERVED },
  }));
}

function profileInfo(c: ContractorCard, workers: EvidenceValue, office: EvidenceValue) {
  return {
    founded_year: c.evidence.founded_year ?? unavailable(),
    staff_workers: workers,
    staff_office: office,
    rough_price: c.evidence.rough_price ?? unavailable(),
    finish_price: c.evidence.finish_price ?? unavailable(),
    typical_project_value: c.evidence.typical_project_value ?? unavailable(),
    dezon_project_count: c.evidence.dezon_project_count ?? unavailable(),
    warranty: c.evidence.warranty ?? unavailable(),
  };
}

export const PROFILES: Record<string, ContractorProfile> = {
  'kien-phong': {
    card: KIEN_PHONG,
    description: ev(
      'Kiến Phong là đơn vị thiết kế và thi công nhà phố, biệt thự, căn hộ tại TP. Hồ Chí Minh, theo phong cách Indochine và hiện đại.',
      'Kiến Phong là đơn vị thiết kế và thi công nhà phố, biệt thự, căn hộ tại TP. Hồ Chí Minh, theo phong cách Indochine và hiện đại.',
      KP_SRC,
    ),
    info: profileInfo(KIEN_PHONG, ev(20, '20 thợ', KP_SRC), ev(12, '12 người', KP_SRC)),
    projects: kpProjects(),
    images: KP_IMAGES,
    total_images: KP_IMAGES.length,
    chips: ['Hồ sơ nhà thầu', 'Dự án thực tế', 'Đơn giá tham khảo'],
  },
  'smartcons-tech': {
    card: SMARTCONS,
    description: ev(
      'Đơn vị thi công xây dựng nhà ở tại TP. Hồ Chí Minh.',
      'Đơn vị thi công xây dựng nhà ở tại TP. Hồ Chí Minh.',
      SC_SRC,
    ),
    info: profileInfo(SMARTCONS, unavailable(), unavailable()),
    projects: [],
    images: [],
    total_images: 0,
    chips: ['Hồ sơ nhà thầu', 'Dự án thực tế', 'Đơn giá tham khảo'],
  },
  'huy-hoang': {
    card: HUY_HOANG,
    description: unavailable(),
    info: profileInfo(HUY_HOANG, unavailable(), unavailable()),
    projects: [],
    images: [],
    total_images: 0,
    chips: ['Hồ sơ nhà thầu', 'Dự án thực tế', 'Đơn giá tham khảo'],
  },
};

// ---------------------------------------------------------------- bảng so sánh

const PROJECTS_CELL: Record<number, EvidenceValue> = {
  1: ev(3, '3 dự án có ảnh', KP_SRC),
};

/** Bảng so sánh các nhà thầu trong kết quả tìm kiếm vừa trả (cùng phạm vi/ngân sách -> cùng điểm phù hợp). */
export function buildComparison(ids: number[], results: MatchedContractor[] = MATCH_FIXTURE.results): ComparisonMatrix {
  const matched = ids
    .map((id) => results.find((m) => m.contractor.id === id))
    .filter((m): m is MatchedContractor => Boolean(m));
  const col = (fn: (m: MatchedContractor) => EvidenceValue | undefined) => matched.map((m) => fn(m) ?? unavailable());
  const rows: ComparisonRow[] = [
    { key: 'area', label_vi: 'Khu vực', cells: col((m) => m.contractor.evidence.province) },
    { key: 'dezon_project_count', label_vi: 'Công trình ghi nhận trên Dezon', cells: col((m) => m.contractor.evidence.dezon_project_count) },
    { key: 'experience', label_vi: 'Kinh nghiệm', cells: col((m) => m.contractor.evidence.years_experience) },
    { key: 'rough_price', label_vi: 'Đơn giá thô', cells: col((m) => m.contractor.evidence.rough_price) },
    { key: 'finish_price', label_vi: 'Đơn giá trọn gói (hoàn thiện cơ bản)', cells: col((m) => m.contractor.evidence.finish_price) },
    { key: 'projects', label_vi: 'Hồ sơ dự án', cells: col((m) => PROJECTS_CELL[m.contractor.id]) },
    { key: 'warranty', label_vi: 'Bảo hành', cells: col((m) => m.contractor.evidence.warranty) },
    {
      key: 'fit',
      label_vi: 'Mức phù hợp',
      cells: col((m) => ev(m.score, `${m.fit_label} (${m.score}/100)`, MATCHING_SOURCE, 'reference')),
    },
  ];
  return {
    contractors: matched.map((m) => ({
      id: m.contractor.id,
      slug: m.contractor.slug,
      name: m.contractor.name,
      profile_url: m.contractor.profile_url,
      avatar_url: m.contractor.avatar_url ?? null,
    })),
    rows,
  };
}

export const COMPARISON_FIXTURE: ComparisonMatrix = buildComparison([1, 2, 3]);
