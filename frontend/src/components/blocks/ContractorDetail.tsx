import { useState } from 'react';
import type { ContractorDetailBlock, ContractorInfoKey, ImageRef, ProjectSummary } from '../../api/types';
import { useWidget } from '../../state/context';
import { useSavedQuoteCodes } from '../../state/quoteCodes';
import { formatDateVi, NO_DATA } from '../../utils/format';
import { BUILDING_TYPE_VI, FINISH_PRICE_LABEL_VI, PROJECT_ROLE_VI } from '../../utils/labels';
import { EvidenceCell } from '../common/EvidenceCell';
import { ExternalLink } from '../common/ExternalLink';
import { GalleryTiles } from '../common/Lightbox';
import { SafeImage } from '../common/SafeImage';
import { QuoteRequestForm } from './QuoteRequestForm';

const INFO_LABELS: Record<ContractorInfoKey, string> = {
  founded_year: 'Năm thành lập',
  years_experience: 'Kinh nghiệm',
  staff_workers: 'Nhân sự thợ',
  staff_office: 'Nhân sự văn phòng',
  rough_price: 'Đơn giá xây thô',
  finish_price: FINISH_PRICE_LABEL_VI,
  typical_project_value: 'Quy mô dự án',
  dezon_project_count: 'Công trình ghi nhận trên Dezon',
  warranty: 'Bảo hành',
};
const INFO_ORDER = Object.keys(INFO_LABELS) as ContractorInfoKey[];
/** Khóa phụ backend có thể gửi (fallback.py INFO_LABEL_VI). */
const EXTRA_INFO_LABELS: Record<string, string> = { staff: 'Nhân sự', province: 'Khu vực' };
/** Không bao giờ in tên khóa thô (vd "years_experience") ra giao diện. */
export function infoLabel(k: string): string {
  return (INFO_LABELS as Record<string, string>)[k] ?? EXTRA_INFO_LABELS[k] ?? 'Thông tin khác';
}
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

/** Bổ sung tên/link công trình cho ảnh khi payload cũ (lịch sử, localStorage) chưa gửi kèm từng ảnh. */
function withProject(img: ImageRef, p: ProjectSummary | undefined): ImageRef {
  if (!p) return img;
  return { ...img, project_name: img.project_name || p.name, project_url: img.project_url || p.project_url };
}

/** Ảnh của một công trình. */
function projectImages(p: ProjectSummary): ImageRef[] {
  return (p.images ?? []).map((img) => withProject(img, p));
}

/** Lưới ảnh chính của hồ sơ: tra công trình theo project_slug để ảnh nào cũng có tên/link công trình. */
function profileImages(images: ImageRef[], projects: ProjectSummary[]): ImageRef[] {
  const bySlug = new Map(projects.map((pr) => [pr.slug, pr]));
  return images.map((img) => withProject(img, img.project_slug ? bySlug.get(img.project_slug) : undefined));
}

/**
 * Hồ sơ mở rộng (mục 8.3). "Yêu cầu báo giá" mở form ngay dưới hồ sơ (không thêm tin nhắn ở cuối hội thoại).
 * `onQuote`: hồ sơ đang mở dưới thẻ trong danh sách -> danh sách tự mở form dưới thẻ đó.
 */
export function ContractorDetail({ block, onQuote }: { block: ContractorDetailBlock; onQuote?: () => void }) {
  const { estimateSessionId, isMock, chatSessionId } = useWidget();
  const p = block.data;
  const c = p.card;
  const [quoteCodes, rememberQuote] = useSavedQuoteCodes(isMock, chatSessionId, `detail-${c.id}`);
  const quoteKey = String(c.id);
  const [quoteOpen, setQuoteOpen] = useState(() => !onQuote && !!quoteCodes[quoteKey]);
  const images = profileImages(p.images ?? [], p.projects ?? []);
  // Ảnh trên Dezon nhiều hơn số ảnh backend gửi -> thêm link xem toàn bộ trên hồ sơ gốc.
  const total = Math.max(p.total_images ?? 0, images.length);
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
        <>
          <GalleryTiles
            images={images}
            max={3}
            contractorName={c.name}
            fallbackUrl={c.profile_url}
            className="dz-gallery"
            tileClassName="dz-gallery__item"
            fallbackAlt={(i) => `Ảnh dự án ${i + 1} của ${c.name}`}
          />
          {total > images.length && (
            <ExternalLink href={c.profile_url} className="dz-extlink dz-small">
              Xem toàn bộ {total} ảnh trên Dezon ↗
            </ExternalLink>
          )}
        </>
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
              <th scope="row">{infoLabel(k)}</th>
              <td>
                <EvidenceCell ev={p.info[k]} title={infoLabel(k)} />
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
              <GalleryTiles
                images={projectImages(pr)}
                max={4}
                contractorName={c.name}
                fallbackUrl={c.profile_url}
                className="dz-pgallery"
                fallbackAlt={(i) => `Ảnh ${i + 1} công trình ${pr.name}`}
              />
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
          aria-expanded={onQuote ? undefined : quoteOpen}
          onClick={() => (onQuote ? onQuote() : setQuoteOpen((v) => !v))}
        >
          {!onQuote && quoteOpen ? 'Thu gọn báo giá' : 'Yêu cầu báo giá'}
        </button>
      </div>
      {!onQuote && quoteOpen && (
        <QuoteRequestForm
          block={{
            type: 'quote_request_form',
            contractors: [{ id: c.id, name: c.name }],
            estimate_session_id: estimateId,
            submitted_code: quoteCodes[quoteKey] ?? null,
          }}
          onSubmitted={(code) => rememberQuote(quoteKey, code)}
          focusOnMount
        />
      )}
    </section>
  );
}
