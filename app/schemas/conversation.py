from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from app.schemas.itinerary import BudgetEstimate, ItineraryRequest, ItineraryResponse


class ConversationAction(str, Enum):
    ANSWER_QUESTION = "answer_question"
    MODIFY_ACTIVITY = "modify_activity"
    MODIFY_DAY = "modify_day"
    REPLACE_PLACE = "replace_place"
    ADD_ACTIVITY = "add_activity"
    REMOVE_ACTIVITY = "remove_activity"
    CHANGE_TRIP_CONSTRAINTS = "change_trip_constraints"
    RECALCULATE_BUDGET = "recalculate_budget"
    REFRESH_TRAVEL_CONTEXT = "refresh_travel_context"
    REQUEST_CLARIFICATION = "request_clarification"


class ConversationMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1_500)
    conversation_id: str | None = None
    revision: int | None = Field(default=None, ge=1)
    itinerary: ItineraryResponse | None = None
    trip_request: ItineraryRequest | None = None

    @model_validator(mode="after")
    def validate_start_or_continue(self) -> "ConversationMessageRequest":
        if self.conversation_id is None and (self.itinerary is None or self.trip_request is None):
            raise ValueError("itinerary and trip_request are required to start a conversation")
        if self.conversation_id is not None and self.revision is None:
            raise ValueError("revision is required when continuing a conversation")
        return self


class ActionDecision(BaseModel):
    action: ConversationAction
    reply: str = Field(min_length=1, max_length=800)
    day_number: int | None = Field(default=None, ge=1)
    activity_title: str | None = Field(default=None, max_length=160)
    replacement_place_id: str | None = Field(default=None, max_length=200)
    new_budget: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    new_currency: str | None = Field(default=None, min_length=3, max_length=3)
    clarification: str | None = Field(default=None, max_length=600)


class ConversationMessage(BaseModel):
    role: str
    content: str


class ConversationResponse(BaseModel):
    conversation_id: str
    revision: int
    assistant_reply: str
    modified: bool
    change_summary: list[str] = Field(default_factory=list)
    itinerary: ItineraryResponse | None = None
    budget_estimate: BudgetEstimate | None = None
    warnings: list[str] = Field(default_factory=list)
