"""Entrypoint: `uv run magellan` or `python -m magellan`."""

from __future__ import annotations

import argparse
import logging

from magellan.bot import MagellanBot
from magellan.config import Config, ConfigError


def main() -> None:
    parser = argparse.ArgumentParser(prog="magellan")
    parser.add_argument(
        "--reload",
        action="store_true",
        help=(
            "dev only: watch magellan/cogs/ and hot-reload a cog when its file "
            "changes, instead of restarting the whole bot"
        ),
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        config = Config.from_env()
    except ConfigError as exc:
        logging.getLogger("magellan").error(str(exc))
        raise SystemExit(1) from exc

    bot = MagellanBot(config, reload=args.reload)
    bot.run(config.token, log_handler=None)


if __name__ == "__main__":
    main()
