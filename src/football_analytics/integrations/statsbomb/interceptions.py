from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from football_analytics.integrations.base import MatchInterception, ProviderUnavailable
from football_analytics.integrations.statsbomb.events import StatsBombEvent, normalize_events


class StatsBombInterceptionOutcome(BaseModel):
    model_config = ConfigDict(strict=True)

    name: Literal[
        "Lost", "Won", "Lost In Play", "Lost Out", "Success", "Success In Play", "Success Out"
    ]


class StatsBombInterceptionDetails(BaseModel):
    model_config = ConfigDict(strict=True)

    outcome: StatsBombInterceptionOutcome


class StatsBombEventWithInterception(StatsBombEvent):
    interception_details: object = Field(default=None, alias="interception")


def normalize_interceptions(payload: object, *, match_id: int) -> list[MatchInterception]:
    headers = normalize_events(payload, match_id=match_id)
    headers_by_id = {event.external_event_id: event for event in headers}

    try:
        events = TypeAdapter(list[StatsBombEventWithInterception]).validate_python(payload)
    except ValidationError as exc:
        raise ProviderUnavailable("StatsBomb returned invalid interception event data") from exc

    result: list[MatchInterception] = []
    for event in sorted(events, key=lambda item: item.index):
        if event.type.name != "Interception":
            continue
        try:
            details = StatsBombInterceptionDetails.model_validate(event.interception_details)
            result.append(
                MatchInterception.model_validate(
                    headers_by_id[event.id].model_dump()
                    | {"outcome": details.outcome.name.lower().replace(" ", "_")}
                )
            )
        except ValidationError as exc:
            raise ProviderUnavailable(
                f"Invalid interception details for StatsBomb event {event.id}"
            ) from exc
    return result
