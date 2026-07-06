import argparse
import logging

import uvicorn

from app.api.app import build_app
from app.core.settings import Settings


LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the LiveChat webhook HTTP server.")
    parser.add_argument("--host", help="Host to bind. Defaults to WEBHOOK_SERVER_HOST or 0.0.0.0.")
    parser.add_argument("--port", type=int, help="Port to bind. Defaults to WEBHOOK_SERVER_PORT or 8000.")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    args = build_arg_parser().parse_args(argv)
    settings = Settings()
    app = build_app(settings=settings)
    uvicorn.run(app, host=args.host or settings.webhook_server_host, port=args.port or settings.webhook_server_port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
