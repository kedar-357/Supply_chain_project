"""Environment-based configuration. All secrets come from environment / .env, never source code."""
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    app_name: str = "SupplyChainX"
    app_env: str = "development"
    jwt_secret: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    demo_mode: bool = True
    demo_admin_password: str | None = None
    demo_manager_password: str | None = None
    demo_analyst_password: str | None = None
    # Working data (CSV tables, JSON documents, uploads, backups, generated reports)
    data_dir: Path = BACKEND_DIR / "data"
    # Original sample datasets produced by the init script; never modified by the app
    sample_dir: Path = BACKEND_DIR / "datasets" / "sample"
    # Trained model files (joblib)
    artifacts_dir: Path = BACKEND_DIR / "artifacts"
    log_level: str = "INFO"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
