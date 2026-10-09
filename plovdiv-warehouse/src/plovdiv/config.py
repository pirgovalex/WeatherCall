"""Application settings and configuration loaded via Pydantic BaseSettings."""

from __future__ import annotations

import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """Plovdiv weather and air quality warehouse configuration."""

    # API Endpoints
    weather_api_url: str = "https://api.open-meteo.com/v1/forecast"
    aq_api_url: str = "https://air-quality-api.open-meteo.com/v1/air-quality"

    # Coordinates for Plovdiv city centre
    latitude: float = 42.14
    longitude: float = 24.75

    # Request Identity
    user_agent: str = "Plovdiv-Weather-Warehouse/0.1 (Research Project)"

    # API Keys (optional for free tier)
    openmeteo_api_key: str | None = None
    eea_api_key: str | None = None

    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
