from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://eve:eve@localhost:5432/eve"
    jwt_secret: str = "change-me-in-production-please"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    webhook_secret: str = "dev-webhook-secret"
    # Comma separated emails that get is_admin=True on signup (bootstrap only).
    admin_emails: str = ""
    # Probability the mock gateway approves a payment when no outcome is forced.
    mock_payment_success_rate: float = 0.8
    log_level: str = "INFO"

    @property
    def admin_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
