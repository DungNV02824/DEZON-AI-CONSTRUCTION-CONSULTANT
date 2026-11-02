import type { VerificationStatus } from '../api/types';

export const STATUS_LABEL_VI: Record<VerificationStatus, string> = {
  verified: 'Đã xác minh',
  on_dezon: 'Có dữ liệu trên Dezon',
  reference: 'Dữ liệu tham khảo',
  unavailable: 'Chưa có dữ liệu',
};

/** Ý nghĩa từng trạng thái (đặc tả mục 4.4). */
export const STATUS_MEANING_VI: Record<VerificationStatus, string> = {
  verified: 'Được Dezon hoặc nguồn có thẩm quyền kiểm tra.',
  on_dezon: 'Có hồ sơ/dự án được ghi nhận trên Dezon nhưng chưa đồng nghĩa đã xác minh độc lập.',
  reference: 'Dữ liệu thị trường hoặc do nhà thầu cung cấp, chưa được kiểm chứng đầy đủ.',
  unavailable: 'Không đủ thông tin để đưa ra kết luận.',
};

export const VERIFICATION_STATUSES: VerificationStatus[] = ['verified', 'on_dezon', 'reference', 'unavailable'];

export function normalizeStatus(s: string | null | undefined): VerificationStatus {
  return s === 'verified' || s === 'on_dezon' || s === 'reference' ? s : 'unavailable';
}

export const BUILDING_TYPE_VI: Record<string, string> = {
  nha_pho: 'Nhà phố',
  biet_thu: 'Biệt thự',
  can_ho: 'Căn hộ',
  van_phong: 'Văn phòng',
  khach_san: 'Khách sạn',
  khac: 'Khác',
};
export const WORK_SCOPE_VI: Record<string, string> = {
  xay_tho: 'Xây thô',
  hoan_thien: 'Hoàn thiện',
  tron_goi: 'Trọn gói',
  noi_that: 'Nội thất',
};
export const QUALITY_TIER_VI: Record<string, string> = {
  tiet_kiem: 'Tiết kiệm',
  trung_binh: 'Trung bình',
  kha: 'Khá',
  cao_cap: 'Cao cấp',
};
export const PROJECT_ROLE_VI: Record<string, string> = {
  thi_cong: 'Thi công',
  thiet_ke: 'Thiết kế',
  thiet_ke_thi_cong: 'Thiết kế & thi công',
  tong_thau: 'Tổng thầu',
};

export const FIT_CLASS: Record<string, string> = {
  'Phù hợp cao': 'high',
  'Phù hợp': 'ok',
  'Cần đánh giá thêm': 'review',
};
