from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ai_gateway.app import create_app
from ai_gateway.config import Settings


@pytest.fixture
async def app(tmp_path: Path) -> AsyncIterator[FastAPI]:
    instance = create_app(
        Settings(database_path=tmp_path / "test.db", credential_file=tmp_path / "secrets.enc")
    )
    async with instance.router.lifespan_context(instance):
        yield instance


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
