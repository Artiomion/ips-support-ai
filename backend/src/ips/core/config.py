from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)
    database_url: str = "postgresql+psycopg://ips:ips@localhost:5432/ips"

    app_env: str = "development"
    web_origin: str = "http://localhost:5173"
    widget_origin: str = "http://localhost:5174"
    smtp_host: str = "localhost"
    smtp_port: int = Field(default=1025, ge=1, le=65535)
    smtp_from: str = "support@example.invalid"
    smtp_tls: Literal["none", "starttls", "implicit"] = "none"
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_timeout_seconds: float = Field(default=10, gt=0, le=60)
    cookie_secure: bool = False
    session_secret: str = "development-only-change-me-before-production"
    ai_runner_enabled: bool = False
    ai_provider: str = "fake"

    @model_validator(mode="after")
    def validate_production(self):
        if self.app_env == "production" and (
            not self.cookie_secure
            or len(self.session_secret) < 32
            or self.session_secret.startswith("development-only")
        ):
            raise ValueError("Production requires a strong session secret and secure cookies")
        return self

    @model_validator(mode="after")
    def validate_smtp(self):
        if bool(self.smtp_username) != bool(self.smtp_password.get_secret_value()):
            raise ValueError("SMTP credentials must be configured together")
        if self.smtp_username and self.smtp_tls == "none":
            raise ValueError("SMTP credentials require TLS")
        return self
