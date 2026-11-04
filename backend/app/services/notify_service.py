"""Thông báo nhóm Lark khi khách gửi "Yêu cầu báo giá" hoặc "Liên hệ nhân viên" (bot webhook tùy chỉnh của Lark).

Cấu hình (.env, không commit): LARK_WEBHOOK_URL (rỗng -> tắt), LARK_WEBHOOK_SECRET (tùy chọn: bật "Set signature
verification" trong cài đặt bot), ADMIN_PUBLIC_URL (tùy chọn: nút "Mở trang quản trị" trên thẻ).

- Thẻ (interactive card) liệt kê MỌI thông tin khách đã điền + ngữ cảnh (nhà thầu, công trình, khái toán, nguồn).
- Nội dung khách nhập luôn ở dạng `plain_text` (không phải lark_md) -> không chèn được định dạng, link hay
  `<at id=all>` để tag cả nhóm.
- Gửi ở luồng nền SAU khi đã lưu DB: Lark chậm/lỗi không làm chậm hay hỏng yêu cầu của khách (chỉ ghi log).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

TIMEOUT_S = 5.0
VN_TZ = timezone(timedelta(hours=7))
NO_VALUE = "—"
MAX_FIELD = 1000  # Lark giới hạn kích thước thẻ; ghi chú dài bị cắt

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dz-lark")


def _sign(secret: str, timestamp: int) -> str:
    """Chữ ký của bot tùy chỉnh Lark: base64(HMAC-SHA256(key="{timestamp}\\n{secret}", msg=b""))."""
    key = f"{timestamp}\n{secret}".encode()
    return base64.b64encode(hmac.new(key, b"", digestmod=hashlib.sha256).digest()).decode()


def _text(v: Any) -> str:
    s = str(v).strip() if v is not None else ""
    if not s:
        return NO_VALUE
    return s if len(s) <= MAX_FIELD else s[: MAX_FIELD - 1] + "…"


def _field(label: str, value: Any, short: bool = True) -> dict:
    return {"is_short": short, "text": {"tag": "plain_text", "content": f"{label}\n{_text(value)}"}}


def _vn_time(dt: datetime | None = None) -> str:
    dt = dt or datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(VN_TZ).strftime("%H:%M %d/%m/%Y")


def build_card(title: str, template: str, fields: list[dict], note: str | None = None) -> dict:
    """Thẻ Lark: tiêu đề màu + các trường (2 cột với trường ngắn) + chú thích + nút mở trang quản trị (nếu cấu hình)."""
    elements: list[dict] = [{"tag": "div", "fields": fields}]
    admin_url = get_settings().admin_public_url.strip()
    if admin_url:
        elements.append({"tag": "action", "actions": [{
            "tag": "button", "type": "primary", "url": admin_url,
            "text": {"tag": "plain_text", "content": "Mở trang quản trị"}}]})
    if note:
        elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": note}]})
    return {"config": {"wide_screen_mode": True},
            "header": {"template": template, "title": {"tag": "plain_text", "content": title}},
            "elements": elements}


def _post(url: str, payload: dict) -> None:
    try:
        r = httpx.post(url, json=payload, timeout=TIMEOUT_S)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        code = body.get("code", body.get("StatusCode", 0)) if isinstance(body, dict) else 0
        if r.status_code >= 400 or code not in (0, None):
            log.warning("Lark webhook từ chối (HTTP %s): %s", r.status_code, str(body)[:300])
    except Exception:  # noqa: BLE001 — thông báo là phần phụ, không được làm hỏng luồng chính
        log.warning("Không gửi được thông báo Lark", exc_info=True)


def send_card(card: dict, *, wait: bool = False) -> bool:
    """Gửi thẻ tới webhook Lark (luồng nền). Chưa cấu hình LARK_WEBHOOK_URL -> bỏ qua, trả False."""
    s = get_settings()
    url = s.lark_webhook_url.strip()
    if not url:
        return False
    payload: dict[str, Any] = {"msg_type": "interactive", "card": card}
    if s.lark_webhook_secret.strip():
        ts = int(time.time())
        payload |= {"timestamp": str(ts), "sign": _sign(s.lark_webhook_secret.strip(), ts)}
    if wait:
        _post(url, payload)
    else:
        try:
            _executor.submit(_post, url, payload)
        except RuntimeError:  # executor đã đóng (tắt tiến trình)
            return False
    return True


# ------------------------------------------------------------------ nội dung
SOURCE_VI = {"composer": "Nút dưới ô chat", "contractor_card": "Thẻ nhà thầu",
             "contractor_detail": "Hồ sơ nhà thầu"}


def contact_card(*, code: str, name: str, phone: str, note: str | None, contractor: str | None,
                 project: str | None, source: str | None, page_url: str | None, created_at: datetime | None,
                 updated: bool = False) -> dict:
    title = f"📞 {'Cập nhật yêu cầu' if updated else 'Yêu cầu'} liên hệ nhân viên · {code}"
    fields = [
        _field("Họ tên", name), _field("Số điện thoại", phone),
        _field("Nhà thầu khách quan tâm", contractor), _field("Nguồn", SOURCE_VI.get(source or "", source)),
        _field("Công trình", project, short=False),
        _field("Ghi chú của khách", note, short=False),
        _field("Thời gian", _vn_time(created_at)), _field("Trang", page_url),
    ]
    tip = ("Khách vừa gửi lại trong 10 phút (cùng số, cùng cuộc trò chuyện): thông tin trên là bản mới nhất."
           if updated else "Gọi lại cho khách rồi cập nhật trạng thái ở tab \"Yêu cầu liên hệ\" trên trang quản trị.")
    return build_card(title, "green" if not updated else "turquoise", fields, tip)


def quote_card(*, code: str, name: str, phone: str, email: str | None, note: str | None, contractors: list[str],
               project: str | None, estimate: str | None, channel: str, created_at: datetime | None = None) -> dict:
    title = f"🧾 Yêu cầu báo giá mới · {code}"
    fields = [
        _field("Họ tên", name), _field("Số điện thoại", phone),
        _field("Email", email), _field("Kênh gửi", channel),
        _field(f"Nhà thầu nhận yêu cầu ({len(contractors)})", ", ".join(contractors), short=False),
        _field("Công trình", project, short=False),
        _field("Khái toán đính kèm", estimate, short=False),
        _field("Ghi chú gửi nhà thầu", note, short=False),
        _field("Thời gian", _vn_time(created_at)),
    ]
    return build_card(title, "orange", fields, "Chuyển yêu cầu tới các nhà thầu trên và theo dõi phản hồi báo giá.")


__all__ = ["build_card", "contact_card", "quote_card", "send_card"]
