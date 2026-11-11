/**
 * "Xem tất cả ảnh": hộp thoại toàn màn hình liệt kê MỌI ảnh của nhà thầu, nhóm theo công trình (tên + link trang
 * công trình trên dezon.vn + dòng meta, lưới đủ ảnh thu nhỏ). Bấm ô -> lightbox chạy ảnh của công trình đó.
 * Cùng khung với Lightbox: role=dialog + aria-modal, focus trap, Esc đóng, khóa cuộn, trả focus về nút mở.
 *
 * Cách dùng (giống useLightbox):
 *   const all = useProjectGallery();
 *   all.open({ groups, contractorName, fallbackUrl }, e.currentTarget);
 *   ...render {all.element}
 * Trong App có <ProjectGalleryProvider> (đặt BÊN TRONG LightboxProvider) -> hộp thoại render ở cấp .dz-root, lightbox
 * mở từ hộp thoại nằm sau nó trong DOM nên phủ lên trên. Không có provider -> `element` render tại chỗ.
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
  type ReactNode,
} from 'react';
import type { ImageRef } from '../../api/types';
import { groupImagesByProject } from '../../utils/gallery';
import { ExternalLink } from './ExternalLink';
import { GalleryTiles, trapTab, useModalLifecycle } from './Lightbox';

export interface GalleryGroup {
  /** Khóa ổn định (slug công trình). */
  key: string;
  /** Tên công trình; null -> "Ảnh khác". */
  name: string | null;
  /** Trang công trình trên dezon.vn. */
  url?: string | null;
  /** Dòng phụ (khu vực · loại công trình · đơn vị thi công …). */
  meta?: string | null;
  images: ImageRef[];
  /** Số ảnh thực có của công trình (ProjectSummary.total_images); lớn hơn images.length -> link xem thêm. */
  total?: number | null;
}

export interface ProjectGalleryRequest {
  groups: GalleryGroup[];
  contractorName?: string | null;
  /** Link dự phòng của chú thích lightbox khi ảnh thiếu project_url (hồ sơ nhà thầu). */
  fallbackUrl?: string | null;
}

/** Nhóm ảnh phía client theo project_slug (thẻ nhà thầu chỉ có cover_images, không có danh sách công trình). */
export function groupsFromImages(images: ImageRef[]): GalleryGroup[] {
  return groupImagesByProject(images).map((g, i) => ({
    key: g.key ?? `other-${i}`,
    name: g.name,
    url: g.url,
    images: g.images,
  }));
}

/** Tổng số ảnh có thể xem trong hộp thoại. */
export function galleryImageCount(groups: GalleryGroup[]): number {
  return groups.reduce((s, g) => s + g.images.length, 0);
}

interface OpenState extends ProjectGalleryRequest {
  returnFocus: HTMLElement | null;
}

type OpenFn = (req: ProjectGalleryRequest, trigger?: HTMLElement | null) => void;

const ProjectGalleryContext = createContext<OpenFn | null>(null);

export interface ProjectGalleryProps extends ProjectGalleryRequest {
  onClose(): void;
  returnFocus?: HTMLElement | null;
}

export function ProjectGallery({ groups: allGroups, contractorName, fallbackUrl, onClose, returnFocus }: ProjectGalleryProps) {
  const groups = useMemo(() => allGroups.filter((g) => g.images.length > 0), [allGroups]);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const titleId = useId();
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useModalLifecycle(closeRef, returnFocus);

  const total = galleryImageCount(groups);
  const named = groups.filter((g) => g.name).length;

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      onCloseRef.current();
    } else if (e.key === 'Tab') {
      trapTab(e, dialogRef.current);
    }
  };

  return (
    <div
      ref={dialogRef}
      className="dz-pgall"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      tabIndex={-1}
      onKeyDown={onKeyDown}
    >
      <div className="dz-pgall__bar">
        <div className="dz-pgall__titles">
          <h2 id={titleId} className="dz-pgall__title">
            {contractorName ? `Ảnh công trình của ${contractorName}` : 'Ảnh công trình'}
          </h2>
          <p className="dz-pgall__count">
            {total} ảnh
            {/* Số nhóm ảnh trong payload, KHÔNG phải dezon_project_count -> không dùng nhãn "công trình ghi nhận trên Dezon". */}
            {named > 0 ? ` từ ${named} công trình có ảnh` : ''}
          </p>
        </div>
        <button ref={closeRef} type="button" className="dz-pgall__close" aria-label="Đóng" title="Đóng (Esc)" onClick={onClose}>
          <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
            <path fill="currentColor" d="M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
          </svg>
        </button>
      </div>

      <div className="dz-pgall__body">
        {groups.map((g) => {
          const name = g.name ?? 'Ảnh khác';
          const extra = (g.total ?? 0) - g.images.length;
          return (
            <section key={g.key} className="dz-pgall__group" aria-label={`Ảnh công trình ${name}`}>
              <h3 className="dz-pgall__name">
                {g.url ? (
                  <ExternalLink href={g.url} className="dz-extlink">
                    {name} ↗
                  </ExternalLink>
                ) : (
                  name
                )}
                <span className="dz-pgall__n"> · {g.images.length} ảnh</span>
              </h3>
              {g.meta && <p className="dz-muted dz-small dz-pgall__meta">{g.meta}</p>}
              <GalleryTiles
                images={g.images}
                max={Infinity}
                contractorName={contractorName}
                fallbackUrl={fallbackUrl}
                className="dz-pgall__grid"
                fallbackAlt={(i) => `Ảnh ${i + 1} công trình ${name}`}
              />
              {extra > 0 && g.url && (
                <ExternalLink href={g.url} className="dz-extlink dz-small">
                  Xem thêm {extra} ảnh trên trang công trình ↗
                </ExternalLink>
              )}
            </section>
          );
        })}
      </div>
    </div>
  );
}

/** Đặt trong LightboxProvider của App: hộp thoại "Xem tất cả ảnh" render ở cấp gốc, dưới lớp lightbox. */
export function ProjectGalleryProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<OpenState | null>(null);
  const open = useCallback<OpenFn>((req, trigger) => {
    if (!galleryImageCount(req.groups)) return;
    setState({ ...req, returnFocus: trigger ?? null });
  }, []);
  const close = useCallback(() => setState(null), []);
  return (
    <ProjectGalleryContext.Provider value={open}>
      {children}
      {state && <ProjectGallery {...state} onClose={close} />}
    </ProjectGalleryContext.Provider>
  );
}

/** Mở hộp thoại "Xem tất cả ảnh". Không có provider -> component gọi hook phải render `element`. */
export function useProjectGallery(): { open: OpenFn; element: ReactNode } {
  const shared = useContext(ProjectGalleryContext);
  const [local, setLocal] = useState<OpenState | null>(null);
  const openLocal = useCallback<OpenFn>((req, trigger) => {
    if (!galleryImageCount(req.groups)) return;
    setLocal({ ...req, returnFocus: trigger ?? null });
  }, []);
  const closeLocal = useCallback(() => setLocal(null), []);
  return useMemo(
    () => ({
      open: shared ?? openLocal,
      element: !shared && local ? <ProjectGallery {...local} onClose={closeLocal} /> : null,
    }),
    [shared, openLocal, local, closeLocal],
  );
}

/** Nút "Xem tất cả N ảnh" mở hộp thoại (dùng ở thẻ nhà thầu và hồ sơ mở rộng). */
export function ShowAllPhotosButton({
  request,
  className = 'dz-linkbtn',
  label,
}: {
  request: ProjectGalleryRequest;
  className?: string;
  label?: string;
}) {
  const all = useProjectGallery();
  const count = galleryImageCount(request.groups);
  if (count === 0) return null;
  return (
    <>
      <button
        type="button"
        className={className}
        aria-haspopup="dialog"
        onClick={(e) => all.open(request, e.currentTarget)}
      >
        {label ?? `Xem tất cả ${count} ảnh`}
      </button>
      {all.element}
    </>
  );
}
