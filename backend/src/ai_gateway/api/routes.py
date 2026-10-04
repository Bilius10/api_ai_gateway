import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from ai_gateway.config import Settings
from ai_gateway.models import (
    ErrorKind,
    GatewayErrorBody,
    GenerateRequest,
    GenerateResponse,
    Metrics,
    ProviderConfig,
    ProviderWrite,
    RequestRecord,
    RoutingSettings,
    RuntimeSettings,
)
from ai_gateway.providers.base import normalize_provider_exception
from ai_gateway.providers.registry import ProviderRegistry
from ai_gateway.services.database import Database
from ai_gateway.services.routing import GatewayUnavailable, RoutingService
from ai_gateway.services.secrets import CredentialStore

router = APIRouter(prefix="/api")


def get_database(request: Request) -> Database:
    return cast(Database, request.app.state.database)


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_credentials(request: Request) -> CredentialStore:
    return cast(CredentialStore, request.app.state.credentials)


def get_registry(request: Request) -> ProviderRegistry:
    return cast(ProviderRegistry, request.app.state.registry)


def get_routing(request: Request) -> RoutingService:
    return cast(RoutingService, request.app.state.routing)


@router.get("/health")
async def health(database: Database = Depends(get_database)) -> dict[str, object]:
    return {
        "status": "ok",
        "providers": len(database.list_providers()),
        "warning": "No authentication: expose only on localhost or a trusted network",
    }


@router.post("/generate", response_model=GenerateResponse)
async def generate(
    payload: GenerateRequest,
    routing: RoutingService = Depends(get_routing),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> GenerateResponse:
    runtime = database.runtime(
        RuntimeSettings(
            retention_days=settings.retention_days,
            max_prompt_chars=settings.max_prompt_chars,
        )
    )
    if len(payload.prompt) > runtime.max_prompt_chars:
        raise HTTPException(status_code=422, detail="prompt exceeds configured maximum")
    try:
        return await routing.generate(payload)
    except GatewayUnavailable as exc:
        body = GatewayErrorBody(
            request_id=exc.request_id,
            code=exc.kind,
            message=exc.message,
            attempts=exc.attempts,
        )
        raise HTTPException(
            status_code=_gateway_status(exc.kind), detail=body.model_dump(mode="json")
        ) from exc


@router.post("/generate/stream")
async def generate_stream(
    payload: GenerateRequest,
    request: Request,
    routing: RoutingService = Depends(get_routing),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    runtime = database.runtime(
        RuntimeSettings(
            retention_days=settings.retention_days,
            max_prompt_chars=settings.max_prompt_chars,
        )
    )
    if len(payload.prompt) > runtime.max_prompt_chars:
        raise HTTPException(status_code=422, detail="prompt exceeds configured maximum")
    try:
        stream = await routing.stream(payload)
    except GatewayUnavailable as exc:
        body = GatewayErrorBody(
            request_id=exc.request_id,
            code=exc.kind,
            message=exc.message,
            attempts=exc.attempts,
        )
        raise HTTPException(
            status_code=_gateway_status(exc.kind), detail=body.model_dump(mode="json")
        ) from exc

    async def events() -> AsyncIterator[str]:
        yield _sse("start", {"request_id": stream.request_id})
        try:
            try:
                async for chunk in stream.chunks:
                    if await request.is_disconnected():
                        raise asyncio.CancelledError
                    yield _sse("chunk", {"text": chunk})
            finally:
                close = getattr(stream.chunks, "aclose", None)
                if close is not None:
                    await close()
            yield _sse(
                "done",
                {
                    "request_id": stream.request_id,
                    "provider": stream.selected_provider,
                    "model": stream.selected_model,
                    "attempts": [attempt.model_dump(mode="json") for attempt in stream.attempts],
                },
            )
        except GatewayUnavailable as exc:
            yield _sse(
                "error",
                GatewayErrorBody(
                    request_id=exc.request_id,
                    code=exc.kind,
                    message=exc.message,
                    attempts=exc.attempts,
                ).model_dump(mode="json"),
            )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/providers", response_model=list[ProviderConfig])
async def providers(database: Database = Depends(get_database)) -> list[ProviderConfig]:
    return database.list_providers()


@router.post("/providers", response_model=ProviderConfig, status_code=status.HTTP_201_CREATED)
async def create_provider(
    payload: ProviderWrite,
    database: Database = Depends(get_database),
    credentials: CredentialStore = Depends(get_credentials),
    settings: Settings = Depends(get_settings),
) -> ProviderConfig:
    _validate_api_key_env(payload.api_key_env, settings)
    provider = ProviderConfig.model_validate(payload.model_dump(exclude={"api_key"}))
    try:
        database.insert_provider(provider)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="provider id already exists") from exc
    try:
        if payload.api_key is not None:
            credentials.set(provider.id, payload.api_key.get_secret_value())
    except Exception as exc:
        database.delete_provider(provider.id)
        raise _credential_error(exc) from exc
    return provider


@router.put("/providers/{provider_id}", response_model=ProviderConfig)
async def update_provider(
    provider_id: str,
    payload: ProviderWrite,
    database: Database = Depends(get_database),
    credentials: CredentialStore = Depends(get_credentials),
    settings: Settings = Depends(get_settings),
) -> ProviderConfig:
    if provider_id != payload.id:
        raise HTTPException(status_code=409, detail="provider id cannot be changed")
    existing = database.get_provider(provider_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="provider not found")
    _validate_api_key_env(payload.api_key_env, settings)
    provider = ProviderConfig.model_validate(payload.model_dump(exclude={"api_key"}))
    try:
        previous_secret = credentials.get(provider_id)
    except Exception as exc:
        raise _credential_error(exc) from exc
    if database.replace_provider(provider) is None:
        raise HTTPException(status_code=404, detail="provider not found")
    try:
        if "api_key" in payload.model_fields_set:
            if payload.api_key is None:
                credentials.delete(provider.id)
            else:
                credentials.set(provider.id, payload.api_key.get_secret_value())
    except Exception as exc:
        database.replace_provider(existing)
        _restore_secret(credentials, provider_id, previous_secret)
        raise _credential_error(exc) from exc
    return provider


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    provider_id: str,
    database: Database = Depends(get_database),
    credentials: CredentialStore = Depends(get_credentials),
) -> None:
    provider = database.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="provider not found")
    try:
        previous_secret = credentials.get(provider_id)
    except Exception as exc:
        raise _credential_error(exc) from exc
    if not database.delete_provider(provider_id):
        raise HTTPException(status_code=404, detail="provider not found")
    try:
        credentials.delete(provider_id)
    except Exception as exc:
        database.insert_provider(provider)
        _restore_secret(credentials, provider_id, previous_secret)
        raise _credential_error(exc) from exc


@router.get("/providers/{provider_id}/health")
async def provider_health(
    provider_id: str,
    database: Database = Depends(get_database),
    registry: ProviderRegistry = Depends(get_registry),
) -> dict[str, object]:
    provider = database.get_provider(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="provider not found")
    adapter = None
    try:
        adapter = registry.create(provider)
        ok, detail = await adapter.health()
    except Exception as exc:
        error = normalize_provider_exception(exc)
        return {"provider": provider_id, "healthy": False, "detail": error.message}
    finally:
        if adapter is not None:
            await adapter.close()
    return {"provider": provider_id, "healthy": ok, "detail": detail}


@router.get("/providers/{provider_id}/models")
async def provider_models(
    provider_id: str,
    database: Database = Depends(get_database),
    registry: ProviderRegistry = Depends(get_registry),
) -> dict[str, object]:
    provider = database.get_provider(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="provider not found")
    adapter = None
    try:
        adapter = registry.create(provider)
        models = await adapter.models()
    except Exception as exc:
        error = normalize_provider_exception(exc)
        raise HTTPException(status_code=502, detail=error.message) from exc
    finally:
        if adapter is not None:
            await adapter.close()
    return {"provider": provider_id, "models": models}


@router.get("/routing", response_model=RoutingSettings)
async def get_routing_settings(database: Database = Depends(get_database)) -> RoutingSettings:
    return database.routing()


@router.put("/routing", response_model=RoutingSettings)
async def update_routing_settings(payload: RoutingSettings, database: Database = Depends(get_database)) -> RoutingSettings:
    return database.set_routing(payload)


@router.get("/requests", response_model=list[RequestRecord])
async def requests(limit: int = Query(default=100, ge=1, le=1000), database: Database = Depends(get_database)) -> list[RequestRecord]:
    return database.list_requests(limit)


@router.get("/requests/{request_id}")
async def request_detail(request_id: str, database: Database = Depends(get_database)) -> dict[str, object]:
    record = database.get_request(request_id)
    if not record:
        raise HTTPException(status_code=404, detail="request not found")
    return {"request": record.model_dump(mode="json"), "attempts": [x.model_dump(mode="json") for x in database.attempts_for(request_id)]}


@router.get("/metrics", response_model=Metrics)
async def metrics(database: Database = Depends(get_database)) -> Metrics:
    return database.metrics()


@router.get("/settings", response_model=RuntimeSettings)
async def runtime_settings(database: Database = Depends(get_database), settings: Settings = Depends(get_settings)) -> RuntimeSettings:
    return database.runtime(RuntimeSettings(retention_days=settings.retention_days, max_prompt_chars=settings.max_prompt_chars))


@router.put("/settings", response_model=RuntimeSettings)
async def update_runtime_settings(payload: RuntimeSettings, database: Database = Depends(get_database)) -> RuntimeSettings:
    saved = database.set_runtime(payload)
    database.configure_retention(saved.retention_days)
    database.prune(saved.retention_days)
    return saved


def _sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _validate_api_key_env(api_key_env: str | None, settings: Settings) -> None:
    if api_key_env and api_key_env not in set(settings.allowed_api_key_envs):
        raise HTTPException(status_code=422, detail="api_key_env is not allowed")


def _credential_error(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="credential store unavailable; configure AI_GATEWAY_MASTER_KEY",
    )


def _gateway_status(kind: ErrorKind) -> int:
    return {
        ErrorKind.AUTHENTICATION: status.HTTP_401_UNAUTHORIZED,
        ErrorKind.PROVIDER_NOT_FOUND: status.HTTP_404_NOT_FOUND,
        ErrorKind.INVALID_REQUEST: status.HTTP_400_BAD_REQUEST,
        ErrorKind.RATE_LIMIT: status.HTTP_429_TOO_MANY_REQUESTS,
        ErrorKind.QUOTA: status.HTTP_429_TOO_MANY_REQUESTS,
        ErrorKind.TIMEOUT: status.HTTP_504_GATEWAY_TIMEOUT,
        ErrorKind.OFFLINE: status.HTTP_503_SERVICE_UNAVAILABLE,
        ErrorKind.UPSTREAM: status.HTTP_502_BAD_GATEWAY,
        ErrorKind.UNKNOWN: status.HTTP_502_BAD_GATEWAY,
        ErrorKind.CANCELLED: 499,
    }[kind]


def _restore_secret(
    credentials: CredentialStore, provider_id: str, secret: str | None
) -> None:
    try:
        if secret is None:
            credentials.delete(provider_id)
        else:
            credentials.set(provider_id, secret)
    except Exception:
        pass
