from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AI_GATEWAY_", extra="ignore")

    database_path: Path = Path(".data/gateway.db")
    credential_file: Path = Path(".data/secrets.enc")
    master_key: str | None = None
    max_prompt_chars: int = Field(default=50_000, ge=1)
    retention_days: int = Field(default=30, ge=1, le=3650)
    request_timeout_seconds: float = Field(default=60.0, gt=0)
    codex_executable: str = "codex"
    codex_workspace: Path = Path(".data/codex-runs")
    cors_origins: list[str] = [
        "http://wails.localhost",
        "wails://wails.localhost",
        "http://localhost",
    ]
    allowed_api_key_envs: list[str] = [
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "DEEPSEEK_API_KEY",
        "DASHSCOPE_API_KEY",
    ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
