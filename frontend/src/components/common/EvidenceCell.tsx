import type { EvidenceValue } from '../../api/types';
import { formatDateVi, NO_DATA } from '../../utils/format';
import { normalizeStatus } from '../../utils/labels';
import { staleMessage } from './SourcePopover';
import { StatusBadge } from './StatusBadge';

export interface EvidenceCellProps {
  ev: EvidenceValue | null | undefined;
  /** Kiểu badge: "full" (nhãn chữ), "dot" (chấm màu), "none" (ẩn badge). */
  badge?: 'full' | 'dot' | 'none';
  /** Nhấn mạnh giá trị (vd chi phí sơ bộ). */
  emphasize?: boolean;
  /** Hiện link nguồn ngay dưới giá trị (bảng khái toán). */
  showSourceLine?: boolean;
  /** Tên trường — hiển thị trong tooltip nguồn. */
  title?: string;
  className?: string;
}

/**
 * Hiển thị một EvidenceValue: LUÔN dùng `display` từ backend, không định dạng lại `value`.
 * Thiếu dữ liệu (status unavailable / null / display rỗng) -> chữ xám đúng "Chưa có dữ liệu".
 */
export function EvidenceCell({ ev, badge = 'full', emphasize, showSourceLine, title, className }: EvidenceCellProps) {
  const status = normalizeStatus(ev?.status);
  const display = ev && typeof ev.display === 'string' && ev.display.trim() !== '' ? ev.display : NO_DATA;
  const unavailable = status === 'unavailable' || display === NO_DATA;
  const cls = ['dz-ev', `dz-ev--${unavailable ? 'unavailable' : status}`, emphasize ? 'dz-ev--em' : '', className ?? '']
    .filter(Boolean)
    .join(' ');

  if (unavailable) {
    return (
      <span className={cls} title="Không đủ thông tin để đưa ra kết luận">
        <span className="dz-ev__text">{NO_DATA}</span>
      </span>
    );
  }

  const source = ev?.source ?? null;
  const stale = Boolean(ev?.is_stale);
  return (
    <span className={cls}>
      <span className="dz-ev__text">{display}</span>
      {badge !== 'none' && (
        <StatusBadge status={status} source={source} isStale={stale} variant={badge} title={title} />
      )}
      {showSourceLine && source && (
        <span className="dz-ev__source">
          Nguồn:{' '}
          {source.url ? (
            <a href={source.url} target="_blank" rel="noopener noreferrer">
              {source.label} ↗
            </a>
          ) : (
            source.label
          )}
          {formatDateVi(source.updated_at) && <> · cập nhật {formatDateVi(source.updated_at)}</>}
        </span>
      )}
      {stale && badge !== 'dot' && <span className="dz-ev__stale">⚠ {staleMessage(source?.updated_at)}</span>}
    </span>
  );
}
