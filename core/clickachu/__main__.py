"""Entry point: `python -m clickachu --port <p>` (PLAN §6)."""

import argparse
import logging
import os
import sys

import uvicorn

from clickachu.api.http import AppConfig, create_app
from clickachu.api.security import default_origins
from clickachu.config import get_paths
from clickachu.db.conn import Database
from clickachu.settings import SettingsStore

TOKEN_ENV = "CLICKACHU_TOKEN"
# Extra comma-separated origins, e.g. the electron-vite dev server if it isn't on :5173.
ORIGINS_ENV = "CLICKACHU_ALLOWED_ORIGINS"

log = logging.getLogger("clickachu")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="clickachu", description="Clickachu core sidecar")
    parser.add_argument("--port", type=int, default=8765, help="port to bind on 127.0.0.1")
    parser.add_argument(
        "--dev-insecure",
        action="store_true",
        help="allow starting without CLICKACHU_TOKEN (disables token auth; dev only)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    # Pop so child processes we start later never inherit it.
    token = os.environ.pop(TOKEN_ENV, None) or None
    if token is None:
        if not args.dev_insecure:
            print(f"{TOKEN_ENV} not set; refusing to start (see --dev-insecure)", file=sys.stderr)
            sys.exit(2)
        log.warning("--dev-insecure: token auth is DISABLED")

    extra_origins = [o.strip() for o in os.environ.get(ORIGINS_ENV, "").split(",")]
    paths = get_paths()
    config = AppConfig(port=args.port, token=token, allowed_origins=default_origins(extra_origins))

    db = Database(paths.db)
    settings = SettingsStore(db)
    settings.import_legacy_json(paths.root / "settings.json")
    app = create_app(config, settings=settings, auto_load_voice=True)
    # Always loopback only (PLAN §6.3).
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="info"))
    app.state.server = server
    server.run()


if __name__ == "__main__":
    main()
