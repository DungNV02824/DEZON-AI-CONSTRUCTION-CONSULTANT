/** Cấu hình widget đọc từ thẻ <script> nhúng, URL trang và biến môi trường lúc build. */

export interface WidgetConfig {
  /** Gốc URL backend; rỗng = cùng origin với trang (đường dẫn /api/...). */
  apiBase: string;
  /** Mở sẵn khung chat khi tải trang. */
  open: boolean;
  /** Dùng client giả lập (không gọi backend). */
  mock: boolean;
}

function truthy(v: string | null | undefined): boolean {
  if (v == null) return false;
  return ['', '1', 'true', 'yes', 'on'].includes(v.trim().toLowerCase());
}

export function readConfig(
  script: HTMLScriptElement | null,
  search: string,
  envApiBase: string | undefined,
): WidgetConfig {
  const ds = script?.dataset ?? {};
  const params = new URLSearchParams(search);
  // data-api-base rỗng/thiếu -> dùng VITE_API_BASE_URL lúc build; cả hai rỗng -> cùng origin.
  const apiBase = (ds.apiBase?.trim() || envApiBase?.trim() || '').replace(/\/+$/, '');
  const mockParam = params.get('dezon_mock');
  // Không tự bật mock khi thiếu apiBase (tránh hiển thị dữ liệu giả như thật): apiBase rỗng = cùng origin.
  const mock = (ds.mock !== undefined && truthy(ds.mock)) || (mockParam !== null && truthy(mockParam));
  const open = (ds.open !== undefined && truthy(ds.open)) || params.get('dezon_open') === '1';
  return { apiBase, open, mock };
}

/**
 * Thẻ <script> đã nạp widget. Ưu tiên document.currentScript (lưu ngay khi script chạy); nếu null
 * (vd script bị chèn bởi trình quản lý thẻ rồi chạy lại trong callback) thì tìm theo tên file.
 */
export function findWidgetScript(current: Element | null, doc: Document): HTMLScriptElement | null {
  if (current && current.tagName === 'SCRIPT') return current as HTMLScriptElement;
  const all = Array.from(doc.querySelectorAll<HTMLScriptElement>('script[src*="dezon-widget"]'));
  return all.find((s) => s.dataset.apiBase !== undefined) ?? all[0] ?? null;
}
