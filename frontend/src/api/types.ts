/**
 * Kiểu dữ liệu khớp 1-1 với Pydantic ở backend:
 *   backend/app/evidence.py, backend/app/schemas/{common,estimate,matching,slots,ui_blocks}.py
 * và hợp đồng docs/API_CONTRACT.md. Khi backend đổi schema, cập nhật file này.
 *
 * Quy ước: UUID và ngày (date) đi qua JSON dưới dạng chuỗi ISO.
 */

export type UUID = string;
/** Ngày ISO "YYYY-MM-DD" (Pydantic `date`). */
export type ISODate = string;
/** Thời điểm ISO 8601 (Pydantic `datetime`). */
export type ISODateTime = string;

// ---------------------------------------------------------------- evidence.py

export type VerificationStatus = 'verified' | 'on_dezon' | 'reference' | 'unavailable';

export type SourceType =
  | 'dezon_api'
  | 'contractor_verified'
  | 'crawl_public'
  | 'dezon_editorial'
  | 'csv_import';

export interface SourceRef {
  label: string;
  url?: string | null;
  updated_at?: ISODate | null;
}

export interface EvidenceValue {
  value?: unknown;
  /** Chuỗi hiển thị đã định dạng ở backend — frontend LUÔN hiển thị nguyên văn. */
  display: string;
  status: VerificationStatus;
  source?: SourceRef | null;
  is_stale?: boolean;
}

// ---------------------------------------------------------------- schemas/common.py

export type BuildingType = 'nha_pho' | 'biet_thu' | 'can_ho' | 'van_phong' | 'khach_san' | 'khac';
export type WorkScope = 'xay_tho' | 'hoan_thien' | 'tron_goi' | 'noi_that';
export type QualityTier = 'tiet_kiem' | 'trung_binh' | 'kha' | 'cao_cap';

// ---------------------------------------------------------------- schemas/estimate.py

export type FoundationType = 'foundation_single' | 'foundation_strip' | 'foundation_pile';
export type RoofType =
  | 'roof_metal'
  | 'roof_concrete'
  | 'roof_tile_on_truss'
  | 'roof_tile_on_concrete'
  | 'none';
export type RooftopType = 'none' | 'covered' | 'open';

export interface EstimateInput {
  building_type: BuildingType;
  province: string;
  district?: string | null;
  land_width_m?: number | null;
  land_length_m?: number | null;
  land_area_m2?: number | null;
  building_area_m2?: number | null;
  floors: number;
  has_mezzanine?: boolean;
  mezzanine_area_m2?: number | null;
  basement?: boolean;
  basement_depth_m?: number | null;
  basement_area_m2?: number | null;
  roof_type?: RoofType | null;
  roof_area_m2?: number | null;
  rooftop_terrace?: RooftopType | null;
  rooftop_area_m2?: number | null;
  foundation_type?: FoundationType | null;
  yard_area_m2?: number | null;
  work_scope: WorkScope;
  quality_tier?: QualityTier;
}

export interface RuleData {
  code: string;
  group_name: string;
  label_vi: string;
  coefficient: number;
  base: string;
  approved: boolean;
  version: number;
  source?: SourceRef | null;
}

export interface RateData {
  id?: number | null;
  building_type: BuildingType;
  province: string;
  work_scope: WorkScope;
  quality_tier: QualityTier;
  price_min: number;
  price_max: number;
  effective_from: ISODate;
  is_fallback?: boolean;
  status?: VerificationStatus;
  source?: SourceRef | null;
}

export interface EstimateItem {
  rule_code: string;
  label_vi: string;
  group_name: string;
  /** A_i (m²) */
  area_m2: number;
  /** k_i */
  coefficient: number;
  /** A_i × k_i */
  converted_m2: number;
}

export interface EstimateResult {
  estimate_session_id?: UUID | null;
  input: EstimateInput;
  items: EstimateItem[];
  converted_area_m2: number;
  cost_min: number | null;
  cost_max: number | null;
  cost: EvidenceValue;
  rate: EvidenceValue;
  rate_id?: number | null;
  rules_version: number;
  rules_status: VerificationStatus;
  assumptions: string[];
  exclusions: string[];
  cost_drivers: string[];
  disclaimer: string;
  warnings?: string[];
}

export interface BudgetOption {
  code: string;
  label_vi: string;
  description: string;
  changes: Record<string, unknown>;
  converted_area_m2: number;
  cost_min: number | null;
  cost_max: number | null;
  cost: EvidenceValue;
  saving_min?: number | null;
  fits_budget: boolean;
}

export interface BudgetAdjustmentResult {
  estimate_session_id?: UUID | null;
  target_budget_vnd: number;
  target_budget: EvidenceValue;
  current_cost: EvidenceValue;
  options: BudgetOption[];
}

// ---------------------------------------------------------------- schemas/matching.py

export const FIT_HIGH = 'Phù hợp cao';
export const FIT_OK = 'Phù hợp';
export const FIT_REVIEW = 'Cần đánh giá thêm';

export interface MatchCriteria {
  province: string;
  district?: string | null;
  building_type: BuildingType;
  work_scope?: WorkScope | null;
  budget_vnd?: number | null;
  floors?: number | null;
  converted_area_m2?: number | null;
  estimate_session_id?: UUID | null;
  limit?: number;
}

export type CriterionKey =
  | 'location'
  | 'building_type'
  | 'budget_fit'
  | 'experience'
  | 'similar_projects'
  | 'profile_quality';

export interface CriterionScore {
  key: CriterionKey | string;
  label_vi: string;
  score: number;
  weight: number;
  weighted: number;
  note: string;
}

export interface Reason {
  text: string;
  /** Khóa trỏ tới ContractorCard.evidence, vd "rough_price". */
  evidence: string;
}

export interface Caution {
  text: string;
  evidence?: string | null;
}

export interface ImageRef {
  url: string;
  caption?: string | null;
  project_slug?: string | null;
}

/** Khóa chuẩn trong ContractorCard.evidence. */
export type ContractorEvidenceKey =
  | 'province'
  | 'building_types'
  | 'years_experience'
  | 'founded_year'
  | 'staff'
  | 'rough_price'
  | 'finish_price'
  | 'typical_project_value'
  | 'dezon_project_count'
  | 'warranty'
  | 'profile_completeness';

export interface ContractorCard {
  id: number;
  slug: string;
  name: string;
  badge?: string | null;
  avatar_url?: string | null;
  /** Link hồ sơ gốc trên dezon.vn */
  profile_url: string;
  profile_source?: SourceRef | null;
  cover_images?: ImageRef[];
  evidence: Partial<Record<ContractorEvidenceKey, EvidenceValue>> & Record<string, EvidenceValue>;
}

export interface MatchedContractor {
  rank: number;
  score: number;
  fit_label: string;
  breakdown: CriterionScore[];
  reasons: Reason[];
  cautions: Caution[];
  estimated_cost: EvidenceValue;
  relaxed_location?: boolean;
  contractor: ContractorCard;
}

export interface MatchResult {
  recommendation_log_id?: number | null;
  weights_version: number;
  criteria: MatchCriteria;
  total_candidates: number;
  relaxed_to_neighbors?: boolean;
  results: MatchedContractor[];
  notes?: string[];
}

export interface ProjectSummary {
  id: number;
  slug: string;
  name: string;
  building_type?: BuildingType | null;
  province?: string | null;
  district?: string | null;
  floors?: number | null;
  status?: string | null;
  start_date?: ISODate | null;
  end_date?: ISODate | null;
  project_url: string;
  role?: string | null;
  contractor_names?: string[];
  images?: ImageRef[];
  source?: SourceRef | null;
}

/** Khóa chuẩn trong ContractorProfile.info (mục 8.3). */
export type ContractorInfoKey =
  | 'founded_year'
  | 'years_experience'
  | 'staff_workers'
  | 'staff_office'
  | 'rough_price'
  | 'finish_price'
  | 'typical_project_value'
  | 'dezon_project_count'
  | 'warranty';

export interface ContractorProfile {
  card: ContractorCard;
  description: EvidenceValue;
  info: Partial<Record<ContractorInfoKey, EvidenceValue>> & Record<string, EvidenceValue>;
  projects: ProjectSummary[];
  images: ImageRef[];
  total_images: number;
  chips?: string[];
}

export interface ComparedContractor {
  id: number;
  slug: string;
  name: string;
  profile_url: string;
  avatar_url?: string | null;
}

export type ComparisonRowKey =
  | 'area'
  | 'dezon_project_count'
  | 'experience'
  | 'rough_price'
  | 'finish_price'
  | 'projects'
  | 'warranty'
  | 'fit';

export interface ComparisonRow {
  key: ComparisonRowKey | string;
  label_vi: string;
  /** Cùng thứ tự với ComparisonMatrix.contractors; ô thiếu = unavailable. */
  cells: EvidenceValue[];
}

export interface ComparisonMatrix {
  contractors: ComparedContractor[];
  rows: ComparisonRow[];
}

/** Một nhà thầu trong câu trả lời câu hỏi tự do (mục 8.4). */
export interface OverviewContractor {
  card: ContractorCard;
  has_projects_of_type: boolean;
  projects_of_type?: ProjectSummary[];
  profile_verified?: boolean;
}

/** Tổng quan nhà thầu theo khu vực + loại công trình (mục 8.4). Mọi số là EvidenceValue. */
export interface ContractorOverview {
  province: string;
  building_type: BuildingType;
  contractor_count: EvidenceValue;
  contractors_with_projects: EvidenceValue;
  total_dezon_projects: EvidenceValue;
  rough_price_range: EvidenceValue;
  finish_price_range: EvidenceValue;
  with_warranty_info: EvidenceValue;
  verified_profiles: EvidenceValue;
  contractors: OverviewContractor[];
  notes?: string[];
  projects_of_type_count?: EvidenceValue;
}

// ---------------------------------------------------------------- schemas/slots.py

export interface Slots {
  building_type?: BuildingType | null;
  province?: string | null;
  district?: string | null;
  land_width_m?: number | null;
  land_length_m?: number | null;
  land_area_m2?: number | null;
  building_area_m2?: number | null;
  floors?: number | null;
  has_mezzanine?: boolean | null;
  basement?: boolean | null;
  basement_depth_m?: number | null;
  roof_type?: RoofType | null;
  rooftop_terrace?: RooftopType | null;
  foundation_type?: FoundationType | null;
  work_scope?: WorkScope | null;
  quality_tier?: QualityTier | null;
  budget_vnd?: number | null;
  start_time?: string | null;
  confirmed?: boolean;
  /** Các slot đã hỏi khách (kể cả khi khách chưa trả lời). */
  asked?: string[];
}

// ---------------------------------------------------------------- schemas/ui_blocks.py

export interface QuickReply {
  label: string;
  value: string;
}

export interface QuickRepliesBlock {
  type: 'quick_replies';
  options: QuickReply[];
  allow_free_text?: boolean;
}

export interface SummaryItem {
  label: string;
  value: string;
}

export interface SummaryConfirmBlock {
  type: 'summary_confirm';
  items: SummaryItem[];
  assumptions?: string[];
  confirm?: QuickReply;
  edit?: QuickReply;
}

export interface EstimateTableBlock {
  type: 'estimate_table';
  data: EstimateResult;
}

export interface BudgetOptionsBlock {
  type: 'budget_options';
  data: BudgetAdjustmentResult;
}

export interface ContractorListBlock {
  type: 'contractor_list';
  data: MatchResult;
}

export interface ComparisonTableBlock {
  type: 'comparison_table';
  data: ComparisonMatrix;
}

export interface ContractorDetailBlock {
  type: 'contractor_detail';
  data: ContractorProfile;
  estimate_session_id?: UUID | null;
}

export interface SourceBadge {
  label: string;
  status: VerificationStatus;
  source: SourceRef;
}

export interface SourceBadgesBlock {
  type: 'source_badges';
  badges: SourceBadge[];
}

export interface QuoteFormContractor {
  id: number;
  name: string;
}

export interface QuoteRequestFormBlock {
  type: 'quote_request_form';
  contractors: QuoteFormContractor[];
  estimate_session_id?: UUID | null;
  /** Chỉ ở client: mã yêu cầu sau khi gửi thành công (lưu localStorage để còn thẻ thành công khi tải lại trang). */
  submitted_code?: string | null;
}

export type UIBlock =
  | QuickRepliesBlock
  | SummaryConfirmBlock
  | EstimateTableBlock
  | BudgetOptionsBlock
  | ContractorListBlock
  | ComparisonTableBlock
  | ContractorDetailBlock
  | SourceBadgesBlock
  | QuoteRequestFormBlock;

export type UIBlockType = UIBlock['type'];

export const UI_BLOCK_TYPES: readonly UIBlockType[] = [
  'quick_replies',
  'summary_confirm',
  'estimate_table',
  'budget_options',
  'contractor_list',
  'comparison_table',
  'contractor_detail',
  'source_badges',
  'quote_request_form',
] as const;

// ---------------------------------------------------------------- Chat API (docs/API_CONTRACT.md)

export type ChatRole = 'user' | 'assistant';

export interface ChatMessageOut {
  id: number;
  role: ChatRole;
  content: string;
  ui_blocks: UIBlock[];
  created_at: ISODateTime;
}

/** POST /api/chat/sessions */
export interface CreateSessionResponse {
  session_id: UUID;
  stage: string;
  slots: Slots;
  messages: ChatMessageOut[];
}

/** GET /api/chat/sessions/{id} */
export interface SessionStateResponse {
  session_id: UUID;
  stage: string;
  slots: Slots;
  missing_required: string[];
  messages: ChatMessageOut[];
}

/** SSE event `done` */
export interface StreamDone {
  message_id: number;
  stage: string;
  slots: Slots;
  missing_required: string[];
}

/** Sự kiện SSE đã được giải mã từ POST /api/chat/sessions/{id}/messages. */
export type ChatStreamEvent =
  | { type: 'text'; delta: string }
  | { type: 'ui_block'; block: UIBlock }
  | ({ type: 'done' } & StreamDone)
  | { type: 'error'; message: string };

// ---------------------------------------------------------------- REST khác

export interface CompareRequest {
  contractor_ids: number[];
  estimate_session_id?: UUID | null;
}

export interface QuoteRequestBody {
  contractor_ids: number[];
  estimate_session_id?: UUID | null;
  customer_name: string;
  customer_phone: string;
  customer_email?: string | null;
  note?: string | null;
}

export interface QuoteRequestResponse {
  ids: UUID[];
  code: string;
}

export interface ClickResponse {
  ok: boolean;
}
