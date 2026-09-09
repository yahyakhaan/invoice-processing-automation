from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
Provider = Literal["mock", "groq", "xai", "ollama", "openai", "anthropic"]
CheckpointBackend = Literal["auto", "memory", "sqlite", "postgres"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        extra="ignore",
        env_nested_delimiter="__",
    )

    llm_provider: Provider | None = None
    llm_model: str | None = None
    groq_api_key: str | None = Field(default=None, repr=False, exclude=True)
    groq_model: str = "llama-3.3-70b-versatile"
    xai_api_key: str | None = Field(default=None, repr=False, exclude=True)
    xai_model: str = "grok-3"
    openai_api_key: str | None = Field(default=None, repr=False, exclude=True)
    anthropic_api_key: str | None = Field(default=None, repr=False, exclude=True)
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.1"
    database_url: str | None = Field(default=None, repr=False, exclude=True)
    inventory_db: Path = ROOT / "db" / "inventory.db"
    checkpoint_db: Path = ROOT / "db" / "checkpoints.db"
    checkpoint_backend: CheckpointBackend = "auto"
    langgraph_aes_key: str | None = Field(default=None, repr=False, exclude=True)
    output_dir: Path = ROOT / "outputs"
    owner_id: str = "local"
    demo_llm_daily_limit: int = Field(default=5, ge=0, le=1000)
    max_upload_mb: int = Field(default=10, ge=1)
    max_pdf_pages: int = Field(default=25, ge=1)
    high_value_usd: float = 10000.0
    fx_eur_usd: float = 1.08
    total_tolerance: float = 0.50
    require_llm: bool = False
    provider_override: Provider | None = Field(default=None, exclude=True)

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        if not value.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError("DATABASE_URL must use PostgreSQL")
        return value

    @field_validator("langgraph_aes_key")
    @classmethod
    def validate_langgraph_aes_key(cls, value: str | None) -> str | None:
        if value is None or not value:
            return None
        if len(value.encode("utf-8")) not in {16, 24, 32}:
            raise ValueError("LANGGRAPH_AES_KEY must be 16, 24, or 32 bytes")
        return value

    @property
    def provider(self) -> Provider:
        if self.provider_override:
            return self.provider_override
        if self.llm_provider:
            return self.llm_provider
        if self.groq_api_key:
            return "groq"
        if self.xai_api_key:
            return "xai"
        return "mock"

    @property
    def agentic_mode(self) -> bool:
        return self.provider != "mock"

    @property
    def uses_postgres(self) -> bool:
        return self.database_url is not None

    @property
    def resolved_checkpoint_backend(self) -> Literal["memory", "sqlite", "postgres"]:
        if self.checkpoint_backend != "auto":
            return self.checkpoint_backend
        return "postgres" if self.uses_postgres else "sqlite"

    @property
    def model_name(self) -> str:
        if self.llm_model:
            return self.llm_model
        if self.provider == "groq":
            return self.groq_model
        if self.provider == "xai":
            return self.xai_model
        if self.provider == "ollama":
            return self.ollama_model
        if self.provider == "anthropic":
            return "claude-3-5-sonnet-latest"
        if self.provider == "openai":
            return "gpt-4o-mini"
        return "mock-deterministic"

    def resolved_api_key(self) -> str | None:
        if self.provider == "groq":
            return self.groq_api_key
        if self.provider == "xai":
            return self.xai_api_key
        if self.provider == "openai":
            return self.openai_api_key
        if self.provider == "anthropic":
            return self.anthropic_api_key
        return None


def get_settings(**overrides) -> Settings:
    settings = Settings()
    for key, value in overrides.items():
        if value is not None:
            setattr(settings, key, value)
    return settings
