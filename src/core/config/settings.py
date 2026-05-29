from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DeploymentMode = Literal["local", "private", "cloud"]
ErrorHttpStatusMode = Literal["standard", "always_200"]
TaskQueueProviderMode = Literal["sync", "database"]


class AppSettings(BaseModel):
    name: str = "FastAPI Core Framework"
    version: str = "0.1.0"
    env: DeploymentMode = "local"
    debug: bool = False


class ApiSettings(BaseModel):
    prefix: str = "/api/v1"
    error_http_status_mode: ErrorHttpStatusMode = "standard"


class SecuritySettings(BaseModel):
    jwt_secret: str = "change-me"
    jwt_secret_ref: str | None = None
    cors_origins: list[str] = Field(default_factory=list)
    trusted_hosts: list[str] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "testserver"]
    )
    max_request_body_bytes: int | None = 10 * 1024 * 1024


class DatabaseSettings(BaseModel):
    url: str = "sqlite+aiosqlite:///./data/local.db"


class ObservabilitySettings(BaseModel):
    service_role: str = "server"
    instance_id: str | None = None
    metrics_enabled: bool = True


class TaskQueueSettings(BaseModel):
    provider: TaskQueueProviderMode = "sync"
    max_attempts: int = 3
    retry_backoff_seconds: int = 30
    idle_sleep_seconds: float = 1.0


class SchedulerSettings(BaseModel):
    idle_sleep_seconds: float = 1.0
    lock_ttl_seconds: int = 60


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        env_file=".env",
        extra="ignore",
    )

    app: AppSettings = Field(default_factory=AppSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    task_queue: TaskQueueSettings = Field(default_factory=TaskQueueSettings)
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)
    installed_apps: list[str] = Field(default_factory=list)


def validate_startup_settings(settings: Settings) -> None:
    if settings.app.env in {"private", "cloud"} and settings.security.jwt_secret == "change-me":
        raise ValueError("Production-like profiles require SECURITY__JWT_SECRET to be changed")
    if settings.api.error_http_status_mode == "always_200" and settings.app.env == "cloud":
        raise ValueError("Cloud profile must use standard HTTP status mode")


@lru_cache
def get_settings() -> Settings:
    return Settings()
