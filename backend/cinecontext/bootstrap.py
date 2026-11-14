import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from alcedo import AlcedoRuntime, Configuration, Entity, configure_alcedo
from .api import router
from .infrastructure.services import add_cinecontext_services
from .observability import configure_logging
from .settings import LoggingOptions, ToolsOptions

ROOT = Path(__file__).resolve().parents[2]


def bootstrap_app(
    configuration: Configuration,
    startup_service: type,
    register: Callable[[AlcedoRuntime], None],
    database_url: str | None = None,
    serve_frontend: bool = True,
    model_settings: dict | None = None,
) -> FastAPI:
    logging_settings = configuration.section("logging")
    logging_options = LoggingOptions.model_validate(logging_settings)
    configure_logging(logging_options, ROOT)

    @asynccontextmanager
    async def lifespan(app):
        runtime = app.state.alcedo
        async with runtime.provider.create_scope() as scope:
            startup = await scope.resolve(startup_service)
            await startup.initialize()
        yield

    app = FastAPI(title="CineContext", lifespan=lifespan)
    database_settings = configuration.section("database")
    database_settings["url"] = (
        database_url or os.getenv("DATABASE_URL") or database_settings.get("url")
    )
    if not database_settings.get("url"):
        raise ValueError("database URL is required")
    runtime_configuration = Configuration(
        {
            "database": database_settings,
            "model": model_settings or configuration.section("model"),
            "tools": configuration.section("tools"),
            "logging": logging_settings,
        },
        base_path=ROOT,
    )
    runtime = configure_alcedo(
        app, configuration=runtime_configuration, metadata=Entity.metadata
    )
    runtime.options.add(ToolsOptions, "tools")
    runtime.options.add(LoggingOptions, "logging")
    add_cinecontext_services(runtime)
    register(runtime)
    app.include_router(router, prefix="/api")

    dist = ROOT / "frontend" / "dist"
    if serve_frontend and dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app
