import type { ComparisonTableBlock, EvidenceValue } from '../../api/types';
import { useWidget } from '../../state/context';
import { NO_DATA } from '../../utils/format';
import { EvidenceCell } from '../common/EvidenceCell';
import { ExternalLink } from '../common/ExternalLink';
import { PdfDownloadLink } from '../common/PdfDownloadLink';
import { PDF_COMPARE_MAX, PDF_COMPARE_MIN } from '../../api/client';

const UNAVAILABLE: EvidenceValue = { value: null, display: NO_DATA, status: 'unavailable', source: null, is_stale: false };

/**
 * Bảng so sánh ngang (mục 8.2): cột đầu cố định, cuộn ngang trên mobile, mọi ô có nội dung.
 * `onOpenContractor`: bảng mở ngay trong danh sách nhà thầu -> bấm tên mở hồ sơ dưới thẻ của nhà thầu đó.
 * `estimateId`: phiên khái toán (ưu tiên hơn `block.estimate_session_id`) -> nút "Tải PDF (khái toán + so sánh)".
 */
export function ComparisonTable({
  block,
  onOpenContractor,
  estimateId,
}: {
  block: ComparisonTableBlock;
  onOpenContractor?: (id: number, slug: string) => void;
  estimateId?: string | null;
}) {
  const { openContractor, recordClick } = useWidget();
  const { contractors, rows } = block.data;
  // Bảng tổng quan (câu hỏi chung, C-F4) dùng lại block này với hàng đầu "projects_of_type".
  const isOverview = rows[0]?.key === 'projects_of_type';
  const title = `${isOverview ? 'Tổng quan' : 'So sánh'} ${contractors.length} nhà thầu`;
  // PDF chỉ cho bảng so sánh 2–5 nhà thầu gắn với một khái toán (bảng tổng quan có hàng khác -> không xuất).
  const pdfEstimateId = estimateId ?? block.estimate_session_id ?? null;
  const canExport = !isOverview && contractors.length >= PDF_COMPARE_MIN && contractors.length <= PDF_COMPARE_MAX;
  return (
    <section className="dz-card dz-compare" aria-label={isOverview ? 'Bảng tổng quan nhà thầu' : 'Bảng so sánh nhà thầu'}>
      <h3 className="dz-card__title">{title}</h3>
      <div className="dz-table-scroll" role="region" aria-label="Bảng so sánh, cuộn ngang để xem thêm" tabIndex={0}>
        <table className="dz-table dz-table--compare">
          <thead>
            <tr>
              <th scope="col" className="dz-sticky">
                Tiêu chí
              </th>
              {contractors.map((c) => (
                <th scope="col" key={c.id}>
                  <button
                    type="button"
                    className="dz-namebtn"
                    onClick={() => {
                      recordClick(c.id);
                      if (onOpenContractor) onOpenContractor(c.id, c.slug);
                      else openContractor(c.slug, c.id);
                    }}
                    aria-label={`Xem chi tiết ${c.name}`}
                  >
                    {c.name}
                  </button>
                  <ExternalLink href={c.profile_url} className="dz-extlink dz-small" ariaLabel={`Mở hồ sơ ${c.name} trên Dezon`}>
                    Hồ sơ ↗
                  </ExternalLink>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key}>
                <th scope="row" className="dz-sticky">
                  {row.label_vi}
                </th>
                {contractors.map((c, i) => (
                  <td key={c.id}>
                    <EvidenceCell ev={row.cells[i] ?? UNAVAILABLE} badge="none" />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {canExport && (
        <PdfDownloadLink
          estimateSessionId={pdfEstimateId}
          contractorIds={contractors.map((c) => c.id)}
          label="Tải PDF (khái toán + so sánh)"
        />
      )}
    </section>
  );
}
