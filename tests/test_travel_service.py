import asyncio
import time

import pytest
import httpx

from app.cache import AsyncTTLCache
from app.schemas.travel import CurrencyResponse, Interest, PlacesResponse, ResolvedLocation, TravelPlanRequest, WeatherResponse
from app.services.travel import TravelPlanService


class DelayedWeatherProvider:
    async def get_weather(self, location: ResolvedLocation, start_date, end_date) -> WeatherResponse:
        await asyncio.sleep(0.05)
        return WeatherResponse(destination=location.name, summary="Clear", temperature_celsius=20, precipitation_chance=0, provider="test")


class DelayedPlacesProvider:
    async def get_places(self, location: ResolvedLocation, interests: list[Interest], limit: int, radius_meters: int) -> PlacesResponse:
        await asyncio.sleep(0.05)
        return PlacesResponse(destination=location.name, places=[], provider="test")


class FailingCurrencyProvider:
    async def get_rate(self, base_currency: str, target_currency: str, amount) -> CurrencyResponse:
        await asyncio.sleep(0.05)
        raise RuntimeError("upstream failure")


class CountingGeocoder:
    def __init__(self) -> None:
        self.calls = 0

    async def resolve_destination(self, destination: str) -> ResolvedLocation:
        self.calls += 1
        return ResolvedLocation(name=destination, latitude=41.9, longitude=12.5)


@pytest.mark.asyncio
async def test_service_fetches_providers_concurrently_and_keeps_partial_results() -> None:
    service = TravelPlanService(DelayedWeatherProvider(), DelayedPlacesProvider(), FailingCurrencyProvider(), CountingGeocoder())
    start = time.perf_counter()
    plan = await service.create_plan(TravelPlanRequest(destination="Rome", target_currency="EUR"))
    elapsed = time.perf_counter() - start

    assert elapsed < 0.12
    assert plan.weather is not None
    assert plan.places is not None
    assert plan.currency is None
    assert plan.service_errors[0].service == "currency"


class FlakyWeatherProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def get_weather(self, location: ResolvedLocation, start_date, end_date) -> WeatherResponse:
        self.calls += 1
        if self.calls == 1:
            raise httpx.ConnectError("temporary network failure")
        return WeatherResponse(destination=location.name, summary="Clear", temperature_celsius=20, precipitation_chance=0, provider="test")


@pytest.mark.asyncio
async def test_service_retries_transient_provider_failures() -> None:
    weather = FlakyWeatherProvider()
    service = TravelPlanService(
        weather,
        DelayedPlacesProvider(),
        FailingCurrencyProvider(),
        CountingGeocoder(),
        cache=AsyncTTLCache(60),
        retry_attempts=2,
        retry_delay_seconds=0,
    )

    result = await service.fetch_weather(TravelPlanRequest(destination="Rome"))
    assert result.summary == "Clear"
    assert weather.calls == 2


@pytest.mark.asyncio
async def test_weather_and_places_share_one_geocoding_request() -> None:
    geocoder = CountingGeocoder()
    service = TravelPlanService(DelayedWeatherProvider(), DelayedPlacesProvider(), FailingCurrencyProvider(), geocoder)
    plan = await service.create_plan(TravelPlanRequest(destination="Rome"))

    assert plan.weather is not None
    assert plan.places is not None
    assert geocoder.calls == 1
