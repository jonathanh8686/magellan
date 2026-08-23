"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class Config:
    token: str
    command_prefix: str
    guild_id: int | None
    traveler_role_id: int | None
    db_path: str
    anthropic_api_key: str | None

    @classmethod
    def from_env(cls) -> Config:
        token = os.getenv("DISCORD_TOKEN")
        if not token:
            raise ConfigError("DISCORD_TOKEN is not set (see .env.example)")

        guild_id_raw = os.getenv("DEV_GUILD_ID")
        guild_id = int(guild_id_raw) if guild_id_raw else None

        traveler_role_id_raw = os.getenv("TRAVELER_ROLE_ID")
        traveler_role_id = int(traveler_role_id_raw) if traveler_role_id_raw else None

        return cls(
            token=token,
            command_prefix=os.getenv("COMMAND_PREFIX", "!"),
            guild_id=guild_id,
            traveler_role_id=traveler_role_id,
            db_path=os.getenv("DB_PATH", "data/magellan.db"),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        )
