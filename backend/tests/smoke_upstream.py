import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI()


@app.get("/v1/models")
async def models() -> dict[str, object]:
    return {"data": [{"id": "smoke-model"}]}


@app.post("/v1/chat/completions", response_model=None)
async def completions(payload: dict[str, Any]) -> dict[str, object] | StreamingResponse:
    prompt = str(payload["messages"][0]["content"])
    if not payload.get("stream"):
        return {"choices": [{"message": {"content": f"echo:{prompt}"}}]}

    async def events() -> AsyncIterator[str]:
        chunk = {"choices": [{"delta": {"content": f"echo:{prompt}"}}]}
        yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")
