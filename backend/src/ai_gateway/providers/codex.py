import asyncio
import contextlib
import json
import os
import shutil
import signal
import tempfile
from collections.abc import AsyncIterator, Mapping
from pathlib import Path

from ai_gateway.models import ErrorKind, ProviderConfig, ProviderResult
from ai_gateway.providers.base import ProviderAdapter, ProviderError


class CodexAdapter(ProviderAdapter):
    def __init__(
        self,
        config: ProviderConfig,
        executable: str = "codex",
        workspace: Path = Path(".data/codex-runs"),
    ) -> None:
        super().__init__(config)
        self.executable = executable
        self.workspace = workspace

    async def generate(self, prompt: str, model: str | None = None) -> ProviderResult:
        selected_model = model or self.config.model
        run_dir = self._new_run_dir()
        args = [
            self.executable,
            "--ask-for-approval",
            "never",
            "--sandbox",
            "read-only",
            "--config",
            "features.shell_tool=false",
            "--config",
            "features.unified_exec=false",
            "--config",
            "features.apps=false",
            "--config",
            "features.browser_use=false",
            "--config",
            "features.computer_use=false",
            "--config",
            "features.goals=false",
            "--config",
            "features.hooks=false",
            "--config",
            "features.multi_agent=false",
            "--config",
            "features.plugins=false",
            "--config",
            "features.remote_plugin=false",
            "--config",
            "features.image_generation=false",
            "--config",
            "features.view_image=false",
            "--config",
            "features.workspace_dependencies=false",
            "--strict-config",
            "exec",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--model",
            selected_model,
            "--cd",
            str(run_dir),
            "-",
        ]
        process: asyncio.subprocess.Process | None = None
        try:
            if _uses_process_group():
                process = await asyncio.create_subprocess_exec(
                    *args,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=run_dir,
                    env=_isolated_environment(),
                    start_new_session=True,
                )
            else:
                process = await asyncio.create_subprocess_exec(
                    *args,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=run_dir,
                    env=_isolated_environment(),
                )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(prompt.encode()), timeout=self.config.timeout_seconds
            )
        except FileNotFoundError as exc:
            raise ProviderError(
                ErrorKind.OFFLINE, "Codex CLI is not installed", status_code=503
            ) from exc
        except TimeoutError as exc:
            await _terminate(process)
            raise ProviderError(ErrorKind.TIMEOUT, "Codex CLI timed out", status_code=504) from exc
        except asyncio.CancelledError:
            await _terminate(process)
            raise
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)
        if process.returncode != 0:
            raise classify_cli_error(stderr.decode(errors="replace"))
        return ProviderResult(text=_parse_output(stdout.decode(errors="replace")), model=selected_model)

    async def stream(self, prompt: str, model: str | None = None) -> AsyncIterator[str]:
        result = await self.generate(prompt, model)
        yield result.text

    async def health(self) -> tuple[bool, str]:
        process: asyncio.subprocess.Process | None = None
        try:
            if _uses_process_group():
                process = await asyncio.create_subprocess_exec(
                    self.executable,
                    "--version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=_isolated_environment(),
                    start_new_session=True,
                )
            else:
                process = await asyncio.create_subprocess_exec(
                    self.executable,
                    "--version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=_isolated_environment(),
                )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=5)
            return process.returncode == 0, (
                stdout.decode(errors="replace").strip()[:100] or "unavailable"
            )
        except FileNotFoundError:
            return False, "unavailable"
        except TimeoutError:
            await _terminate(process)
            return False, "unavailable"
        except asyncio.CancelledError:
            await _terminate(process)
            raise

    def _new_run_dir(self) -> Path:
        self.workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        return Path(tempfile.mkdtemp(prefix="request-", dir=self.workspace))


async def _terminate(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return
    pid = getattr(process, "pid", None)
    if os.name != "nt" and isinstance(pid, int):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pid, signal.SIGKILL)
    elif isinstance(pid, int):
        with contextlib.suppress(OSError):
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
    with contextlib.suppress(ProcessLookupError):
        process.kill()
    with contextlib.suppress(ProcessLookupError):
        await process.wait()


def _isolated_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    values = source or os.environ
    allowed = {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "TERM",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "SYSTEMROOT",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
    }
    if os.name == "nt":
        return {key: value for key, value in values.items() if key.upper() in allowed}
    return {key: value for key, value in values.items() if key in allowed}


def _uses_process_group() -> bool:
    return os.name != "nt"


def _parse_output(output: str) -> str:
    final: str | None = None
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProviderError(
                ErrorKind.UPSTREAM, "Codex CLI returned an invalid response"
            ) from exc
        if not isinstance(event, dict):
            raise ProviderError(ErrorKind.UPSTREAM, "Codex CLI returned an invalid response")
        event_type = str(event.get("type", ""))
        if event_type in {"error", "turn.failed"}:
            raise classify_cli_error(_event_error(event))
        item = event.get("item")
        if (
            event_type == "item.completed"
            and isinstance(item, dict)
            and item.get("type") == "agent_message"
        ):
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                final = text
    if final is None:
        raise ProviderError(ErrorKind.UPSTREAM, "Codex CLI returned no final answer")
    return final


def _event_error(event: dict[str, object]) -> str:
    error = event.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str):
            return message
    if isinstance(error, str):
        return error
    message = event.get("message")
    return message if isinstance(message, str) else "Codex CLI failed"


def classify_cli_error(message: str) -> ProviderError:
    lowered = message.lower()
    if "quota" in lowered or "billing" in lowered:
        return ProviderError(ErrorKind.QUOTA, "Codex quota exhausted", status_code=429)
    if "rate limit" in lowered or "429" in lowered:
        return ProviderError(ErrorKind.RATE_LIMIT, "Codex rate limit reached", status_code=429)
    if "offline" in lowered or "network" in lowered or "connection" in lowered:
        return ProviderError(ErrorKind.OFFLINE, "Codex CLI is unreachable", status_code=503)
    if "unauthorized" in lowered or "authentication" in lowered:
        return ProviderError(
            ErrorKind.AUTHENTICATION,
            "Codex authentication failed",
            recoverable=False,
            status_code=401,
        )
    return ProviderError(ErrorKind.UPSTREAM, "Codex CLI failed")
