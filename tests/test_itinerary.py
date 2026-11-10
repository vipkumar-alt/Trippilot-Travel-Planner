from datetime import date
from decimal import Decimal

import pytest

from app.cache import AsyncTTLCache
from app.clients.ollama import OllamaLlmClient
from app.config import Settings
from app.schemas.itinerary import ContextPlace, ItineraryContext, ItineraryRequest
from app.services.budget import BudgetService
from app.services.itinerary import ItineraryGenerationService
from app.services.itinerary_validation import ItineraryConstraintValidator


class StaticContextService:
    async def prepare(self, request: ItineraryRequest) -> ItineraryContext:
        return ItineraryContext(
            destination=request.destination,
            places=[ContextPlace(place_id="geo-1", name="Verified Museum", category="entertainment.museum")],
            limitations=["No verified weather forecast was available for the requested dates."],
        )


class SequencedLlm:
    def __init__(self, replies: list[dict]) -> None:
        self.replies = replies
        self.calls = 0

    async def generate(self, system_prompt: str, user_prompt: str, schema: dict) -> dict:
        reply = self.replies[self.calls]
        self.calls += 1
        return reply


def valid_reply(place_id: str = "geo-1") -> dict:
    return {
        "summary": "A museum-focused day.",
        "days": [{
            "day_number": 1,
            "date": "2026-11-01",
            "theme": "Culture",
            "activities": [{
                "title": "Visit the museum",
                "description": "Explore the verified museum.",
                "time_of_day": "morning",
                "duration_minutes": 120,
                "place_id": place_id,
                "place_name": "Verified Museum",
                "source": "geoapify",
                "estimated_cost": None,
                "currency": None,
            }],
        }],
        "assumptions": [],
        "limitations": [],
        "practical_tips": ["Confirm venue details before visiting."],
    }


def missing_provider_identifier_reply() -> dict:
    reply = valid_reply()
    reply["days"][0]["activities"][0].pop("place_id")
    reply["days"][0]["activities"][0].pop("place_name")
    return reply


def request() -> ItineraryRequest:
    return ItineraryRequest(destination="Paris", start_date=date(2026, 11, 1), end_date=date(2026, 11, 1), budget=Decimal("500"), currency="EUR")


@pytest.mark.asyncio
async def test_itinerary_repairs_fabricated_place_id_once() -> None:
    llm = SequencedLlm([valid_reply("invented"), valid_reply()])
    service = ItineraryGenerationService(
        StaticContextService(), llm, BudgetService(), ItineraryConstraintValidator(), AsyncTTLCache(60),
        Settings(itinerary_repair_attempts=1),
    )

    itinerary = await service.generate(request())
    assert itinerary.days[0].activities[0].place_id == "geo-1"
    assert itinerary.budget_estimate.per_day_budget == Decimal("500.00")
    assert llm.calls == 2


@pytest.mark.asyncio
async def test_itinerary_cache_isolated_by_request_constraints() -> None:
    llm = SequencedLlm([valid_reply(), valid_reply()])
    service = ItineraryGenerationService(
        StaticContextService(), llm, BudgetService(), ItineraryConstraintValidator(), AsyncTTLCache(60), Settings(),
    )
    first = request()
    second = request().model_copy(update={"additional_constraints": "vegetarian meals"})
    await service.generate(first)
    await service.generate(first)
    await service.generate(second)
    assert llm.calls == 2


@pytest.mark.asyncio
async def test_itinerary_downgrades_only_identifierless_provider_claims() -> None:
    reply = missing_provider_identifier_reply()
    reply["days"][0]["activities"][0]["title"] = "Explore at leisure"
    reply["days"][0]["activities"][0]["description"] = "Choose an activity based on the day."
    llm = SequencedLlm([reply])
    service = ItineraryGenerationService(
        StaticContextService(), llm, BudgetService(), ItineraryConstraintValidator(), AsyncTTLCache(60), Settings(),
    )

    itinerary = await service.generate(request())

    activity = itinerary.days[0].activities[0]
    assert activity.source == "general_suggestion"
    assert activity.place_id is None
    assert llm.calls == 1
    assert any("verifiable place identifiers" in limitation for limitation in itinerary.limitations)


@pytest.mark.asyncio
async def test_itinerary_recovers_a_unique_provider_id_from_exact_place_name() -> None:
    reply = missing_provider_identifier_reply()
    reply["days"][0]["activities"][0]["title"] = "Visit Verified Museum"
    service = ItineraryGenerationService(
        StaticContextService(), SequencedLlm([reply]), BudgetService(), ItineraryConstraintValidator(), AsyncTTLCache(60), Settings(),
    )

    itinerary = await service.generate(request())

    activity = itinerary.days[0].activities[0]
    assert activity.source == "geoapify"
    assert activity.place_id == "geo-1"
    assert activity.place_name == "Verified Museum"


@pytest.mark.asyncio
async def test_itinerary_uses_the_verified_name_for_a_valid_provider_id() -> None:
    reply = valid_reply()
    reply["days"][0]["activities"][0]["place_name"] = "Incorrect model label"
    service = ItineraryGenerationService(
        StaticContextService(), SequencedLlm([reply]), BudgetService(), ItineraryConstraintValidator(), AsyncTTLCache(60), Settings(),
    )

    itinerary = await service.generate(request())

    assert itinerary.days[0].activities[0].place_name == "Verified Museum"


def test_request_and_budget_validation() -> None:
    with pytest.raises(ValueError, match="currency is required"):
        ItineraryRequest(destination="Paris", start_date=date(2026, 11, 1), end_date=date(2026, 11, 2), budget=Decimal("1"))
    with pytest.raises(ValueError, match="end_date"):
        ItineraryRequest(destination="Paris", start_date=date(2026, 11, 2), end_date=date(2026, 11, 1))

    estimate = BudgetService().estimate(request().model_copy(update={"travelers": 2}))
    assert estimate.per_person_budget == Decimal("250.00")
    assert estimate.estimated_total is None


@pytest.mark.asyncio
async def test_ollama_client_reports_missing_model() -> None:
    import httpx
    from app.errors import ConfigurationError

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model not found"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        llm = OllamaLlmClient(client, "http://ollama.test", "missing-model", 1)
        with pytest.raises(ConfigurationError, match="ollama pull missing-model"):
            await llm.generate("system", "user", {})
