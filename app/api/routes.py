import asyncio
import json
import logging
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_conversation_service, get_itinerary_service, get_travel_plan_query, get_travel_plan_service
from app.errors import ConfigurationError, ConversationNotFoundError, ProviderError, RevisionConflictError
from app.schemas.conversation import ConversationMessageRequest, ConversationResponse
from app.schemas.itinerary import ItineraryRequest, ItineraryResponse
from app.schemas.travel import CurrencyResponse, PlacesResponse, ServiceError, TravelPlanRequest, TravelPlanResponse, WeatherResponse
from app.services.travel import TravelPlanService
from app.services.itinerary import ItineraryGenerationService
from app.services.conversation import ConversationService

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/api/v1/travel-plan", response_model=TravelPlanResponse, tags=["travel"])
async def create_travel_plan(
    payload: TravelPlanRequest,
    service: TravelPlanService = Depends(get_travel_plan_service),
) -> TravelPlanResponse:
    return await service.create_plan(payload)


@router.post("/api/v1/itineraries/generate", response_model=ItineraryResponse, tags=["itinerary"])
async def generate_itinerary(
    payload: ItineraryRequest,
    service: ItineraryGenerationService = Depends(get_itinerary_service),
) -> ItineraryResponse:
    try:
        return await service.generate(payload)
    except ConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ProviderError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@router.post("/api/v1/conversations/messages", response_model=ConversationResponse, tags=["conversation"])
async def send_conversation_message(
    payload: ConversationMessageRequest,
    service: ConversationService = Depends(get_conversation_service),
) -> ConversationResponse:
    try:
        return await service.respond(payload)
    except ConversationNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RevisionConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ProviderError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@router.post("/api/v1/itineraries/generate/stream", tags=["itinerary"])
async def stream_itinerary(
    request: Request,
    payload: ItineraryRequest,
    service: ItineraryGenerationService = Depends(get_itinerary_service),
) -> StreamingResponse:
    return StreamingResponse(
        _itinerary_events(request, payload, service),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/v1/weather", response_model=WeatherResponse, tags=["travel"])
async def get_weather(
    payload: Annotated[TravelPlanRequest, Depends(get_travel_plan_query)],
    service: Annotated[TravelPlanService, Depends(get_travel_plan_service)],
) -> WeatherResponse:
    try:
        return await service.fetch_weather(payload)
    except ProviderError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get("/api/v1/places", response_model=PlacesResponse, tags=["travel"])
async def get_places(
    payload: Annotated[TravelPlanRequest, Depends(get_travel_plan_query)],
    service: Annotated[TravelPlanService, Depends(get_travel_plan_service)],
) -> PlacesResponse:
    try:
        return await service.fetch_places(payload)
    except ProviderError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get("/api/v1/currency", response_model=CurrencyResponse, tags=["travel"])
async def get_currency(
    base: Annotated[str, Query(min_length=3, max_length=3)],
    target: Annotated[str, Query(min_length=3, max_length=3)],
    amount: Decimal | None = Query(default=None, gt=0),
    service: TravelPlanService = Depends(get_travel_plan_service),
) -> CurrencyResponse:
    try:
        request = TravelPlanRequest(
            destination="Currency", source_currency=base, target_currency=target, currency_amount=amount
        )
        return await service.fetch_currency(request)
    except ProviderError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/api/v1/travel-plan/stream", tags=["travel"])
async def stream_travel_plan(
    request: Request,
    payload: Annotated[TravelPlanRequest, Depends(get_travel_plan_query)],
    service: Annotated[TravelPlanService, Depends(get_travel_plan_service)],
) -> StreamingResponse:
    return StreamingResponse(
        _travel_plan_events(request, payload, service),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _travel_plan_events(
    request: Request, payload: TravelPlanRequest, service: TravelPlanService
) -> AsyncIterator[str]:
    tasks = {
        asyncio.create_task(service.fetch_weather(payload)): "weather",
        asyncio.create_task(service.fetch_places(payload)): "places",
    }
    if payload.currency_requested:
        tasks[asyncio.create_task(service.fetch_currency(payload))] = "currency"
    errors: list[ServiceError] = []
    try:
        while tasks:
            if await request.is_disconnected():
                logger.info("SSE client disconnected", extra={"destination": payload.destination})
                return
            done, _ = await asyncio.wait(tasks, timeout=0.25, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                event_name = tasks.pop(task)
                try:
                    result = task.result()
                except Exception:
                    error = ServiceError(service=event_name, message="Provider temporarily unavailable")
                    errors.append(error)
                    yield _sse_message("error", error.model_dump(mode="json"))
                else:
                    yield _sse_message(event_name, result.model_dump(mode="json"))
        yield _sse_message(
            "complete", {"destination": payload.destination, "service_errors": [error.model_dump() for error in errors]}
        )
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def _sse_message(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def _itinerary_events(
    request: Request, payload: ItineraryRequest, service: ItineraryGenerationService
) -> AsyncIterator[str]:
    try:
        yield _sse_message("status", {"stage": "preparing_context"})
        context = await service.prepare_context(payload)
        if await request.is_disconnected():
            return
        yield _sse_message("context_ready", {"limitations": context.limitations, "place_count": len(context.places)})
        task = asyncio.create_task(service.generate_from_context(payload, context))
        while not task.done():
            if await request.is_disconnected():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                return
            await asyncio.sleep(0.1)
        itinerary = task.result()
        for warning in itinerary.limitations + itinerary.validation_warnings:
            yield _sse_message("warning", {"message": warning})
        yield _sse_message("itinerary", itinerary.model_dump(mode="json"))
        yield _sse_message("complete", {"status": "ok"})
    except (ConfigurationError, ProviderError) as error:
        yield _sse_message("error", {"message": str(error)})
        yield _sse_message("complete", {"status": "error"})

