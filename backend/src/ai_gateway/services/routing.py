import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from time import perf_counter
from uuid import uuid4

from ai_gateway.models import (
    AttemptPublic,
    ErrorKind,
    GenerateRequest,
    GenerateResponse,
    ProviderConfig,
)
from ai_gateway.providers import ProviderAdapter, ProviderError
from ai_gateway.providers.base import invalid_response, normalize_provider_exception
from ai_gateway.services.database import Database


class GatewayUnavailable(Exception):
    def __init__(
        self,
        request_id: str,
        attempts: list[AttemptPublic],
        message: str,
        kind: ErrorKind = ErrorKind.OFFLINE,
    ) -> None:
        super().__init__(message)
        self.request_id = request_id
        self.attempts = attempts
        self.message = message
        self.kind = kind


AdapterFactory = Callable[[ProviderConfig], ProviderAdapter]


@dataclass
class StreamResult:
    request_id: str
    chunks: AsyncIterator[str]
    attempts: list[AttemptPublic]
    started: float
    selected_provider: str | None = None
    selected_model: str | None = None


class RoutingService:
    def __init__(self, database: Database, create_adapter: AdapterFactory) -> None:
        self.database = database
        self.create_adapter = create_adapter

    def _candidates(self, requested: str | None) -> list[ProviderConfig]:
        if requested:
            provider = self.database.get_provider(requested)
            if provider is None:
                raise ProviderError(
                    ErrorKind.PROVIDER_NOT_FOUND,
                    "Requested provider was not found",
                    recoverable=False,
                    status_code=404,
                )
            if not provider.enabled:
                raise ProviderError(
                    ErrorKind.OFFLINE,
                    "Requested provider is unavailable",
                    recoverable=False,
                    status_code=503,
                )
            return [provider]
        routing = self.database.routing()
        candidates = [provider for provider in self.database.list_providers() if provider.enabled]
        return candidates[: routing.max_attempts] if routing.fallback_enabled else candidates[:1]

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        request_id = uuid4().hex
        started = perf_counter()
        attempts: list[AttemptPublic] = []
        try:
            candidates = self._candidates(request.provider)
        except ProviderError as error:
            attempts.append(_missing_attempt(request.provider or "auto", error))
            self.database.add_request(
                request_id, request.provider, None, request.model, "failed", 0, attempts
            )
            raise GatewayUnavailable(
                request_id, attempts, _terminal_message(error.kind), error.kind
            ) from error
        if not candidates:
            self.database.add_request(request_id, None, None, request.model, "failed", 0, [])
            raise GatewayUnavailable(request_id, [], "No enabled providers", ErrorKind.OFFLINE)

        last_error = ProviderError(ErrorKind.OFFLINE, "No enabled providers")
        for provider in candidates:
            attempt_started = perf_counter()
            adapter: ProviderAdapter | None = None
            try:
                adapter = self.create_adapter(provider)
                result = await asyncio.wait_for(
                    adapter.generate(request.prompt, request.model),
                    timeout=provider.timeout_seconds,
                )
                if not result.text:
                    raise invalid_response()
                attempts.append(_success_attempt(provider, attempt_started))
                latency = _elapsed(started)
                self.database.add_request(
                    request_id,
                    request.provider,
                    provider.id,
                    result.model,
                    "success",
                    latency,
                    attempts,
                )
                return GenerateResponse(
                    request_id=request_id,
                    provider=provider.id,
                    model=result.model,
                    response=result.text,
                    attempts=attempts,
                )
            except TimeoutError:
                last_error = ProviderError(
                    ErrorKind.TIMEOUT, "Provider timed out", status_code=504
                )
            except Exception as error:
                last_error = normalize_provider_exception(error)
            finally:
                if adapter is not None:
                    with contextlib.suppress(Exception):
                        await adapter.close()
            attempts.append(_failed_attempt(provider, attempt_started, last_error))
            if request.provider or not last_error.recoverable:
                break
        self.database.add_request(
            request_id, request.provider, None, request.model, "failed", _elapsed(started), attempts
        )
        raise GatewayUnavailable(
            request_id,
            attempts,
            "No provider completed the request",
            last_error.kind,
        )

    async def stream(self, request: GenerateRequest) -> StreamResult:
        request_id = uuid4().hex
        started = perf_counter()
        attempts: list[AttemptPublic] = []
        try:
            candidates = self._candidates(request.provider)
        except ProviderError as error:
            attempts.append(_missing_attempt(request.provider or "auto", error))
            self.database.add_request(
                request_id, request.provider, None, request.model, "failed", 0, attempts
            )
            raise GatewayUnavailable(
                request_id, attempts, _terminal_message(error.kind), error.kind
            ) from error
        if not candidates:
            self.database.add_request(request_id, None, None, request.model, "failed", 0, [])
            raise GatewayUnavailable(request_id, [], "No enabled providers", ErrorKind.OFFLINE)

        stream_result = StreamResult(request_id, _empty_iterator(), attempts, started)

        async def iterator() -> AsyncIterator[str]:
            last_error = ProviderError(ErrorKind.OFFLINE, "No enabled providers")
            for provider in candidates:
                adapter: ProviderAdapter | None = None
                attempt_started = perf_counter()
                emitted = False
                result_model = request.model or provider.model
                try:
                    adapter = self.create_adapter(provider)
                    async with asyncio.timeout(provider.timeout_seconds):
                        if provider.supports_stream:
                            source = adapter.stream(request.prompt, request.model)
                            try:
                                async for chunk in source:
                                    if not chunk:
                                        continue
                                    emitted = True
                                    stream_result.selected_provider = provider.id
                                    stream_result.selected_model = result_model
                                    yield chunk
                            finally:
                                close = getattr(source, "aclose", None)
                                if close is not None:
                                    await close()
                            if not emitted:
                                raise invalid_response()
                        else:
                            result = await adapter.generate(request.prompt, request.model)
                            if not result.text:
                                raise invalid_response()
                            result_model = result.model
                            emitted = True
                            stream_result.selected_provider = provider.id
                            stream_result.selected_model = result_model
                            yield result.text
                    attempts.append(_success_attempt(provider, attempt_started))
                    self.database.add_request(
                        request_id,
                        request.provider,
                        provider.id,
                        result_model,
                        "success",
                        _elapsed(started),
                        attempts,
                    )
                    return
                except (asyncio.CancelledError, GeneratorExit):
                    cancelled = ProviderError(
                        ErrorKind.CANCELLED,
                        "Request was cancelled",
                        recoverable=False,
                    )
                    attempts.append(_failed_attempt(provider, attempt_started, cancelled))
                    self.database.add_request(
                        request_id,
                        request.provider,
                        provider.id if emitted else None,
                        result_model,
                        "cancelled",
                        _elapsed(started),
                        attempts,
                    )
                    raise
                except TimeoutError:
                    last_error = ProviderError(
                        ErrorKind.TIMEOUT, "Provider timed out", status_code=504
                    )
                except Exception as error:
                    last_error = normalize_provider_exception(error)
                finally:
                    if adapter is not None:
                        with contextlib.suppress(Exception):
                            await adapter.close()
                attempts.append(_failed_attempt(provider, attempt_started, last_error))
                if emitted or request.provider or not last_error.recoverable:
                    break
            self.database.add_request(
                request_id,
                request.provider,
                stream_result.selected_provider,
                stream_result.selected_model or request.model,
                "failed",
                _elapsed(started),
                attempts,
            )
            raise GatewayUnavailable(
                request_id,
                attempts,
                "No provider completed the stream",
                last_error.kind,
            )

        stream_result.chunks = iterator()
        return stream_result


async def _empty_iterator() -> AsyncIterator[str]:
    if False:
        yield ""


def _elapsed(started: float) -> int:
    return max(0, int((perf_counter() - started) * 1000))


def _success_attempt(provider: ProviderConfig, started: float) -> AttemptPublic:
    return AttemptPublic(
        provider_id=provider.id,
        provider_name=provider.name,
        success=True,
        latency_ms=_elapsed(started),
    )


def _failed_attempt(
    provider: ProviderConfig, started: float, error: ProviderError
) -> AttemptPublic:
    return AttemptPublic(
        provider_id=provider.id,
        provider_name=provider.name,
        success=False,
        latency_ms=_elapsed(started),
        error_kind=error.kind,
        error_message=_safe_error_message(error.kind),
    )


def _missing_attempt(provider_id: str, error: ProviderError) -> AttemptPublic:
    return AttemptPublic(
        provider_id=provider_id,
        provider_name=provider_id,
        success=False,
        latency_ms=0,
        error_kind=error.kind,
        error_message=_safe_error_message(error.kind),
    )


def _terminal_message(kind: ErrorKind) -> str:
    return "Requested provider was not found" if kind is ErrorKind.PROVIDER_NOT_FOUND else "Requested provider is unavailable"


def _safe_error_message(kind: ErrorKind) -> str:
    return {
        ErrorKind.TIMEOUT: "Provider timed out",
        ErrorKind.RATE_LIMIT: "Provider rate limit reached",
        ErrorKind.QUOTA: "Provider quota exhausted",
        ErrorKind.OFFLINE: "Provider unavailable",
        ErrorKind.UPSTREAM: "Provider returned an invalid response",
        ErrorKind.INVALID_REQUEST: "Provider rejected the request",
        ErrorKind.PROVIDER_NOT_FOUND: "Provider not found",
        ErrorKind.AUTHENTICATION: "Provider authentication failed",
        ErrorKind.UNKNOWN: "Provider request failed",
        ErrorKind.CANCELLED: "Request was cancelled",
    }[kind]
