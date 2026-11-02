/**
 * Tooltip nguồn dữ liệu dùng chung (một popover duy nhất, định vị `position: fixed`) để không bị cắt
 * trong vùng cuộn ngang của bảng. Hover/focus hiện tạm; bấm (tap trên mobile) để ghim; Esc để đóng.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import type { SourceRef, VerificationStatus } from '../../api/types';
import { formatDateVi } from '../../utils/format';
import { STATUS_LABEL_VI, STATUS_MEANING_VI } from '../../utils/labels';

export interface SourceInfo {
  status: VerificationStatus;
  source?: SourceRef | null;
  isStale?: boolean;
  title?: string;
}

interface PopoverState {
  anchor: HTMLElement;
  info: SourceInfo;
  pinned: boolean;
}

interface PopoverApi {
  show(anchor: HTMLElement, info: SourceInfo, pinned: boolean): void;
  scheduleHide(): void;
  cancelHide(): void;
  hide(): void;
  isPinnedFor(anchor: HTMLElement): boolean;
}

const PopoverContext = createContext<PopoverApi | null>(null);

export function staleMessage(updatedAt?: string | null): string {
  const d = formatDateVi(updatedAt);
  return d ? `Dữ liệu cập nhật ngày ${d}, có thể đã cũ` : 'Dữ liệu có thể đã cũ';
}

export function SourceDetails({ info }: { info: SourceInfo }) {
  const { status, source, isStale } = info;
  const date = formatDateVi(source?.updated_at);
  return (
    <div className="dz-src">
      {info.title && <div className="dz-src__title">{info.title}</div>}
      <div className={`dz-src__status dz-status-text--${status}`}>
        <span className={`dz-dot dz-dot--${status}`} aria-hidden="true" />
        {STATUS_LABEL_VI[status]}
      </div>
      <p className="dz-src__meaning">{STATUS_MEANING_VI[status]}</p>
      {source ? (
        <dl className="dz-src__meta">
          <dt>Nguồn</dt>
          <dd>
            {source.url ? (
              <a href={source.url} target="_blank" rel="noopener noreferrer">
                {source.label} ↗
              </a>
            ) : (
              source.label
            )}
          </dd>
          {date && (
            <>
              <dt>Cập nhật</dt>
              <dd>{date}</dd>
            </>
          )}
        </dl>
      ) : (
        status !== 'unavailable' && <p className="dz-src__meaning">Chưa ghi nhận nguồn chi tiết.</p>
      )}
      {isStale && (
        <p className="dz-src__stale" role="note">
          ⚠ {staleMessage(source?.updated_at)}
        </p>
      )}
    </div>
  );
}

export function SourcePopoverProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<PopoverState | null>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const popRef = useRef<HTMLDivElement | null>(null);
  const stateRef = useRef<PopoverState | null>(null);
  const posRef = useRef<{ top: number; left: number } | null>(null);

  const cancelHide = useCallback(() => {
    if (hideTimer.current) clearTimeout(hideTimer.current);
    hideTimer.current = null;
  }, []);
  const hide = useCallback(() => {
    cancelHide();
    stateRef.current = null;
    setState(null);
  }, [cancelHide]);
  const scheduleHide = useCallback(() => {
    cancelHide();
    hideTimer.current = setTimeout(() => {
      if (!stateRef.current?.pinned) {
        stateRef.current = null;
        setState(null);
      }
    }, 180);
  }, [cancelHide]);
  // Lưu ý: tính state kế tiếp từ ref rồi setState(giá trị) — KHÔNG dùng updater tạo object mới, vì React có
  // thể áp lại updater khi rebase các lane cập nhật (mouseenter/focus khác độ ưu tiên), sinh object mới mỗi lần
  // render -> layout effect chạy lại -> vòng lặp vô hạn (lỗi React #185).
  const show = useCallback(
    (anchor: HTMLElement, info: SourceInfo, pinned: boolean) => {
      cancelHide();
      const prev = stateRef.current;
      // Đang ghim popover khác: hover/focus phần tử khác không thay thế.
      if (prev && prev.pinned && !pinned && prev.anchor !== anchor) return;
      if (prev && prev.anchor === anchor && prev.pinned === pinned && prev.info === info) return;
      const next = { anchor, info, pinned };
      stateRef.current = next;
      setState(next);
    },
    [cancelHide],
  );
  const isPinnedFor = useCallback((anchor: HTMLElement) => Boolean(stateRef.current?.pinned && stateRef.current.anchor === anchor), []);

  useLayoutEffect(() => {
    if (!state) {
      posRef.current = null;
      setPos(null);
      return;
    }
    const place = () => {
      const r = state.anchor.getBoundingClientRect();
      const pop = popRef.current;
      const w = pop?.offsetWidth ?? 260;
      const h = pop?.offsetHeight ?? 120;
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      let left = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), Math.max(8, vw - w - 8));
      let top = r.bottom + 6;
      if (top + h > vh - 8 && r.top - h - 6 > 8) top = r.top - h - 6;
      if (!Number.isFinite(left)) left = 8;
      const cur = posRef.current;
      if (cur && Math.abs(cur.top - top) < 0.5 && Math.abs(cur.left - left) < 0.5) return;
      posRef.current = { top, left };
      setPos(posRef.current);
    };
    place();
    // Cuộn/đổi kích thước: popover tạm thời đóng lại, popover ghim thì bám theo.
    const onMove = () => {
      if (stateRef.current?.pinned) place();
      else hide();
    };
    window.addEventListener('resize', onMove);
    document.addEventListener('scroll', onMove, true);
    const root = state.anchor.getRootNode();
    root.addEventListener('scroll', onMove, true);
    return () => {
      window.removeEventListener('resize', onMove);
      document.removeEventListener('scroll', onMove, true);
      root.removeEventListener('scroll', onMove, true);
    };
  }, [state, hide]);

  useEffect(() => {
    if (!state?.pinned) return undefined;
    const onKey = (e: Event) => {
      if ((e as KeyboardEvent).key === 'Escape') {
        state.anchor.focus();
        hide();
      }
    };
    const onDown = (e: Event) => {
      const path = e.composedPath();
      if (popRef.current && path.includes(popRef.current)) return;
      if (path.includes(state.anchor)) return;
      hide();
    };
    const root = state.anchor.getRootNode();
    root.addEventListener('keydown', onKey);
    root.addEventListener('pointerdown', onDown, true);
    return () => {
      root.removeEventListener('keydown', onKey);
      root.removeEventListener('pointerdown', onDown, true);
    };
  }, [state, hide]);

  useEffect(() => () => cancelHide(), [cancelHide]);

  const api = useMemo<PopoverApi>(() => ({ show, scheduleHide, cancelHide, hide, isPinnedFor }), [show, scheduleHide, cancelHide, hide, isPinnedFor]);

  return (
    <PopoverContext.Provider value={api}>
      {children}
      {state && (
        <div
          ref={popRef}
          className="dz-popover"
          role="tooltip"
          style={pos ? { top: pos.top, left: pos.left } : { visibility: 'hidden', top: 0, left: 0 }}
          onMouseEnter={cancelHide}
          onMouseLeave={scheduleHide}
        >
          {state.pinned && (
            <button type="button" className="dz-popover__close" aria-label="Đóng thông tin nguồn" onClick={hide}>
              ×
            </button>
          )}
          <SourceDetails info={state.info} />
        </div>
      )}
    </PopoverContext.Provider>
  );
}

/** Thuộc tính gắn vào phần tử kích hoạt (badge/chip) để mở tooltip nguồn. */
export function useSourceTrigger(info: SourceInfo) {
  const api = useContext(PopoverContext);
  return useMemo(() => {
    if (!api) return {};
    return {
      onMouseEnter: (e: { currentTarget: HTMLElement }) => api.show(e.currentTarget, info, false),
      onMouseLeave: () => api.scheduleHide(),
      onFocus: (e: { currentTarget: HTMLElement }) => api.show(e.currentTarget, info, false),
      onBlur: () => api.scheduleHide(),
      onClick: (e: { currentTarget: HTMLElement; stopPropagation(): void }) => {
        e.stopPropagation();
        if (api.isPinnedFor(e.currentTarget)) api.hide();
        else api.show(e.currentTarget, info, true);
      },
    };
  }, [api, info]);
}
