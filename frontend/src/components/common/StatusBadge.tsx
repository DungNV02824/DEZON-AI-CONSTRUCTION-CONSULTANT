import { useMemo } from 'react';
import type { SourceRef, VerificationStatus } from '../../api/types';
import { formatDateVi } from '../../utils/format';
import { STATUS_LABEL_VI, normalizeStatus } from '../../utils/labels';
import { useSourceTrigger, type SourceInfo } from './SourcePopover';

export interface StatusBadgeProps {
  status: VerificationStatus | string;
  source?: SourceRef | null;
  isStale?: boolean;
  /** "full": nhãn chữ; "dot": chấm màu gọn (bảng so sánh). */
  variant?: 'full' | 'dot';
  /** Nhãn thay thế (vd chip "Dezon" trong source_badges). */
  label?: string;
  /** Tiêu đề trong tooltip (vd tên trường). */
  title?: string;
}

/** Badge trạng thái xác minh (mục 4.4). Bấm/hover để xem nguồn, ngày cập nhật. */
export function StatusBadge({ status, source, isStale, variant = 'full', label, title }: StatusBadgeProps) {
  const st = normalizeStatus(status);
  const info: SourceInfo = useMemo(() => ({ status: st, source, isStale, title }), [st, source, isStale, title]);
  const trigger = useSourceTrigger(info);
  const statusLabel = STATUS_LABEL_VI[st];
  const date = formatDateVi(source?.updated_at);
  const aria = [
    label ? `${label}:` : null,
    statusLabel,
    source ? `— nguồn: ${source.label}` : null,
    date ? `, cập nhật ${date}` : null,
    '. Bấm để xem nguồn',
  ]
    .filter(Boolean)
    .join(' ');
  return (
    <button
      type="button"
      className={`dz-badge dz-badge--${st} dz-badge--${variant}${isStale ? ' dz-badge--stale' : ''}`}
      aria-label={aria}
      title={variant === 'dot' ? statusLabel : undefined}
      {...trigger}
    >
      {variant === 'dot' ? (
        <span className={`dz-dot dz-dot--${st}`} aria-hidden="true" />
      ) : (
        <span>{label ?? statusLabel}</span>
      )}
      {isStale && (
        <span className="dz-badge__stale" aria-hidden="true">
          ⚠
        </span>
      )}
    </button>
  );
}
