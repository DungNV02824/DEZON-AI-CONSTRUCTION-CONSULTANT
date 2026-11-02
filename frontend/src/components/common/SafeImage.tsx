import { useState } from 'react';

/** Ảnh có phương án dự phòng khi lỗi tải (hiện ô xám thay vì icon ảnh vỡ). */
export function SafeImage({ src, alt, className }: { src: string; alt: string; className?: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <span className={`dz-img-fallback ${className ?? ''}`} role="img" aria-label={alt}>
        Không tải được ảnh
      </span>
    );
  }
  return <img src={src} alt={alt} className={className} loading="lazy" decoding="async" onError={() => setFailed(true)} />;
}
