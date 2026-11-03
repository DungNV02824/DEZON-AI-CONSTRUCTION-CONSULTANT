/**
 * Client giả lập (chế độ mock): trả dữ liệu fixture đúng schema và mô phỏng luồng hội thoại mục 5.4,
 * kể cả stream văn bản dần dần. Dùng để kiểm tra giao diện khi chưa có backend.
 */
import type { ApiClient, StreamHandlers } from './client';
import {
  MOCK_ESTIMATE_SESSION_ID,
  PROFILES,
  SCOPES_WITHOUT_RATE,
  budgetFixtureFor,
  buildComparison,
  estimateFixtureFor,
  estimateSourceBadgesFor,
  matchFixtureFor,
} from './fixtures';
import type {
  ChatMessageOut,
  EstimateResult,
  MatchResult,
  QuestionStepBlock,
  QuickRepliesBlock,
  RoofType,
  SessionStateResponse,
  SummaryItem,
  Slots,
  UIBlock,
} from './types';
import { ApiError } from './client';
import { formatVnd } from '../utils/format';
import { newId } from '../utils/id';

export interface MockOptions {
  /** Độ trễ giữa các mẩu văn bản stream (ms). 0 trong test. */
  delayMs?: number;
  storage?: Storage | null;
}

interface MockSession {
  session_id: string;
  stage: string;
  slots: Slots;
  messages: ChatMessageOut[];
  nextId: number;
  /** Lượt hỏi bổ sung đang chạy ("Cung cấp thêm thông tin" / "Sửa" một dòng chi tiết); null/không có = không. */
  extra?: ExtraStep[] | null;
  /** Lượt bổ sung là "Sửa" một dòng: chỉ hỏi đúng câu đó (như backend: không hỏi tiếp độ sâu hầm / diện tích lửng). */
  extraEdit?: boolean;
}

interface MockReply {
  text: string;
  blocks: UIBlock[];
  stage?: string;
}

const STORAGE_PREFIX = 'dezon_mock_session:';

const qr = (labels: string[], allowFreeText = true): QuickRepliesBlock => ({
  type: 'quick_replies',
  options: labels.map((l) => ({ label: l, value: l })),
  allow_free_text: allowFreeText,
});

const BUILDING_LABEL: Record<string, string> = {
  nha_pho: 'Nhà phố',
  biet_thu: 'Biệt thự',
  can_ho: 'Căn hộ',
  van_phong: 'Văn phòng',
  khach_san: 'Khách sạn',
  khac: 'Khác',
};
const SCOPE_LABEL: Record<string, string> = {
  xay_tho: 'Xây thô',
  hoan_thien: 'Hoàn thiện',
  tron_goi: 'Trọn gói',
  noi_that: 'Nội thất',
};
/** Như backend dialog._ROOF_VI (giá trị trên thẻ xác nhận). */
const ROOF_LABEL: Partial<Record<RoofType, string>> = {
  roof_concrete: 'Mái bê tông cốt thép',
  roof_metal: 'Mái tôn',
  roof_tile_on_truss: 'Mái ngói kèo thép',
  roof_tile_on_concrete: 'Mái ngói đổ BTCT',
};

/** Bỏ dấu tiếng Việt + chữ thường để so khớp từ khóa. */
export function fold(s: string): string {
  return s
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/đ/g, 'd')
    .replace(/Đ/g, 'D')
    .toLowerCase();
}

/** Lời chào — câu hỏi loại công trình nằm trong thẻ question_step đi kèm (văn bản không lặp lại câu hỏi). */
const GREETING = 'Xin chào anh/chị! Em có thể hỗ trợ khái toán chi phí xây dựng và tìm nhà thầu phù hợp trên Dezon.';

/** Tin nhắn mà các nút của thẻ câu hỏi / thẻ xác nhận gửi lên (giống hợp đồng backend). */
export const BACK_VALUE = 'Quay lại câu trước';
export const SKIP_VALUE = 'Bỏ qua câu này';
export const MORE_VALUE = 'Tôi muốn cung cấp thêm thông tin';
export const CONFIRM_VALUE = 'Đúng rồi, tính khái toán giúp tôi';
export const CONFIRM_LABEL = 'Tính ngay → xem bảng khái toán';
export const MORE_LABEL = 'Cung cấp thêm thông tin để báo giá chính xác hơn';
const FREE_TEXT = 'Nhập câu trả lời của anh/chị';

function extractSlots(text: string, slots: Slots): Slots {
  const t = fold(text);
  const s: Slots = { ...slots };
  const bt: [RegExp, Slots['building_type']][] = [
    [/nha pho/, 'nha_pho'],
    [/biet thu/, 'biet_thu'],
    [/can ho/, 'can_ho'],
    [/van phong/, 'van_phong'],
    [/khach san/, 'khach_san'],
    [/cong trinh khac|^\s*khac\s*$/, 'khac'],
  ];
  for (const [re, v] of bt) if (re.test(t)) s.building_type = v;
  if (/ho chi minh|hcm|sai gon|thu duc/.test(t)) s.province = 'TP. Hồ Chí Minh';
  else if (/ha noi/.test(t)) s.province = 'Hà Nội';
  else if (/da nang/.test(t)) s.province = 'Đà Nẵng';
  if (/thu duc/.test(t)) s.district = 'TP. Thủ Đức';
  const land = t.match(/(\d+(?:[.,]\d+)?)\s*[x×*]\s*(\d+(?:[.,]\d+)?)/);
  if (land) {
    const w = Number(land[1]!.replace(',', '.'));
    const l = Number(land[2]!.replace(',', '.'));
    s.land_width_m = w;
    s.land_length_m = l;
    s.land_area_m2 = Math.round(w * l * 100) / 100;
  }
  const m2 = t.match(/(\d+(?:[.,]\d+)?)\s*m(?:2|²)/);
  if (!land && m2) s.land_area_m2 = Number(m2[1]!.replace(',', '.'));
  const tretLau = t.match(/1\s*tret\s*(\d+)\s*lau/);
  const tang = t.match(/(\d+)\s*tang/);
  if (tretLau) s.floors = Number(tretLau[1]) + 1;
  else if (tang) s.floors = Number(tang[1]);
  else if (/\b1\s*tret\b/.test(t) && !/\blau\b/.test(t)) s.floors = 1;
  if (/xay tho|phan tho/.test(t)) s.work_scope = 'xay_tho';
  else if (/hoan thien/.test(t)) s.work_scope = 'hoan_thien';
  else if (/tron goi/.test(t)) s.work_scope = 'tron_goi';
  else if (/noi that/.test(t)) s.work_scope = 'noi_that';
  const budget = t.match(/(\d+(?:[.,]\d+)?)\s*(ty|ti)\b/);
  if (budget) s.budget_vnd = Math.round(Number(budget[1]!.replace(',', '.')) * 1e9);
  // Chi tiết công trình nêu sẵn trong câu (vd "xây kín đất, không hầm không lửng, mái tôn").
  if (/kin dat/.test(t) && s.land_area_m2) s.building_area_m2 = s.land_area_m2;
  const levels = parseLevels(t);
  if (levels.basement !== undefined) s.basement = levels.basement;
  if (levels.has_mezzanine !== undefined) s.has_mezzanine = levels.has_mezzanine;
  const roof = parseRoof(t);
  if (roof) s.roof_type = roof;
  return s;
}

// ---------------------------------------------------------------- 5 câu hỏi cốt lõi + câu hỏi chi tiết (tùy chọn)

/**
 * Luồng hỏi (quyết định của chủ sản phẩm): ĐÚNG 5 câu trước thẻ xác nhận, bộ đếm luôn "i/5" theo vị trí cố định
 * (câu khách đã nêu sẵn được bỏ qua nhưng số thứ tự không đổi). Chi tiết (diện tích xây dựng, hầm/lửng, mái, độ sâu
 * hầm, diện tích lửng) KHÔNG hỏi trước tóm tắt — dùng mặc định của engine, nêu ở dòng "Tạm tính" trên thẻ xác nhận;
 * nút "Cung cấp thêm thông tin" hỏi lần lượt các chi tiết đó (bộ đếm "i/N" của nhóm câu bổ sung), rồi về thẻ xác nhận.
 * Ngân sách hỏi SAU khái toán; phong cách / mức đầu tư không hỏi.
 */
type CoreStep = 'building_type' | 'province' | 'land' | 'floors' | 'work_scope';
/** Câu hỏi chi tiết; khóa trùng tên slot (ghi vào `asked` khi khách "Chưa biết" / bỏ qua). */
type DetailStep = 'building_area_m2' | 'basement' | 'basement_depth_m' | 'mezzanine_area_m2' | 'roof_type';
/** Câu hỏi của một lượt bổ sung: chi tiết ("Cung cấp thêm thông tin", "Sửa" một dòng chi tiết) hoặc ngân sách ("Sửa"). */
type ExtraStep = DetailStep | 'budget_vnd';
type QKey = CoreStep | ExtraStep;

export const CORE_STEPS: CoreStep[] = ['building_type', 'province', 'land', 'floors', 'work_scope'];

const UNKNOWN_RE = /chua biet|khong biet|chua ro|khong ro/;
/** "ok"/"được" cho câu bổ sung = giữ mặc định (như backend: không phải xác nhận tính khái toán). */
const ACCEPT_DEFAULT_RE = /^(ok|oke|okay|duoc|dong y|vang|u|uh)$/;
const num = (x: string) => Number(x.replace(',', '.'));
const viNum = (n: number) => String(n).replace('.', ',');

const DEPTH_OPTIONS: [RegExp, number][] = [
  [/duoi 1[.,]3/, 1.2],
  [/1[.,]3\s*[–-]\s*1[.,]7/, 1.5],
  [/1[.,]7\s*[–-]\s*2/, 1.85],
  [/tren 2/, 2.2],
];

function parseLevels(t: string): { basement?: boolean; has_mezzanine?: boolean } {
  const out: { basement?: boolean; has_mezzanine?: boolean } = {};
  if (/\bham\b/.test(t)) out.basement = !/khong (co )?(tang )?ham/.test(t);
  if (/\blung\b/.test(t)) out.has_mezzanine = !/khong (co )?(tang )?lung/.test(t);
  return out;
}

function parseRoof(t: string): RoofType | null {
  if (/ngoi/.test(t)) return /keo|thep/.test(t) ? 'roof_tile_on_truss' : 'roof_tile_on_concrete';
  if (/mai ton|\bton\b/.test(t)) return 'roof_metal';
  if (/mai bang|mai be tong|mai btct/.test(t)) return 'roof_concrete';
  return null;
}

const coreComplete = (s: Slots) => Boolean(s.building_type && s.province && s.land_area_m2 && s.floors && s.work_scope);

/** Như backend `schemas.slots.details_apply`: câu hỏi chi tiết kết cấu không áp dụng cho căn hộ / phạm vi Nội thất. */
export function detailsApply(s: Slots): boolean {
  return s.building_type !== 'can_ho' && s.work_scope !== 'noi_that';
}

/** Như backend `default_assumptions`: Hoàn thiện / Nội thất / căn hộ không tính móng -> không nêu quy ước móng. */
const noFoundation = (s: Slots) => s.work_scope === 'hoan_thien' || s.work_scope === 'noi_that' || s.building_type === 'can_ho';

/** Diện tích một sàn làm mốc: diện tích xây dựng mỗi tầng, chưa có thì diện tích đất (như engine). */
const floorArea = (s: Slots) => s.building_area_m2 ?? s.land_area_m2 ?? null;

/**
 * Chi tiết khách CHƯA cung cấp (engine dùng mặc định) theo thứ tự hỏi: diện tích xây dựng, hầm/lửng, mái, rồi độ sâu
 * hầm / diện tích lửng nếu có. Rỗng -> không có dòng "Tạm tính", không có nút "Cung cấp thêm thông tin".
 */
export function missingDetails(s: Slots): DetailStep[] {
  if (!detailsApply(s)) return [];
  const out: DetailStep[] = [];
  if (s.building_area_m2 == null) out.push('building_area_m2');
  if (s.basement == null || s.has_mezzanine == null) out.push('basement');
  if (s.roof_type == null) out.push('roof_type');
  if (s.basement && s.basement_depth_m == null) out.push('basement_depth_m');
  if (s.has_mezzanine && s.mezzanine_area_m2 == null) out.push('mezzanine_area_m2');
  return out;
}

/** Dòng "Tạm tính: ..." liệt kê mặc định đang áp dụng; null khi khách đã cung cấp đủ chi tiết. */
export function defaultsLine(s: Slots): string | null {
  if (!detailsApply(s)) return null;
  const parts: string[] = [];
  if (s.building_area_m2 == null && s.land_area_m2) parts.push(`xây kín đất (${viNum(s.land_area_m2)} m²/tầng)`);
  if (s.basement == null) parts.push('không hầm');
  else if (s.basement && s.basement_depth_m == null) parts.push('hầm sâu 1,7–2 m');
  if (s.has_mezzanine == null) parts.push('không lửng');
  else if (s.has_mezzanine && s.mezzanine_area_m2 == null) {
    const f = floorArea(s);
    parts.push(`lửng bằng diện tích sàn${f ? ` (${viNum(f)} m²)` : ''}`);
  }
  if (s.roof_type == null) parts.push('mái bê tông cốt thép');
  if (!parts.length) return null;
  return `Tạm tính: ${parts.join(', ')} — anh/chị bấm Sửa hoặc Cung cấp thêm thông tin để thay đổi.`;
}

const asked = (s: Slots, k: string) => (s.asked ?? []).includes(k);

/** Câu hỏi của lượt bổ sung đã xong (có giá trị, hoặc khách "Chưa biết" / bỏ qua -> giữ mặc định). */
function extraDone(s: Slots, k: ExtraStep): boolean {
  if (asked(s, k)) return true;
  if (k === 'basement') return s.basement != null && s.has_mezzanine != null;
  return s[k] != null;
}

/** Danh sách câu của lượt bổ sung hiện tại; có hầm / có lửng -> thêm độ sâu hầm / diện tích lửng ở cuối. */
function extraPlan(session: MockSession): ExtraStep[] {
  const plan: ExtraStep[] = [...(session.extra ?? [])];
  const s = session.slots;
  if (plan.includes('basement') && !session.extraEdit) {
    if (s.basement && !plan.includes('basement_depth_m')) plan.push('basement_depth_m');
    if (s.has_mezzanine && !plan.includes('mezzanine_area_m2')) plan.push('mezzanine_area_m2');
  }
  return plan;
}

const pendingExtra = (session: MockSession): ExtraStep | null =>
  extraPlan(session).find((k) => !extraDone(session.slots, k)) ?? null;

/** Câu hỏi hiện hành: 5 câu cốt lõi, rồi câu của lượt bổ sung (nếu đang có); null = hiện thẻ xác nhận. */
function currentQuestion(session: MockSession): QKey | null {
  const s = session.slots;
  if (!s.building_type) return 'building_type';
  if (!s.province) return 'province';
  if (!s.land_area_m2) return 'land';
  if (!s.floors) return 'floors';
  if (!s.work_scope) return 'work_scope';
  return pendingExtra(session);
}

/** Hiểu câu trả lời (đã bỏ dấu) cho câu bổ sung đang chờ; null nếu không nhận ra. */
function answerExtra(t: string, raw: string, slots: Slots, step: ExtraStep): Slots | null {
  const s: Slots = { ...slots };
  if (UNKNOWN_RE.test(t) || ACCEPT_DEFAULT_RE.test(t.trim())) return { ...s, asked: [...(s.asked ?? []), step] };
  if (step === 'budget_vnd') {
    const ex = extractSlots(raw, slots);
    return ex.budget_vnd != null && ex.budget_vnd !== slots.budget_vnd ? ex : null;
  }
  if (step === 'building_area_m2') {
    if (/kin dat/.test(t)) return { ...s, building_area_m2: s.land_area_m2 };
    const m = t.match(/(\d+(?:[.,]\d+)?)/);
    return m ? { ...s, building_area_m2: num(m[1]!) } : null;
  }
  if (step === 'basement') {
    const lv = parseLevels(t);
    if (lv.basement === undefined && lv.has_mezzanine === undefined) return null;
    // Câu chỉ hỏi phần còn thiếu (hầm hoặc lửng) -> giữ phần khách đã nêu.
    return { ...s, basement: lv.basement ?? s.basement ?? false, has_mezzanine: lv.has_mezzanine ?? s.has_mezzanine ?? false };
  }
  if (step === 'basement_depth_m') {
    for (const [re, d] of DEPTH_OPTIONS) if (re.test(t)) return { ...s, basement_depth_m: d };
    const m = t.match(/(\d+(?:[.,]\d+)?)/);
    return m ? { ...s, basement_depth_m: num(m[1]!) } : null;
  }
  if (step === 'mezzanine_area_m2') {
    const m = t.match(/(\d+(?:[.,]\d+)?)/);
    return m ? { ...s, mezzanine_area_m2: num(m[1]!) } : null;
  }
  const roof = parseRoof(t);
  return roof ? { ...s, roof_type: roof } : null;
}

// ---------------------------------------------------------------- thẻ câu hỏi từng bước (question_step)

const LAND_CHIPS: [number, number][] = [
  [4, 10],
  [5, 10],
  [6, 10],
  [6, 12],
  [9, 12],
  [12, 10],
];
const APARTMENT_AREAS = [50, 80, 120, 200, 300];

const opts = (pairs: [string, string][]) => pairs.map(([label, value]) => ({ label, value }));
const same = (labels: string[]) => labels.map((l) => ({ label: l, value: l }));

const CLEAR_FIELDS: Record<QKey, (keyof Slots)[]> = {
  building_type: ['building_type'],
  province: ['province', 'district'],
  land: ['land_area_m2', 'land_width_m', 'land_length_m'],
  floors: ['floors'],
  work_scope: ['work_scope'],
  building_area_m2: ['building_area_m2'],
  basement: ['basement', 'has_mezzanine', 'basement_depth_m', 'mezzanine_area_m2'],
  basement_depth_m: ['basement_depth_m'],
  mezzanine_area_m2: ['mezzanine_area_m2'],
  roof_type: ['roof_type'],
  budget_vnd: ['budget_vnd'],
};

/** Khóa `asked` gắn với một câu (xóa khi hỏi lại câu đó). */
const askedKeys = (key: QKey): string[] =>
  key === 'basement' ? ['basement', 'basement_depth_m', 'mezzanine_area_m2'] : [key];

/** Xóa câu trả lời của một câu hỏi để hỏi lại (nút Trước / Sửa). */
function clearQuestion(s: Slots, key: QKey): Slots {
  const out: Slots = { ...s, confirmed: false };
  for (const f of CLEAR_FIELDS[key]) (out as Record<string, unknown>)[f] = null;
  const drop = askedKeys(key);
  out.asked = (s.asked ?? []).filter((k) => !drop.includes(k));
  return out;
}

type QuestionSpec = Omit<QuestionStepBlock, 'type' | 'step' | 'total' | 'back_value'>;

/** Nội dung câu hỏi (không gồm số bước) — cùng câu chữ / lựa chọn với backend dialog.question_for_key. */
function questionSpec(key: QKey, s: Slots): QuestionSpec {
  const base = { hint: null, chips: [], input_placeholder: FREE_TEXT, skippable: false, skip_value: null };
  switch (key) {
    case 'building_type':
      return {
        ...base,
        question: 'Loại công trình anh/chị dự định xây dựng?',
        options: opts([
          ['Nhà phố', 'Nhà phố'],
          ['Biệt thự', 'Biệt thự'],
          ['Căn hộ', 'Căn hộ'],
          ['Văn phòng', 'Văn phòng'],
          ['Khách sạn', 'Khách sạn'],
          ['Khác', 'Công trình khác'],
        ]),
        slots: ['building_type'],
      };
    case 'province':
      return {
        ...base,
        question: 'Khu vực xây dựng?',
        hint: 'Tỉnh/thành phố, kèm quận/huyện nếu có — đơn giá khác nhau theo khu vực.',
        options: same(['TP. Hồ Chí Minh', 'Hà Nội', 'Đà Nẵng']),
        input_placeholder: 'Nhập tỉnh/thành phố, quận/huyện',
        slots: ['province', 'district'],
      };
    case 'land':
      if (s.building_type === 'can_ho') {
        return {
          ...base,
          question: 'Diện tích căn hộ (m²)?',
          hint: 'Diện tích sử dụng của căn hộ (theo sổ hồng hoặc hợp đồng mua bán).',
          options: opts(APARTMENT_AREAS.map((a): [string, string] => [`${a} m²`, `Diện tích căn hộ ${a} m2`])),
          input_placeholder: 'Nhập diện tích (m²)',
          slots: ['land_area_m2'],
        };
      }
      return {
        ...base,
        question: 'Diện tích đất?',
        hint: 'Chọn kích thước đất ngang × dài (m), hoặc nhập tổng diện tích đất (m²).',
        options: [],
        chips: opts(LAND_CHIPS.map(([w, l]): [string, string] => [`${w} × ${l}`, `Đất ${w}×${l} m`])),
        input_placeholder: 'Nhập ngang × dài (m) hoặc tổng m²',
        slots: ['land_area_m2'],
      };
    case 'floors':
      if (s.building_type === 'can_ho') {
        return {
          ...base,
          question: 'Căn hộ có mấy tầng?',
          hint: 'Số tầng của chính căn hộ — căn duplex là 2 tầng.',
          options: opts([
            ['1 tầng', 'Căn hộ 1 tầng'],
            ['2 tầng (duplex)', 'Căn hộ 2 tầng (duplex)'],
          ]),
          input_placeholder: 'Nhập số tầng',
          slots: ['floors'],
        };
      }
      return {
        ...base,
        question: 'Số tầng dự kiến?',
        hint: 'Tầng trệt tính là 1 tầng; hầm, lửng (nếu có) anh/chị bổ sung ở bản xác nhận.',
        options: same(['1 trệt', '1 trệt 1 lầu', '1 trệt 2 lầu', '1 trệt 3 lầu', '1 trệt 4 lầu']),
        input_placeholder: 'Nhập số tầng (vd 1 trệt 3 lầu)',
        slots: ['floors'],
      };
    case 'work_scope':
      return {
        ...base,
        question: 'Phạm vi thi công cần khái toán?',
        hint: 'Trọn gói gồm phần thô và hoàn thiện.',
        options: same(['Xây thô', 'Hoàn thiện', 'Trọn gói', 'Nội thất']),
        slots: ['work_scope'],
      };
    case 'building_area_m2': {
      const land = viNum(s.land_area_m2 ?? 0);
      return {
        ...base,
        question: 'Diện tích xây dựng mỗi tầng (m²)?',
        hint: 'Phần sàn xây, không tính sân.',
        options: [
          ...(s.land_area_m2 ? opts([[`Xây kín đất (${land} m²)`, `Diện tích xây dựng mỗi tầng ${land} m2`]]) : []),
          { label: 'Chưa biết', value: 'Tôi chưa biết diện tích xây dựng' },
        ],
        input_placeholder: 'Nhập diện tích (m²)',
        slots: ['building_area_m2'],
      };
    }
    case 'basement':
      // Khách đã nêu một trong hai (vd "không hầm") -> chỉ hỏi phần còn thiếu.
      if (s.basement == null && s.has_mezzanine != null) {
        return {
          ...base,
          question: 'Nhà có tầng hầm không?',
          options: opts([
            ['Không có hầm', 'Không có tầng hầm'],
            ['Có tầng hầm', 'Có tầng hầm'],
          ]),
          slots: ['basement'],
        };
      }
      if (s.has_mezzanine == null && s.basement != null) {
        return {
          ...base,
          question: 'Nhà có tầng lửng không?',
          options: opts([
            ['Không có lửng', 'Không có tầng lửng'],
            ['Có tầng lửng', 'Có tầng lửng'],
          ]),
          slots: ['has_mezzanine'],
        };
      }
      return {
        ...base,
        question: 'Nhà có tầng hầm hoặc tầng lửng không?',
        options: opts([
          ['Không hầm, không lửng', 'Không hầm, không lửng'],
          ['Có tầng lửng', 'Có tầng lửng, không hầm'],
          ['Có tầng hầm', 'Có tầng hầm, không lửng'],
          ['Có cả hầm và lửng', 'Có tầng hầm và tầng lửng'],
        ]),
        slots: ['basement', 'has_mezzanine'],
      };
    case 'basement_depth_m':
      return {
        ...base,
        question: 'Độ sâu tầng hầm?',
        hint: 'Tính từ mặt đất xuống sàn hầm.',
        options: opts([
          ['Dưới 1,3 m', 'Hầm sâu 1,2 m'],
          ['1,3–1,7 m', 'Hầm sâu 1,5 m'],
          ['1,7–2 m', 'Hầm sâu 1,9 m'],
          ['Trên 2 m', 'Hầm sâu 2,5 m'],
          ['Chưa biết', 'Tôi chưa biết độ sâu hầm'],
        ]),
        input_placeholder: 'Nhập độ sâu (m)',
        slots: ['basement_depth_m'],
      };
    case 'mezzanine_area_m2': {
      const f = floorArea(s);
      const choices: [string, string][] = f
        ? [
            [`Khoảng 1/2 sàn (${viNum(Math.round(f * 5) / 10)} m²)`, `Tầng lửng ${viNum(Math.round(f * 5) / 10)} m2`],
            [`Bằng cả sàn (${viNum(f)} m²)`, `Tầng lửng ${viNum(f)} m2`],
          ]
        : [];
      return {
        ...base,
        question: 'Diện tích tầng lửng (m²)?',
        options: [...opts(choices), { label: 'Chưa biết', value: 'Tôi chưa biết diện tích tầng lửng' }],
        input_placeholder: 'Nhập diện tích (m²)',
        slots: ['mezzanine_area_m2'],
      };
    }
    case 'roof_type':
      return {
        ...base,
        question: 'Loại mái dự kiến?',
        options: [
          ...same(['Mái bằng bê tông', 'Mái tôn', 'Mái ngói kèo thép', 'Mái ngói đổ bê tông']),
          { label: 'Chưa biết', value: 'Tôi chưa biết loại mái' },
        ],
        slots: ['roof_type'],
      };
    case 'budget_vnd':
      return {
        ...base,
        skippable: true,
        skip_value: SKIP_VALUE,
        question: 'Ngân sách dự kiến?',
        hint: 'Có ngân sách giúp em tư vấn sát hơn và tự gợi ý phân khúc phù hợp ạ.',
        options: opts([
          ['<2 tỷ', 'Ngân sách dưới 2 tỷ'],
          ['2–4 tỷ', 'Ngân sách 2–4 tỷ'],
          ['4–6 tỷ', 'Ngân sách 4–6 tỷ'],
          ['6–10 tỷ', 'Ngân sách 6–10 tỷ'],
          ['>10 tỷ', 'Ngân sách trên 10 tỷ'],
        ]),
        input_placeholder: 'Nhập ngân sách (vd 3,5 tỷ)',
        slots: ['budget_vnd'],
      };
  }
}

/**
 * Block question_step cho câu hỏi `key`. Câu cốt lõi: bước = vị trí cố định trong 5 câu ("i/5").
 * Câu bổ sung: bước = vị trí trong `plan` (danh sách câu của lượt bổ sung, "i/N").
 */
export function questionStep(key: QKey, s: Slots, plan?: readonly QKey[]): QuestionStepBlock {
  const list: readonly QKey[] = (CORE_STEPS as QKey[]).includes(key) ? CORE_STEPS : (plan ?? [key]);
  const idx = Math.max(0, list.indexOf(key));
  return {
    type: 'question_step',
    ...questionSpec(key, s),
    step: idx + 1,
    total: list.length,
    back_value: idx > 0 ? BACK_VALUE : null,
  };
}

/** Tin nhắn "Tôi muốn sửa <mục>" (nút Sửa trên thẻ xác nhận) -> câu hỏi cần hỏi lại. */
const EDIT_TARGETS: [RegExp, QKey][] = [
  [/loai cong trinh|loai nha/, 'building_type'],
  [/khu vuc|dia diem/, 'province'],
  [/dien tich xay dung/, 'building_area_m2'],
  [/dien tich (tang )?lung/, 'mezzanine_area_m2'],
  [/do sau|sau ham/, 'basement_depth_m'],
  [/dien tich|kich thuoc/, 'land'],
  [/\bham\b|\blung\b/, 'basement'],
  [/\bmai\b/, 'roof_type'],
  [/so tang/, 'floors'],
  [/pham vi/, 'work_scope'],
  [/ngan sach/, 'budget_vnd'],
];

const ask = (text: string, key: QKey, session: MockSession): MockReply => ({
  text,
  blocks: [questionStep(key, session.slots, extraPlan(session))],
  stage: 'collect',
});

/** Hậu tố giá trị mặc định trên thẻ xác nhận (như backend dialog.TENTATIVE). */
const TENTATIVE = '(tạm tính)';

/** Như backend describe_floors: "1 trệt 3 lầu (4 tầng)"; có lửng "1 trệt 1 lửng 2 lầu (3 tầng + lửng)"; "1 trệt (1 tầng)". */
function describeFloors(s: Slots): string {
  if (!s.floors) return '—';
  if (s.building_type === 'can_ho') return s.floors === 2 ? '2 tầng (duplex)' : `${s.floors} tầng`;
  const parts = ['1 trệt', ...(s.has_mezzanine ? ['1 lửng'] : []), ...(s.floors > 1 ? [`${s.floors - 1} lầu`] : [])];
  return `${parts.join(' ')} (${s.floors} tầng${s.has_mezzanine ? ' + lửng' : ''})`;
}

function summaryReply(s: Slots): MockReply {
  const btype = BUILDING_LABEL[s.building_type ?? ''] ?? '—';
  const apartment = s.building_type === 'can_ho';
  const items: SummaryItem[] = [
    { label: 'Loại công trình', value: btype, edit_value: 'Tôi muốn sửa loại công trình' },
    { label: 'Khu vực', value: [s.district, s.province].filter(Boolean).join(', '), edit_value: 'Tôi muốn sửa khu vực' },
    {
      label: apartment ? 'Diện tích căn hộ' : 'Diện tích đất',
      value:
        !apartment && s.land_width_m && s.land_length_m
          ? `${viNum(s.land_width_m)} × ${viNum(s.land_length_m)} m (${viNum(s.land_area_m2 ?? 0)} m²)`
          : `${viNum(s.land_area_m2 ?? 0)} m²`,
      edit_value: 'Tôi muốn sửa diện tích đất',
    },
    { label: 'Số tầng', value: describeFloors(s), edit_value: 'Tôi muốn sửa số tầng' },
    { label: 'Phạm vi', value: SCOPE_LABEL[s.work_scope ?? ''] ?? '—', edit_value: 'Tôi muốn sửa phạm vi công việc' },
  ];
  // Như backend build_summary: công trình xây mới luôn có dòng chi tiết (giá trị khách nêu, hoặc mặc định "(tạm tính)")
  // kèm nút Sửa; căn hộ / phạm vi Nội thất không có dòng chi tiết.
  if (detailsApply(s)) {
    const land = s.land_area_m2 ? `${viNum(s.land_area_m2)} m²` : null;
    items.push(
      {
        label: 'Diện tích xây dựng mỗi tầng',
        value:
          s.building_area_m2 != null ? `${viNum(s.building_area_m2)} m²` : land ? `${land} ${TENTATIVE}` : `Xây kín đất ${TENTATIVE}`,
        edit_value: 'Tôi muốn sửa diện tích xây dựng',
      },
      {
        label: 'Tầng hầm',
        value:
          s.basement == null
            ? `Không ${TENTATIVE}`
            : !s.basement
              ? 'Không'
              : s.basement_depth_m
                ? `Có (sâu ${viNum(s.basement_depth_m)} m)`
                : 'Có',
        edit_value: 'Tôi muốn sửa thông tin hầm, lửng của nhà',
      },
      {
        label: 'Tầng lửng',
        value:
          s.has_mezzanine == null
            ? `Không ${TENTATIVE}`
            : !s.has_mezzanine
              ? 'Không'
              : s.mezzanine_area_m2
                ? `Có (${viNum(s.mezzanine_area_m2)} m²)`
                : 'Có',
        edit_value: s.has_mezzanine ? 'Tôi muốn sửa diện tích lửng của nhà' : 'Tôi muốn sửa thông tin hầm, lửng của nhà',
      },
      {
        label: 'Mái',
        value: (s.roof_type && ROOF_LABEL[s.roof_type]) || `Mái bê tông cốt thép ${TENTATIVE}`,
        edit_value: 'Tôi muốn sửa loại mái',
      },
    );
  }
  if (s.budget_vnd) items.push({ label: 'Ngân sách', value: formatVnd(s.budget_vnd), edit_value: 'Tôi muốn sửa ngân sách' });
  const assumptions: string[] = [];
  const defaults = defaultsLine(s);
  if (defaults) assumptions.push(defaults);
  if (!noFoundation(s) && s.floors) {
    assumptions.push(
      s.floors >= 4
        ? 'Móng tính theo thông lệ: móng cọc (nhà từ 4 tầng nổi trở lên), chưa gồm chi phí ép cọc.'
        : 'Móng tính theo thông lệ: móng băng (nhà đến 3 tầng nổi).',
    );
  }
  const more = missingDetails(s).length > 0;
  return {
    text: 'Em tóm tắt lại thông tin công trình. Anh/chị xác nhận để em tính khái toán nhé?',
    blocks: [
      {
        type: 'summary_confirm',
        items,
        assumptions,
        confirm: { label: 'Đúng rồi', value: CONFIRM_VALUE },
        edit: { label: 'Sửa lại', value: 'Tôi muốn sửa lại thông tin' },
        title: `Xác nhận thông tin — ${btype}`,
        confirm_label: CONFIRM_LABEL,
        confirm_value: CONFIRM_VALUE,
        // Chỉ khi còn chi tiết đang tạm tính theo mặc định.
        more_label: more ? MORE_LABEL : null,
        more_value: more ? MORE_VALUE : null,
      },
    ],
    stage: 'estimate',
  };
}

/** Câu nêu rõ mức đầu tư chỉ được ghi nhận, không tính vào khái toán (chưa có đơn giá theo mức đầu tư). */
export const investmentNote = (level: string) =>
  `Mức đầu tư ${level} chưa được tính vào khái toán vì Dezon chưa có đơn giá theo mức đầu tư.`;

/** Khái toán của phiên = cùng phạm vi / loại công trình / tỉnh khách đã chọn (mọi bước sau dùng lại). */
function sessionEstimate(session: MockSession): EstimateResult {
  const sl = session.slots;
  const e = estimateFixtureFor(sl.work_scope ?? 'xay_tho', { building_type: sl.building_type, province: sl.province, district: sl.district });
  // Phong cách / mức đầu tư: chỉ ghi nhận — KHÔNG đổi chi phí; mức đầu tư thì nêu rõ là chưa tính.
  return sl.investment_level ? { ...e, warnings: [...(e.warnings ?? []), investmentNote(sl.investment_level)] } : e;
}

/** Kết quả tìm nhà thầu theo đúng khái toán của phiên (phạm vi, loại công trình, tỉnh, diện tích quy đổi, ngân sách). */
function sessionMatch(session: MockSession): MatchResult {
  const estimate = sessionEstimate(session);
  const inp = estimate.input;
  return matchFixtureFor(inp.work_scope, estimate.converted_area_m2, {
    where: { building_type: inp.building_type, province: inp.province, district: inp.district },
    budgetVnd: session.slots.budget_vnd ?? null,
  });
}

function matchReply(session: MockSession): MockReply {
  const match = sessionMatch(session);
  const c = match.criteria;
  const scope = c.work_scope ?? 'xay_tho';
  const where = `${(BUILDING_LABEL[c.building_type] ?? 'công trình').toLowerCase()} tại ${c.province}`;
  if (match.results.length === 0) {
    // Như backend khi không có kết quả: không hiện danh sách, nêu ghi chú.
    return {
      text:
        `Dạ, hiện em chưa tìm thấy nhà thầu phù hợp với tiêu chí của anh/chị (${where}). ` +
        `${match.notes?.slice(1).join(' ') ?? ''} Anh/chị có thể nới rộng khu vực hoặc điều chỉnh tiêu chí để em tìm lại ạ.`.trim(),
      blocks: [qr(['Yêu cầu báo giá'], true)],
      stage: 'match',
    };
  }
  const names = match.results.map((m) => m.contractor.name);
  const top = match.results[0]!;
  const why = top.reasons
    .filter((r) => r.evidence !== 'province')
    .slice(0, 3)
    .map((r) => r.text.charAt(0).toLowerCase() + r.text.slice(1))
    .join(', ');
  let text = `Dạ, em tìm được ${names.length} nhà thầu ${where} phù hợp với tiêu chí của anh/chị: ${names.join(', ')}. Em đề xuất xem trước ${top.contractor.name} vì ${why}.`;
  if (SCOPES_WITHOUT_RATE.includes(scope)) {
    text +=
      ` Các nhà thầu trên Dezon chưa công bố đơn giá riêng cho phạm vi ${SCOPE_LABEL[scope]} nên em chưa ước được chi phí theo đơn giá nhà thầu; ` +
      'anh/chị nên yêu cầu báo giá để có con số cụ thể.';
  } else {
    if (top.estimated_cost.status !== 'unavailable') {
      const over = top.cautions.some((x) => x.text.startsWith('Chi phí ước theo đơn giá nhà thầu vượt ngân sách'));
      text += ` Chi phí ước theo đơn giá ${SCOPE_LABEL[scope]!.toLowerCase()} trên hồ sơ ${top.contractor.name} khoảng ${top.estimated_cost.display}${over ? ', cao hơn ngân sách anh/chị nêu' : ''}.`;
    }
    const noPrice = match.results.filter((m) => m.estimated_cost.status === 'unavailable').map((m) => m.contractor.name);
    if (noPrice.length) text += ` ${noPrice.join(' và ')} chưa có dữ liệu đơn giá, anh/chị nên yêu cầu báo giá để đối chiếu.`;
  }
  return {
    text,
    blocks: [{ type: 'contractor_list', data: match }, qr([`So sánh ${names.length} nhà thầu`, 'Yêu cầu báo giá'], false)],
    stage: 'match',
  };
}

function budgetReply(session: MockSession): MockReply {
  const estimate = sessionEstimate(session);
  const data = budgetFixtureFor(estimate);
  // Khách nêu ngân sách -> backend ghi slot budget_vnd (bước tìm nhà thầu dùng lại).
  session.slots = { ...session.slots, budget_vnd: data.target_budget_vnd };
  const budget = data.target_budget.display;
  if (data.options.length === 0) {
    const inp = estimate.input;
    const why = SCOPES_WITHOUT_RATE.includes(inp.work_scope)
      ? `các nhà thầu trên Dezon chưa công bố đơn giá riêng cho phạm vi ${SCOPE_LABEL[inp.work_scope]}`
      : `chưa có đơn giá tham chiếu cho ${BUILDING_LABEL[inp.building_type] ?? 'công trình'} tại ${inp.province} (phạm vi ${SCOPE_LABEL[inp.work_scope]})`;
    return {
      text:
        `Dạ, khái toán hiện tại của công trình là "${data.current_cost.display}" vì ${why}, ` +
        `nên em chưa tính được phương án điều chỉnh theo ngân sách ${budget}. Anh/chị có thể yêu cầu báo giá từ nhà thầu để có con số cụ thể.`,
      blocks: [{ type: 'budget_options', data }, qr(['Tìm nhà thầu phù hợp'], true)],
      stage: 'estimate',
    };
  }
  const cheapest = data.options.reduce((a, b) => ((b.cost_min ?? Infinity) < (a.cost_min ?? Infinity) ? b : a));
  const fits = data.options.filter((o) => o.fits_budget);
  const roughOnly = cheapest.changes.work_scope === 'xay_tho' && estimate.input.work_scope !== 'xay_tho' ? ', chỉ là chi phí giai đoạn xây thô' : '';
  return {
    text:
      `Dạ, với ngân sách ${budget}, khái toán hiện tại là ${data.current_cost.display}, em đã tính ${data.options.length} phương án điều chỉnh trong bảng bên dưới. ` +
      `Phương án chi phí thấp nhất là “${cheapest.label_vi}” (${cheapest.cost.display}${roughOnly}); ` +
      (fits.length
        ? `có ${fits.length} phương án nằm trong ngân sách ${budget} của anh/chị.`
        : `theo đơn giá hiện có, chưa có phương án nào đưa chi phí về trong ngân sách ${budget} của anh/chị.`),
    blocks: [{ type: 'budget_options', data }, qr(['Tìm nhà thầu phù hợp'], true)],
    stage: 'estimate',
  };
}

export function mockReply(text: string, session: MockSession): MockReply {
  const t = fold(text);
  const flow = collectControl(t, session);
  if (flow) return flow;
  if (/so sanh/.test(t)) {
    const results = sessionMatch(session).results;
    if (results.length === 0) return matchReply(session);
    return {
      text: `Dạ, em đã lập bảng so sánh ${results.length} nhà thầu theo cùng tiêu chí. Ô nào Dezon chưa có dữ liệu, em ghi rõ "Chưa có dữ liệu" để anh/chị tiện đối chiếu.`,
      blocks: [{ type: 'comparison_table', data: buildComparison(results.map((m) => m.contractor.id), results) }],
      stage: 'compare',
    };
  }
  if (/bao gia/.test(t)) {
    return {
      text: 'Dạ, anh/chị để lại thông tin liên hệ, em sẽ gửi yêu cầu báo giá tới các nhà thầu đã chọn kèm bản khái toán.',
      blocks: [
        {
          type: 'quote_request_form',
          // Nhà thầu của lần tìm kiếm theo đúng khu vực khái toán (không gợi ý nhà thầu ngoài kết quả).
          contractors: sessionMatch(session).results.map((m) => ({ id: m.contractor.id, name: m.contractor.name })),
          estimate_session_id: MOCK_ESTIMATE_SESSION_ID,
        },
      ],
      stage: 'quote',
    };
  }
  if (/nha thau/.test(t)) return matchReply(session);
  // Đang ở câu hỏi bổ sung (vd sửa "Ngân sách dự kiến?") -> "1,5 tỷ" là câu trả lời, không phải điều chỉnh ngân sách.
  const inExtra = coreComplete(session.slots) && pendingExtra(session) !== null;
  if (!inExtra && /1[.,]5 t[yi]|ngan sach .*(thap|it|chi)|dieu chinh/.test(t)) return budgetReply(session);
  if (/dung roi|tinh khai toan|xac nhan/.test(t)) {
    session.slots = { ...session.slots, confirmed: true };
    session.extra = null;
    const scope = session.slots.work_scope ?? 'xay_tho';
    const estimate = sessionEstimate(session);
    const area = `${viNum(estimate.converted_area_m2)} m²`;
    const badges = estimateSourceBadgesFor(estimate);
    if (!badges || !estimate.rate.source) {
      // Không có đơn giá: nói đúng lý do backend nêu (phạm vi chưa có giá / khu vực chưa có đơn giá tổng hợp).
      const why = SCOPES_WITHOUT_RATE.includes(scope)
        ? `Hiện các nhà thầu trên Dezon chưa công bố đơn giá riêng cho phạm vi ${SCOPE_LABEL[scope]} nên em chưa tính được chi phí.`
        : `Hiện chưa có đơn giá tham chiếu cho ${BUILDING_LABEL[estimate.input.building_type] ?? 'công trình'} tại ${estimate.input.province} (phạm vi ${SCOPE_LABEL[scope]}) nên em chưa tính được chi phí.`;
      return {
        text:
          `Dạ, em đã tính diện tích sàn quy đổi ${area}. ${why} Anh/chị có thể yêu cầu báo giá từ nhà thầu để có con số cụ thể.` +
          (session.slots.investment_level ? ` ${investmentNote(session.slots.investment_level)}` : ''),
        blocks: [{ type: 'estimate_table', data: estimate }, qr(['Tìm nhà thầu phù hợp'], true)],
        stage: 'estimate',
      };
    }
    return {
      text:
        `Dạ, em đã tính khái toán theo công thức của Dezon: diện tích sàn quy đổi ${area}, đơn giá tham chiếu theo “${estimate.rate.source.label}”, chi phí sơ bộ khoảng ${estimate.cost.display}. ` +
        'Chi phí chưa gồm ép cọc, giấy phép và thiết kế; anh/chị xem chi tiết hệ số và giả định trong bảng bên dưới.' +
        (session.slots.investment_level ? ` ${investmentNote(session.slots.investment_level)}` : ''),
      blocks: [
        { type: 'estimate_table', data: estimate },
        badges,
        qr(['Tìm nhà thầu phù hợp', 'Ngân sách của tôi chỉ 1,5 tỷ'], true),
      ],
      stage: 'estimate',
    };
  }
  if (/sua lai/.test(t)) {
    return {
      text: 'Dạ, anh/chị muốn sửa thông tin nào ạ?',
      blocks: [
        {
          type: 'quick_replies',
          options: [
            { label: 'Loại công trình', value: 'Tôi muốn sửa loại công trình' },
            { label: 'Khu vực', value: 'Tôi muốn sửa khu vực' },
            { label: 'Diện tích đất', value: 'Tôi muốn sửa diện tích đất' },
            { label: 'Số tầng', value: 'Tôi muốn sửa số tầng' },
            { label: 'Phạm vi thi công', value: 'Tôi muốn sửa phạm vi công việc' },
          ],
          allow_free_text: true,
        },
      ],
      stage: 'collect',
    };
  }

  const before = JSON.stringify(session.slots);
  const pending = coreComplete(session.slots) ? pendingExtra(session) : null;
  if (pending) session.slots = answerExtra(t, text, session.slots, pending) || extractSlots(text, session.slots);
  else session.slots = extractSlots(text, session.slots);
  const s = session.slots;
  const gotSomething = before !== JSON.stringify(s);
  const next = currentQuestion(session);
  // Văn bản không lặp lại câu hỏi (thẻ question_step mang câu hỏi) — chỉ một lời xác nhận ngắn.
  if (next) return ask(gotSomething ? 'Dạ, em đã ghi nhận.' : 'Dạ, em chưa rõ ý anh/chị, anh/chị chọn giúp em một lựa chọn bên dưới nhé.', next, session);
  session.extra = null; // hết lượt bổ sung -> về thẻ xác nhận
  if (gotSomething || !s.confirmed) return summaryReply(s);
  return {
    text: 'Dạ, ở chế độ giả lập em chỉ có kịch bản mẫu. Anh/chị có thể chọn một thao tác bên dưới.',
    blocks: [qr(['Tìm nhà thầu phù hợp', 'Ngân sách của tôi chỉ 1,5 tỷ', 'So sánh 3 nhà thầu'])],
  };
}

/** Bắt đầu một lượt hỏi bổ sung với các câu `keys` (xóa câu trả lời cũ / "chưa biết" của các câu đó). */
function startExtra(session: MockSession, keys: ExtraStep[], text: string, edit = false): MockReply {
  let slots: Slots = { ...session.slots, confirmed: false };
  const drop = keys.flatMap(askedKeys);
  slots = { ...slots, asked: (slots.asked ?? []).filter((k) => !drop.includes(k)) };
  session.slots = slots;
  session.extra = keys;
  session.extraEdit = edit;
  const next = currentQuestion(session);
  if (next) return ask(text, next, session);
  session.extra = null;
  return summaryReply(session.slots);
}

/** Nút điều khiển của thẻ câu hỏi / thẻ xác nhận: Trước, Bỏ qua, Sửa <mục>, Cung cấp thêm thông tin. */
function collectControl(t: string, session: MockSession): MockReply | null {
  const s = session.slots;
  if (t === fold(BACK_VALUE)) {
    const cur = currentQuestion(session);
    const extra = cur !== null && !(CORE_STEPS as QKey[]).includes(cur);
    // Lượt bổ sung: lùi trong nhóm câu bổ sung; còn lại: lùi trong 5 câu cốt lõi (từ thẻ xác nhận -> câu 5).
    const plan: QKey[] = extra ? extraPlan(session) : CORE_STEPS;
    const idx = cur ? plan.indexOf(cur) : plan.length;
    const prev = idx > 0 ? plan[idx - 1]! : null;
    if (!prev) return cur ? ask('Dạ, đây là câu hỏi đầu tiên ạ.', cur, session) : null;
    if (!extra) session.extra = null;
    session.slots = clearQuestion(s, prev);
    return ask('Dạ, mình quay lại câu trước nhé.', currentQuestion(session) ?? prev, session);
  }
  if (t === fold(SKIP_VALUE)) {
    const cur = currentQuestion(session);
    if (!cur) return summaryReply(s);
    // Câu cốt lõi không bỏ qua được (thẻ của câu đó không có nút "Bỏ qua").
    if ((CORE_STEPS as QKey[]).includes(cur)) {
      return ask('Dạ, câu này cần để em tính khái toán, anh/chị trả lời giúp em nhé.', cur, session);
    }
    session.slots = { ...s, asked: [...(s.asked ?? []), cur] };
    const next = currentQuestion(session);
    if (next) return ask('Dạ, em bỏ qua câu này.', next, session);
    session.extra = null;
    return summaryReply(session.slots);
  }
  if (t === fold(MORE_VALUE)) {
    return startExtra(session, missingDetails(s), 'Dạ, anh/chị bổ sung giúp em nhé.');
  }
  const edit = t.match(/^toi muon sua (.+)$/);
  if (edit && !/^lai\b/.test(edit[1]!)) {
    const target = EDIT_TARGETS.find(([re]) => re.test(edit[1]!))?.[1];
    if (!target) return null;
    const text = 'Dạ, anh/chị cập nhật lại giúp em nhé.';
    session.slots = clearQuestion(s, target);
    // Sửa một dòng chi tiết / ngân sách: lượt bổ sung chỉ gồm câu đó; sửa câu cốt lõi: hỏi lại câu đó (i/5).
    if ((CORE_STEPS as QKey[]).includes(target)) {
      session.extra = null;
      return ask(text, currentQuestion(session) ?? target, session);
    }
    return startExtra(session, [target as ExtraStep], text, true);
  }
  return null;
}

/** Cắt văn bản thành các mẩu nhỏ (giữ khoảng trắng) để mô phỏng stream. */
export function chunkText(text: string, wordsPerChunk = 2): string[] {
  const parts = text.split(/(\s+)/);
  const out: string[] = [];
  for (let i = 0; i < parts.length; i += wordsPerChunk * 2) out.push(parts.slice(i, i + wordsPerChunk * 2).join(''));
  return out.filter((p) => p.length > 0);
}

const sleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    if (signal?.aborted) return reject(new DOMException('Aborted', 'AbortError'));
    if (ms <= 0) return resolve();
    const t = setTimeout(resolve, ms);
    signal?.addEventListener('abort', () => {
      clearTimeout(t);
      reject(new DOMException('Aborted', 'AbortError'));
    });
  });

export function createMockClient(opts: MockOptions = {}): ApiClient {
  const delay = opts.delayMs ?? 30;
  const storage = opts.storage === undefined ? safeLocalStorage() : opts.storage;
  const sessions = new Map<string, MockSession>();
  let activeId: string | undefined; // phiên khách đang dùng — nút "So sánh" trên danh sách nhà thầu chấm theo phiên này

  const save = (s: MockSession) => {
    sessions.set(s.session_id, s);
    activeId = s.session_id;
    try {
      storage?.setItem(STORAGE_PREFIX + s.session_id, JSON.stringify(s));
    } catch {
      /* hết dung lượng / bị chặn: bỏ qua */
    }
  };
  const load = (id: string): MockSession | undefined => {
    const inMem = sessions.get(id);
    if (inMem) return inMem;
    try {
      const raw = storage?.getItem(STORAGE_PREFIX + id);
      if (raw) {
        const s = JSON.parse(raw) as MockSession;
        sessions.set(id, s);
        return s;
      }
    } catch {
      /* dữ liệu hỏng */
    }
    return undefined;
  };
  const msg = (s: MockSession, role: ChatMessageOut['role'], content: string, blocks: UIBlock[] = []): ChatMessageOut => ({
    id: s.nextId++,
    role,
    content,
    ui_blocks: blocks,
    created_at: new Date().toISOString(),
  });
  const state = (s: MockSession): SessionStateResponse => ({
    session_id: s.session_id,
    stage: s.stage,
    slots: s.slots,
    missing_required: ['building_type', 'province', 'land_area_m2', 'floors', 'work_scope'].filter(
      (k) => (s.slots as Record<string, unknown>)[k] == null,
    ),
    messages: s.messages,
  });

  return {
    isMock: true,
    async createSession() {
      const s: MockSession = { session_id: newId(), stage: 'collect', slots: {}, messages: [], nextId: 1 };
      s.messages.push(msg(s, 'assistant', GREETING, [questionStep('building_type', s.slots)]));
      save(s);
      const { missing_required: _m, ...rest } = state(s);
      void _m;
      return rest;
    },
    async getSession(id) {
      const s = load(id);
      if (!s) throw new ApiError(404, 'Không tìm thấy phiên');
      return state(s);
    },
    async sendMessage(sessionId, content, handlers: StreamHandlers, signal) {
      const s = load(sessionId);
      if (!s) throw new ApiError(404, 'Không tìm thấy phiên');
      s.messages.push(msg(s, 'user', content));
      const reply = mockReply(content, s);
      if (/dung roi|tinh khai toan/.test(fold(content))) s.slots = { ...s.slots, confirmed: true };
      await sleep(delay * 4, signal);
      for (const piece of chunkText(reply.text)) {
        await sleep(delay, signal);
        handlers.onText?.(piece);
      }
      for (const block of reply.blocks) {
        await sleep(delay, signal);
        handlers.onBlock?.(block);
      }
      if (reply.stage) s.stage = reply.stage;
      const out = msg(s, 'assistant', reply.text, reply.blocks);
      s.messages.push(out);
      save(s);
      const st = state(s);
      handlers.onDone?.({ message_id: out.id, stage: st.stage, slots: st.slots, missing_required: st.missing_required });
    },
    async getContractor(slug) {
      await sleep(delay * 3);
      const p = PROFILES[slug];
      if (!p) throw new ApiError(404, 'Không tìm thấy nhà thầu');
      return p;
    },
    async compare(ids) {
      await sleep(delay * 3);
      if (ids.length < 2 || ids.length > 5) throw new ApiError(422, 'Chọn từ 2 đến 5 nhà thầu');
      const s = activeId ? load(activeId) : undefined;
      // Chấm theo khái toán của phiên (cùng phạm vi/loại/khu vực với danh sách vừa hiển thị), không dùng mẫu xây thô.
      return s?.slots.confirmed ? buildComparison(ids, sessionMatch(s).results) : buildComparison(ids);
    },
    async createQuoteRequest(body) {
      await sleep(delay * 5);
      const d = new Date();
      const code = `DZ-${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}-${String(Math.floor(Math.random() * 10000)).padStart(4, '0')}`;
      return { ids: body.contractor_ids.map(() => newId()), code };
    },
    async recordClick() {
      return { ok: true };
    },
  };
}

function safeLocalStorage(): Storage | null {
  try {
    return typeof window !== 'undefined' ? window.localStorage : null;
  } catch {
    return null;
  }
}
