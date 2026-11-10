import json

import httpx
import pytest

from app.clients.ollama import OllamaLlmClient
from app.config import Settings
from app.errors import ProviderError


@pytest.mark.asyncio
async def test_ollama_client_posts_schema_and_parses_json() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("http://ollama.test/api/chat")
        payload = json.loads(request.content)
        assert payload["model"] == "llama3.2"
        assert payload["stream"] is False
        assert payload["format"] == {"type": "object"}
        return httpx.Response(200, json={"message": {"content": '{"summary":"Valid"}'}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await OllamaLlmClient(client, "http://ollama.test/", "llama3.2", 1).generate(
            "system", "user", {"type": "object"}
        )
    assert result == {"summary": "Valid"}


@pytest.mark.asyncio
async def test_ollama_client_handles_invalid_output_and_server_failure() -> None:
    async def invalid_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "not-json"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(invalid_handler)) as client:
        with pytest.raises(ProviderError, match="invalid structured"):
            await OllamaLlmClient(client, "http://ollama.test", "llama3.2", 1).generate("s", "u", {})

    async def unavailable_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable_handler)) as client:
        with pytest.raises(ProviderError, match="temporarily unavailable"):
            await OllamaLlmClient(client, "http://ollama.test", "llama3.2", 1).generate("s", "u", {})


def test_ollama_settings_accept_requested_environment_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:12345")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.2:3b")
    settings = Settings()
    assert settings.ollama_base_url == "http://localhost:12345"
    assert settings.ollama_model == "llama3.2:3b"
