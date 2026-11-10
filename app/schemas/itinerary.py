from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.travel import PlaceResponse, ServiceError


class ItineraryInterest(str, Enum):
    HISTORY = "history"
    CULTURE = "culture"
    FOOD = "food"
    NATURE = "nature"
    SHOPPING = "shopping"
    ADVENTURE = "adventure"
    NIGHTLIFE = "nightlife"
    FAMILY = "family"


class TravelStyle(str, Enum):
    BUDGET = "budget"
    BALANCED = "balanced"
    LUXURY = "luxury"


class Pace(str, Enum):
    RELAXED = "relaxed"
    MODERATE = "moderate"
    PACKED = "packed"


class ItineraryRequest(BaseModel):
    destination: str = Field(min_length=2, max_length=100)
    start_date: date
    end_date: date
    budget: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    travelers: int = Field(default=1, ge=1, le=20)
    interests: list[ItineraryInterest] = Field(default_factory=list, max_length=6)
    travel_style: TravelStyle = TravelStyle.BALANCED
    pace: Pace = Pace.MODERATE
    accommodation_preference: str | None = Field(default=None, max_length=200)
    dietary_preferences: list[str] = Field(default_factory=list, max_length=10)
    additional_constraints: str | None = Field(default=None, max_length=1_000)

    @field_validator("destination")
    @classmethod
    def normalize_destination(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("destination must not be blank")
        return value

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.isalpha():
            raise ValueError("currency must be a three-letter alphabetic code")
        return value.upper()

    @model_validator(mode="after")
    def validate_request(self) -> "ItineraryRequest":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if self.budget is not None and self.currency is None:
            raise ValueError("currency is required when budget is provided")
        return self

    @property
    def number_of_days(self) -> int:
        return (self.end_date - self.start_date).days + 1


class ContextPlace(BaseModel):
    place_id: str
    name: str | None = None
    category: str | None = None
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class AccommodationDetails(BaseModel):
    preference: str | None = None
    recommended_area: str | None = None
    hotel_options: list[ContextPlace] = Field(default_factory=list)
    practical_tips: list[str] = Field(default_factory=list)


class DiningDetails(BaseModel):
    dietary_preferences: list[str] = Field(default_factory=list)
    local_specialties: list[str] = Field(default_factory=list)
    recommended_spots: list[ContextPlace] = Field(default_factory=list)
    dining_tips: list[str] = Field(default_factory=list)


class ItineraryContext(BaseModel):
    destination: str
    weather: list[dict[str, object]] = Field(default_factory=list)
    places: list[ContextPlace] = Field(default_factory=list)
    accommodation_places: list[ContextPlace] = Field(default_factory=list)
    food_places: list[ContextPlace] = Field(default_factory=list)
    source_errors: list[ServiceError] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ItineraryActivity(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=500)
    time_of_day: Literal["morning", "afternoon", "evening", "flexible"]
    duration_minutes: int = Field(ge=15, le=480)
    place_id: str | None = None
    place_name: str | None = Field(default=None, max_length=160)
    source: Literal["geoapify", "general_suggestion"]
    estimated_cost: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    practical_note: str | None = Field(default=None, max_length=300)


class ItineraryDay(BaseModel):
    day_number: int = Field(ge=1)
    date: date
    theme: str = Field(min_length=1, max_length=160)
    activities: list[ItineraryActivity] = Field(min_length=1, max_length=8)
    weather_note: str | None = Field(default=None, max_length=300)


class BudgetEstimate(BaseModel):
    stated_budget: Decimal | None = None
    currency: str | None = None
    per_day_budget: Decimal | None = None
    per_person_budget: Decimal | None = None
    estimated_total: Decimal | None = None
    accommodation_estimate: Decimal | None = None
    food_estimate: Decimal | None = None
    transport_estimate: Decimal | None = None
    activities_estimate: Decimal | None = None
    other_estimate: Decimal | None = None
    contingency_estimate: Decimal | None = None
    limitations: list[str] = Field(default_factory=list)


class GeneratedItinerary(BaseModel):
    summary: str = Field(min_length=1, max_length=600)
    days: list[ItineraryDay]
    accommodation_notes: str | None = Field(default=None, max_length=500)
    dining_notes: str | None = Field(default=None, max_length=500)
    assumptions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    practical_tips: list[str] = Field(default_factory=list)


class ItineraryResponse(BaseModel):
    destination: str
    start_date: date
    end_date: date
    number_of_days: int
    travelers: int
    budget: Decimal | None = None
    currency: str | None = None
    summary: str = Field(min_length=1, max_length=600)
    days: list[ItineraryDay]
    budget_estimate: BudgetEstimate
    accommodation: AccommodationDetails | None = None
    dining: DiningDetails | None = None
    assumptions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    practical_tips: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)
    source_references: list[str] = Field(default_factory=list)
