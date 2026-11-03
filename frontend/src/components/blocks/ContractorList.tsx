import { Fragment, useRef, useState } from 'react';
import type { ComparisonMatrix, ContractorListBlock, ContractorProfile, QuoteFormContractor } from '../../api/types';
import { useWidget } from '../../state/context';
import { useSavedQuoteCodes } from '../../state/quoteCodes';
import { ComparisonTable } from './ComparisonTable';
import { ContractorCardView } from './ContractorCard';
import { ContractorDetail } from './ContractorDetail';
import { QuoteRequestForm } from './QuoteRequestForm';

const MAX_COMPARE = 5;

/** Chi tiết mở ngay dưới thẻ: đang tải | đã có hồ sơ | lỗi. */
type DetailState = { status: 'loading' } | { status: 'ok'; profile: ContractorProfile } | { status: 'error' };

/** Bảng so sánh mở ngay dưới nút bấm: "top" = nút "So sánh N nhà thầu trong bảng", "bar" = thanh "Đã chọn". */
type CompareState = {
  where: 'top' | 'bar';
  ids: number[];
  status: 'loading' | 'ok' | 'error';
  matrix?: ComparisonMatrix;
};

/**
 * Khóa form báo giá theo tập nhà thầu (mở lại form đã gửi -> hiện thẻ thành công, không cho gửi trùng).
 * id sắp xếp tăng dần (không phụ thuộc thứ tự chọn); form gửi nhiều nhà thầu có tiền tố "b:".
 */
const quoteKey = (contractors: QuoteFormContractor[], bulk = false) => {
  const ids = contractors
    .map((c) => c.id)
    .sort((a, b) => a - b)
    .join(',');
  return bulk ? `b:${ids}` : ids;
};

export function ContractorList({ block }: { block: ContractorListBlock }) {
  const { loadContractor, loadComparison, recordClick, estimateSessionId, isMock, chatSessionId } = useWidget();
  const d = block.data;
  const [selected, setSelected] = useState<number[]>([]);
  const [details, setDetails] = useState<Record<number, DetailState>>({});
  const listKey = String(d.recommendation_log_id ?? d.results.map((r) => r.contractor.id).join('-'));
  const [quoteCodes, rememberQuote] = useSavedQuoteCodes(isMock, chatSessionId, listKey);
  /** Form báo giá mở ngay dưới thẻ (theo id nhà thầu) và dưới thanh "Đã chọn" (gửi nhiều nhà thầu).
   *  Đã gửi trước đó (tải lại trang) -> mở sẵn thẻ "Đã gửi" kèm mã. */
  const [quotes, setQuotes] = useState<Record<number, true>>(() =>
    Object.fromEntries(Object.keys(quoteCodes).filter((k) => !k.startsWith('b:')).map((k) => [Number(k), true])),
  );
  /** Form gửi nhiều nhà thầu (thanh "Đã chọn"): đóng mỗi khi danh sách chọn thay đổi để không gửi nhầm nhà thầu. */
  const [bulkQuote, setBulkQuote] = useState<QuoteFormContractor[] | null>(null);
  const quoteRefs = useRef<Record<number, HTMLDivElement | null>>({});
  /** Lượt mở chi tiết mới nhất theo nhà thầu: kết quả tải của lượt cũ (mở -> đóng -> mở lại) bị bỏ qua. */
  const detailSeq = useRef<Record<number, number>>({});
  const [cmp, setCmp] = useState<CompareState | null>(null);
  const cmpSeq = useRef(0);
  const cardRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const estimateId = d.criteria?.estimate_session_id ?? estimateSessionId;
  const logId = d.recommendation_log_id ?? null;

  const toggle = (id: number) => {
    setBulkQuote(null);
    // Bảng so sánh của thanh "Đã chọn" theo lựa chọn cũ -> đóng khi lựa chọn đổi.
    setCmp((prev) => (prev?.where === 'bar' ? null : prev));
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : prev.length >= MAX_COMPARE ? prev : [...prev, id]));
  };

  /** "So sánh": mở/thu gọn bảng so sánh ngay dưới nút bấm (không đẩy xuống cuối hội thoại). */
  const toggleCompare = (where: CompareState['where'], ids: number[]) => {
    const seq = ++cmpSeq.current;
    if (cmp?.where === where) {
      setCmp(null);
      return;
    }
    setCmp({ where, ids, status: 'loading' });
    loadComparison(ids, estimateId).then(
      (matrix) => {
        if (cmpSeq.current === seq) setCmp({ where, ids, status: 'ok', matrix });
      },
      () => {
        if (cmpSeq.current === seq) setCmp({ where, ids, status: 'error' });
      },
    );
  };

  /** Bấm tên nhà thầu trong bảng so sánh: mở hồ sơ dưới thẻ của nhà thầu đó rồi cuộn tới thẻ. */
  const openDetailFromTable = (id: number, slug: string) => {
    if (!details[id]) toggleDetail(id, slug);
    cardRefs.current[id]?.scrollIntoView?.({ block: 'start', behavior: 'smooth' });
  };

  const compareTable = (where: CompareState['where']) => {
    if (cmp?.where !== where) return null;
    if (cmp.status === 'loading') {
      return (
        <p className="dz-muted dz-small" role="status">
          Đang lập bảng so sánh…
        </p>
      );
    }
    if (cmp.status === 'error' || !cmp.matrix) {
      return (
        <p className="dz-note dz-note--warn" role="alert">
          Xin lỗi, em chưa lập được bảng so sánh. Anh/chị vui lòng thử lại sau.
        </p>
      );
    }
    return <ComparisonTable block={{ type: 'comparison_table', data: cmp.matrix }} onOpenContractor={openDetailFromTable} />;
  };

  /** "Xem chi tiết": mở/thu gọn hồ sơ ngay dưới thẻ (không đẩy xuống cuối hội thoại). */
  const toggleDetail = (id: number, slug: string) => {
    const seq = (detailSeq.current[id] = (detailSeq.current[id] ?? 0) + 1);
    if (details[id]) {
      setDetails(({ [id]: _closed, ...rest }) => rest);
      return;
    }
    setDetails((prev) => ({ ...prev, [id]: { status: 'loading' } }));
    const apply = (st: DetailState) =>
      setDetails((prev) => (prev[id] && detailSeq.current[id] === seq ? { ...prev, [id]: st } : prev));
    loadContractor(slug).then(
      (profile) => apply({ status: 'ok', profile }),
      () => apply({ status: 'error' }),
    );
  };

  /** "Yêu cầu báo giá": mở/thu gọn form ngay dưới thẻ (không đẩy xuống cuối hội thoại). */
  const toggleQuote = (id: number) =>
    setQuotes(({ [id]: open, ...rest }) => (open ? rest : { ...rest, [id]: true }));
  /** Mở form dưới thẻ; form đã mở sẵn (có thể đang ở ngoài màn hình) -> cuộn tới và đặt con trỏ vào form. */
  const openQuote = (id: number) => {
    if (!quotes[id]) {
      setQuotes((prev) => ({ ...prev, [id]: true }));
      return;
    }
    const el = quoteRefs.current[id];
    el?.scrollIntoView?.({ block: 'nearest' });
    el?.querySelector<HTMLElement>('input, button')?.focus();
  };

  const quoteForm = (contractors: QuoteFormContractor[], bulk = false) => {
    const key = quoteKey(contractors, bulk);
    return (
      <QuoteRequestForm
        key={key}
        block={{ type: 'quote_request_form', contractors, estimate_session_id: estimateId, submitted_code: quoteCodes[key] ?? null }}
        onSubmitted={(code) => rememberQuote(key, code)}
        focusOnMount
      />
    );
  };

  const nameOf = (id: number) => d.results.find((r) => r.contractor.id === id)?.contractor.name ?? '';
  // Báo giá gửi nhiều nhà thầu đã xong (kể cả sau khi tải lại trang): giữ thẻ "Đã gửi" dưới danh sách.
  const openBulkKey = bulkQuote ? quoteKey(bulkQuote, true) : null;
  const sentBulk = Object.keys(quoteCodes).filter((k) => k.startsWith('b:') && k !== openBulkKey);
  const allIds = d.results.map((r) => r.contractor.id).slice(0, MAX_COMPARE);

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
          <p className="dz-note dz-note--info">Do ít nhà thầu trong khu vực, kết quả đã mở rộng tìm kiếm sang tỉnh lân cận.</p>
        )}
        {(d.notes ?? []).map((n, i) => (
          <p key={i} className="dz-note dz-note--info">
            {n}
          </p>
        ))}
        {allIds.length >= 2 && (
          <button
            type="button"
            className="dz-btn dz-btn--sm dz-btn--compare"
            aria-expanded={cmp?.where === 'top'}
            onClick={() => toggleCompare('top', allIds)}
          >
            <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
              <path
                fill="currentColor"
                d="M4 4h16a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1zm1 2v3h4V6H5zm6 0v3h8V6h-8zm-6 5v7h4v-7H5zm6 0v7h8v-7h-8z"
              />
            </svg>
            {cmp?.where === 'top' ? 'Thu gọn bảng so sánh' : `So sánh ${allIds.length} nhà thầu trong bảng`}
          </button>
        )}
        {compareTable('top')}
      </div>
      <div className="dz-clist__items">
        {d.results.map((item) => {
          const id = item.contractor.id;
          const det = details[id];
          return (
            <Fragment key={id}>
              <div
                className="dz-clist__anchor"
                ref={(el) => {
                  cardRefs.current[id] = el;
                }}
              />
              <ContractorCardView
                item={item}
                workScope={d.criteria?.work_scope ?? null}
                selected={selected.includes(id)}
                canSelectMore={selected.length < MAX_COMPARE}
                onToggleCompare={toggle}
                onClickTrack={() => recordClick(id, logId)}
                detailOpen={!!det}
                onDetail={() => toggleDetail(id, item.contractor.slug)}
                quoteOpen={!!quotes[id]}
                onQuote={() => toggleQuote(id)}
              />
              {quotes[id] && (
                <div
                  ref={(el) => {
                    quoteRefs.current[id] = el;
                  }}
                >
                  {quoteForm([{ id, name: item.contractor.name }])}
                </div>
              )}
              {det?.status === 'loading' && (
                <p className="dz-muted dz-small" role="status">
                  Đang tải hồ sơ {item.contractor.name}…
                </p>
              )}
              {det?.status === 'error' && (
                <p className="dz-note dz-note--warn" role="alert">
                  Xin lỗi, em chưa tải được hồ sơ nhà thầu. Anh/chị vui lòng thử lại sau.
                </p>
              )}
              {det?.status === 'ok' && (
                <ContractorDetail
                  block={{ type: 'contractor_detail', data: det.profile, estimate_session_id: estimateId }}
                  onQuote={() => openQuote(id)}
                />
              )}
            </Fragment>
          );
        })}
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
              aria-expanded={cmp?.where === 'bar'}
              onClick={() => toggleCompare('bar', [...selected])}
            >
              {cmp?.where === 'bar' ? 'Thu gọn so sánh' : 'So sánh'}
            </button>
            <button
              type="button"
              className="dz-btn dz-btn--sm"
              aria-expanded={selected.length > 1 ? !!bulkQuote : undefined}
              onClick={() =>
                // Chọn 1 nhà thầu -> mở form ngay dưới thẻ của nhà thầu đó (một khóa duy nhất, không gửi trùng).
                selected.length === 1
                  ? openQuote(selected[0]!)
                  : setBulkQuote((prev) => (prev ? null : selected.map((id) => ({ id, name: nameOf(id) }))))
              }
            >
              {bulkQuote ? 'Thu gọn báo giá' : 'Yêu cầu báo giá'}
            </button>
            <button type="button" className="dz-btn dz-btn--sm dz-btn--ghost" onClick={() => {
                setSelected([]);
                setBulkQuote(null);
                setCmp((prev) => (prev?.where === 'bar' ? null : prev));
              }}
              aria-label="Bỏ chọn tất cả">
              Bỏ chọn
            </button>
          </div>
        </div>
      )}
      {selected.length > 0 && compareTable('bar')}
      {sentBulk.map((k) => (
        <Fragment key={k}>
          {quoteForm(
            k
              .slice(2)
              .split(',')
              .map((id) => ({ id: Number(id), name: nameOf(Number(id)) })),
            true,
          )}
        </Fragment>
      ))}
      {bulkQuote && quoteForm(bulkQuote, true)}
    </section>
  );
}
