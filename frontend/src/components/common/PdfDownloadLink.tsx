import { useWidget } from '../../state/context';

/**
 * Nút "Tải PDF" (V2): thẻ <a> thường tới GET /api/estimates/{id}/pdf — chạy được trong Shadow DOM, không bị chặn
 * popup, mở trình xem PDF gốc trên iOS/Android. Không có phiên khái toán hoặc chế độ mock -> không hiển thị.
 * Cố ý KHÔNG đặt thuộc tính `download`: tên file đã do `Content-Disposition: attachment` của server quyết định, còn
 * khi lỗi (429, nhà thầu không còn, …) server trả trang HTML tiếng Việt — có `download` thì trình duyệt sẽ báo
 * "tải xuống thất bại" hoặc lưu trang lỗi thành file thay vì cho khách đọc thông báo.
 */
export function PdfDownloadLink({
  estimateSessionId,
  contractorIds,
  label,
}: {
  estimateSessionId?: string | null;
  contractorIds?: number[];
  label: string;
}) {
  const { isMock, pdfUrl } = useWidget();
  if (!estimateSessionId || isMock) return null;
  const href = pdfUrl(estimateSessionId, contractorIds);
  if (!href) return null;
  return (
    <div className="dz-pdf-actions">
      <a className="dz-btn dz-btn--sm dz-pdf-link" href={href} target="_blank" rel="noopener noreferrer">
        {label}
      </a>
    </div>
  );
}
