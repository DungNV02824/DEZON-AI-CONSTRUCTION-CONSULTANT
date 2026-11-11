"""Ảnh công trình: suy URL ảnh thu nhỏ (thumbnail) của dezon.vn từ URL ảnh gốc (hàm thuần).

dezon.vn lưu ảnh công trình nhà thầu ở hai bản (theo trang `/nha-thau/<slug>/<công-trình>/`,
`a.dznt-shot[href][data-thumb]`):
  ảnh gốc      https://dezon.vn/wp-content/uploads/dz-data/nt/<slug>/o/<id>.jpg
  ảnh thu nhỏ  https://dezon.vn/wp-content/uploads/dz-data/nt/<slug>/<id>.jpg
Snapshot cũ chỉ có ảnh gốc -> bỏ đoạn "/o/" để ra ảnh thu nhỏ. Chỉ áp dụng ĐÚNG mẫu trên (không khớp -> None,
frontend dùng ảnh gốc). Đây là giả định về cấu trúc thư mục dezon.vn (docs/DECISIONS.md "V2 — gallery"); frontend
luôn lùi về ảnh gốc nếu ảnh thu nhỏ lỗi tải (SafeImage.fallbackSrc).
"""
from __future__ import annotations

import re

_ORIGINAL_RE = re.compile(
    r"^(?P<base>https?://(?:www\.)?dezon\.vn/wp-content/uploads/dz-data/nt/[a-z0-9][a-z0-9-]*)"
    r"/o/(?P<file>[A-Za-z0-9_-]+\.(?:jpe?g|png|webp))$",
    re.IGNORECASE,
)


def derive_thumb_url(url: str | None) -> str | None:
    """URL ảnh thu nhỏ của dezon.vn suy từ URL ảnh gốc `.../dz-data/nt/<slug>/o/<id>.<ext>`; không khớp -> None."""
    if not url:
        return None
    m = _ORIGINAL_RE.match(url.strip())
    if m is None:
        return None
    return f"{m.group('base')}/{m.group('file')}"
