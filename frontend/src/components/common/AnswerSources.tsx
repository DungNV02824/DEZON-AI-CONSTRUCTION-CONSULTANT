/**
 * V2 — Link nhỏ "Nguồn dữ liệu (N)" cuối mỗi câu trả lời có số liệu; bấm mở hộp thoại ngay trong widget liệt kê mọi
 * EvidenceValue đã dùng, nhóm theo nguồn (nhãn, link mở tab mới, ngày cập nhật, trạng thái, cảnh báo dữ liệu cũ),
 * mỗi nhóm là bảng "Thông tin | Giá trị" (nguyên văn `display`), cuối cùng là mục thu gọn "Chưa có dữ liệu (k)".
 * N = số nguồn. Esc / nút × / bấm nền để đóng; focus trả về link. Chạy trong Shadow DOM (chỉ dùng sự kiện React).
 *
 * Trong App có <AnswerSourcesProvider> (đặt trong LightboxProvider/ProjectGalleryProvider, cấp .dz-root) -> hộp thoại
 * render NGOÀI .dz-panel (panel là position:fixed + z-index -> tạo stacking context riêng, nút launcher sẽ đè lên
 * hộp thoại) và NGOÀI log aria-live của danh sách tin nhắn (không bị trình đọc màn hình đọc lại cả bảng nguồn).
 * Không có provider (test, block độc lập) -> hộp thoại render tại chỗ.
 */
import {
  createContext,
  useCallback,
  useContext,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
} from 'react';
import type { UIBlock } from '../../api/types';
import { collectAnswerSources, type AnswerSources, type SourceEntry } from '../../utils/answerSources';
import { NO_DATA } from '../../utils/format';
import { trapTab, useModalLifecycle } from './Lightbox';
import { SourceDetails } from './SourcePopover';

export const ANSWER_SOURCES_TITLE = 'Nguồn dữ liệu';
export const ANSWER_SOURCES_INTRO =
  'Đây là các giá trị em đã dùng để trả lời anh/chị. Anh/chị bấm vào link nguồn để tự kiểm chứng trên trang gốc.';

interface OpenRequest {
  data: AnswerSources;
  /** Gọi khi hộp thoại đóng (link cập nhật aria-expanded). */
  onClosed?: () => void;
}

interface OpenState extends OpenRequest {
  returnFocus: HTMLElement | null;
  /** Link mở nằm trong giao diện mở rộng (.dz-root--page) -> hộp thoại giữ tông giao diện đó khi render ở gốc. */
  page: boolean;
}

type OpenFn = (req: OpenRequest, trigger?: HTMLElement | null) => void;

const AnswerSourcesContext = createContext<OpenFn | null>(null);

function toState(req: OpenRequest, trigger: HTMLElement | null | undefined): OpenState {
  return { ...req, returnFocus: trigger ?? null, page: !!trigger?.closest('.dz-root--page') };
}

/** Đặt trong .dz-root của App: hộp thoại "Nguồn dữ liệu" render ở cấp gốc (ngoài panel và log tin nhắn). */
export function AnswerSourcesProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<OpenState | null>(null);
  const stateRef = useRef(state);
  stateRef.current = state;
  const open = useCallback<OpenFn>((req, trigger) => setState(toState(req, trigger)), []);
  const close = useCallback(() => {
    stateRef.current?.onClosed?.();
    setState(null);
  }, []);
  return (
    <AnswerSourcesContext.Provider value={open}>
      {children}
      {state && <AnswerSourcesDialog data={state.data} page={state.page} onClose={close} returnFocus={state.returnFocus} />}
    </AnswerSourcesContext.Provider>
  );
}

export function AnswerSourcesLink({ blocks }: { blocks: readonly UIBlock[] }) {
  const data = useMemo(() => collectAnswerSources(blocks), [blocks]);
  const shared = useContext(AnswerSourcesContext);
  const [open, setOpen] = useState(false);
  const btnRef = useRef<HTMLButtonElement | null>(null);
  if (data.groups.length === 0) return null;
  const onClick = () => {
    setOpen(true);
    if (shared) shared({ data, onClosed: () => setOpen(false) }, btnRef.current);
  };
  return (
    <>
      <button
        ref={btnRef}
        type="button"
        className="dz-srclink"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={onClick}
      >
        {ANSWER_SOURCES_TITLE} ({data.groups.length})
      </button>
      {!shared && open && <AnswerSourcesDialog data={data} onClose={() => setOpen(false)} returnFocus={btnRef.current} />}
    </>
  );
}

function EntryRows({ entries }: { entries: SourceEntry[] }) {
  return (
    <>
      {entries.map((e, i) => (
        <tr key={i}>
          <th scope="row">
            {e.context && <span className="dz-srcdlg__ctx">{e.context}</span>}
            {e.label}
          </th>
          <td>
            {e.display}
            {e.isStale && (
              <span className="dz-srcdlg__stale" role="img" aria-label="Dữ liệu có thể đã cũ" title="Dữ liệu có thể đã cũ">
                {' '}⚠
              </span>
            )}
          </td>
        </tr>
      ))}
    </>
  );
}

function EntryTable({ entries }: { entries: SourceEntry[] }) {
  return (
    <table className="dz-table dz-srcdlg__table">
      <thead>
        <tr>
          <th scope="col">Thông tin</th>
          <th scope="col">Giá trị</th>
        </tr>
      </thead>
      <tbody>
        <EntryRows entries={entries} />
      </tbody>
    </table>
  );
}

export function AnswerSourcesDialog({
  data,
  onClose,
  returnFocus,
  page = false,
}: {
  data: AnswerSources;
  onClose(): void;
  returnFocus?: HTMLElement | null;
  /** Thêm lớp .dz-root--page (tông giao diện mở rộng) khi hộp thoại render ở gốc, ngoài lớp phủ. */
  page?: boolean;
}) {
  const titleId = useId();
  const boxRef = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  useModalLifecycle(closeRef, returnFocus);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') {
      // Chặn để khung chat / giao diện mở rộng không đóng theo.
      e.preventDefault();
      e.stopPropagation();
      onClose();
    } else if (e.key === 'Tab') {
      trapTab(e, boxRef.current);
    }
  };
  const onBackdrop = (e: MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) onClose();
  };

  return (
    <div className={page ? 'dz-srcdlg dz-root--page' : 'dz-srcdlg'} onClick={onBackdrop} onKeyDown={onKeyDown}>
      {/* tabIndex=-1: bấm vào chữ trong hộp thoại thì focus ở lại hộp thoại (Esc / bẫy Tab vẫn chạy), như Lightbox. */}
      <div
        ref={boxRef}
        className="dz-srcdlg__box"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <header className="dz-srcdlg__head">
          <h3 id={titleId} className="dz-srcdlg__title">
            {ANSWER_SOURCES_TITLE}
          </h3>
          <button ref={closeRef} type="button" className="dz-srcdlg__close" onClick={onClose} aria-label="Đóng" title="Đóng">
            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
              <path
                fill="currentColor"
                d="M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"
              />
            </svg>
          </button>
        </header>
        <div className="dz-srcdlg__body">
          <p className="dz-srcdlg__intro">{ANSWER_SOURCES_INTRO}</p>
          {data.groups.map((g) => (
            <section key={g.key} className="dz-srcdlg__group">
              {/* Trạng thái + ý nghĩa + link nguồn (tab mới, rel=noopener) + ngày cập nhật + cảnh báo dữ liệu cũ. */}
              <SourceDetails info={{ status: g.status, source: g.source, isStale: g.isStale }} />
              <EntryTable entries={g.entries} />
            </section>
          ))}
          {data.missing.length > 0 && (
            <details className="dz-srcdlg__missing">
              <summary>
                {NO_DATA} ({data.missing.length})
              </summary>
              <EntryTable entries={data.missing} />
            </details>
          )}
        </div>
      </div>
    </div>
  );
}
