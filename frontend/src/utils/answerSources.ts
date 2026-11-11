/**
 * V2 — "Nguồn dữ liệu" của một câu trả lời: gom MỌI EvidenceValue có trong các UI block của tin nhắn
 * (bảng khái toán, phương án ngân sách, thẻ nhà thầu, bảng so sánh/tổng quan, hồ sơ) và các giá trị không có
 * block (`source_badges.items` từ backend), rồi nhóm theo nguồn.
 *
 * Hàm thuần, không định dạng lại số: giá trị luôn là nguyên văn `EvidenceValue.display` của backend.
 * Khóa nhóm = (label, url, updated_at, status) — cùng cách khử trùng lặp nguồn như backend (blocks.source_badges),
 * thêm status vì mỗi nhóm hiển thị một trạng thái.
 */
import type { EvidenceValue, SourceRef, UIBlock, VerificationStatus } from '../api/types';
import { FINISH_PRICE_LABEL_VI, normalizeStatus, SCOPES_WITHOUT_CONTRACTOR_PRICE } from './labels';

export interface SourceEntry {
  /** Ngữ cảnh (tên nhà thầu, "Khái toán", ...); null = không cần. */
  context: string | null;
  label: string;
  /** Nguyên văn EvidenceValue.display. */
  display: string;
  status: VerificationStatus;
  isStale: boolean;
}

export interface SourceGroup {
  key: string;
  /** null = giá trị có số liệu nhưng chưa ghi nhận nguồn chi tiết. */
  source: SourceRef | null;
  status: VerificationStatus;
  /** Có ít nhất một giá trị trong nhóm bị đánh dấu dữ liệu cũ. */
  isStale: boolean;
  entries: SourceEntry[];
}

export interface AnswerSources {
  groups: SourceGroup[];
  /** Giá trị "Chưa có dữ liệu" (liệt kê ở mục thu gọn cuối hộp thoại). */
  missing: SourceEntry[];
  /** Tổng số giá trị (có nguồn + chưa có dữ liệu). */
  total: number;
}

/** Tên tiếng Việt của khóa evidence trong thẻ / hồ sơ nhà thầu (không in khóa thô ra giao diện). */
export const CONTRACTOR_FIELD_LABEL_VI: Record<string, string> = {
  province: 'Khu vực',
  building_types: 'Loại công trình',
  years_experience: 'Kinh nghiệm',
  founded_year: 'Năm thành lập',
  staff: 'Nhân sự',
  staff_workers: 'Nhân sự thợ',
  staff_office: 'Nhân sự văn phòng',
  rough_price: 'Đơn giá xây thô',
  finish_price: FINISH_PRICE_LABEL_VI,
  typical_project_value: 'Quy mô dự án',
  dezon_project_count: 'Công trình ghi nhận trên Dezon',
  warranty: 'Bảo hành',
  profile_completeness: 'Mức đầy đủ hồ sơ',
};

const fieldLabel = (k: string) => CONTRACTOR_FIELD_LABEL_VI[k] ?? 'Thông tin khác';

function isEvidence(v: unknown): v is EvidenceValue {
  return !!v && typeof v === 'object' && typeof (v as EvidenceValue).display === 'string' && 'status' in (v as object);
}

function isMissing(ev: EvidenceValue): boolean {
  return normalizeStatus(ev.status) === 'unavailable' || ev.value === null || ev.value === undefined;
}

/** Khóa nhóm nguồn — cùng (label, url, ngày) như backend + trạng thái. */
function groupKey(src: SourceRef | null | undefined, status: VerificationStatus): string {
  return JSON.stringify([src?.label ?? null, src?.url ?? null, src?.updated_at ?? null, status]);
}

class Collector {
  private groups = new Map<string, SourceGroup>();
  private missing: SourceEntry[] = [];
  private seen = new Set<string>();

  add(context: string | null, label: string, ev: EvidenceValue | null | undefined): void {
    if (!isEvidence(ev)) return;
    const missing = isMissing(ev);
    const status: VerificationStatus = missing ? 'unavailable' : normalizeStatus(ev.status);
    const src = missing ? null : ev.source?.label ? ev.source : null;
    const gk = missing ? 'missing' : groupKey(src, status);
    // Cùng một giá trị xuất hiện ở nhiều block (vd thẻ nhà thầu + bảng so sánh) -> chỉ liệt kê một lần.
    const ek = JSON.stringify([gk, context, label, ev.display]);
    if (this.seen.has(ek)) return;
    this.seen.add(ek);
    const entry: SourceEntry = { context, label, display: ev.display, status, isStale: !missing && !!ev.is_stale };
    if (missing) {
      this.missing.push(entry);
      return;
    }
    let g = this.groups.get(gk);
    if (!g) {
      g = { key: gk, source: src, status, isStale: false, entries: [] };
      this.groups.set(gk, g);
    }
    g.entries.push(entry);
    if (entry.isStale) g.isStale = true;
  }

  result(): AnswerSources {
    const groups = [...this.groups.values()];
    const n = groups.reduce((s, g) => s + g.entries.length, 0);
    return { groups, missing: this.missing, total: n + this.missing.length };
  }
}

function addEvidenceMap(c: Collector, context: string | null, map: Record<string, EvidenceValue> | null | undefined, skip: string[] = []) {
  for (const [k, ev] of Object.entries(map ?? {})) {
    if (!skip.includes(k)) c.add(context, fieldLabel(k), ev);
  }
}

function collectBlock(c: Collector, b: UIBlock): void {
  switch (b.type) {
    case 'estimate_table': {
      c.add('Khái toán', 'Đơn giá tham chiếu', b.data.rate);
      c.add('Khái toán', 'Chi phí sơ bộ', b.data.cost);
      return;
    }
    case 'budget_options': {
      const ctx = 'Phương án ngân sách';
      c.add(ctx, 'Ngân sách của anh/chị', b.data.target_budget);
      c.add(ctx, 'Chi phí khái toán hiện tại', b.data.current_cost);
      for (const o of b.data.options ?? []) c.add(ctx, o.label_vi, o.cost);
      return;
    }
    case 'contractor_list': {
      const scope = b.data.criteria?.work_scope;
      // Như thẻ nhà thầu: phạm vi không có đơn giá riêng -> không dùng chi phí ước tính theo đơn giá nhà thầu.
      const noScopePrice = !!scope && SCOPES_WITHOUT_CONTRACTOR_PRICE.includes(scope);
      for (const r of b.data.results ?? []) {
        const name = r.contractor.name;
        addEvidenceMap(c, name, r.contractor.evidence);
        if (!noScopePrice) c.add(name, 'Chi phí ước tính theo đơn giá nhà thầu', r.estimated_cost);
      }
      return;
    }
    case 'comparison_table': {
      const names = (b.data.contractors ?? []).map((x) => x.name);
      for (const row of b.data.rows ?? []) {
        (row.cells ?? []).forEach((cell, i) => c.add(names[i] ?? null, row.label_vi, cell));
      }
      return;
    }
    case 'contractor_detail': {
      const p = b.data;
      const name = p.card.name;
      c.add(name, 'Giới thiệu', p.description);
      addEvidenceMap(c, name, p.info);
      addEvidenceMap(c, name, p.card.evidence, Object.keys(p.info ?? {}));
      return;
    }
    case 'source_badges': {
      for (const it of b.items ?? []) c.add(it.group ?? null, it.label, it.evidence);
      return;
    }
    default:
      return;
  }
}

/** Gom nguồn của một tin nhắn trợ lý (chỉ đọc block, không gọi mạng). */
export function collectAnswerSources(blocks: readonly UIBlock[] | null | undefined): AnswerSources {
  const c = new Collector();
  for (const b of blocks ?? []) {
    try {
      collectBlock(c, b);
    } catch {
      /* block thiếu trường (payload cũ) -> bỏ qua block đó, không làm hỏng tin nhắn */
    }
  }
  return c.result();
}
