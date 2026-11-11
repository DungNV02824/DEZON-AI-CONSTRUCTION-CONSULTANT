/**
 * Hàm thuần cho gallery ảnh công trình: nhóm ảnh theo công trình (project_slug), sắp slideshow chạy hết ảnh
 * công trình này rồi sang công trình sau, chọn ô xem trước thuộc các công trình khác nhau, vị trí "Ảnh k/n" của
 * ảnh trong công trình.
 */
import type { ImageRef } from '../api/types';

/** Khóa công trình của ảnh: project_slug -> project_url -> project_name; không có -> null (ảnh lẻ). */
export function imageGroupKey(img: ImageRef): string | null {
  const slug = (img.project_slug ?? '').trim();
  if (slug) return `s:${slug}`;
  const url = (img.project_url ?? '').trim();
  if (url) return `u:${url}`;
  const name = (img.project_name ?? '').trim();
  return name ? `n:${name}` : null;
}

export interface ImageGroup {
  /** Khóa nhóm; null = các ảnh không gắn công trình. */
  key: string | null;
  /** Tên công trình (ảnh đầu tiên có project_name) hoặc null. */
  name: string | null;
  /** Trang công trình trên dezon.vn (ảnh đầu tiên có project_url) hoặc null. */
  url: string | null;
  images: ImageRef[];
}

/** Nhóm ảnh theo công trình, giữ thứ tự xuất hiện đầu tiên của từng công trình và thứ tự ảnh trong công trình. */
export function groupImagesByProject(images: ImageRef[]): ImageGroup[] {
  const groups: ImageGroup[] = [];
  const byKey = new Map<string | null, ImageGroup>();
  for (const img of images) {
    const key = imageGroupKey(img);
    let g = byKey.get(key);
    if (!g) {
      g = { key, name: null, url: null, images: [] };
      byKey.set(key, g);
      groups.push(g);
    }
    if (!g.name && img.project_name?.trim()) g.name = img.project_name.trim();
    if (!g.url && img.project_url?.trim()) g.url = img.project_url.trim();
    g.images.push(img);
  }
  return groups;
}

/** Thứ tự slideshow theo công trình: hết ảnh công trình A rồi sang B… (ổn định, không đổi thứ tự trong nhóm). */
export function orderByProject(images: ImageRef[]): ImageRef[] {
  return groupImagesByProject(images).flatMap((g) => g.images);
}

/**
 * Chỉ số (trong `ordered`) của tối đa `max` ô xem trước: ảnh đầu của mỗi công trình khác nhau trước, thiếu thì
 * thêm ảnh kế tiếp theo thứ tự; kết quả tăng dần (ô trên thẻ đi đúng chiều slideshow).
 */
export function previewIndexes(ordered: ImageRef[], max: number): number[] {
  const limit = Math.max(0, Math.min(max, ordered.length));
  const picked = new Set<number>();
  const seen = new Set<string | null>();
  ordered.forEach((img, i) => {
    const key = imageGroupKey(img);
    if (picked.size < limit && key !== null && !seen.has(key)) {
      seen.add(key);
      picked.add(i);
    }
  });
  for (let i = 0; i < ordered.length && picked.size < limit; i++) picked.add(i);
  return [...picked].sort((a, b) => a - b);
}

export interface ProjectPosition {
  /** Vị trí 1-based của ảnh trong công trình. */
  k: number;
  /** Số ảnh của công trình trong danh sách. */
  n: number;
  name: string | null;
}

/**
 * Vị trí của từng ảnh trong công trình của nó ("Ảnh k/n · <tên công trình>"). Danh sách chỉ có một công trình
 * (bộ đếm "i / n" đã đủ) hoặc ảnh không gắn công trình -> null.
 */
export function projectPositions(images: ImageRef[]): (ProjectPosition | null)[] {
  const groups = groupImagesByProject(images);
  const keyed = groups.filter((g) => g.key !== null);
  if (keyed.length < 2) return images.map(() => null);
  const byKey = new Map(keyed.map((g) => [g.key, g]));
  const seen = new Map<string, number>();
  return images.map((img) => {
    const key = imageGroupKey(img);
    const g = key !== null ? byKey.get(key) : undefined;
    if (!g || key === null) return null;
    const k = (seen.get(key) ?? 0) + 1;
    seen.set(key, k);
    return { k, n: g.images.length, name: g.name };
  });
}
