import { useState } from 'react';
import type { ContractorListBlock } from '../../api/types';
import { useWidget } from '../../state/context';
import { StatusLegend } from '../common/StatusLegend';
import { ContractorCardView } from './ContractorCard';

const MAX_COMPARE = 5;

export function ContractorList({ block }: { block: ContractorListBlock }) {
  const { openContractor, compare, openQuoteForm, recordClick, estimateSessionId } = useWidget();
  const d = block.data;
  const [selected, setSelected] = useState<number[]>([]);
  const estimateId = d.criteria?.estimate_session_id ?? estimateSessionId;
  const logId = d.recommendation_log_id ?? null;

  const toggle = (id: number) =>
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : prev.length >= MAX_COMPARE ? prev : [...prev, id]));

  const nameOf = (id: number) => d.results.find((r) => r.contractor.id === id)?.contractor.name ?? '';
  const allIds = d.results.map((r) => r.contractor.id).slice(0, MAX_COMPARE);
  // Lý do có chấm trạng thái nguồn -> hiện chú giải màu (đọc được trên mobile, không cần hover).
  const hasDots = d.results.some((r) =>
    r.reasons.some((x) => {
      const st = r.contractor.evidence?.[x.evidence]?.status;
      return !!st && st !== 'unavailable';
    }),
  );

  if (d.results.length === 0) {
    return (
      <section className="dz-card" aria-label="Danh sách nhà thầu">
        <p className="dz-muted">Chưa có dữ liệu nhà thầu phù hợp với tiêu chí của anh/chị.</p>
        {(d.notes ?? []).map((n, i) => (
          <p key={i} className="dz-note dz-note--info">
            {n}
          </p>
        ))}
      </section>
    );
  }

  return (
    <section className="dz-clist" aria-label="Danh sách nhà thầu đề xuất">
      <div className="dz-clist__head">
        <h3 className="dz-card__title">{d.results.length} nhà thầu phù hợp nhất với tiêu chí của anh/chị</h3>
        <p className="dz-muted dz-small">
          Trong {d.total_candidates} nhà thầu đạt điều kiện lọc. Điểm số chỉ để tham khảo, lý do đề xuất kèm nguồn là căn cứ chính.
        </p>
        {d.relaxed_to_neighbors && (
          <p className="dz-note dz-note--info">Do ít nhà thầu trong khu vực, em đã mở rộng tìm kiếm sang tỉnh lân cận.</p>
        )}
        {(d.notes ?? []).map((n, i) => (
          <p key={i} className="dz-note dz-note--info">
            {n}
          </p>
        ))}
        {hasDots && (
          <div className="dz-legend-box">
            <p className="dz-muted dz-small">Chấm màu cạnh mỗi lý do cho biết mức xác minh của nguồn (bấm chấm để xem nguồn):</p>
            <StatusLegend />
          </div>
        )}
        {allIds.length >= 2 && (
          <button type="button" className="dz-linkbtn" onClick={() => compare(allIds, estimateId)}>
            So sánh {allIds.length} nhà thầu trong bảng
          </button>
        )}
      </div>
      <div className="dz-clist__items">
        {d.results.map((item) => (
          <ContractorCardView
            key={item.contractor.id}
            item={item}
            selected={selected.includes(item.contractor.id)}
            canSelectMore={selected.length < MAX_COMPARE}
            onToggleCompare={toggle}
            onClickTrack={() => recordClick(item.contractor.id, logId)}
            onDetail={() => openContractor(item.contractor.slug, item.contractor.id)}
            onQuote={() => openQuoteForm([{ id: item.contractor.id, name: item.contractor.name }], estimateId)}
          />
        ))}
      </div>
      {selected.length > 0 && (
        <div className="dz-selbar" role="region" aria-label="Nhà thầu đã chọn">
          <span className="dz-selbar__count">
            Đã chọn {selected.length}/{MAX_COMPARE}
            {selected.length < 2 && <span className="dz-muted"> · chọn ít nhất 2 để so sánh</span>}
          </span>
          <div className="dz-actions">
            <button
              type="button"
              className="dz-btn dz-btn--sm dz-btn--primary"
              disabled={selected.length < 2}
              onClick={() => compare(selected, estimateId)}
            >
              So sánh
            </button>
            <button
              type="button"
              className="dz-btn dz-btn--sm"
              onClick={() => openQuoteForm(selected.map((id) => ({ id, name: nameOf(id) })), estimateId)}
            >
              Yêu cầu báo giá
            </button>
            <button type="button" className="dz-btn dz-btn--sm dz-btn--ghost" onClick={() => setSelected([])} aria-label="Bỏ chọn tất cả">
              Bỏ chọn
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
