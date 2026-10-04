import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ai_gateway.models import ErrorKind, ProviderKind, ProviderResult
from ai_gateway.providers.base import (
    ProviderAdapter,
    ProviderError,
    classify_http_error,
    classify_transport_error,
    invalid_response,
)

PRESETS: dict[ProviderKind, str] = {
    ProviderKind.OPENAI: "https://api.openai.com/v1",
    ProviderKind.OPENROUTER: "https://openrouter.ai/api/v1",
    ProviderKind.DEEPSEEK: "https://api.deepseek.com/v1",
    ProviderKind.QWEN: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
}


class OpenAICompatibleAdapter(ProviderAdapter):
    def _base_url(self) -> str:
        value = self.config.base_url or PRESETS.get(self.config.kind)
        if not value:
            raise ProviderError(
                ErrorKind.INVALID_REQUEST,
                "Provider endpoint is not configured",
                recoverable=False,
                status_code=400,
            )
        return value.rstrip("/")

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _client(self, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=timeout or self.config.timeout_seconds,
            transport=self.transport,
        )

    async def generate(self, prompt: str, model: str | None = None) -> ProviderResult:
        selected_model = model or self.config.model
        try:
            async with self._client() as client:
                response = await client.post(
                    f"{self._base_url()}/chat/completions",
                    headers=self._headers(),
                    json={
                        "model": selected_model,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
            _raise_for_status(response)
            data = _json_object(response)
            choices = data.get("choices")
            first = choices[0] if isinstance(choices, list) and choices else None
            message = first.get("message") if isinstance(first, dict) else None
            text = message.get("content") if isinstance(message, dict) else None
            if not isinstance(text, str) or not text:
                raise invalid_response()
            return ProviderResult(text=text, model=selected_model)
        except ProviderError:
            raise
        except Exception as exc:
            raise classify_transport_error(exc) from exc

    async def stream(self, prompt: str, model: str | None = None) -> AsyncIterator[str]:
        selected_model = model or self.config.model
        emitted = False
        done = False
        try:
            async with self._client() as client:
                async with client.stream(
                    "POST",
                    f"{self._base_url()}/chat/completions",
                    headers=self._headers(),
                    json={
                        "model": selected_model,
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": True,
                    },
                ) as response:
                    _raise_for_status(response)
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            done = True
                            break
                        try:
                            data = json.loads(payload)
                            choices = data.get("choices") if isinstance(data, dict) else None
                            first = choices[0] if isinstance(choices, list) and choices else None
                            delta = first.get("delta") if isinstance(first, dict) else None
                            content = delta.get("content") if isinstance(delta, dict) else None
                        except (ValueError, TypeError, KeyError, IndexError) as exc:
                            raise invalid_response() from exc
                        if content is not None and not isinstance(content, str):
                            raise invalid_response()
                        if content:
                            emitted = True
                            yield content
            if not done or not emitted:
                raise invalid_response()
        except ProviderError:
            raise
        except Exception as exc:
            raise classify_transport_error(exc) from exc

    async def health(self) -> tuple[bool, str]:
        try:
            async with self._client(min(self.config.timeout_seconds, 5)) as client:
                response = await client.get(
                    f"{self._base_url()}/models",
                    headers=self._headers(),
                )
            _raise_for_status(response)
            _model_ids(response)
            return True, "healthy"
        except ProviderError as exc:
            return False, exc.kind.value
        except Exception:
            return False, "unreachable"

    async def models(self) -> list[str]:
        if self.config.kind is not ProviderKind.OPENROUTER:
            return await super().models()
        try:
            async with self._client() as client:
                response = await client.get(
                    f"{self._base_url()}/models",
                    headers=self._headers(),
                )
            _raise_for_status(response)
            return _model_ids(response)
        except ProviderError:
            raise
        except Exception as exc:
            raise classify_transport_error(exc) from exc


class AnthropicAdapter(ProviderAdapter):
    def _base_url(self) -> str:
        return (self.config.base_url or "https://api.anthropic.com/v1").rstrip("/")

    def _headers(self) -> dict[str, str]:
        headers = {"anthropic-version": "2023-06-01", "content-type": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    def _client(self, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout or self.config.timeout_seconds, transport=self.transport)

    async def generate(self, prompt: str, model: str | None = None) -> ProviderResult:
        selected_model = model or self.config.model
        try:
            async with self._client() as client:
                response = await client.post(
                    f"{self._base_url()}/messages",
                    headers=self._headers(),
                    json={
                        "model": selected_model,
                        "max_tokens": 4096,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
            _raise_for_status(response)
            content = _json_object(response).get("content")
            if not isinstance(content, list):
                raise invalid_response()
            text = "".join(
                str(item.get("text", "")) for item in content if isinstance(item, dict)
            )
            if not text:
                raise invalid_response()
            return ProviderResult(text=text, model=selected_model)
        except ProviderError:
            raise
        except Exception as exc:
            raise classify_transport_error(exc) from exc

    async def health(self) -> tuple[bool, str]:
        try:
            async with self._client(min(self.config.timeout_seconds, 5)) as client:
                response = await client.get(f"{self._base_url()}/models", headers=self._headers())
            _raise_for_status(response)
            _model_ids(response)
            return True, "healthy"
        except ProviderError as exc:
            return False, exc.kind.value
        except Exception:
            return False, "unreachable"


class GeminiAdapter(ProviderAdapter):
    def _base_url(self) -> str:
        return (self.config.base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")

    def _client(self, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout or self.config.timeout_seconds, transport=self.transport)

    async def generate(self, prompt: str, model: str | None = None) -> ProviderResult:
        selected_model = model or self.config.model
        try:
            async with self._client() as client:
                response = await client.post(
                    f"{self._base_url()}/models/{selected_model}:generateContent",
                    params={"key": self.api_key or ""},
                    json={"contents": [{"parts": [{"text": prompt}]}]},
                )
            _raise_for_status(response)
            candidates = _json_object(response).get("candidates")
            first = candidates[0] if isinstance(candidates, list) and candidates else None
            content = first.get("content") if isinstance(first, dict) else None
            parts = content.get("parts") if isinstance(content, dict) else None
            if not isinstance(parts, list):
                raise invalid_response()
            text = "".join(
                str(item.get("text", "")) for item in parts if isinstance(item, dict)
            )
            if not text:
                raise invalid_response()
            return ProviderResult(text=text, model=selected_model)
        except ProviderError:
            raise
        except Exception as exc:
            raise classify_transport_error(exc) from exc

    async def health(self) -> tuple[bool, str]:
        try:
            async with self._client(min(self.config.timeout_seconds, 5)) as client:
                response = await client.get(
                    f"{self._base_url()}/models",
                    params={"key": self.api_key or ""},
                )
            _raise_for_status(response)
            data = _json_object(response)
            if not isinstance(data.get("models"), list):
                raise invalid_response()
            return True, "healthy"
        except ProviderError as exc:
            return False, exc.kind.value
        except Exception:
            return False, "unreachable"


class OllamaAdapter(ProviderAdapter):
    def _base_url(self) -> str:
        return (self.config.base_url or "http://localhost:11434").rstrip("/")

    def _client(self, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout or self.config.timeout_seconds, transport=self.transport)

    async def generate(self, prompt: str, model: str | None = None) -> ProviderResult:
        selected_model = model or self.config.model
        try:
            async with self._client() as client:
                response = await client.post(
                    f"{self._base_url()}/api/generate",
                    json={"model": selected_model, "prompt": prompt, "stream": False},
                )
            _raise_for_status(response)
            text = _json_object(response).get("response")
            if not isinstance(text, str) or not text:
                raise invalid_response()
            return ProviderResult(text=text, model=selected_model)
        except ProviderError:
            raise
        except Exception as exc:
            raise classify_transport_error(exc) from exc

    async def health(self) -> tuple[bool, str]:
        try:
            async with self._client(min(self.config.timeout_seconds, 5)) as client:
                response = await client.get(f"{self._base_url()}/api/tags")
            _raise_for_status(response)
            _ollama_models(response)
            return True, "healthy"
        except ProviderError as exc:
            return False, exc.kind.value
        except Exception:
            return False, "unreachable"

    async def models(self) -> list[str]:
        try:
            async with self._client() as client:
                response = await client.get(f"{self._base_url()}/api/tags")
            _raise_for_status(response)
            return _ollama_models(response)
        except ProviderError:
            raise
        except Exception as exc:
            raise classify_transport_error(exc) from exc


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_error:
        raise classify_http_error(response.status_code, _classification_hint(response))


def _classification_hint(response: httpx.Response) -> str:
    body = response.text.lower()[:1000]
    return "quota" if "quota" in body or "billing" in body else ""


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        value = response.json()
    except (ValueError, TypeError) as exc:
        raise invalid_response() from exc
    if not isinstance(value, dict):
        raise invalid_response()
    return value


def _model_ids(response: httpx.Response) -> list[str]:
    data = _json_object(response).get("data")
    if not isinstance(data, list):
        raise invalid_response()
    models = [item.get("id") for item in data if isinstance(item, dict)]
    values = sorted(item for item in models if isinstance(item, str) and item)
    if not values:
        raise invalid_response()
    return values


def _ollama_models(response: httpx.Response) -> list[str]:
    data = _json_object(response).get("models")
    if not isinstance(data, list):
        raise invalid_response()
    models = [item.get("name") for item in data if isinstance(item, dict)]
    return sorted(item for item in models if isinstance(item, str) and item)
