import os
from typing import Any, List, Literal
from dotenv import find_dotenv, load_dotenv
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Automatically load .env variables into process os.environ for LangChain/LangSmith AI tracing
load_dotenv(find_dotenv())


class Settings(BaseSettings):
    PROJECT_NAME: str = "Migraflow API"
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"
    SECRET_KEY: str = "default_secret_key_change_me_in_production"
    JWT_SECRET_KEY: str = "default_jwt_secret_key_change_me_in_production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    COOKIE_SECURE: bool = False  # Set to True in production (HTTPS)
    COOKIE_SAMESITE: Literal["lax", "none", "strict"] = "lax"
    COOKIE_DOMAIN: str | None = None

    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/v1/auth/google/callback"
    FRONTEND_URL: str = "http://localhost:3000"
    BACKEND_URL: str = "http://host.docker.internal:8000"
    AGENT_DOCKER_IMAGE: str = "data-migration-agent:latest"

    API_V1_STR: str = "/api/v1"

    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres_password@localhost:5432/migration_platform"
    REDIS_URL: str = "redis://redis:6379/0"

    GEMINI_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    LLM_PROVIDER: Literal["gemini", "openai"] = "gemini"
    LLM_MODEL: str = "gemini-3.5-flash-lite"
    LLM_TEMPERATURE: float = 0.0
    LLM_MAX_RETRIES: int = 2
    LLM_TIMEOUT_SECONDS: float = 360.0

    # LangSmith AI Observability & Error Tracing
    LANGCHAIN_TRACING_V2: str = "false"
    LANGCHAIN_API_KEY: str = ""
    LANGCHAIN_PROJECT: str = "migraflow-platform"

    MAX_CLOUD_ROWS: int = 500_000
    MAX_CLOUD_SIZE_MB: float = 100.0

    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Any) -> List[str]:
        if isinstance(v, str):
            v_strip = v.strip()
            if v_strip.startswith("["):
                import json
                try:
                    return json.loads(v_strip)
                except Exception:
                    pass
            return [i.strip() for i in v.split(",") if i.strip()]
        return v

    model_config = SettingsConfigDict(
        env_file=("../../.env", "../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
