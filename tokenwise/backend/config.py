from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from tokenwise.backend.models.schemas import ModelProfile, Provider, TokenPricing


class Settings(BaseSettings):
    app_name: str = "Tokenwise"
    api_host: str = Field(default="127.0.0.1", alias="TOKENWISE_API_HOST")
    api_port: int = Field(default=8000, alias="TOKENWISE_API_PORT")
    db_path: str = Field(default="tokenwise.db", alias="TOKENWISE_DB_PATH")
    daily_budget_usd: float = Field(default=10.0, alias="TOKENWISE_DAILY_BUDGET_USD")
    max_task_length: int = Field(default=2000, alias="TOKENWISE_MAX_TASK_LENGTH")
    max_concurrent_runs: int = Field(default=3, alias="TOKENWISE_MAX_CONCURRENT_RUNS")
    request_timeout_seconds: float = 90.0
    tier1_timeout_seconds: float = Field(default=15.0, alias="TOKENWISE_TIER1_TIMEOUT")
    tier2_timeout_seconds: float = Field(default=30.0, alias="TOKENWISE_TIER2_TIMEOUT")
    tier3_timeout_seconds: float = Field(default=90.0, alias="TOKENWISE_TIER3_TIMEOUT")
    tier1_max_output_tokens: int = Field(default=1500, alias="TOKENWISE_TIER1_MAX_OUTPUT_TOKENS")
    tier2_max_output_tokens: int = Field(default=3000, alias="TOKENWISE_TIER2_MAX_OUTPUT_TOKENS")
    tier3_max_output_tokens: int = Field(default=6000, alias="TOKENWISE_TIER3_MAX_OUTPUT_TOKENS")
    latency_threshold_ms: int = 18_000
    recent_runs_limit: int = 8
    cors_origins: list[str] = Field(
        default=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "https://tokenwise-production.up.railway.app",
        ],
        alias="TOKENWISE_CORS_ORIGINS",
    )

    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="TOKENWISE_OPENAI_BASE_URL")
    anthropic_base_url: str = Field(default="https://api.anthropic.com/v1", alias="TOKENWISE_ANTHROPIC_BASE_URL")
    meta_agent_provider: Provider = Field(default=Provider.OPENAI, alias="TOKENWISE_META_AGENT_PROVIDER")

    openai_tier1_model_id: str = Field(default="gpt-4o-mini", alias="TOKENWISE_OPENAI_TIER1_MODEL_ID")
    openai_tier2_model_id: str = Field(default="gpt-4o", alias="TOKENWISE_OPENAI_TIER2_MODEL_ID")
    openai_tier3_model_id: str = Field(default="o1", alias="TOKENWISE_OPENAI_TIER3_MODEL_ID")
    anthropic_tier1_model_id: str = Field(
        default="claude-3-5-haiku-20241022",
        alias="TOKENWISE_ANTHROPIC_TIER1_MODEL_ID",
    )
    anthropic_tier2_model_id: str = Field(
        default="claude-sonnet-4-20250514",
        alias="TOKENWISE_ANTHROPIC_TIER2_MODEL_ID",
    )
    anthropic_tier3_model_id: str = Field(
        default="claude-opus-4-1-20250805",
        alias="TOKENWISE_ANTHROPIC_TIER3_MODEL_ID",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @property
    def resolved_db_path(self) -> Path:
        return Path(self.db_path).expanduser().resolve()

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: str | list[str]) -> list[str] | str:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    def require_provider_keys(self) -> None:
        missing = []
        if not self.openai_api_key:
            missing.append("OPENAI_API_KEY")
        if not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY")
        if missing:
            missing_keys = ", ".join(missing)
            raise RuntimeError(f"Missing required API keys: {missing_keys}")


def build_model_registry(settings: Settings) -> dict[str, ModelProfile]:
    return {
        "tier1_openai": ModelProfile(
            alias="tier1_openai",
            display_name="GPT-4o mini",
            provider=Provider.OPENAI,
            tier=1,
            model_id=settings.openai_tier1_model_id,
            pricing=TokenPricing(input_per_million=0.15, output_per_million=0.60),
            capability_flags=["cheap", "general", "fast"],
        ),
        "tier2_openai": ModelProfile(
            alias="tier2_openai",
            display_name="GPT-4o",
            provider=Provider.OPENAI,
            tier=2,
            model_id=settings.openai_tier2_model_id,
            pricing=TokenPricing(input_per_million=2.50, output_per_million=10.00),
            capability_flags=["general", "synthesis", "balanced"],
        ),
        "tier3_openai": ModelProfile(
            alias="tier3_openai",
            display_name="o1",
            provider=Provider.OPENAI,
            tier=3,
            model_id=settings.openai_tier3_model_id,
            pricing=TokenPricing(input_per_million=15.00, output_per_million=60.00),
            capability_flags=["reasoning", "escalation", "high_confidence"],
        ),
        "tier1_anthropic": ModelProfile(
            alias="tier1_anthropic",
            display_name="Claude Haiku 3.5",
            provider=Provider.ANTHROPIC,
            tier=1,
            model_id=settings.anthropic_tier1_model_id,
            pricing=TokenPricing(input_per_million=0.80, output_per_million=4.00),
            capability_flags=["structured", "fast", "cheap"],
        ),
        "tier2_anthropic": ModelProfile(
            alias="tier2_anthropic",
            display_name="Claude Sonnet 4",
            provider=Provider.ANTHROPIC,
            tier=2,
            model_id=settings.anthropic_tier2_model_id,
            pricing=TokenPricing(input_per_million=3.00, output_per_million=15.00),
            capability_flags=["structured", "analysis", "balanced"],
        ),
        "tier3_anthropic": ModelProfile(
            alias="tier3_anthropic",
            display_name="Claude Opus 4.1",
            provider=Provider.ANTHROPIC,
            tier=3,
            model_id=settings.anthropic_tier3_model_id,
            pricing=TokenPricing(input_per_million=15.00, output_per_million=75.00),
            capability_flags=["reasoning", "deep_analysis", "high_confidence"],
        ),
    }
