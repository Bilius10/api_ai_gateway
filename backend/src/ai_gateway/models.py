from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class ProviderKind(StrEnum):
    CODEX = "codex"
    OPENAI = "openai"
    GEMINI = "gemini"
    OLLAMA = "ollama"
    ANTHROPIC = "anthropic"
    OPENROUTER = "openrouter"
    OPENAI_COMPATIBLE = "openai-compatible"
    DEEPSEEK = "deepseek"
    QWEN = "qwen"


class ErrorKind(StrEnum):
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    QUOTA = "quota"
    OFFLINE = "offline"
    UPSTREAM = "upstream_5xx"
    INVALID_REQUEST = "invalid_request"
    PROVIDER_NOT_FOUND = "provider_not_found"
    AUTHENTICATION = "authentication"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid4().hex[:12], pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=100)
    kind: ProviderKind
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=10_000)
    base_url: str | None = None
    model: str = Field(min_length=1, max_length=200)
    api_key_env: str | None = None
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    supports_stream: bool = True

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must use http or https")
        if parsed.username or parsed.password:
            raise ValueError("base_url must not contain credentials")
        sensitive = {"key", "api_key", "apikey", "token", "secret", "password", "auth"}
        if any(name.lower() in sensitive for name, _ in parse_qsl(parsed.query, keep_blank_values=True)):
            raise ValueError("base_url query must not contain credentials")
        return value.rstrip("/")


class ProviderWrite(ProviderConfig):
    api_key: SecretStr | None = None


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    provider: str | None = None
    model: str | None = None
    stream: bool = False

    @field_validator("prompt")
    @classmethod
    def prompt_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must not be blank")
        return value


class AttemptPublic(BaseModel):
    provider_id: str
    provider_name: str
    success: bool
    latency_ms: int
    error_kind: ErrorKind | None = None
    error_message: str | None = None


class GenerateResponse(BaseModel):
    request_id: str
    provider: str
    model: str
    response: str
    attempts: list[AttemptPublic]


class GatewayErrorBody(BaseModel):
    request_id: str | None = None
    code: ErrorKind
    message: str
    attempts: list[AttemptPublic] = []


class RequestRecord(BaseModel):
    id: str
    requested_provider: str | None
    selected_provider: str | None
    model: str | None
    status: str
    latency_ms: int
    attempt_count: int
    created_at: datetime


class Metrics(BaseModel):
    total_requests: int
    successful_requests: int
    failed_requests: int
    fallback_requests: int
    average_latency_ms: float
    providers: dict[str, dict[str, int | float]]


class RoutingSettings(BaseModel):
    fallback_enabled: bool = True
    max_attempts: int = Field(default=5, ge=1, le=20)


class RuntimeSettings(BaseModel):
    retention_days: int = Field(default=30, ge=1, le=3650)
    max_prompt_chars: int = Field(default=50_000, ge=1, le=1_000_000)


class ProviderResult(BaseModel):
    text: str
    model: str
    raw_metadata: dict[str, Any] = {}


def utcnow() -> datetime:
    return datetime.now(UTC)
