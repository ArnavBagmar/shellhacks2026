"""Open-Meteo weather for ``ask`` questions, with the HTTP layer mocked: how a
free-text location becomes coordinates, what the model is told, caching,
and that a broken network is ``None`` rather than an exception.
"""

from __future__ import annotations

import httpx
import pytest

from cane.scene.weather import OpenMeteoWeather, describe_code, is_weather_question, weather_from_env

GEOCODE = {
    "FIU": [{"name": "Fiu", "admin1": "Rivers", "country": "Nigeria", "country_code": "NG",
             "latitude": 4.8, "longitude": 7.0}],
    "Miami": [
        {"name": "Miami", "admin1": "Oklahoma", "country": "United States", "country_code": "US",
         "latitude": 36.87, "longitude": -94.88},
        {"name": "Miami", "admin1": "Florida", "country": "United States", "country_code": "US",
         "latitude": 25.77, "longitude": -80.19},
    ],
}
FORECAST = {
    "current": {"time": "2026-09-26T15:30", "temperature_2m": 84.5, "apparent_temperature": 87.4,
                "relative_humidity_2m": 47, "precipitation": 0.0, "weather_code": 1, "wind_speed_10m": 4.8},
    "current_units": {"temperature_2m": "°F", "wind_speed_10m": "mp/h"},
    "daily": {"temperature_2m_max": [84.9], "temperature_2m_min": [72.6],
              "precipitation_probability_max": [14], "weather_code": [3]},
}


def _client(requests: list[httpx.Request], *, fail: bool = False) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if fail:
            raise httpx.ConnectError("no network")
        if request.url.host.startswith("geocoding"):
            return httpx.Response(200, json={"results": GEOCODE.get(request.url.params["name"], [])})
        return httpx.Response(200, json=FORECAST)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_a_campus_location_geocodes_to_its_city_and_state() -> None:
    requests: list[httpx.Request] = []
    weather = OpenMeteoWeather("FIU, Miami, Florida", client=_client(requests))
    summary = await weather.summary()

    forecast = requests[-1]
    assert forecast.url.params["latitude"] == "25.77"
    assert forecast.url.params["longitude"] == "-80.19"
    assert forecast.url.params["temperature_unit"] == "fahrenheit"
    assert summary is not None and "Miami, Florida" in summary


async def test_the_summary_has_what_a_person_would_say() -> None:
    weather = OpenMeteoWeather("Miami, Florida", client=_client([]))
    summary = await weather.summary()
    assert summary is not None
    for part in ("85°F", "mainly clear", "feels like 87°F", "high 85°F", "low 73°F", "14% chance of rain", "3:30 PM"):
        assert part in summary, part


async def test_it_is_cached_between_questions() -> None:
    requests: list[httpx.Request] = []
    weather = OpenMeteoWeather("Miami, Florida", client=_client(requests))
    await weather.summary()
    count = len(requests)
    await weather.summary()
    assert len(requests) == count


async def test_explicit_coordinates_skip_geocoding() -> None:
    requests: list[httpx.Request] = []
    weather = OpenMeteoWeather(None, lat_lon=(25.756, -80.374), client=_client(requests))
    assert await weather.summary() is not None
    assert [r.url.host for r in requests] == ["api.open-meteo.com"]


async def test_no_network_is_none_not_an_exception() -> None:
    weather = OpenMeteoWeather("Miami, Florida", client=_client([], fail=True))
    assert await weather.summary() is None


async def test_an_unknown_place_is_none() -> None:
    weather = OpenMeteoWeather("Atlantis", client=_client([]))
    assert await weather.summary() is None


@pytest.mark.parametrize("question", [
    "how is the weather today", "is it raining", "do i need an umbrella", "how hot is it outside",
    "what is the temperature", "will it rain later", "is it cold out",
])
def test_weather_questions(question: str) -> None:
    assert is_weather_question(question)


@pytest.mark.parametrize("question", [
    "is there a bathroom around", "what is outside the door", "what is the capital of france",
])
def test_not_weather_questions(question: str) -> None:
    assert not is_weather_question(question)


def test_wmo_codes_read_as_words() -> None:
    assert describe_code(0) == "clear"
    assert describe_code(95) == "thunderstorms"
    assert describe_code(12345) == "unknown conditions"


def test_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CANE_LOCATION", raising=False)
    monkeypatch.delenv("CANE_LAT_LON", raising=False)
    assert weather_from_env() is None
    monkeypatch.setenv("CANE_LOCATION", "Miami, Florida")
    assert weather_from_env() is not None
    monkeypatch.setenv("CANE_WEATHER", "off")
    assert weather_from_env() is None
