from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./medify.db"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "medify-development-secret-change-me"
    access_token_minutes: int = 30
    refresh_token_days: int = 7
    demo_mode: bool = True
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

