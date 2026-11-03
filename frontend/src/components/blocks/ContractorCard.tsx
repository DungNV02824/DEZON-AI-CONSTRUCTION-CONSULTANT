import { useId } from 'react';
import { FIT_REVIEW, type EvidenceValue, type MatchedContractor, type WorkScope } from '../../api/types';
import { NO_DATA, viNumber } from '../../utils/format';
import { FINISH_PRICE_LABEL_VI, FIT_CLASS, SCOPES_WITHOUT_CONTRACTOR_PRICE } from '../../utils/labels';
import { ContactStaffButton } from '../common/ContactStaffButton';
import { EvidenceCell } from '../common/EvidenceCell';
import { ExternalLink } from '../common/ExternalLink';
import { GalleryTiles } from '../common/Lightbox';
import { SafeImage } from '../common/SafeImage';

export interface ContractorCardProps {
  item: MatchedContractor;
  selected?: boolean;
  canSelectMore?: boolean;
  onToggleCompare?: (id: number) => void;
  onDetail?: () => void;
  /** Chi tiết đang mở ngay dưới thẻ -> nút "Xem chi tiết" thành "Thu gọn chi tiết". */
  detailOpen?: boolean;
  onQuote?: () => void;
  /** Form báo giá đang mở ngay dưới thẻ -> nút thành "Thu gọn báo giá". */
  quoteOpen?: boolean;
  /** Ghi nhận lượt bấm (recommendation_logs). */
  onClickTrack?: () => void;
  /** Phạm vi thi công khách chọn (MatchResult.criteria.work_scope). */
  workScope?: WorkScope | null;
}

const NO_COST: EvidenceValue = { value: null, display: NO_DATA, status: 'unavailable', source: null, is_stale: false };

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join('');
}

/** Thẻ nhà thầu (mục 8.1 / 7.4): lý do là nội dung chính, điểm số hiển thị phụ. */
export function ContractorCardView({
  item,
  selected = false,
  canSelectMore = true,
  onToggleCompare,
  onDetail,
  detailOpen = false,
  onQuote,
  quoteOpen = false,
  onClickTrack,
  workScope,
}: ContractorCardProps) {
  const c = item.contractor;
  const ev = c.evidence ?? {};
  const covers = c.cover_images ?? [];
  const headingId = useId();
  const fitCls = FIT_CLASS[item.fit_label] ?? 'review';
  // Hoàn thiện / Nội thất: hồ sơ nhà thầu không có đơn giá riêng (đơn giá hoàn thiện cơ bản là giá trọn gói)
  // -> không hiển thị chi phí ước tính từ đơn giá đó, kể cả khi payload cũ còn gửi.
  const noScopePrice = !!workScope && SCOPES_WITHOUT_CONTRACTOR_PRICE.includes(workScope);
  const estimatedCost = noScopePrice ? NO_COST : item.estimated_cost;

  return (
    <article className={`dz-ccard${selected ? ' dz-ccard--selected' : ''}`} aria-labelledby={headingId}>
      <GalleryTiles
        images={covers}
        max={3}
        contractorName={c.name}
        fallbackUrl={c.profile_url}
        className="dz-ccard__covers"
        fallbackAlt={() => `Ảnh dự án của ${c.name}`}
      />
      <div className="dz-ccard__body">
        <header className="dz-ccard__head">
          {c.avatar_url ? (
            <SafeImage className="dz-avatar" src={c.avatar_url} alt={`Logo ${c.name}`} />
          ) : (
            <span className="dz-avatar dz-avatar--initials" aria-hidden="true">
              {initials(c.name)}
            </span>
          )}
          <div className="dz-ccard__titles">
            <h4 id={headingId} className="dz-ccard__name">
              <button
                type="button"
                className="dz-namebtn"
                onClick={() => {
                  onClickTrack?.();
                  onDetail?.();
                }}
              >
                {c.name}
              </button>
            </h4>
            <div className="dz-ccard__tags">
              {c.badge && <span className="dz-pill dz-pill--brand">{c.badge}</span>}
              {/* Chủ sản phẩm yêu cầu bỏ nhãn "Cần đánh giá thêm"; giữ "Phù hợp cao" / "Phù hợp". */}
              {item.fit_label !== FIT_REVIEW && <span className={`dz-pill dz-fit dz-fit--${fitCls}`}>{item.fit_label}</span>}
              <span className="dz-score" title="Điểm phù hợp theo tiêu chí của anh/chị (tham khảo)">
                Điểm {item.score}/100
              </span>
            </div>
          </div>
        </header>

        {item.relaxed_location && (
          <p className="dz-note dz-note--info dz-note--inline">Nhà thầu được mở rộng tìm kiếm từ tỉnh lân cận.</p>
        )}

        {item.reasons.length > 0 && (
          <div className="dz-ccard__section">
            <h5>Vì sao đề xuất</h5>
            <ul className="dz-reasons">
              {item.reasons.map((r, i) => (
                <li key={i}>
                  <span className="dz-reasons__icon" aria-hidden="true">
                    ✓
                  </span>
                  <span>{r.text}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        <dl className="dz-facts">
          <div>
            <dt>Công trình ghi nhận trên Dezon</dt>
            <dd>
              <EvidenceCell ev={ev.dezon_project_count} title="Công trình ghi nhận trên Dezon" />
            </dd>
          </div>
          <div>
            <dt>Kinh nghiệm</dt>
            <dd>
              <EvidenceCell ev={ev.years_experience} title="Kinh nghiệm" />
            </dd>
          </div>
          <div>
            <dt>Đơn giá xây thô</dt>
            <dd>
              <EvidenceCell ev={ev.rough_price} title="Đơn giá xây thô" />
            </dd>
          </div>
          <div>
            <dt>{FINISH_PRICE_LABEL_VI}</dt>
            <dd>
              <EvidenceCell ev={ev.finish_price} title={FINISH_PRICE_LABEL_VI} />
            </dd>
          </div>
          <div className="dz-facts__wide">
            <dt>Chi phí ước tính theo đơn giá nhà thầu</dt>
            <dd>
              <EvidenceCell ev={estimatedCost} title="Chi phí ước tính theo đơn giá nhà thầu" />
            </dd>
          </div>
        </dl>

        {item.breakdown.length > 0 && (
          <details className="dz-breakdown">
            <summary>Cách tính điểm</summary>
            <table className="dz-table dz-table--grid">
              <thead>
                <tr>
                  <th scope="col">Tiêu chí</th>
                  <th scope="col" className="dz-num">
                    Trọng số
                  </th>
                  <th scope="col" className="dz-num">
                    Điểm
                  </th>
                </tr>
              </thead>
              <tbody>
                {item.breakdown.map((b) => (
                  <tr key={b.key}>
                    <td>
                      {b.label_vi}
                      {b.note && <div className="dz-muted dz-small">{b.note}</div>}
                    </td>
                    <td className="dz-num">{viNumber(b.weight * 100, 0)}%</td>
                    <td className="dz-num">{viNumber(b.weighted, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        )}

        <div className="dz-ccard__links">
          <ExternalLink href={c.profile_url} className="dz-extlink" onClick={onClickTrack}>
            Xem hồ sơ năng lực ↗
          </ExternalLink>
        </div>
        <div className="dz-actions dz-actions--wrap">
          <button
            type="button"
            className="dz-btn dz-btn--sm"
            aria-expanded={detailOpen}
            onClick={() => {
              if (!detailOpen) onClickTrack?.();
              onDetail?.();
            }}
          >
            {detailOpen ? 'Thu gọn chi tiết' : 'Xem chi tiết'}
          </button>
          <button
            type="button"
            className={`dz-btn dz-btn--sm${selected ? ' dz-btn--active' : ''}`}
            aria-pressed={selected}
            disabled={!selected && !canSelectMore}
            onClick={() => onToggleCompare?.(c.id)}
            title={!selected && !canSelectMore ? 'Chỉ so sánh tối đa 5 nhà thầu' : undefined}
          >
            {selected ? '✓ Đã chọn so sánh' : 'So sánh'}
          </button>
          <button
            type="button"
            className="dz-btn dz-btn--sm dz-btn--primary"
            aria-expanded={quoteOpen}
            onClick={() => {
              if (!quoteOpen) onClickTrack?.();
              onQuote?.();
            }}
          >
            {quoteOpen ? 'Thu gọn báo giá' : 'Yêu cầu báo giá'}
          </button>
          <ContactStaffButton />
        </div>
      </div>
    </article>
  );
}
