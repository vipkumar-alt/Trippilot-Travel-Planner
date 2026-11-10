from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import Query, Request
from pydantic import ValidationError
from fastapi.exceptions import RequestValidationError

from app.schemas.travel import Interest, TravelPlanRequest
from app.services.travel import TravelPlanService
from app.services.itinerary import ItineraryGenerationService
from app.services.conversation import ConversationService


def get_travel_plan_service(request: Request) -> TravelPlanService:
    return request.app.state.travel_plan_service


def get_itinerary_service(request: Request) -> ItineraryGenerationService:
    return request.app.state.itinerary_service


def get_conversation_service(request: Request) -> ConversationService:
    return request.app.state.conversation_service


async def get_travel_plan_query(
    destination: Annotated[str, Query(min_length=2, max_length=100)],
    start_date: date | None = None,
    end_date: date | None = None,
    interests: Annotated[list[Interest] | None, Query()] = None,
    source_currency: Annotated[str, Query(min_length=3, max_length=3)] = "USD",
    target_currency: Annotated[str, Query(min_length=3, max_length=3)] = "USD",
    currency_amount: Decimal | None = Query(default=None, gt=0),
    places_limit: int = Query(default=10, ge=1, le=20),
    places_radius_meters: int | None = Query(default=None, ge=100, le=50_000),
) -> TravelPlanRequest:
    try:
        return TravelPlanRequest(
            destination=destination,
            start_date=start_date,
            end_date=end_date,
            interests=interests or [],
            source_currency=source_currency,
            target_currency=target_currency,
            currency_amount=currency_amount,
            places_limit=places_limit,
            places_radius_meters=places_radius_meters,
        )
    except ValidationError as error:
        raise RequestValidationError(error.errors()) from error

