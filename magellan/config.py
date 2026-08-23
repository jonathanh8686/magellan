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

    @classmethod
    def from_env(cls) -> Config:
        token = os.getenv("DISCORD_TOKEN")
        if not token:
            raise ConfigError("DISCORD_TOKEN is not set (see .env.example)")

        guild_id_raw = os.getenv("DEV_GUILD_ID")
        guild_id = int(guild_id_raw) if guild_id_raw else None

        return cls(
            token=token,
            command_prefix=os.getenv("COMMAND_PREFIX", "!"),
            guild_id=guild_id,
        )
