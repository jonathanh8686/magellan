"""Entrypoint: `uv run magellan` or `python -m magellan`."""

from __future__ import annotations

import logging

from magellan.bot import MagellanBot
from magellan.config import Config, ConfigError


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        config = Config.from_env()
    except ConfigError as exc:
        logging.getLogger("magellan").error(str(exc))
        raise SystemExit(1) from exc

    bot = MagellanBot(config)
    bot.run(config.token, log_handler=None)


if __name__ == "__main__":
    main()
