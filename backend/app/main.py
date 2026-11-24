"""dc_agent FastAPI 极简入口（Layer 7）。"""

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import register_routers
from app.catalog.batch_ingest import recover_stale_batch_jobs
from app.infra.config import get_settings
from app.infra.db import SessionLocal, init_db
from app.infra.llm import close_http_client
from app.infra.task_runner import task_runner

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    with SessionLocal() as session:
        try:
            recover_stale_batch_jobs(session)
        except Exception:  # noqa: BLE001
            logger.exception("Startup stale batch recovery failed")
    try:
        yield
    finally:
        task_runner().shutdown(wait=False)
        close_http_client()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    origins = [item.strip() for item in settings.cors_origins.split(",") if item.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_routers(app)
    return app


app = create_app()
