"""Weather data collector via Open-Meteo API."""

from __future__ import annotations

import logging
from typing import Any

from core.utils import ScraperResult, format_number_pt_br, http_get_json

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# Códigos WMO devolvidos pelo Open-Meteo. Traduzir aqui em vez de deixar o número no payload:
# o modelo não tem como saber o que significa "weather_code: 61".
WMO_CODES = {
    0: "Céu limpo",
    1: "Predominantemente limpo",
    2: "Parcialmente nublado",
    3: "Nublado",
    45: "Nevoeiro",
    48: "Nevoeiro com geada",
    51: "Garoa fraca",
    53: "Garoa moderada",
    55: "Garoa intensa",
    56: "Garoa congelante",
    57: "Garoa congelante intensa",
    61: "Chuva fraca",
    63: "Chuva moderada",
    65: "Chuva forte",
    66: "Chuva congelante",
    67: "Chuva congelante forte",
    71: "Neve fraca",
    73: "Neve moderada",
    75: "Neve forte",
    77: "Grãos de neve",
    80: "Pancadas de chuva isoladas",
    81: "Pancadas de chuva",
    82: "Pancadas de chuva fortes",
    85: "Pancadas de neve",
    86: "Pancadas de neve fortes",
    95: "Tempestade",
    96: "Tempestade com granizo",
    99: "Tempestade com granizo forte",
}


def _celsius(value: float | None) -> str | None:
    """'18,4°C'. Pelo mesmo motivo das cotações: entregue como float, o modelo escreve
    '18.4°C' com ponto decimal no meio de um texto em português."""
    return None if value is None else f"{format_number_pt_br(float(value), 1)}°C"


def _uv_label(index: float | None) -> str | None:
    if index is None:
        return None
    for limit, label in ((2.9, "baixo"), (5.9, "moderado"), (7.9, "alto"), (10.9, "muito alto")):
        if index <= limit:
            return label
    return "extremo"


def _rain_window(hours: list[int | None], probabilities: list[int | None], threshold: int) -> str | None:
    """Primeiro intervalo contínuo do dia com chance de chuva acima do limiar.

    "Vai chover entre 16h e 19h" muda o dia de quem lê às 6h; "60% de chance hoje" não.
    """
    start: int | None = None
    end: int | None = None

    for hour, probability in zip(hours, probabilities):
        if probability is not None and probability >= threshold:
            if start is None:
                start = hour
            end = hour
        elif start is not None:
            break

    if start is None:
        return None
    return f"{start}h" if start == end else f"{start}h-{end}h"


async def fetch(settings) -> ScraperResult:
    section = "weather"
    weather_cfg = settings.get("weather") or {}
    city = weather_cfg.get("city", "Unknown")
    lat = weather_cfg.get("lat")
    lon = weather_cfg.get("lon")
    rain_threshold = int(weather_cfg.get("rain_threshold_percent", 40))

    if lat is None or lon is None:
        return ScraperResult(
            section=section,
            status="error",
            error="weather.lat and weather.lon are required in config.yaml",
        )

    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": (
            "temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
            "precipitation_sum,sunrise,sunset,uv_index_max,weather_code"
        ),
        "hourly": "precipitation_probability",
        "timezone": "America/Sao_Paulo",
        "forecast_days": 1,
    }

    try:
        payload = await http_get_json(OPEN_METEO_URL, settings, params=params)
        daily = payload.get("daily") or {}
        current = payload.get("current") or {}
        hourly = payload.get("hourly") or {}

        if not daily.get("time"):
            return ScraperResult(section=section, status="error", error="Open-Meteo returned empty forecast")

        hours = [int(stamp[11:13]) for stamp in hourly.get("time", [])]
        probabilities = hourly.get("precipitation_probability", [])
        uv_max = (daily.get("uv_index_max") or [None])[0]

        wind = current.get("wind_speed_10m")
        temp_min = daily["temperature_2m_min"][0]
        temp_max = daily["temperature_2m_max"][0]

        data: dict[str, Any] = {
            "city": city,
            "date": daily["time"][0],
            "condition": WMO_CODES.get((daily.get("weather_code") or [None])[0]),
            "condition_now": WMO_CODES.get(current.get("weather_code")),
            "temp_now": _celsius(current.get("temperature_2m")),
            "feels_like": _celsius(current.get("apparent_temperature")),
            "wind": None if wind is None else f"{format_number_pt_br(float(wind), 0)} km/h",
            "temp_range": f"{_celsius(temp_min)} a {_celsius(temp_max)}",
            # Numéricos crus para o histórico comparativo; o texto acima é para o LLM copiar.
            "temp_min_c": temp_min,
            "temp_max_c": temp_max,
            "rain_probability_percent": (daily.get("precipitation_probability_max") or [None])[0],
            "rain_mm": (daily.get("precipitation_sum") or [None])[0],
            "rain_window": _rain_window(hours, probabilities, rain_threshold),
            "sunrise": (daily.get("sunrise") or [""])[0][-5:] or None,
            "sunset": (daily.get("sunset") or [""])[0][-5:] or None,
            "uv_index_max": uv_max,
            "uv_label": _uv_label(uv_max),
        }
        return ScraperResult(section=section, status="ok", data=data)

    except Exception as exc:
        logger.warning("Weather fetch failed: %s", exc)
        return ScraperResult(section=section, status="error", error=str(exc))
