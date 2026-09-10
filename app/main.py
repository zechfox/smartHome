"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import create_api_router
from app.config import AppConfig, load_config
from app.system import DeviceFactory, SmartHomeSystem
from app.ws import create_ws_router

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent / "web"


def create_app(
    config: AppConfig | None = None,
    device_factory: DeviceFactory | None = None,
) -> FastAPI:
    config = config or load_config()
    system = SmartHomeSystem(config, device_factory=device_factory)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info(
            "starting smartHome with %d modbus device(s)", len(config.modbus)
        )
        await system.start()
        try:
            yield
        finally:
            await system.stop()
            logger.info("smartHome stopped")

    app = FastAPI(title="smartHome", version="0.1.0", lifespan=lifespan)
    app.state.config = config
    app.state.system = system
    app.include_router(create_api_router(system, config.server.api_token))
    app.include_router(create_ws_router(system, config.server.api_token))

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app
