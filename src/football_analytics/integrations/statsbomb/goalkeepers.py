from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from football_analytics.integrations.base import (
    EventLocation,
    MatchGoalkeeperAction,
    ProviderUnavailable,
)
from football_analytics.integrations.statsbomb.events import (
    StatsBombEvent,
    normalize_events,
)
from football_analytics.integrations.statsbomb.spatial import LOCATION_ADAPTER


class StatsBombGoalkeeperAttribute(BaseModel):
    model_config = ConfigDict(strict=True)

    name: str = Field(min_length=1)


class StatsBombGoalkeeperDetails(BaseModel):
    model_config = ConfigDict(strict=True)

    type: StatsBombGoalkeeperAttribute
    outcome: StatsBombGoalkeeperAttribute | None = None
    body_part: StatsBombGoalkeeperAttribute | None = None
    technique: StatsBombGoalkeeperAttribute | None = None
    position: StatsBombGoalkeeperAttribute | None = None
    end_location: object = None

    @model_validator(mode="after")
    def reject_explicit_null_fields(self) -> "StatsBombGoalkeeperDetails":
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"Explicit null {name} requires review")
        return self


class StatsBombEventWithGoalkeeper(StatsBombEvent):
    goalkeeper_details: object = Field(default=None, alias="goalkeeper")
    location: object = None
    related_events: object = None


def _normalized_name(
    attribute: StatsBombGoalkeeperAttribute | None,
) -> str | None:
    if attribute is None:
        return None
    return attribute.name.lower().replace(" ", "_")


def _goalkeeper_location(value: object) -> EventLocation | None:
    if value is None:
        return None

    coordinates = LOCATION_ADAPTER.validate_python(value)
    if len(coordinates) != 2:
        raise ValueError("Goalkeeper locations must be two-dimensional")

    return EventLocation(x=coordinates[0], y=coordinates[1])


def normalize_goalkeeper_actions(
    payload: object,
    *,
    match_id: int,
) -> list[MatchGoalkeeperAction]:
    headers = normalize_events(payload, match_id=match_id)
    headers_by_id = {event.external_event_id: event for event in headers}

    try:
        events = TypeAdapter(list[StatsBombEventWithGoalkeeper]).validate_python(payload)
    except ValidationError as exc:
        raise ProviderUnavailable("StatsBomb returned invalid goalkeeper event data") from exc

    result: list[MatchGoalkeeperAction] = []

    for event in sorted(events, key=lambda item: item.index):
        if event.type.name != "Goal Keeper":
            continue

        try:
            details = StatsBombGoalkeeperDetails.model_validate(event.goalkeeper_details)

            for field in ("location", "related_events"):
                if field in event.model_fields_set and getattr(event, field) is None:
                    raise ValueError(f"Explicit null {field} requires review")

            related_ids: tuple[str, ...] | None = None
            if "related_events" in event.model_fields_set:
                related = TypeAdapter(list[str]).validate_python(
                    event.related_events,
                    strict=True,
                )
                related_ids = tuple(related)

            goalkeeper_type = _normalized_name(details.type)

            # Documented abbreviated spelling of the same source category.
            if goalkeeper_type == "shot_saved_off_t":
                goalkeeper_type = "shot_saved_off_target"

            result.append(
                MatchGoalkeeperAction.model_validate(
                    headers_by_id[event.id].model_dump()
                    | {
                        "goalkeeper_type": goalkeeper_type,
                        "outcome": _normalized_name(details.outcome),
                        "body_part": _normalized_name(details.body_part),
                        "technique": _normalized_name(details.technique),
                        "keeper_position": _normalized_name(details.position),
                        "coordinate_system": "statsbomb_120_80",
                        "start_location": _goalkeeper_location(event.location),
                        "end_location": _goalkeeper_location(details.end_location),
                        "related_event_ids": related_ids,
                    }
                )
            )

        except ValueError as exc:
            raise ProviderUnavailable(
                f"Invalid goalkeeper details for StatsBomb event {event.id}"
            ) from exc

    return result
