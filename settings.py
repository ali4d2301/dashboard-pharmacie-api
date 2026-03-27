from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().with_name(".env")

class Settings(BaseSettings):
    DATABASE_URL: str
    CORS_ORIGINS: str = ""
    JWT_SECRET: str = "CHANGE_ME"
    JWT_ALG: str = "HS256"
    JWT_EXPIRES_MIN: int = 60 * 12  # 12h
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_FROM: str | None = None
    SMTP_USE_TLS: bool = True
    SMTP_USE_SSL: bool = False
    WEEKLY_REPORT_RECIPIENTS: str = ""
    WEEKLY_REPORT_TIMEZONE: str = "UTC"
    WEEKLY_REPORT_SUBJECT_PREFIX: str = "Pharmacie"
    WEEKLY_REPORT_ATTACH_CSV: bool = True
    EXPIRY_REPORT_RECIPIENTS: str = ""
    EXPIRY_REPORT_TIMEZONE: str = "UTC"
    EXPIRY_REPORT_SUBJECT_PREFIX: str = "Pharmacie"
    EXPIRY_REPORT_ATTACH_CSV: bool = True

    model_config = SettingsConfigDict(env_file=ENV_FILE)

settings = Settings()
