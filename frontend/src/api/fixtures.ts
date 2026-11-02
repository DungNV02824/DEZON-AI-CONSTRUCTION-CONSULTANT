/**
 * Dữ liệu giả lập cho chế độ mock (data-mock="true" hoặc ?dezon_mock=1) và cho test giao diện.
 * Bám theo seed dev (backend/app/db/seed.py) và ca nghiệm thu mục 13:
 *   100 m² đất, 4 tầng, móng cọc, mái BTCT, đơn giá 4,2–4,8 triệu/m² -> 490 m², 2,058–2,352 tỷ.
 * Mọi chuỗi `display` ở đây mô phỏng đầu ra của backend — frontend không tự định dạng số tiền.
 * KHÔNG dùng làm dữ liệu thật.
 */
import type {
  BudgetAdjustmentResult,
  ComparisonMatrix,
  ComparisonRow,
  ContractorCard,
  ContractorProfile,
  EstimateResult,
  EvidenceValue,
  ImageRef,
  MatchResult,
  MatchedContractor,
  SourceBadgesBlock,
  SourceRef,
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

const RATE_SOURCE: SourceRef = {
  label: 'Đơn giá tham khảo (seed dev — chờ Dezon cung cấp bảng giá)',
  url: null,
  updated_at: OBSERVED,
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

export const ESTIMATE_FIXTURE: EstimateResult = {
  estimate_session_id: MOCK_ESTIMATE_SESSION_ID,
  input: {
    building_type: 'nha_pho',
    province: 'TP. Hồ Chí Minh',
    district: 'TP. Thủ Đức',
    land_width_m: 5,
    land_length_m: 20,
    land_area_m2: 100,
    building_area_m2: 100,
    floors: 4,
    has_mezzanine: false,
    basement: false,
    roof_type: 'roof_concrete',
    rooftop_terrace: null,
    foundation_type: 'foundation_pile',
    work_scope: 'xay_tho',
    quality_tier: 'trung_binh',
  },
  items: [
    { rule_code: 'foundation_pile', label_vi: 'Móng cọc (chưa gồm ép cọc)', group_name: 'mong', area_m2: 100, coefficient: 0.4, converted_m2: 40 },
    { rule_code: 'floor_standard', label_vi: 'Tầng trệt', group_name: 'tang', area_m2: 100, coefficient: 1, converted_m2: 100 },
    { rule_code: 'floor_standard', label_vi: 'Lầu 1', group_name: 'tang', area_m2: 100, coefficient: 1, converted_m2: 100 },
    { rule_code: 'floor_standard', label_vi: 'Lầu 2', group_name: 'tang', area_m2: 100, coefficient: 1, converted_m2: 100 },
    { rule_code: 'floor_standard', label_vi: 'Lầu 3', group_name: 'tang', area_m2: 100, coefficient: 1, converted_m2: 100 },
    { rule_code: 'roof_concrete', label_vi: 'Mái bê tông cốt thép', group_name: 'mai', area_m2: 100, coefficient: 0.5, converted_m2: 50 },
  ],
  converted_area_m2: 490,
  cost_min: 2_058_000_000,
  cost_max: 2_352_000_000,
  cost: ev([2_058_000_000, 2_352_000_000], '2,058–2,352 tỷ', RATE_SOURCE, 'reference'),
  rate: ev([4_200_000, 4_800_000], '4,2–4,8 triệu/m²', RATE_SOURCE, 'reference'),
  rate_id: 1,
  rules_version: 1,
  rules_status: 'reference',
  assumptions: [
    'Diện tích xây dựng mỗi tầng bằng diện tích đất (100 m²) vì anh/chị chưa cung cấp.',
    'Móng cọc — mặc định cho nhà từ 4 tầng nổi; chưa gồm chi phí ép cọc.',
    'Mái bê tông cốt thép, diện tích mái bằng diện tích xây dựng.',
    'Không có tầng hầm, không có tầng lửng.',
    'Mức hoàn thiện trung bình.',
  ],
  exclusions: [
    'Ép cọc / khoan cọc nhồi',
    'Chi phí xin giấy phép xây dựng',
    'Thiết kế kiến trúc – kết cấu',
    'Nội thất rời',
    'Điện nước ngoài nhà',
  ],
  cost_drivers: [
    'Hẻm nhỏ, xe vật tư không vào được',
    'Nền đất yếu cần gia cố thêm',
    'Làm thêm tầng hầm hoặc mái ngói',
    'Nhiều ban công, mặt tiền phức tạp',
  ],
  disclaimer:
    'Đây là khái toán sơ bộ dựa trên đơn giá tham chiếu (Đơn giá tham khảo — seed dev, cập nhật 31/10/2026) và các giả định nêu trên, chưa tính điều kiện thực tế của khu đất. Không phải báo giá thi công. Vui lòng yêu cầu báo giá từ nhà thầu để có con số chính xác.',
  warnings: ['Hệ số khái toán đang là dữ liệu tham khảo, chờ Dezon phê duyệt.'],
};

export const ESTIMATE_SOURCE_BADGES: SourceBadgesBlock = {
  type: 'source_badges',
  badges: [
    { label: 'Đơn giá tham khảo', status: 'reference', source: RATE_SOURCE },
    { label: 'Hệ số khái toán', status: 'reference', source: RULES_SOURCE },
  ],
};

export const BUDGET_FIXTURE: BudgetAdjustmentResult = {
  estimate_session_id: MOCK_ESTIMATE_SESSION_ID,
  target_budget_vnd: 1_500_000_000,
  target_budget: ev(1_500_000_000, '1,5 tỷ', { label: 'Khách cung cấp', url: null, updated_at: null }, 'reference'),
  current_cost: ESTIMATE_FIXTURE.cost,
  options: [
    {
      code: 'reduce_floor_lower_quality',
      label_vi: 'Giảm 1 tầng và chọn mức tiết kiệm',
      description: 'Xây 1 trệt 2 lầu với vật tư mức tiết kiệm.',
      changes: { floors: 3, quality_tier: 'tiet_kiem' },
      converted_area_m2: 390,
      cost_min: 1_474_000_000,
      cost_max: 1_685_000_000,
      cost: ev([1_474_000_000, 1_685_000_000], '1,474–1,685 tỷ', RATE_SOURCE, 'reference'),
      saving_min: 584_000_000,
      fits_budget: true,
    },
    {
      code: 'reduce_floor',
      label_vi: 'Giảm 1 tầng',
      description: 'Xây 1 trệt 2 lầu, giữ nguyên mức hoàn thiện trung bình.',
      changes: { floors: 3 },
      converted_area_m2: 390,
      cost_min: 1_638_000_000,
      cost_max: 1_872_000_000,
      cost: ev([1_638_000_000, 1_872_000_000], '1,638–1,872 tỷ', RATE_SOURCE, 'reference'),
      saving_min: 420_000_000,
      fits_budget: false,
    },
    {
      code: 'lower_quality',
      label_vi: 'Chọn mức tiết kiệm',
      description: 'Giữ 4 tầng, dùng vật tư mức tiết kiệm.',
      changes: { quality_tier: 'tiet_kiem' },
      converted_area_m2: 490,
      cost_min: 1_852_000_000,
      cost_max: 2_117_000_000,
      cost: ev([1_852_000_000, 2_117_000_000], '1,852–2,117 tỷ', RATE_SOURCE, 'reference'),
      saving_min: 206_000_000,
      fits_budget: false,
    },
    {
      code: 'change_roof',
      label_vi: 'Đổi sang mái tôn',
      description: 'Thay mái bê tông cốt thép bằng mái tôn.',
      changes: { roof_type: 'roof_metal' },
      converted_area_m2: 470,
      cost_min: 1_974_000_000,
      cost_max: 2_256_000_000,
      cost: ev([1_974_000_000, 2_256_000_000], '1,974–2,256 tỷ', RATE_SOURCE, 'reference'),
      saving_min: 84_000_000,
      fits_budget: false,
    },
  ],
};

// ---------------------------------------------------------------- nhà thầu

const placeholder = (text: string, size = '800x600') => `https://placehold.co/${size}?text=${encodeURIComponent(text).replace(/%20/g, '+')}`;

const KP_SRC = profileSource('kien-phong', 'Kiến Phong');
const SC_SRC = profileSource('smartcons-tech', 'Smartcons Tech');
// Huy Hoàng: ngày cập nhật cũ để minh họa cảnh báo "có thể đã cũ" (is_stale).
const HH_SRC = profileSource('huy-hoang', 'Huy Hoàng', '2026-03-15');

const KP_PROJECTS = [
  { slug: 'france-indochine-22-ha-noi', name: 'France Indochine 22 Hà Nội', province: 'Hà Nội', district: null },
  { slug: 'green-pool-villa-vinh-long', name: 'Green Pool Villa Vĩnh Long', province: 'Vĩnh Long', district: null },
  { slug: 'indochine-villa-quan-7', name: 'Indochine Villa Quận 7 Sài Gòn', province: 'TP. Hồ Chí Minh', district: 'Quận 7' },
];

const KP_IMAGES: ImageRef[] = Array.from({ length: 7 }, (_, i) => ({
  url: placeholder(`Project ${i + 1}`),
  caption: `${KP_PROJECTS[i % 3]!.name} (ảnh minh họa dev)`,
  project_slug: KP_PROJECTS[i % 3]!.slug,
}));

export const KIEN_PHONG: ContractorCard = {
  id: 1,
  slug: 'kien-phong',
  name: 'Kiến Phong',
  badge: 'Nhà thầu tiềm năng',
  avatar_url: placeholder('Kiến Phong', '160x160'),
  profile_url: `${SITE}/nha-thau/kien-phong`,
  profile_source: KP_SRC,
  cover_images: KP_IMAGES.slice(0, 3),
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

const crit = (key: string, label: string, score: number, weight: number, note: string) => ({
  key,
  label_vi: label,
  score,
  weight,
  weighted: Math.round(score * weight * 1000) / 10,
  note,
});

const MATCHED: MatchedContractor[] = [
  {
    rank: 1,
    score: 76,
    fit_label: 'Phù hợp',
    breakdown: [
      crit('location', 'Khu vực hoạt động', 0.8, 0.2, 'Khớp tỉnh/thành'),
      crit('building_type', 'Loại công trình', 1, 0.2, 'Có năng lực và dự án cùng loại'),
      crit('budget_fit', 'Phù hợp ngân sách', 0.865, 0.2, 'Chi phí ước tính vượt ngân sách khoảng 5%'),
      crit('experience', 'Kinh nghiệm và năng lực', 0.88, 0.15, '8 năm, 32 nhân sự'),
      crit('similar_projects', 'Dự án tương tự', 0.2, 0.15, '1 dự án tương tự'),
      crit('profile_quality', 'Hồ sơ đầy đủ', 0.63, 0.1, 'Hồ sơ đủ 90% trường'),
    ],
    reasons: [
      { text: 'Có kinh nghiệm thi công nhà phố, biệt thự, căn hộ', evidence: 'building_types' },
      { text: 'Hoạt động tại TP. Hồ Chí Minh', evidence: 'province' },
      { text: '7 công trình ghi nhận trên Dezon', evidence: 'dezon_project_count' },
      { text: 'Đơn giá xây thô 4,3–4,7 triệu/m²', evidence: 'rough_price' },
    ],
    cautions: [{ text: 'Chưa có thông tin bảo hành', evidence: 'warranty' }],
    estimated_cost: ev([2_107_000_000, 2_303_000_000], '2,107–2,303 tỷ', KP_SRC, 'reference'),
    relaxed_location: false,
    contractor: KIEN_PHONG,
  },
  {
    rank: 2,
    score: 42,
    fit_label: 'Cần đánh giá thêm',
    breakdown: [
      crit('location', 'Khu vực hoạt động', 0.8, 0.2, 'Khớp tỉnh/thành'),
      crit('building_type', 'Loại công trình', 0.7, 0.2, 'Có năng lực, chưa có dự án cùng loại'),
      crit('budget_fit', 'Phù hợp ngân sách', 0.5, 0.2, 'Chưa có đơn giá — điểm trung tính'),
      crit('experience', 'Kinh nghiệm và năng lực', 0, 0.15, 'Chưa có dữ liệu'),
      crit('similar_projects', 'Dự án tương tự', 0, 0.15, 'Chưa có dự án tương tự'),
      crit('profile_quality', 'Hồ sơ đầy đủ', 0.21, 0.1, 'Hồ sơ đủ 30% trường'),
    ],
    reasons: [
      { text: 'Hoạt động tại TP. Hồ Chí Minh', evidence: 'province' },
      { text: '8 công trình ghi nhận trên Dezon', evidence: 'dezon_project_count' },
      { text: 'Có năng lực thi công nhà phố, biệt thự', evidence: 'building_types' },
    ],
    cautions: [
      { text: 'Chưa có thông tin đơn giá — cần đối chiếu', evidence: 'rough_price' },
      { text: 'Chưa có thông tin kinh nghiệm', evidence: 'years_experience' },
      { text: 'Chưa có thông tin bảo hành', evidence: 'warranty' },
    ],
    estimated_cost: unavailable(),
    relaxed_location: false,
    contractor: SMARTCONS,
  },
  {
    rank: 3,
    score: 41,
    fit_label: 'Cần đánh giá thêm',
    breakdown: [
      crit('location', 'Khu vực hoạt động', 0.8, 0.2, 'Khớp tỉnh/thành'),
      crit('building_type', 'Loại công trình', 0.7, 0.2, 'Có năng lực, chưa có dự án cùng loại'),
      crit('budget_fit', 'Phù hợp ngân sách', 0.5, 0.2, 'Chưa có đơn giá — điểm trung tính'),
      crit('experience', 'Kinh nghiệm và năng lực', 0, 0.15, 'Chưa có dữ liệu'),
      crit('similar_projects', 'Dự án tương tự', 0, 0.15, 'Chưa có dự án tương tự'),
      crit('profile_quality', 'Hồ sơ đầy đủ', 0.14, 0.1, 'Hồ sơ đủ 20% trường'),
    ],
    reasons: [
      { text: 'Hoạt động tại TP. Hồ Chí Minh', evidence: 'province' },
      { text: '6 công trình ghi nhận trên Dezon', evidence: 'dezon_project_count' },
    ],
    cautions: [
      { text: 'Chưa có thông tin đơn giá — cần đối chiếu', evidence: 'rough_price' },
      { text: 'Chưa có thông tin kinh nghiệm', evidence: 'years_experience' },
    ],
    estimated_cost: unavailable(),
    relaxed_location: false,
    contractor: HUY_HOANG,
  },
];

export const MATCH_FIXTURE: MatchResult = {
  recommendation_log_id: 101,
  weights_version: 1,
  criteria: {
    province: 'TP. Hồ Chí Minh',
    district: 'TP. Thủ Đức',
    building_type: 'nha_pho',
    work_scope: 'xay_tho',
    budget_vnd: 2_000_000_000,
    floors: 4,
    converted_area_m2: 490,
    estimate_session_id: MOCK_ESTIMATE_SESSION_ID,
    limit: 5,
  },
  total_candidates: 3,
  relaxed_to_neighbors: false,
  results: MATCHED,
  notes: ['Dữ liệu giả lập để kiểm tra giao diện — không phải kết quả thật.'],
};

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
    project_url: `${SITE}/du-an/${p.slug}`,
    role: 'thi_cong',
    contractor_names: ['Kiến Phong'],
    images: [KP_IMAGES[i]!],
    source: { label: `Dự án ${p.name}`, url: `${SITE}/du-an/${p.slug}`, updated_at: OBSERVED },
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

export function buildComparison(ids: number[]): ComparisonMatrix {
  const matched = ids
    .map((id) => MATCHED.find((m) => m.contractor.id === id))
    .filter((m): m is MatchedContractor => Boolean(m));
  const col = (fn: (m: MatchedContractor) => EvidenceValue | undefined) => matched.map((m) => fn(m) ?? unavailable());
  const rows: ComparisonRow[] = [
    { key: 'area', label_vi: 'Khu vực', cells: col((m) => m.contractor.evidence.province) },
    { key: 'dezon_project_count', label_vi: 'Công trình ghi nhận trên Dezon', cells: col((m) => m.contractor.evidence.dezon_project_count) },
    { key: 'experience', label_vi: 'Kinh nghiệm', cells: col((m) => m.contractor.evidence.years_experience) },
    { key: 'rough_price', label_vi: 'Đơn giá thô', cells: col((m) => m.contractor.evidence.rough_price) },
    { key: 'finish_price', label_vi: 'Đơn giá hoàn thiện', cells: col((m) => m.contractor.evidence.finish_price) },
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
