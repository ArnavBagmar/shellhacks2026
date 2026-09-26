"""Live weather for "Hey Cane, how's the weather?" -- Open-Meteo, free and
keyless.

Gemini alone cannot know today's weather, and Google Search grounding is
not on every key (live 2026-09-26: 429 for search, plain calls fine). So
for weather questions the laptop looks it up itself and hands the model
one sentence of facts next to the camera views. ``CANE_LOCATION`` ("FIU,
Miami, Florida") is geocoded once by trying each comma part as a place
name, filtered by the parts after it; ``CANE_LAT_LON`` skips that.

Every failure -- no network, unknown place, junk JSON -- is ``None``, and
the model then says it cannot check the forecast.
"""

from __future__ import annotations

import logging
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Protocol

import httpx

logger = logging.getLogger(__name__)

WEATHER_ENV = "CANE_WEATHER"
LOCATION_ENV = "CANE_LOCATION"
LAT_LON_ENV = "CANE_LAT_LON"
TEMP_UNIT_ENV = "CANE_TEMP_UNIT"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
#: Open-Meteo's own models refresh every 15 min; 10 min keeps it live
#: without a request per question.
CACHE_S = 600.0
#: The wearer is waiting: a slow weather lookup is dropped, not waited on.
TIMEOUT_S = 3.0

_WEATHER_WORDS = re.compile(
    r"\b(weather|forecast|rain|raining|rainy|umbrella|temperature|degrees|humid|humidity|sunny|"
    r"cloudy|storm|stormy|thunder|windy|snow|snowing|hot|cold|warm|chilly|freezing)\b"
    r"|\bit\b.*\bout(side)?\b"
)

#: WMO weather interpretation codes, as a person would say them.
_WMO = {
    0: "clear", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "foggy",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "heavy showers", 85: "snow showers", 86: "snow showers",
    95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail",
}


class WeatherSource(Protocol):
    async def summary(self) -> str | None: ...


def is_weather_question(utterance: str) -> bool:
    return bool(_WEATHER_WORDS.search(utterance.lower()))


def describe_code(code: int) -> str:
    return _WMO.get(code, "unknown conditions")


@dataclass(frozen=True)
class Place:
    name: str
    latitude: float
    longitude: float


class OpenMeteoWeather:
    def __init__(
        self,
        location: str | None,
        *,
        lat_lon: tuple[float, float] | None = None,
        unit: str = "fahrenheit",
        client: httpx.AsyncClient | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._location = location
        self._place = Place(location or "here", *lat_lon) if lat_lon else None
        self._unit = "celsius" if unit.strip().lower().startswith("c") else "fahrenheit"
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT_S)
        self._monotonic = monotonic
        self._cached: tuple[float, str] | None = None

    async def summary(self) -> str | None:
        """One sentence of live weather for the prompt, or ``None``."""
        now = self._monotonic()
        if self._cached is not None and now - self._cached[0] < CACHE_S:
            return self._cached[1]
        try:
            place = self._place or await self._geocode()
            if place is None:
                logger.warning("weather: could not find %r on Open-Meteo", self._location)
                return None
            self._place = place
            text = await self._forecast(place)
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            logger.warning("weather lookup failed: %s", exc)
            return None
        self._cached = (now, text)
        logger.info("weather: %s", text)
        return text

    async def _geocode(self) -> Place | None:
        parts = [p.strip() for p in (self._location or "").split(",") if p.strip()]
        for i, name in enumerate(parts):
            rest = [p.lower() for p in parts[i + 1:]]
            response = await self._client.get(
                GEOCODE_URL, params={"name": name, "count": 10, "language": "en", "format": "json"}
            )
            response.raise_for_status()
            for result in response.json().get("results") or []:
                labels = {str(result.get(k, "")).lower() for k in
                          ("name", "admin1", "admin2", "admin3", "country", "country_code")}
                if all(part in labels for part in rest):
                    region = result.get("admin1") or result.get("country") or ""
                    label = f"{result['name']}, {region}" if region else str(result["name"])
                    return Place(label, float(result["latitude"]), float(result["longitude"]))
        return None

    async def _forecast(self, place: Place) -> str:
        response = await self._client.get(FORECAST_URL, params={
            "latitude": place.latitude,
            "longitude": place.longitude,
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,"
                       "weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
            "temperature_unit": self._unit,
            "wind_speed_unit": "mph" if self._unit == "fahrenheit" else "kmh",
            "timezone": "auto",
            "forecast_days": 1,
        })
        response.raise_for_status()
        return self._format(place, response.json())

    def _format(self, place: Place, data: dict[str, Any]) -> str:
        now, today = data["current"], data["daily"]
        deg = "°F" if self._unit == "fahrenheit" else "°C"
        wind = "mph" if self._unit == "fahrenheit" else "km/h"
        as_of = datetime.fromisoformat(now["time"]).strftime("%I:%M %p").lstrip("0")
        text = (
            f"Live weather for {place.name} (Open-Meteo, {as_of}): "
            f"{_whole(now['temperature_2m'])}{deg} and {describe_code(int(now['weather_code']))}, "
            f"feels like {_whole(now['apparent_temperature'])}{deg}, "
            f"humidity {_whole(now['relative_humidity_2m'])}%, wind {_whole(now['wind_speed_10m'])} {wind}"
        )
        if float(now.get("precipitation") or 0) > 0:
            text += ", raining now"
        text += (
            f". Today: {describe_code(int(today['weather_code'][0]))}, "
            f"high {_whole(today['temperature_2m_max'][0])}{deg}, low {_whole(today['temperature_2m_min'][0])}{deg}"
        )
        chance = today.get("precipitation_probability_max", [None])[0]
        if chance is not None:
            text += f", {_whole(chance)}% chance of rain"
        return text + "."


def _whole(value: object) -> int:
    """Half up, as people round temperatures (Python's round(84.5) is 84)."""
    return math.floor(float(value) + 0.5)  # type: ignore[arg-type]


def weather_from_env() -> OpenMeteoWeather | None:
    """``None`` when switched off or when there is no location to look up."""
    if os.environ.get(WEATHER_ENV, "on").strip().lower() in {"off", "0", "false", "no"}:
        return None
    location = os.environ.get(LOCATION_ENV, "").strip() or None
    lat_lon = None
    raw = os.environ.get(LAT_LON_ENV, "").strip()
    if raw:
        try:
            lat, lon = (float(v) for v in raw.split(","))
            lat_lon = (lat, lon)
        except ValueError:
            logger.warning("%s=%r is not 'lat,lon'; ignoring it", LAT_LON_ENV, raw)
    if location is None and lat_lon is None:
        return None
    return OpenMeteoWeather(location, lat_lon=lat_lon, unit=os.environ.get(TEMP_UNIT_ENV, "fahrenheit"))
