"""Run the controller with ``python -m app``."""

from __future__ import annotations

import logging

import uvicorn

from app.config import ConfigError, load_config
from app.main import create_app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = load_config()
    except ConfigError as exc:
        raise SystemExit(f"configuration error: {exc}") from exc

    app = create_app(config)
    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
        log_level="info",
        timeout_graceful_shutdown=config.server.shutdown_timeout,
    )


if __name__ == "__main__":
    main()
