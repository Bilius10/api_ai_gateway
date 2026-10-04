import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

import httpx

from ai_gateway.models import ErrorKind, ProviderConfig, ProviderResult


class ProviderError(Exception):
    def __init__(
        self,
        kind: ErrorKind,
        message: str,
        *,
        recoverable: bool = True,
        status_code: int = 502,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.recoverable = recoverable
        self.status_code = status_code


class ProviderAdapter(ABC):
    def __init__(
        self,
        config: ProviderConfig,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self.api_key = api_key
        self.transport = transport

    @abstractmethod
    async def generate(self, prompt: str, model: str | None = None) -> ProviderResult: ...

    async def stream(self, prompt: str, model: str | None = None) -> AsyncIterator[str]:
        result = await self.generate(prompt, model)
        yield result.text

    @abstractmethod
    async def health(self) -> tuple[bool, str]: ...

    async def models(self) -> list[str]:
        return [self.config.model]

    async def close(self) -> None:
        return None


def classify_http_error(status: int, detail: str = "Provider request failed") -> ProviderError:
    if status == 408:
        return ProviderError(ErrorKind.TIMEOUT, "Provider timed out", status_code=504)
    if status == 402:
        return ProviderError(ErrorKind.QUOTA, "Provider quota exhausted", status_code=402)
    if status == 429:
        lowered = detail.lower()
        kind = (
            ErrorKind.QUOTA if "quota" in lowered or "billing" in lowered else ErrorKind.RATE_LIMIT
        )
        message = "Provider quota exhausted" if kind is ErrorKind.QUOTA else "Provider rate limit reached"
        return ProviderError(kind, message, status_code=429)
    if status in {401, 403}:
        return ProviderError(
            ErrorKind.AUTHENTICATION,
            "Provider authentication failed",
            recoverable=False,
            status_code=status,
        )
    if status >= 500:
        return ProviderError(ErrorKind.UPSTREAM, "Provider returned a server error", status_code=502)
    return ProviderError(
        ErrorKind.INVALID_REQUEST,
        "Provider rejected the request",
        recoverable=False,
        status_code=400,
    )


def classify_transport_error(error: Exception) -> ProviderError:
    if isinstance(error, (TimeoutError, asyncio.TimeoutError, httpx.TimeoutException)):
        return ProviderError(ErrorKind.TIMEOUT, "Provider timed out", status_code=504)
    if isinstance(error, httpx.RequestError):
        return ProviderError(ErrorKind.OFFLINE, "Provider is unreachable", status_code=503)
    return ProviderError(ErrorKind.UNKNOWN, "Provider request failed")


def invalid_response() -> ProviderError:
    return ProviderError(ErrorKind.UPSTREAM, "Provider returned an invalid response")


def normalize_provider_exception(error: BaseException) -> ProviderError:
    if isinstance(error, ProviderError):
        return error
    if isinstance(error, Exception):
        return classify_transport_error(error)
    return ProviderError(ErrorKind.UNKNOWN, "Provider request failed")
