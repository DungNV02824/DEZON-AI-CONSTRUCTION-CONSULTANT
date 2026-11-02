/**
 * Client giả lập (chế độ mock): trả dữ liệu fixture đúng schema và mô phỏng luồng hội thoại mục 5.4,
 * kể cả stream văn bản dần dần. Dùng để kiểm tra giao diện khi chưa có backend.
 */
import type { ApiClient, StreamHandlers } from './client';
import {
  BUDGET_FIXTURE,
  CONTRACTORS,
  ESTIMATE_FIXTURE,
  ESTIMATE_SOURCE_BADGES,
  MATCH_FIXTURE,
  MOCK_ESTIMATE_SESSION_ID,
  PROFILES,
  buildComparison,
} from './fixtures';
import type {
  ChatMessageOut,
  QuickRepliesBlock,
  SessionStateResponse,
  Slots,
  UIBlock,
} from './types';
import { ApiError } from './client';
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

/** Bỏ dấu tiếng Việt + chữ thường để so khớp từ khóa. */
export function fold(s: string): string {
  return s
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/đ/g, 'd')
    .replace(/Đ/g, 'D')
    .toLowerCase();
}

const GREETING =
  'Xin chào anh/chị! Em có thể hỗ trợ khái toán chi phí xây dựng và tìm nhà thầu phù hợp trên Dezon. Anh/chị đang có nhu cầu xây dựng loại công trình nào ạ?';

function extractSlots(text: string, slots: Slots): Slots {
  const t = fold(text);
  const s: Slots = { ...slots };
  const bt: [RegExp, Slots['building_type']][] = [
    [/nha pho/, 'nha_pho'],
    [/biet thu/, 'biet_thu'],
    [/can ho/, 'can_ho'],
    [/van phong/, 'van_phong'],
    [/khach san/, 'khach_san'],
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
  if (/xay tho|phan tho/.test(t)) s.work_scope = 'xay_tho';
  else if (/hoan thien/.test(t)) s.work_scope = 'hoan_thien';
  else if (/tron goi/.test(t)) s.work_scope = 'tron_goi';
  else if (/noi that/.test(t)) s.work_scope = 'noi_that';
  const budget = t.match(/(\d+(?:[.,]\d+)?)\s*(ty|ti)\b/);
  if (budget) s.budget_vnd = Math.round(Number(budget[1]!.replace(',', '.')) * 1e9);
  return s;
}

function summaryReply(s: Slots): MockReply {
  const items = [
    { label: 'Loại công trình', value: BUILDING_LABEL[s.building_type ?? ''] ?? '—' },
    { label: 'Khu vực', value: [s.district, s.province].filter(Boolean).join(', ') },
    {
      label: 'Diện tích đất',
      value:
        s.land_width_m && s.land_length_m
          ? `${s.land_width_m} × ${s.land_length_m} m (${s.land_area_m2} m²)`
          : `${s.land_area_m2} m²`,
    },
    { label: 'Số tầng', value: s.floors ? `${s.floors} tầng (1 trệt ${s.floors - 1} lầu)` : '—' },
    { label: 'Phạm vi', value: SCOPE_LABEL[s.work_scope ?? ''] ?? '—' },
    { label: 'Mức hoàn thiện', value: 'Trung bình (mặc định)' },
  ];
  return {
    text: 'Em tóm tắt lại thông tin công trình. Anh/chị xác nhận để em tính khái toán nhé?',
    blocks: [
      {
        type: 'summary_confirm',
        items,
        assumptions: [
          'Diện tích xây dựng mỗi tầng bằng diện tích đất.',
          'Móng và mái theo mặc định của Dezon (sẽ ghi rõ trong bảng khái toán).',
        ],
        confirm: { label: 'Đúng rồi', value: 'Đúng rồi, tính khái toán giúp tôi' },
        edit: { label: 'Sửa lại', value: 'Tôi muốn sửa lại thông tin' },
      },
    ],
    stage: 'estimate',
  };
}

export function mockReply(text: string, session: MockSession): MockReply {
  const t = fold(text);
  if (/so sanh/.test(t)) {
    return {
      text: 'Dạ, em đã lập bảng so sánh 3 nhà thầu theo cùng tiêu chí. Ô nào Dezon chưa có dữ liệu, em ghi rõ "Chưa có dữ liệu" để anh/chị tiện đối chiếu.',
      blocks: [{ type: 'comparison_table', data: buildComparison([1, 2, 3]) }],
      stage: 'compare',
    };
  }
  if (/bao gia/.test(t)) {
    return {
      text: 'Dạ, anh/chị để lại thông tin liên hệ, em sẽ gửi yêu cầu báo giá tới các nhà thầu đã chọn kèm bản khái toán.',
      blocks: [
        {
          type: 'quote_request_form',
          contractors: CONTRACTORS.map((c) => ({ id: c.id, name: c.name })),
          estimate_session_id: MOCK_ESTIMATE_SESSION_ID,
        },
      ],
      stage: 'quote',
    };
  }
  if (/nha thau/.test(t)) {
    return {
      text:
        'Dạ, em tìm được 3 nhà thầu phù hợp với tiêu chí của anh/chị tại TP. Hồ Chí Minh. Em đề xuất Kiến Phong vì đơn vị có kinh nghiệm thi công nhà phố, biệt thự, có 7 công trình ghi nhận trên Dezon và có thông tin đơn giá xây thô 4,3–4,7 triệu/m². ' +
        'Smartcons Tech và Huy Hoàng có 8 và 6 công trình ghi nhận trên Dezon nhưng chưa có dữ liệu kinh nghiệm và đơn giá, anh/chị nên yêu cầu báo giá để đối chiếu.',
      blocks: [{ type: 'contractor_list', data: MATCH_FIXTURE }, qr(['So sánh 3 nhà thầu', 'Yêu cầu báo giá'], false)],
      stage: 'match',
    };
  }
  if (/1[.,]5 t[yi]|ngan sach .*(thap|it|chi)|dieu chinh/.test(t)) {
    return {
      text: 'Dạ, với ngân sách 1,5 tỷ, em đã thử các phương án điều chỉnh. Chi phí mỗi phương án do công cụ khái toán tính lại theo cùng đơn giá tham chiếu:',
      blocks: [{ type: 'budget_options', data: BUDGET_FIXTURE }, qr(['Tìm nhà thầu phù hợp'], true)],
      stage: 'estimate',
    };
  }
  if (/dung roi|tinh khai toan|xac nhan/.test(t)) {
    return {
      text:
        'Dạ, em đã tính khái toán theo công thức của Dezon: diện tích sàn quy đổi 490 m², chi phí sơ bộ khoảng 2,058–2,352 tỷ. ' +
        'Chi phí chưa gồm ép cọc, giấy phép và thiết kế; anh/chị xem chi tiết hệ số và giả định trong bảng bên dưới.',
      blocks: [
        { type: 'estimate_table', data: ESTIMATE_FIXTURE },
        ESTIMATE_SOURCE_BADGES,
        qr(['Tìm nhà thầu phù hợp', 'Ngân sách của tôi chỉ 1,5 tỷ'], true),
      ],
      stage: 'estimate',
    };
  }
  if (/sua lai/.test(t)) {
    return {
      text: 'Dạ, anh/chị muốn sửa thông tin nào ạ?',
      blocks: [qr(['Diện tích đất', 'Số tầng', 'Phạm vi thi công', 'Mức hoàn thiện'], true)],
      stage: 'collect',
    };
  }

  const before = JSON.stringify(session.slots);
  session.slots = extractSlots(text, session.slots);
  const s = session.slots;
  const gotSomething = before !== JSON.stringify(s);
  const prefix = gotSomething ? 'Dạ, ' : 'Dạ, em chưa rõ ý anh/chị. ';
  if (!s.building_type) {
    return { text: `${prefix}anh/chị đang có nhu cầu xây dựng loại công trình nào ạ?`, blocks: [qr(['Nhà phố', 'Biệt thự', 'Căn hộ', 'Văn phòng', 'Khác'])] };
  }
  if (!s.province) {
    return { text: `${prefix}anh/chị dự định xây nhà ở khu vực nào?`, blocks: [qr(['TP. Hồ Chí Minh', 'Hà Nội', 'Đà Nẵng'])] };
  }
  if (!s.land_area_m2 || !s.floors) {
    const ask = !s.land_area_m2 && !s.floors ? 'diện tích đất và số tầng dự kiến' : !s.land_area_m2 ? 'diện tích đất' : 'số tầng dự kiến';
    return { text: `${prefix}anh/chị cho em xin thêm ${ask} nhé.`, blocks: [qr(['5 × 20 m', '1 trệt 2 lầu', '1 trệt 3 lầu'])] };
  }
  if (!s.work_scope) {
    return { text: `${prefix}anh/chị muốn khái toán cho phạm vi thi công nào?`, blocks: [qr(['Xây thô', 'Hoàn thiện', 'Trọn gói', 'Nội thất'])] };
  }
  if (gotSomething || !s.confirmed) return summaryReply(s);
  return {
    text: 'Dạ, ở chế độ giả lập em chỉ có kịch bản mẫu. Anh/chị có thể chọn một thao tác bên dưới.',
    blocks: [qr(['Tìm nhà thầu phù hợp', 'Ngân sách của tôi chỉ 1,5 tỷ', 'So sánh 3 nhà thầu'])],
  };
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

  const save = (s: MockSession) => {
    sessions.set(s.session_id, s);
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
      s.messages.push(msg(s, 'assistant', GREETING, [qr(['Nhà phố', 'Biệt thự', 'Căn hộ', 'Văn phòng', 'Khác'])]));
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
      return buildComparison(ids);
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
