from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from football_analytics.integrations.base import MatchDribble, ProviderUnavailable
from football_analytics.integrations.statsbomb.events import (
    StatsBombEvent,
    normalize_events,
)


class StatsBombDribbleOutcome(BaseModel):
    model_config = ConfigDict(strict=True)

    name: Literal["Complete", "Incomplete"]


class StatsBombDribbleDetails(BaseModel):
    model_config = ConfigDict(strict=True)

    outcome: StatsBombDribbleOutcome


class StatsBombEventWithDribble(StatsBombEvent):
    dribble_details: object = Field(default=None, alias="dribble")


def normalize_dribbles(
    payload: object,
    *,
    match_id: int,
) -> list[MatchDribble]:
    headers = normalize_events(payload, match_id=match_id)
    headers_by_id = {event.external_event_id: event for event in headers}

    try:
        source_events = TypeAdapter(list[StatsBombEventWithDribble]).validate_python(payload)
    except ValidationError as exc:
        raise ProviderUnavailable("StatsBomb returned invalid dribble event data") from exc

    outcome_mapping = {
        "Complete": "complete",
        "Incomplete": "incomplete",
    }

    result: list[MatchDribble] = []

    for event in sorted(source_events, key=lambda item: item.index):
        if event.type.name != "Dribble":
            continue

        try:
            details = StatsBombDribbleDetails.model_validate(event.dribble_details)
        except ValidationError as exc:
            raise ProviderUnavailable(
                f"Invalid dribble details for StatsBomb event {event.id}"
            ) from exc

        header = headers_by_id[event.id]

        result.append(
            MatchDribble.model_validate(
                header.model_dump() | {"outcome": outcome_mapping[details.outcome.name]}
            )
        )

    return result
