"""Weather reports via Open-Meteo free API (no key required)."""

from __future__ import annotations

import asyncio

from pydantic import Field

from ..policy import Tier
from .registry import Args, ToolError, tool

_WMO_CODES: dict[int, str] = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snowfall", 73: "Moderate snowfall", 75: "Heavy snowfall",
    77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}


def _describe_code(code: int | None) -> str:
    if code is None:
        return "Unknown"
    return _WMO_CODES.get(code, f"WMO code {code}")


def _fetch_weather(location: str, units: str) -> dict:
    import httpx

    with httpx.Client(timeout=10) as client:
        geo = client.get("https://geocoding-api.open-meteo.com/v1/search",
                         params={"name": location, "count": 1, "language": "en"})
        geo.raise_for_status()
        results = geo.json().get("results")
        if not results:
            raise ToolError(f"Location not found: {location}")
        place = results[0]
        lat, lon = place["latitude"], place["longitude"]
        resolved = f"{place.get('name', location)}, {place.get('country', '')}"

        temp_unit = "fahrenheit" if units == "imperial" else "celsius"
        wind_unit = "mph" if units == "imperial" else "kmh"
        forecast = client.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": lat, "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,wind_speed_10m,weather_code",
            "daily": "temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max",
            "timezone": "auto", "forecast_days": 3,
            "temperature_unit": temp_unit, "wind_speed_unit": wind_unit,
        })
        forecast.raise_for_status()
        data = forecast.json()

    current = data.get("current", {})
    daily = data.get("daily", {})
    unit_label = "°F" if units == "imperial" else "°C"
    wind_label = "mph" if units == "imperial" else "km/h"

    days = []
    for i in range(len(daily.get("time", []))):
        days.append({
            "date": daily["time"][i],
            "high": f"{daily['temperature_2m_max'][i]}{unit_label}",
            "low": f"{daily['temperature_2m_min'][i]}{unit_label}",
            "condition": _describe_code(daily["weather_code"][i]),
            "precipitation_chance": f"{daily.get('precipitation_probability_max', [None])[i] or 0}%",
        })

    return {
        "location": resolved,
        "current": {
            "temperature": f"{current.get('temperature_2m')}{unit_label}",
            "feels_like": f"{current.get('apparent_temperature')}{unit_label}",
            "humidity": f"{current.get('relative_humidity_2m')}%",
            "wind": f"{current.get('wind_speed_10m')} {wind_label}",
            "condition": _describe_code(current.get("weather_code")),
        },
        "forecast": days,
    }


class WeatherArgs(Args):
    location: str = Field("", max_length=200, description="City name, e.g. 'New York' or 'London'. Leave empty to use auto-detected location.")
    units: str = Field("metric", pattern="^(metric|imperial)$", description="metric (°C) or imperial (°F)")


@tool("weather_get", "Get current weather and 3-day forecast. Defaults to the user's current location if none specified.",
      WeatherArgs, Tier.T0, summary=lambda a: f"Weather for {a.location or 'current location'}")
async def weather_get(a: WeatherArgs) -> dict:
    loc = a.location
    if not loc:
        from .. import location as loc_mod
        loc = loc_mod.city()
        if not loc:
            raise ToolError("Could not detect location automatically. Please specify a city name.")
    return await asyncio.to_thread(_fetch_weather, loc, a.units)
