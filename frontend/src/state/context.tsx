import { createContext, useContext } from 'react';
import type { ComparisonMatrix, ContractorProfile, QuoteFormContractor } from '../api/types';

/** Các thao tác mà UI block có thể gọi. Mặc định là no-op để block render được độc lập (test, storybook). */
export interface WidgetActions {
  isMock: boolean;
  /** Đang chờ trả lời — khóa các nút gửi tin nhắn. */
  busy: boolean;
  send(text: string): void;
  focusInput(): void;
  openContractor(slug: string, contractorId?: number): void;
  /** Tải hồ sơ nhà thầu để mở chi tiết ngay dưới thẻ (không thêm tin nhắn mới). */
  loadContractor(slug: string): Promise<ContractorProfile>;
  compare(contractorIds: number[], estimateSessionId?: string | null): void;
  /** Tải bảng so sánh để mở ngay dưới nút bấm trong danh sách nhà thầu (không thêm tin nhắn mới). */
  loadComparison(contractorIds: number[], estimateSessionId?: string | null): Promise<ComparisonMatrix>;
  openQuoteForm(contractors: QuoteFormContractor[], estimateSessionId?: string | null): void;
  recordClick(contractorId: number, logId?: number | null): void;
  submitQuote: import('../api/client').ApiClient['createQuoteRequest'];
  /** Form báo giá đã gửi thành công -> ghi mã vào block (giữ thẻ thành công sau khi tải lại trang). */
  quoteSubmitted(block: import('../api/types').QuoteRequestFormBlock, code: string): void;
  /** estimate_session_id gần nhất trong hội thoại (để đính kèm báo giá/so sánh). */
  estimateSessionId: string | null;
  /** Mã phiên chat hiện tại — gửi kèm yêu cầu báo giá để backend ghi phong cách / mức đầu tư khách đã chọn. */
  chatSessionId: string | null;
}

const noop = () => undefined;

export const defaultActions: WidgetActions = {
  isMock: false,
  busy: false,
  send: noop,
  focusInput: noop,
  openContractor: noop,
  loadContractor: () => Promise.reject(new Error('Chưa cấu hình client')),
  compare: noop,
  loadComparison: () => Promise.reject(new Error('Chưa cấu hình client')),
  openQuoteForm: noop,
  recordClick: noop,
  submitQuote: () => Promise.reject(new Error('Chưa cấu hình client')),
  quoteSubmitted: noop,
  estimateSessionId: null,
  chatSessionId: null,
};

export const WidgetContext = createContext<WidgetActions>(defaultActions);

export function useWidget(): WidgetActions {
  return useContext(WidgetContext);
}
