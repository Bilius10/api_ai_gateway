from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ai_gateway.api.routes import router
from ai_gateway.config import Settings, get_settings
from ai_gateway.models import RuntimeSettings
from ai_gateway.providers.registry import ProviderRegistry
from ai_gateway.services.database import Database
from ai_gateway.services.routing import RoutingService
from ai_gateway.services.secrets import CredentialStore


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database(runtime_settings.database_path)
        credentials = CredentialStore(runtime_settings.credential_file, runtime_settings.master_key)
        registry = ProviderRegistry(runtime_settings, credentials)
        app.state.settings = runtime_settings
        app.state.database = database
        app.state.credentials = credentials
        app.state.registry = registry
        app.state.routing = RoutingService(database, registry.create)
        persisted = database.runtime(
            RuntimeSettings(
                retention_days=runtime_settings.retention_days,
                max_prompt_chars=runtime_settings.max_prompt_chars,
            )
        )
        database.configure_retention(persisted.retention_days)
        database.prune(persisted.retention_days)
        yield
        database.close()

    app = FastAPI(
        title="Stateless AI Gateway",
        version="0.1.0",
        description="No authentication. Bind to localhost or a trusted network only.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=runtime_settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()
