"""Phụ thuộc dùng chung cho các router REST (trừ chat).

- `require_admin`: kiểm tra header `X-Admin-Token` (so sánh hằng thời gian) cho mọi `/api/admin/*`.
- `VietnameseRoute`: route class đổi lỗi validation (422) sang thông báo tiếng Việt, không lộ chi tiết nội bộ.
  FastAPI không cho đăng ký exception handler ở cấp router, nên dùng route class (mẫu chính thức của FastAPI)
  để không phải sửa `app/main.py`.
- `log_event`: ghi `analytics_events` (mục 12).
"""
from __future__ import annotations

import hmac
import logging
import uuid
from collections.abc import Callable, Sequence
from typing import Any

from fastapi import Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import AnalyticsEvent, ChatSession

log = logging.getLogger(__name__)

ADMIN_HEADER = "X-Admin-Token"
MAX_BIGINT = 2**63 - 1


# ------------------------------------------------------------------ quyền admin
def require_admin(x_admin_token: str | None = Header(default=None, alias=ADMIN_HEADER)) -> str:
    """Dependency cho /api/admin/*: thiếu hoặc sai token -> 401. So sánh bằng hmac.compare_digest."""
    settings = get_settings()
    if settings.app_env == "production" and settings.admin_token_is_default:
        raise HTTPException(status_code=503, detail="Chưa cấu hình ADMIN_TOKEN an toàn cho môi trường production; "
                                                    "trang quản trị bị khóa.")
    expected = settings.admin_token or ""
    given = x_admin_token or ""
    if not expected or not given or not hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Thiếu hoặc sai mã quản trị (header X-Admin-Token).",
                            headers={"WWW-Authenticate": ADMIN_HEADER})
    return "admin"


# ------------------------------------------------------------------ lỗi validation tiếng Việt
_FIELD_VI = {
    "body": "dữ liệu gửi lên", "query": "tham số", "path": "đường dẫn", "header": "header",
    "building_type": "loại công trình", "province": "tỉnh/thành", "district": "quận/huyện",
    "work_scope": "phạm vi công việc", "quality_tier": "mức chất lượng", "floors": "số tầng",
    "land_area_m2": "diện tích đất", "land_width_m": "chiều rộng đất", "land_length_m": "chiều dài đất",
    "target_budget_vnd": "ngân sách mục tiêu", "budget_vnd": "ngân sách", "contractor_ids": "danh sách nhà thầu",
    "contractor_id": "nhà thầu", "customer_name": "họ tên", "customer_phone": "số điện thoại",
    "customer_email": "email", "note": "ghi chú", "estimate_session_id": "mã khái toán",
    "chat_session_id": "mã phiên chat", "limit": "giới hạn số kết quả", "weights": "trọng số",
    "coefficient": "hệ số", "price_min": "đơn giá thấp nhất", "price_max": "đơn giá cao nhất",
    "effective_from": "ngày hiệu lực", "effective_to": "ngày hết hiệu lực", "status": "trạng thái",
    "log_id": "mã lượt đề xuất", "slug": "mã nhà thầu", "estimate_id": "mã khái toán", "rate_id": "mã đơn giá",
    "rule_id": "mã hệ số", "source_id": "mã nguồn", "version": "phiên bản", "source": "nguồn đồng bộ",
    "kind": "loại dữ liệu", "code": "mã hệ số", "label_vi": "nhãn", "group_name": "nhóm hệ số",
    "base": "cơ sở tính", "approved": "trạng thái duyệt", "is_active": "trạng thái kích hoạt",
    "verified_by": "người xác minh", "offset": "vị trí bắt đầu", "rule": "loại vi phạm",
    "q": "từ khóa", "basement_depth_m": "độ sâu tầng hầm",
    "building_area_m2": "diện tích xây dựng", "mezzanine_area_m2": "diện tích lửng",
    "basement_area_m2": "diện tích hầm", "roof_area_m2": "diện tích mái", "rooftop_area_m2": "diện tích sân thượng",
    "yard_area_m2": "diện tích sân", "roof_type": "loại mái", "foundation_type": "loại móng",
    "rooftop_terrace": "sân thượng", "converted_area_m2": "diện tích quy đổi",
}


def _field_label(loc: Sequence[Any]) -> str:
    parts = [str(p) for p in loc if not isinstance(p, int)]
    if parts and parts[0] in ("body", "query", "path", "header") and len(parts) > 1:
        parts = parts[1:]
    if not parts:
        return "dữ liệu gửi lên"
    name = parts[-1]
    return _FIELD_VI.get(name, name)


def _message_vi(err: dict[str, Any]) -> str:
    t = err.get("type", "")
    ctx = err.get("ctx") or {}
    msg = str(err.get("msg", ""))
    if t == "missing":
        return "thiếu thông tin bắt buộc"
    if t in ("int_parsing", "int_type", "int_from_float"):
        return "phải là số nguyên"
    if t in ("float_parsing", "float_type", "decimal_parsing", "decimal_type"):
        return "phải là số"
    if t in ("finite_number", "decimal_max_digits", "decimal_max_places", "decimal_whole_digits"):
        return "phải là số hữu hạn hợp lệ"
    if t in ("bool_parsing", "bool_type"):
        return "phải là true/false"
    if t in ("uuid_parsing", "uuid_type"):
        return "mã định danh (UUID) không hợp lệ"
    if t in ("date_parsing", "date_from_datetime_parsing", "date_type"):
        return "ngày không hợp lệ (định dạng YYYY-MM-DD)"
    if t == "greater_than":
        return f"phải lớn hơn {ctx.get('gt')}"
    if t == "greater_than_equal":
        return f"phải lớn hơn hoặc bằng {ctx.get('ge')}"
    if t == "less_than":
        return f"phải nhỏ hơn {ctx.get('lt')}"
    if t == "less_than_equal":
        return f"phải nhỏ hơn hoặc bằng {ctx.get('le')}"
    if t == "too_short":
        return f"cần ít nhất {ctx.get('min_length')} phần tử/ký tự"
    if t == "too_long":
        return f"tối đa {ctx.get('max_length')} phần tử/ký tự"
    if t in ("string_too_short",):
        return f"cần ít nhất {ctx.get('min_length')} ký tự"
    if t in ("string_too_long",):
        return f"tối đa {ctx.get('max_length')} ký tự"
    if t in ("string_type", "string_parsing"):
        return "phải là chuỗi ký tự"
    if t in ("enum", "literal_error"):
        expected = str(ctx.get("expected") or "").replace(" or ", " hoặc ")
        return f"giá trị không hợp lệ (chấp nhận: {expected})" if expected else "giá trị không hợp lệ"
    if t == "json_invalid":
        return "JSON không hợp lệ"
    if t in ("model_type", "model_attributes_type", "dict_type"):
        return "phải là một đối tượng JSON"
    if t == "list_type":
        return "phải là một danh sách"
    if t == "value_error":
        return msg.removeprefix("Value error, ")
    if t == "assertion_error":
        return msg.removeprefix("Assertion failed, ")
    return msg or "giá trị không hợp lệ"


def validation_error_response(errors: Sequence[dict[str, Any]]) -> JSONResponse:
    items = []
    for e in errors:
        loc = list(e.get("loc", ()))
        items.append({"loc": loc, "field": _field_label(loc), "message": _message_vi(e), "type": e.get("type")})
    summary = "; ".join(f"{i['field']}: {i['message']}" for i in items[:5]) or "dữ liệu không hợp lệ"
    return JSONResponse(status_code=422, content={"detail": f"Dữ liệu không hợp lệ — {summary}", "errors": items})


class VietnameseRoute(APIRoute):
    """Route class: lỗi validation request -> 422 với thông báo tiếng Việt (detail là chuỗi để widget hiển thị)."""

    def get_route_handler(self) -> Callable:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except RequestValidationError as exc:
                return validation_error_response(exc.errors())

        return handler


def unprocessable(message: str) -> HTTPException:
    return HTTPException(status_code=422, detail=message)


def not_found(message: str) -> HTTPException:
    return HTTPException(status_code=404, detail=message)


# ------------------------------------------------------------------ analytics
def existing_chat_session_id(db: Session, value: uuid.UUID | str | None) -> uuid.UUID | None:
    """UUID phiên chat nếu tồn tại; không có/không hợp lệ -> None (tránh lỗi khóa ngoại)."""
    if not value:
        return None
    try:
        sid = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except ValueError:
        return None
    return sid if db.get(ChatSession, sid) is not None else None


def log_event(db: Session, event: str, session_id: uuid.UUID | None = None, commit: bool = True,
              **props: Any) -> None:
    """Ghi một dòng analytics_events. Lỗi ghi sự kiện không được làm hỏng request chính."""
    try:
        db.add(AnalyticsEvent(session_id=session_id, event=event, props=props))
        if commit:
            db.commit()
    except Exception:  # noqa: BLE001
        log.exception("Không ghi được analytics_events %s", event)
        db.rollback()
