import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MATCH_FIXTURE, PROFILES } from '../../api/fixtures';
import type { ImageRef } from '../../api/types';
import { renderWithWidget } from '../../test/utils';
import { BlockRenderer } from '../blocks/BlockRenderer';
import { ContractorCardView } from '../blocks/ContractorCard';
import { GalleryTiles, LightboxProvider } from './Lightbox';

const IMAGES: ImageRef[] = Array.from({ length: 5 }, (_, i) => ({
  url: `https://dezon.vn/wp-content/uploads/dz-data/nt/demo/o/img${i + 1}.jpg`,
  caption: `Công trình ${i < 3 ? 'A' : 'B'} — ảnh ${i + 1}`,
  project_name: i < 3 ? 'Công trình A' : 'Công trình B',
  project_url: i < 3 ? 'https://dezon.vn/nha-thau/demo/cong-trinh-a/' : null,
}));

function dialog() {
  return screen.getByRole('dialog', { name: /Xem ảnh/ });
}

function counter() {
  return dialog().querySelector('.dz-lightbox__counter')?.textContent?.replace(/\s+/g, ' ').trim();
}

function currentSrc() {
  return dialog().querySelector('img.dz-lightbox__img')?.getAttribute('src');
}

function renderTiles(images = IMAGES) {
  return render(<GalleryTiles images={images} max={3} contractorName="Kiến Phong" className="dz-ccard__covers" />);
}

describe('GalleryTiles + Lightbox', () => {
  it('mỗi ô là nút có nhãn "Xem ảnh i/n — <công trình>", ô cuối có lớp "+N"', () => {
    renderTiles();
    const tiles = screen.getAllByRole('button', { name: /^Xem ảnh/ });
    expect(tiles).toHaveLength(3);
    expect(tiles[0]).toHaveAccessibleName('Xem ảnh 1/5 — Công trình A');
    expect(tiles[2]).toHaveAccessibleName('Xem ảnh 3/5 — Công trình A');
    expect(within(tiles[2]!).getByText('+2')).toBeInTheDocument();
    expect(within(tiles[0]!).queryByText(/^\+/)).toBeNull();
  });

  it('không quá 3 ảnh -> không có lớp "+N"; 1 ảnh -> không có nút trước/sau, không bộ đếm', () => {
    renderTiles(IMAGES.slice(0, 1));
    expect(screen.queryByText(/^\+\d/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Xem ảnh 1/1 — Công trình A' }));
    expect(dialog()).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Ảnh trước' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Ảnh tiếp theo' })).toBeNull();
    expect(counter()).toBeUndefined();
  });

  it('bấm ô thứ 2 -> mở đúng ảnh 2/5; tiếp/trước quay vòng', () => {
    renderTiles();
    fireEvent.click(screen.getByRole('button', { name: 'Xem ảnh 2/5 — Công trình A' }));
    const dlg = dialog();
    expect(dlg).toHaveAttribute('aria-modal', 'true');
    expect(counter()).toBe('2 / 5');
    expect(currentSrc()).toBe(IMAGES[1]!.url);

    const next = screen.getByRole('button', { name: 'Ảnh tiếp theo' });
    const prev = screen.getByRole('button', { name: 'Ảnh trước' });
    fireEvent.click(next);
    fireEvent.click(next);
    fireEvent.click(next);
    expect(counter()).toBe('5 / 5');
    fireEvent.click(next);
    expect(counter()).toBe('1 / 5');
    expect(currentSrc()).toBe(IMAGES[0]!.url);
    fireEvent.click(prev);
    expect(counter()).toBe('5 / 5');
  });

  it('bàn phím ←/→ chuyển ảnh, Esc đóng và trả focus về ô đã bấm', () => {
    renderTiles();
    const tile = screen.getByRole('button', { name: 'Xem ảnh 3/5 — Công trình A' });
    tile.focus();
    fireEvent.click(tile);
    const dlg = dialog();
    // Focus chuyển vào hộp thoại (nút Đóng).
    expect(dlg.contains(document.activeElement)).toBe(true);
    expect(screen.getByRole('button', { name: 'Đóng' })).toHaveFocus();
    fireEvent.keyDown(dlg, { key: 'ArrowRight' });
    expect(counter()).toBe('4 / 5');
    fireEvent.keyDown(dlg, { key: 'ArrowLeft' });
    fireEvent.keyDown(dlg, { key: 'ArrowLeft' });
    expect(counter()).toBe('2 / 5');
    fireEvent.keyDown(dlg, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(tile).toHaveFocus();
  });

  it('nút Đóng (X) đóng lightbox', () => {
    renderTiles();
    fireEvent.click(screen.getByRole('button', { name: 'Xem ảnh 1/5 — Công trình A' }));
    fireEvent.click(screen.getByRole('button', { name: 'Đóng' }));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('bấm nền đóng; bấm vào ảnh hoặc chú thích không đóng', () => {
    renderTiles();
    fireEvent.click(screen.getByRole('button', { name: 'Xem ảnh 1/5 — Công trình A' }));
    const dlg = dialog();
    fireEvent.click(dlg.querySelector('img.dz-lightbox__img')!);
    fireEvent.click(dlg.querySelector('.dz-lightbox__caption')!);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    fireEvent.click(dlg.querySelector('.dz-lightbox__stage')!);
    expect(screen.queryByRole('dialog')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Xem ảnh 1/5 — Công trình A' }));
    fireEvent.click(dialog());
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('vuốt trái -> ảnh sau, vuốt phải -> ảnh trước; vuốt ngắn/dọc không đổi', () => {
    renderTiles();
    fireEvent.click(screen.getByRole('button', { name: 'Xem ảnh 1/5 — Công trình A' }));
    const dlg = dialog();
    const swipe = (fromX: number, toX: number, fromY = 300, toY = 300) => {
      fireEvent.touchStart(dlg, { touches: [{ clientX: fromX, clientY: fromY }] });
      fireEvent.touchEnd(dlg, { changedTouches: [{ clientX: toX, clientY: toY }] });
    };
    swipe(300, 200);
    expect(counter()).toBe('2 / 5');
    swipe(100, 220);
    expect(counter()).toBe('1 / 5');
    swipe(100, 220);
    expect(counter()).toBe('5 / 5');
    swipe(200, 180); // dưới ngưỡng 40px
    expect(counter()).toBe('5 / 5');
    swipe(200, 140, 100, 400); // vuốt dọc
    expect(counter()).toBe('5 / 5');
  });

  it('chú thích: tên công trình · nhà thầu + link mở trang công trình (tab mới, noopener); thiếu project_url, không có trang dự phòng -> "Mở ảnh gốc"', () => {
    renderTiles();
    fireEvent.click(screen.getByRole('button', { name: 'Xem ảnh 1/5 — Công trình A' }));
    const cap = dialog().querySelector('figcaption')!;
    expect(cap.textContent).toContain('Công trình A');
    expect(cap.textContent).toContain('Kiến Phong');
    const link = within(cap).getByRole('link', { name: /Mở trang công trình Công trình A trên Dezon/ });
    expect(link).toHaveAttribute('href', 'https://dezon.vn/nha-thau/demo/cong-trinh-a/');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link.getAttribute('rel')).toContain('noopener');

    fireEvent.keyDown(dialog(), { key: 'ArrowLeft' }); // ảnh 5 thuộc Công trình B, không có project_url
    const cap2 = dialog().querySelector('figcaption')!;
    expect(cap2.textContent).toContain('Công trình B');
    const raw = within(cap2).getByRole('link');
    expect(raw).toHaveAttribute('href', IMAGES[4]!.url);
    expect(raw).toHaveAccessibleName('Mở ảnh gốc (tab mới)');
    expect(raw).toHaveAttribute('title', 'Mở ảnh gốc');
  });

  it('thiếu project_url + có fallbackUrl -> link hồ sơ nhà thầu trên Dezon, nhãn đúng đích đến', () => {
    render(
      <GalleryTiles
        images={IMAGES}
        max={3}
        contractorName="Kiến Phong"
        fallbackUrl="https://dezon.vn/nha-thau/kien-phong"
        className="dz-ccard__covers"
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Xem ảnh 1/5 — Công trình A' }));
    expect(within(dialog().querySelector('figcaption')!).getByRole('link')).toHaveAttribute(
      'href',
      'https://dezon.vn/nha-thau/demo/cong-trinh-a/',
    );
    fireEvent.keyDown(dialog(), { key: 'ArrowLeft' }); // ảnh 5: không có project_url
    const link = within(dialog().querySelector('figcaption')!).getByRole('link');
    expect(link).toHaveAttribute('href', 'https://dezon.vn/nha-thau/kien-phong');
    expect(link).toHaveAccessibleName('Mở hồ sơ nhà thầu Kiến Phong trên Dezon (tab mới)');
    expect(link).toHaveAttribute('title', 'Xem hồ sơ nhà thầu trên Dezon');
  });

  it('trang đang pinch-zoom (visualViewport.scale > 1) -> kéo ngang không chuyển ảnh', () => {
    const desc = Object.getOwnPropertyDescriptor(window, 'visualViewport');
    const vv = { scale: 2 };
    Object.defineProperty(window, 'visualViewport', { configurable: true, value: vv });
    try {
      renderTiles();
      fireEvent.click(screen.getByRole('button', { name: 'Xem ảnh 1/5 — Công trình A' }));
      const dlg = dialog();
      const swipe = () => {
        fireEvent.touchStart(dlg, { touches: [{ clientX: 300, clientY: 300 }] });
        fireEvent.touchEnd(dlg, { changedTouches: [{ clientX: 150, clientY: 300 }] });
      };
      swipe();
      expect(counter()).toBe('1 / 5');
      vv.scale = 1;
      swipe();
      expect(counter()).toBe('2 / 5');
    } finally {
      if (desc) Object.defineProperty(window, 'visualViewport', desc);
      else delete (window as { visualViewport?: unknown }).visualViewport;
    }
  });

  it('ảnh lỗi -> ô "Không tải được ảnh" (SafeImage), chuyển ảnh thì thử tải lại', () => {
    renderTiles();
    fireEvent.click(screen.getByRole('button', { name: 'Xem ảnh 1/5 — Công trình A' }));
    fireEvent.error(dialog().querySelector('img.dz-lightbox__img')!);
    expect(within(dialog()).getByText('Không tải được ảnh')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Ảnh tiếp theo' }));
    expect(currentSrc()).toBe(IMAGES[1]!.url);
  });

  it('Tab giữ focus trong hộp thoại', () => {
    renderTiles();
    fireEvent.click(screen.getByRole('button', { name: 'Xem ảnh 1/5 — Công trình A' }));
    const next = screen.getByRole('button', { name: 'Ảnh tiếp theo' });
    next.focus();
    fireEvent.keyDown(dialog(), { key: 'Tab' });
    expect(screen.getByRole('button', { name: 'Đóng' })).toHaveFocus();
    fireEvent.keyDown(dialog(), { key: 'Tab', shiftKey: true });
    expect(next).toHaveFocus();
  });

  it('khóa cuộn trang chủ khi mở và khôi phục khi đóng', () => {
    document.documentElement.style.overflow = 'auto';
    renderTiles();
    fireEvent.click(screen.getByRole('button', { name: 'Xem ảnh 1/5 — Công trình A' }));
    expect(document.documentElement.style.overflow).toBe('hidden');
    fireEvent.keyDown(dialog(), { key: 'Escape' });
    expect(document.documentElement.style.overflow).toBe('auto');
    document.documentElement.style.overflow = '';
  });

  it('có LightboxProvider -> lightbox render ở cấp provider (ngoài thẻ), Esc không lan ra ngoài', () => {
    let outerEsc = 0;
    const { container } = render(
      <div onKeyDown={(e) => e.key === 'Escape' && !e.defaultPrevented && outerEsc++}>
        <LightboxProvider>
          <div className="card">
            <GalleryTiles images={IMAGES} contractorName="Kiến Phong" className="dz-ccard__covers" />
          </div>
        </LightboxProvider>
      </div>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Xem ảnh 2/5 — Công trình A' }));
    const dlg = dialog();
    expect(container.querySelector('.card')!.contains(dlg)).toBe(false);
    act(() => {
      fireEvent.keyDown(dlg, { key: 'Escape' });
    });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(outerEsc).toBe(0);
  });
});

describe('Lightbox trong thẻ nhà thầu / hồ sơ mở rộng', () => {
  it('ContractorCard: 3 ô + "+9" trên ô cuối, mở lightbox trên toàn bộ 12 ảnh tại đúng vị trí', () => {
    renderWithWidget(<ContractorCardView item={MATCH_FIXTURE.results[0]!} />);
    const tiles = screen.getAllByRole('button', { name: /^Xem ảnh \d+\/12/ });
    expect(tiles).toHaveLength(3);
    expect(within(tiles[2]!).getByText('+9')).toBeInTheDocument();
    // cover_images xoay vòng giữa các công trình như backend: 3 ô đầu là ảnh 1 của 3 công trình khác nhau.
    expect(tiles[0]).toHaveAccessibleName('Xem ảnh 1/12 — France Indochine 22 Hà Nội');
    expect(tiles[1]).toHaveAccessibleName('Xem ảnh 2/12 — Green Pool Villa Vĩnh Long');
    expect(tiles[2]).toHaveAccessibleName('Xem ảnh 3/12 — Indochine Villa Quận 7 Sài Gòn');
    fireEvent.click(tiles[2]!);
    expect(counter()).toBe('3 / 12');
    const cap = dialog().querySelector('figcaption')!;
    expect(cap.textContent).toContain('Indochine Villa Quận 7 Sài Gòn');
    expect(cap.textContent).toContain('Kiến Phong');
    expect(within(cap).getByRole('link')).toHaveAttribute('href', 'https://dezon.vn/nha-thau/kien-phong/indochine-villa-quan-7-sai-gon/');
    fireEvent.keyDown(dialog(), { key: 'ArrowRight' }); // ảnh 4: vòng 2, công trình đầu tiên
    expect(counter()).toBe('4 / 12');
    expect(dialog().querySelector('figcaption')!.textContent).toContain('France Indochine 22 Hà Nội');
    fireEvent.keyDown(dialog(), { key: 'ArrowRight' });
    expect(counter()).toBe('5 / 12');
    expect(dialog().querySelector('figcaption')!.textContent).toContain('Green Pool Villa Vĩnh Long');
  });

  it('ContractorDetail payload cũ (ảnh chỉ có project_slug): lưới chính vẫn có tên + link công trình', () => {
    const profile = PROFILES['kien-phong']!;
    const legacy = {
      ...profile,
      images: profile.images.map(({ url, caption, project_slug }) => ({ url, caption, project_slug })),
      projects: profile.projects.map((pr) => ({
        ...pr,
        images: (pr.images ?? []).map(({ url, caption, project_slug }) => ({ url, caption, project_slug })),
      })),
    };
    renderWithWidget(<BlockRenderer block={{ type: 'contractor_detail', data: legacy }} active />);
    const main = screen.getByText('+9').closest('button')!;
    expect(main).toHaveAccessibleName('Xem ảnh 3/12 — France Indochine 22 Hà Nội');
    fireEvent.click(main);
    const cap = dialog().querySelector('figcaption')!;
    expect(cap.querySelector('.dz-lightbox__project')!.textContent).toBe('France Indochine 22 Hà Nội');
    expect(within(cap).getByRole('link')).toHaveAttribute('href', 'https://dezon.vn/nha-thau/kien-phong/france-indochine-22-ha-noi/');
  });

  it('ContractorDetail: ô ảnh của từng công trình mở lightbox chỉ với ảnh công trình đó', () => {
    renderWithWidget(<BlockRenderer block={{ type: 'contractor_detail', data: PROFILES['kien-phong']! }} active />);
    fireEvent.click(screen.getByRole('button', { name: 'Xem ảnh 2/4 — Indochine Villa Quận 7 Sài Gòn' }));
    expect(counter()).toBe('2 / 4');
    expect(within(dialog().querySelector('figcaption')!).getByRole('link')).toHaveAttribute(
      'href',
      'https://dezon.vn/nha-thau/kien-phong/indochine-villa-quan-7-sai-gon/',
    );
  });
});
