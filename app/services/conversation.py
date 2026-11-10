import asyncio
import json
import re
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.clients.ollama import ItineraryLlmProvider
from app.config import Settings
from app.errors import ConversationError, ConversationNotFoundError, RevisionConflictError
from app.prompts.conversation import CONVERSATION_PROMPT_VERSION, SYSTEM_PROMPT
from app.schemas.conversation import (
    ActionDecision,
    ConversationAction,
    ConversationMessage,
    ConversationMessageRequest,
    ConversationResponse,
)
from app.schemas.itinerary import GeneratedItinerary, ItineraryActivity, ItineraryContext, ItineraryRequest, ItineraryResponse
from app.services.budget import BudgetService
from app.services.itinerary_context import ItineraryContextService
from app.services.itinerary_validation import ItineraryConstraintValidator


@dataclass
class _ConversationState:
    itinerary: ItineraryResponse
    trip_request: ItineraryRequest
    context: ItineraryContext
    revision: int = 1
    messages: deque[ConversationMessage] = field(default_factory=deque)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ConversationService:
    """Bounded, in-memory conversation coordinator for validated itinerary changes."""

    def __init__(
        self,
        llm_provider: ItineraryLlmProvider,
        context_service: ItineraryContextService,
        budget_service: BudgetService,
        validator: ItineraryConstraintValidator,
        settings: Settings,
    ) -> None:
        self._llm_provider = llm_provider
        self._context_service = context_service
        self._budget_service = budget_service
        self._validator = validator
        self._settings = settings
        self._states: dict[str, _ConversationState] = {}
        self._states_lock = asyncio.Lock()

    async def respond(self, request: ConversationMessageRequest) -> ConversationResponse:
        conversation_id, state = await self._get_or_create_state(request)
        async with state.lock:
            if request.conversation_id and request.revision != state.revision:
                raise RevisionConflictError("This itinerary changed in another request; refresh before trying again")
            state.messages.append(ConversationMessage(role="user", content=request.message))
            self._trim_history(state)
            decision = await self._decide(state, request.message)
            response = await self._apply_decision(conversation_id, state, decision)
            state.messages.append(ConversationMessage(role="assistant", content=response.assistant_reply))
            self._trim_history(state)
            return response

    async def _get_or_create_state(self, request: ConversationMessageRequest) -> tuple[str, _ConversationState]:
        if request.conversation_id:
            async with self._states_lock:
                state = self._states.get(request.conversation_id)
            if state is None:
                raise ConversationNotFoundError("Conversation was not found; start a new conversation with the active itinerary")
            return request.conversation_id, state

        assert request.itinerary is not None and request.trip_request is not None
        context = await self._context_service.prepare(request.trip_request)
        conversation_id = str(uuid.uuid4())
        state = _ConversationState(itinerary=request.itinerary, trip_request=request.trip_request, context=context)
        async with self._states_lock:
            self._states[conversation_id] = state
        return conversation_id, state

    async def _decide(self, state: _ConversationState, message: str) -> ActionDecision:
        deterministic = self._deterministic_decision(state, message)
        if deterministic is not None:
            return deterministic
        evidence = {
            "trip": state.trip_request.model_dump(mode="json"),
            "revision": state.revision,
            "itinerary_days": [day.model_dump(mode="json") for day in state.itinerary.days],
            "candidate_places": [
                {"place_id": place.place_id, "name": place.name, "category": place.category}
                for place in state.context.places[:12]
            ],
            "recent_messages": [item.model_dump() for item in list(state.messages)[-self._settings.conversation_history_limit :]],
            "latest_message": message,
        }
        raw = await self._llm_provider.generate(
            SYSTEM_PROMPT,
            "Route this conversation turn using only this JSON evidence:\n" + json.dumps(evidence, separators=(",", ":")),
            ActionDecision.model_json_schema(),
        )
        try:
            return ActionDecision.model_validate(raw)
        except ValidationError as error:
            raise ConversationError("Conversation agent returned an invalid action") from error

    def _deterministic_decision(self, state: _ConversationState, message: str) -> ActionDecision | None:
        """Handle common, low-risk requests without asking a small local model to route them."""
        text = message.casefold()
        day_match = re.search(r"\bday\s*(\d+)\b", text)
        day_number = int(day_match.group(1)) if day_match else None
        if day_number and any(word in text for word in ("relax", "slower", "less busy", "less packed")):
            return ActionDecision(
                action=ConversationAction.MODIFY_DAY,
                day_number=day_number,
                reply=f"I made Day {day_number} lighter while keeping its core activities.",
            )
        if any(phrase in text for phrase in ("recalculate budget", "recalculate the budget", "calculate budget")):
            return ActionDecision(action=ConversationAction.RECALCULATE_BUDGET, reply="I recalculated your stated-budget figures.")
        if any(word in text for word in ("budget", "afford", "cost", "cheaper")):
            if "cheaper" in text and not re.search(r"\b\d+(?:\.\d{1,2})?\b", text):
                return ActionDecision(
                    action=ConversationAction.REQUEST_CLARIFICATION,
                    reply="I can help make the plan more budget-conscious. What total budget and currency should I work with?",
                )
            return ActionDecision(action=ConversationAction.ANSWER_QUESTION, reply=self._budget_answer(state))
        if day_number and any(word in text for word in ("explain", "what", "show", "tell", "plan", "day")):
            return ActionDecision(action=ConversationAction.ANSWER_QUESTION, day_number=day_number, reply=self._day_answer(state, day_number))
        if "remove" in text and day_number:
            day = self._find_day(state, day_number)
            if day:
                matching = [activity for activity in day.activities if activity.title.casefold() in text]
                if len(matching) == 1:
                    return ActionDecision(
                        action=ConversationAction.REMOVE_ACTIVITY,
                        day_number=day_number,
                        activity_title=matching[0].title,
                        reply=f"I removed {matching[0].title} from Day {day_number}.",
                    )
        return None

    async def _apply_decision(
        self, conversation_id: str, state: _ConversationState, decision: ActionDecision
    ) -> ConversationResponse:
        if decision.action in {ConversationAction.ANSWER_QUESTION, ConversationAction.REQUEST_CLARIFICATION}:
            return self._response(conversation_id, state, decision.reply, False)
        if decision.action == ConversationAction.RECALCULATE_BUDGET:
            return self._recalculate_budget(conversation_id, state, decision.reply)
        if decision.action == ConversationAction.REFRESH_TRAVEL_CONTEXT:
            state.context = await self._context_service.prepare(state.trip_request)
            return self._response(conversation_id, state, decision.reply, False, ["Refreshed available travel context."])
        if decision.action == ConversationAction.CHANGE_TRIP_CONSTRAINTS:
            return self._change_constraints(conversation_id, state, decision)
        if decision.action == ConversationAction.MODIFY_DAY:
            return self._relax_day(conversation_id, state, decision)
        if decision.action == ConversationAction.REMOVE_ACTIVITY:
            return self._remove_activity(conversation_id, state, decision)
        if decision.action in {ConversationAction.REPLACE_PLACE, ConversationAction.MODIFY_ACTIVITY}:
            return await self._replace_place(conversation_id, state, decision)
        if decision.action == ConversationAction.ADD_ACTIVITY:
            return await self._add_activity(conversation_id, state, decision)
        return self._response(conversation_id, state, decision.reply, False, ["Please clarify the requested change."])

    def _recalculate_budget(self, conversation_id: str, state: _ConversationState, reply: str) -> ConversationResponse:
        candidate = state.itinerary.model_copy(update={"budget_estimate": self._budget_service.estimate(state.trip_request)})
        return self._commit(conversation_id, state, candidate, reply, ["Recalculated deterministic stated-budget figures."])

    @staticmethod
    def _budget_answer(state: _ConversationState) -> str:
        budget = state.itinerary.budget_estimate
        if budget.stated_budget is None:
            return "No total budget is set yet, so I cannot assess cost fit. Share a total budget and three-letter currency to add one."
        currency = budget.currency or state.itinerary.currency or ""
        return (
            f"Your stated budget is {currency} {budget.stated_budget} for {state.itinerary.travelers} traveler(s): "
            f"{currency} {budget.per_day_budget} per day and {currency} {budget.per_person_budget} per traveler. "
            "These are planning figures; live booking prices are not included."
        )

    @staticmethod
    def _day_answer(state: _ConversationState, day_number: int) -> str:
        day = next((item for item in state.itinerary.days if item.day_number == day_number), None)
        if day is None:
            return f"This itinerary has no Day {day_number}. Choose a day from 1 to {len(state.itinerary.days)}."
        activities = "; ".join(f"{item.time_of_day}: {item.title} ({item.duration_minutes} min)" for item in day.activities)
        weather = f" {day.weather_note}" if day.weather_note else ""
        return f"Day {day.day_number} ({day.date}) is themed “{day.theme}”. Planned: {activities}.{weather}"

    def _change_constraints(self, conversation_id: str, state: _ConversationState, decision: ActionDecision) -> ConversationResponse:
        if decision.new_budget is None:
            return self._response(conversation_id, state, decision.clarification or "What total budget should I use?", False)
        currency = (decision.new_currency or state.trip_request.currency or "").upper()
        if len(currency) != 3 or not currency.isalpha():
            return self._response(conversation_id, state, "Please provide a three-letter budget currency.", False)
        trip_request = state.trip_request.model_copy(update={"budget": decision.new_budget, "currency": currency})
        candidate = state.itinerary.model_copy(update={"budget": decision.new_budget, "currency": currency, "budget_estimate": self._budget_service.estimate(trip_request)})
        return self._commit(conversation_id, state, candidate, decision.reply, ["Updated the stated trip budget."], trip_request)

    def _relax_day(self, conversation_id: str, state: _ConversationState, decision: ActionDecision) -> ConversationResponse:
        day = self._find_day(state, decision.day_number)
        if day is None:
            return self._response(conversation_id, state, "Which day would you like to make more relaxed?", False)
        if len(day.activities) <= 2:
            return self._response(conversation_id, state, decision.reply, False, ["That day is already lightly scheduled."])
        relaxed = day.model_copy(update={"activities": day.activities[:2], "theme": f"Relaxed {day.theme}"})
        candidate = state.itinerary.model_copy(update={"days": [relaxed if item.day_number == day.day_number else item for item in state.itinerary.days]})
        return self._commit(conversation_id, state, candidate, decision.reply, [f"Reduced Day {day.day_number} to two activities."])

    def _remove_activity(self, conversation_id: str, state: _ConversationState, decision: ActionDecision) -> ConversationResponse:
        day = self._find_day(state, decision.day_number)
        if day is None or not decision.activity_title:
            return self._response(conversation_id, state, decision.clarification or "Which activity and day should I remove?", False)
        matches = [activity for activity in day.activities if activity.title.casefold() == decision.activity_title.casefold()]
        if len(matches) != 1 or len(day.activities) <= 1:
            return self._response(conversation_id, state, "Please identify one removable activity; each day needs at least one activity.", False)
        updated = day.model_copy(update={"activities": [activity for activity in day.activities if activity not in matches]})
        candidate = state.itinerary.model_copy(update={"days": [updated if item.day_number == day.day_number else item for item in state.itinerary.days]})
        return self._commit(conversation_id, state, candidate, decision.reply, [f"Removed one activity from Day {day.day_number}."])

    async def _replace_place(self, conversation_id: str, state: _ConversationState, decision: ActionDecision) -> ConversationResponse:
        if decision.replacement_place_id:
            state.context = await self._context_service.prepare(state.trip_request)
        day = self._find_day(state, decision.day_number)
        if day is None or not decision.activity_title or not decision.replacement_place_id:
            return self._response(conversation_id, state, decision.clarification or "Which activity and verified replacement should I use?", False)
        replacements = {place.place_id: place for place in state.context.places}
        replacement = replacements.get(decision.replacement_place_id)
        matches = [activity for activity in day.activities if activity.title.casefold() == decision.activity_title.casefold()]
        if replacement is None:
            return self._response(conversation_id, state, "That replacement is not in the available verified places. Please choose another option.", False)
        if len(matches) != 1:
            return self._response(conversation_id, state, "I could not identify one activity to replace. Please specify its day and title.", False)
        old = matches[0]
        new_activity = old.model_copy(update={
            "title": replacement.name or old.title,
            "description": "A verified place selected from the current destination context.",
            "place_id": replacement.place_id,
            "place_name": replacement.name,
            "source": "geoapify",
            "estimated_cost": None,
            "currency": None,
        })
        updated = day.model_copy(update={"activities": [new_activity if item is old else item for item in day.activities]})
        candidate = state.itinerary.model_copy(update={"days": [updated if item.day_number == day.day_number else item for item in state.itinerary.days]})
        return self._commit(conversation_id, state, candidate, decision.reply, [f"Replaced one Day {day.day_number} activity with a verified place."])

    async def _add_activity(self, conversation_id: str, state: _ConversationState, decision: ActionDecision) -> ConversationResponse:
        state.context = await self._context_service.prepare(state.trip_request)
        day = self._find_day(state, decision.day_number)
        replacements = {place.place_id: place for place in state.context.places}
        place = replacements.get(decision.replacement_place_id or "")
        if day is None or place is None:
            return self._response(conversation_id, state, decision.clarification or "Please choose a day and an available verified place.", False)
        activity = ItineraryActivity(
            title=place.name or "Verified place visit", description="A verified place selected from the current destination context.",
            time_of_day="flexible", duration_minutes=60, place_id=place.place_id, place_name=place.name, source="geoapify",
        )
        updated = day.model_copy(update={"activities": [*day.activities, activity]})
        candidate = state.itinerary.model_copy(update={"days": [updated if item.day_number == day.day_number else item for item in state.itinerary.days]})
        return self._commit(conversation_id, state, candidate, decision.reply, [f"Added a verified place to Day {day.day_number}."])

    def _commit(
        self, conversation_id: str, state: _ConversationState, candidate: ItineraryResponse, reply: str,
        changes: list[str], trip_request: ItineraryRequest | None = None,
    ) -> ConversationResponse:
        generated = GeneratedItinerary(
            summary=candidate.summary, days=candidate.days, assumptions=candidate.assumptions,
            limitations=candidate.limitations, practical_tips=candidate.practical_tips,
        )
        errors = self._validator.validate(generated, trip_request or state.trip_request, state.context)
        if errors:
            return self._response(conversation_id, state, "I could not apply that change safely.", False, errors)
        state.itinerary = candidate
        if trip_request is not None:
            state.trip_request = trip_request
        state.revision += 1
        return self._response(conversation_id, state, reply, True, changes)

    def _response(
        self, conversation_id: str, state: _ConversationState, reply: str, modified: bool,
        changes: list[str] | None = None,
    ) -> ConversationResponse:
        return ConversationResponse(
            conversation_id=conversation_id, revision=state.revision, assistant_reply=reply, modified=modified,
            change_summary=changes or [], itinerary=state.itinerary if modified else None,
            budget_estimate=state.itinerary.budget_estimate, warnings=state.context.limitations,
        )

    @staticmethod
    def _find_day(state: _ConversationState, day_number: int | None):
        return next((day for day in state.itinerary.days if day.day_number == day_number), None)

    def _trim_history(self, state: _ConversationState) -> None:
        while len(state.messages) > self._settings.conversation_history_limit:
            state.messages.popleft()
