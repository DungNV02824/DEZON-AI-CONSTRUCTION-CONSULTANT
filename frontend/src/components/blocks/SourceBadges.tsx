import type { SourceBadgesBlock } from '../../api/types';
import { StatusBadge } from '../common/StatusBadge';

/** Chip nguồn (vd "Dezon") — hover/bấm để xem URL + ngày cập nhật. */
export function SourceBadges({ block }: { block: SourceBadgesBlock }) {
  if (!block.badges.length) return null;
  return (
    <div className="dz-srcbadges" role="group" aria-label="Nguồn dữ liệu">
      <span className="dz-muted dz-small">Nguồn:</span>
      {block.badges.map((b, i) => (
        <StatusBadge key={`${b.label}-${i}`} status={b.status} source={b.source} label={b.label} title={b.label} />
      ))}
    </div>
  );
}
