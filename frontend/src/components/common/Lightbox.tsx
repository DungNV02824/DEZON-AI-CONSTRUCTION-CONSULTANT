/**
 * Xem ảnh toàn màn hình (lightbox / slide ảnh): ảnh lớn ở giữa, nút trước/sau, bộ đếm "i / n",
 * chú thích ("Ảnh k/n" trong công trình ·) tên công trình (· nhà thầu) + link sang trang công trình trên dezon.vn,
 * nút Đóng, lớp "Đang tải ảnh…" khi ảnh gốc chưa về.
 * Bàn phím ←/→/Home/End/Esc, vuốt trái/phải trên di động, bấm nền để đóng; focus trả về ô ảnh đã bấm.
 *
 * Cách dùng:
 *   const gallery = useLightbox();
 *   gallery.open({ images, index, contractorName }, e.currentTarget);
 *   ...render {gallery.element}
 * Trong App có <LightboxProvider> -> lightbox render ở cấp .dz-root (phủ cả widget nổi, giao diện mở rộng
 * và trang AI for Planning). Không có provider (test, block độc lập) -> `element` render tại chỗ.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
  type TouchEvent,
} from 'react';
import type { ImageRef } from '../../api/types';
import { orderByProject, previewIndexes, projectPositions } from '../../utils/gallery';
import { SafeImage } from './SafeImage';

/** Ngưỡng vuốt ngang (px) để chuyển ảnh. */
export const SWIPE_THRESHOLD = 40;

export interface LightboxRequest {
  images: ImageRef[];
  /** Ảnh mở đầu tiên (0-based). */
  index: number;
  /** Tên nhà thầu hiển thị sau tên công trình trong chú thích. */
  contractorName?: string | null;
  /** Trang dự phòng khi ảnh thiếu project_url (hồ sơ nhà thầu trên dezon.vn, `profile_url`). */
  fallbackUrl?: string | null;
}

interface OpenState extends LightboxRequest {
  returnFocus: HTMLElement | null;
}

type OpenFn = (req: LightboxRequest, trigger?: HTMLElement | null) => void;

const LightboxContext = createContext<OpenFn | null>(null);

/** Tên công trình của ảnh: project_name, nếu không có thì chú thích ảnh. */
export function imageProjectName(img: ImageRef | undefined): string | null {
  if (!img) return null;
  const name = (img.project_name ?? '').trim();
  if (name) return name;
  const cap = (img.caption ?? '').trim();
  return cap || null;
}

/** Link ở chú thích: trang công trình -> hồ sơ nhà thầu -> ảnh gốc; nhãn mô tả đúng đích đến. */
export function imageLink(
  img: ImageRef,
  projectName: string | null,
  contractorName?: string | null,
  fallbackUrl?: string | null,
): { href: string; label: string; title: string } {
  const projectUrl = (img.project_url ?? '').trim();
  if (projectUrl) {
    const name = projectName ?? contractorName ?? '';
    return {
      href: projectUrl,
      label: `Mở trang công trình ${name} trên Dezon (tab mới)`.replace(/\s+/g, ' '),
      title: 'Xem công trình trên Dezon',
    };
  }
  const profile = (fallbackUrl ?? '').trim();
  if (profile) {
    return {
      href: profile,
      label: `Mở hồ sơ nhà thầu ${contractorName ?? ''} trên Dezon (tab mới)`.replace(/\s+/g, ' '),
      title: 'Xem hồ sơ nhà thầu trên Dezon',
    };
  }
  return { href: img.url, label: 'Mở ảnh gốc (tab mới)', title: 'Mở ảnh gốc' };
}

/** Trang đang bị phóng to bằng hai ngón (pinch-zoom) -> kéo ngang là để xem phần ảnh, không phải chuyển ảnh. */
function pageZoomed(): boolean {
  const vv = typeof window !== 'undefined' ? window.visualViewport : null;
  return !!vv && vv.scale > 1.01;
}

function wrap(i: number, n: number): number {
  return ((i % n) + n) % n;
}

// `details > summary`: mục thu gọn (vd "Chưa có dữ liệu (k)" của hộp thoại Nguồn dữ liệu) phải tới được bằng Tab.
const FOCUSABLE = 'button:not([disabled]), a[href], details > summary, [tabindex]:not([tabindex="-1"])';

/** Giữ focus trong hộp thoại aria-modal khi bấm Tab / Shift+Tab (dùng chung cho Lightbox và ProjectGallery). */
export function trapTab(e: KeyboardEvent<HTMLElement>, container: HTMLElement | null): void {
  // Phần tử nằm trong <details> đang thu gọn (trừ chính <summary> của nó) không nhận focus -> bỏ khỏi vòng Tab.
  const collapsed = (el: HTMLElement) => {
    const d = el.closest('details:not([open])');
    return !!d && !(el.tagName === 'SUMMARY' && el.parentElement === d);
  };
  const items = Array.from(container?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []).filter((el) => !collapsed(el));
  if (items.length === 0) return;
  const root = container?.getRootNode() as Document | ShadowRoot | undefined;
  const active = (root?.activeElement ?? document.activeElement) as HTMLElement | null;
  const first = items[0]!;
  const last = items[items.length - 1]!;
  if (e.shiftKey && (active === first || !items.includes(active as HTMLElement))) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && (active === last || !items.includes(active as HTMLElement))) {
    e.preventDefault();
    first.focus();
  }
}

/**
 * Vòng đời hộp thoại toàn màn hình: focus vào `initialFocus` khi mở, khóa cuộn trang chủ; khi đóng khôi phục cuộn
 * và trả focus về `returnFocus`. Lồng nhau (ProjectGallery -> Lightbox) vẫn đúng vì lớp trên đóng trước.
 */
export function useModalLifecycle(
  initialFocus: { current: HTMLElement | null },
  returnFocus: HTMLElement | null | undefined,
): void {
  useEffect(() => {
    initialFocus.current?.focus({ preventScroll: true });
    const html = document.documentElement;
    const prevOverflow = html.style.overflow;
    html.style.overflow = 'hidden';
    return () => {
      html.style.overflow = prevOverflow;
      if (returnFocus && returnFocus.isConnected) returnFocus.focus({ preventScroll: true });
    };
  }, [initialFocus, returnFocus]);
}

export interface LightboxProps extends LightboxRequest {
  onClose(): void;
  /** Phần tử nhận lại focus khi đóng (ô ảnh đã bấm). */
  returnFocus?: HTMLElement | null;
}

export function Lightbox({ images, index: startIndex, contractorName, fallbackUrl, onClose, returnFocus }: LightboxProps) {
  const n = images.length;
  const [index, setIndex] = useState(() => wrap(startIndex, Math.max(n, 1)));
  // Ảnh đang xem đã tải xong (hoặc lỗi) -> ẩn "Đang tải ảnh…"; khóa theo vị trí + URL nên chuyển ảnh là hiện lại.
  const [settled, setSettled] = useState<string | null>(null);
  const positions = useMemo(() => projectPositions(images), [images]);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const touchRef = useRef<{ x: number; y: number } | null>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  const go = useCallback((delta: number) => setIndex((i) => wrap(i + delta, Math.max(n, 1))), [n]);

  // Focus vào hộp thoại khi mở; trả focus về ô ảnh khi đóng. Khóa cuộn trang chủ trong lúc xem ảnh.
  useModalLifecycle(closeRef, returnFocus);

  // Tải trước ảnh kề bên để chuyển ảnh mượt.
  useEffect(() => {
    if (n < 2 || typeof Image === 'undefined') return;
    for (const j of [wrap(index + 1, n), wrap(index - 1, n)]) {
      const url = images[j]?.url;
      if (url) {
        const pre = new Image();
        pre.decoding = 'async';
        pre.src = url;
      }
    }
  }, [index, images, n]);

  if (n === 0) return null;
  const img = images[index]!;
  const projectName = imageProjectName(img);
  const link = imageLink(img, projectName, contractorName, fallbackUrl);
  const alt = img.caption?.trim() || `Ảnh ${index + 1}/${n}${projectName ? ` — ${projectName}` : ''}`;
  const pos = positions[index] ?? null;
  const imgKey = `${index}-${img.url}`;
  const loading = settled !== imgKey;

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      onCloseRef.current();
    } else if (e.key === 'ArrowRight' && n > 1) {
      e.preventDefault();
      e.stopPropagation();
      go(1);
    } else if (e.key === 'ArrowLeft' && n > 1) {
      e.preventDefault();
      e.stopPropagation();
      go(-1);
    } else if ((e.key === 'Home' || e.key === 'End') && n > 1) {
      e.preventDefault();
      e.stopPropagation();
      setIndex(e.key === 'Home' ? 0 : n - 1);
    } else if (e.key === 'Tab') {
      trapTab(e, dialogRef.current);
    }
  };

  // Bấm vào nền (ngoài ảnh, nút, chú thích) -> đóng.
  const onClick = (e: MouseEvent<HTMLDivElement>) => {
    const t = e.target as HTMLElement;
    if (t.dataset && t.dataset.lbBackdrop !== undefined) onCloseRef.current();
  };

  const onTouchStart = (e: TouchEvent<HTMLDivElement>) => {
    const t = e.touches[0];
    touchRef.current = t && e.touches.length === 1 ? { x: t.clientX, y: t.clientY } : null;
  };
  const onTouchEnd = (e: TouchEvent<HTMLDivElement>) => {
    const start = touchRef.current;
    touchRef.current = null;
    const t = e.changedTouches[0];
    if (!start || !t || n < 2 || pageZoomed()) return;
    const dx = t.clientX - start.x;
    const dy = t.clientY - start.y;
    if (Math.abs(dx) >= SWIPE_THRESHOLD && Math.abs(dx) > Math.abs(dy)) go(dx < 0 ? 1 : -1);
  };

  return (
    <div
      ref={dialogRef}
      className="dz-lightbox"
      role="dialog"
      aria-modal="true"
      aria-label={projectName ? `Xem ảnh: ${projectName}` : 'Xem ảnh công trình'}
      tabIndex={-1}
      data-lb-backdrop=""
      onKeyDown={onKeyDown}
      onClick={onClick}
      onTouchStart={onTouchStart}
      onTouchEnd={onTouchEnd}
    >
      <div className="dz-lightbox__bar" data-lb-backdrop="">
        {n > 1 && (
          <span className="dz-lightbox__counter" aria-live="polite">
            {index + 1} / {n}
          </span>
        )}
        <button ref={closeRef} type="button" className="dz-lightbox__close" aria-label="Đóng" title="Đóng (Esc)" onClick={onClose}>
          <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
            <path fill="currentColor" d="M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
          </svg>
        </button>
      </div>

      <figure className="dz-lightbox__figure" data-lb-backdrop="">
        <div className="dz-lightbox__stage" data-lb-backdrop="">
          <SafeImage
            key={imgKey}
            className="dz-lightbox__img"
            src={img.url}
            alt={alt}
            loading="eager"
            onLoad={() => setSettled(imgKey)}
            onFail={() => setSettled(imgKey)}
          />
          {loading && (
            <span className="dz-lightbox__loading" role="status">
              <span className="dz-lightbox__spinner" aria-hidden="true" />
              Đang tải ảnh…
            </span>
          )}
        </div>
        {(projectName || contractorName) && (
          <figcaption className="dz-lightbox__caption">
            {pos && (
              <>
                <span className="dz-lightbox__pos">
                  Ảnh {pos.k}/{pos.n}
                </span>
                {projectName && <span aria-hidden="true"> · </span>}
              </>
            )}
            {projectName && <span className="dz-lightbox__project">{projectName}</span>}
            {projectName && contractorName && <span aria-hidden="true"> · </span>}
            {contractorName && <span className="dz-lightbox__by">{contractorName}</span>}
            <a
              className="dz-lightbox__ext"
              href={link.href}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={link.label}
              title={link.title}
            >
              <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
                <path
                  fill="currentColor"
                  d="M14 3v2h3.59l-9.83 9.83 1.41 1.41L19 6.41V10h2V3zM19 19H5V5h7V3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7h-2z"
                />
              </svg>
            </a>
          </figcaption>
        )}
      </figure>

      {n > 1 && (
        <>
          <button type="button" className="dz-lightbox__nav dz-lightbox__nav--prev" aria-label="Ảnh trước" onClick={() => go(-1)}>
            <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
              <path fill="currentColor" d="M15.41 7.41 14 6l-6 6 6 6 1.41-1.41L10.83 12z" />
            </svg>
          </button>
          <button type="button" className="dz-lightbox__nav dz-lightbox__nav--next" aria-label="Ảnh tiếp theo" onClick={() => go(1)}>
            <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
              <path fill="currentColor" d="M10 6 8.59 7.41 13.17 12l-4.58 4.59L10 18l6-6z" />
            </svg>
          </button>
        </>
      )}
    </div>
  );
}

/** Đặt trong .dz-root của App: lightbox render ở cấp gốc, phủ trên mọi lớp (kể cả .dz-overlay). */
export function LightboxProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<OpenState | null>(null);
  const open = useCallback<OpenFn>((req, trigger) => {
    if (!req.images.length) return;
    setState({ ...req, returnFocus: trigger ?? null });
  }, []);
  const close = useCallback(() => setState(null), []);
  return (
    <LightboxContext.Provider value={open}>
      {children}
      {state && <Lightbox {...state} onClose={close} />}
    </LightboxContext.Provider>
  );
}

/**
 * Mở lightbox từ một ô ảnh. Có LightboxProvider -> dùng lightbox chung; không có -> `element`
 * chứa lightbox cục bộ (component gọi hook phải render `element`).
 */
export function useLightbox(): { open: OpenFn; element: ReactNode } {
  const shared = useContext(LightboxContext);
  const [local, setLocal] = useState<OpenState | null>(null);
  const openLocal = useCallback<OpenFn>((req, trigger) => {
    if (!req.images.length) return;
    setLocal({ ...req, returnFocus: trigger ?? null });
  }, []);
  const closeLocal = useCallback(() => setLocal(null), []);
  return useMemo(
    () => ({
      open: shared ?? openLocal,
      element: !shared && local ? <Lightbox {...local} onClose={closeLocal} /> : null,
    }),
    [shared, openLocal, local, closeLocal],
  );
}

export interface GalleryTilesProps {
  images: ImageRef[];
  /** Số ô hiển thị tối đa; ô cuối có lớp "+N" khi còn ảnh. */
  max?: number;
  contractorName?: string | null;
  /** Link dự phòng của chú thích khi ảnh thiếu project_url (hồ sơ nhà thầu). */
  fallbackUrl?: string | null;
  /** Lớp của khung lưới (vd "dz-ccard__covers"); thêm hậu tố `--<số ô>`. */
  className: string;
  /** Lớp thêm cho từng ô. */
  tileClassName?: string;
  /** Alt dự phòng của ảnh thứ i khi thiếu chú thích. */
  fallbackAlt?: (i: number) => string;
  /**
   * "given" (mặc định): ô và slideshow theo đúng thứ tự `images`.
   * "by_project": slideshow chạy hết ảnh công trình này rồi sang công trình sau; các ô xem trước lấy ảnh đầu
   * của các công trình khác nhau (thẻ nhà thầu, lưới chính của hồ sơ).
   */
  order?: 'given' | 'by_project';
}

/** Lưới ô ảnh bấm được -> mở lightbox trên TOÀN BỘ `images` tại đúng vị trí. Ô dùng ảnh thu nhỏ (thumb_url). */
export function GalleryTiles({
  images: input,
  max = 3,
  contractorName,
  fallbackUrl,
  className,
  tileClassName,
  fallbackAlt,
  order = 'given',
}: GalleryTilesProps) {
  const gallery = useLightbox();
  const images = useMemo(() => (order === 'by_project' ? orderByProject(input) : input), [input, order]);
  const shownIdx = useMemo(
    () =>
      order === 'by_project'
        ? previewIndexes(images, max)
        : Array.from({ length: Math.max(0, Math.min(max, images.length)) }, (_, i) => i),
    [images, max, order],
  );
  const n = images.length;
  if (n === 0) return null;
  const more = n - shownIdx.length;
  return (
    <>
      <div className={`${className} ${className}--${Number.isFinite(max) ? shownIdx.length : 'all'}`}>
        {shownIdx.map((idx, i) => {
          const img = images[idx]!;
          const name = imageProjectName(img) ?? contractorName ?? '';
          const isLast = i === shownIdx.length - 1;
          return (
            <button
              key={`${img.url}-${idx}`}
              type="button"
              className={`dz-gtile${tileClassName ? ` ${tileClassName}` : ''}`}
              aria-label={`Xem ảnh ${idx + 1}/${n}${name ? ` — ${name}` : ''}`}
              onClick={(e) => gallery.open({ images, index: idx, contractorName, fallbackUrl }, e.currentTarget)}
            >
              <SafeImage
                src={img.thumb_url || img.url}
                fallbackSrc={img.url}
                alt={img.caption ?? fallbackAlt?.(idx) ?? `Ảnh ${idx + 1}`}
              />
              {isLast && more > 0 && (
                <span className="dz-gtile__more" aria-hidden="true">
                  +{more}
                </span>
              )}
            </button>
          );
        })}
      </div>
      {gallery.element}
    </>
  );
}
