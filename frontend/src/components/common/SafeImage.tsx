import { useState } from 'react';

/**
 * Ảnh có phương án dự phòng khi lỗi tải: thử `fallbackSrc` một lần (vd ô ảnh dùng ảnh thu nhỏ, lỗi thì lùi về ảnh
 * gốc), lỗi tiếp -> ô xám "Không tải được ảnh" thay vì icon ảnh vỡ.
 */
export function SafeImage({
  src,
  alt,
  className,
  loading = 'lazy',
  fallbackSrc,
  onLoad,
  onFail,
}: {
  src: string;
  alt: string;
  className?: string;
  /** Mặc định "lazy"; lightbox dùng "eager" cho ảnh đang xem. */
  loading?: 'lazy' | 'eager';
  /** Ảnh thay thế khi `src` lỗi (bỏ qua nếu trùng `src`). */
  fallbackSrc?: string | null;
  /** Ảnh đã tải xong. */
  onLoad?: () => void;
  /** Mọi nguồn đều lỗi (đang hiện ô "Không tải được ảnh"). */
  onFail?: () => void;
}) {
  // Số lần lỗi của đúng `src` hiện tại (src đổi -> thử lại từ đầu).
  const [fail, setFail] = useState<{ src: string; count: number }>({ src, count: 0 });
  const count = fail.src === src ? fail.count : 0;
  const alt2 = fallbackSrc && fallbackSrc !== src ? fallbackSrc : null;
  const current = count === 0 ? src : count === 1 && alt2 ? alt2 : null;
  if (current === null) {
    return (
      <span className={`dz-img-fallback ${className ?? ''}`} role="img" aria-label={alt}>
        Không tải được ảnh
      </span>
    );
  }
  return (
    <img
      key={current}
      src={current}
      alt={alt}
      className={className}
      loading={loading}
      decoding="async"
      onLoad={onLoad}
      onError={() => {
        const next = count + 1;
        setFail({ src, count: next });
        if (!(next === 1 && alt2)) onFail?.();
      }}
    />
  );
}
