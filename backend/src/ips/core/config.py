from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://ips:ips@localhost:5432/ips"

    app_env: str = "development"
    web_origin: str = "http://localhost:5173"
    cookie_secure: bool = False
    session_secret: str = "development-only-change-me-before-production"

    @model_validator(mode="after")
    def validate_production(self):
        if self.app_env == "production" and (
            not self.cookie_secure
            or len(self.session_secret) < 32
            or self.session_secret.startswith("development-only")
        ):
            raise ValueError("Production requires a strong session secret and secure cookies")
        return self
