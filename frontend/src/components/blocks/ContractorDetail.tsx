import type { ContractorDetailBlock, ContractorInfoKey, ProjectSummary } from '../../api/types';
import { useWidget } from '../../state/context';
import { formatDateVi, NO_DATA } from '../../utils/format';
import { BUILDING_TYPE_VI, PROJECT_ROLE_VI } from '../../utils/labels';
import { EvidenceCell } from '../common/EvidenceCell';
import { ExternalLink } from '../common/ExternalLink';
import { SafeImage } from '../common/SafeImage';

const INFO_LABELS: Record<ContractorInfoKey, string> = {
  founded_year: 'Năm thành lập',
  years_experience: 'Kinh nghiệm',
  staff_workers: 'Nhân sự thợ',
  staff_office: 'Nhân sự văn phòng',
  rough_price: 'Đơn giá xây thô',
  finish_price: 'Đơn giá hoàn thiện cơ bản',
  typical_project_value: 'Quy mô dự án',
  dezon_project_count: 'Công trình ghi nhận trên Dezon',
  warranty: 'Bảo hành',
};
const INFO_ORDER = Object.keys(INFO_LABELS) as ContractorInfoKey[];
const DEFAULT_CHIPS = ['Hồ sơ nhà thầu', 'Dự án thực tế', 'Đơn giá tham khảo'];

function projectMeta(p: ProjectSummary): string[] {
  const parts: string[] = [];
  const place = [p.district, p.province].filter(Boolean).join(', ');
  if (place) parts.push(place);
  if (p.building_type) parts.push(BUILDING_TYPE_VI[p.building_type] ?? p.building_type);
  const names = p.contractor_names ?? [];
  if (names.length) parts.push(`Đơn vị thi công: ${names.join(', ')}`);
  else if (p.role) parts.push(PROJECT_ROLE_VI[p.role] ?? p.role);
  const start = formatDateVi(p.start_date);
  const end = formatDateVi(p.end_date);
  if (start || end) parts.push(`Thời gian: ${start ?? '…'} – ${end ?? '…'}`);
  return parts;
}

/** Hồ sơ mở rộng (mục 8.3). */
export function ContractorDetail({ block }: { block: ContractorDetailBlock }) {
  const { openQuoteForm, estimateSessionId } = useWidget();
  const p = block.data;
  const c = p.card;
  const images = p.images.slice(0, 3);
  const total = Math.max(p.total_images ?? 0, p.images.length);
  const more = total - images.length;
  const infoKeys = [...INFO_ORDER, ...Object.keys(p.info).filter((k) => !(INFO_ORDER as string[]).includes(k))];
  const chips = p.chips && p.chips.length ? p.chips : DEFAULT_CHIPS;
  const estimateId = block.estimate_session_id ?? estimateSessionId;

  return (
    <section className="dz-card dz-detail" aria-label={`Hồ sơ nhà thầu ${c.name}`}>
      <header className="dz-detail__head">
        {c.avatar_url && <SafeImage className="dz-avatar" src={c.avatar_url} alt={`Logo ${c.name}`} />}
        <div>
          <h3 className="dz-card__title">{c.name}</h3>
          {c.badge && <span className="dz-pill dz-pill--brand">{c.badge}</span>}
        </div>
      </header>

      <ul className="dz-chips" aria-label="Loại dữ liệu">
        {chips.map((ch) => (
          <li key={ch} className="dz-chip">
            {ch}
          </li>
        ))}
      </ul>

      {images.length > 0 ? (
        <div className={`dz-gallery dz-gallery--${images.length + (more > 0 ? 1 : 0)}`}>
          {images.map((img, i) => (
            <figure key={`${img.url}-${i}`} className="dz-gallery__item">
              <SafeImage src={img.url} alt={img.caption ?? `Ảnh dự án ${i + 1} của ${c.name}`} />
            </figure>
          ))}
          {more > 0 && (
            <ExternalLink href={c.profile_url} className="dz-gallery__more" ariaLabel={`Xem thêm ${more} ảnh trên Dezon`}>
              +{more}
            </ExternalLink>
          )}
        </div>
      ) : (
        <p className="dz-muted">Ảnh dự án: {NO_DATA}</p>
      )}

      <div className="dz-detail__desc">
        <EvidenceCell ev={p.description} title="Giới thiệu" />
      </div>

      <table className="dz-table dz-table--kv">
        <tbody>
          {infoKeys.map((k) => (
            <tr key={k}>
              <th scope="row">{INFO_LABELS[k as ContractorInfoKey] ?? k}</th>
              <td>
                <EvidenceCell ev={p.info[k]} title={INFO_LABELS[k as ContractorInfoKey] ?? k} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h4 className="dz-subtitle">Công trình thực tế trên Dezon</h4>
      {p.projects.length === 0 ? (
        <p className="dz-ev dz-ev--unavailable">
          <span className="dz-ev__text">{NO_DATA}</span>
        </p>
      ) : (
        <ul className="dz-projects">
          {p.projects.map((pr) => (
            <li key={pr.id}>
              <ExternalLink href={pr.project_url} className="dz-extlink">
                {pr.name} ↗
              </ExternalLink>
              <div className="dz-muted dz-small">{projectMeta(pr).join(' · ')}</div>
            </li>
          ))}
        </ul>
      )}

      <div className="dz-actions dz-actions--wrap">
        <ExternalLink href={c.profile_url} className="dz-btn dz-btn--sm">
          Mở hồ sơ đầy đủ trên Dezon ↗
        </ExternalLink>
        <button
          type="button"
          className="dz-btn dz-btn--sm dz-btn--primary"
          onClick={() => openQuoteForm([{ id: c.id, name: c.name }], estimateId)}
        >
          Yêu cầu báo giá
        </button>
      </div>
    </section>
  );
}
