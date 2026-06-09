"""Application configuration loaded from environment variables.

Purpose: single typed settings object for the whole app, sourced from the
``.env`` contract documented in ``.env.example``. Created 2026-06-09.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings.

    Values are read from the process environment (and an optional ``.env``
    file). Defaults mirror ``.env.example`` so the app boots without a
    populated environment during development and tests.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_port: int = 8742
    database_url: str = "sqlite:////data/fmf.db"
    fetch_mode: str = "local"
    scrape_delay_seconds: float = 1.5
    cache_ttl_hours: int = 12
    eur_to_gbp: float = 0.85

    traveller_a_name: str = "Lucas"
    traveller_a_origin: str = "MAN"
    traveller_b_name: str = "Talita"
    traveller_b_origins: list[str] = ["LIS", "OPO", "FAO"]

    @field_validator("traveller_b_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Allow a comma-separated string for the B-origins env var."""
        if isinstance(value, str):
            return [part.strip().upper() for part in value.split(",") if part.strip()]
        return value

    @property
    def db_path(self) -> Path:
        """Filesystem path to the SQLite database file.

        Parses the ``sqlite:///`` URL form into a concrete path. Falls back
        to a local ``data/fmf.db`` when the URL is not a sqlite file URL.
        """
        url = self.database_url
        prefix = "sqlite:///"
        if url.startswith(prefix):
            # Three slashes -> relative path; four slashes -> absolute path.
            return Path(url[len(prefix) :])
        return Path("data/fmf.db")


@lru_cache
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance."""
    return Settings()
