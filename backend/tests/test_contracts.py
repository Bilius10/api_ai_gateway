import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ai_gateway.app import create_app
from ai_gateway.config import Settings
from ai_gateway.models import ErrorKind, ProviderConfig, ProviderKind, ProviderResult
from ai_gateway.providers.base import ProviderAdapter, ProviderError
from ai_gateway.providers.http import (
    AnthropicAdapter,
    GeminiAdapter,
    OllamaAdapter,
    OpenAICompatibleAdapter,
)


def provider(kind: ProviderKind, *, provider_id: str = "contract") -> ProviderConfig:
    return ProviderConfig(
        id=provider_id,
        name=provider_id,
        kind=kind,
        model="contract-model",
        enabled=True,
        base_url="https://provider.test/v1" if kind is not ProviderKind.OLLAMA else "http://provider.test",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [ProviderKind.OPENAI, ProviderKind.OPENROUTER, ProviderKind.DEEPSEEK, ProviderKind.QWEN, ProviderKind.OPENAI_COMPATIBLE],
)
async def test_openai_compatible_contracts(kind: ProviderKind) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload["messages"] == [{"role": "user", "content": "question"}]
        return httpx.Response(200, json={"choices": [{"message": {"content": "answer"}}]})

    adapter = OpenAICompatibleAdapter(provider(kind), "test-key", httpx.MockTransport(handler))
    assert (await adapter.generate("question")).text == "answer"


@pytest.mark.asyncio
async def test_openai_compatible_stream_contract() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["stream"] is True
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"one"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":" two"}}]}\n\n'
            "data: [DONE]\n\n",
        )

    adapter = OpenAICompatibleAdapter(
        provider(ProviderKind.OPENAI), "test-key", httpx.MockTransport(handler)
    )
    assert [chunk async for chunk in adapter.stream("question")] == ["one", " two"]


@pytest.mark.asyncio
async def test_openai_compatible_stream_classifies_authentication_error() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    adapter = OpenAICompatibleAdapter(
        provider(ProviderKind.OPENAI), "bad-key", httpx.MockTransport(handler)
    )
    with pytest.raises(ProviderError) as failure:
        await anext(adapter.stream("question"))
    assert failure.value.kind is ErrorKind.AUTHENTICATION


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adapter_type", "kind", "path", "response"),
    [
        (AnthropicAdapter, ProviderKind.ANTHROPIC, "/v1/messages", {"content": [{"type": "text", "text": "answer"}]}),
        (GeminiAdapter, ProviderKind.GEMINI, "/v1/models/contract-model:generateContent", {"candidates": [{"content": {"parts": [{"text": "answer"}]}}]}),
        (OllamaAdapter, ProviderKind.OLLAMA, "/api/generate", {"response": "answer"}),
    ],
)
async def test_native_provider_contracts(
    adapter_type: type[ProviderAdapter],
    kind: ProviderKind,
    path: str,
    response: dict[str, object],
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == path
        payload = json.loads(request.content)
        assert "question" in json.dumps(payload)
        return httpx.Response(200, json=response)

    adapter = adapter_type(provider(kind), "test-key", httpx.MockTransport(handler))
    assert (await adapter.generate("question")).text == "answer"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body", "kind"),
    [(402, "billing", ErrorKind.QUOTA), (429, "slow down", ErrorKind.RATE_LIMIT), (503, "down", ErrorKind.UPSTREAM)],
)
async def test_upstream_failures_are_normalized(status: int, body: str, kind: ErrorKind) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=body)

    adapter = OpenAICompatibleAdapter(provider(ProviderKind.OPENAI), "secret-value", httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as failure:
        await adapter.generate("question")
    assert failure.value.kind is kind
    assert "secret-value" not in failure.value.message


@pytest.mark.asyncio
async def test_malformed_success_is_normalized() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    adapter = OpenAICompatibleAdapter(provider(ProviderKind.OPENAI), transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as failure:
        await adapter.generate("question")
    assert failure.value.kind is ErrorKind.UPSTREAM


class StreamAdapter(ProviderAdapter):
    def __init__(self, config: ProviderConfig, succeeds: bool) -> None:
        super().__init__(config)
        self.succeeds = succeeds

    async def generate(self, prompt: str, model: str | None = None) -> ProviderResult:
        if not self.succeeds:
            raise ProviderError(ErrorKind.OFFLINE, "offline")
        return ProviderResult(text=prompt, model=model or self.config.model)

    async def stream(self, prompt: str, model: str | None = None) -> AsyncIterator[str]:
        if not self.succeeds:
            raise ProviderError(ErrorKind.OFFLINE, "offline")
        yield f"answer:{prompt}"

    async def health(self) -> tuple[bool, str]:
        return self.succeeds, "test"


@pytest.mark.asyncio
async def test_stream_fallback_reports_actual_provider(client: AsyncClient, app: FastAPI) -> None:
    for priority, provider_id in enumerate(("openai", "gemini")):
        current = app.state.database.get_provider(provider_id)
        assert current
        app.state.database.upsert_provider(current.model_copy(update={"enabled": True, "priority": priority, "supports_stream": True}))
    app.state.routing.create_adapter = lambda config: StreamAdapter(config, config.id == "gemini")

    response = await client.post("/api/generate/stream", json={"prompt": "question", "stream": True})
    assert response.status_code == 200
    assert "answer:question" in response.text
    assert '"provider": "gemini"' in response.text
    assert response.text.count('"success": false') == 1
    assert response.text.count('"success": true') == 1
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.headers["x-accel-buffering"] == "no"


@pytest.mark.asyncio
async def test_provider_credential_lifecycle_and_allowlist(tmp_path: Path) -> None:
    settings = Settings(
        database_path=tmp_path / "gateway.db",
        credential_file=tmp_path / "secrets.enc",
        master_key=Fernet.generate_key().decode(),
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            payload = {
                "id": "custom",
                "name": "Custom",
                "kind": "openai-compatible",
                "enabled": True,
                "priority": 1,
                "base_url": "http://localhost:9000/v1",
                "model": "model",
                "api_key_env": None,
                "timeout_seconds": 10,
                "supports_stream": True,
                "api_key": "first-secret",
            }
            assert (await client.post("/api/providers", json=payload)).status_code == 201
            assert app.state.credentials.get("custom") == "first-secret"
            payload.pop("api_key")
            payload["name"] = "Updated"
            assert (await client.put("/api/providers/custom", json=payload)).status_code == 200
            assert app.state.credentials.get("custom") == "first-secret"
            payload["api_key"] = None
            assert (await client.put("/api/providers/custom", json=payload)).status_code == 200
            assert app.state.credentials.get("custom") is None
            payload["api_key_env"] = "AI_GATEWAY_MASTER_KEY"
            assert (await client.put("/api/providers/custom", json=payload)).status_code == 422
            assert (await client.post("/api/providers", json=payload)).status_code in {409, 422}


@pytest.mark.asyncio
async def test_routing_policy_and_metrics(client: AsyncClient, app: FastAPI) -> None:
    for priority, provider_id in enumerate(("openai", "gemini")):
        current = app.state.database.get_provider(provider_id)
        assert current
        app.state.database.upsert_provider(current.model_copy(update={"enabled": True, "priority": priority}))
    calls: list[str] = []
    app.state.routing.create_adapter = lambda config: (calls.append(config.id) or StreamAdapter(config, config.id == "gemini"))
    assert (await client.put("/api/routing", json={"fallback_enabled": False, "max_attempts": 5})).status_code == 200
    assert (await client.post("/api/generate", json={"prompt": "one"})).status_code == 503
    assert calls == ["openai"]
    calls.clear()
    assert (await client.put("/api/routing", json={"fallback_enabled": True, "max_attempts": 2})).status_code == 200
    assert (await client.post("/api/generate", json={"prompt": "two"})).status_code == 200
    assert calls == ["openai", "gemini"]
    metrics = (await client.get("/api/metrics")).json()
    assert metrics["total_requests"] == 2
    assert metrics["successful_requests"] == 1
    assert metrics["failed_requests"] == 1
    assert metrics["fallback_requests"] == 1


@pytest.mark.asyncio
async def test_request_detail_is_not_limited_to_latest_thousand(client: AsyncClient, app: FastAPI) -> None:
    created_at = datetime.now(UTC).isoformat()
    rows = [
        (f"bulk-{index}", None, None, None, "failed", 0, created_at)
        for index in range(1001)
    ]
    with app.state.database.lock:
        app.state.database.connection.executemany(
            "INSERT INTO requests VALUES(?, ?, ?, ?, ?, ?, ?)", rows
        )
        app.state.database.connection.commit()
    response = await client.get("/api/requests/bulk-0")
    assert response.status_code == 200
    assert response.json()["request"]["id"] == "bulk-0"


@pytest.mark.asyncio
async def test_retention_is_persisted_pruned_and_restored(tmp_path: Path) -> None:
    settings = Settings(
        database_path=tmp_path / "retention.db",
        credential_file=tmp_path / "secrets.enc",
    )
    first = create_app(settings)
    async with first.router.lifespan_context(first):
        first.state.database.add_request("old", None, None, None, "failed", 0, [])
        first.state.database.add_request("current", None, None, None, "success", 0, [])
        old_date = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        with first.state.database.lock:
            first.state.database.connection.execute(
                "UPDATE requests SET created_at=? WHERE id='old'", (old_date,)
            )
            first.state.database.connection.commit()
        async with AsyncClient(transport=ASGITransport(app=first), base_url="http://test") as client:
            response = await client.put(
                "/api/settings", json={"retention_days": 7, "max_prompt_chars": 1234}
            )
            assert response.status_code == 200
        assert first.state.database.get_request("old") is None
        assert first.state.database.get_request("current") is not None

    second = create_app(settings)
    async with second.router.lifespan_context(second):
        async with AsyncClient(transport=ASGITransport(app=second), base_url="http://test") as client:
            assert (await client.get("/api/settings")).json() == {
                "retention_days": 7,
                "max_prompt_chars": 1234,
            }
        assert second.state.database.retention_days == 7


@pytest.mark.asyncio
async def test_stream_total_timeout_is_normalized(client: AsyncClient, app: FastAPI) -> None:
    current = app.state.database.get_provider("openai")
    assert current
    configured = current.model_copy(
        update={"enabled": True, "priority": 0, "supports_stream": True, "timeout_seconds": 0.01}
    )
    app.state.database.upsert_provider(configured)

    class SlowAdapter(StreamAdapter):
        async def stream(self, prompt: str, model: str | None = None) -> AsyncIterator[str]:
            await asyncio.sleep(60)
            yield prompt

    app.state.routing.create_adapter = lambda config: SlowAdapter(config, True)
    response = await client.post(
        "/api/generate/stream", json={"prompt": "question", "provider": "openai", "stream": True}
    )
    assert response.status_code == 200
    assert '"code": "timeout"' in response.text
    record = (await client.get("/api/requests")).json()[0]
    assert record["status"] == "failed"
