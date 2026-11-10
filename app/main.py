from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.cache import AsyncTTLCache
from app.clients.frankfurter import FrankfurterClient
from app.clients.geoapify import GeoapifyPlacesClient
from app.clients.ollama import OllamaLlmClient
from app.clients.open_meteo import OpenMeteoClient
from app.config import get_settings
from app.services.travel import TravelPlanService
from app.services.budget import BudgetService
from app.services.itinerary import ItineraryGenerationService
from app.services.itinerary_context import ItineraryContextService
from app.services.itinerary_validation import ItineraryConstraintValidator
from app.services.conversation import ConversationService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    timeout = httpx.Timeout(settings.request_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as client:
        open_meteo = OpenMeteoClient(client, settings.open_meteo_geocoding_url, settings.open_meteo_forecast_url, settings.weather_forecast_days)
        app.state.travel_plan_service = TravelPlanService(
            weather_provider=open_meteo,
            places_provider=GeoapifyPlacesClient(client, settings.geoapify_places_url, settings.geoapify_api_key),
            currency_provider=FrankfurterClient(client, settings.frankfurter_url),
            geocoding_provider=open_meteo,
            cache=AsyncTTLCache(settings.cache_ttl_seconds),
            settings=settings,
            timeout_seconds=settings.request_timeout_seconds,
            retry_attempts=settings.provider_retry_attempts,
            retry_delay_seconds=settings.provider_retry_delay_seconds,
        )
        llm_provider = OllamaLlmClient(
            client, settings.ollama_base_url, settings.ollama_model, settings.ollama_timeout_seconds
        )
        app.state.itinerary_service = ItineraryGenerationService(
            context_service=ItineraryContextService(app.state.travel_plan_service),
            llm_provider=llm_provider,
            budget_service=BudgetService(),
            validator=ItineraryConstraintValidator(),
            cache=app.state.travel_plan_service.cache,
            settings=settings,
        )
        app.state.conversation_service = ConversationService(
            llm_provider=llm_provider,
            context_service=ItineraryContextService(app.state.travel_plan_service),
            budget_service=BudgetService(),
            validator=ItineraryConstraintValidator(),
            settings=settings,
        )
        yield


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
    application.include_router(router)
    application.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="web")
    return application


app = create_app()

