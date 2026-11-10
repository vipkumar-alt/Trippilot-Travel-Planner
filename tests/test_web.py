import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_web_ui_is_served(client: AsyncClient) -> None:
    response = await client.get("/")
    assert response.status_code == 200
    assert "TripPilot — Plan a trip worth taking" in response.text
