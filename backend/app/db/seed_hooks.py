"""Đăng ký các bước seed bổ sung. Mỗi module muốn chạy sau seed cơ bản khai báo hàm `seed_hook(db)`.

Thứ tự: dữ liệu bổ sung -> chỉ số dẫn xuất -> RAG index.
"""
import importlib

from app.db.seed import EXTRA_SEED_HOOKS

HOOK_MODULES = [
    "app.ingestion.seed_snapshot",  # snapshot dữ liệu crawl (chỉ khi bật SEED_SNAPSHOT=1)
    "app.services.contractor_service",  # tính profile_completeness
    "app.rag.embed",  # chunk + embed hồ sơ/dự án vào documents
]

for _name in HOOK_MODULES:
    try:
        _mod = importlib.import_module(_name)
    except ModuleNotFoundError as exc:
        if exc.name == _name:  # module chưa được viết -> bỏ qua; lỗi import bên trong thì báo
            continue
        raise
    _hook = getattr(_mod, "seed_hook", None)
    if callable(_hook) and _hook not in EXTRA_SEED_HOOKS:
        EXTRA_SEED_HOOKS.append(_hook)
