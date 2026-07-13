from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./medify.db"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "medify-development-secret-change-me"
    field_encryption_key: str = ""
    access_token_minutes: int = 30
    refresh_token_days: int = 7
    demo_mode: bool = False
    environment: str = "production"
    allowed_origins: str = ""
    cookie_secure: bool = True
    login_max_attempts: int = 5
    login_lock_minutes: int = 15
    rate_limit_per_minute: int = 120
    data_region: str = "saudi-arabia"
    support_access_enabled: bool = False
    public_registration_enabled: bool = False
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def origins(self) -> list[str]:
        return [value.strip() for value in self.allowed_origins.split(",") if value.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
