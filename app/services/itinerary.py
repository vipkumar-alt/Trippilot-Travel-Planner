import hashlib
import json
from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from app.cache import AsyncTTLCache
from app.clients.ollama import ItineraryLlmProvider
from app.config import Settings
from app.errors import ProviderError
from app.prompts.itinerary import SYSTEM_PROMPT, SYSTEM_PROMPT_VERSION
from app.schemas.itinerary import (
    AccommodationDetails,
    ContextPlace,
    DiningDetails,
    GeneratedItinerary,
    ItineraryActivity,
    ItineraryContext,
    ItineraryDay,
    ItineraryRequest,
    ItineraryResponse,
)
from app.services.budget import BudgetService
from app.services.itinerary_context import ItineraryContextService
from app.services.itinerary_validation import ItineraryConstraintValidator


class ItineraryGenerationService:
    def __init__(
        self,
        context_service: ItineraryContextService,
        llm_provider: ItineraryLlmProvider,
        budget_service: BudgetService,
        validator: ItineraryConstraintValidator,
        cache: AsyncTTLCache,
        settings: Settings,
    ) -> None:
        self._context_service = context_service
        self._llm_provider = llm_provider
        self._budget_service = budget_service
        self._validator = validator
        self._cache = cache
        self._settings = settings

    async def prepare_context(self, request: ItineraryRequest) -> ItineraryContext:
        if request.number_of_days > self._settings.itinerary_max_days:
            raise ProviderError(f"Itineraries are limited to {self._settings.itinerary_max_days} days")
        return await self._context_service.prepare(request)

    async def generate(self, request: ItineraryRequest) -> ItineraryResponse:
        key = ("itinerary", self._request_fingerprint(request), self._settings.ollama_model, SYSTEM_PROMPT_VERSION)
        return await self._cache.get_or_set(
            key, lambda: self._generate_uncached(request), ttl_seconds=self._settings.itinerary_cache_ttl_seconds
        )

    async def _generate_uncached(self, request: ItineraryRequest) -> ItineraryResponse:
        context = await self.prepare_context(request)
        return await self.generate_from_context(request, context)

    async def generate_from_context(self, request: ItineraryRequest, context: ItineraryContext) -> ItineraryResponse:
        generated = await self._generate_and_validate(request, context)
        budget = self._budget_service.estimate(request)

        # Assemble AccommodationDetails
        hotel_options = context.accommodation_places[:5] if context.accommodation_places else []
        acc_tips = [
            f"Recommended accommodation style: {request.accommodation_preference or request.travel_style.value.capitalize()} stay.",
            "Book verified accommodation in advance for optimal rates and location convenience.",
        ]
        if not hotel_options:
            acc_tips.append("No direct hotel API records returned; explore centrally located verified stays in the main district.")
        accommodation = AccommodationDetails(
            preference=request.accommodation_preference or f"{request.travel_style.value.capitalize()} lodging",
            recommended_area=f"Central {request.destination}",
            hotel_options=hotel_options,
            practical_tips=acc_tips,
        )

        # Assemble DiningDetails
        food_options = context.food_places[:6] if context.food_places else []
        dining_tips = [
            "Experience authentic local eateries and markets for traditional culinary highlights.",
            "Ask for dietary modifications in advance when dining at local venues.",
        ]
        if request.dietary_preferences:
            dining_tips.append(f"Dietary preferences noted: {', '.join(request.dietary_preferences)}.")
        dining = DiningDetails(
            dietary_preferences=request.dietary_preferences,
            local_specialties=[f"{request.destination} local cuisine", "Regional specialties"],
            recommended_spots=food_options,
            dining_tips=dining_tips,
        )

        return ItineraryResponse(
            destination=request.destination,
            start_date=request.start_date,
            end_date=request.end_date,
            number_of_days=request.number_of_days,
            travelers=request.travelers,
            budget=request.budget,
            currency=request.currency,
            summary=generated.summary,
            days=generated.days,
            budget_estimate=budget,
            accommodation=accommodation,
            dining=dining,
            assumptions=generated.assumptions,
            limitations=list(dict.fromkeys([*context.limitations, *generated.limitations, *budget.limitations])),
            practical_tips=generated.practical_tips,
            validation_warnings=[],
            source_references=self._source_references(context),
        )

    async def _generate_and_validate(self, request: ItineraryRequest, context: ItineraryContext) -> GeneratedItinerary:
        user_prompt = self._build_user_prompt(request, context)
        errors: list[str] = []
        last_exception: Exception | None = None
        for attempt in range(self._settings.itinerary_repair_attempts + 1):
            prompt = user_prompt if not errors else f"{user_prompt}\n\nREPAIR THESE ERRORS:\n- " + "\n- ".join(errors)
            try:
                raw = await self._llm_provider.generate(SYSTEM_PROMPT, prompt, GeneratedItinerary.model_json_schema())
            except Exception as exc:
                last_exception = exc
                continue
            raw = self._normalize_provider_attributions(raw, context)
            try:
                generated = GeneratedItinerary.model_validate(raw)
            except ValidationError as error:
                errors = [f"Structured itinerary validation failed: {item['msg']}" for item in error.errors()]
                continue
            errors = self._validator.validate(generated, request, context)
            if not errors:
                return generated

        # If LLM failed repair or was temporarily unavailable, produce a grounded deterministic fallback itinerary
        return self._generate_fallback_itinerary(request, context)

    def _generate_fallback_itinerary(self, request: ItineraryRequest, context: ItineraryContext) -> GeneratedItinerary:
        """Deterministic, grounded fallback itinerary constructed directly from verified provider context."""
        from datetime import timedelta
        days: list[ItineraryDay] = []
        available_places = [p for p in context.places if p.name and p.place_id]
        place_idx = 0

        for day_num in range(1, request.number_of_days + 1):
            day_date = request.start_date + timedelta(days=day_num - 1)
            activities: list[ItineraryActivity] = []

            # Morning activity
            if place_idx < len(available_places):
                p = available_places[place_idx]
                place_idx += 1
                activities.append(ItineraryActivity(
                    title=f"Visit {p.name}",
                    description=f"Explore {p.name} located in {request.destination}.",
                    time_of_day="morning",
                    duration_minutes=120,
                    place_id=p.place_id,
                    place_name=p.name,
                    source="geoapify",
                ))
            else:
                activities.append(ItineraryActivity(
                    title=f"Morning exploration of {request.destination}",
                    description=f"Take a morning walk through local historic streets and neighborhood markets.",
                    time_of_day="morning",
                    duration_minutes=90,
                    source="general_suggestion",
                ))

            # Afternoon activity
            if place_idx < len(available_places):
                p = available_places[place_idx]
                place_idx += 1
                activities.append(ItineraryActivity(
                    title=f"Discover {p.name}",
                    description=f"Spend the afternoon discovering {p.name}.",
                    time_of_day="afternoon",
                    duration_minutes=90,
                    place_id=p.place_id,
                    place_name=p.name,
                    source="geoapify",
                ))
            else:
                activities.append(ItineraryActivity(
                    title=f"Afternoon cultural visit",
                    description=f"Enjoy regional culture and local culinary delicacies in {request.destination}.",
                    time_of_day="afternoon",
                    duration_minutes=90,
                    source="general_suggestion",
                ))

            # Evening activity
            activities.append(ItineraryActivity(
                title=f"Evening relaxation & dining",
                description=f"Unwind with traditional regional dinner and leisure stroll.",
                time_of_day="evening",
                duration_minutes=60,
                source="general_suggestion",
            ))

            days.append(ItineraryDay(
                day_number=day_num,
                date=day_date,
                theme=f"Discover {request.destination} - Day {day_num}",
                activities=activities,
                weather_note=f"Check local conditions before departing for daytime outdoor activities.",
            ))

        return GeneratedItinerary(
            summary=f"A curated {request.number_of_days}-day itinerary for {request.destination} tailored to your {request.travel_style.value} pace.",
            days=days,
            assumptions=[f"Activities paced for {request.pace.value} travel style."],
            limitations=["Itinerary generated with fallback heuristics to ensure robust end-to-end plan delivery."],
            practical_tips=[
                "Carry local currency for smaller venues and local transport.",
                "Verify venue operating hours and local guidelines before visiting.",
            ],
        )

    @staticmethod
    def _normalize_provider_attributions(raw: dict[str, Any], context: ItineraryContext) -> dict[str, Any]:
        """Recover a unique verified reference or remove unsupported attribution.

        Some smaller local models label generic suggestions as Geoapify activities but
        omit the identifier needed to verify that claim. The identifier is the trust
        boundary: an invented or guessed value remains a validation error, while a
        missing value is matched only against one exact provider name in the model's
        activity text. Otherwise it is safely represented as a general suggestion.
        """
        normalized = deepcopy(raw)
        downgraded = False
        all_context_places = context.places + context.accommodation_places + context.food_places
        places_by_name = {
            place.name.casefold(): place
            for place in all_context_places
            if place.name and place.place_id
        }
        places_by_id = {place.place_id: place for place in all_context_places}
        days = normalized.get("days")
        if not isinstance(days, list):
            return normalized
        for day in days:
            if not isinstance(day, dict) or not isinstance(day.get("activities"), list):
                continue
            for activity in day["activities"]:
                if not isinstance(activity, dict):
                    continue
                if activity.get("source") != "geoapify":
                    continue
                place_id = activity.get("place_id")
                if place_id:
                    verified_place = places_by_id.get(place_id)
                    if verified_place:
                        activity["place_name"] = verified_place.name
                    continue
                text = " ".join(
                    value for value in (activity.get("title"), activity.get("description")) if isinstance(value, str)
                ).casefold()
                matches = [place for name, place in places_by_name.items() if name in text]
                if len(matches) == 1:
                    activity["place_id"] = matches[0].place_id
                    activity["place_name"] = matches[0].name
                    continue
                activity["source"] = "general_suggestion"
                activity.pop("place_id", None)
                activity.pop("place_name", None)
                downgraded = True
        if downgraded:
            limitations = normalized.setdefault("limitations", [])
            if isinstance(limitations, list):
                limitations.append(
                    "Some activities are general suggestions because the model did not provide verifiable place identifiers."
                )
        return normalized

    @staticmethod
    def _build_user_prompt(request: ItineraryRequest, context: ItineraryContext) -> str:
        payload: dict[str, Any] = {
            "request": request.model_dump(mode="json"),
            "context_places": [place.model_dump() for place in context.places],
            "weather": context.weather,
            "limitations": context.limitations,
        }
        return "Generate an itinerary from this JSON evidence:\n" + json.dumps(payload, separators=(",", ":"))

    @staticmethod
    def _source_references(context: ItineraryContext) -> list[str]:
        sources = ["geoapify" for _ in context.places]
        if context.weather:
            sources.append("open-meteo")
        return list(dict.fromkeys(sources + [place.place_id for place in context.places]))

    @staticmethod
    def _request_fingerprint(request: ItineraryRequest) -> str:
        serialized = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode()).hexdigest()
