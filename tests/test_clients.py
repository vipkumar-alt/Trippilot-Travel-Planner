from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.clients.frankfurter import FrankfurterClient
from app.clients.geoapify import GeoapifyPlacesClient
from app.clients.open_meteo import OpenMeteoClient
from app.errors import ConfigurationError, ProviderError
from app.schemas.travel import Interest, ResolvedLocation


@pytest.mark.asyncio
async def test_open_meteo_geocodes_and_normalizes_daily_forecast() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/geo":
            return httpx.Response(200, json={"results": [{"name": "Goa", "latitude": 15.3, "longitude": 74.1, "country": "India"}]})
        assert request.url.params["daily"] == "temperature_2m_min,temperature_2m_max,precipitation_probability_max,weather_code,wind_speed_10m_max"
        return httpx.Response(200, json={"daily": {"time": ["2026-10-16"], "temperature_2m_min": [24], "temperature_2m_max": [31], "precipitation_probability_max": [30], "weather_code": [2], "wind_speed_10m_max": [18]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenMeteoClient(client, "https://test/geo", "https://test/forecast", 16)
        location = await provider.resolve_destination("Goa")
        weather = await provider.get_weather(location, None, None)

    assert location.latitude == 15.3
    assert weather.forecasts[0].weather_condition == "Partly cloudy"
    assert weather.forecasts[0].temperature_max_celsius == 31


@pytest.mark.asyncio
async def test_open_meteo_rejects_malformed_geocoding_response() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"results": []}))) as client:
        provider = OpenMeteoClient(client, "https://test/geo", "https://test/forecast", 16)
        with pytest.raises(ProviderError, match="could not be resolved"):
            await provider.resolve_destination("Unknown")


@pytest.mark.asyncio
async def test_geoapify_parses_missing_optional_fields_and_requires_key() -> None:
    location = ResolvedLocation(name="Goa", latitude=15.3, longitude=74.1)
    async with httpx.AsyncClient() as client:
        unconfigured = GeoapifyPlacesClient(client, "https://test/places", None)
        with pytest.raises(ConfigurationError, match="not configured"):
            await unconfigured.get_places(location, [], 10, 5000)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["apiKey"] == "private-key"
        return httpx.Response(200, json={"features": [{"properties": {"place_id": "abc", "categories": ["tourism.attraction"]}, "geometry": {"coordinates": [74.1, 15.3]}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = GeoapifyPlacesClient(client, "https://test/places", "private-key")
        places = await provider.get_places(location, [Interest.CULTURE], 10, 5000)

    assert places.places[0].name is None
    assert places.places[0].provider_id == "abc"


@pytest.mark.asyncio
async def test_frankfurter_converts_amount_and_handles_unsupported_currency() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"rate": 83.4567, "date": "2026-10-15"}))) as client:
        provider = FrankfurterClient(client, "https://test/v2")
        currency = await provider.get_rate("USD", "INR", Decimal("100.00"))
    assert currency.converted_amount == Decimal("8345.67")
    assert currency.rate_date == date(2026, 10, 15)

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(422, json={"message": "invalid currency"}))) as client:
        provider = FrankfurterClient(client, "https://test/v2")
        with pytest.raises(ProviderError, match="unsupported"):
            await provider.get_rate("USD", "ZZZ", None)
