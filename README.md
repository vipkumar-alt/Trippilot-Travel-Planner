# TripPilot API

TripPilot is a FastAPI travel-data backend with concurrent JSON and Server-Sent Events (SSE) responses. It uses real provider data: Open-Meteo for geocoding and weather, Geoapify for places, Frankfurter for currency reference rates, and a local Ollama model for grounded itineraries and conversations. It does not include authentication or persistence.

## Setup

Python 3.11 or newer is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
# Add your Geoapify API key to .env before requesting places.
# Install Ollama and download the local model once:
ollama pull llama3.2
ollama list
uvicorn app.main:app --reload
```

Open-Meteo and Frankfurter use public endpoints and need no API key. Geoapify requires an account and API key; create one through [Geoapify](https://www.geoapify.com/), then set `TRIPPILOT_GEOAPIFY_API_KEY` in `.env`. Do not commit `.env`.

## Configuration

- `TRIPPILOT_GEOAPIFY_API_KEY`: required only for places.
- `TRIPPILOT_REQUEST_TIMEOUT_SECONDS`: provider request timeout; default `5`.
- `TRIPPILOT_PROVIDER_RETRY_ATTEMPTS`: bounded transient-failure retry count; default `2`.
- `TRIPPILOT_WEATHER_FORECAST_DAYS`: Open-Meteo forecast horizon, 1–16 days; default `7`.
- `TRIPPILOT_PLACES_RADIUS_METERS`: default Geoapify search radius; default `5000`.
- `OLLAMA_BASE_URL`: local Ollama server URL; default `http://localhost:11434`.
- `OLLAMA_MODEL`: local model used for structured itinerary and conversation output; default `llama3.2`.
- `OLLAMA_TIMEOUT_SECONDS`: maximum local inference request duration; default `30`.
- `TRIPPILOT_CONVERSATION_HISTORY_LIMIT`: bounded, process-local message history; default `12`.

Ollama requires no API key for ordinary local inference. Confirm that the server is running before starting TripPilot, for example with `ollama list`. `OLLAMA_BASE_URL=http://localhost:11434` means the machine running the FastAPI process. A hosted backend (for example Render) cannot reach an Ollama server running only on your Mac at that address; run Ollama alongside the backend or expose it through an appropriately secured private network.

## API

```bash
curl http://127.0.0.1:8000/health
curl 'http://127.0.0.1:8000/api/v1/weather?destination=Goa'
curl 'http://127.0.0.1:8000/api/v1/places?destination=Goa&interests=nature&places_limit=5'
curl 'http://127.0.0.1:8000/api/v1/currency?base=USD&target=INR&amount=100'
```

Aggregate travel data with optional currency conversion:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/travel-plan \
  -H 'content-type: application/json' \
  -d '{"destination":"Goa","start_date":"2026-10-18","end_date":"2026-10-20","interests":["nature","food"],"places_limit":5,"source_currency":"USD","target_currency":"INR","currency_amount":100}'
```

Representative response sections:

```json
{
  "weather": {"provider":"open-meteo","forecasts":[{"date":"2026-10-18","temperature_min_celsius":24,"temperature_max_celsius":31,"weather_condition":"Partly cloudy"}]},
  "places": {"provider":"geoapify","places":[{"name":"...","address":"...","provider_id":"..."}]},
  "currency": {"provider":"frankfurter","rate":83.45,"amount":"100","converted_amount":"8345.00","rate_date":"2026-10-15"},
  "service_errors": []
}
```

Stream sections as they complete:

```bash
curl -N 'http://127.0.0.1:8000/api/v1/travel-plan/stream?destination=Goa&interests=nature&source_currency=USD&target_currency=INR&currency_amount=100'
```

```text
event: weather
data: {"destination":"Goa","provider":"open-meteo",...}

event: places
data: {"destination":"Goa","provider":"geoapify",...}

event: currency
data: {"base_currency":"USD","target_currency":"INR",...}

event: complete
data: {"destination":"Goa","service_errors":[]}
```

Event order follows provider completion. Provider failures emit `error`; successful sections remain available.

## Phase 3: grounded itinerary generation

`POST /api/v1/itineraries/generate` first collects normalized weather and Geoapify place context, then asks the configured Ollama model for JSON matching a validated itinerary schema. Named provider places must use an ID supplied in that context. Invalid or fabricated IDs, impossible dates, over-packed schedules, and unsourced activity costs are rejected; one bounded repair attempt is allowed.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/itineraries/generate \
  -H 'content-type: application/json' \
  -d '{"destination":"Goa","start_date":"2026-10-27","end_date":"2026-10-29","budget":30000,"currency":"INR","travelers":2,"interests":["nature","food"],"pace":"moderate"}'
```

The response includes a day-by-day activity list, source references, limitations, and deterministic stated-budget arithmetic (`per_day_budget` and `per_person_budget`). It does **not** invent flight, accommodation, restaurant, entrance, or transport prices; without a reliable price source, the estimated total remains `null` and the limitation is explicit.

`POST /api/v1/itineraries/generate/stream` emits `status`, `context_ready`, zero or more `warning`, `itinerary`, and `complete` events. It emits only an itinerary that passed grounding and deterministic checks.

## Phase 4: conversational itinerary changes

After an itinerary is displayed, the built-in chat panel can explain it, make a specified day more relaxed, remove an identified activity, replace or add a place from refreshed Geoapify context, change the stated budget, recalculate budget arithmetic, or refresh travel context.

The JSON endpoint is:

```text
POST /api/v1/conversations/messages
```

The first turn includes the active `itinerary` and original `trip_request`; the response returns a `conversation_id` and `revision`. Later turns send only the message, conversation ID, and latest revision:

```json
{
  "conversation_id": "a1b2c3...",
  "revision": 2,
  "message": "Make day 2 more relaxed"
}
```

Responses include `modified`, `change_summary`, and an updated itinerary only after validation and commit. A stale revision returns HTTP `409`; an unknown or restarted conversation returns HTTP `404`.

Conversation state is in memory only: it is lost on restart, unavailable to other workers/instances, and has no authentication or ownership layer. This is intentional Phase 4 scope, not a persistence or security guarantee. The web UI currently uses the validated JSON endpoint; itinerary-generation SSE remains available independently.

The conversation router uses the configured Ollama model’s structured JSON output, but the backend alone executes a small, bounded set of actions. It never allows model-generated code, shell commands, SQL, arbitrary HTTP, invented place IDs, or unsourced price changes. Replacements must match real place IDs in refreshed provider context.

## Caching and data limits

The asynchronous cache is process-local and not shared across Uvicorn workers or hosts. It caches only successful normalized results: geocoding for 24 hours, places for 6 hours, weather for 30 minutes, and currency for 1 hour. Concurrent identical misses are coalesced.

Validated generated itineraries are cached for 15 minutes using a fingerprint that includes all request preferences, dates, budget, travelers, interests, pace, and constraints. Failures are never cached.

Open-Meteo forecast requests support today through the configured horizon; TripPilot rejects past and out-of-range dates rather than inventing forecasts. Open-Meteo documents a maximum 16-day public forecast horizon. [Open-Meteo docs](https://open-meteo.com/en/docs)

Geoapify fields may be absent and are returned as `null`, never fabricated. Observe the API plan’s credits, rate limits, and attribution requirements; review the [Geoapify Places documentation](https://apidocs.geoapify.com/docs/places/) and current terms before production use.

Frankfurter provides daily reference rates, not live transaction, bid, ask, card-network, or cash-exchange prices. Conversion uses deterministic decimal arithmetic. [Frankfurter documentation](https://frankfurter.dev/)

## Tests and optional live smoke test

Automated tests mock all HTTP responses and never consume provider quotas:

```bash
pytest -q
```

For a manual live smoke test, configure a valid Geoapify key, start the server, and issue the API `curl` examples. You can independently confirm keyless services:

```bash
curl 'https://geocoding-api.open-meteo.com/v1/search?name=Goa&count=1&format=json'
curl 'https://api.frankfurter.dev/v2/rate/usd/inr'
```

To verify local LLM integration, keep `ollama serve` running (when it is not managed by the Ollama app), run `ollama pull llama3.2`, start TripPilot, and call the itinerary endpoint above. Ollama receives one non-streaming `/api/chat` request with the generated JSON schema; TripPilot validates the complete response before its normal JSON or SSE itinerary event is emitted. Llama 3.2 can still produce invalid or poorly grounded output, so the existing bounded repair attempt and deterministic validation remain necessary. Local inference speed and structured-output reliability depend on the downloaded model and available hardware.

Do not treat mocked test output as proof of production credentials, availability, or quota status.
