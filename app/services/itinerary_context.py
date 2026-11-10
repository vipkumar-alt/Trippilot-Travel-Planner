from app.schemas.itinerary import ContextPlace, ItineraryContext, ItineraryInterest, ItineraryRequest
from app.schemas.travel import Interest, TravelPlanRequest
from app.services.travel import TravelPlanService

_INTEREST_MAP = {
    ItineraryInterest.HISTORY: Interest.CULTURE,
    ItineraryInterest.CULTURE: Interest.CULTURE,
    ItineraryInterest.FOOD: Interest.FOOD,
    ItineraryInterest.NATURE: Interest.NATURE,
    ItineraryInterest.ADVENTURE: Interest.NATURE,
    ItineraryInterest.SHOPPING: Interest.SHOPPING,
    ItineraryInterest.NIGHTLIFE: Interest.NIGHTLIFE,
    ItineraryInterest.FAMILY: Interest.FAMILY,
}


class ItineraryContextService:
    def __init__(self, travel_service: TravelPlanService) -> None:
        self._travel_service = travel_service

    async def prepare(self, request: ItineraryRequest) -> ItineraryContext:
        interests = list(dict.fromkeys(_INTEREST_MAP[item] for item in request.interests))
        # Ensure culture/sights interest is present by default if empty
        if not interests:
            interests = [Interest.CULTURE]

        # Concurrently fetch sights/attractions, accommodation places, and food places
        travel_request = TravelPlanRequest(
            destination=request.destination,
            start_date=request.start_date,
            end_date=request.end_date,
            interests=interests,
            places_limit=15,
        )

        hotel_request = TravelPlanRequest(
            destination=request.destination,
            start_date=request.start_date,
            end_date=request.end_date,
            interests=[Interest.ACCOMMODATION],
            places_limit=10,
        )

        food_request = TravelPlanRequest(
            destination=request.destination,
            start_date=request.start_date,
            end_date=request.end_date,
            interests=[Interest.FOOD],
            places_limit=10,
        )

        import asyncio
        main_task = self._travel_service.create_plan(travel_request)
        hotel_task = self._travel_service.fetch_places(hotel_request)
        food_task = self._travel_service.fetch_places(food_request)

        results = await asyncio.gather(main_task, hotel_task, food_task, return_exceptions=True)
        result = results[0] if not isinstance(results[0], Exception) else None
        hotel_result = results[1] if not isinstance(results[1], Exception) else None
        food_result = results[2] if not isinstance(results[2], Exception) else None

        places: list[ContextPlace] = []
        seen: set[str] = set()

        def extract_places(places_resp) -> list[ContextPlace]:
            extracted = []
            if places_resp and hasattr(places_resp, "places"):
                for place in places_resp.places:
                    if not place.provider_id or place.provider_id in seen:
                        continue
                    # Ignore unnamed places for clean LLM context
                    if not place.name:
                        continue
                    seen.add(place.provider_id)
                    extracted.append(ContextPlace(
                        place_id=place.provider_id,
                        name=place.name,
                        category=place.category,
                        address=place.address,
                        latitude=place.latitude,
                        longitude=place.longitude,
                    ))
            return extracted

        if result and result.places:
            places = extract_places(result.places)

        accommodation_places = extract_places(hotel_result)
        food_places = extract_places(food_result)

        weather = []
        limitations = []
        source_errors = []
        if result:
            source_errors = result.service_errors
            limitations = [error.message for error in result.service_errors]
            if result.weather:
                weather = [forecast.model_dump(mode="json") for forecast in result.weather.forecasts]

        if not weather:
            limitations.append("No verified weather forecast was available for the requested dates.")
        if not places and not food_places:
            limitations.append("No verified places were available; general suggestions are not provider-verified.")

        return ItineraryContext(
            destination=request.destination,
            weather=weather,
            places=places,
            accommodation_places=accommodation_places,
            food_places=food_places,
            source_errors=source_errors,
            limitations=list(dict.fromkeys(limitations)),
        )
