import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock

from cryptography.fernet import Fernet, InvalidToken


class CredentialStore:
    """Encrypted local credential map; the master key is supplied only by the environment."""

    def __init__(self, path: Path, master_key: str | None) -> None:
        self.path = path
        self._fernet = Fernet(master_key.encode()) if master_key else None
        self._lock = RLock()

    @property
    def available(self) -> bool:
        return self._fernet is not None

    def get(self, provider_id: str) -> str | None:
        with self._lock:
            return self._read().get(provider_id)

    def set(self, provider_id: str, secret: str) -> None:
        if self._fernet is None:
            raise RuntimeError("AI_GATEWAY_MASTER_KEY is required to persist credentials")
        with self._lock:
            values = self._read()
            values[provider_id] = secret
            self._write(values)

    def delete(self, provider_id: str) -> None:
        if self._fernet is None or not self.path.exists():
            return
        with self._lock:
            values = self._read()
            if provider_id in values:
                del values[provider_id]
                self._write(values)

    def _write(self, values: dict[str, str]) -> None:
        if self._fernet is None:
            raise RuntimeError("AI_GATEWAY_MASTER_KEY is required to persist credentials")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(dir=self.path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                os.chmod(temporary, 0o600)
                handle.write(self._fernet.encrypt(json.dumps(values).encode()))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            self.path.chmod(0o600)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def _read(self) -> dict[str, str]:
        if self._fernet is None or not self.path.exists():
            return {}
        try:
            value = json.loads(self._fernet.decrypt(self.path.read_bytes()))
        except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "Credential store cannot be decrypted with the supplied master key"
            ) from exc
        return {str(key): str(secret) for key, secret in value.items()}
