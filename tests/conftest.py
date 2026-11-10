from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.schemas.travel import CurrencyResponse, PlaceResponse, PlacesResponse, ResolvedLocation, WeatherResponse
from app.schemas.itinerary import BudgetEstimate, ItineraryActivity, ItineraryContext, ItineraryDay, ItineraryResponse
from app.schemas.conversation import ConversationResponse
from app.services.travel import TravelPlanService


class FakeGeocoder:
    async def resolve_destination(self, destination: str) -> ResolvedLocation:
        return ResolvedLocation(name=destination, latitude=48.8566, longitude=2.3522, country="France")


class FakeWeather:
    async def get_weather(self, location: ResolvedLocation, start_date, end_date) -> WeatherResponse:
        return WeatherResponse(
            destination=location.name,
            summary="Partly cloudy",
            temperature_celsius=21,
            precipitation_chance=20,
            provider="test-weather",
            location=location,
        )


class FakePlaces:
    async def get_places(self, location: ResolvedLocation, interests, limit: int, radius_meters: int) -> PlacesResponse:
        return PlacesResponse(
            destination=location.name,
            places=[PlaceResponse(name="Test Museum", category="entertainment.museum")],
            provider="test-places",
            location=location,
        )


class FakeCurrency:
    async def get_rate(self, base_currency: str, target_currency: str, amount) -> CurrencyResponse:
        return CurrencyResponse(
            base_currency=base_currency,
            target_currency=target_currency,
            rate=0.92,
            amount=amount,
            converted_amount=(amount * Decimal("0.92") if amount is not None else None),
            provider="test-currency",
        )


class FakeItineraryService:
    async def generate(self, request) -> ItineraryResponse:
        return ItineraryResponse(
            destination=request.destination,
            start_date=request.start_date,
            end_date=request.end_date,
            number_of_days=request.number_of_days,
            travelers=request.travelers,
            budget=request.budget,
            currency=request.currency,
            summary="A tested itinerary.",
            days=[ItineraryDay(
                day_number=1,
                date=request.start_date,
                theme="Flexible day",
                activities=[ItineraryActivity(
                    title="Explore at leisure", description="A general suggestion.", time_of_day="morning",
                    duration_minutes=60, source="general_suggestion",
                )],
            )],
            budget_estimate=BudgetEstimate(),
        )

    async def prepare_context(self, request) -> ItineraryContext:
        return ItineraryContext(destination=request.destination)

    async def generate_from_context(self, request, context) -> ItineraryResponse:
        return await self.generate(request)


class FakeConversationService:
    async def respond(self, request) -> ConversationResponse:
        return ConversationResponse(
            conversation_id=request.conversation_id or "test-conversation",
            revision=(request.revision or 1) + 1,
            assistant_reply="I can help adjust the itinerary.",
            modified=False,
        )


@pytest.fixture
async def client():
    app = create_app()
    async with app.router.lifespan_context(app):
        app.state.travel_plan_service = TravelPlanService(
            FakeWeather(), FakePlaces(), FakeCurrency(), FakeGeocoder(), retry_delay_seconds=0
        )
        app.state.itinerary_service = FakeItineraryService()
        app.state.conversation_service = FakeConversationService()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
            yield test_client

