from typing import Any

import httpx

from app.errors import ConfigurationError, ProviderError
from app.schemas.travel import Interest, PlaceResponse, PlacesResponse, ResolvedLocation

_INTEREST_CATEGORIES = {
    Interest.CULTURE: ("tourism.attraction", "tourism.sights", "entertainment.museum"),
    Interest.FOOD: ("catering.restaurant", "catering.cafe"),
    Interest.NATURE: ("leisure.park", "national_park", "natural"),
    Interest.SHOPPING: ("commercial.shopping_mall",),
    Interest.NIGHTLIFE: ("entertainment", "catering.bar", "catering.pub"),
    Interest.FAMILY: ("entertainment.zoo", "entertainment.theme_park", "leisure.playground"),
    Interest.ACCOMMODATION: ("accommodation.hotel", "accommodation.guest_house", "accommodation.hostel", "accommodation"),
}
_DEFAULT_CATEGORIES = ("tourism.attraction", "tourism.sights", "entertainment.museum")


class GeoapifyPlacesClient:
    def __init__(self, client: httpx.AsyncClient, places_url: str, api_key: str | None) -> None:
        self._client = client
        self._places_url = places_url
        self._api_key = api_key

    async def get_places(
        self, location: ResolvedLocation, interests: list[Interest], limit: int, radius_meters: int
    ) -> PlacesResponse:
        if not self._api_key:
            raise ConfigurationError("Geoapify integration is not configured; set TRIPPILOT_GEOAPIFY_API_KEY")
        categories = self._categories(interests)
        response = await self._client.get(
            self._places_url,
            params={
                "categories": ",".join(categories),
                "filter": f"circle:{location.longitude},{location.latitude},{radius_meters}",
                "bias": f"proximity:{location.longitude},{location.latitude}",
                "limit": limit,
                "apiKey": self._api_key,
            },
        )
        if response.status_code in {401, 403}:
            raise ConfigurationError("Geoapify credentials were rejected")
        if response.status_code == 429:
            raise ProviderError("Geoapify rate limit was reached")
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as error:
            raise ProviderError("Geoapify returned invalid JSON") from error
        features = payload.get("features") if isinstance(payload, dict) else None
        if not isinstance(features, list):
            raise ProviderError("Geoapify returned an invalid places response")
        return PlacesResponse(
            destination=location.name,
            places=[place for feature in features if (place := self._normalize_place(feature)) is not None],
            provider="geoapify",
            location=location,
        )

    @staticmethod
    def _categories(interests: list[Interest]) -> tuple[str, ...]:
        selected = [category for interest in interests for category in _INTEREST_CATEGORIES[interest]]
        return tuple(dict.fromkeys(selected)) or _DEFAULT_CATEGORIES

    @staticmethod
    def _normalize_place(feature: Any) -> PlaceResponse | None:
        if not isinstance(feature, dict):
            return None
        properties = feature.get("properties")
        geometry = feature.get("geometry")
        if not isinstance(properties, dict):
            return None
        coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
        longitude = latitude = None
        if isinstance(coordinates, list) and len(coordinates) >= 2:
            longitude, latitude = coordinates[0], coordinates[1]
        categories = properties.get("categories")
        category = categories[0] if isinstance(categories, list) and categories else None
        return PlaceResponse(
            name=properties.get("name"),
            category=category,
            address=properties.get("formatted"),
            latitude=latitude,
            longitude=longitude,
            provider_id=properties.get("place_id"),
        )
