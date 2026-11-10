import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

from app.cache import AsyncTTLCache
from app.clients.base import CurrencyProvider, GeocodingProvider, PlacesProvider, WeatherProvider
from app.config import Settings
from app.errors import ProviderError
from app.schemas.travel import (
    CurrencyResponse,
    PlacesResponse,
    ServiceError,
    TravelPlanRequest,
    TravelPlanResponse,
    WeatherResponse,
)

logger = logging.getLogger(__name__)
T = TypeVar("T")


class TravelPlanService:
    def __init__(
        self,
        weather_provider: WeatherProvider,
        places_provider: PlacesProvider,
        currency_provider: CurrencyProvider,
        geocoding_provider: GeocodingProvider,
        *,
        cache: AsyncTTLCache | None = None,
        settings: Settings | None = None,
        timeout_seconds: float = 5.0,
        retry_attempts: int = 2,
        retry_delay_seconds: float = 0.1,
    ) -> None:
        if timeout_seconds <= 0 or retry_attempts < 1 or retry_delay_seconds < 0:
            raise ValueError("invalid reliability configuration")
        self._weather_provider = weather_provider
        self._places_provider = places_provider
        self._currency_provider = currency_provider
        self._geocoding_provider = geocoding_provider
        self._cache = cache or AsyncTTLCache(ttl_seconds=300)
        self._timeout_seconds = timeout_seconds
        self._retry_attempts = retry_attempts
        self._retry_delay_seconds = retry_delay_seconds
        self._settings = settings or Settings()

    @property
    def cache(self) -> AsyncTTLCache:
        return self._cache

    async def fetch_location(self, request: TravelPlanRequest):
        key = ("geocoding", request.destination.casefold())
        return await self._cache.get_or_set(
            key,
            lambda: self._call_provider("geocoding", lambda: self._geocoding_provider.resolve_destination(request.destination)),
            ttl_seconds=self._settings.geocoding_cache_ttl_seconds,
        )

    async def fetch_weather(self, request: TravelPlanRequest) -> WeatherResponse:
        location = await self.fetch_location(request)
        key = ("weather", location.latitude, location.longitude, request.start_date, request.end_date)
        return await self._cache.get_or_set(
            key,
            lambda: self._call_provider(
                "weather", lambda: self._weather_provider.get_weather(location, request.start_date, request.end_date)
            ),
            ttl_seconds=self._settings.weather_cache_ttl_seconds,
        )

    async def fetch_places(self, request: TravelPlanRequest) -> PlacesResponse:
        location = await self.fetch_location(request)
        radius = request.places_radius_meters or self._settings.places_radius_meters
        key = ("places", location.latitude, location.longitude, tuple(interest.value for interest in request.interests), request.places_limit, radius)
        return await self._cache.get_or_set(
            key,
            lambda: self._call_provider(
                "places", lambda: self._places_provider.get_places(location, request.interests, request.places_limit, radius)
            ),
            ttl_seconds=self._settings.places_cache_ttl_seconds,
        )

    async def fetch_currency(self, request: TravelPlanRequest) -> CurrencyResponse:
        key = ("currency", request.source_currency, request.target_currency, request.currency_amount)
        return await self._cache.get_or_set(
            key,
            lambda: self._call_provider(
                "currency",
                lambda: self._currency_provider.get_rate(request.source_currency, request.target_currency, request.currency_amount),
            ),
            ttl_seconds=self._settings.currency_cache_ttl_seconds,
        )

    async def create_plan(self, request: TravelPlanRequest) -> TravelPlanResponse:
        """Fetch independent travel data concurrently and retain partial results."""
        names = ["weather", "places"]
        operations = [self.fetch_weather(request), self.fetch_places(request)]
        if request.currency_requested:
            names.append("currency")
            operations.append(self.fetch_currency(request))
        results = await asyncio.gather(*operations, return_exceptions=True)
        errors: list[ServiceError] = []

        outcome = dict(zip(names, results, strict=True))
        for name, result in outcome.items():
            if isinstance(result, Exception):
                logger.warning(
                    "travel provider unavailable",
                    extra={"service": name, "error_type": type(result).__name__},
                )
                message = str(result) if isinstance(result, ProviderError) else "Provider temporarily unavailable"
                errors.append(ServiceError(service=name, message=message))

        return TravelPlanResponse(
            destination=request.destination,
            weather=outcome.get("weather") if isinstance(outcome.get("weather"), WeatherResponse) else None,
            places=outcome.get("places") if isinstance(outcome.get("places"), PlacesResponse) else None,
            currency=outcome.get("currency") if isinstance(outcome.get("currency"), CurrencyResponse) else None,
            service_errors=errors,
        )

    async def _call_provider(self, service: str, operation: Callable[[], Awaitable[T]]) -> T:
        """Apply a bounded timeout and retry only clearly transient HTTP failures."""
        for attempt in range(1, self._retry_attempts + 1):
            try:
                async with asyncio.timeout(self._timeout_seconds):
                    return await operation()
            except (TimeoutError, httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as error:
                if (
                    isinstance(error, httpx.HTTPStatusError)
                    and error.response.status_code not in {429, 500, 502, 503, 504}
                ):
                    raise
                if attempt == self._retry_attempts:
                    logger.warning(
                        "travel provider failed after retries",
                        extra={"service": service, "attempts": attempt, "error_type": type(error).__name__},
                    )
                    raise
                logger.info(
                    "retrying transient travel provider failure",
                    extra={"service": service, "attempt": attempt, "error_type": type(error).__name__},
                )
                await asyncio.sleep(self._retry_delay_seconds * attempt)

