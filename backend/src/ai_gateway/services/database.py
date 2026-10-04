import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any

from ai_gateway.models import (
    AttemptPublic,
    ErrorKind,
    Metrics,
    ProviderConfig,
    ProviderKind,
    RequestRecord,
    RoutingSettings,
    RuntimeSettings,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS providers (
  id TEXT PRIMARY KEY, config_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS requests (
  id TEXT PRIMARY KEY, requested_provider TEXT, selected_provider TEXT, model TEXT,
  status TEXT NOT NULL, latency_ms INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL, provider_id TEXT NOT NULL,
  provider_name TEXT NOT NULL, success INTEGER NOT NULL, latency_ms INTEGER NOT NULL,
  error_kind TEXT, error_message TEXT, FOREIGN KEY(request_id) REFERENCES requests(id)
);
CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY, value_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_requests_created_at ON requests(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_attempts_request_id ON attempts(request_id);
"""


DEFAULT_PROVIDERS = [
    ProviderConfig(id="codex", name="Codex CLI", kind=ProviderKind.CODEX, enabled=False, priority=10, model="gpt-5", supports_stream=False),
    ProviderConfig(id="openai", name="OpenAI", kind=ProviderKind.OPENAI, enabled=False, priority=20, model="gpt-5", api_key_env="OPENAI_API_KEY"),
    ProviderConfig(
        id="gemini",
        name="Gemini",
        kind=ProviderKind.GEMINI,
        enabled=False,
        priority=30,
        model="gemini-2.5-flash",
        api_key_env="GEMINI_API_KEY",
        supports_stream=False,
    ),
    ProviderConfig(
        id="ollama",
        name="Ollama",
        kind=ProviderKind.OLLAMA,
        enabled=False,
        priority=40,
        model="llama3.2",
        base_url="http://localhost:11434",
        supports_stream=False,
    ),
    ProviderConfig(
        id="anthropic",
        name="Anthropic",
        kind=ProviderKind.ANTHROPIC,
        enabled=False,
        priority=50,
        model="claude-sonnet-4-5",
        api_key_env="ANTHROPIC_API_KEY",
        supports_stream=False,
    ),
    ProviderConfig(
        id="openrouter", name="OpenRouter", kind=ProviderKind.OPENROUTER, enabled=False, priority=60, model="openai/gpt-5", api_key_env="OPENROUTER_API_KEY"
    ),
    ProviderConfig(
        id="deepseek", name="DeepSeek", kind=ProviderKind.DEEPSEEK, enabled=False, priority=70, model="deepseek-chat", api_key_env="DEEPSEEK_API_KEY"
    ),
    ProviderConfig(id="qwen", name="Qwen", kind=ProviderKind.QWEN, enabled=False, priority=80, model="qwen-plus", api_key_env="DASHSCOPE_API_KEY"),
]


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.lock = RLock()
        with self.lock:
            self.connection.execute("PRAGMA busy_timeout = 30000")
            self.connection.execute("PRAGMA journal_mode = WAL")
            self.connection.executescript(SCHEMA)
            self.connection.commit()
        self.retention_days = 30
        self._seed()

    def close(self) -> None:
        self.connection.close()

    def _seed(self) -> None:
        if self.list_providers():
            return
        for provider in DEFAULT_PROVIDERS:
            self.upsert_provider(provider)

    def list_providers(self) -> list[ProviderConfig]:
        with self.lock:
            rows = self.connection.execute("SELECT config_json FROM providers").fetchall()
        return sorted(
            (ProviderConfig.model_validate_json(row["config_json"]) for row in rows),
            key=lambda item: item.priority,
        )

    def get_provider(self, provider_id: str) -> ProviderConfig | None:
        with self.lock:
            row = self.connection.execute("SELECT config_json FROM providers WHERE id = ?", (provider_id,)).fetchone()
        return ProviderConfig.model_validate_json(row["config_json"]) if row else None

    def upsert_provider(self, provider: ProviderConfig) -> ProviderConfig:
        with self.lock:
            self.connection.execute(
                "INSERT INTO providers(id, config_json) VALUES(?, ?) ON CONFLICT(id) DO UPDATE SET config_json=excluded.config_json",
                (provider.id, provider.model_dump_json()),
            )
            self.connection.commit()
        return provider

    def insert_provider(self, provider: ProviderConfig) -> ProviderConfig:
        with self.lock:
            try:
                self.connection.execute(
                    "INSERT INTO providers(id, config_json) VALUES(?, ?)",
                    (provider.id, provider.model_dump_json()),
                )
                self.connection.commit()
            except sqlite3.IntegrityError:
                self.connection.rollback()
                raise
        return provider

    def replace_provider(self, provider: ProviderConfig) -> ProviderConfig | None:
        with self.lock:
            cursor = self.connection.execute(
                "UPDATE providers SET config_json=? WHERE id=?",
                (provider.model_dump_json(), provider.id),
            )
            self.connection.commit()
        return provider if cursor.rowcount else None

    def delete_provider(self, provider_id: str) -> bool:
        with self.lock:
            cursor = self.connection.execute("DELETE FROM providers WHERE id = ?", (provider_id,))
            self.connection.commit()
        return cursor.rowcount > 0

    def get_setting(self, key: str, default: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            row = self.connection.execute("SELECT value_json FROM app_settings WHERE key = ?", (key,)).fetchone()
        return dict(json.loads(row["value_json"])) if row else default

    def set_setting(self, key: str, value: dict[str, Any]) -> None:
        with self.lock:
            self.connection.execute(
                "INSERT INTO app_settings(key, value_json) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                (key, json.dumps(value)),
            )
            self.connection.commit()

    def routing(self) -> RoutingSettings:
        return RoutingSettings.model_validate(self.get_setting("routing", {}))

    def set_routing(self, settings: RoutingSettings) -> RoutingSettings:
        self.set_setting("routing", settings.model_dump())
        return settings

    def runtime(self, defaults: RuntimeSettings) -> RuntimeSettings:
        return RuntimeSettings.model_validate(self.get_setting("runtime", defaults.model_dump()))

    def set_runtime(self, settings: RuntimeSettings) -> RuntimeSettings:
        self.set_setting("runtime", settings.model_dump())
        return settings

    def add_request(
        self,
        request_id: str,
        requested_provider: str | None,
        selected_provider: str | None,
        model: str | None,
        status: str,
        latency_ms: int,
        attempts: list[AttemptPublic],
    ) -> None:
        created_at = datetime.now(UTC).isoformat()
        with self.lock:
            self.connection.execute(
                "INSERT INTO requests VALUES(?, ?, ?, ?, ?, ?, ?)",
                (request_id, requested_provider, selected_provider, model, status, latency_ms, created_at),
            )
            self.connection.executemany(
                "INSERT INTO attempts(request_id, provider_id, provider_name, success, latency_ms, error_kind, error_message) VALUES(?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        request_id,
                        attempt.provider_id,
                        attempt.provider_name,
                        int(attempt.success),
                        attempt.latency_ms,
                        attempt.error_kind.value if attempt.error_kind else None,
                        attempt.error_message,
                    )
                    for attempt in attempts
                ],
            )
            self.connection.commit()
        self.prune(self.retention_days)

    def list_requests(self, limit: int = 100) -> list[RequestRecord]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT r.*, COUNT(a.id) AS attempt_count FROM requests r "
                "LEFT JOIN attempts a ON a.request_id=r.id GROUP BY r.id "
                "ORDER BY r.created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [RequestRecord(**dict(row)) for row in rows]

    def get_request(self, request_id: str) -> RequestRecord | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT r.*, COUNT(a.id) AS attempt_count FROM requests r "
                "LEFT JOIN attempts a ON a.request_id=r.id WHERE r.id=? GROUP BY r.id",
                (request_id,),
            ).fetchone()
        return RequestRecord(**dict(row)) if row else None

    def configure_retention(self, retention_days: int) -> None:
        self.retention_days = retention_days

    def attempts_for(self, request_id: str) -> list[AttemptPublic]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT provider_id, provider_name, success, latency_ms, error_kind, error_message FROM attempts WHERE request_id=? ORDER BY id", (request_id,)
            ).fetchall()
        return [
            AttemptPublic(
                provider_id=row["provider_id"],
                provider_name=row["provider_name"],
                success=bool(row["success"]),
                latency_ms=row["latency_ms"],
                error_kind=ErrorKind(row["error_kind"]) if row["error_kind"] else None,
                error_message=row["error_message"],
            )
            for row in rows
        ]

    def metrics(self) -> Metrics:
        with self.lock:
            summary = self.connection.execute(
                "SELECT COUNT(*) total, SUM(status='success') successful, SUM(status!='success') failed, COALESCE(AVG(latency_ms),0) average FROM requests"
            ).fetchone()
            fallback = self.connection.execute("SELECT COUNT(*) count FROM (SELECT request_id FROM attempts GROUP BY request_id HAVING COUNT(*)>1)").fetchone()[
                "count"
            ]
            provider_rows = self.connection.execute(
                "SELECT provider_id, COUNT(*) attempts, SUM(success) successes, "
                "COALESCE(AVG(latency_ms),0) average_latency_ms FROM attempts GROUP BY provider_id"
            ).fetchall()
        return Metrics(
            total_requests=summary["total"],
            successful_requests=summary["successful"] or 0,
            failed_requests=summary["failed"] or 0,
            fallback_requests=fallback,
            average_latency_ms=round(float(summary["average"]), 2),
            providers={
                row["provider_id"]: {
                    "attempts": row["attempts"],
                    "successes": row["successes"],
                    "average_latency_ms": round(float(row["average_latency_ms"]), 2),
                }
                for row in provider_rows
            },
        )

    def prune(self, retention_days: int) -> int:
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
        with self.lock:
            ids = [row["id"] for row in self.connection.execute("SELECT id FROM requests WHERE created_at < ?", (cutoff,)).fetchall()]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                self.connection.execute(f"DELETE FROM attempts WHERE request_id IN ({placeholders})", ids)
                self.connection.execute(f"DELETE FROM requests WHERE id IN ({placeholders})", ids)
                self.connection.commit()
        return len(ids)
