"""Weather data collector via Open-Meteo API."""

from __future__ import annotations

import logging

from core.utils import ScraperResult, http_get_json

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


async def fetch(settings) -> ScraperResult:
    section = "weather"
    weather_cfg = settings.get("weather") or {}
    city = weather_cfg.get("city", "Unknown")
    lat = weather_cfg.get("lat")
    lon = weather_cfg.get("lon")

    if lat is None or lon is None:
        return ScraperResult(
            section=section,
            status="error",
            error="weather.lat and weather.lon are required in config.yaml",
        )

    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": ["temperature_2m_max", "temperature_2m_min", "precipitation_probability_max"],
        "timezone": "America/Sao_Paulo",
        "forecast_days": 1,
    }

    try:
        payload = await http_get_json(OPEN_METEO_URL, settings, params=params)
        daily = payload.get("daily", {})

        if not daily.get("time"):
            return ScraperResult(section=section, status="error", error="Open-Meteo returned empty forecast")

        data = {
            "city": city,
            "date": daily["time"][0],
            "temp_min_c": daily["temperature_2m_min"][0],
            "temp_max_c": daily["temperature_2m_max"][0],
            "rain_probability_percent": daily.get("precipitation_probability_max", [None])[0],
        }
        return ScraperResult(section=section, status="ok", data=data)

    except Exception as exc:
        logger.warning("Weather fetch failed: %s", exc)
        return ScraperResult(section=section, status="error", error=str(exc))
