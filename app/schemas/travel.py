from datetime import date
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator


class Interest(str, Enum):
    CULTURE = "culture"
    FOOD = "food"
    NATURE = "nature"
    SHOPPING = "shopping"
    NIGHTLIFE = "nightlife"
    FAMILY = "family"
    ACCOMMODATION = "accommodation"


class TravelPlanRequest(BaseModel):
    destination: str = Field(min_length=2, max_length=100, description="City or region to visit")
    start_date: date | None = None
    end_date: date | None = None
    interests: list[Interest] = Field(default_factory=list, max_length=8)
    source_currency: str = Field(default="USD", min_length=3, max_length=3)
    target_currency: str = Field(default="USD", min_length=3, max_length=3)
    currency_amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    places_limit: int = Field(default=10, ge=1, le=20)
    places_radius_meters: int | None = Field(default=None, ge=100, le=50_000)

    @field_validator("destination")
    @classmethod
    def normalize_destination(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("destination must not be blank")
        return normalized

    @field_validator("source_currency", "target_currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        if not value.isalpha():
            raise ValueError("currency must be a three-letter alphabetic code")
        return value.upper()

    @model_validator(mode="after")
    def validate_dates(self) -> "TravelPlanRequest":
        if self.end_date and not self.start_date:
            raise ValueError("start_date is required when end_date is provided")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self

    @property
    def currency_requested(self) -> bool:
        return self.currency_amount is not None or self.source_currency != self.target_currency


class ResolvedLocation(BaseModel):
    name: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    country: str | None = None
    timezone: str | None = None


class DailyWeatherForecast(BaseModel):
    date: date
    temperature_min_celsius: float | None = None
    temperature_max_celsius: float | None = None
    precipitation_probability_max: int | None = Field(default=None, ge=0, le=100)
    weather_code: int | None = None
    weather_condition: str | None = None
    wind_speed_max_kmh: float | None = None


class WeatherResponse(BaseModel):
    destination: str
    summary: str | None = None
    temperature_celsius: float | None = None
    precipitation_chance: int | None = Field(default=None, ge=0, le=100)
    provider: str
    location: ResolvedLocation | None = None
    forecasts: list[DailyWeatherForecast] = Field(default_factory=list)


class PlaceResponse(BaseModel):
    name: str | None = None
    category: str | None = None
    description: str | None = None
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    provider_id: str | None = None


class PlacesResponse(BaseModel):
    destination: str
    places: list[PlaceResponse]
    provider: str
    location: ResolvedLocation | None = None


class CurrencyResponse(BaseModel):
    base_currency: str
    target_currency: str
    rate: float = Field(gt=0)
    provider: str
    amount: Decimal | None = None
    converted_amount: Decimal | None = None
    rate_date: date | None = None


class ServiceError(BaseModel):
    service: str
    message: str


class TravelPlanResponse(BaseModel):
    destination: str
    weather: WeatherResponse | None = None
    places: PlacesResponse | None = None
    currency: CurrencyResponse | None = None
    service_errors: list[ServiceError] = Field(default_factory=list)

