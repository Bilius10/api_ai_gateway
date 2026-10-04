import os

from ai_gateway.config import Settings
from ai_gateway.models import ProviderConfig, ProviderKind
from ai_gateway.providers.base import ProviderAdapter
from ai_gateway.providers.codex import CodexAdapter
from ai_gateway.providers.http import (
    AnthropicAdapter,
    GeminiAdapter,
    OllamaAdapter,
    OpenAICompatibleAdapter,
)
from ai_gateway.services.secrets import CredentialStore


class ProviderRegistry:
    def __init__(self, settings: Settings, credentials: CredentialStore) -> None:
        self.settings = settings
        self.credentials = credentials

    def create(self, config: ProviderConfig) -> ProviderAdapter:
        if config.kind is ProviderKind.CODEX:
            return CodexAdapter(
                config,
                self.settings.codex_executable,
                self.settings.codex_workspace,
            )
        if config.kind is ProviderKind.OLLAMA:
            return OllamaAdapter(config, None)
        allowed = set(self.settings.allowed_api_key_envs)
        secret = (
            os.getenv(config.api_key_env, "")
            if config.api_key_env and config.api_key_env in allowed
            else ""
        )
        api_key = secret or self.credentials.get(config.id)
        if config.kind is ProviderKind.ANTHROPIC:
            return AnthropicAdapter(config, api_key)
        if config.kind is ProviderKind.GEMINI:
            return GeminiAdapter(config, api_key)
        return OpenAICompatibleAdapter(config, api_key)
