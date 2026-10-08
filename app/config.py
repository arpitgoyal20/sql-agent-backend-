"""Application settings, read from environment variables (and `.env` if present)."""

import os
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent

# Browser origins allowed to call the API. Local dev servers on any port are also allowed
# (see `allowed_origin_regex`); ALLOWED_ORIGINS can add more without a code change.
CORS_ORIGINS = [
    "https://sql-agent-frontend-delta.vercel.app",
    "http://localhost:5173",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    llm_provider: str = "google"
    llm_model: str = "gemini-3.5-flash-lite"  # gemini-2.5-flash is closed to new API keys
    # Accept either name; Google's docs use both.
    # Optional cap on Gemini "thinking" tokens; lower is faster (e.g. 512). Unset = model default.
    llm_thinking_budget: int | None = None
    google_api_key: str = Field(
        default="", validation_alias=AliasChoices("GOOGLE_API_KEY", "GEMINI_API_KEY")
    )

    allowed_origins: str = ""  # optional extra origins, comma-separated
    # Local dev servers on any port (Vite moves to 5174, 5175... when 5173 is busy).
    allowed_origin_regex: str = r"http://(localhost|127\.0\.0\.1)(:\d+)?"
    checkpoint_db: str = "data/checkpoints.db"
    sample_db: str = "data/sample.db"
    rate_limit: str = "20/minute"

    langsmith_api_key: str = ""
    langsmith_tracing: bool = False
    langsmith_project: str = "sql-agent"
    langsmith_workspace_id: str = ""  # needed for org-scoped service keys (lsv2_sk_...)

    # Execution limits (§4.4)
    row_cap: int = 200
    query_timeout_s: float = 5.0

    @property
    def sample_db_path(self) -> Path:
        return _resolve(self.sample_db)

    @property
    def checkpoint_db_path(self) -> Path:
        return _resolve(self.checkpoint_db)

    @property
    def origins(self) -> list[str]:
        extra = [o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip()]
        return list(dict.fromkeys(CORS_ORIGINS + extra))


def _resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    # pydantic-settings reads .env without exporting it; LangSmith reads os.environ.
    if settings.langsmith_tracing and settings.langsmith_api_key:
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
        os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
        if settings.langsmith_workspace_id:
            os.environ.setdefault("LANGSMITH_WORKSPACE_ID", settings.langsmith_workspace_id)
    return settings
