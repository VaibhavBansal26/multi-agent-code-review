from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    # GitHub
    github_webhook_secret: str = Field(default="dev-secret")
    github_app_id: str = Field(default="")
    github_app_private_key_path: str = Field(default="./secrets/github-app.pem")
    github_token: str = Field(default="")

    # Anthropic
    anthropic_api_key: str = Field(default="")

    # Database
    database_url: str = Field(default="postgresql+asyncpg://macrv:macrv@localhost:5432/macrv")
    database_url_sync: str = Field(default="postgresql://macrv:macrv@localhost:5432/macrv")

    # Redis / Celery
    redis_url: str = Field(default="redis://localhost:6379/0")
    celery_broker_url: str = Field(default="redis://localhost:6379/0")
    celery_result_backend: str = Field(default="redis://localhost:6379/1")

    # Email
    sendgrid_api_key: str = Field(default="")
    email_from: str = Field(default="reviews@macrv.dev")
    email_from_name: str = Field(default="MACRV Code Review")

    # Slack
    slack_webhook_url: str = Field(default="")

    # Jira
    jira_url: str = Field(default="")
    jira_email: str = Field(default="")
    jira_token: str = Field(default="")
    jira_project_key: str = Field(default="ENG")

    # Linear
    linear_api_key: str = Field(default="")
    linear_team_id: str = Field(default="")

    # Observability
    wandb_api_key: str = Field(default="")
    weave_project: str = Field(default="macrv")

    # Models
    orchestrator_model: str = Field(default="claude-opus-5-5")
    worker_model: str = Field(default="claude-sonnet-4-6")
    classifier_model: str = Field(default="claude-haiku-5-5")

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
