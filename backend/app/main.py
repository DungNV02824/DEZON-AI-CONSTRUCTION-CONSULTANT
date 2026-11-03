import importlib
import json
import re
from urllib.parse import parse_qsl

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.deps import validation_error_response
from app.config import get_settings

# Mỗi module khai báo biến `router` (APIRouter). Module chưa tồn tại thì bỏ qua; lỗi import bên trong thì báo.
ROUTER_MODULES = [
    "app.api.chat",
    "app.api.estimates",
    "app.api.contractors",
    "app.api.quotes",
    "app.api.contact",
    "app.api.admin",
]

# Ký tự điều khiển C0 (trừ \t \n \r) và DEL. PostgreSQL không lưu được NUL trong text/JSONB -> trước đây
# gây 500 / lỗi hệ thống trong chat (O-F2). Chặn ở một chỗ cho mọi endpoint.
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
CONTROL_CHAR_DETAIL = ("Dữ liệu không hợp lệ — nội dung chứa ký tự điều khiển không được hỗ trợ "
                       "(ví dụ byte NUL). Vui lòng xóa ký tự đó rồi gửi lại.")
MAX_INSPECT_BODY = 1_000_000  # body JSON lớn hơn ngưỡng này không soi (các endpoint JSON đều nhỏ)


def _has_control(value: object) -> bool:
    if isinstance(value, str):
        return bool(CONTROL_CHARS.search(value))
    if isinstance(value, dict):
        return any(_has_control(k) or _has_control(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_control(v) for v in value)
    return False


class RejectControlCharsMiddleware:
    """ASGI middleware: path, query string và body JSON chứa ký tự điều khiển -> 422 tiếng Việt.

    Dạng ASGI thuần (không dùng BaseHTTPMiddleware) để không ảnh hưởng luồng SSE của chat.
    Body không phải JSON (multipart/octet-stream của trang admin) không bị soi.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def _reject(self, send: Send) -> None:
        body = json.dumps({"detail": CONTROL_CHAR_DETAIL}, ensure_ascii=False).encode("utf-8")
        await send({"type": "http.response.start", "status": 422,
                    "headers": [(b"content-type", b"application/json; charset=utf-8"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        query = scope.get("query_string", b"").decode("latin-1")
        if _has_control(scope.get("path", "")) or any(
                _has_control(k) or _has_control(v) for k, v in parse_qsl(query, keep_blank_values=True)):
            await self._reject(send)
            return
        headers = dict(scope.get("headers") or [])
        ctype = headers.get(b"content-type", b"").decode("latin-1").lower()
        if "json" not in ctype or scope.get("method") in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return
        # Đọc trọn body JSON, kiểm tra, rồi phát lại cho ứng dụng.
        chunks: list[bytes] = []
        size = 0
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            chunks.append(chunk)
            size += len(chunk)
            more = message.get("more_body", False)
        raw = b"".join(chunks)
        if size <= MAX_INSPECT_BODY:
            if CONTROL_CHARS.search(raw.decode("utf-8", errors="ignore")):
                await self._reject(send)
                return
            try:
                parsed = json.loads(raw) if raw.strip() else None
            except ValueError:
                parsed = None  # JSON hỏng -> để FastAPI trả 422 "JSON không hợp lệ"
            if _has_control(parsed):
                await self._reject(send)
                return
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": raw, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Dezon AI Consultant", version="0.1.0")
    # Thứ tự: middleware thêm sau nằm ngoài -> CORS bọc ngoài để phản hồi 422 vẫn có header CORS.
    app.add_middleware(RejectControlCharsMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(RequestValidationError)
    async def _validation_vi(request: Request, exc: RequestValidationError):
        # Router nào chưa dùng VietnameseRoute (vd chat) cũng trả detail chuỗi tiếng Việt, không echo input (O-F5).
        return validation_error_response(exc.errors())

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "llm_provider": settings.resolved_llm_provider}

    for name in ROUTER_MODULES:
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name == name:
                continue
            raise
        app.include_router(module.router)
    return app


app = create_app()
