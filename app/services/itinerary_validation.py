from datetime import timedelta

from app.schemas.itinerary import GeneratedItinerary, ItineraryContext, ItineraryRequest


class ItineraryConstraintValidator:
    def validate(
        self, itinerary: GeneratedItinerary, request: ItineraryRequest, context: ItineraryContext
    ) -> list[str]:
        errors: list[str] = []
        if len(itinerary.days) != request.number_of_days:
            errors.append("The itinerary must contain exactly one entry per requested trip day.")
        valid_places = {
            place.place_id: place
            for place in (context.places + context.accommodation_places + context.food_places)
        }
        for index, day in enumerate(itinerary.days, start=1):
            expected_date = request.start_date + timedelta(days=index - 1)
            if day.day_number != index or day.date != expected_date:
                errors.append(f"Day {index} must use date {expected_date.isoformat()} and day_number {index}.")
            if sum(activity.duration_minutes for activity in day.activities) > 720:
                errors.append(f"Day {index} contains more than 12 hours of scheduled activities.")
            for activity in day.activities:
                if activity.estimated_cost is not None:
                    errors.append("Activity costs require a reliable price source and must be omitted.")
                if activity.source == "geoapify":
                    if activity.place_id not in valid_places:
                        errors.append(f"Activity '{activity.title}' references a place not supplied by Geoapify.")
                    elif activity.place_name and valid_places[activity.place_id].name != activity.place_name:
                        errors.append(f"Activity '{activity.title}' does not match its supplied place identifier.")
                elif activity.place_id is not None or activity.place_name is not None:
                    errors.append(f"General suggestion '{activity.title}' must not claim a provider place.")
                if activity.currency and request.currency and activity.currency != request.currency:
                    errors.append(f"Activity '{activity.title}' uses a currency inconsistent with the requested budget.")
        return list(dict.fromkeys(errors))
