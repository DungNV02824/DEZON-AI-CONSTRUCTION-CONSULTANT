import importlib

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings

# Mỗi module khai báo biến `router` (APIRouter). Module chưa tồn tại thì bỏ qua; lỗi import bên trong thì báo.
ROUTER_MODULES = [
    "app.api.chat",
    "app.api.estimates",
    "app.api.contractors",
    "app.api.quotes",
    "app.api.admin",
]


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Dezon AI Consultant", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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
