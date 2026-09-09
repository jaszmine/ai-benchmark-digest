from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # OpenRouter Model Settings
    openrouter_api_key: str = Field(..., description="OpenRouter API Key")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        description="OpenRouter API endpoint base URL",
    )
    model_name: str = Field(
        default="nvidia/nemotron-3-super-120b-a12b:free",
        description="Parity model identifier",
    )
    app_referer: str = Field(
        default="https://github.com/ai-benchmark-digest",
        description="Required by OpenRouter for free-tier rankings",
    )
    app_title: str = Field(
        default="AI Benchmark Digest",
        description="Application title for OpenRouter analytics",
    )

    # Search Tooling
    tavily_api_key: str = Field(..., description="Tavily API key for Track B search")

    # Resend Delivery
    resend_api_key: str = Field(..., description="Resend API key for delivery")
    notification_email_to: str = Field(..., description="Destination email recipient")
    notification_email_from: str = Field(
        default="benchmark@updates.internal",
        description="Verified sending address",
    )

    # Telemetry and Execution Limits
    lookback_days: int = Field(default=7, ge=1, le=14)
    story_cap: int = Field(default=5, description="Exact number of stories per track")
    max_track_b_turns: int = Field(default=3, ge=1, le=5)

    # Rate-Limit Budgeting (OpenRouter Free Tier Protections)
    inter_turn_sleep_seconds: float = Field(
        default=3.0,
        description="Mandatory sleep between individual agent interactions",
    )
    inter_track_pause_seconds: float = Field(
        default=15.0,
        description="Buffer pause between Track A completion and Track B dispatch",
    )

    # Network Engine
    http_timeout_seconds: float = Field(default=12.0)
    max_retries: int = Field(default=3)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
