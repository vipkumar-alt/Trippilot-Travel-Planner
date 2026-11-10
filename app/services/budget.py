from decimal import Decimal, ROUND_HALF_UP

from app.schemas.itinerary import BudgetEstimate, ItineraryRequest


class BudgetService:
    """Computes only deterministic budget arithmetic; it does not invent market prices."""

    def estimate(self, request: ItineraryRequest) -> BudgetEstimate:
        if request.budget is None:
            return BudgetEstimate(limitations=["No total budget was supplied; cost fit cannot be assessed."])
        per_day = (request.budget / request.number_of_days).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        per_person = (request.budget / request.travelers).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # Illustrative planning allocation benchmarks (40% accommodation, 25% food, 15% transport, 10% activities, 10% contingency)
        acc_est = (request.budget * Decimal("0.40")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        food_est = (request.budget * Decimal("0.25")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        trans_est = (request.budget * Decimal("0.15")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        act_est = (request.budget * Decimal("0.10")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        other_est = (request.budget * Decimal("0.05")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        contingency_est = (request.budget * Decimal("0.05")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        return BudgetEstimate(
            stated_budget=request.budget,
            currency=request.currency,
            per_day_budget=per_day,
            per_person_budget=per_person,
            accommodation_estimate=acc_est,
            food_estimate=food_est,
            transport_estimate=trans_est,
            activities_estimate=act_est,
            other_estimate=other_est,
            contingency_estimate=contingency_est,
            limitations=["Budget category breakdowns are indicative planning allocations based on your stated budget; live market rates should be verified before booking."],
        )
