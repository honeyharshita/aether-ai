"""
AetherAI configuration.

All configuration is environment-driven (Section 62 of the spec).
No secrets are hardcoded; defaults here are for local development only
and mirror .env.example.
"""
import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://postgres:aetherpass@localhost:5432/aetherai",
    )
    JWT_SECRET: str = os.getenv("JWT_SECRET", "dev-secret-change-me-in-prod")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")  # optional, raises GitHub rate limit
    GITHUB_CLIENT_ID: str = os.getenv("GITHUB_CLIENT_ID", "")
    GITHUB_CLIENT_SECRET: str = os.getenv("GITHUB_CLIENT_SECRET", "")
    GITHUB_REDIRECT_URI: str = os.getenv(
        "GITHUB_REDIRECT_URI", "http://localhost:8000/api/v1/auth/github/callback"
    )
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:8080")
    JIRA_CLIENT_ID: str = os.getenv("JIRA_CLIENT_ID", "")
    JIRA_CLIENT_SECRET: str = os.getenv("JIRA_CLIENT_SECRET", "")
    SONAR_URL: str = os.getenv("SONAR_URL", "")
    SONAR_TOKEN: str = os.getenv("SONAR_TOKEN", "")
    ZAP_URL: str = os.getenv("ZAP_URL", "")
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME: str = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM_EMAIL: str = os.getenv("SMTP_FROM_EMAIL", "")
    SMTP_STARTTLS: bool = os.getenv("SMTP_STARTTLS", "true").lower() == "true"
    ENV: str = os.getenv("ENV", "dev")
    MODEL_ARTIFACT_DIR: str = os.getenv("MODEL_ARTIFACT_DIR", "./model_artifacts")

    class Config:
        env_file = os.path.join(os.path.dirname(__file__), "..", "..", ".env")


settings = Settings()
