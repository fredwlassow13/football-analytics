from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from football_analytics.integrations.base import (
    EventLocation,
    MatchEventSpatial,
    ProviderUnavailable,
)
from football_analytics.integrations.statsbomb.events import (
    StatsBombEvent,
    normalize_events,
)

LOCATION_ADAPTER = TypeAdapter(
    list[float],
    config=ConfigDict(strict=True, allow_inf_nan=False),
)

REQUIRED_START_TYPES = {
    "Pass",
    "Ball Receipt*",
    "Carry",
    "Shot",
    "Dribble",
    "Pressure",
}


class StatsBombSpatialDetails(BaseModel):
    model_config = ConfigDict(strict=True)

    end_location: object


class StatsBombEventWithSpatial(StatsBombEvent):
    location: object = None
    pass_details: object = Field(default=None, alias="pass")
    carry_details: object = Field(default=None, alias="carry")
    shot_details: object = Field(default=None, alias="shot")


def _normalize_location(
    value: object,
    *,
    allowed_dimensions: set[int],
) -> EventLocation:
    coordinates = LOCATION_ADAPTER.validate_python(value)

    if len(coordinates) not in allowed_dimensions:
        raise ValueError("Unsupported location dimensions")

    return EventLocation(
        x=coordinates[0],
        y=coordinates[1],
        z=coordinates[2] if len(coordinates) == 3 else None,
    )


def normalize_event_spatial(
    payload: object,
    *,
    match_id: int,
) -> list[MatchEventSpatial]:
    headers = normalize_events(payload, match_id=match_id)
    headers_by_id = {event.external_event_id: event for event in headers}

    try:
        source_events = TypeAdapter(list[StatsBombEventWithSpatial]).validate_python(payload)
    except ValidationError as exc:
        raise ProviderUnavailable("StatsBomb returned invalid spatial event data") from exc

    result: list[MatchEventSpatial] = []

    for event in sorted(source_events, key=lambda item: item.index):
        event_type = event.type.name

        try:
            if event.location is None:
                if event_type in REQUIRED_START_TYPES:
                    raise ValueError("Action requires a start location")

                start_location = None
            else:
                start_location = _normalize_location(
                    event.location,
                    allowed_dimensions={2},
                )

            end_location = None
            details_by_type = {
                "Pass": event.pass_details,
                "Carry": event.carry_details,
                "Shot": event.shot_details,
            }

            if event_type in details_by_type:
                details = StatsBombSpatialDetails.model_validate(details_by_type[event_type])
                end_location = _normalize_location(
                    details.end_location,
                    allowed_dimensions={2, 3} if event_type == "Shot" else {2},
                )

        except ValueError as exc:
            raise ProviderUnavailable(
                f"Invalid spatial details for StatsBomb event {event.id}"
            ) from exc

        header = headers_by_id[event.id]

        result.append(
            MatchEventSpatial.model_validate(
                header.model_dump()
                | {
                    "coordinate_system": "statsbomb_120_80",
                    "start_location": start_location,
                    "end_location": end_location,
                }
            )
        )

    return result
