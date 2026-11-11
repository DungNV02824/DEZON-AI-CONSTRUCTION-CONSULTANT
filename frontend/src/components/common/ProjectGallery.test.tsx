import { fireEvent, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MATCH_FIXTURE, PROFILES } from '../../api/fixtures';
import { renderWithWidget } from '../../test/utils';
import { BlockRenderer } from '../blocks/BlockRenderer';
import { ContractorCardView } from '../blocks/ContractorCard';
import { LightboxProvider } from './Lightbox';
import { ProjectGalleryProvider } from './ProjectGallery';

const PROJECT_NAMES = ['France Indochine 22 Hà Nội', 'Green Pool Villa Vĩnh Long', 'Indochine Villa Quận 7 Sài Gòn'];

function gallery() {
  return screen.getByRole('dialog', { name: /Ảnh công trình của Kiến Phong/ });
}

function lightbox() {
  return screen.getByRole('dialog', { name: /Xem ảnh/ });
}

function renderDetail(withProviders = false) {
  const block = <BlockRenderer block={{ type: 'contractor_detail', data: PROFILES['kien-phong']! }} active />;
  return renderWithWidget(
    withProviders ? (
      <LightboxProvider>
        <ProjectGalleryProvider>{block}</ProjectGalleryProvider>
      </LightboxProvider>
    ) : (
      block
    ),
  );
}

describe('ProjectGallery — "Xem tất cả ảnh" nhóm theo công trình', () => {
  it('ContractorDetail: nút mở hộp thoại, mỗi công trình có tiêu đề + link trang công trình + đủ ô ảnh thu nhỏ', () => {
    renderDetail();
    const btn = screen.getByRole('button', { name: 'Xem tất cả 12 ảnh theo công trình' });
    fireEvent.click(btn);
    const dlg = gallery();
    expect(dlg).toHaveAttribute('aria-modal', 'true');
    expect(dlg.textContent).toContain('12 ảnh từ 3 công trình có ảnh');
    // Số nhóm ảnh không phải dezon_project_count -> không được mượn nhãn dành riêng cho số công trình.
    expect(dlg.textContent).not.toContain('công trình ghi nhận trên Dezon');
    expect(dlg.textContent).not.toMatch(/tốt nhất/i);
    const sections = within(dlg).getAllByRole('region');
    expect(sections).toHaveLength(3);
    sections.forEach((sec, i) => {
      const name = PROJECT_NAMES[i]!;
      const link = within(sec).getByRole('link', { name: new RegExp(name) });
      expect(link.getAttribute('href')).toContain('https://dezon.vn/nha-thau/kien-phong/');
      expect(link).toHaveAttribute('target', '_blank');
      const tiles = within(sec).getAllByRole('button', { name: /^Xem ảnh \d\/4/ });
      expect(tiles).toHaveLength(4);
      expect(within(sec).queryByText(/^\+\d/)).toBeNull(); // lưới đầy đủ, không có lớp "+N"
      for (const t of tiles) expect(t.querySelector('img')!.getAttribute('src')).not.toContain('/o/');
    });
    // Dòng meta công trình (khu vực · đơn vị thi công).
    expect(within(sections[2]!).getByText(/Quận 7, TP\. Hồ Chí Minh/)).toBeInTheDocument();
  });

  it('bấm ô -> lightbox chỉ chạy ảnh công trình đó; Esc lần 1 đóng lightbox, lần 2 đóng hộp thoại, focus về nút mở', () => {
    renderDetail(true);
    const btn = screen.getByRole('button', { name: 'Xem tất cả 12 ảnh theo công trình' });
    btn.focus();
    fireEvent.click(btn);
    expect(screen.getByRole('button', { name: 'Đóng' })).toHaveFocus();
    const sec = within(gallery()).getAllByRole('region')[1]!;
    const tile = within(sec).getByRole('button', { name: 'Xem ảnh 3/4 — Green Pool Villa Vĩnh Long' });
    fireEvent.click(tile);
    const lb = lightbox();
    expect(lb.querySelector('.dz-lightbox__counter')!.textContent!.replace(/\s+/g, ' ').trim()).toBe('3 / 4');
    expect(lb.querySelector('img.dz-lightbox__img')!.getAttribute('src')).toContain('/o/'); // ảnh gốc
    fireEvent.keyDown(lb, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: /Xem ảnh/ })).toBeNull();
    expect(gallery()).toBeInTheDocument();
    expect(tile).toHaveFocus();
    fireEvent.keyDown(gallery(), { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(btn).toHaveFocus();
  });

  it('khóa cuộn trang khi mở (kể cả lồng lightbox) và khôi phục khi đóng', () => {
    document.documentElement.style.overflow = 'auto';
    renderDetail(true);
    fireEvent.click(screen.getByRole('button', { name: 'Xem tất cả 12 ảnh theo công trình' }));
    expect(document.documentElement.style.overflow).toBe('hidden');
    fireEvent.click(within(gallery()).getAllByRole('button', { name: /^Xem ảnh/ })[0]!);
    fireEvent.keyDown(lightbox(), { key: 'Escape' });
    expect(document.documentElement.style.overflow).toBe('hidden');
    fireEvent.click(within(gallery()).getByRole('button', { name: 'Đóng' }));
    expect(document.documentElement.style.overflow).toBe('auto');
    document.documentElement.style.overflow = '';
  });

  it('Tab giữ focus trong hộp thoại', () => {
    renderDetail();
    fireEvent.click(screen.getByRole('button', { name: 'Xem tất cả 12 ảnh theo công trình' }));
    const dlg = gallery();
    const close = within(dlg).getByRole('button', { name: 'Đóng' });
    const tiles = within(dlg).getAllByRole('button', { name: /^Xem ảnh/ });
    tiles[tiles.length - 1]!.focus();
    fireEvent.keyDown(dlg, { key: 'Tab' });
    expect(close).toHaveFocus();
    fireEvent.keyDown(dlg, { key: 'Tab', shiftKey: true });
    expect(tiles[tiles.length - 1]).toHaveFocus();
  });

  it('ContractorCard: nút "Xem tất cả 12 ảnh" nhóm cover_images theo project_slug phía client', () => {
    renderWithWidget(<ContractorCardView item={MATCH_FIXTURE.results[0]!} />);
    fireEvent.click(screen.getByRole('button', { name: 'Xem tất cả 12 ảnh' }));
    const sections = within(gallery()).getAllByRole('region');
    expect(sections.map((s) => s.getAttribute('aria-label'))).toEqual(PROJECT_NAMES.map((n) => `Ảnh công trình ${n}`));
    // Trong mỗi công trình: ảnh 1, 2, 3, 4 theo thứ tự (không xoay vòng).
    const tiles = within(sections[0]!).getAllByRole('button', { name: /^Xem ảnh/ });
    expect(tiles.map((t) => t.getAttribute('aria-label'))).toEqual(
      [1, 2, 3, 4].map((k) => `Xem ảnh ${k}/4 — France Indochine 22 Hà Nội`),
    );
  });

  it('không quá 3 ảnh -> không có nút "Xem tất cả"', () => {
    const item = MATCH_FIXTURE.results[0]!;
    const few = { ...item, contractor: { ...item.contractor, cover_images: item.contractor.cover_images!.slice(0, 3) } };
    renderWithWidget(<ContractorCardView item={few} />);
    expect(screen.queryByRole('button', { name: /Xem tất cả/ })).toBeNull();
  });
});
