import { describe, expect, it } from 'vitest';
import type { ImageRef } from '../api/types';
import { groupImagesByProject, imageGroupKey, orderByProject, previewIndexes, projectPositions } from './gallery';

const img = (slug: string | null, k: number, extra: Partial<ImageRef> = {}): ImageRef => ({
  url: `https://dezon.vn/${slug ?? 'le'}/${k}.jpg`,
  project_slug: slug,
  project_name: slug ? `Công trình ${slug.toUpperCase()}` : null,
  ...extra,
});

// Thứ tự xoay vòng như backend cover_images: A1 B1 C1 A2 B2 A3.
const ROUND_ROBIN = [img('a', 1), img('b', 1), img('c', 1), img('a', 2), img('b', 2), img('a', 3)];

describe('gallery utils', () => {
  it('imageGroupKey: project_slug -> project_url -> project_name -> null', () => {
    expect(imageGroupKey(img('a', 1))).toBe('s:a');
    expect(imageGroupKey({ url: 'x', project_url: 'https://dezon.vn/p/' })).toBe('u:https://dezon.vn/p/');
    expect(imageGroupKey({ url: 'x', project_name: 'Nhà A' })).toBe('n:Nhà A');
    expect(imageGroupKey({ url: 'x', caption: 'chỉ có chú thích' })).toBeNull();
  });

  it('groupImagesByProject giữ thứ tự xuất hiện đầu tiên của công trình và thứ tự ảnh trong công trình', () => {
    const groups = groupImagesByProject(ROUND_ROBIN);
    expect(groups.map((g) => g.key)).toEqual(['s:a', 's:b', 's:c']);
    expect(groups[0]!.images.map((i) => i.url)).toEqual(['https://dezon.vn/a/1.jpg', 'https://dezon.vn/a/2.jpg', 'https://dezon.vn/a/3.jpg']);
    expect(groups[0]!.name).toBe('Công trình A');
  });

  it('orderByProject: hết ảnh công trình A rồi sang B, C', () => {
    expect(orderByProject(ROUND_ROBIN).map((i) => i.url.replace('https://dezon.vn/', ''))).toEqual([
      'a/1.jpg', 'a/2.jpg', 'a/3.jpg', 'b/1.jpg', 'b/2.jpg', 'c/1.jpg',
    ]);
  });

  it('previewIndexes: ảnh đầu của các công trình khác nhau, thiếu thì lấy ảnh kế tiếp', () => {
    const ordered = orderByProject(ROUND_ROBIN);
    expect(previewIndexes(ordered, 3)).toEqual([0, 3, 5]);
    expect(previewIndexes(ordered, 2)).toEqual([0, 3]);
    const two = orderByProject([img('a', 1), img('a', 2), img('a', 3), img('b', 1)]);
    expect(previewIndexes(two, 3)).toEqual([0, 1, 3]);
    expect(previewIndexes([img(null, 1), img(null, 2)], 3)).toEqual([0, 1]);
    expect(previewIndexes([], 3)).toEqual([]);
  });

  it('projectPositions: "Ảnh k/n" trong công trình; một công trình hoặc ảnh lẻ -> null', () => {
    const ordered = orderByProject(ROUND_ROBIN);
    expect(projectPositions(ordered).map((p) => (p ? `${p.k}/${p.n}` : null))).toEqual([
      '1/3', '2/3', '3/3', '1/2', '2/2', '1/1',
    ]);
    expect(projectPositions(ordered)[3]!.name).toBe('Công trình B');
    expect(projectPositions([img('a', 1), img('a', 2)])).toEqual([null, null]);
    expect(projectPositions([img('a', 1), img(null, 1), img('b', 1)])[1]).toBeNull();
  });
});
