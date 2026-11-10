"""Ollama adapter for schema-constrained local model responses."""

import json
from typing import Any, Protocol

import httpx

from app.errors import ConfigurationError, ProviderError


class ItineraryLlmProvider(Protocol):
    async def generate(self, system_prompt: str, user_prompt: str, schema: dict[str, Any]) -> dict[str, Any]: ...


class OllamaLlmClient:
    """Minimal client for Ollama's local ``/api/chat`` structured-output API."""

    def __init__(self, client: httpx.AsyncClient, base_url: str, model: str, timeout_seconds: float) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_seconds = timeout_seconds

    async def generate(self, system_prompt: str, user_prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(
                f"{self._base_url}/api/chat",
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "format": schema,
                    "stream": False,
                    "options": {"temperature": 0.2},
                },
                timeout=self._timeout_seconds,
            )
        except httpx.TimeoutException as error:
            raise ProviderError("Ollama request timed out") from error
        except httpx.TransportError as error:
            raise ProviderError("Ollama server was unavailable") from error

        if response.status_code == 404:
            raise ConfigurationError(f"Ollama model '{self._model}' is unavailable; run: ollama pull {self._model}")
        if response.status_code >= 500:
            raise ProviderError("Ollama server was temporarily unavailable")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise ProviderError("Ollama rejected the generation request") from error

        try:
            payload = response.json()
            content = payload["message"]["content"]
            decoded = json.loads(content)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ProviderError("Ollama returned an invalid structured response") from error
        if not isinstance(decoded, dict):
            raise ProviderError("Ollama returned an invalid structured response")
        return decoded
