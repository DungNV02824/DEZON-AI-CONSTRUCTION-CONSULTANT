import type { WidgetConfig } from './config';

declare global {
  interface Window {
    __DEZON_WIDGET_MOUNTED__?: boolean;
    /** API điều khiển widget từ trang chủ (vd nút "Tư vấn ngay" trên dezon.vn gọi DezonWidget.open()). */
    DezonWidget?: { open(): void; close(): void; toggle(): void; expand(): void; config: WidgetConfig };
  }
}

export {};
