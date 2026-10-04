import os

import uvicorn


def main() -> None:
    host = os.getenv("AI_GATEWAY_HOST", "0.0.0.0")
    port = int(os.getenv("AI_GATEWAY_PORT", "8000"))
    uvicorn.run(
        "ai_gateway.app:app",
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
