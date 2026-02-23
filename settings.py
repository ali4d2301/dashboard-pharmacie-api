from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    DATABASE_URL: str
    CORS_ORIGINS: str = ""
    JWT_SECRET: str = "CHANGE_ME"
    JWT_ALG: str = "HS256"
    JWT_EXPIRES_MIN: int = 60 * 12  # 12h

    model_config = SettingsConfigDict(env_file=".env")

settings = Settings()
