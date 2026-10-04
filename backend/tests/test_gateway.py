import asyncio
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import AsyncClient

from ai_gateway.models import ErrorKind, GenerateRequest, ProviderConfig, ProviderKind, ProviderResult
from ai_gateway.providers.base import ProviderAdapter, ProviderError, classify_http_error
from ai_gateway.providers.codex import CodexAdapter, _parse_output, classify_cli_error
from ai_gateway.providers.http import PRESETS, OpenAICompatibleAdapter
from ai_gateway.services.secrets import CredentialStore


class FakeAdapter(ProviderAdapter):
    def __init__(self, config: ProviderConfig, outcome: str) -> None:
        super().__init__(config)
        self.outcome = outcome
        self.cancelled = False

    async def generate(self, prompt: str, model: str | None = None) -> ProviderResult:
        if self.outcome == "success":
            return ProviderResult(text=f"answer:{prompt}", model=model or self.config.model)
        if self.outcome == "timeout":
            raise ProviderError(ErrorKind.TIMEOUT, "timeout")
        if self.outcome == "rate":
            raise ProviderError(ErrorKind.RATE_LIMIT, "rate limited")
        raise ProviderError(ErrorKind.OFFLINE, "offline")

    async def stream(self, prompt: str, model: str | None = None) -> AsyncIterator[str]:
        try:
            if self.outcome != "success":
                raise ProviderError(ErrorKind.OFFLINE, "offline")
            yield "answer:"
            await asyncio.sleep(0)
            yield prompt
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def health(self) -> tuple[bool, str]:
        return self.outcome == "success", self.outcome


def enable(app: FastAPI, *ids: str) -> None:
    for priority, provider_id in enumerate(ids):
        provider = app.state.database.get_provider(provider_id)
        assert provider
        app.state.database.upsert_provider(provider.model_copy(update={"enabled": True, "priority": priority}))


@pytest.mark.asyncio
async def test_health(client: AsyncClient) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_automatic_fallback_records_sanitized_attempts(client: AsyncClient, app: FastAPI) -> None:
    enable(app, "openai", "gemini")
    outcomes = {"openai": "timeout", "gemini": "success"}
    app.state.routing.create_adapter = lambda config: FakeAdapter(config, outcomes[config.id])
    secret_prompt = "do not persist this unique secret prompt"

    response = await client.post("/api/generate", json={"prompt": secret_prompt})
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "gemini"
    assert [item["success"] for item in body["attempts"]] == [False, True]

    requests = (await client.get("/api/requests")).json()
    assert requests[0]["attempt_count"] == 2
    raw = app.state.database.connection.execute("SELECT * FROM requests").fetchall()
    attempts = app.state.database.connection.execute("SELECT * FROM attempts").fetchall()
    assert secret_prompt not in repr(raw) + repr(attempts)


@pytest.mark.asyncio
async def test_manual_provider_never_falls_back(client: AsyncClient, app: FastAPI) -> None:
    enable(app, "openai", "gemini")
    calls: list[str] = []

    def factory(config: ProviderConfig) -> FakeAdapter:
        calls.append(config.id)
        return FakeAdapter(config, "offline" if config.id == "openai" else "success")

    app.state.routing.create_adapter = factory
    response = await client.post("/api/generate", json={"prompt": "hello", "provider": "openai"})
    assert response.status_code == 503
    assert calls == ["openai"]


@pytest.mark.asyncio
async def test_total_unavailability_returns_structured_503(client: AsyncClient, app: FastAPI) -> None:
    enable(app, "openai", "gemini")
    app.state.routing.create_adapter = lambda config: FakeAdapter(config, "offline")
    response = await client.post("/api/generate", json={"prompt": "hello"})
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "offline"
    assert len(detail["attempts"]) == 2


@pytest.mark.asyncio
async def test_invalid_prompts_create_no_external_attempt(client: AsyncClient, app: FastAPI) -> None:
    enable(app, "openai")
    called = False

    def factory(config: ProviderConfig) -> FakeAdapter:
        nonlocal called
        called = True
        return FakeAdapter(config, "success")

    app.state.routing.create_adapter = factory
    assert (await client.post("/api/generate", json={"prompt": "   "})).status_code == 422
    assert (await client.post("/api/generate", json={"prompt": "x" * 50_001})).status_code == 422
    assert called is False


@pytest.mark.asyncio
async def test_stream_emits_normalized_events(client: AsyncClient, app: FastAPI) -> None:
    enable(app, "openai")
    app.state.routing.create_adapter = lambda config: FakeAdapter(config, "success")
    response = await client.post("/api/generate/stream", json={"prompt": "hello", "stream": True})
    assert response.status_code == 200
    assert "event: start" in response.text
    assert "event: chunk" in response.text
    assert "event: done" in response.text


@pytest.mark.asyncio
async def test_stream_failure_emits_structured_error_event(client: AsyncClient, app: FastAPI) -> None:
    enable(app, "openai")
    app.state.routing.create_adapter = lambda config: FakeAdapter(config, "offline")
    response = await client.post("/api/generate/stream", json={"prompt": "hello", "stream": True})
    assert response.status_code == 200
    assert "event: error" in response.text
    assert '"code": "offline"' in response.text


def test_error_classification_and_presets() -> None:
    assert classify_http_error(429, "quota exceeded").kind is ErrorKind.QUOTA
    assert classify_http_error(429, "slow down").kind is ErrorKind.RATE_LIMIT
    assert classify_http_error(503).kind is ErrorKind.UPSTREAM
    assert PRESETS
    assert PRESETS[next(kind for kind in PRESETS if kind.value == "deepseek")].startswith("https://")
    assert PRESETS[next(kind for kind in PRESETS if kind.value == "qwen")].startswith("https://")


@pytest.mark.asyncio
async def test_openrouter_discovers_models_dynamically(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        is_error = False
        status_code = 200

        def json(self) -> dict[str, object]:
            return {"data": [{"id": "z/model"}, {"id": "a/model"}]}

    class Client:
        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, *args: object, **kwargs: object) -> Response:
            return Response()

    monkeypatch.setattr("ai_gateway.providers.http.httpx.AsyncClient", lambda **kwargs: Client())
    config = ProviderConfig(
        id="router-test",
        name="OpenRouter",
        kind=ProviderKind.OPENROUTER,
        model="default/model",
    )
    assert await OpenAICompatibleAdapter(config).models() == ["a/model", "z/model"]


def test_schema_never_contains_content_columns(app: FastAPI) -> None:
    connection: sqlite3.Connection = app.state.database.connection
    columns = {row[1] for table in ("requests", "attempts") for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    assert {"prompt", "response", "content", "api_key"}.isdisjoint(columns)


@pytest.mark.asyncio
async def test_stream_cancellation_reaches_provider(app: FastAPI) -> None:
    enable(app, "openai")

    class HangingAdapter(FakeAdapter):
        async def stream(self, prompt: str, model: str | None = None) -> AsyncIterator[str]:
            try:
                yield "first"
                await asyncio.sleep(60)
                yield "never"
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    provider = app.state.database.get_provider("openai")
    assert provider
    adapter = HangingAdapter(provider, "success")
    app.state.routing.create_adapter = lambda config: adapter
    result = await app.state.routing.stream(GenerateRequest(prompt="hello", stream=True))
    iterator = result.chunks.__aiter__()
    assert await anext(iterator) == "first"
    pending = asyncio.create_task(anext(iterator))
    await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert adapter.cancelled is True


@pytest.mark.asyncio
async def test_codex_timeout_kills_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    class Process:
        returncode: int | None = None
        killed = False

        async def communicate(self, value: bytes) -> tuple[bytes, bytes]:
            await asyncio.sleep(60)
            return b"", b""

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        async def wait(self) -> int:
            return self.returncode or 0

    process = Process()

    async def create(*args: object, **kwargs: object) -> Process:
        assert "exec" in args
        assert "--json" in args
        assert "--strict-config" in args
        assert "--skip-git-repo-check" in args
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    config = ProviderConfig(
        id="codex-test",
        name="Codex",
        kind=ProviderKind.CODEX,
        model="gpt-test",
        timeout_seconds=0.01,
    )
    with pytest.raises(ProviderError) as failure:
        await CodexAdapter(config).generate("secret")
    assert failure.value.kind is ErrorKind.TIMEOUT
    assert process.killed is True


def test_codex_json_and_text_output_are_normalized() -> None:
    assert _parse_output('{"type":"item.completed","item":{"type":"agent_message","text":"answer"}}') == "answer"
    with pytest.raises(ProviderError):
        _parse_output('{"item":{"type":"agent_message","text":"partial"}}')
    with pytest.raises(ProviderError):
        _parse_output("plain answer")
    assert classify_cli_error("quota exceeded").kind is ErrorKind.QUOTA
    assert classify_cli_error("429 rate limit").kind is ErrorKind.RATE_LIMIT
    assert classify_cli_error("network unavailable").kind is ErrorKind.OFFLINE


def test_credentials_are_encrypted_and_require_master_key(tmp_path: Path) -> None:
    directory = tmp_path
    path = directory / "credentials.enc"
    store = CredentialStore(path, Fernet.generate_key().decode())
    store.set("openai", "super-secret-key")
    assert store.get("openai") == "super-secret-key"
    assert b"super-secret-key" not in path.read_bytes()
    with pytest.raises(RuntimeError):
        CredentialStore(directory / "other.enc", None).set("openai", "secret")


@pytest.mark.asyncio
async def test_provider_api_encrypts_write_only_credentials_and_deletes_them(client: AsyncClient, app: FastAPI, tmp_path: Path) -> None:
    credential_path = tmp_path / "api-credentials.enc"
    store = CredentialStore(credential_path, Fernet.generate_key().decode())
    app.state.credentials = store
    payload = {
        "id": "custom-provider",
        "name": "Custom provider",
        "kind": "openai-compatible",
        "enabled": True,
        "priority": 5,
        "base_url": "http://localhost:9999/v1",
        "model": "custom-model",
        "api_key_env": None,
        "timeout_seconds": 30,
        "supports_stream": True,
        "api_key": "create-secret",
    }
    created = await client.post("/api/providers", json=payload)
    assert created.status_code == 201
    assert "api_key" not in created.json()
    assert "create-secret" not in created.text
    assert store.get("custom-provider") == "create-secret"
    assert b"create-secret" not in credential_path.read_bytes()

    payload["api_key"] = "replacement-secret"
    updated = await client.put("/api/providers/custom-provider", json=payload)
    assert updated.status_code == 200
    assert "api_key" not in updated.json()
    assert "replacement-secret" not in updated.text
    assert store.get("custom-provider") == "replacement-secret"

    listed = await client.get("/api/providers")
    assert "create-secret" not in listed.text
    assert "replacement-secret" not in listed.text
    deleted = await client.delete("/api/providers/custom-provider")
    assert deleted.status_code == 204
    assert store.get("custom-provider") is None
