from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import httpx

from app.errors import ProviderError
from app.schemas.travel import CurrencyResponse


class FrankfurterClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    async def get_rate(self, base_currency: str, target_currency: str, amount: Decimal | None) -> CurrencyResponse:
        if base_currency == target_currency:
            return self._response(base_currency, target_currency, Decimal("1"), amount, None, "identity")
        response = await self._client.get(f"{self._base_url}/rate/{base_currency.lower()}/{target_currency.lower()}")
        if response.status_code in {400, 404, 422}:
            raise ProviderError("Currency pair is unsupported or unavailable")
        response.raise_for_status()
        try:
            payload: Any = response.json()
            rate = Decimal(str(payload["rate"]))
            rate_date = date.fromisoformat(str(payload["date"])) if payload.get("date") else None
        except (KeyError, TypeError, ValueError) as error:
            raise ProviderError("Frankfurter returned an invalid exchange-rate response") from error
        if rate <= 0:
            raise ProviderError("Frankfurter returned an invalid exchange rate")
        return self._response(base_currency, target_currency, rate, amount, rate_date, "frankfurter")

    @staticmethod
    def _response(
        base: str, target: str, rate: Decimal, amount: Decimal | None, rate_date: date | None, provider: str
    ) -> CurrencyResponse:
        converted = (amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if amount is not None else None
        return CurrencyResponse(
            base_currency=base,
            target_currency=target,
            rate=float(rate),
            amount=amount,
            converted_amount=converted,
            rate_date=rate_date,
            provider=provider,
        )
