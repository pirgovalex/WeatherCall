"""Tests for application settings and environment configuration."""

import os
from pathlib import Path
import pytest
from pydantic_settings import BaseSettings

from plovdiv.config import Settings, settings
from plovdiv.extract.fetch_forecasts import settings as forecast_settings


def test_settings_inheritance():
    """Verify Settings is a Pydantic BaseSettings instance."""
    assert issubclass(Settings, BaseSettings)
    assert isinstance(settings, Settings)


def test_default_settings_values():
    """Verify settings defaults match Plovdiv requirements."""
    assert settings.weather_api_url.startswith("https://")
    assert settings.aq_api_url.startswith("https://")
    assert settings.latitude == 42.14
    assert settings.longitude == 24.75
    assert "Plovdiv" in settings.user_agent


def test_fetch_forecasts_settings_integration():
    """Verify fetch_forecasts uses centralized settings."""
    assert forecast_settings.latitude == settings.latitude
    assert forecast_settings.weather_api_url == settings.weather_api_url


def test_dotenv_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify Settings loads overrides from a .env file."""
    custom_env = tmp_path / ".env"
    custom_env.write_text(
        "WEATHER_API_URL=https://custom.endpoint/forecast\n"
        "LATITUDE=43.0\n"
        "LONGITUDE=25.0\n",
        encoding="utf-8",
    )

    class CustomSettings(Settings):
        model_config = {
            "env_file": str(custom_env),
            "env_file_encoding": "utf-8",
            "extra": "ignore",
        }

    custom_cfg = CustomSettings()
    assert custom_cfg.weather_api_url == "https://custom.endpoint/forecast"
    assert custom_cfg.latitude == 43.0
    assert custom_cfg.longitude == 25.0
