from functools import lru_cache

from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    statsbomb_base_url: str = "https://raw.githubusercontent.com/statsbomb/open-data/master/data/"

    database_url: PostgresDsn = PostgresDsn(
        "postgresql+psycopg://football:football_local@localhost:5433/football"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
