from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
Provider = Literal["mock", "xai", "ollama", "openai", "anthropic"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        extra="ignore",
        env_nested_delimiter="__",
    )

    llm_provider: Provider | None = None
    llm_model: str | None = None
    xai_api_key: str | None = None
    xai_model: str = "grok-3"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.1"
    inventory_db: Path = ROOT / "db" / "inventory.db"
    checkpoint_db: Path = ROOT / "db" / "checkpoints.db"
    output_dir: Path = ROOT / "outputs"
    high_value_usd: float = 10000.0
    fx_eur_usd: float = 1.08
    total_tolerance: float = 0.50
    require_llm: bool = False
    provider_override: Provider | None = Field(default=None, exclude=True)

    @property
    def provider(self) -> Provider:
        if self.provider_override:
            return self.provider_override
        if self.llm_provider:
            return self.llm_provider
        if self.xai_api_key:
            return "xai"
        return "mock"

    @property
    def agentic_mode(self) -> bool:
        return self.provider != "mock"

    @property
    def model_name(self) -> str:
        if self.llm_model:
            return self.llm_model
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
