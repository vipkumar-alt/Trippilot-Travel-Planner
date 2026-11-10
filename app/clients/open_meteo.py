from datetime import date, timedelta
from typing import Any

import httpx

from app.errors import ProviderError
from app.schemas.travel import DailyWeatherForecast, ResolvedLocation, WeatherResponse

_WEATHER_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog",
    48: "Depositing rime fog", 51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain", 71: "Slight snow fall",
    73: "Moderate snow fall", 75: "Heavy snow fall", 80: "Slight rain showers",
    81: "Moderate rain showers", 82: "Violent rain showers", 95: "Thunderstorm",
}


class OpenMeteoClient:
    def __init__(
        self, client: httpx.AsyncClient, geocoding_url: str, forecast_url: str, forecast_horizon_days: int
    ) -> None:
        self._client = client
        self._geocoding_url = geocoding_url
        self._forecast_url = forecast_url
        self._forecast_horizon_days = forecast_horizon_days

    async def resolve_destination(self, destination: str) -> ResolvedLocation:
        response = await self._client.get(
            self._geocoding_url, params={"name": destination, "count": 1, "language": "en", "format": "json"}
        )
        response.raise_for_status()
        payload = self._json(response, "Open-Meteo geocoding")
        results = payload.get("results")
        if not isinstance(results, list) or not results or not isinstance(results[0], dict):
            raise ProviderError("Destination could not be resolved")
        result = results[0]
        try:
            return ResolvedLocation(
                name=str(result["name"]), latitude=result["latitude"], longitude=result["longitude"],
                country=result.get("country"), timezone=result.get("timezone"),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ProviderError("Geocoding response was incomplete") from error

    async def get_weather(
        self, location: ResolvedLocation, start_date: date | None, end_date: date | None
    ) -> WeatherResponse:
        self._validate_date_range(start_date, end_date)
        params: dict[str, Any] = {
            "latitude": location.latitude,
            "longitude": location.longitude,
            "daily": "temperature_2m_min,temperature_2m_max,precipitation_probability_max,weather_code,wind_speed_10m_max",
            "timezone": "auto",
        }
        if start_date:
            params.update({"start_date": start_date.isoformat(), "end_date": end_date.isoformat() if end_date else start_date.isoformat()})
        else:
            params["forecast_days"] = self._forecast_horizon_days
        response = await self._client.get(self._forecast_url, params=params)
        response.raise_for_status()
        payload = self._json(response, "Open-Meteo forecast")
        daily = payload.get("daily")
        if not isinstance(daily, dict) or not isinstance(daily.get("time"), list):
            raise ProviderError("Weather forecast was unavailable for the requested dates")
        forecasts: list[DailyWeatherForecast] = []
        for index, item_date in enumerate(daily["time"]):
            try:
                code = self._value(daily, "weather_code", index)
                forecasts.append(DailyWeatherForecast(
                    date=item_date,
                    temperature_min_celsius=self._value(daily, "temperature_2m_min", index),
                    temperature_max_celsius=self._value(daily, "temperature_2m_max", index),
                    precipitation_probability_max=self._value(daily, "precipitation_probability_max", index),
                    weather_code=code,
                    weather_condition=_WEATHER_CODES.get(code, "Unknown weather condition") if code is not None else None,
                    wind_speed_max_kmh=self._value(daily, "wind_speed_10m_max", index),
                ))
            except (TypeError, ValueError) as error:
                raise ProviderError("Weather forecast response was malformed") from error
        if not forecasts:
            raise ProviderError("Weather forecast was unavailable for the requested dates")
        first = forecasts[0]
        return WeatherResponse(
            destination=location.name, summary=first.weather_condition, temperature_celsius=first.temperature_max_celsius,
            precipitation_chance=first.precipitation_probability_max, provider="open-meteo", location=location, forecasts=forecasts,
        )

    def _validate_date_range(self, start_date: date | None, end_date: date | None) -> None:
        if start_date is None:
            return
        today = date.today()
        latest = today + timedelta(days=self._forecast_horizon_days - 1)
        if start_date < today:
            raise ProviderError("Historical weather is unavailable from the forecast endpoint")
        if start_date > latest or (end_date and end_date > latest):
            raise ProviderError(f"Weather forecast is available only through {latest.isoformat()}")

    @staticmethod
    def _json(response: httpx.Response, provider: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as error:
            raise ProviderError(f"{provider} returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise ProviderError(f"{provider} returned an invalid response")
        return payload

    @staticmethod
    def _value(data: dict[str, Any], key: str, index: int) -> Any | None:
        values = data.get(key)
        return values[index] if isinstance(values, list) and index < len(values) else None
