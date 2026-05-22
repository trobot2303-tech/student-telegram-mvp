from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN")
    mini_app_url: str = Field(alias="MINI_APP_URL")
    admin_telegram_ids: str = Field(default="", alias="ADMIN_TELEGRAM_IDS")

    bind_nonce_ttl_seconds: int = Field(default=600, alias="BIND_NONCE_TTL_SECONDS")
    initial_fa_balance: int = Field(default=1000, alias="INITIAL_FA_BALANCE")

    database_url: str = Field(default="sqlite+aiosqlite:///./data/app.db", alias="DATABASE_URL")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    def admin_ids_set(self) -> set[int]:
        raw = (self.admin_telegram_ids or "").strip()
        if not raw:
            return set()
        out: set[int] = set()
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            out.add(int(part))
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()

