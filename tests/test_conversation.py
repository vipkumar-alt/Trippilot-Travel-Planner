from datetime import date
from decimal import Decimal

import pytest

from app.config import Settings
from app.errors import RevisionConflictError
from app.schemas.conversation import ConversationAction, ConversationMessageRequest
from app.schemas.itinerary import (
    BudgetEstimate,
    ContextPlace,
    ItineraryActivity,
    ItineraryContext,
    ItineraryDay,
    ItineraryRequest,
    ItineraryResponse,
)
from app.services.budget import BudgetService
from app.services.conversation import ConversationService
from app.services.itinerary_validation import ItineraryConstraintValidator


class ContextService:
    async def prepare(self, request):
        return ItineraryContext(destination=request.destination, places=[ContextPlace(place_id="park-1", name="Verified Park")])


class DecisionLlm:
    def __init__(self, decisions): self.decisions = decisions
    async def generate(self, system_prompt, user_prompt, schema): return self.decisions.pop(0)


def trip_request() -> ItineraryRequest:
    return ItineraryRequest(destination="Paris", start_date=date(2026, 11, 1), end_date=date(2026, 11, 1))


def itinerary() -> ItineraryResponse:
    return ItineraryResponse(
        destination="Paris", start_date=date(2026, 11, 1), end_date=date(2026, 11, 1), number_of_days=1,
        travelers=1, summary="A day in Paris.", budget_estimate=BudgetEstimate(),
        days=[ItineraryDay(day_number=1, date=date(2026, 11, 1), theme="Culture", activities=[ItineraryActivity(
            title="Museum visit", description="A general activity.", time_of_day="morning", duration_minutes=90,
            source="general_suggestion",
        )])],
    )


def decision(action, **kwargs):
    return {"action": action, "reply": "Done.", **kwargs}


@pytest.mark.asyncio
async def test_replacement_preserves_other_itinerary_content_and_increments_revision() -> None:
    service = ConversationService(
        DecisionLlm([decision("replace_place", day_number=1, activity_title="Museum visit", replacement_place_id="park-1")]),
        ContextService(), BudgetService(), ItineraryConstraintValidator(), Settings(),
    )
    response = await service.respond(ConversationMessageRequest(message="Replace the museum", itinerary=itinerary(), trip_request=trip_request()))

    assert response.modified is True
    assert response.revision == 2
    assert response.itinerary.days[0].activities[0].place_id == "park-1"
    assert response.itinerary.days[0].activities[0].place_name == "Verified Park"


@pytest.mark.asyncio
async def test_conversation_state_isolated_and_rejects_stale_revision() -> None:
    service = ConversationService(
        DecisionLlm([
            decision("answer_question"), decision("answer_question"),
            decision("change_trip_constraints", new_budget="100", new_currency="EUR"),
        ]),
        ContextService(), BudgetService(), ItineraryConstraintValidator(), Settings(),
    )
    first = await service.respond(ConversationMessageRequest(message="Explain it", itinerary=itinerary(), trip_request=trip_request()))
    second = await service.respond(ConversationMessageRequest(message="Explain it", itinerary=itinerary(), trip_request=trip_request()))
    assert first.conversation_id != second.conversation_id

    changed = await service.respond(ConversationMessageRequest(message="Set my total to 100 EUR", conversation_id=first.conversation_id, revision=1))
    assert changed.revision == 2
    with pytest.raises(RevisionConflictError):
        await service.respond(ConversationMessageRequest(message="Again", conversation_id=first.conversation_id, revision=1))


@pytest.mark.asyncio
async def test_unverified_replacement_does_not_mutate_itinerary() -> None:
    service = ConversationService(
        DecisionLlm([decision("replace_place", day_number=1, activity_title="Museum visit", replacement_place_id="made-up")]),
        ContextService(), BudgetService(), ItineraryConstraintValidator(), Settings(),
    )
    response = await service.respond(ConversationMessageRequest(message="Replace it", itinerary=itinerary(), trip_request=trip_request()))
    assert response.modified is False
    assert response.revision == 1
    assert "not in the available verified places" in response.assistant_reply


@pytest.mark.asyncio
async def test_common_budget_and_day_questions_do_not_require_llm_routing() -> None:
    service = ConversationService(
        DecisionLlm([]), ContextService(), BudgetService(), ItineraryConstraintValidator(), Settings(),
    )
    plan = itinerary().model_copy(update={"budget_estimate": BudgetService().estimate(trip_request().model_copy(update={"budget": Decimal("300"), "currency": "EUR"}))})

    start = await service.respond(ConversationMessageRequest(message="Explain day 1", itinerary=plan, trip_request=trip_request()))
    answer = await service.respond(ConversationMessageRequest(message="Will this fit my budget?", conversation_id=start.conversation_id, revision=1))

    assert "Day 1" in start.assistant_reply
    assert "EUR 300" in answer.assistant_reply
