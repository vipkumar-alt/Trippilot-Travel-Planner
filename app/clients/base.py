from typing import Protocol

from datetime import date
from decimal import Decimal

from app.schemas.travel import CurrencyResponse, Interest, PlacesResponse, ResolvedLocation, WeatherResponse


class GeocodingProvider(Protocol):
    async def resolve_destination(self, destination: str) -> ResolvedLocation: ...


class WeatherProvider(Protocol):
    async def get_weather(
        self, location: ResolvedLocation, start_date: date | None, end_date: date | None
    ) -> WeatherResponse: ...


class PlacesProvider(Protocol):
    async def get_places(
        self, location: ResolvedLocation, interests: list[Interest], limit: int, radius_meters: int
    ) -> PlacesResponse: ...


class CurrencyProvider(Protocol):
    async def get_rate(
        self, base_currency: str, target_currency: str, amount: Decimal | None
    ) -> CurrencyResponse: ...

