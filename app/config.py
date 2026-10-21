from functools import lru_cache
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse  # noqa: I001

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ASYNCPG_ALLOWED_PARAMS = {"ssl", "timeout", "statement_cache_size", "prepared_statement_cache_size"}


def _make_asyncpg_url(url: str) -> str:
    """Coerce a plain postgresql:// URL to postgresql+asyncpg://, stripping params asyncpg rejects."""
    url = url.replace("postgres://", "postgresql://", 1)
    if not url.startswith("postgresql+asyncpg://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    # Translate sslmode → ssl (asyncpg uses ssl=require, not sslmode=require)
    if "sslmode" in params:
        mode = params.pop("sslmode")[0]
        if mode in ("require", "verify-ca", "verify-full"):
            params["ssl"] = ["require"]
        elif mode == "disable":
            params["ssl"] = ["disable"]
    # Drop any param asyncpg doesn't understand (e.g. channel_binding, options, application_name)
    params = {k: v for k, v in params.items() if k in _ASYNCPG_ALLOWED_PARAMS}
    return urlunparse(parsed._replace(query=urlencode(params, doseq=True)))


def _make_psycopg2_url(url: str) -> str:
    """Coerce a URL to postgresql+psycopg2://, keeping standard params psycopg2 supports."""
    url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql+asyncpg://"):
        url = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


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

    # Connected repos (comma-separated list of owner/repo to show in dashboard)
    connected_repos: str = Field(default="")

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

    @model_validator(mode="after")
    def _fix_db_schemes(self) -> "Settings":
        self.database_url = _make_asyncpg_url(self.database_url)
        self.database_url_sync = _make_psycopg2_url(self.database_url_sync)
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
