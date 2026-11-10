from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="TRIPPILOT_")

    app_name: str = "TripPilot API"
    debug: bool = False
    request_timeout_seconds: float = Field(default=5.0, gt=0)
    cache_ttl_seconds: float = Field(default=300.0, gt=0)
    provider_retry_attempts: int = Field(default=2, ge=1)
    provider_retry_delay_seconds: float = Field(default=0.1, ge=0)
    geoapify_api_key: str | None = None
    open_meteo_geocoding_url: str = "https://geocoding-api.open-meteo.com/v1/search"
    open_meteo_forecast_url: str = "https://api.open-meteo.com/v1/forecast"
    geoapify_places_url: str = "https://api.geoapify.com/v2/places"
    frankfurter_url: str = "https://api.frankfurter.dev/v2"
    weather_forecast_days: int = Field(default=7, ge=1, le=16)
    places_radius_meters: int = Field(default=5_000, ge=100, le=50_000)
    weather_cache_ttl_seconds: float = Field(default=1_800, gt=0)
    places_cache_ttl_seconds: float = Field(default=21_600, gt=0)
    geocoding_cache_ttl_seconds: float = Field(default=86_400, gt=0)
    currency_cache_ttl_seconds: float = Field(default=3_600, gt=0)
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        validation_alias=AliasChoices("OLLAMA_BASE_URL", "TRIPPILOT_OLLAMA_BASE_URL"),
    )
    ollama_model: str = Field(
        default="llama3.2",
        validation_alias=AliasChoices("OLLAMA_MODEL", "TRIPPILOT_OLLAMA_MODEL"),
    )
    ollama_timeout_seconds: float = Field(
        default=30,
        gt=0,
        validation_alias=AliasChoices("OLLAMA_TIMEOUT_SECONDS", "TRIPPILOT_OLLAMA_TIMEOUT_SECONDS"),
    )
    itinerary_cache_ttl_seconds: float = Field(default=900, gt=0)
    itinerary_max_days: int = Field(default=14, ge=1, le=30)
    itinerary_repair_attempts: int = Field(default=1, ge=0, le=2)
    conversation_history_limit: int = Field(default=12, ge=2, le=40)
    conversation_max_tool_calls: int = Field(default=2, ge=1, le=5)


@lru_cache
def get_settings() -> Settings:
    return Settings()

