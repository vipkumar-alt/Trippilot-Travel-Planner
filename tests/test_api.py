import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_travel_plan_returns_aggregated_mock_data(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/travel-plan",
        json={
            "destination": "Paris",
            "interests": ["culture", "food"],
            "source_currency": "USD",
            "target_currency": "EUR",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["destination"] == "Paris"
    assert body["weather"]["provider"] == "test-weather"
    assert len(body["places"]["places"]) == 1
    assert body["currency"]["target_currency"] == "EUR"
    assert body["service_errors"] == []


@pytest.mark.asyncio
async def test_travel_plan_rejects_invalid_input(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/travel-plan",
        json={"destination": " ", "source_currency": "US1"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_travel_plan_stream_emits_sse_sections_and_completion(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/travel-plan/stream?destination=Paris&interests=culture&interests=food&target_currency=EUR"
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [chunk for chunk in response.text.strip().split("\n\n") if chunk]
    assert all(chunk.startswith("event: ") and "\ndata: {" in chunk for chunk in events)
    assert {chunk.split("\n", 1)[0] for chunk in events} == {
        "event: weather",
        "event: places",
        "event: currency",
        "event: complete",
    }
    assert events[-1].startswith("event: complete\n")


@pytest.mark.asyncio
async def test_standalone_provider_endpoints_and_optional_currency(client: AsyncClient) -> None:
    weather = await client.get("/api/v1/weather?destination=Paris")
    places = await client.get("/api/v1/places?destination=Paris&places_limit=1")
    currency = await client.get("/api/v1/currency?base=USD&target=EUR&amount=100")
    no_currency_plan = await client.post("/api/v1/travel-plan", json={"destination": "Paris"})

    assert weather.status_code == places.status_code == currency.status_code == 200
    assert weather.json()["provider"] == "test-weather"
    assert places.json()["provider"] == "test-places"
    assert currency.json()["converted_amount"] == "92.00"
    assert no_currency_plan.json()["currency"] is None


@pytest.mark.asyncio
async def test_itinerary_generation_endpoint_validates_and_returns_json(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/itineraries/generate",
        json={"destination": "Paris", "start_date": "2026-11-01", "end_date": "2026-11-01", "travelers": 2},
    )
    assert response.status_code == 200
    assert response.json()["number_of_days"] == 1

    invalid = await client.post("/api/v1/itineraries/generate", json={"destination": "Paris"})
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_itinerary_stream_has_expected_framing(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/itineraries/generate/stream",
        json={"destination": "Paris", "start_date": "2026-11-01", "end_date": "2026-11-01"},
    )
    events = [item for item in response.text.strip().split("\n\n") if item]
    assert response.headers["content-type"].startswith("text/event-stream")
    assert [item.split("\n", 1)[0] for item in events] == [
        "event: status", "event: context_ready", "event: itinerary", "event: complete"
    ]


@pytest.mark.asyncio
async def test_conversation_message_endpoint_accepts_initial_itinerary(client: AsyncClient) -> None:
    itinerary = {
        "destination": "Paris", "start_date": "2026-11-01", "end_date": "2026-11-01", "number_of_days": 1,
        "travelers": 1, "summary": "A day", "days": [{"day_number": 1, "date": "2026-11-01", "theme": "Flexible", "activities": [{"title": "Explore", "description": "General activity", "time_of_day": "morning", "duration_minutes": 60, "source": "general_suggestion"}]}],
        "budget_estimate": {},
    }
    trip_request = {"destination": "Paris", "start_date": "2026-11-01", "end_date": "2026-11-01"}
    response = await client.post("/api/v1/conversations/messages", json={"message": "Explain day one", "itinerary": itinerary, "trip_request": trip_request})
    assert response.status_code == 200
    assert response.json()["conversation_id"] == "test-conversation"

