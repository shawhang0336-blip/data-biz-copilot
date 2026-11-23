"""统一 HTTP 路由装配入口（Layer 6 API）。"""

from fastapi import APIRouter, FastAPI

from .catalog.assets import router as assets_router
from .copilot.chat import router as copilot_router
from .ingestion.sources import router as sources_router
from .platform.system import router as platform_router


def register_routers(app: FastAPI) -> None:
    api_router = APIRouter(prefix="/api")
    api_router.include_router(platform_router)
    api_router.include_router(sources_router)
    api_router.include_router(assets_router)
    api_router.include_router(copilot_router)
    app.include_router(api_router)
